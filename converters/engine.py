"""Shared subprocess + tool helpers used by every converter module.

Keeps all "shell out to an external binary" logic in one place so the format
modules stay small and consistent. Every helper raises :class:`ConversionError`
with a useful message on failure (which the Flask layer turns into a 4xx/5xx).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class ConversionError(Exception):
    """Raised when a conversion cannot be completed."""


def have(binary: str) -> bool:
    """True if ``binary`` is on PATH. Use to skip tests / give clear errors."""
    return shutil.which(binary) is not None


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise ConversionError(
            f"Required tool '{binary}' is not installed in this image."
        )
    return path


def run(cmd, timeout: int = 300, cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``cmd`` (a list), capturing output. Raise ConversionError on failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            env={**os.environ, **(env or {})},
        )
    except FileNotFoundError as e:
        raise ConversionError(f"Required tool not found: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise ConversionError(f"Conversion timed out after {timeout}s") from e
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        raise ConversionError(
            f"{os.path.basename(str(cmd[0]))} failed (exit {proc.returncode}): {err or 'no output'}"
        )
    return proc


# --- Pandoc -----------------------------------------------------------------

def pandoc(in_path: str, out_path: str, *, from_fmt: str | None = None,
           to_fmt: str | None = None, extra: list[str] | None = None,
           pdf_engine: str = "weasyprint") -> None:
    """Run pandoc. ``out_path`` extension usually determines the writer, but
    ``to_fmt`` can force it. For PDF output we use a CSS engine (weasyprint) so
    we don't need a multi-GB TeX install."""
    require("pandoc")
    cmd = ["pandoc", in_path, "-o", out_path, "--standalone"]
    if from_fmt:
        cmd += ["-f", from_fmt]
    if to_fmt:
        cmd += ["-t", to_fmt]
    if out_path.lower().endswith(".pdf"):
        cmd += [f"--pdf-engine={pdf_engine}"]
    if extra:
        cmd += extra
    run(cmd)


# --- LibreOffice (headless) -------------------------------------------------

def soffice_convert(in_path: str, out_dir: str, target_ext: str,
                    convert_filter: str | None = None) -> str:
    """Convert via LibreOffice headless. Returns the produced file path.

    Each call uses a private, throwaway profile dir so concurrent gunicorn
    workers don't clash on a shared UserInstallation lock. The profile is
    deleted afterwards: LibreOffice leaves ~0.5 MB in it per run, which
    used to pile up in /tmp for the life of the container.
    """
    require("soffice")
    os.makedirs(out_dir, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="lo_profile_")
    to_arg = f"{target_ext}:{convert_filter}" if convert_filter else target_ext
    try:
        run([
            "soffice", "--headless", "--norestore", "--nolockcheck", "--nodefault",
            # as_uri() yields a valid file URL on both POSIX and Windows paths
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--convert-to", to_arg, "--outdir", out_dir, in_path,
        ], timeout=240)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    base = os.path.splitext(os.path.basename(in_path))[0]
    produced = os.path.join(out_dir, f"{base}.{target_ext}")
    if not os.path.exists(produced):
        raise ConversionError("LibreOffice did not produce an output file.")
    return produced


# --- ffmpeg -----------------------------------------------------------------

def ffmpeg(in_path: str, out_path: str, extra: list[str] | None = None) -> None:
    require("ffmpeg")
    cmd = ["ffmpeg", "-y", "-i", in_path]
    if extra:
        cmd += extra
    cmd += [out_path]
    run(cmd)


# --- Calibre ----------------------------------------------------------------

def ebook_convert(in_path: str, out_path: str, extra: list[str] | None = None) -> None:
    require("ebook-convert")
    cmd = ["ebook-convert", in_path, out_path]
    if extra:
        cmd += extra
    run(cmd, timeout=300)
