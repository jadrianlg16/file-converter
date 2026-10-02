"""Audio conversions (ffmpeg).

Scope:
  * Any -> any across: mp3, wav, ogg, flac, m4a, aac (via ffmpeg).
  * Sane default codecs per target:
      mp3      -> libmp3lame -q:a 2 (VBR ~190 kbps)
      m4a/aac  -> aac -b:a 192k
      ogg      -> libvorbis -q:a 5
      flac     -> flac (lossless)
      wav      -> pcm_s16le (16-bit PCM)

All conversions shell out through engine.ffmpeg(); the ffmpeg binary is required
at run time (engine.require) but not at import time, so the package imports even
on a box without ffmpeg.
"""

from __future__ import annotations

import os

from . import engine
from .registry import register_many

AUDIO = ["mp3", "wav", "ogg", "flac", "m4a", "aac"]

# Per-target ffmpeg encoder + quality flags. Keys are output extensions.
_CODEC_ARGS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-c:a", "pcm_s16le"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "aac": ["-c:a", "aac", "-b:a", "192k"],
}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


def convert_audio(in_path: str, out_path: str) -> None:
    """Transcode ``in_path`` to ``out_path`` choosing a codec from the target
    extension. Carries metadata tags across by default (ffmpeg's behaviour).
    """
    target = _ext(out_path)
    codec_args = _CODEC_ARGS.get(target)
    if codec_args is None:
        raise engine.ConversionError(f"Unsupported audio target: {target!r}")

    extra = ["-vn"]  # drop any cover-art / video stream
    extra += codec_args
    # Raw AAC in an ADTS container needs the bitstream filter for some inputs;
    # ffmpeg adds it automatically for the .aac muxer, so no extra flag needed.
    engine.ffmpeg(in_path, out_path, extra=extra)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise engine.ConversionError("Audio conversion produced an empty file.")


# --- Registration (handler reuses engine.ffmpeg; no heavy import at top) ---

register_many(AUDIO, AUDIO, convert_audio)
