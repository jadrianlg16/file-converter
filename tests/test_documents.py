"""Tests for the documents converter module.

Engine-dependent tests SKIP (never fail) when the needed binary/library is
absent locally, so the suite is meaningful on a bare dev box and inside the
full Docker image alike:
  * pandoc-backed tests use ``@requires("pandoc")``.
  * LibreOffice-backed tests use ``@requires("soffice")``.
  * PyMuPDF / pdf2docx tests use ``pytest.importorskip`` so they skip when the
    Python library is not installed.

Fixtures are tiny and generated in temp dirs.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import requires
from converters import documents, get_converter
from converters.engine import ConversionError

MD_SAMPLE = """# Title

A paragraph with **bold** and *italic* text and a [link](https://example.com).

## Subheading

- item one
- item two

1. first
2. second

| A | B |
|---|---|
| 1 | 2 |

```python
print("hello")
```
"""


def _write(path: str, text: str, encoding: str = "utf-8") -> str:
    with open(path, "w", encoding=encoding) as fh:
        fh.write(text)
    return path


def _nonempty(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


# --- Registration / wiring (no engine needed) ------------------------------


def test_text_hub_pairs_registered():
    # A representative spread of the text hub any<->any matrix.
    for src, dst in [
        ("md", "html"),
        ("html", "md"),
        ("rst", "docx"),
        ("docx", "md"),
        ("md", "rst"),
        ("odt", "html"),
        ("tex", "html"),
        ("epub", "html"),
    ]:
        assert get_converter(src, dst) is not None, f"missing {src}->{dst}"


def test_to_pdf_pairs_registered():
    for src in [
        "md",
        "markdown",
        "rst",
        "txt",
        "html",
        "htm",
        "docx",
        "odt",
        "rtf",
        "tex",
        "latex",
        "epub",
    ]:
        assert get_converter(src, "pdf") is not None, f"missing {src}->pdf"


def test_epub_to_pdf_owned_here():
    # This module owns epub->pdf; the handler must be ours.
    assert get_converter("epub", "pdf") is documents.text_to_pdf


def test_pdf_export_pairs_registered():
    assert get_converter("pdf", "txt") is documents.pdf_to_txt
    assert get_converter("pdf", "md") is documents.pdf_to_md
    assert get_converter("pdf", "html") is documents.pdf_to_html
    assert get_converter("pdf", "docx") is documents.pdf_to_docx


def test_no_image_or_ebook_only_pairs_registered():
    # Boundaries: images own pdf->png/jpg; ebooks own epub->mobi/azw3/fb2.
    for bad in [
        ("pdf", "png"),
        ("pdf", "jpg"),
        ("epub", "mobi"),
        ("epub", "azw3"),
        ("epub", "fb2"),
    ]:
        fn = get_converter(*bad)
        # Either unregistered, or owned by another module (not ours).
        if fn is not None:
            assert fn.__module__ != documents.__name__, f"{bad} should not be ours"


def test_module_imports_without_heavy_libs():
    # Importing the module must not require fitz/pdf2docx/pandoc to be present.
    import importlib

    importlib.reload(documents)  # should not raise


# --- Pandoc text hub round-trips -------------------------------------------


@requires("pandoc")
def test_md_to_html_preserves_structure(tmp_path):
    src = _write(str(tmp_path / "in.md"), MD_SAMPLE)
    out = str(tmp_path / "out.html")
    documents.text_to_text(src, out)
    assert _nonempty(out)
    html = Path(out).read_text(encoding="utf-8").lower()
    assert "<h1" in html and "<h2" in html
    assert "<strong>" in html and "<em>" in html
    # pandoc 3.x emits attributes on list tags (e.g. <ol type="1">).
    assert "<ul" in html and "<ol" in html
    assert "<table" in html
    assert "<a href=" in html
    assert "<code" in html or "<pre" in html


@requires("pandoc")
def test_md_html_md_roundtrip(tmp_path):
    src = _write(str(tmp_path / "in.md"), MD_SAMPLE)
    mid = str(tmp_path / "mid.html")
    back = str(tmp_path / "back.md")
    documents.text_to_text(src, mid)
    documents.text_to_text(mid, back)
    assert _nonempty(back)
    text = Path(back).read_text(encoding="utf-8")
    assert "Title" in text
    assert "**bold**" in text or "bold" in text
    assert "item one" in text


@requires("pandoc")
def test_md_to_rst(tmp_path):
    src = _write(str(tmp_path / "in.md"), MD_SAMPLE)
    out = str(tmp_path / "out.rst")
    documents.text_to_text(src, out)
    assert _nonempty(out)
    assert "Title" in Path(out).read_text(encoding="utf-8")


@requires("pandoc")
def test_md_to_docx_binary_output(tmp_path):
    src = _write(str(tmp_path / "in.md"), MD_SAMPLE)
    out = str(tmp_path / "out.docx")
    documents.text_to_text(src, out)
    assert _nonempty(out)
    # docx is a zip; first bytes are "PK".
    assert Path(out).read_bytes()[:2] == b"PK"


@requires("pandoc")
def test_txt_input_read_as_text(tmp_path):
    src = _write(str(tmp_path / "in.txt"), "Just a plain line of text.\n")
    out = str(tmp_path / "out.html")
    documents.text_to_text(src, out)
    assert _nonempty(out)
    assert "plain line" in Path(out).read_text(encoding="utf-8")


@requires("pandoc")
def test_empty_input_produces_valid_output(tmp_path):
    src = _write(str(tmp_path / "empty.md"), "")
    out = str(tmp_path / "out.html")
    documents.text_to_text(src, out)
    # Empty doc still yields a standalone HTML skeleton.
    assert _nonempty(out)
    assert "<html" in Path(out).read_text(encoding="utf-8").lower()


@requires("pandoc")
def test_utf8_is_preserved(tmp_path):
    src = _write(str(tmp_path / "u.md"), "# Café — naïve résumé ☕\n")
    out = str(tmp_path / "out.html")
    documents.text_to_text(src, out)
    assert "Café" in Path(out).read_text(encoding="utf-8")


# --- To PDF -----------------------------------------------------------------


@requires("pandoc")
def test_md_to_pdf_pandoc(tmp_path):
    # Needs pandoc AND weasyprint; skip if weasyprint is missing.
    pytest.importorskip("weasyprint")
    src = _write(str(tmp_path / "in.md"), MD_SAMPLE)
    out = str(tmp_path / "out.pdf")
    documents.text_to_pdf(src, out)
    assert _nonempty(out)
    assert Path(out).read_bytes()[:5] == b"%PDF-"


@requires("soffice")
def test_docx_to_pdf_libreoffice(tmp_path):
    # Build a docx first with pandoc if available, else skip.
    import shutil

    if shutil.which("pandoc") is None:
        pytest.skip("pandoc needed to build the docx fixture")
    docx = str(tmp_path / "in.docx")
    documents.text_to_text(_write(str(tmp_path / "in.md"), MD_SAMPLE), docx)
    out = str(tmp_path / "out.pdf")
    documents.text_to_pdf(docx, out)
    assert _nonempty(out)
    assert Path(out).read_bytes()[:5] == b"%PDF-"


# --- Untrusted input: pandoc must not read server files --------------------

SECRET = b"FC-SECRET-MARKER-7f3a"


def _secret_file(tmp_path: Path) -> Path:
    """A stand-in for a server file (/etc/passwd, another user's upload)."""
    path = tmp_path / "server-secret.txt"
    path.write_bytes(SECRET + b"\n")
    return path


def _zip_contains(path: str, needle: bytes) -> bool:
    import zipfile

    with zipfile.ZipFile(path) as z:
        return any(needle in z.read(name) for name in z.namelist())


@requires("pandoc")
@pytest.mark.parametrize(
    "target", ["docx", "odt", "epub", "html", "md", "rtf", "rst", "tex", "txt"]
)
def test_every_pandoc_writer_works_sandboxed(tmp_path, target):
    # Older pandoc builds (Debian's 3.1.11) can't reach their own data files
    # under --sandbox, so docx/odt/epub output fails outright.
    src = _write(str(tmp_path / "in.md"), MD_SAMPLE)
    out = str(tmp_path / f"out.{target}")
    documents.text_to_text(src, out)
    assert _nonempty(out)


@requires("pandoc")
@pytest.mark.parametrize("target", ["docx", "odt", "epub"])
def test_html_image_cannot_embed_a_server_file(tmp_path, target):
    secret = _secret_file(tmp_path)
    src = _write(str(tmp_path / "in.html"), f'<p>hello</p><img src="{secret.as_posix()}" alt="x">')
    out = str(tmp_path / f"out.{target}")
    documents.text_to_text(src, out)
    assert _nonempty(out)
    assert not _zip_contains(out, SECRET)


@requires("pandoc")
@pytest.mark.parametrize(
    "name, body",
    [
        ("in.tex", "\\documentclass{article}\\begin{document}Hi \\input{%s}\\end{document}"),
        ("in.rst", "Hi\n\n.. include:: %s\n"),
    ],
)
def test_include_directives_cannot_read_server_files(tmp_path, name, body):
    src = _write(str(tmp_path / name), body % _secret_file(tmp_path).as_posix())
    out = str(tmp_path / "out.md")
    try:
        documents.text_to_text(src, out)
    except ConversionError:
        return  # refusing the document is as good as leaving the file out
    assert SECRET.decode() not in Path(out).read_text(encoding="utf-8")


@requires("pandoc")
def test_pdf_engine_cannot_attach_a_server_file(tmp_path):
    pytest.importorskip("weasyprint")
    fitz = pytest.importorskip("fitz")
    secret = _secret_file(tmp_path)
    src = _write(
        str(tmp_path / "in.md"),
        f'Hello\n\n<a rel="attachment" href="{secret.as_uri()}">a</a>\n\n'
        f'<img src="{secret.as_posix()}">\n',
    )
    out = str(tmp_path / "out.pdf")
    documents.text_to_pdf(src, out)
    with fitz.open(out) as doc:
        assert "Hello" in doc[0].get_text()
        streams = [
            doc.xref_stream(x) or b"" for x in range(1, doc.xref_length()) if doc.xref_is_stream(x)
        ]
    assert not any(SECRET in s for s in streams)


# --- PDF -> editable (PyMuPDF / pdf2docx) ----------------------------------


def _make_pdf(path: str, text: str = "Hello PDF world.") -> str:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def test_pdf_to_txt(tmp_path):
    pdf = _make_pdf(str(tmp_path / "in.pdf"), "Hello PDF world.")
    out = str(tmp_path / "out.txt")
    documents.pdf_to_txt(pdf, out)
    assert _nonempty(out)
    assert "Hello PDF world." in Path(out).read_text(encoding="utf-8")


def test_pdf_to_md(tmp_path):
    pdf = _make_pdf(str(tmp_path / "in.pdf"), "Some markdown body text.")
    out = str(tmp_path / "out.md")
    documents.pdf_to_md(pdf, out)
    assert _nonempty(out)
    assert "markdown body text" in Path(out).read_text(encoding="utf-8")


def test_pdf_to_html(tmp_path):
    pdf = _make_pdf(str(tmp_path / "in.pdf"), "Body for HTML export.")
    out = str(tmp_path / "out.html")
    documents.pdf_to_html(pdf, out)
    assert _nonempty(out)
    html = Path(out).read_text(encoding="utf-8").lower()
    assert "<html" in html and "<body" in html
    assert "html export" in html


def test_pdf_to_docx(tmp_path):
    pytest.importorskip("pdf2docx")
    pdf = _make_pdf(str(tmp_path / "in.pdf"), "Content for docx reconstruction.")
    out = str(tmp_path / "out.docx")
    documents.pdf_to_docx(pdf, out)
    assert _nonempty(out)
    assert Path(out).read_bytes()[:2] == b"PK"


def _make_dense_pdf(path: str, pages: int = 2) -> str:
    """A PDF whose pages are filled edge to edge — the layout that used to
    make pdf2docx output spill each source page onto two docx pages.

    Headings use letters, not numbers: a per-page number at the top that
    happens to equal the page index would legitimately be detected as a page
    number and moved into a real Word header by pdf_docx_fixup."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for pg in range(pages):
        page = doc.new_page()  # A4: 595 x 842 pt
        y = 56.0
        page.insert_text((72, y), f"Section {chr(65 + pg)}", fontsize=16, fontname="hebo")
        y += 28
        while y < 806:
            page.insert_text((72, y), "lorem ipsum dolor sit amet consectetur " * 2, fontsize=10)
            y += 12.6
    doc.save(path)
    doc.close()
    return path


def test_pdf_to_docx_dense_multipage_content(tmp_path):
    pytest.importorskip("pdf2docx")
    docx_mod = pytest.importorskip("docx")
    pdf = _make_dense_pdf(str(tmp_path / "dense.pdf"), pages=2)
    out = str(tmp_path / "out.docx")
    documents.pdf_to_docx(pdf, out)
    assert _nonempty(out)
    d = docx_mod.Document(out)
    text = "\n".join(p.text for p in d.paragraphs)
    assert "Section A" in text and "Section B" in text
    # Post-processing must have disabled widow/orphan control everywhere.
    assert all(p.paragraph_format.widow_control is False for p in d.paragraphs)


@requires("soffice")
def test_pdf_to_docx_page_count_preserved(tmp_path):
    # The regression this suite exists for: a dense N-page PDF must yield a
    # docx that still paginates to N pages, not 2N.
    pytest.importorskip("pdf2docx")
    pdf = _make_dense_pdf(str(tmp_path / "dense.pdf"), pages=3)
    out = str(tmp_path / "out.docx")
    documents.pdf_to_docx(pdf, out)
    assert documents._docx_rendered_pages(out) == 3


def test_pdf_to_docx_rejects_scanned(tmp_path):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("pdf2docx")
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(50, 50, 500, 700), fill=(0.8, 0.8, 0.8))  # no text layer
    pdf = str(tmp_path / "scan.pdf")
    doc.save(pdf)
    doc.close()
    with pytest.raises(ConversionError, match="text layer"):
        documents.pdf_to_docx(pdf, str(tmp_path / "out.docx"))


def test_pdf_to_docx_rejects_encrypted(tmp_path):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("pdf2docx")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret text here")
    pdf = str(tmp_path / "enc.pdf")
    doc.save(pdf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    with pytest.raises(ConversionError, match="password"):
        documents.pdf_to_docx(pdf, str(tmp_path / "out.docx"))


@pytest.mark.parametrize("fn", ["pdf_to_txt", "pdf_to_md", "pdf_to_html"])
def test_pdf_text_exports_reject_encrypted_clearly(tmp_path, fn):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret text here")
    pdf = str(tmp_path / "enc.pdf")
    doc.save(pdf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    doc.close()
    with pytest.raises(ConversionError, match="password"):
        getattr(documents, fn)(pdf, str(tmp_path / "out"))


def test_pdf_to_html_title_comes_from_metadata_and_is_escaped(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "body")
    doc.set_metadata({"title": "Q3 <draft> & notes"})
    pdf = str(tmp_path / "in.pdf")
    doc.save(pdf)
    doc.close()
    out = str(tmp_path / "out.html")
    documents.pdf_to_html(pdf, out)
    html = Path(out).read_text(encoding="utf-8")
    assert "<title>Q3 &lt;draft&gt; &amp; notes</title>" in html


def test_pdf_to_md_collapses_blank_runs(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Page one")
    doc.new_page()  # blank page in the middle
    doc.new_page().insert_text((72, 72), "Page three")
    pdf = str(tmp_path / "in.pdf")
    doc.save(pdf)
    doc.close()
    out = str(tmp_path / "out.md")
    documents.pdf_to_md(pdf, out)
    assert Path(out).read_text(encoding="utf-8") == "Page one\n\nPage three\n"


def test_strangled_table_check_failure_is_not_fatal(monkeypatch, caplog):
    from converters import pdf_docx_fixup

    def boom(_doc):
        raise RuntimeError("analysis bug")

    monkeypatch.setattr(pdf_docx_fixup, "has_strangled_tables", boom)
    assert documents._strangled(object()) is False
    assert "strangled-table check failed" in caplog.text  # logged, not silent


def test_failing_repair_step_is_logged_and_conversion_continues(tmp_path, monkeypatch, caplog):
    pytest.importorskip("pdf2docx")
    from converters import pdf_docx_fixup

    def boom(*_args):
        raise RuntimeError("repair bug")

    monkeypatch.setattr(pdf_docx_fixup, "merge_row_paragraphs", boom)
    pdf = _make_pdf(str(tmp_path / "in.pdf"), "Still converted.")
    out = str(tmp_path / "out.docx")
    documents.pdf_to_docx(pdf, out)
    assert _nonempty(out)
    assert "row merging failed" in caplog.text and "repair bug" in caplog.text
