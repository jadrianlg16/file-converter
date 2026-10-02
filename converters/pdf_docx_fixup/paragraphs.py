"""Paragraph repairs: tab-stop rows, split list items, alignment, banners."""
from __future__ import annotations

import copy
import re
from typing import Any

import fitz  # PyMuPDF
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph

from .docx_xml import W_NS, add_span_runs, body_pages, has_opaque, iter_elements_paragraphs, pt
from .model import Layout, Row, squash

# A row qualifies for tab-stop merging when neighbouring spans are separated
# by at least this gap (pt) — smaller gaps are just word spacing.
_ROW_GAP_PT = 12.0
# How many consecutive docx paragraphs one PDF row may have been split into.
_MAX_ROW_PIECES = 6

_LIST_START = re.compile(
    r"^\s*(\d{1,3}[.)°:]\s|[IVXLC]{1,6}[.)]\s|[a-z][.)]\s|[•▪◦‣∙*-]\s)",
    re.IGNORECASE)


# --- Tab-stop rows ---------------------------------------------------------

def _tab_rows(rows: list[Row]) -> list[Row]:
    """Rows with at least one wide gap between neighbouring spans."""
    out = []
    for row in rows:
        if len(row.spans) < 2:
            continue
        gaps = [row.spans[i + 1].x0 - row.spans[i].x1
                for i in range(len(row.spans) - 1)]
        if max(gaps) >= _ROW_GAP_PT:
            out.append(row)
    return out


def _match_row(paras: list[Paragraph], i: int, candidates: list[Row],
               used: set[int]) -> tuple[int, Row, int] | None:
    """The first unused candidate row whose text is exactly paras[i:j]
    joined (at least two paragraphs), as (row index, row, j)."""
    acc = squash(paras[i].text)
    for ridx, row in enumerate(candidates):
        if ridx in used or not row.squash.startswith(acc):
            continue
        j, a = i + 1, acc
        while (len(a) < len(row.squash) and j < len(paras)
               and j - i < _MAX_ROW_PIECES):
            nxt = a + squash(paras[j].text)
            if not row.squash.startswith(nxt):
                break
            a = nxt
            j += 1
        if a == row.squash and j - i >= 2:
            return ridx, row, j
    return None


def _tab_paragraph(first: Paragraph, row: Row, doc: Any, margin_l: float,
                   right_edge: float) -> Paragraph:
    """A new paragraph before ``first`` holding ``row``'s spans, each
    reached by a tab stop at its PDF x-position (right-aligned when the span
    ends at the right margin)."""
    new_p = OxmlElement("w:p")
    first._p.addprevious(new_p)
    merged = Paragraph(new_p, doc)
    pf = merged.paragraph_format
    pf.space_before = first.paragraph_format.space_before
    pf.space_after = Pt(0)
    for k, sp in enumerate(row.spans):
        if k == 0:
            if sp.x0 - margin_l > 3:
                pf.tab_stops.add_tab_stop(Pt(sp.x0 - margin_l),
                                          WD_TAB_ALIGNMENT.LEFT)
                merged.add_run("\t")
        else:
            if abs(sp.x1 - right_edge) <= 8:
                pf.tab_stops.add_tab_stop(Pt(right_edge - margin_l),
                                          WD_TAB_ALIGNMENT.RIGHT)
            else:
                pf.tab_stops.add_tab_stop(Pt(sp.x0 - margin_l),
                                          WD_TAB_ALIGNMENT.LEFT)
            merged.add_run("\t")
        add_span_runs(merged, sp)
    return merged


def merge_row_paragraphs(doc: Any, layout: Layout) -> None:
    """Re-join consecutive paragraphs that were one visual row in the PDF
    into a single paragraph with tab stops at the original x-positions."""
    for pno, elements in enumerate(body_pages(doc)):
        if pno >= len(layout.rows):
            break
        section = doc.sections[min(pno, len(doc.sections) - 1)]
        margin_l = pt(section.left_margin)
        right_edge = layout.page_w - pt(section.right_margin)
        candidates = _tab_rows(layout.rows[pno])
        if not candidates:
            continue

        paras = [Paragraph(el, doc) for el in elements
                 if el.tag == f"{W_NS}p" and
                 el.find(f"{W_NS}pPr/{W_NS}sectPr") is None]
        paras = [p for p in paras if p.text.strip()]
        used: set[int] = set()
        i = 0
        while i < len(paras):
            hit = _match_row(paras, i, candidates, used)
            # the merged paragraph is rebuilt from PDF text only
            if not hit or any(has_opaque(p._p) for p in paras[i:hit[2]]):
                i += 1
                continue
            ridx, row, j = hit
            used.add(ridx)
            merged = _tab_paragraph(paras[i], row, doc, margin_l, right_edge)
            for p in paras[i:j]:
                p._p.getparent().remove(p._p)
            paras[i:j] = [merged]
            i += 1


# --- List items merged into one paragraph -----------------------------------

def _isolate_breaks(el: Any) -> None:
    """Rewrite ``el``'s runs so every w:br sits alone in its own run."""
    for r in list(el.findall(f"{W_NS}r")):
        children = list(r)
        brs = [c for c in children if c.tag == f"{W_NS}br"]
        if not brs or len(children) <= 1:
            continue
        rpr = r.find(f"{W_NS}rPr")
        groups: list[Any] = []
        cur: list[Any] = []
        for c in children:
            if c.tag == f"{W_NS}rPr":
                continue
            if c.tag == f"{W_NS}br":
                groups.append(cur)
                groups.append("BR")
                cur = []
            else:
                cur.append(c)
        groups.append(cur)
        prev = r
        for g in groups:
            nr = OxmlElement("w:r")
            if rpr is not None:
                nr.append(copy.deepcopy(rpr))
            if g == "BR":
                nr.append(OxmlElement("w:br"))
            else:
                for c in g:
                    nr.append(c)
            prev.addnext(nr)
            prev = nr
        r.getparent().remove(r)


def _segments(el: Any) -> tuple[list[list[Any]], list[Any]]:
    """The paragraph's runs split at break-only runs: (segments, breaks),
    where breaks[k] precedes segments[k + 1]."""
    segments, breaks, cur = [], [], []
    for r in (c for c in el if c.tag == f"{W_NS}r"):
        if r.find(f"{W_NS}br") is not None and len(
                [c for c in r if c.tag != f"{W_NS}rPr"]) == 1:
            segments.append(cur)
            breaks.append(r)
            cur = []
        else:
            cur.append(r)
    segments.append(cur)
    return segments, breaks


def _seg_text(seg: list[Any]) -> str:
    return "".join(t.text or "" for r in seg for t in r.findall(f"{W_NS}t"))


def _split_at_list_items(el: Any, ppr: Any, segments: list[list[Any]],
                         breaks: list[Any]) -> list[Any]:
    """Move every segment that starts a list item into a new paragraph after
    ``el`` (same paragraph properties, minus any section break). Returns
    the paragraphs, ``el`` first."""
    created = [el]
    current_target = el
    dropped = []
    for seg, brk in zip(segments[1:], breaks, strict=True):
        if seg and _LIST_START.match(_seg_text(seg)):
            dropped.append(brk)
            np = OxmlElement("w:p")
            if ppr is not None:
                nppr = copy.deepcopy(ppr)
                s = nppr.find(f"{W_NS}sectPr")
                if s is not None:
                    nppr.remove(s)
                np.append(nppr)
            created[-1].addnext(np)
            created.append(np)
            current_target = np
            for r in seg:
                np.append(r)
        else:
            # a non-list line keeps its own line break (dropping it would
            # glue its first word onto the previous line)
            for r in [brk, *seg]:
                current_target.append(r)
    # remove the now-orphaned breaks that preceded the split-off items
    for r in dropped:
        r.getparent().remove(r)
    return created


def split_list_breaks(doc: Any) -> None:
    """Split paragraphs at hard line breaks that start a list-like item."""
    for el in list(doc.element.body):
        if el.tag != f"{W_NS}p" or "\n" not in Paragraph(el, doc).text:
            continue
        _isolate_breaks(el)
        segments, breaks = _segments(el)
        if len(segments) < 2:
            continue
        if not any(_LIST_START.match(_seg_text(s)) for s in segments[1:]):
            continue

        ppr = el.find(f"{W_NS}pPr")
        created = _split_at_list_items(el, ppr, segments, breaks)
        # a section break must stay on the LAST paragraph of the page
        sect = ppr.find(f"{W_NS}sectPr") if ppr is not None else None
        if sect is not None and created[-1] is not el:
            last = created[-1]
            last_ppr = last.find(f"{W_NS}pPr")
            if last_ppr is None:
                last_ppr = OxmlElement("w:pPr")
                last.insert(0, last_ppr)
            ppr.remove(sect)
            last_ppr.append(sect)
        # every split line fits one source line: left-align at full width
        for pel in created:
            p = Paragraph(pel, doc)
            p.paragraph_format.right_indent = Pt(0)
            if p.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
                p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT


# --- Alignment and banner fills ---------------------------------------------

def fix_justified_ragged(doc: Any, layout: Layout) -> None:
    """pdf2docx infers JUSTIFY for merged blocks; when the source block's
    right edge was ragged, restore LEFT alignment and full usable width."""
    for pno, elements in enumerate(body_pages(doc)):
        if pno >= len(layout.blocks):
            break
        blocks = layout.blocks[pno]
        for el in elements:
            if el.tag != f"{W_NS}p":
                continue
            para = Paragraph(el, doc)
            pf = para.paragraph_format
            if pf.alignment != WD_ALIGN_PARAGRAPH.JUSTIFY:
                continue
            probe = squash(para.text)[:32]
            if not probe:
                continue
            blk = next((b for b in blocks if b.squash.startswith(probe)
                        or probe.startswith(b.squash[:32])), None)
            if blk is None or len(blk.line_x1) < 3:
                continue
            interior = blk.line_x1[:-1]
            ragged = (max(interior) - min(interior)) > max(
                6.0, 0.03 * blk.width)
            if ragged:
                pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
                pf.right_indent = Pt(0)


def _is_light(color_val: int) -> bool:
    r, g, b = (color_val >> 16) & 0xFF, (color_val >> 8) & 0xFF, color_val & 0xFF
    return (r * 299 + g * 587 + b * 114) / 1000 > 200


def _banner_fill(para: Paragraph, rows: list[Row],
                 fills: list[tuple[fitz.Rect, Any]]) -> str | None:
    """Hex fill of the PDF rectangle behind ``para`` when all of its text is
    light-coloured, else None."""
    probe = squash(para.text)
    if not probe:
        return None
    colors = [r.font.color.rgb for r in para.runs
              if r.font.color and r.font.color.rgb is not None]
    if not colors or not all(_is_light(int(str(c), 16)) for c in colors):
        return None
    row = next((r for r in rows if r.squash == probe
                or r.squash.startswith(probe)), None)
    if row is None:
        return None
    cx = (row.spans[0].x0 + row.spans[-1].x1) / 2
    cy = (row.spans[0].y0 + row.spans[0].y1) / 2
    rect_fill = next((fill for rect, fill in fills
                      if rect.contains(fitz.Point(cx, cy))), None)
    if rect_fill is None:
        return None
    r, g, b = (round(v * 255) for v in rect_fill[:3])
    return f"{r:02X}{g:02X}{b:02X}"


def restore_banner_shading(doc: Any, layout: Layout) -> None:
    """Light text drawn over a filled rectangle (e.g. white-on-red banner
    titles in official letters) loses the rectangle in pdf2docx and becomes
    invisible. Re-apply the fill as paragraph shading."""
    for pno, elements in enumerate(body_pages(doc)):
        fills = layout.fillrects[pno] if pno < len(layout.fillrects) else []
        if not fills:
            continue
        rows = layout.rows[pno] if pno < len(layout.rows) else []
        for para in iter_elements_paragraphs(elements, doc):
            rgb = _banner_fill(para, rows, fills)
            if rgb is None:
                continue
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), rgb)
            para._p.get_or_add_pPr().append(shd)
