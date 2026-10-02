"""File Converter — web service.

Registry-driven multi-format converter. The set of supported conversions is
defined entirely by the ``converters`` package; this layer only:
  * GET  /api/formats  -> the full conversion matrix (drives the UI)
  * POST /convert       -> {file, target} multipart -> converted file download
  * GET  /health        -> liveness probe
  * GET  /              -> the single-page UI

Source format is auto-detected from the uploaded filename's extension.
"""

from __future__ import annotations

import contextlib
import os
import re
import uuid

from flask import Flask, Response, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from converters import FORMATS, get_converter, matrix
from converters.engine import ConversionError
from demo_guard import DemoGuard, client_ip

flask_app = Flask(__name__, static_folder="static", template_folder="templates")

# Scratch space for uploads/outputs (deleted after each request) and the demo
# guard's SQLite file. The Docker image sets DATA_DIR=/app/data; a source
# checkout defaults to the git-ignored ./data next to this file.
WORK = os.environ.get("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data"
)
os.makedirs(WORK, exist_ok=True)

guard = DemoGuard(os.path.join(WORK, "demo-guard.sqlite"))

# 200 MB upload cap (audio/ebooks can be large); demo instances cap harder.
_upload_mb = guard.max_upload_mb if guard.enabled else int(os.environ.get("MAX_UPLOAD_MB", "200"))
flask_app.config["MAX_CONTENT_LENGTH"] = _upload_mb * 1024 * 1024

# The page loads only its own CSS/JS and a data: favicon, so the policy can be
# tight: no inline scripts, no framing, same-origin only. style-src inherits
# 'self' from default-src; the UI sets styles through the CSSOM (el.style.x),
# which CSP permits, so no 'unsafe-inline' is needed.
_CSP = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


@flask_app.after_request
def _security_headers(resp):  # Flask after_request hook
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


# Control characters plus what Windows forbids in file names (a superset of
# what macOS/Linux forbid).
_UNSAFE_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def _download_stem(filename: str) -> str:
    """The upload's name minus its extension, for the download.

    Only used as a name, never as a path on disk (uploads are stored under a
    random id). Unlike werkzeug's secure_filename this keeps accents and
    non-Latin scripts ("Año" stays "Año", not "Ano"): send_file emits an
    RFC 5987 ``filename*`` for non-ASCII names, which the UI decodes.
    """
    name = re.split(r"[\\/]", filename)[-1]  # some browsers send a full path
    stem = _UNSAFE_NAME_CHARS.sub("_", os.path.splitext(name)[0]).strip(" .")
    return stem[:150].rstrip(" .") or "converted"


@flask_app.errorhandler(413)
def too_large(_err: RequestEntityTooLarge) -> tuple[Response, int]:
    """JSON instead of Werkzeug's HTML 413 page, like every other error."""
    return jsonify({"error": f"File is too large — the limit is {_upload_mb} MB."}), 413


@flask_app.get("/api/formats")
def api_formats() -> Response:
    """The conversion matrix, the upload cap and (in demo mode) its limits."""
    payload = {"formats": matrix(), "maxUploadMb": _upload_mb}
    demo = guard.public_info()
    if demo:
        payload["demo"] = demo
    return jsonify(payload)


@flask_app.post("/convert")
def convert() -> Response | tuple[Response, int]:
    """Convert the uploaded ``file`` to ``target`` and send it back.

    Errors are JSON ``{"error": ...}`` with a 4xx/5xx status. Both the upload
    and the output are deleted once the response is done.
    """
    f = request.files.get("file")
    target = (request.form.get("target") or "").lower().lstrip(".")
    if not f or not f.filename:
        return jsonify({"error": "Please choose a file."}), 400
    if not target:
        return jsonify({"error": "Please choose a target format."}), 400

    src = _ext(f.filename)
    if src not in FORMATS:
        return jsonify({"error": f"Unsupported source type: .{src or '?'}"}), 400
    if target not in FORMATS:
        return jsonify({"error": f"Unsupported target type: .{target}"}), 400

    fn = get_converter(src, target)
    if fn is None:
        return jsonify({"error": f"No converter for .{src} → .{target}."}), 400

    ip = client_ip(request.headers, request.remote_addr, guard.proxy_hops)
    verdict = guard.check_and_count(ip, src, target)
    if not verdict.allowed:
        return jsonify({"error": verdict.error}), verdict.status

    job = uuid.uuid4().hex
    stem = _download_stem(f.filename)
    in_path = os.path.join(WORK, f"{job}.{src}")
    out_path = os.path.join(WORK, f"{job}.{target}")
    f.save(in_path)
    error: tuple[str, int] | None = None
    try:
        fn(in_path, out_path)
    except NotImplementedError:
        error = (f".{src} → .{target} is not implemented yet.", 501)
    except ConversionError as e:
        error = (str(e), 422)
    except Exception:  # a handler bug must still answer in JSON
        # The exception text can carry server paths and library internals;
        # it goes to the log, and the client gets a generic message.
        flask_app.logger.exception("Unexpected failure converting .%s -> .%s", src, target)
        error = ("Conversion failed because of an unexpected server error.", 500)
    finally:
        _safe_unlink(in_path)

    if error is None and not os.path.exists(out_path):
        error = ("The converter finished without producing a file.", 500)
    if error is not None:
        # A handler can fail after it has started writing; don't leave the
        # partial file behind in DATA_DIR.
        _safe_unlink(out_path)
        return jsonify({"error": error[0]}), error[1]

    resp = send_file(
        out_path,
        as_attachment=True,
        download_name=f"{stem}.{target}",
    )
    # send_file marks the response direct_passthrough, and Werkzeug then hands
    # the raw file to the server without wiring up Response.close() — so
    # call_on_close callbacks never fire and every output would pile up in
    # DATA_DIR. Streaming it through the response restores close(), which
    # closes the file first and then deletes it (safe on Windows too).
    resp.direct_passthrough = False
    resp.call_on_close(lambda: _safe_unlink(out_path))
    return resp


def _build_stamp() -> str:
    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "BUILD_STAMP"),
            encoding="utf-8",
        ) as fh:
            return fh.read().strip()
    except OSError:
        return "dev (no BUILD_STAMP — running outside the Docker image)"


@flask_app.get("/health")
def health() -> dict[str, object]:
    """Liveness probe with the number of source formats and the build stamp."""
    return {"status": "ok", "formats": len(matrix()), "build": _build_stamp()}


@flask_app.get("/")
def index() -> str:
    """The single-page UI, served as-is (no Jinja needed).

    The fallback page keeps the API usable if the template is missing from a
    build. The path is resolved against root_path because template_folder is
    relative and the process may be started from another directory.
    """
    tpl = os.path.join(flask_app.root_path, flask_app.template_folder, "index.html")
    if os.path.exists(tpl):
        with open(tpl, encoding="utf-8") as fh:
            return fh.read()
    return _FALLBACK_PAGE


def _safe_unlink(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


_FALLBACK_PAGE = """<!doctype html><meta charset=utf-8>
<title>File Converter</title>
<body style="font-family:sans-serif;max-width:540px;margin:40px auto">
<h1>File Converter</h1>
<p>API is up. The full UI has not been built yet.</p>
<form method=post action=/convert enctype=multipart/form-data>
  <input type=file name=file required>
  <input name=target placeholder="target ext, e.g. pdf" required>
  <button>Convert</button>
</form>
<p><a href=/api/formats>/api/formats</a></p>
</body>"""


if __name__ == "__main__":
    # Dev server only; the Docker image runs gunicorn (bound to 0.0.0.0:5007
    # inside the container). This binds to localhost unless HOST says otherwise,
    # so `python web_app.py` doesn't expose the unhardened app on every
    # interface. Set HOST=0.0.0.0 to serve beyond the local machine on purpose.
    flask_app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5007")),
    )
