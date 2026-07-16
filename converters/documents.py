"""Document conversions — OWNED BY THE "documents" AGENT.

Scope (all bidirectional unless noted):
  * Text hub via Pandoc: md, markdown, rst, txt, html, htm, docx, odt, rtf,
    tex, latex, epub  -> any of each other.
  * -> pdf  : from every text format above (Pandoc with weasyprint, or
    LibreOffice for docx/odt/rtf where it looks better).
  * pdf ->  : best-effort extraction to txt, md, html (PyMuPDF) and docx
    (pdf2docx).

Do NOT register image/data/audio/ebook-only pairs here. epub<->{mobi,azw3,fb2}
belongs to the ebooks module; pdf->{png,jpg,...} belongs to the images module.
This module OWNS epub->pdf.

Design notes
------------
* Pandoc is the workhorse for the text hub. We pass explicit ``-f``/``-t``
  format names (derived from the file extension) instead of relying on pandoc's
  extension sniffing, because some extensions are ambiguous (``.txt`` would be
  read as Markdown, ``.tex``/``.latex`` need the ``latex`` reader, ``.htm``
  needs the ``html`` reader, etc.). ``--standalone`` is always on so writers
  that need a document wrapper (html, latex, epub, docx...) emit a complete file.
* docx/odt/rtf -> pdf is routed through LibreOffice (``soffice``) when available
  because its layout fidelity for office documents is far better than pandoc +
  weasyprint; we fall back to pandoc if LibreOffice is missing.
* PDF is a one-way street for editing: PyMuPDF extracts text/markup for
  txt/md/html, and pdf2docx reconstructs a docx. These are explicitly
  best-effort (no OCR for scanned/image-only PDFs).
* pdf2docx recreates each PDF page as a fixed-size docx page positioned with
  exact spacing from the PDF's font metrics; Word/LibreOffice render with
  substituted fonts whose lines are slightly taller, so full pages overflow
  and spill onto extra docx pages. ``pdf_to_docx`` therefore post-processes
  the output (widow control off, bottom-margin slack) and, when LibreOffice
  is available, verifies the rendered page count against the source PDF,
  progressively tightening vertical metrics until they match.

Heavy libraries (fitz, pdf2docx) are imported lazily *inside* the handlers so
this module still imports when those libs/binaries are absent locally.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from .engine import ConversionError, have, pandoc, soffice_convert
from .registry import register, register_many

TEXT_HUB = [
    "md", "markdown", "rst", "txt", "html", "htm",
    "docx", "odt", "rtf", "tex", "latex", "epub",
]

# Map a file extension to the pandoc format name used for both reading and
# writing. Extensions that pandoc cannot unambiguously sniff are pinned here.
_PANDOC_FMT = {
    "md": "markdown",
    "markdown": "markdown",
    "rst": "rst",
    "txt": "plain",        # as a *writer*: emit plain text (no markup)
    "html": "html",
    "htm": "html",
    "docx": "docx",
    "odt": "odt",
    "rtf": "rtf",
    "tex": "latex",
    "latex": "latex",
    "epub": "epub",
}

# When *reading* a .txt file there is no "plain" reader in pandoc; Markdown is
# the right superset (a plain paragraph is valid Markdown).
_PANDOC_READER = dict(_PANDOC_FMT)
_PANDOC_READER["txt"] = "markdown"


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


# --- Text hub: any -> any via Pandoc ---------------------------------------

def text_to_text(in_path: str, out_path: str) -> None:
    """Convert between any two text-hub formats with Pandoc.

    Preserves headings, emphasis, lists, tables, links and code blocks (these
    map to native pandoc AST nodes, so they survive any source/target pairing
    pandoc supports). Handles empty inputs gracefully — pandoc emits a valid,
    possibly-empty document for each writer.
    """
    from_fmt = _PANDOC_READER.get(_ext(in_path))
    to_fmt = _PANDOC_FMT.get(_ext(out_path))

    extra: list[str] = []
    # Markdown writer: don't hard-wrap lines so round-trips stay stable.
    if to_fmt == "markdown":
        extra += ["--wrap=none"]

    pandoc(in_path, out_path, from_fmt=from_fmt, to_fmt=to_fmt,
           extra=extra or None)


# --- Anything text-ish -> PDF ----------------------------------------------

def text_to_pdf(in_path: str, out_path: str) -> None:
    """Convert a text-hub format to PDF.

    For office formats (docx/odt/rtf) LibreOffice gives much better fidelity, so
    use it when present; otherwise fall back to Pandoc + weasyprint (which the
    engine wires up automatically for ``.pdf`` outputs).
    """
    src = _ext(in_path)
    if src in ("docx", "odt", "rtf") and have("soffice"):
        out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
        produced = soffice_convert(in_path, out_dir, "pdf")
        if os.path.abspath(produced) != os.path.abspath(out_path):
            os.replace(produced, out_path)
        return

    # Pandoc path (weasyprint engine set by engine.pandoc for .pdf outputs).
    from_fmt = _PANDOC_READER.get(src)
    pandoc(in_path, out_path, from_fmt=from_fmt)


# --- PDF -> editable formats (best effort) ---------------------------------

def _pdf_blocks_text(in_path: str) -> str:
    """Extract plain text from a PDF using PyMuPDF, page by page."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - depends on local env
        raise ConversionError(
            "PyMuPDF (pymupdf / import name 'fitz') is required for PDF text "
            "extraction but is not installed."
        ) from e

    parts: list[str] = []
    try:
        with fitz.open(in_path) as doc:
            for page in doc:
                parts.append(page.get_text("text"))
    except Exception as e:
        raise ConversionError(f"Could not read PDF: {e}") from e
    # Separate pages with a blank line so paragraph structure is preserved.
    return "\n\n".join(p.strip("\n") for p in parts)


def pdf_to_txt(in_path: str, out_path: str) -> None:
    """Extract the PDF's text layer to a UTF-8 .txt file (best effort)."""
    text = _pdf_blocks_text(in_path)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)


def pdf_to_md(in_path: str, out_path: str) -> None:
    """Extract PDF text and write Markdown (best effort).

    PyMuPDF has no semantic structure for arbitrary PDFs, so we emit the text
    as Markdown paragraphs (blank-line separated, page breaks preserved). This
    keeps the output valid Markdown and readable; it is explicitly best-effort.
    """
    text = _pdf_blocks_text(in_path)
    # Normalise runs of blank lines into Markdown paragraph breaks.
    lines = [ln.rstrip() for ln in text.splitlines()]
    md = "\n".join(lines).strip() + "\n" if lines else ""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(md)


def pdf_to_html(in_path: str, out_path: str) -> None:
    """Render the PDF to standalone HTML preserving layout (best effort).

    PyMuPDF's ``page.get_text("html")`` keeps fonts/positions, so the result
    visually resembles the source. We wrap the per-page fragments in a minimal
    standalone HTML document with a UTF-8 charset.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - depends on local env
        raise ConversionError(
            "PyMuPDF (pymupdf / import name 'fitz') is required for PDF->HTML "
            "but is not installed."
        ) from e

    try:
        with fitz.open(in_path) as doc:
            body = []
            for i, page in enumerate(doc):
                frag = page.get_text("html")
                body.append(f'<section class="pdf-page" id="page-{i + 1}">{frag}</section>')
    except Exception as e:
        raise ConversionError(f"Could not read PDF: {e}") from e

    title = os.path.splitext(os.path.basename(in_path))[0]
    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        f"<title>{title}</title>\n"
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)


# Vertical-compaction ladder for the verify-and-retry loop: 1.0 keeps the
# pdf2docx metrics untouched; later steps shrink exact line heights and
# paragraph spacing just enough to absorb font-substitution growth.
_COMPACT_LADDER = (1.0, 0.96, 0.91)
# Above this page count, re-rendering the docx through LibreOffice per ladder
# step is too slow; apply a mild blind compaction instead.
_VERIFY_MAX_PAGES = 50


def _preflight_pdf(in_path: str) -> int:
    """Validate the PDF is convertible; return its page count.

    Raises a clear ConversionError for password-protected PDFs and for
    scanned/image-only PDFs (no text layer at all — OCR is not supported), so
    the user gets an explanation instead of an empty or garbled docx.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - depends on local env
        raise ConversionError(
            "PyMuPDF (pymupdf / import name 'fitz') is required for PDF->DOCX "
            "but is not installed."
        ) from e

    try:
        doc = fitz.open(in_path)
    except Exception as e:
        raise ConversionError(f"Could not read PDF: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ConversionError(
                "This PDF is password-protected. Remove the password and try again."
            )
        pages = doc.page_count
        if pages == 0:
            raise ConversionError("This PDF contains no pages.")
        sample = min(pages, 10)
        chars = sum(len(doc[i].get_text("text").strip()) for i in range(sample))
        if chars == 0:
            raise ConversionError(
                "This PDF has no extractable text layer (it looks scanned or "
                "image-only). OCR is not supported, so PDF->DOCX would come "
                "out empty."
            )
        return pages


def _iter_paragraphs(container):
    """Yield every paragraph in a Document or table cell, nested tables included."""
    for para in container.paragraphs:
        yield para
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_paragraphs(cell)


def _iter_tables(container):
    for table in container.tables:
        yield table
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_tables(cell)


def _compact_docx(path: str, vscale: float = 1.0) -> None:
    """Post-process a pdf2docx output in place so pages don't spill.

    Always: disable widow/orphan control (Word otherwise drags lines to the
    next page in pairs) and trim the bottom margin so slightly-taller rendering
    still fits the page box. With ``vscale < 1``: additionally shrink exact
    line heights, paragraph spacing and at-least row heights by that factor.
    """
    from docx import Document
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Emu, Pt

    doc = Document(path)
    # A rebuilt real footer (pdf_docx_fixup) reserves bottom margin — don't
    # trim it away. is_linked_to_previous is checked first because reading
    # .paragraphs on a linked footer would create an empty definition.
    sec0 = doc.sections[0]
    footer_in_use = (not sec0.footer.is_linked_to_previous and
                     (bool(sec0.footer.tables) or
                      any(p.text.strip() for p in sec0.footer.paragraphs)))
    for section in doc.sections:
        if not footer_in_use and section.bottom_margin is not None:
            section.bottom_margin = min(section.bottom_margin, Pt(14))

    for para in _iter_paragraphs(doc):
        pf = para.paragraph_format
        pf.widow_control = False
        if vscale >= 1.0:
            continue
        if (pf.line_spacing is not None
                and pf.line_spacing_rule in (WD_LINE_SPACING.EXACTLY,
                                             WD_LINE_SPACING.AT_LEAST)):
            pf.line_spacing = Emu(int(pf.line_spacing * vscale))
        if pf.space_before:
            pf.space_before = Emu(int(pf.space_before * vscale))
        if pf.space_after:
            pf.space_after = Emu(int(pf.space_after * vscale))

    if vscale < 1.0:
        for table in _iter_tables(doc):
            for row in table.rows:
                # Exact row heights would clip content if shrunk; only scale
                # grow-as-needed rows.
                if row.height is not None and row.height_rule != WD_ROW_HEIGHT_RULE.EXACTLY:
                    row.height = Emu(int(row.height * vscale))

    doc.save(path)


def _pdf_page_count(path: str) -> int:
    import fitz

    with fitz.open(path) as doc:
        return doc.page_count


def _docx_rendered_pages(docx_path: str) -> int | None:
    """Page count of the docx as LibreOffice lays it out; None if unavailable."""
    if not have("soffice"):
        return None
    tmpdir = tempfile.mkdtemp(prefix="fcdocx_")
    try:
        rendered = soffice_convert(docx_path, tmpdir, "pdf")
        return _pdf_page_count(rendered)
    except Exception:
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _run_pdf2docx(in_path: str, out_path: str, **settings) -> None:
    from pdf2docx import Converter

    try:
        cv = Converter(in_path)
        try:
            cv.convert(out_path, **settings)  # all pages
        finally:
            cv.close()
    except Exception as e:
        raise ConversionError(f"PDF->DOCX conversion failed: {e}") from e
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise ConversionError("PDF->DOCX produced no output.")


def _layout_fixed_docx(in_path: str, base_path: str) -> None:
    """Convert ``in_path`` with pdf2docx and repair the layout into
    ``base_path``: real Word headers/footers for repeated bands, real column
    widths for layout tables (re-converting without stream tables when they
    come out strangled), tab-stop rows, list splitting and alignment fixes.
    Every repair is best-effort; on analysis failure this degrades to the
    plain pdf2docx output."""
    from docx import Document

    from . import pdf_docx_fixup as fixup

    try:
        layout = fixup.analyze_pdf(in_path)
    except Exception:
        layout = None
    if layout is None:
        _run_pdf2docx(in_path, base_path)
        return

    work_path = in_path
    redacted = base_path + ".redacted.pdf"
    bands = layout.header or layout.footer
    if bands:
        try:
            fixup.redact_bands(in_path, redacted, layout)
            work_path = redacted
        except Exception:
            layout.header = layout.footer = None
            bands = False

    try:
        _run_pdf2docx(work_path, base_path)
        doc = Document(base_path)
        try:
            fixup.fix_stream_table_widths(doc, layout)
        except Exception:
            pass
        if fixup.has_strangled_tables(doc):
            # invented layout tables beyond repair — re-convert without
            # stream tables and rebuild the rows with tab stops instead
            _run_pdf2docx(work_path, base_path,
                          parse_stream_table=False)
            doc = Document(base_path)
        if bands:
            # after a successful redaction this must succeed, or the band
            # content would be lost: fall back to converting the intact PDF
            try:
                fixup.build_header_footer(doc, layout)
            except Exception:
                _run_pdf2docx(in_path, base_path)
                doc = Document(base_path)
                try:
                    fixup.fix_stream_table_widths(doc, layout)
                except Exception:
                    pass
                if fixup.has_strangled_tables(doc):
                    _run_pdf2docx(in_path, base_path,
                                  parse_stream_table=False)
                    doc = Document(base_path)
        for step in (lambda: fixup.merge_row_paragraphs(doc, layout),
                     lambda: fixup.split_list_breaks(doc),
                     lambda: fixup.fix_justified_ragged(doc, layout)):
            try:
                step()
            except Exception:
                pass
        doc.save(base_path)
    finally:
        _safe_remove(redacted)


def pdf_to_docx(in_path: str, out_path: str) -> None:
    """Reconstruct an editable .docx from a PDF via pdf2docx (best effort),
    then repair the layout (headers/footers, tables, paragraphs — see
    pdf_docx_fixup) and finally verify pagination so source pages don't
    spill onto extra docx pages (see the module docstring for why they
    otherwise do)."""
    try:
        import pdf2docx  # noqa: F401
    except ImportError as e:  # pragma: no cover - depends on local env
        raise ConversionError(
            "pdf2docx is required for PDF->DOCX but is not installed."
        ) from e

    src_pages = _preflight_pdf(in_path)

    base_path = out_path + ".base.docx"
    try:
        _layout_fixed_docx(in_path, base_path)

        if src_pages > _VERIFY_MAX_PAGES:
            shutil.copyfile(base_path, out_path)
            _compact_docx(out_path, vscale=0.97)
            return

        best_pages: int | None = None
        best_scale = _COMPACT_LADDER[0]
        for scale in _COMPACT_LADDER:
            shutil.copyfile(base_path, out_path)
            _compact_docx(out_path, vscale=scale)
            rendered = _docx_rendered_pages(out_path)
            if rendered is None:
                return  # can't verify (no LibreOffice) — keep untightened output
            if rendered <= src_pages:
                return  # page counts match — done
            if best_pages is None or rendered < best_pages:
                best_pages, best_scale = rendered, scale
        # No ladder step matched; keep the one that got closest.
        if best_scale != _COMPACT_LADDER[-1]:
            shutil.copyfile(base_path, out_path)
            _compact_docx(out_path, vscale=best_scale)
    finally:
        _safe_remove(base_path)


# --- Registration (top-level; handlers do the lazy importing) --------------

# Text hub: any -> any (pandoc).
register_many(TEXT_HUB, TEXT_HUB, text_to_text)
# Everything text-ish -> PDF (includes epub->pdf, which this module owns).
register_many(TEXT_HUB, ["pdf"], text_to_pdf)
# PDF -> editable (best effort).
register("pdf", "txt", pdf_to_txt)
register("pdf", "md", pdf_to_md)
register("pdf", "html", pdf_to_html)
register("pdf", "docx", pdf_to_docx)
