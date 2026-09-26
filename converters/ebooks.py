"""Ebook conversions (Calibre's `ebook-convert`).

Scope (via Calibre's `ebook-convert`):
  * epub, mobi, azw3, fb2 -> each other (the ebook-specific set).
  * ebook -> pdf (mobi/azw3/fb2 -> pdf) via Calibre.

IMPORTANT boundary: epub<->text document formats (md/html/docx/...) is owned by
documents.py (pandoc). Here, only register the ebook<->ebook set and ebook->pdf.
Don't re-register (epub, pdf): documents.py owns epub->pdf; this module owns
mobi/azw3/fb2 -> pdf.

Calibre's ``ebook-convert`` picks the input/output format from the file
extensions, so a single handler serves every registered pair. ``engine``'s
helpers (require/run) raise ``ConversionError`` with a clear message when
Calibre is not installed.
"""
from __future__ import annotations

from . import engine
from .registry import register_many

EBOOKS = ["epub", "mobi", "azw3", "fb2"]


def convert_ebook(in_path: str, out_path: str) -> None:
    """Convert between ebook formats (and ebook -> pdf) via Calibre.

    The target format is inferred by ``ebook-convert`` from ``out_path``'s
    extension. Raises ``engine.ConversionError`` if Calibre is missing or the
    conversion fails.
    """
    engine.ebook_convert(in_path, out_path)


# Ebook <-> ebook (cartesian product; identity pairs are skipped by register).
register_many(EBOOKS, EBOOKS, convert_ebook)
# Non-epub ebooks -> pdf (epub->pdf is owned by documents.py).
register_many(["mobi", "azw3", "fb2"], ["pdf"], convert_ebook)
