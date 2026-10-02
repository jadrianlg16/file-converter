"""Image conversions (Pillow, CairoSVG, PyMuPDF).

Scope:
  * Raster hub via Pillow: png, jpg, jpeg, webp, gif, bmp, tiff, tif -> any of
    each other. Handles RGBA->RGB flattening for jpeg/bmp, animated sources
    (first frame), and EXIF orientation.
  * svg -> raster via CairoSVG. svg is a SOURCE only (no raster->svg).
    CairoSVG (>= 2.7) does not fetch external resources by default.
  * raster -> pdf via Pillow (single page).
  * pdf -> raster: renders the FIRST page via PyMuPDF (fitz). Multi-page PDFs
    only yield page 1 — kept deliberately simple and predictable. These are
    the only pdf pairs this module owns; documents.py owns pdf->text formats.

Heavy libs (PIL, cairosvg, fitz) are imported lazily inside the handlers so the
package still imports when an optional dependency is missing locally.
"""

from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING

from .engine import ConversionError
from .registry import register_many

if TYPE_CHECKING:  # Pillow is imported lazily, inside the handlers
    from PIL import Image

RASTER = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"]

# Map an output extension to the Pillow "format" string it expects.
_PIL_FORMAT = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "gif": "GIF",
    "bmp": "BMP",
    "tiff": "TIFF",
    "tif": "TIFF",
}

# Targets that cannot store an alpha channel and must be flattened first.
_NO_ALPHA = {"jpg", "jpeg", "bmp"}

# Encoder settings per Pillow format (formats not listed use Pillow defaults).
_SAVE_KWARGS = {
    "JPEG": {"quality": 90, "optimize": True},
    "WEBP": {"quality": 90, "method": 4},
    "PNG": {"optimize": True},
    "TIFF": {"compression": "tiff_deflate"},
}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


def _pil_format_for(out_path: str, what: str) -> tuple[str, str]:
    """(target ext, Pillow format name) for ``out_path``, or ConversionError."""
    target = _ext(out_path)
    pil_format = _PIL_FORMAT.get(target)
    if pil_format is None:
        raise ConversionError(f"Unsupported {what} target: {target!r}")
    return target, pil_format


def _load_first_frame(img: Image.Image) -> Image.Image:
    """Return a single still frame for animated sources (GIF/WebP/TIFF)."""
    if getattr(img, "is_animated", False):
        img.seek(0)
    return img


def _flatten_to_rgb(
    img: Image.Image, background: tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """Composite any alpha/palette transparency onto a solid background and
    return an RGB image, suitable for formats without an alpha channel."""
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _normalise_for(target_ext: str, img: Image.Image) -> Image.Image:
    """Apply EXIF orientation, pick the first frame, and coerce the colour mode
    so it can be saved as ``target_ext``."""
    from PIL import ImageOps

    img = _load_first_frame(img)
    # Honour the camera-orientation EXIF tag, then drop it (the pixels are now
    # in the right place; a stale tag would re-rotate on the next read).
    img = ImageOps.exif_transpose(img)

    if target_ext in _NO_ALPHA:
        return _flatten_to_rgb(img)

    # PNG/WEBP/GIF/TIFF can keep alpha. Palette ("P") and CMYK round-trip more
    # reliably through RGB/RGBA, so normalise anything exotic.
    if img.mode in ("RGB", "RGBA", "L", "LA"):
        return img
    if img.mode == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode == "CMYK":
        return img.convert("RGB")
    if img.mode == "1":
        return img.convert("L")
    return img.convert("RGB")


def raster_to_raster(in_path: str, out_path: str) -> None:
    """Convert between raster formats with Pillow."""
    from PIL import Image, UnidentifiedImageError

    target, pil_format = _pil_format_for(out_path, "raster")
    try:
        with Image.open(in_path) as src:
            img = _normalise_for(target, src)
            img.save(out_path, format=pil_format, **_SAVE_KWARGS.get(pil_format, {}))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ConversionError(f"Image conversion failed: {e}") from e


def svg_to_raster(in_path: str, out_path: str) -> None:
    """Rasterise an SVG to a PNG (always) then convert to the requested target
    via Pillow so we get JPEG flattening, WebP, etc. for free."""
    try:
        import cairosvg
    except ImportError as e:
        raise ConversionError(
            "SVG conversion requires the 'cairosvg' library, which is not installed."
        ) from e
    except OSError as e:  # cairocffi loads libcairo when cairosvg is imported
        raise ConversionError(
            "SVG conversion needs the Cairo graphics library (libcairo), which was not found."
        ) from e
    from PIL import Image, UnidentifiedImageError

    target, pil_format = _pil_format_for(out_path, "SVG raster")
    try:
        if pil_format == "PNG":
            # Direct path — no intermediate decode needed.
            cairosvg.svg2png(url=in_path, write_to=out_path)
            return
        # Render to PNG bytes, then hand off to the raster pipeline for the
        # final encode (handles alpha flattening for JPEG/BMP, quality, etc.).
        png_bytes = cairosvg.svg2png(url=in_path)
        with Image.open(io.BytesIO(png_bytes)) as src:
            img = _normalise_for(target, src)
            img.save(out_path, format=pil_format, **_SAVE_KWARGS.get(pil_format, {}))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ConversionError(f"SVG conversion failed: {e}") from e


def raster_to_pdf(in_path: str, out_path: str) -> None:
    """Embed a single raster image as a one-page PDF via Pillow."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(in_path) as src:
            img = _load_first_frame(src)
            img = ImageOps.exif_transpose(img)
            # PDF has no alpha channel; flatten onto white.
            img = _flatten_to_rgb(img)
            img.save(out_path, format="PDF", resolution=100.0)
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ConversionError(f"Image -> PDF conversion failed: {e}") from e


def pdf_to_raster(in_path: str, out_path: str) -> None:
    """Rasterise the FIRST page of a PDF via PyMuPDF.

    Multi-page PDFs only produce page 1 — this keeps the single-file download
    model simple and predictable. The behaviour is documented in the module
    docstring and surfaced here for clarity.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ConversionError(
            "PDF rasterisation requires PyMuPDF (import name 'fitz'), which is not installed."
        ) from e
    from PIL import Image

    _, pil_format = _pil_format_for(out_path, "PDF raster")
    doc = None
    try:
        doc = fitz.open(in_path)
        if doc.needs_pass:
            raise ConversionError(
                "This PDF is password-protected. Remove the password and try again."
            )
        if doc.page_count == 0:
            raise ConversionError("PDF has no pages to rasterise.")
        page = doc.load_page(0)  # first page only (documented)
        # ~144 DPI (2x the 72pt default) for a crisp result without huge files.
        # alpha=False renders onto white, so the pixmap is always plain RGB.
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img.save(out_path, format=pil_format, **_SAVE_KWARGS.get(pil_format, {}))
    except ConversionError:
        raise
    except Exception as e:  # fitz / PIL surface a variety of error types
        raise ConversionError(f"PDF -> image conversion failed: {e}") from e
    finally:
        if doc is not None:
            doc.close()


# --- Registration (kept at module top level; handlers lazy-import heavy libs) ---

# Raster <-> raster.
register_many(RASTER, RASTER, raster_to_raster)
# SVG -> raster (source only).
register_many(["svg"], RASTER, svg_to_raster)
# Raster -> PDF.
register_many(RASTER, ["pdf"], raster_to_pdf)
# PDF -> raster, first page (this module owns only these pdf pairs).
register_many(["pdf"], RASTER, pdf_to_raster)
