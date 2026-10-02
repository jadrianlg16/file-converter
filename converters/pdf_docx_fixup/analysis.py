"""PDF-side analysis: read the source layout with PyMuPDF."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import fitz  # PyMuPDF

from .bands import detect_bands
from .model import BlockInfo, Layout, Row, Span, norm_repeat, squash

log = logging.getLogger(__name__)

# Drawings are only needed for rules, borders and banners, which live on the
# first pages of the documents this repairs; reading them is slow.
_DRAWING_PAGES = 30


def _mk_span(sp: dict[str, Any]) -> Span | None:
    """A Span from one PyMuPDF ``dict`` span, or None for blank text."""
    text = sp.get("text", "")
    if not text.strip():
        return None
    flags = sp.get("flags", 0)
    fname = sp.get("font", "") or ""
    x0, y0, x1, y1 = sp["bbox"]
    return Span(
        text=text.strip(), x0=x0, x1=x1, y0=y0, y1=y1,
        size=float(sp.get("size", 11.0)), font=fname,
        bold=bool(flags & 16) or "bold" in fname.lower(),
        italic=bool(flags & 2) or "italic" in fname.lower()
              or "oblique" in fname.lower(),
        color=int(sp.get("color", 0)),
        lead_space=text[:1].isspace(), trail_space=text[-1:].isspace(),
    )


def _coalesce_row(spans: list[Span]) -> list[Span]:
    """Merge spans on one baseline whose gap is just word spacing. The merged
    span keeps each piece in ``parts`` so runs retain their own format."""
    spans = sorted(spans, key=lambda s: s.x0)
    out: list[Span] = []
    for sp in spans:
        if out and sp.x0 - out[-1].x1 < max(6.0, out[-1].size * 0.6):
            prev = out[-1]
            gap = sp.x0 - prev.x1
            # PyMuPDF often keeps the word space inside a span's text (which
            # _mk_span strips), so the bbox gap alone can read as zero
            sep = (" " if gap > prev.size * 0.12 or prev.trail_space
                   or sp.lead_space else "")
            out[-1] = Span(
                text=prev.text + sep + sp.text, x0=prev.x0, x1=sp.x1,
                y0=min(prev.y0, sp.y0), y1=max(prev.y1, sp.y1),
                size=prev.size, font=prev.font, bold=prev.bold,
                italic=prev.italic, color=prev.color,
                trail_space=sp.trail_space,
                parts=[*(prev.parts or [prev]), replace(sp, text=sep + sp.text)],
            )
        else:
            out.append(sp)
    return out


def _page_rows(page: fitz.Page) -> list[Row]:
    """Group every span on the page into visual rows (baseline clusters)."""
    spans = []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                s = _mk_span(sp)
                if s:
                    spans.append(s)
    spans.sort(key=lambda s: (s.y0, s.x0))
    rows, current, cur_y = [], [], None
    for sp in spans:
        if cur_y is None or abs(sp.y0 - cur_y) <= 2.0:
            current.append(sp)
            cur_y = sp.y0 if cur_y is None else cur_y
        else:
            rows.append(current)
            current, cur_y = [sp], sp.y0
    if current:
        rows.append(current)

    out = []
    for group in rows:
        merged = _coalesce_row(group)
        out.append(Row(spans=merged,
                       squash=squash("".join(s.text for s in merged))))
    return out


def _page_blocks(page: fitz.Page) -> tuple[list[BlockInfo], list[dict[str, Any]]]:
    """(BlockInfo list for raggedness, raw block records for band detection)."""
    infos, records = [], []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        lines, x1s, texts = [], [], []
        for ln in blk.get("lines", []):
            row = [s for s in (_mk_span(sp) for sp in ln.get("spans", []))
                   if s]
            if not row:
                continue
            row = _coalesce_row(row)
            lines.append(row)
            x1s.append(max(s.x1 for s in row))
            texts.append(" ".join(s.text for s in row))
        if not lines:
            continue
        full = "\n".join(texts)
        x0, _, x1, _ = blk["bbox"]
        infos.append(BlockInfo(squash=squash(full), line_x1=x1s,
                               width=x1 - x0))
        records.append({
            "norm": norm_repeat(full), "texts": texts, "lines": lines,
            "rect": fitz.Rect(blk["bbox"]),
        })
    return infos, records


def _page_drawings(page: fitz.Page, pno: int) -> tuple[list, list, list]:
    """(horizontal rules, vertical lines, filled bars) drawn on ``page``."""
    hlines, vlines, fills = [], [], []
    try:
        drawings = page.get_drawings()
    except Exception:  # MuPDF raises several types on bad content streams
        log.warning("Could not read the drawings on page %d; rules, borders "
                    "and banner fills there are ignored", pno + 1, exc_info=True)
        return hlines, vlines, fills
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        if r.height <= 3 and r.width >= 20:
            hlines.append(r)
        elif r.width <= 3 and r.height >= 16:
            vlines.append((r.x0, r.y0, r.y1))
        elif (d.get("fill") is not None and 6 <= r.height <= 80
              and r.width >= 30):
            fills.append((r, d["fill"]))
    return hlines, vlines, fills


def analyze_pdf(path: str) -> Layout:
    """Geometry snapshot of ``path``: visual rows, text blocks, ruled lines,
    filled bars and (for uniform page sizes) the repeated header/footer."""
    with fitz.open(path) as doc:
        first = doc[0].rect
        layout = Layout(page_w=first.width, page_h=first.height,
                        page_count=doc.page_count)
        uniform = all(abs(doc[p].rect.width - first.width) < 2 and
                      abs(doc[p].rect.height - first.height) < 2
                      for p in range(doc.page_count))
        page_records = []
        hlines_per_page = []
        for pno in range(doc.page_count):
            page = doc[pno]
            layout.rows.append(_page_rows(page))
            infos, records = _page_blocks(page)
            layout.blocks.append(infos)
            page_records.append(records)
            hlines, vlines, fills = (_page_drawings(page, pno)
                                     if pno < _DRAWING_PAGES else ([], [], []))
            hlines_per_page.append(hlines)
            layout.vlines.append(vlines)
            layout.fillrects.append(fills)
        if uniform:
            layout.header, layout.footer = detect_bands(
                doc, page_records, hlines_per_page, first.width, first.height)
        return layout
