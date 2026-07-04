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

import os
import tempfile
import uuid

from flask import (
    Flask,
    abort,
    jsonify,
    request,
    send_file,
)
from werkzeug.utils import secure_filename

from converters import FORMATS, get_converter, matrix
from converters.engine import ConversionError

flask_app = Flask(__name__, static_folder="static", template_folder="templates")

WORK = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(WORK, exist_ok=True)

# 200 MB upload cap (audio/ebooks can be large).
flask_app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "200")) * 1024 * 1024


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


@flask_app.get("/api/formats")
def api_formats():
    return jsonify({"formats": matrix()})


@flask_app.post("/convert")
def convert():
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

    job = uuid.uuid4().hex
    stem = secure_filename(os.path.splitext(f.filename)[0]) or "converted"
    in_path = os.path.join(WORK, f"{job}.{src}")
    out_path = os.path.join(WORK, f"{job}.{target}")
    f.save(in_path)
    try:
        fn(in_path, out_path)
    except NotImplementedError:
        return jsonify({"error": f".{src} → .{target} is not implemented yet."}), 501
    except ConversionError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Conversion failed: {e}"}), 500
    finally:
        _safe_unlink(in_path)

    if not os.path.exists(out_path):
        abort(500)

    resp = send_file(
        out_path,
        as_attachment=True,
        download_name=f"{stem}.{target}",
    )

    @resp.call_on_close
    def _cleanup():  # noqa: ANN202
        _safe_unlink(out_path)

    return resp


@flask_app.get("/health")
def health():
    return {"status": "ok", "formats": len(matrix())}


@flask_app.get("/")
def index():
    # The UI agent provides templates/index.html. Fall back to a minimal page so
    # the service is usable before the real UI lands.
    tpl = os.path.join(flask_app.template_folder, "index.html")
    if os.path.exists(tpl):
        with open(tpl, "r", encoding="utf-8") as fh:
            return fh.read()
    return _FALLBACK_PAGE


def _safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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
    flask_app.run(host="0.0.0.0", port=5007)
