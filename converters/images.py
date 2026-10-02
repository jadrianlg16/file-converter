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
import logging
import os
import re
from typing import TYPE_CHECKING

from .engine import ConversionError
from .registry import register_many

log = logging.getLogger(__name__)

if TYPE_CHECKING:  # Pillow is imported lazily, inside the handlers
    from PIL import Image

RASTER = ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"]

# Largest decoded raster we will touch. A tiny compressed file can declare a
# huge canvas (a 668 KiB 13000x13000 PNG decodes to ~3 GiB of RGBA), so we
# check the declared size before load() and reject anything above this. Pillow
# only *warns* at ~179 Mpx by default; we make that an error too.
_MAX_IMAGE_PIXELS = 40_000_000  # 40 Mpx (e.g. ~7300x5500, well past A3 @ 600dpi)
# Cap on an SVG's rendered canvas: cairosvg allocates width*height*4 bytes, so
# a <svg width="40000" height="40000"> is ~6 GiB regardless of file size.
_MAX_SVG_PIXELS = _MAX_IMAGE_PIXELS


def _guard_pillow_bombs() -> None:
    """Make Pillow reject oversized images instead of warning or crashing.

    Sets the hard pixel ceiling and promotes the decompression-bomb *warning*
    to an error, so a declared-huge image raises before it is decoded.
    """
    import warnings

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)


def _open_raster(in_path: str):  # returns a PIL.Image.Image
    """Open a raster image with the bomb guards on, rejecting an oversized
    canvas (by declared dimensions) before any pixels are decoded."""
    from PIL import Image, UnidentifiedImageError

    _guard_pillow_bombs()
    try:
        img = Image.open(in_path)
    except UnidentifiedImageError as e:
        raise ConversionError("Unsupported or corrupt image file.") from e
    w, h = img.size
    if w * h > _MAX_IMAGE_PIXELS:
        img.close()
        raise ConversionError(
            f"Image is too large to process ({w}x{h}); the limit is "
            f"{_MAX_IMAGE_PIXELS // 1_000_000} megapixels."
        )
    return img


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
        with _open_raster(in_path) as src:
            img = _normalise_for(target, src)
            img.save(out_path, format=pil_format, **_SAVE_KWARGS.get(pil_format, {}))
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as e:
        raise ConversionError("Image is too large to process.") from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ConversionError("Image conversion failed — the file may be corrupt.") from e


# Length units cairosvg understands, in CSS px at 96 dpi.
_SVG_UNIT_PX = {
    "": 1.0,
    "px": 1.0,
    "pt": 96 / 72,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96 / 2.54,
    "mm": 96 / 25.4,
}
_SVG_LEN = re.compile(r"^\s*([0-9.]+)\s*(px|pt|pc|in|cm|mm)?\s*$", re.IGNORECASE)


def _svg_length_px(value: str) -> float | None:
    """A CSS length in px, or None for a relative/unknown unit (%, em, ...)."""
    m = _SVG_LEN.match(value)
    if not m:
        return None
    return float(m.group(1)) * _SVG_UNIT_PX[(m.group(2) or "").lower()]


def _svg_canvas_px(in_path: str) -> float | None:
    """Rendered pixel area cairosvg will allocate for this SVG, or None when
    it can't be told from width/height/viewBox. Parses only the opening <svg>
    tag's attributes with a regex, so no XML entities are expanded."""
    with open(in_path, encoding="utf-8", errors="replace") as fh:
        head = fh.read(8192)
    m = re.search(r"<svg\b[^>]*>", head, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    tag = m.group(0)
    attrs = dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', tag))
    w = _svg_length_px(attrs["width"]) if "width" in attrs else None
    h = _svg_length_px(attrs["height"]) if "height" in attrs else None
    if w is None or h is None:
        # No absolute width/height: cairosvg falls back to the viewBox size.
        vb = attrs.get("viewBox") or attrs.get("viewbox")
        if vb:
            nums = re.findall(r"-?[0-9.]+", vb)
            if len(nums) == 4:
                w, h = abs(float(nums[2])), abs(float(nums[3]))
    if w is None or h is None:
        return None
    return w * h


def svg_to_raster(in_path: str, out_path: str) -> None:
    """Rasterise an SVG to a PNG (always) then convert to the requested target
    via Pillow so we get JPEG flattening, WebP, etc. for free."""
    canvas = _svg_canvas_px(in_path)
    if canvas is not None and canvas > _MAX_SVG_PIXELS:
        raise ConversionError(
            f"SVG canvas is too large to render ({int(canvas) // 1_000_000} megapixels); "
            f"the limit is {_MAX_SVG_PIXELS // 1_000_000} megapixels."
        )
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
        log.warning("SVG conversion failed", exc_info=True)
        raise ConversionError("SVG conversion failed — the file may be malformed.") from e


def raster_to_pdf(in_path: str, out_path: str) -> None:
    """Embed a single raster image as a one-page PDF via Pillow."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with _open_raster(in_path) as src:
            img = _load_first_frame(src)
            img = ImageOps.exif_transpose(img)
            # PDF has no alpha channel; flatten onto white.
            img = _flatten_to_rgb(img)
            img.save(out_path, format="PDF", resolution=100.0)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as e:
        raise ConversionError("Image is too large to process.") from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ConversionError("Image -> PDF conversion failed — the file may be corrupt.") from e


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
        # ~144 DPI (2x the 72pt default) for a crisp result without huge files,
        # but a huge page (a malicious PDF can declare metres) would allocate
        # gigabytes, so drop the zoom to keep the pixmap under the pixel cap.
        rect = page.rect
        zoom = 2.0
        if rect.width * rect.height * zoom * zoom > _MAX_IMAGE_PIXELS:
            import math

            zoom = math.sqrt(_MAX_IMAGE_PIXELS / max(rect.width * rect.height, 1))
        # alpha=False renders onto white, so the pixmap is always plain RGB.
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img.save(out_path, format=pil_format, **_SAVE_KWARGS.get(pil_format, {}))
    except ConversionError:
        raise
    except Exception as e:  # fitz / PIL surface a variety of error types
        log.warning("PDF -> image conversion failed", exc_info=True)
        raise ConversionError("Could not render this PDF to an image.") from e
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
