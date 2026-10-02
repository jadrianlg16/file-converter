"""Geometry snapshot of the source PDF, plus the text keys used to match it.

PDF text and DOCX text are compared through :func:`squash` (all whitespace
removed), because pdf2docx re-wraps lines and drops or adds spaces freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import fitz

DIGITS = re.compile(r"\d+")


def norm_repeat(text: str) -> str:
    """Whitespace-collapsed text with digit runs wildcarded (page numbers,
    dates and folios compare equal across pages)."""
    return DIGITS.sub("#", " ".join(text.split())).strip()


def squash(text: str) -> str:
    """``text`` with every whitespace character removed."""
    return "".join(text.split())


@dataclass
class Span:
    """One run of identically formatted text on a single baseline."""

    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    size: float
    font: str
    bold: bool
    italic: bool
    color: int = 0  # sRGB int as reported by PyMuPDF (0 = black)
    lead_space: bool = False  # raw text had leading/trailing whitespace
    trail_space: bool = False  # (stripped, but still a word boundary)
    parts: list[Span] = field(default_factory=list)  # coalesced pieces, own format


@dataclass
class Row:
    """Spans sharing one visual baseline, wide gaps between them."""

    spans: list[Span]
    squash: str


@dataclass
class BlockInfo:
    """One fitz text block: used for raggedness lookup."""

    squash: str
    line_x1: list[float]
    width: float


@dataclass
class RepeatedBlock:
    """A text block that recurs at the same place on most pages."""

    lines: list[list[Span]]  # from the first occurrence
    occurrences: dict[int, fitz.Rect] = field(default_factory=dict)  # page -> rect
    # (line index, span index) -> [(start, end, "PAGE" | "NUMPAGES")]
    span_markers: dict[tuple[int, int], list[tuple[int, int, str]]] = field(default_factory=dict)
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    exact: bool = True  # repeats with identical text (digits too)
    has_page_token: bool = False  # contains a page-number-tracking digit


@dataclass
class Band:
    """Everything that repeats in the header or footer zone."""

    blocks: list[RepeatedBlock] = field(default_factory=list)
    rule: dict[str, Any] | None = None  # {"occurrences": {page: Rect}, "y": float}
    images: list[dict[str, Any]] = field(default_factory=list)  # {occurrences, data, ext, bbox}
    on_first_page: bool = True


@dataclass
class Layout:
    """Per-page geometry of the source PDF that the DOCX repairs work from."""

    page_w: float
    page_h: float
    page_count: int
    rows: list[list[Row]] = field(default_factory=list)
    blocks: list[list[BlockInfo]] = field(default_factory=list)
    vlines: list[list[tuple[float, float, float]]] = field(default_factory=list)  # (x, y0, y1)
    fillrects: list[list[tuple[fitz.Rect, Any]]] = field(default_factory=list)  # (rect, rgb)
    header: Band | None = None
    footer: Band | None = None
