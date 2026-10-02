"""Tests for the subprocess helpers in converters/engine.py (no engines needed)."""

import os
import sys
from urllib.parse import unquote, urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters import engine


def _fake_soffice(monkeypatch, *, fail: bool) -> dict:
    """Stand in for LibreOffice: record the profile URL it was given, write
    into the profile like the real one does, then produce the output file
    (or fail)."""
    seen = {}

    def fake_run(cmd, timeout=300, cwd=None, env=None):
        arg = next(a for a in cmd if a.startswith("-env:UserInstallation="))
        seen["uri"] = arg.split("=", 1)[1]
        path = unquote(urlparse(seen["uri"]).path)
        if os.name == "nt":
            path = path.lstrip("/")  # file:///C:/x -> C:/x
        seen["profile"] = path
        with open(os.path.join(path, "registrymodifications.xcu"), "w") as fh:
            fh.write("<x/>")
        if fail:
            raise engine.ConversionError("soffice failed (exit 1): boom")
        out_dir = cmd[cmd.index("--outdir") + 1]
        base = os.path.splitext(os.path.basename(cmd[-1]))[0]
        with open(os.path.join(out_dir, f"{base}.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4\n")

    monkeypatch.setattr(engine, "require", lambda binary: binary)
    monkeypatch.setattr(engine, "run", fake_run)
    return seen


@pytest.mark.parametrize("fail", [False, True])
def test_soffice_profile_is_a_valid_uri_and_is_removed(tmp_path, monkeypatch, fail):
    seen = _fake_soffice(monkeypatch, fail=fail)
    src = tmp_path / "in.docx"
    src.write_bytes(b"x")
    if fail:
        with pytest.raises(engine.ConversionError):
            engine.soffice_convert(str(src), str(tmp_path), "pdf")
    else:
        produced = engine.soffice_convert(str(src), str(tmp_path), "pdf")
        assert os.path.basename(produced) == "in.pdf"
    assert seen["uri"].startswith("file:///")
    assert "\\" not in seen["uri"]
    # The profile existed during the run and is gone afterwards, even on failure.
    assert seen["profile"] and not os.path.exists(seen["profile"])


def test_run_raises_conversion_error_on_missing_binary():
    with pytest.raises(engine.ConversionError, match="not found"):
        engine.run(["definitely-not-a-real-binary-xyz"])


def _captured_pandoc_cmd(monkeypatch, out_name: str) -> list:
    seen = {}
    monkeypatch.setattr(engine, "require", lambda binary: binary)
    monkeypatch.setattr(engine, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd))
    engine.pandoc("in.html", out_name, from_fmt="html")
    return seen["cmd"]


@pytest.mark.parametrize("out_name", ["out.docx", "out.md", "out.epub", "out.pdf"])
def test_pandoc_always_runs_sandboxed(monkeypatch, out_name):
    cmd = _captured_pandoc_cmd(monkeypatch, out_name)
    assert "--sandbox" in cmd


def test_pandoc_pdf_engine_may_only_fetch_data_uris(monkeypatch):
    cmd = _captured_pandoc_cmd(monkeypatch, "out.pdf")
    assert "--pdf-engine=weasyprint" in cmd
    assert "--pdf-engine-opt=--allowed-protocols=data" in cmd
