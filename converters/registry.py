"""Central conversion registry — the single source of truth for the app.

Every format the app knows about is declared once in ``FORMATS``. Each
converter module (documents, images, data, ebooks, audio) registers the
``(source, target)`` pairs it can handle by calling :func:`register` at import
time. The Flask layer never hard-codes formats; it asks this registry what is
possible via :func:`matrix` and looks up the handler via :func:`get_converter`.

A handler is any callable ``fn(in_path: str, out_path: str) -> None`` that reads
``in_path`` and writes the converted result to ``out_path`` (raising on error).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

Converter = Callable[[str, str], None]


@dataclass(frozen=True)
class Format:
    ext: str  # canonical lowercase extension, no dot, e.g. "docx"
    name: str  # human label, e.g. "Word Document"
    category: str  # one of CATEGORIES


CATEGORIES = ["document", "image", "data", "ebook", "audio"]

# Single source of truth. Aliases (jpeg/jpg, yml/yaml, htm/html, markdown/md,
# tif/tiff, latex/tex) are all listed so the UI can accept whatever the user
# uploads; converter modules decide which ones they wire up.
_RAW_FORMATS: list[tuple[str, str, str]] = [
    # --- documents ---
    ("md", "Markdown", "document"),
    ("markdown", "Markdown", "document"),
    ("rst", "reStructuredText", "document"),
    ("txt", "Plain Text", "document"),
    ("html", "HTML", "document"),
    ("htm", "HTML", "document"),
    ("docx", "Word Document", "document"),
    ("odt", "OpenDocument Text", "document"),
    ("rtf", "Rich Text Format", "document"),
    ("tex", "LaTeX", "document"),
    ("latex", "LaTeX", "document"),
    ("pdf", "PDF", "document"),
    # --- images ---
    ("png", "PNG Image", "image"),
    ("jpg", "JPEG Image", "image"),
    ("jpeg", "JPEG Image", "image"),
    ("webp", "WebP Image", "image"),
    ("gif", "GIF Image", "image"),
    ("bmp", "Bitmap Image", "image"),
    ("tiff", "TIFF Image", "image"),
    ("tif", "TIFF Image", "image"),
    ("svg", "SVG Vector", "image"),
    # --- data ---
    ("csv", "CSV", "data"),
    ("tsv", "TSV", "data"),
    ("json", "JSON", "data"),
    ("xlsx", "Excel Workbook", "data"),
    ("xls", "Excel 97-2003", "data"),
    ("yaml", "YAML", "data"),
    ("yml", "YAML", "data"),
    # --- ebooks ---
    ("epub", "EPUB", "ebook"),
    ("mobi", "Mobipocket", "ebook"),
    ("azw3", "Kindle AZW3", "ebook"),
    ("fb2", "FictionBook", "ebook"),
    # --- audio ---
    ("mp3", "MP3 Audio", "audio"),
    ("wav", "WAV Audio", "audio"),
    ("ogg", "Ogg Vorbis", "audio"),
    ("flac", "FLAC Audio", "audio"),
    ("m4a", "M4A Audio", "audio"),
    ("aac", "AAC Audio", "audio"),
]

FORMATS: dict[str, Format] = {ext: Format(ext, name, cat) for ext, name, cat in _RAW_FORMATS}

# (src, dst) -> handler
_CONVERTERS: dict[tuple[str, str], Converter] = {}


class UnknownFormat(KeyError):
    pass


def _check(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext not in FORMATS:
        raise UnknownFormat(f"Unknown format: {ext!r}")
    return ext


def register(src: str, dst: str, fn: Converter) -> None:
    """Register a single conversion handler. Last registration wins."""
    src, dst = _check(src), _check(dst)
    if src == dst:
        return
    _CONVERTERS[(src, dst)] = fn


def register_many(srcs: Iterable[str], dsts: Iterable[str], fn: Converter) -> None:
    """Register the cartesian product srcs x dsts (skipping equal pairs)."""
    for s in srcs:
        for d in dsts:
            register(s, d, fn)


def get_converter(src: str, dst: str) -> Converter | None:
    """The handler for ``src`` -> ``dst``, or None (also for unknown formats)."""
    try:
        return _CONVERTERS.get((_check(src), _check(dst)))
    except UnknownFormat:
        return None


def targets_for(src: str) -> list[str]:
    """Sorted target extensions registered for ``src``."""
    src = _check(src)
    return sorted({dst for (s, dst) in _CONVERTERS if s == src})


def sources() -> set[str]:
    """Every extension at least one conversion starts from."""
    return {s for (s, _) in _CONVERTERS}


def matrix() -> dict[str, dict]:
    """Shape consumed by the UI's GET /api/formats.

    {ext: {"name", "category", "targets": [{"ext","name","category"}, ...]}}
    Only formats that can actually be *converted from* are included.
    """
    out: dict[str, dict] = {}
    for src in sorted(sources()):
        tgts = targets_for(src)
        if not tgts:
            continue
        f = FORMATS[src]
        out[src] = {
            "name": f.name,
            "category": f.category,
            "targets": [
                {"ext": t, "name": FORMATS[t].name, "category": FORMATS[t].category} for t in tgts
            ],
        }
    return out
