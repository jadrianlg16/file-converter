"""Tests for the media converters (images + audio).

Pillow-based image tests run whenever Pillow is importable. The svg and pdf
raster paths additionally need cairosvg / PyMuPDF (fitz) and skip when those are
missing. Audio tests require the ffmpeg binary and skip otherwise. Every test
generates its own tiny fixture, so the suite is self-contained.
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import requires
from converters import audio, images


def _has_module(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


has_pillow = _has_module("PIL")
has_cairosvg = _has_module("cairosvg")
has_fitz = _has_module("fitz")

requires_pillow = pytest.mark.skipif(not has_pillow, reason="Pillow not installed")
requires_cairosvg = pytest.mark.skipif(not has_cairosvg, reason="cairosvg not installed")
requires_fitz = pytest.mark.skipif(not has_fitz, reason="PyMuPDF (fitz) not installed")


def _nonempty(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


# --------------------------------------------------------------------------- #
# Image fixtures
# --------------------------------------------------------------------------- #


def _make_png(path: str, mode="RGBA", size=(2, 2), color=(255, 0, 0, 128)):
    from PIL import Image

    Image.new(mode, size, color).save(path)
    return path


# --------------------------------------------------------------------------- #
# Raster <-> raster
# --------------------------------------------------------------------------- #


@requires_pillow
@pytest.mark.parametrize("target", ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"])
def test_png_to_each_raster(tmp_path, target):
    from PIL import Image

    src = _make_png(str(tmp_path / "src.png"))
    out = str(tmp_path / f"out.{target}")
    images.raster_to_raster(src, out)
    assert _nonempty(out)
    with Image.open(out) as im:
        im.load()  # decodes successfully -> valid image


@requires_pillow
def test_rgba_to_jpeg_is_flattened_rgb(tmp_path):
    from PIL import Image

    src = _make_png(str(tmp_path / "src.png"), color=(0, 255, 0, 0))
    out = str(tmp_path / "out.jpg")
    images.raster_to_raster(src, out)
    with Image.open(out) as im:
        assert im.mode == "RGB"  # alpha removed


@requires_pillow
def test_animated_gif_takes_first_frame(tmp_path):
    from PIL import Image

    gif = str(tmp_path / "anim.gif")
    frames = [Image.new("RGB", (4, 4), c) for c in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=80, loop=0)

    out = str(tmp_path / "frame.png")
    images.raster_to_raster(gif, out)
    assert _nonempty(out)
    with Image.open(out) as im:
        assert not getattr(im, "is_animated", False)  # single still frame


@requires_pillow
def test_invalid_image_raises_conversion_error(tmp_path):
    from converters.engine import ConversionError

    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not really an image")
    with pytest.raises(ConversionError):
        images.raster_to_raster(str(bad), str(tmp_path / "out.jpg"))


# --------------------------------------------------------------------------- #
# Raster -> PDF
# --------------------------------------------------------------------------- #


@requires_pillow
def test_raster_to_pdf(tmp_path):
    src = _make_png(str(tmp_path / "src.png"), mode="RGB", color=(10, 20, 30))
    out = str(tmp_path / "out.pdf")
    images.raster_to_pdf(src, out)
    assert _nonempty(out)
    with open(out, "rb") as f:
        assert f.read(5) == b"%PDF-"


# --------------------------------------------------------------------------- #
# SVG -> raster (needs cairosvg)
# --------------------------------------------------------------------------- #

_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    '<rect width="8" height="8" fill="#3366cc"/></svg>'
)


@requires_pillow
@requires_cairosvg
@pytest.mark.parametrize("target", ["png", "jpg", "webp"])
def test_svg_to_raster(tmp_path, target):
    from PIL import Image

    svg = tmp_path / "in.svg"
    svg.write_text(_SVG, encoding="utf-8")
    out = str(tmp_path / f"out.{target}")
    images.svg_to_raster(str(svg), out)
    assert _nonempty(out)
    with Image.open(out) as im:
        im.load()


def test_svg_without_cairosvg_gives_clear_error(tmp_path, monkeypatch):
    """If cairosvg can't be imported, the handler must raise ConversionError
    (not ImportError) with an actionable message."""
    from converters.engine import ConversionError

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "cairosvg":
            raise ImportError("simulated missing cairosvg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    svg = tmp_path / "in.svg"
    svg.write_text(_SVG, encoding="utf-8")
    with pytest.raises(ConversionError):
        images.svg_to_raster(str(svg), str(tmp_path / "out.png"))


# --------------------------------------------------------------------------- #
# PDF -> raster (needs fitz + Pillow)
# --------------------------------------------------------------------------- #


@requires_pillow
@requires_fitz
@pytest.mark.parametrize("target", ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"])
def test_pdf_to_raster_first_page(tmp_path, target):
    import fitz
    from PIL import Image

    from converters import get_converter

    assert get_converter("pdf", target) is images.pdf_to_raster

    pdf = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    doc.new_page(width=72, height=72)
    doc.new_page(width=144, height=72)  # 2 pages: only page 1 should render
    doc.save(pdf)
    doc.close()

    out = str(tmp_path / f"page.{target}")
    images.pdf_to_raster(pdf, out)
    assert _nonempty(out)
    with Image.open(out) as im:
        im.load()
        assert im.size == (144, 144)  # page 1 (72pt square) at 2x zoom


# --------------------------------------------------------------------------- #
# Audio (needs ffmpeg)
# --------------------------------------------------------------------------- #


def _make_wav(path: str, duration="0.3"):
    """Generate a short sine-tone WAV via ffmpeg."""
    from converters import engine

    engine.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            path,
        ]
    )
    return path


@requires("ffmpeg")
@pytest.mark.parametrize("target", ["mp3", "wav", "ogg", "flac", "m4a", "aac"])
def test_wav_to_each_audio(tmp_path, target):
    src = _make_wav(str(tmp_path / "tone.wav"))
    out = str(tmp_path / f"out.{target}")
    audio.convert_audio(src, out)
    assert _nonempty(out)


@requires("ffmpeg")
def test_audio_roundtrip_mp3_to_wav(tmp_path):
    wav = _make_wav(str(tmp_path / "tone.wav"))
    mp3 = str(tmp_path / "tone.mp3")
    audio.convert_audio(wav, mp3)
    back = str(tmp_path / "back.wav")
    audio.convert_audio(mp3, back)
    assert _nonempty(back)


@requires("ffmpeg")
def test_audio_unsupported_target_raises(tmp_path):
    from converters.engine import ConversionError

    wav = _make_wav(str(tmp_path / "tone.wav"))
    with pytest.raises(ConversionError):
        audio.convert_audio(wav, str(tmp_path / "out.xyz"))
