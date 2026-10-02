"""Regression tests for the PDF->DOCX layout repairs (pdf_docx_fixup).

Each test converts a fixture from pdf_fixtures.py and asserts the *repaired*
structure: real Word headers/footers with PAGE fields, tab-stop rows instead
of strangled tables, split clause paragraphs, and geometry-true table widths.
Render-dependent assertions need LibreOffice and skip without it.
"""
import os
import sys
from itertools import pairwise

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import requires
from converters import documents

fitz = pytest.importorskip("fitz")
pytest.importorskip("pdf2docx")
docx = pytest.importorskip("docx")

import pdf_fixtures  # noqa: E402 - after the importorskip checks

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _header_footer_xml(docx_path: str, kind: str) -> str:
    import zipfile

    out = ""
    with zipfile.ZipFile(docx_path) as z:
        for name in z.namelist():
            if kind in name and name.endswith(".xml"):
                out += z.read(name).decode("utf-8", "replace")
    return out


def _convert(tmp_path, maker, name, **kwargs):
    pdf = maker(str(tmp_path / f"{name}.pdf"), **kwargs)
    out = str(tmp_path / f"{name}.docx")
    documents.pdf_to_docx(pdf, out)
    return pdf, out


# --- Grid letterhead: real header/footer with fields ------------------------

def test_grid_letterhead_header_extracted(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_grid_letterhead, "grid")
    d = docx.Document(out)
    header = d.sections[0].header
    assert header.tables, "header should hold the rebuilt 1x3 band table"
    cells = header.tables[0].rows[0].cells
    assert "ACME" in cells[0].text
    assert "Informe Trimestral" in cells[1].text
    assert "16/07/2026" in cells[2].text
    # varying page number became a real field
    hx = _header_footer_xml(out, "header")
    assert 'w:instr="PAGE"' in hx and 'w:instr="NUMPAGES"' in hx
    fx = _header_footer_xml(out, "footer")
    assert "Documento confidencial" in fx and 'w:instr="PAGE"' in fx
    # the letterhead is no longer body content, the body heading still is
    body = "\n".join(p.text for p in d.paragraphs)
    assert "ACME" not in body and "confidencial" not in body
    assert "Seccion 1. Resumen ejecutivo" in body


@requires("soffice")
def test_grid_letterhead_page_count(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_grid_letterhead, "grid")
    assert documents._docx_rendered_pages(out) == 3


# --- Form letterhead: tab-stop rows + split clauses -------------------------

def test_form_rows_and_clauses(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_form_letterhead, "form")
    d = docx.Document(out)
    body = [p.text for p in d.paragraphs if p.text.strip()]
    # label/value pairs share one paragraph, separated by a real tab stop
    joined = [t for t in body if "Nombre:" in t and "Juan Perez Lopez" in t]
    assert joined and "\t" in joined[0]
    # no strangled 1x2 layout tables left in the body
    assert not d.tables
    # numbered clauses are separate paragraphs again
    clauses = [t for t in body if t.strip().startswith("2. Clausula")]
    assert clauses, "clause 2 should start its own paragraph"
    # header got the three buckets
    cells = d.sections[0].header.tables[0].rows[0].cells
    assert "NOTARIA 88" in cells[0].text
    assert "ACTA DE ENTREGA" in cells[1].text
    assert "Expediente" in cells[2].text


@requires("soffice")
def test_form_letterhead_page_count(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_form_letterhead, "form")
    assert documents._docx_rendered_pages(out) == 2


@requires("soffice")
def test_form_values_share_line_when_rendered(tmp_path):
    """The regression: label and value must stay on one visual line."""
    import glob
    import subprocess
    import uuid

    _, out = _convert(tmp_path, pdf_fixtures.make_form_letterhead, "form")
    outdir = str(tmp_path / "render")
    subprocess.run(
        ["soffice", "--headless", "--norestore",
         f"-env:UserInstallation=file:///tmp/lo_{uuid.uuid4().hex}",
         "--convert-to", "pdf", "--outdir", outdir, out],
        capture_output=True, timeout=180, check=False)
    pdfs = glob.glob(outdir + "/*.pdf")
    assert pdfs, "LibreOffice must render the docx"
    with fitz.open(pdfs[0]) as rendered:
        words = rendered[0].get_text("words")  # (x0, y0, x1, y1, word, ...)
    label = next(w for w in words if w[4] == "Nombre:")
    value = next(w for w in words if w[4] == "Juan")
    assert abs(label[1] - value[1]) < 3, "label and value must share a line"
    assert value[0] > label[2], "value must sit to the right of its label"


# --- Single page: no band extraction ----------------------------------------

def test_single_page_keeps_content_in_body(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_form_letterhead,
                      "single", pages=1)
    d = docx.Document(out)
    assert not d.sections[0].header.tables
    body = "\n".join(p.text for p in d.paragraphs)
    assert "NOTARIA 88" in body or any(
        "NOTARIA 88" in c.text for t in d.tables
        for r in t.rows for c in r.cells)


# --- Borderless columns become tab-stop rows --------------------------------

def test_tab_columns_alignment(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_tab_columns, "cols")
    d = docx.Document(out)
    rows = [p for p in d.paragraphs if "Tornillos" in p.text]
    assert rows and rows[0].text.count("\t") == 2
    tabs = rows[0]._p.findall(f"{NS}pPr/{NS}tabs/{NS}tab")
    assert len(tabs) == 2
    # tab positions match the PDF x-offsets (300pt and 430pt from page left)
    positions = sorted(int(t.get(f"{NS}pos")) for t in tabs)
    margin = int(d.sections[0].left_margin) // 635  # EMU -> twips
    assert abs(positions[0] + margin - 300 * 20) < 500
    assert abs(positions[1] + margin - 430 * 20) < 500


# --- Official letter: column sections flattened, banner visible -------------

def test_official_letter_structure(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_official_letter, "carta")
    import zipfile

    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    # pdf2docx's multi-column sections must be gone (they scramble the render
    # and force page splits in Word/LibreOffice)
    assert 'w:num="2"' not in xml
    # the white-on-red banner text must have regained its shading
    assert "Datos personales" in xml
    import re
    assert re.search(r'<w:shd[^>]*w:fill="[0-9A-F]{6}"', xml), \
        "banner paragraph must carry the fill of the rectangle behind it"
    # reading order: personal data stays together, before the recipient block
    d = docx.Document(out)
    full = []
    for p in d.paragraphs:
        full.append(p.text)
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                full.append(c.text)
    joined = "\n".join(full)
    for needle in ["GARCIA LOPEZ JUAN ALBERTO", "NSS: 12345678901",
                   "CURP: GALJ850101HNLRPN09", "MONTERREY, NUEVO LEON",
                   "P R E S E N T E", "A T E N T A M E N T E"]:
        assert needle in joined, f"missing: {needle}"


@requires("soffice")
def test_official_letter_single_page(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_official_letter, "carta")
    assert documents._docx_rendered_pages(out) == 1


# --- Ruled table keeps real column widths ------------------------------------

def test_lattice_table_real_widths(tmp_path):
    _, out = _convert(tmp_path, pdf_fixtures.make_lattice_table, "lattice")
    d = docx.Document(out)
    assert d.tables, "ruled table must survive as a real table"
    t = d.tables[0]
    assert len(t.columns) == 3 and len(t.rows) == 3
    widths = [int(g.get(f"{NS}w"))
              for g in t._tbl.tblGrid.findall(f"{NS}gridCol")]
    real = [(b - a) * 20 for a, b in pairwise(pdf_fixtures.LATTICE_COLS)]
    for got, want in zip(widths, real, strict=True):
        assert abs(got - want) <= 200, (widths, real)
    assert t.rows[1].cells[0].text.strip() == "Cemento gris 50kg"
