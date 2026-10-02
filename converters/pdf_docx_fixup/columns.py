"""Replace pdf2docx's multi-column sections with a borderless layout table."""

from __future__ import annotations

from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt
from docx.table import Table, _Cell

from .docx_xml import (
    EMU_PER_TWIP,
    W_NS,
    add_span_runs,
    cell_paragraph,
    has_opaque,
    iter_elements_paragraphs,
    set_table_borders,
    starts_new_page,
)
from .model import Layout, Row, squash

# One body section: its elements, its sectPr, and the paragraph carrying
# that sectPr (None for the body-level sectPr or a trailing run without one).
Section = dict[str, Any]


def _parse_sections(body: Any) -> list[Section]:
    """Split the body into sections at every sectPr."""
    sections, current = [], []
    for el in list(body):
        tag = el.tag
        if tag == f"{W_NS}p":
            current.append(el)
            spr = el.find(f"{W_NS}pPr/{W_NS}sectPr")
            if spr is not None:
                sections.append({"els": current, "sect": spr, "host": el})
                current = []
        elif tag == f"{W_NS}tbl":
            current.append(el)
        elif tag == f"{W_NS}sectPr":
            sections.append({"els": current, "sect": el, "host": None})
            current = []
    if current:
        sections.append({"els": current, "sect": None, "host": None})
    return sections


def _cols(sec: Section) -> int:
    """Number of text columns the section declares (1 when it says nothing)."""
    if sec["sect"] is None:
        return 1
    c = sec["sect"].find(f"{W_NS}cols")
    if c is None:
        return 1
    return int(c.get(qn("w:num")) or 1)


def _section_pages(sections: list[Section]) -> list[int]:
    """Source page index of each section.

    A page-breaking sectPr immediately followed by a multi-column section is
    pdf2docx's columns machinery, not a real source page break: the column
    zone lives on the same page."""
    page_of, pno = [], 0
    for k, sec in enumerate(sections):
        page_of.append(pno)
        if sec["sect"] is None or not starts_new_page(sec["sect"]):
            continue
        next_cols = _cols(sections[k + 1]) if k + 1 < len(sections) else 1
        if next_cols < 2:
            pno += 1
    return page_of


def _column_runs(sections: list[Section]) -> list[tuple[int, int]]:
    """(start, end) index ranges of consecutive multi-column sections."""
    runs = []
    i = 0
    while i < len(sections):
        if _cols(sections[i]) >= 2:
            j = i
            while j < len(sections) and _cols(sections[j]) >= 2:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _make_continuous(sect_pr: Any) -> None:
    """Turn a page-breaking sectPr into a continuous one."""
    t = sect_pr.find(f"{W_NS}type")
    if t is None:
        t = OxmlElement("w:type")
        sect_pr.insert(0, t)
    t.set(qn("w:val"), "continuous")


def _column_widths_tw(doc: Any, group: list[Section]) -> list[int]:
    """Cell widths in twips: the first section's explicit w:col list, or the
    text width split evenly."""
    n = len(group)
    widths_tw = []
    c = group[0]["sect"].find(f"{W_NS}cols")
    if c is not None:
        widths_tw = [int(col.get(qn("w:w")) or 0) for col in c.findall(f"{W_NS}col")]
    if len(widths_tw) != n or not all(widths_tw):
        sec_obj = doc.sections[0]
        text_tw = (
            int(sec_obj.page_width - sec_obj.left_margin - sec_obj.right_margin) // EMU_PER_TWIP
        )
        widths_tw = [text_tw // n] * n
    return widths_tw


def _layout_table(doc: Any, widths_tw: list[int]) -> Table:
    """A borderless one-row table with one cell per column."""
    table = doc.add_table(rows=1, cols=len(widths_tw))
    table.autofit = False
    set_table_borders(table, bottom_rule=False)
    for gc, w in zip(table._tbl.tblGrid.findall(f"{W_NS}gridCol"), widths_tw, strict=True):
        gc.set(qn("w:w"), str(w))
    return table


def _rebuild_cell(doc: Any, body: Any, sec: Section, cell: _Cell, rows: list[Row]) -> bool:
    """Fill ``cell`` from PDF geometry, one paragraph per source line, with
    the original formatting. Only done when the PDF rows account for all of
    the section's text and nothing a text rebuild would drop is present;
    returns False (leaving everything untouched) otherwise."""
    texts = [p.text for p in iter_elements_paragraphs(sec["els"], doc) if p.text.strip()]
    sq = squash("".join(texts))
    matched = [r for r in rows if r.squash and r.squash in sq]
    covered = sum(len(r.squash) for r in matched)
    if not (matched and covered == len(sq) and not any(has_opaque(el) for el in sec["els"])):
        return False

    matched.sort(key=lambda r: r.spans[0].y0)
    cl_x0 = min(s.x0 for r in matched for s in r.spans)
    cl_x1 = max(s.x1 for r in matched for s in r.spans)
    cl_cx = (cl_x0 + cl_x1) / 2
    prev_y1 = None
    for index, r in enumerate(matched):
        p = cell_paragraph(cell, index)
        pf = p.paragraph_format
        pf.space_after = Pt(0)
        if prev_y1 is not None:
            pf.space_before = Pt(max(r.spans[0].y0 - prev_y1, 0))
        prev_y1 = max(s.y1 for s in r.spans)
        row_x0 = min(s.x0 for s in r.spans)
        row_x1 = max(s.x1 for s in r.spans)
        # a short line centred over the column was centred in the source
        if row_x1 - row_x0 < (cl_x1 - cl_x0) * 0.85 and abs((row_x0 + row_x1) / 2 - cl_cx) <= 8:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for si, sp in enumerate(r.spans):
            add_span_runs(p, sp, " " if si < len(r.spans) - 1 else "")
    for el in sec["els"]:
        body.remove(el)
    return True


def _move_into_cell(body: Any, sec: Section, cell: _Cell) -> None:
    """Move the section's existing elements into ``cell``, in order, ahead of
    its empty placeholder paragraph. A cell must end with a w:p, so the
    placeholder goes only when a paragraph was moved last."""
    placeholder = cell.paragraphs[0]._p
    last = None
    for el in sec["els"]:
        if el is sec["host"]:
            ppr = el.find(f"{W_NS}pPr")
            spr = ppr.find(f"{W_NS}sectPr") if ppr is not None else None
            if spr is not None:
                ppr.remove(spr)
            if not el.findall(f"{W_NS}r"):
                body.remove(el)
                continue
        placeholder.addprevious(el)
        last = el
    if last is not None and last.tag == f"{W_NS}p":
        placeholder.getparent().remove(placeholder)


def _drop_section_break(body: Any, sec: Section) -> None:
    """Remove the section's own break (a host paragraph still in the body
    loses its sectPr, and goes too when it has no runs left)."""
    host = sec["host"]
    if host is None or host.getparent() is None:
        return
    ppr = host.find(f"{W_NS}pPr")
    spr = ppr.find(f"{W_NS}sectPr") if ppr is not None else None
    if spr is not None:
        ppr.remove(spr)
    if not host.findall(f"{W_NS}r"):
        body.remove(host)


def flatten_column_sections(doc: Any, layout: Layout) -> None:
    """Replace pdf2docx's multi-column sections with a real layout table.

    pdf2docx models side-by-side zones (e.g. personal data left, place/date
    right in official letters) as consecutive Word sections with
    ``w:cols num="2"`` joined by ``nextColumn`` breaks. Word and LibreOffice
    render these poorly — content reflows across the columns and the section
    boundaries often force page breaks. A borderless 1xN table with one
    column-flow per cell renders identically everywhere and is editable.

    Cell content is rebuilt from PDF geometry (one paragraph per source line,
    original formatting) when every source row can be matched; otherwise the
    existing elements are moved into the cells unchanged.
    """
    body = doc.element.body
    sections = _parse_sections(body)
    page_of = _section_pages(sections)

    for start, end in _column_runs(sections):
        group = sections[start:end]
        anchor = next((el for sec in group for el in sec["els"]), None)
        if anchor is None:
            continue
        pno = page_of[start]
        # pdf2docx closes the zone *before* the columns with a typeless
        # (page-breaking) sectPr even though it's the same source page —
        # neutralize it so the flattened flow stays on one page
        if start > 0 and page_of[start - 1] == pno:
            prev = sections[start - 1]["sect"]
            if prev is not None:
                _make_continuous(prev)

        widths_tw = _column_widths_tw(doc, group)
        table = _layout_table(doc, widths_tw)
        anchor.addprevious(table._tbl)

        rows = layout.rows[pno] if pno < len(layout.rows) else []
        for sec, cell, w in zip(group, table.rows[0].cells, widths_tw, strict=True):
            cell.width = Emu(w * EMU_PER_TWIP)
            if not _rebuild_cell(doc, body, sec, cell, rows):
                _move_into_cell(body, sec, cell)
            _drop_section_break(body, sec)
