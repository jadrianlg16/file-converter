"""Shared subprocess + tool helpers used by every converter module.

Keeps all "shell out to an external binary" logic in one place so the format
modules stay small and consistent. Every helper raises :class:`ConversionError`
with a useful message on failure (which the Flask layer turns into a 4xx/5xx).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class ConversionError(Exception):
    """Raised when a conversion cannot be completed.

    The message is shown to the user verbatim, so it must not contain server
    paths or raw tool output; log those instead.
    """


def have(binary: str) -> bool:
    """True if ``binary`` is on PATH. Use to skip tests / give clear errors."""
    return shutil.which(binary) is not None


def require(binary: str) -> str:
    """Path of ``binary``, or a ConversionError naming the missing tool."""
    path = shutil.which(binary)
    if not path:
        raise ConversionError(f"Required tool '{binary}' is not installed in this image.")
    return path


def run(
    cmd: list[str], timeout: int = 300, cwd: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
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
        tool = os.path.basename(str(cmd[0]))
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        # stderr can echo upload content and absolute server paths, so it goes
        # to the log with the exit code; the user only learns which tool failed.
        log.warning("%s failed (exit %s): %s", tool, proc.returncode, err or "no output")
        raise ConversionError(f"The {tool} step failed to convert this file.")
    return proc


# --- Pandoc -----------------------------------------------------------------


def pandoc(
    in_path: str,
    out_path: str,
    *,
    from_fmt: str | None = None,
    to_fmt: str | None = None,
    extra: list[str] | None = None,
) -> None:
    """Run pandoc. ``out_path`` extension usually determines the writer, but
    ``to_fmt`` can force it. For PDF output we use a CSS engine (WeasyPrint) so
    we don't need a multi-GB TeX install.

    Uploads are untrusted, so pandoc runs with ``--sandbox``: readers and
    writers may only touch the input file. Without it an ``<img
    src="/etc/passwd">`` in an HTML upload is embedded in the .docx, and a
    LaTeX ``\\input`` or RST ``include`` pulls server files into the text.
    Needs a pandoc whose docx/odt/epub writers work in the sandbox (the
    Docker image installs 3.11; Debian's 3.1.11 package fails there).
    """
    require("pandoc")
    in_path, out_path = os.path.abspath(in_path), os.path.abspath(out_path)
    cmd = ["pandoc", in_path, "-o", out_path, "--standalone", "--sandbox"]
    if from_fmt:
        cmd += ["-f", from_fmt]
    if to_fmt:
        cmd += ["-t", to_fmt]
    if out_path.lower().endswith(".pdf"):
        # --sandbox doesn't reach the PDF engine, and WeasyPrint fetches any
        # file:// URL in the HTML (<a rel="attachment"> embeds the file in
        # the PDF). Only inline data: URIs are allowed through.
        cmd += ["--pdf-engine=weasyprint", "--pdf-engine-opt=--allowed-protocols=data"]
    if extra:
        cmd += extra
    # PDF output writes its intermediate HTML into the working directory,
    # which in the image (/app) is read-only for the app user.
    with tempfile.TemporaryDirectory(prefix="pandoc_") as work:
        run(cmd, cwd=work)


# --- LibreOffice (headless) -------------------------------------------------

# Profile settings applied before every LibreOffice run: disable all document
# macros (level 3 = very high) and never update external/DDE links, so an
# uploaded Office file can't run code or fetch local/remote resources on load.
_LO_REGISTRYMODIFICATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Calc/Content/Update/Link">
  <prop oor:name="Mode" oor:op="fuse"><value>0</value></prop>
 </item>
</oor:items>
"""


def _seed_lo_profile(profile: str) -> None:
    """Write the hardening settings into a fresh LibreOffice profile."""
    user_dir = os.path.join(profile, "user")
    os.makedirs(user_dir, exist_ok=True)
    with open(os.path.join(user_dir, "registrymodifications.xcu"), "w", encoding="utf-8") as fh:
        fh.write(_LO_REGISTRYMODIFICATIONS)


def soffice_convert(
    in_path: str, out_dir: str, target_ext: str, convert_filter: str | None = None
) -> str:
    """Convert via LibreOffice headless. Returns the produced file path.

    Each call uses a private, throwaway profile dir so concurrent gunicorn
    workers don't clash on a shared UserInstallation lock. The profile is
    deleted afterwards: LibreOffice leaves ~0.5 MB in it per run, which
    used to pile up in /tmp for the life of the container. The profile is
    pre-seeded to disable macros and external-link updates.
    """
    require("soffice")
    os.makedirs(out_dir, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="lo_profile_")
    _seed_lo_profile(profile)
    to_arg = f"{target_ext}:{convert_filter}" if convert_filter else target_ext
    try:
        run(
            [
                "soffice",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--nodefault",
                # as_uri() yields a valid file URL on both POSIX and Windows paths
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--convert-to",
                to_arg,
                "--outdir",
                out_dir,
                in_path,
            ],
            timeout=240,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    base = os.path.splitext(os.path.basename(in_path))[0]
    produced = os.path.join(out_dir, f"{base}.{target_ext}")
    if not os.path.exists(produced):
        raise ConversionError("LibreOffice did not produce an output file.")
    return produced


# --- ffmpeg -----------------------------------------------------------------


def ffmpeg(
    in_path: str,
    out_path: str,
    extra: list[str] | None = None,
    input_format: str | None = None,
) -> None:
    """Run ffmpeg; the output extension picks the format, ``extra`` adds options.

    Uploads are untrusted, so the input is locked down: ``-protocol_whitelist
    file,pipe`` blocks ``http:``/``concat:``/``subfile:`` and friends, and
    ``input_format`` forces the demuxer (``-f``) instead of letting ffmpeg
    sniff it. Without that, a file with an audio extension could be read as an
    HLS or concat playlist that references local files or remote URLs.
    """
    require("ffmpeg")
    cmd = ["ffmpeg", "-y", "-protocol_whitelist", "file,pipe"]
    if input_format:
        cmd += ["-f", input_format]
    cmd += ["-i", in_path]
    if extra:
        cmd += extra
    cmd += [out_path]
    run(cmd)


# --- Calibre ----------------------------------------------------------------


def ebook_convert(in_path: str, out_path: str, extra: list[str] | None = None) -> None:
    """Run Calibre's ebook-convert; the extensions pick the formats."""
    require("ebook-convert")
    cmd = ["ebook-convert", in_path, out_path]
    if extra:
        cmd += extra
    run(cmd, timeout=300)
