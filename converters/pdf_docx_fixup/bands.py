"""Repeated header/footer bands: detection on the PDF and redaction from it."""

from __future__ import annotations

import logging
from typing import Any

import fitz  # PyMuPDF

from .model import DIGITS, Band, Layout, RepeatedBlock, Span

log = logging.getLogger(__name__)

# Bands: header candidates live entirely above 18% of the page height,
# footer candidates entirely below 82% (a detected horizontal rule overrides
# these, see detect_bands). A band must also start close to the page edge
# and be contiguous (no gap larger than _BAND_GAP_PT between its blocks).
_HEADER_LIMIT = 0.18
_FOOTER_LIMIT = 0.82
_EDGE_START = 0.12
_BAND_GAP_PT = 24.0
_ZONES = ("header", "footer")

Marker = tuple[int, int, str]  # (start, end, "PAGE" | "NUMPAGES")


def _line_markers(
    first: str, others: list[str], page_nums: list[int], page_count: int
) -> list[Marker]:
    """Positions in ``first`` whose digit runs should become PAGE/NUMPAGES
    fields. ``others``/``page_nums`` are the same line on other pages (raw
    text, 0-based page index; first entry corresponds to ``first``)."""
    tokens = list(DIGITS.finditer(first))
    if not tokens:
        return []
    all_tokens = [list(DIGITS.finditer(t)) for t in others]
    if any(len(t) != len(tokens) for t in all_tokens):
        return []
    markers, has_page = [], False
    for k, tok in enumerate(tokens):
        values = [int(t[k].group()) for t in all_tokens]
        tracks_page = all(v == p + 1 for v, p in zip(values, page_nums, strict=True))
        if tracks_page and len(set(values)) > 1:  # genuinely varying page number
            markers.append((tok.start(), tok.end(), "PAGE"))
            has_page = True
    if has_page:
        for k, tok in enumerate(tokens):
            values = [int(t[k].group()) for t in all_tokens]
            if len(set(values)) == 1 and values[0] == page_count:
                markers.append((tok.start(), tok.end(), "NUMPAGES"))
    return sorted(markers)


def _span_markers(
    lines: list[list[Span]], line_markers: list[list[Marker]]
) -> dict[tuple[int, int], list[Marker]]:
    """Convert line-level PAGE/NUMPAGES markers into span-local offsets."""
    out: dict[tuple[int, int], list[Marker]] = {}
    for li, spans in enumerate(lines):
        markers = line_markers[li] if li < len(line_markers) else []
        if not markers:
            continue
        offset = 0
        for si, sp in enumerate(spans):
            if si:
                offset += 1  # single joining space, matches _page_blocks texts
            start, end = offset, offset + len(sp.text)
            local = [
                (ms - start, me - start, instr)
                for ms, me, instr in markers
                if start <= ms and me <= end
            ]
            if local:
                out[(li, si)] = local
            offset = end
    return out


def _find_rules(
    hlines_per_page: list[list[fitz.Rect]], W: float, H: float, threshold: int
) -> dict[str, dict[str, Any] | None]:
    """Thin horizontal lines repeated near the top/bottom of most pages —
    the natural separator between a letterhead and the body."""
    rules: dict[str, dict[str, Any] | None] = {"header": None, "footer": None}
    occ: dict[str, dict[int, fitz.Rect]] = {"header": {}, "footer": {}}
    for pno, hlines in enumerate(hlines_per_page):
        for r in hlines:
            if r.width < 0.4 * W:
                continue
            zone = "header" if r.y1 <= 0.25 * H else "footer" if r.y0 >= 0.78 * H else None
            if zone and pno not in occ[zone]:
                occ[zone][pno] = fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)
    for zone in _ZONES:
        if len(occ[zone]) >= threshold:
            ys = sorted(r.y0 for r in occ[zone].values())
            median = ys[len(ys) // 2]
            if all(abs(r.y0 - median) <= 5 for r in occ[zone].values()):
                rules[zone] = {"occurrences": occ[zone], "y": median}
    return rules


def _group_edge_blocks(
    page_records: list[list[dict[str, Any]]], zone_limit: dict[str, float]
) -> dict[tuple[str, str], list[dict]]:
    """Blocks inside the header/footer zones, grouped by normalised text.

    A group holds at most one block per page, and only blocks at the same
    height with the same left or right edge as those already in it."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for pno, records in enumerate(page_records):
        for rec in records:
            r = rec["rect"]
            if r.y1 <= zone_limit["header"]:
                zone = "header"
            elif r.y0 >= zone_limit["footer"]:
                zone = "footer"
            else:
                continue
            grp = groups.setdefault((zone, rec["norm"]), [])
            # same block repeats: similar y, similar left or right edge
            if grp and not any(
                abs(g["rect"].y0 - r.y0) <= 5
                and (abs(g["rect"].x0 - r.x0) <= 8 or abs(g["rect"].x1 - r.x1) <= 8)
                for g in grp
            ):
                continue
            if any(g["page"] == pno for g in grp):
                continue
            grp.append({"page": pno, "rect": r, "rec": rec})
    return groups


def _repeated_block(grp: list[dict], page_count: int) -> RepeatedBlock:
    """A RepeatedBlock from one group, with PAGE/NUMPAGES markers for digit
    runs that track the page number."""
    first = grp[0]["rec"]
    rb = RepeatedBlock(lines=first["lines"], bbox=tuple(grp[0]["rect"]))
    for g in grp:
        rb.occurrences[g["page"]] = g["rect"]
    rb.exact = len({" ".join("\n".join(g["rec"]["texts"]).split()) for g in grp}) == 1
    page_nums = [g["page"] for g in grp]
    line_markers = []
    for li, line_text in enumerate(first["texts"]):
        if all(li < len(g["rec"]["texts"]) for g in grp):
            texts = [g["rec"]["texts"][li] for g in grp]
            line_markers.append(_line_markers(line_text, texts, page_nums, page_count))
        else:
            line_markers.append([])
    rb.span_markers = _span_markers(rb.lines, line_markers)
    rb.has_page_token = any(
        instr == "PAGE" for ms in rb.span_markers.values() for _, _, instr in ms
    )
    return rb


def _contiguous_from_edge(blocks: list[RepeatedBlock], zone: str, H: float) -> list[RepeatedBlock]:
    """Without a rule to delimit the band, keep only the blocks that start
    near the page edge and follow each other without a wide gap."""
    if zone == "header":
        kept, reach = [], H * _EDGE_START
        for b in sorted(blocks, key=lambda b: b.bbox[1]):
            if b.bbox[1] <= reach + _BAND_GAP_PT:
                kept.append(b)
                reach = max(reach, b.bbox[3])
        return kept
    kept, reach = [], H * (1 - _EDGE_START)
    for b in sorted(blocks, key=lambda b: -b.bbox[3]):
        if b.bbox[3] >= reach - _BAND_GAP_PT:
            kept.append(b)
            reach = min(reach, b.bbox[1])
    return kept


def detect_bands(
    doc: fitz.Document,
    page_records: list[list[dict[str, Any]]],
    hlines_per_page: list[list[fitz.Rect]],
    W: float,
    H: float,
) -> tuple[Band | None, Band | None]:
    """Find blocks repeated across pages that form the header/footer bands.

    A horizontal rule (when present on most pages) delimits the band exactly.
    Without one, candidates must sit inside a narrow edge band, repeat with
    *identical* text or carry a page-number-tracking digit, start close to
    the page edge, and be contiguous — this keeps repeated body content
    (templated forms, numbered headings) out of the header.
    """
    n = len(page_records)
    if n < 2:
        return None, None
    threshold = max(2, -(-n * 3 // 5))  # ceil(0.6 * n)
    rules = _find_rules(hlines_per_page, W, H, threshold)
    zone_limit = {
        "header": (rules["header"]["y"] + 2 if rules["header"] else H * _HEADER_LIMIT),
        "footer": (rules["footer"]["y"] - 2 if rules["footer"] else H * _FOOTER_LIMIT),
    }

    bands = {zone: Band() for zone in _ZONES}
    for (zone, norm), grp in _group_edge_blocks(page_records, zone_limit).items():
        if len(grp) >= threshold and norm:
            bands[zone].blocks.append(_repeated_block(grp, n))

    found: dict[str, Band | None] = {}
    for zone, band in bands.items():
        # only trust exact repeats or page-number blocks, rule or not: any
        # other varying digits (folios, dates) would be frozen to the first
        # occurrence while the real per-page values get redacted
        band.blocks = [b for b in band.blocks if b.exact or b.has_page_token]
        if band.blocks and not rules[zone]:
            band.blocks = _contiguous_from_edge(band.blocks, zone, H)
        if not band.blocks:
            found[zone] = None
            continue
        band.on_first_page = all(0 in b.occurrences for b in band.blocks)
        band.rule = rules[zone]
        band.images = _find_band_images(doc, H, threshold, zone, zone_limit[zone])
        found[zone] = band
    return found["header"], found["footer"]


def _find_band_images(
    doc: fitz.Document, H: float, threshold: int, zone: str, limit_y: float | None = None
) -> list[dict[str, Any]]:
    """Images (logos) with the same xref+position in the band on most pages."""
    if limit_y is None:
        limit_y = H * (_HEADER_LIMIT if zone == "header" else _FOOTER_LIMIT)
    out = []
    try:
        seen: dict[int, dict[str, Any]] = {}
        for pno in range(doc.page_count):
            for img in doc[pno].get_images(full=True):
                xref = img[0]
                for r in doc[pno].get_image_rects(xref):
                    in_band = r.y1 <= limit_y if zone == "header" else r.y0 >= limit_y
                    if not in_band:
                        continue
                    entry = seen.setdefault(xref, {"occurrences": {}, "rect": r})
                    if abs(entry["rect"].y0 - r.y0) <= 5:
                        entry["occurrences"][pno] = r
        for xref, entry in seen.items():
            if len(entry["occurrences"]) < threshold:
                continue
            pix = doc.extract_image(xref)
            if pix and pix.get("ext") in ("png", "jpeg", "jpg", "bmp", "gif"):
                out.append(
                    {
                        "occurrences": entry["occurrences"],
                        "data": pix["image"],
                        "ext": pix["ext"],
                        "bbox": tuple(entry["rect"]),
                    }
                )
    except Exception:  # MuPDF raises several types on broken images
        log.warning("Could not read the %s images; logos stay in the body", zone, exc_info=True)
        return []
    return out


def _padded(r: fitz.Rect) -> fitz.Rect:
    return fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)


def redact_bands(src: str, dst: str, layout: Layout) -> None:
    """Remove the detected header/footer content from a copy of the PDF."""
    doc = fitz.open(src)
    try:
        graphics_flag = getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", None)
        image_flag = getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 2)
        image_none = getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0)
        for pno in range(doc.page_count):
            page = doc[pno]
            rects, img_rects = [], []
            for band in (layout.header, layout.footer):
                if not band:
                    continue
                for blk in band.blocks:
                    r = blk.occurrences.get(pno)
                    if r:
                        rects.append(_padded(r))
                if band.rule:
                    r = band.rule["occurrences"].get(pno)
                    if r:
                        rects.append(r)
                for img in band.images:
                    r = img["occurrences"].get(pno)
                    if r:
                        img_rects.append(_padded(r))
            # two passes: text/rule rects must leave every image alone (a
            # background or body image merely touching a band block would
            # otherwise be removed whole); only band logos remove images
            for batch, images in ((rects, image_none), (img_rects, image_flag)):
                if not batch:
                    continue
                for r in batch:
                    page.add_redact_annot(r)
                if graphics_flag is not None:
                    page.apply_redactions(images=images, graphics=graphics_flag)
                else:  # older PyMuPDF without the graphics parameter
                    page.apply_redactions(images=images)
        doc.save(dst, garbage=3, deflate=True)
    finally:
        doc.close()
