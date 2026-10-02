"""Tests for the ebook converters.

The actual conversions need Calibre's ``ebook-convert`` binary, which is rarely
present on a dev box, so those tests are decorated with ``@requires(...)`` and
SKIP when it's missing. The registry-wiring test runs everywhere.
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import requires
from converters import ebooks, get_converter

# A minimal but valid EPUB 2.0 (zip with mimetype, container, content, nav).
_CONTENT_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Tiny Test Book</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="bookid">urn:uuid:12345678-0000-0000-0000-000000000000</dc:identifier>
  </metadata>
  <manifest>
    <item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
  </spine>
</package>
"""

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter 1</title></head>
<body><h1>Chapter 1</h1><p>Hello from a tiny test book.</p></body></html>
"""

_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:12345678-0000-0000-0000-000000000000"/></head>
  <docTitle><text>Tiny Test Book</text></docTitle>
  <navMap>
    <navPoint id="np1" playOrder="1"><navLabel><text>Chapter 1</text></navLabel>
      <content src="ch1.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def _make_epub(path: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        # mimetype must be first and stored (uncompressed) per the EPUB spec.
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", _CONTAINER)
        z.writestr("content.opf", _CONTENT_OPF)
        z.writestr("ch1.xhtml", _CH1)
        z.writestr("toc.ncx", _NCX)


@requires("ebook-convert")
def test_epub_to_mobi(tmp_path):
    src = os.path.join(str(tmp_path), "book.epub")
    _make_epub(src)
    out = os.path.join(str(tmp_path), "book.mobi")
    ebooks.convert_ebook(src, out)
    assert os.path.exists(out) and os.path.getsize(out) > 0


@requires("ebook-convert")
def test_epub_to_azw3(tmp_path):
    src = os.path.join(str(tmp_path), "book.epub")
    _make_epub(src)
    out = os.path.join(str(tmp_path), "book.azw3")
    ebooks.convert_ebook(src, out)
    assert os.path.exists(out) and os.path.getsize(out) > 0


@requires("ebook-convert")
def test_epub_to_fb2(tmp_path):
    src = os.path.join(str(tmp_path), "book.epub")
    _make_epub(src)
    out = os.path.join(str(tmp_path), "book.fb2")
    ebooks.convert_ebook(src, out)
    assert os.path.exists(out) and os.path.getsize(out) > 0


@requires("ebook-convert")
def test_fb2_to_pdf(tmp_path):
    """fb2 -> pdf is owned by this module (epub->pdf is documents.py)."""
    # Build an fb2 from the epub first, then go fb2 -> pdf.
    epub = os.path.join(str(tmp_path), "book.epub")
    _make_epub(epub)
    fb2 = os.path.join(str(tmp_path), "book.fb2")
    ebooks.convert_ebook(epub, fb2)
    pdf = os.path.join(str(tmp_path), "book.pdf")
    ebooks.convert_ebook(fb2, pdf)
    assert os.path.exists(pdf) and os.path.getsize(pdf) > 0


def test_registry_wiring():
    """Ebook pairs are registered; epub->pdf is NOT owned here."""
    # all ebook<->ebook permutations
    exts = ["epub", "mobi", "azw3", "fb2"]
    for s in exts:
        for d in exts:
            if s == d:
                continue
            assert get_converter(s, d) is not None, f"missing {s}->{d}"
    # non-epub ebooks -> pdf are owned here
    assert get_converter("mobi", "pdf") is not None
    assert get_converter("azw3", "pdf") is not None
    assert get_converter("fb2", "pdf") is not None
    # epub->pdf belongs to documents.py; if present it must NOT be our handler
    epub_pdf = get_converter("epub", "pdf")
    assert epub_pdf is not ebooks.convert_ebook
