"""HTTP-level tests for web_app.py (Flask test client). No external engines:
the requests go through the pure-Python data converters."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("flask")
pytest.importorskip("pandas")

import web_app  # noqa: E402


@pytest.fixture
def client():
    return web_app.flask_app.test_client()


def _post(client, name: str, payload: bytes, target: str):
    return client.post(
        "/convert",
        data={"file": (io.BytesIO(payload), name), "target": target},
        content_type="multipart/form-data",
    )


def _work_files() -> set:
    return {f for f in os.listdir(web_app.WORK) if not f.startswith("demo-guard")}


def test_formats_payload_reports_upload_limit(client):
    body = client.get("/api/formats").get_json()
    assert body["maxUploadMb"] == web_app._upload_mb


def test_json_output_downloads_as_a_file(client):
    # .json outputs are served as application/json — the same content type as
    # error bodies — so the UI must treat any 2xx as the file.
    resp = _post(client, "people.csv", b"a,b\n1,2\n", "json")
    try:
        assert resp.status_code == 200
        disposition = resp.headers["Content-Disposition"]
        assert "attachment" in disposition and "people.json" in disposition
        assert resp.get_json() == [{"a": 1, "b": 2}]
    finally:
        resp.close()  # triggers the call_on_close cleanup
    assert _work_files() == set()


def test_failed_conversion_leaves_no_files_behind(client):
    before = _work_files()
    # openpyxl rejects control characters after the output file is started.
    resp = _post(client, "t.csv", b"a,b\n\x01bad,2\n", "xlsx")
    assert resp.status_code == 422
    assert resp.get_json()["error"]
    assert _work_files() == before


def test_unexpected_handler_crash_is_a_json_500(client, monkeypatch, caplog):
    def crash(_in, out):
        with open(out, "w") as fh:
            fh.write("partial")
        raise RuntimeError("kaboom at /app/data/secret-path")

    monkeypatch.setattr(web_app, "get_converter", lambda s, t: crash)
    before = _work_files()
    resp = _post(client, "x.csv", b"a\n1\n", "json")
    assert resp.status_code == 500
    error = resp.get_json()["error"]
    # The client gets a generic message; the detail is only in the log.
    assert error and "kaboom" not in error and "/app/data" not in error
    assert "kaboom at /app/data/secret-path" in caplog.text
    assert _work_files() == before


def test_handler_that_writes_nothing_is_a_json_500(client, monkeypatch):
    monkeypatch.setattr(web_app, "get_converter", lambda s, t: (lambda i, o: None))
    resp = _post(client, "x.csv", b"a\n1\n", "json")
    assert resp.status_code == 500
    assert resp.is_json and resp.get_json()["error"]


def test_oversized_upload_gets_a_json_error(client, monkeypatch):
    monkeypatch.setitem(web_app.flask_app.config, "MAX_CONTENT_LENGTH", 1024)
    resp = _post(client, "big.csv", b"a,b\n" + b"1,2\n" * 1000, "json")
    assert resp.status_code == 413
    assert resp.is_json and "too large" in resp.get_json()["error"]


@pytest.mark.parametrize("name,target", [
    ("x.nope", "pdf"),   # unknown source
    ("x.csv", "nope"),   # unknown target
    ("x.mp3", "xlsx"),   # known formats, no converter between them
    ("x.csv", ""),       # no target
])
def test_bad_requests_are_json_400s(client, name, target):
    resp = _post(client, name, b"data", target)
    assert resp.status_code == 400
    assert resp.is_json and resp.get_json()["error"]


@pytest.mark.parametrize("upload,expected", [
    ("Señor López – contrato.csv", "Señor López – contrato.json"),
    ("Año 2026.csv", "Año 2026.json"),   # secure_filename made this "Ano_2026"
    ("реестр.csv", "реестр.json"),       # ...and this "converted"
    ("plain name.csv", "plain name.json"),
])
def test_download_keeps_the_original_name(client, upload, expected):
    from urllib.parse import unquote

    resp = _post(client, upload, b"a\n1\n", "json")
    try:
        assert resp.status_code == 200
        disposition = resp.headers["Content-Disposition"]
        if expected.isascii():
            assert f'filename="{expected}"' in disposition or f"filename={expected}" in disposition
        else:
            # what the UI's parseFilename() reads
            star = disposition.split("filename*=UTF-8''", 1)[1].split(";")[0]
            assert unquote(star) == expected
    finally:
        resp.close()


@pytest.mark.parametrize("upload,stem", [
    ("C:\\Users\\me\\Desktop\\report.csv", "report"),   # full path from old browsers
    ("../../etc/passwd.csv", "passwd"),
    ('bad<>:"|?*name.csv', "bad_______name"),
    ("tab\there.csv", "tab_here"),
    ("  .hidden .csv", "hidden"),
    ("... .csv", "converted"),
    ("x" * 300 + ".csv", "x" * 150),
])
def test_download_stem_is_sanitised(upload, stem):
    assert web_app._download_stem(upload) == stem


def test_ui_is_served_regardless_of_working_directory(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'id="dropzone"' in resp.data  # the real UI, not the fallback page


def test_health_reports_build(client):
    body = client.get("/health").get_json()
    assert body["status"] == "ok" and body["formats"] > 0 and body["build"]
