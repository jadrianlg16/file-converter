"""Layout fixup for PDF -> DOCX, companion to ``documents.pdf_to_docx``.

pdf2docx alone reproduces three classes of layout damage (all captured by
tests/pdf_fixtures.py):

* Repeated per-page letterhead/footer content is emitted as *body* content on
  every page (often as fake layout tables) instead of real Word
  headers/footers, so editing the body dislocates every "header" below it.
* Side-by-side text (label/value forms, left|center|right rows) becomes
  "stream tables" with invented column widths and cell indents that can leave
  a *negative* usable width (text renders one character per line).
* Separate short lines are merged into justified paragraphs even when the
  source was ragged-right.

This package analyses the source PDF with PyMuPDF and repairs the converted
document with python-docx:

``analyze_pdf``             (analysis) geometry snapshot: visual rows, blocks,
                            ruled lines, filled bars, and the repeated
                            header/footer bands found by ``bands``.
``redact_bands``            (bands) strip the repeated bands from a working
                            copy of the PDF before pdf2docx sees it.
``build_header_footer``     (header_footer) rebuild the bands as a real Word
                            header/footer: 1x3 borderless table bucketed
                            left/center/right by x-position, PAGE/NUMPAGES
                            fields for varying page numbers, a separate
                            first-page part when page 1 differs.
``fix_stream_table_widths`` (tables) real column widths from PDF geometry +
                            clamp impossible cell indents.
``has_strangled_tables``    (tables) detect leftover invented-table pathology
                            so the caller can re-convert without stream tables.
``flatten_column_sections`` (columns) turn multi-column sections into a
                            borderless layout table.
``merge_row_paragraphs``    (paragraphs) re-join same-visual-row paragraphs
                            into one paragraph with tab stops at the PDF
                            x-positions.
``split_list_breaks``       (paragraphs) split merged list-like lines (1. / a)
                            / bullets) back into separate paragraphs.
``fix_justified_ragged``    (paragraphs) drop inferred justification when the
                            source block had a ragged right edge.
``restore_banner_shading``  (paragraphs) re-apply the fill behind light text.

Everything is best-effort: the repairs raise freely and the caller guards
each one, falling back to the plain pdf2docx output.
"""
from .analysis import analyze_pdf
from .bands import redact_bands
from .columns import flatten_column_sections
from .docx_xml import W_NS, body_pages
from .header_footer import build_header_footer
from .model import Layout
from .paragraphs import (
    fix_justified_ragged,
    merge_row_paragraphs,
    restore_banner_shading,
    split_list_breaks,
)
from .tables import fix_stream_table_widths, has_strangled_tables

__all__ = [
    "W_NS",
    "Layout",
    "analyze_pdf",
    "body_pages",
    "build_header_footer",
    "fix_justified_ragged",
    "fix_stream_table_widths",
    "flatten_column_sections",
    "has_strangled_tables",
    "merge_row_paragraphs",
    "redact_bands",
    "restore_banner_shading",
    "split_list_breaks",
]
