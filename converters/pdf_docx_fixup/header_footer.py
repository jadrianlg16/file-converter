"""Rebuild the detected bands as a real Word header and footer."""

from __future__ import annotations

import io
import logging
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.image.exceptions import (
    InvalidImageStreamError,
    UnexpectedEndOfFileError,
    UnrecognizedImageError,
)
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from .docx_xml import (
    EMU_PER_PT,
    TWIPS_PER_PT,
    W_NS,
    add_span_runs,
    apply_span_format,
    cell_paragraph,
    pt,
    set_table_borders,
)
from .model import Band, Layout, Span

log = logging.getLogger(__name__)

_BUCKETS = ("left", "center", "right")
_BUCKET_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}

SpanItem = tuple[Span, list[tuple[int, int, str]]]  # a span and its field markers


def _add_field(paragraph: Paragraph, instr: str, shown: str, span: Span) -> None:
    """Append a fldSimple (PAGE / NUMPAGES) rendered like ``span``."""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(round(span.size * 2)))
    rpr.append(sz)
    if span.bold:
        rpr.append(OxmlElement("w:b"))
    r.append(rpr)
    t = OxmlElement("w:t")
    t.text = shown
    r.append(t)
    fld.append(r)
    paragraph._p.append(fld)


def _emit_span(
    paragraph: Paragraph, span: Span, markers: list[tuple[int, int, str]], trailing_space: bool
) -> None:
    """Write one span into ``paragraph`` as formatted runs, substituting
    PAGE/NUMPAGES fields at the span-local marker offsets."""
    if not markers:
        add_span_runs(paragraph, span, " " if trailing_space else "")
        return
    text = span.text
    cursor = 0
    for ms, me, instr in sorted(markers):
        if ms < cursor or me > len(text):
            continue
        if ms > cursor:
            apply_span_format(paragraph.add_run(text[cursor:ms]), span)
        _add_field(paragraph, instr, text[ms:me], span)
        cursor = me
    tail = text[cursor:] + (" " if trailing_space else "")
    if tail:
        apply_span_format(paragraph.add_run(tail), span)


def _shrink_paragraph(p: Paragraph) -> None:
    """Make an (empty) paragraph take almost no vertical space."""
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = Pt(2)
    for run in p.runs:
        run.font.size = Pt(2)


def _zero_cell_margins(table: Table) -> None:
    tbl_pr = table._tbl.tblPr
    mar = OxmlElement("w:tblCellMar")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), "0")
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tbl_pr.append(mar)


def _bucket(bbox: tuple[float, ...], W: float) -> str:
    """Which third of the page (left/center/right) a box's centre falls in."""
    cx = (bbox[0] + bbox[2]) / 2
    if cx < W * 0.4:
        return "left"
    if cx > W * 0.6:
        return "right"
    return "center"


def _bucket_band(
    band: Band, W: float
) -> tuple[dict[str, list[SpanItem]], dict[str, list[dict[str, Any]]]]:
    """Spans and images of ``band`` sorted into left/center/right buckets.

    Bucketing is per span, not per block: a single fitz block may span the
    whole page width (left text + centered title + right date in one block),
    so block-level bucketing would dump everything into the center cell."""
    spans: dict[str, list[SpanItem]] = {name: [] for name in _BUCKETS}
    for blk in band.blocks:
        for li, line in enumerate(blk.lines):
            for si, sp in enumerate(line):
                markers = blk.span_markers.get((li, si), [])
                spans[_bucket((sp.x0, sp.y0, sp.x1, sp.y1), W)].append((sp, markers))
    images: dict[str, list[dict[str, Any]]] = {name: [] for name in _BUCKETS}
    for img in band.images:
        images[_bucket(img["bbox"], W)].append(img)
    return spans, images


def _visual_lines(items: list[SpanItem]) -> list[list[SpanItem]]:
    """Group one bucket's spans into visual lines (same baseline)."""
    groups: list[list[SpanItem]] = []
    current: list[SpanItem] = []
    for item in sorted(items, key=lambda it: (it[0].y0, it[0].x0)):
        if current and abs(item[0].y0 - current[0][0].y0) > 2.5:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    return groups


def _needed_width(lines: list[list[SpanItem]], images: list[dict[str, Any]]) -> float:
    """Widest line or image of one bucket, in points (0 when empty)."""
    widths = [max(sp.x1 for sp, _ in g) - min(sp.x0 for sp, _ in g) for g in lines]
    widths += [img["bbox"][2] - img["bbox"][0] for img in images]
    return max(widths) if widths else 0.0


def _padded(width: float) -> float:
    # the 10% + 8pt slack absorbs substituted-font width growth in Word/LO
    return width * 1.10 + 8 if width else 24.0


def _column_widths(
    lines: dict[str, list[list[SpanItem]]], images: dict[str, list[dict[str, Any]]], text_w: float
) -> list[float]:
    """[left, center, right] cell widths in points.

    Widths come from the real line widths, since the docx text area may be
    narrower than the original header extent after redaction. Side columns
    are symmetric when a centered cell exists, so it stays page-centered."""
    need_l, need_c, need_r = (_needed_width(lines[n], images[n]) for n in _BUCKETS)
    if need_c and need_l and need_r:
        left_w = right_w = min(max(_padded(need_l), _padded(need_r)), text_w * 0.40)
    else:
        left_w = min(_padded(need_l), text_w - 48)
        right_w = min(_padded(need_r), text_w - left_w - 24)
    center_w = max(text_w - left_w - right_w, 24.0)
    return [left_w, center_w, right_w]


def _fill_band_cell(
    cell: _Cell, name: str, width: float, images: list[dict[str, Any]], lines: list[list[SpanItem]]
) -> None:
    """Images first, then one paragraph per visual line, aligned to the
    bucket's side of the page."""
    cell.width = Emu(int(width * EMU_PER_PT))
    for index, item in enumerate([*images, *lines]):
        p = cell_paragraph(cell, index)
        p.alignment = _BUCKET_ALIGN[name]
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        if isinstance(item, dict):
            w_pt = item["bbox"][2] - item["bbox"][0]
            try:
                p.add_run().add_picture(io.BytesIO(item["data"]), width=Emu(int(w_pt * EMU_PER_PT)))
            except (UnrecognizedImageError, InvalidImageStreamError, UnexpectedEndOfFileError):
                log.warning(
                    "Skipping a %s logo python-docx can't read", item.get("ext", "?"), exc_info=True
                )
            continue
        for gi, (sp, markers) in enumerate(item):
            _emit_span(p, sp, markers, gi < len(item) - 1)


def _estimated_height(
    lines: dict[str, list[list[SpanItem]]], images: dict[str, list[dict[str, Any]]]
) -> float:
    """Rendered band height in points: the tallest bucket (sum of its line
    heights plus its images) and room for the shrunk trailing paragraph."""
    est = 0.0
    for name in _BUCKETS:
        h = sum(max(sp.size for sp, _ in g) * 1.3 for g in lines[name])
        h += sum(img["bbox"][3] - img["bbox"][1] for img in images[name])
        est = max(est, h)
    return est + 5.0  # shrunk trailing paragraph + border


def _build_band_content(hf: Any, band: Band, layout: Layout, section: Any) -> float:
    """Build the band table inside a header/footer part. Returns the
    estimated rendered height (pt) so the caller can reserve page margin."""
    hf.is_linked_to_previous = False
    margin_l = pt(section.left_margin)
    margin_r = pt(section.right_margin)
    text_w = max(layout.page_w - margin_l - margin_r, 100.0)

    spans, images = _bucket_band(band, layout.page_w)
    lines = {name: _visual_lines(spans[name]) for name in _BUCKETS}
    widths = _column_widths(lines, images, text_w)

    table = hf.add_table(rows=1, cols=3, width=Emu(int(text_w * EMU_PER_PT)))
    table.autofit = False
    for gc, w in zip(table._tbl.tblGrid.findall(f"{W_NS}gridCol"), widths, strict=True):
        gc.set(qn("w:w"), str(int(w * TWIPS_PER_PT)))
    set_table_borders(table, bottom_rule=band.rule is not None)
    _zero_cell_margins(table)
    for name, cell, width in zip(_BUCKETS, table.rows[0].cells, widths, strict=True):
        _fill_band_cell(cell, name, width, images[name], lines[name])

    # header/footer parts start with one default empty paragraph — keep it
    # (Word wants a trailing w:p after a table) but make it invisible, and
    # move the table above it.
    anchor = hf.paragraphs[0]
    anchor._p.addprevious(table._tbl)
    _shrink_paragraph(anchor)
    return _estimated_height(lines, images)


def _first_page_band(band: Band) -> Band | None:
    """The part of ``band`` present on page 1 (None when nothing is)."""
    blocks = [b for b in band.blocks if 0 in b.occurrences]
    images = [i for i in band.images if 0 in i["occurrences"]]
    rule = band.rule if band.rule and 0 in band.rule["occurrences"] else None
    if not blocks and not images and not rule:
        return None
    return Band(blocks=blocks, rule=rule, images=images)


def build_header_footer(doc: Any, layout: Layout) -> None:
    """Rebuild the detected repeated bands as real Word header/footer on the
    first section (later sections inherit — pdf2docx never writes its own).
    Page margins grow to fit the band so it doesn't push body content."""
    section = doc.sections[0]
    header_h = footer_h = 0.0
    if layout.header:
        header_h = _build_band_content(section.header, layout.header, layout, section)
    if layout.footer:
        footer_h = _build_band_content(section.footer, layout.footer, layout, section)
    if any(b and not b.on_first_page for b in (layout.header, layout.footer)):
        # titlePg blanks BOTH first-page parts: rebuild whatever part of each
        # band page 1 does carry (already redacted from its body) there
        section.different_first_page_header_footer = True
        for band, hf in (
            (layout.header, section.first_page_header),
            (layout.footer, section.first_page_footer),
        ):
            first = _first_page_band(band) if band else None
            if first:
                _build_band_content(hf, first, layout, section)
    for sec in doc.sections:
        sec.header_distance = Pt(14)
        sec.footer_distance = Pt(14)
        if header_h:
            need = Pt(14 + header_h + 4)
            if sec.top_margin is None or sec.top_margin < need:
                sec.top_margin = need
        if footer_h:
            need = Pt(14 + footer_h + 4)
            if sec.bottom_margin is None or sec.bottom_margin < need:
                sec.bottom_margin = need
