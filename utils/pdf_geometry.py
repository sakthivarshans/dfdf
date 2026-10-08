"""
PDF Geometry
============

Rebuilds a product table from **word positions** instead of trusting a
vision-language model's reading order.  This is the module that closes
errors #4, #5, #6, #7 (and the structural half of #12).

Why geometry?
-------------
PaddleOCR-VL turns a page into HTML.  Everything that is *structural* in
that HTML - which cell a ``rowspan`` is attached to, how many columns a row
has, whether a row exists at all - is a guess made by a language model.  The
PDF itself already knows the answer: every word sits at an exact (x, y), and
the table borders are drawn as lines.

The word list can come from either of two sources, and this module does not
care which (it only sees ``Word`` objects):

* the PDF **text layer** (exact characters - used whenever it exists), or
* a second, independent OCR engine run on the page (for scanned PDFs).

Everything here is a pure function of (words, rules, bounding box): no PDF
library, no OCR engine, no file I/O.  That is deliberate - it makes the
logic unit-testable with hand-written coordinates.

Coordinate system
-----------------
Everything is in **page-image pixels at the pipeline DPI** (the same space
as PaddleOCR's ``block_bbox``), origin top-left, y grows downward.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from statistics import median

from config.document_profile import ACTIVE_PROFILE, DocumentProfile
from utils.field_validators import clean_text, validate_deal
from utils.headers import lookup_header, normalize_col

_log = logging.getLogger(__name__)

# ==========================================================================
# Primitive types
# ==========================================================================


@dataclass(frozen=True)
class Word:
    """One word (or one OCR text box) with its bounding box."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class HRule:
    """A horizontal line (table border) at height ``y`` spanning x0..x1."""

    y: float
    x0: float
    x1: float


@dataclass(frozen=True)
class VRule:
    """A vertical line (table border) at ``x`` spanning y0..y1."""

    x: float
    y0: float
    y1: float


@dataclass
class ColumnLayout:
    """
    Column structure of a table: names plus the x-position of every column
    edge (``len(edges) == len(names) + 1``).

    A layout can be **carried** from one table to the next.  That is how a
    continuation fragment - the tail of a table that flowed onto the next
    page or got detected as a separate block, with no header of its own
    (error #5) - is read with the right columns.
    """

    names: list[str]
    edges: list[float]
    source: str = "header"           # "header" | "carried" | "vlm-header"

    def index_at(self, x: float) -> int | None:
        """Index of the column containing x, or None if outside the table."""
        if x < self.edges[0] or x >= self.edges[-1]:
            return None
        for i in range(len(self.names)):
            if self.edges[i] <= x < self.edges[i + 1]:
                return i
        return None

    def span(self, name: str) -> tuple[float, float] | None:
        """(x_left, x_right) of a named column, or None if it does not exist."""
        if name not in self.names:
            return None
        i = self.names.index(name)
        return self.edges[i], self.edges[i + 1]

    def to_dict(self) -> dict:
        return {"names": self.names, "edges": self.edges, "source": self.source}

    @classmethod
    def from_dict(cls, data: dict) -> "ColumnLayout":
        return cls(list(data["names"]), [float(e) for e in data["edges"]],
                   data.get("source", "carried"))


@dataclass
class GeomRow:
    """One rebuilt row."""

    kind: str                              # product | banner | blank_midas | orphan
    y0: float
    y1: float
    cells: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    deal_method: str = "own-cell"          # own-cell | rules-group | rules-single
    # Independent second-pass reads of risky cells (scans only): column ->
    # text.  The CSV stage compares them with ``cells`` (consensus).
    alt: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "y0": self.y0, "y1": self.y1,
                "cells": self.cells, "flags": self.flags,
                "deal_method": self.deal_method, "alt": self.alt}


@dataclass
class GeometryTable:
    """The geometry-rebuilt version of one layout-detected table block."""

    bbox: tuple[float, float, float, float]
    layout: ColumnLayout | None
    rows: list[GeomRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    header_found: bool = False
    band_method: str = ""                  # rules | anchors | none

    def to_dict(self) -> dict:
        return {
            "bbox": list(self.bbox),
            "layout": self.layout.to_dict() if self.layout else None,
            "rows": [r.to_dict() for r in self.rows],
            "warnings": self.warnings,
            "header_found": self.header_found,
            "band_method": self.band_method,
        }


# ==========================================================================
# Small helpers
# ==========================================================================


def median_height(words: list[Word]) -> float:
    """Typical text height - the unit all tolerances are expressed in."""
    heights = [w.h for w in words if w.h > 0]
    return median(heights) if heights else 10.0


def group_lines(words: list[Word], tol: float) -> list[list[Word]]:
    """
    Group words into text lines (top to bottom), each sorted left to right.
    Two words share a line when their vertical centres differ by <= tol.
    """

    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.cy, w.x0)):
        if lines and abs(word.cy - _mean_cy(lines[-1])) <= tol:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _mean_cy(line: list[Word]) -> float:
    return sum(w.cy for w in line) / len(line)


def join_words(words: list[Word], tol: float) -> str:
    """Reading-order text of a set of words: line by line, left to right."""
    return clean_text(" ".join(w.text for line in group_lines(words, tol) for w in line))


def _merge_close(values: list[float], gap: float) -> list[float]:
    """Collapse values closer than ``gap`` (double-drawn rules) to their mean."""
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


# ==========================================================================
# 1. Header detection  ->  column layout
# ==========================================================================

# Tokens that occur in this document's column headers.  A line is a header
# line when several of its words are in this set.
_HEADER_TOKENS = {
    "midas", "code", "consumer", "deal", "product", "description", "case",
    "size", "pack", "ean", "barcode", "prom", "promo", "wsp", "std", "rsp",
    "por", "por%", "leaflet", "feature", "space", "image", "shelf",
}


def _token(text: str) -> str:
    return re.sub(r"[^a-z%]", "", text.lower())


@dataclass
class _HeaderItem:
    name: str      # canonical column name
    x0: float
    x1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def detect_header(
    words: list[Word], first_anchor_y: float | None, mh: float
) -> tuple[list[_HeaderItem], float] | None:
    """
    Find the table header in a word list.

    Returns ``(items, header_bottom_y)`` or None when the region has no
    header (a continuation fragment).

    A header is the 1-3 adjacent text lines, above the first product row,
    with the most header-vocabulary words.  Headers are often *stacked*
    (``Midas`` over ``Code``), so words that overlap in x across lines are
    merged into one cell before names are looked up.
    """

    candidates = [w for w in words if first_anchor_y is None or w.cy < first_anchor_y - 0.2 * mh]
    lines = group_lines(candidates, tol=0.6 * mh)
    if not lines:
        return None

    scores = [sum(1 for w in line if _token(w.text) in _HEADER_TOKENS) for line in lines]
    best = max(range(len(lines)), key=lambda i: scores[i])
    if scores[best] < 2:
        return None

    # grow the band to adjacent lines that also carry header vocabulary
    lo = hi = best
    while lo > 0 and scores[lo - 1] >= 1 and _line_gap(lines[lo - 1], lines[lo]) < 2.5 * mh and hi - lo < 2:
        lo -= 1
    while hi < len(lines) - 1 and scores[hi + 1] >= 1 and _line_gap(lines[hi], lines[hi + 1]) < 2.5 * mh and hi - lo < 2:
        hi += 1

    band_words = [w for line in lines[lo:hi + 1] for w in line]
    tokens = {_token(w.text) for w in band_words} & _HEADER_TOKENS
    if len(tokens) < 3 or not ({"midas", "consumer"} & tokens):
        return None

    header_bottom = max(w.y1 for w in band_words)

    # --- merge words that overlap in x (stacked header cells) -------------
    clusters: list[list[Word]] = []
    for w in sorted(band_words, key=lambda w: w.x0):
        placed = False
        for cluster in clusters:
            cx0 = min(c.x0 for c in cluster)
            cx1 = max(c.x1 for c in cluster)
            overlap = min(cx1, w.x1) - max(cx0, w.x0)
            # overlap must be real, and the word must be on a DIFFERENT line
            # (two words on one line never stack)
            if overlap > 0.25 * min(w.w, cx1 - cx0) and all(abs(c.cy - w.cy) > 0.6 * mh for c in cluster):
                cluster.append(w)
                placed = True
                break
        if not placed:
            clusters.append([w])

    units = sorted(
        (
            (" ".join(w.text for w in sorted(c, key=lambda w: (w.cy, w.x0))),
             min(w.x0 for w in c), max(w.x1 for w in c))
            for c in clusters
        ),
        key=lambda u: u[1],
    )

    # --- greedy phrase matching along x ("Midas" "Code" -> "Midas Code") ---
    items: list[_HeaderItem] = []
    i = 0
    while i < len(units):
        matched = False
        for k in (3, 2, 1):
            chunk = units[i:i + k]
            if len(chunk) < k:
                continue
            name = lookup_header(" ".join(u[0] for u in chunk))
            if name:
                items.append(_HeaderItem(name, chunk[0][1], chunk[-1][2]))
                i += k
                matched = True
                break
        if not matched:
            text, x0, x1 = units[i]
            items.append(_HeaderItem(normalize_col(text), x0, x1))
            i += 1

    # a canonical name may appear once only; keep the first occurrence
    seen, unique = set(), []
    for item in items:
        if item.name not in seen:
            seen.add(item.name)
            unique.append(item)
    return unique, header_bottom


def _line_gap(upper: list[Word], lower: list[Word]) -> float:
    return min(w.y0 for w in lower) - max(w.y1 for w in upper)


def _gutter_between(a: float, b: float, data_words: list[Word], min_width: float) -> float | None:
    """
    Centre of the widest vertical white corridor between x=a and x=b, i.e.
    the gutter between two columns of text.  None when the words leave no
    gap of at least ``min_width``.
    """

    spans = sorted(
        (max(a, w.x0), min(b, w.x1)) for w in data_words if w.x1 > a and w.x0 < b
    )
    best = None
    cursor = a
    for s0, s1 in spans:
        if s0 - cursor >= min_width and (best is None or s0 - cursor > best[1] - best[0]):
            best = (cursor, s0)
        cursor = max(cursor, s1)
    if b - cursor >= min_width and (best is None or b - cursor > best[1] - best[0]):
        best = (cursor, b)
    return (best[0] + best[1]) / 2 if best else None


def layout_from_header(
    items: list[_HeaderItem],
    data_words: list[Word],
    bbox: tuple[float, float, float, float],
    v_rules: list[VRule],
    mh: float,
) -> ColumnLayout:
    """
    Turn header items into column edges.

    Between two neighbouring headers the boundary is chosen, in order of
    preference, from: (1) a drawn vertical rule, (2) the widest white
    gutter in the data text, (3) the midpoint between the header texts.
    """

    edges = [bbox[0]]
    for left, right in zip(items, items[1:]):
        lo, hi = left.cx, right.cx
        edge = None

        rules = [r.x for r in v_rules if lo < r.x < hi]
        if rules:
            mid = (left.x1 + right.x0) / 2
            edge = min(rules, key=lambda x: abs(x - mid))
        if edge is None:
            edge = _gutter_between(lo, hi, data_words, min_width=0.5 * mh)
        if edge is None:
            edge = (left.x1 + right.x0) / 2
        edges.append(edge)
    edges.append(bbox[2])
    return ColumnLayout([i.name for i in items], edges, "header")


# ==========================================================================
# 2. Row bands
# ==========================================================================

_PRICE = re.compile(r"£\s?\d")
_PERCENT = re.compile(r"\d\s?%")
_LONG_DIGITS = re.compile(r"\d{8,}")


def _has_product_evidence(words: list[Word], profile: DocumentProfile) -> bool:
    """Does a band contain anything only a product row would contain?"""

    for w in words:
        t = w.text
        if (profile.midas_like_pattern.match(t) or _PRICE.search(t)
                or _PERCENT.search(t) or _LONG_DIGITS.search(t.replace(" ", ""))):
            return True
    return False


def find_anchors(words: list[Word], layout: ColumnLayout, profile: DocumentProfile) -> list[Word]:
    """Midas-looking words that sit inside the Midas column, top to bottom."""

    span = layout.span("Midas Code")
    if span is None:
        return []
    return sorted(
        (w for w in words if span[0] <= w.cx < span[1] and profile.midas_like_pattern.match(w.text)),
        key=lambda w: w.cy,
    )


def _separator_ys(
    h_rules: list[HRule], span: tuple[float, float], y_top: float, y_bottom: float, mh: float
) -> list[float]:
    """y of every horizontal rule covering >=70% of the column span."""

    width = span[1] - span[0]
    ys = []
    for r in h_rules:
        covered = min(r.x1, span[1]) - max(r.x0, span[0])
        if covered >= 0.7 * width and y_top - mh <= r.y <= y_bottom + mh:
            ys.append(r.y)
    return _merge_close(ys, 0.4 * mh)


def compute_bands(
    data_words: list[Word],
    layout: ColumnLayout,
    h_rules: list[HRule],
    y_top: float,
    y_bottom: float,
    mh: float,
    profile: DocumentProfile,
) -> tuple[list[tuple[float, float]], str, list[str]]:
    """
    Split the data region into one vertical band per table row.

    Strategy 1 - **drawn rules** (preferred): the horizontal borders that
    cross the Midas column are the row separators.  This is the only method
    that also sees a row whose Midas code is *missing* (error #4): such a
    row has a band but no anchor.

    Strategy 2 - **anchors**: boundaries halfway between consecutive Midas
    codes.  Used when the table has no drawn row borders.  A row without a
    Midas code is invisible to this method, so unusually large gaps between
    anchors are reported as warnings instead of being ignored.

    Returns (bands, method, warnings).
    """

    warnings: list[str] = []
    anchors = find_anchors(data_words, layout, profile)
    midas_span = layout.span("Midas Code")

    # ---- strategy 1 -----------------------------------------------------
    if midas_span is not None:
        seps = _separator_ys(h_rules, midas_span, y_top, y_bottom, mh)
        edges = sorted({y_top, y_bottom, *[s for s in seps if y_top < s < y_bottom]})
        bands = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 0.8 * mh]
        bands = [b for b in bands if any(b[0] <= w.cy < b[1] for w in data_words)]

        per_band = [sum(1 for a in anchors if b[0] <= a.cy < b[1]) for b in bands]
        rules_consistent = (
            len(bands) >= 2
            and max(per_band, default=0) <= 1
            and sum(1 for n in per_band if n == 1) == len(anchors)
        )
        if rules_consistent:
            return bands, "rules", warnings

    # ---- strategy 2 -----------------------------------------------------
    if not anchors:
        return [], "none", ["no Midas code found in the table region"]

    cys = [a.cy for a in anchors]
    cuts = [y_top] + [(a + b) / 2 for a, b in zip(cys, cys[1:])] + [y_bottom]
    bands = list(zip(cuts, cuts[1:]))

    if len(cys) >= 3:
        pitches = [b - a for a, b in zip(cys, cys[1:])]
        typical = median(pitches)
        for (a, b), pitch in zip(zip(cys, cys[1:]), pitches):
            if pitch > 1.7 * typical:
                warnings.append(
                    f"gap of {pitch / typical:.1f} rows between y={a:.0f} and y={b:.0f}: "
                    "a row without a Midas code may be missing"
                )
    return bands, "anchors", warnings


# ==========================================================================
# 3. Cells and merged Consumer Deal groups
# ==========================================================================


def assign_cells(band_words: list[Word], layout: ColumnLayout, mh: float) -> dict[str, str]:
    """Distribute a band's words into the layout's columns by word centre."""

    buckets: dict[str, list[Word]] = {name: [] for name in layout.names}
    for w in band_words:
        index = layout.index_at(w.cx)
        if index is not None:
            buckets[layout.names[index]].append(w)
    return {name: join_words(ws, 0.6 * mh) for name, ws in buckets.items()}


def resolve_deal_groups(
    rows: list[GeomRow],
    layout: ColumnLayout,
    data_words: list[Word],
    h_rules: list[HRule],
    y_top: float,
    y_bottom: float,
    mh: float,
    deal_column: str = "Consumer Deal",
) -> None:
    """
    Decide which rows share a vertically merged Consumer Deal - from the
    DRAWN GEOMETRY, never from the OCR's ``rowspan`` (errors #6 and #7).

    How it works
    ------------
    In the source document a merged cell has no horizontal border inside
    it.  So, in the Consumer Deal column only, the horizontal rules that
    ARE present are the boundaries between deal groups.  Every product row
    whose centre falls between the same two boundaries belongs to the same
    group, and the deal text printed in that region is its deal.

    Why this fixes #7: the group is defined by the rules and the words in
    the region, not by "the first row".  If the first row is damaged, blank
    or missing, the remaining rows still resolve.

    Safety net: the text of a multi-row group is accepted only if it forms
    one valid deal (``validate_deal``).  If it does not, but every row's own
    cell is a valid deal, the rows keep their own deals (the column was not
    actually merged).  The rows are modified in place.
    """

    span = layout.span(deal_column)
    if span is None:
        return

    seps = _separator_ys(h_rules, span, y_top, y_bottom, mh)
    if not seps:
        return          # no drawn structure in this column: rows keep their own cell

    bounds = [y_top] + [s for s in seps if y_top < s < y_bottom] + [y_bottom]
    product_kinds = ("product", "blank_midas")

    for lo, hi in zip(bounds, bounds[1:]):
        group = [r for r in rows if r.kind in product_kinds and lo <= (r.y0 + r.y1) / 2 < hi]
        if not group:
            continue
        if len(group) == 1:
            group[0].deal_method = "rules-single"
            continue

        region_words = [w for w in data_words if span[0] <= w.cx < span[1] and lo <= w.cy < hi]
        joined = join_words(region_words, 0.6 * mh)
        own = [r.cells.get(deal_column, "") for r in group]

        if joined and validate_deal(joined) is None:
            # one valid deal printed once against several rows: spread it
            for r in group:
                r.cells[deal_column] = joined
                r.deal_method = "rules-group"
        elif all(o and validate_deal(o) is None for o in own):
            for r in group:
                r.deal_method = "rules-single"       # each row stated its own deal
        elif joined:
            # not a deal we recognise: still hand every blank row the text, so
            # the REVIEW file shows what the document said; validation rejects it.
            for r in group:
                if not r.cells.get(deal_column):
                    r.cells[deal_column] = joined
                r.deal_method = "rules-group"
                r.flags.append("deal_text_not_in_grammar")


# ==========================================================================
# 4. The one entry point
# ==========================================================================


def rebuild_table(
    words: list[Word],
    h_rules: list[HRule],
    v_rules: list[VRule],
    bbox: tuple[float, float, float, float],
    *,
    carried_layout: ColumnLayout | None = None,
    fallback_names: list[str] | None = None,
    profile: DocumentProfile = ACTIVE_PROFILE,
) -> GeometryTable:
    """
    Rebuild one table from the words/rules inside ``bbox``.

    Parameters
    ----------
    words, h_rules, v_rules
        Everything on the page (the function selects those inside ``bbox``).
    bbox
        Layout-detected table box (x0, y0, x1, y1), already padded.
    carried_layout
        Layout of the previous table.  Used when this region has no header of
        its own (continuation fragment, error #5) - but only if its x-extent
        matches this region, so a different table is never read with the
        wrong columns.
    fallback_names
        Column names taken from the OCR table header, used (by position) only
        if the PDF itself shows no recognisable header and no layout can be
        carried.
    """

    x0, y0, x1, y1 = bbox
    inside = [w for w in words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]
    table = GeometryTable(bbox=bbox, layout=None)

    if len(inside) < 3:
        table.warnings.append("fewer than 3 words in table region")
        return table

    mh = median_height(inside)
    local_h = [r for r in h_rules if y0 - mh <= r.y <= y1 + mh and r.x1 >= x0 and r.x0 <= x1]
    # A real column border runs most of the table's height.  Short vertical
    # strokes (barcode bars, glyph parts) must not be taken for borders.
    local_v = [
        r for r in v_rules
        if x0 - mh <= r.x <= x1 + mh
        and (min(r.y1, y1) - max(r.y0, y0)) >= 0.5 * (y1 - y0)
    ]

    # ---- 1. columns ------------------------------------------------------
    midas_like = [w for w in inside if profile.midas_like_pattern.match(w.text)]
    first_anchor_y = min((w.cy for w in midas_like), default=None)

    header = detect_header(inside, first_anchor_y, mh)
    if header is not None:
        items, header_bottom = header
        data_words = [w for w in inside if w.cy > header_bottom + 0.1 * mh]
        table.layout = layout_from_header(items, data_words, bbox, local_v, mh)
        table.header_found = True
        y_top = header_bottom + 0.1 * mh
    else:
        data_words = inside
        y_top = y0
        if carried_layout is not None and _layout_compatible(carried_layout, bbox):
            table.layout = ColumnLayout(carried_layout.names, carried_layout.edges, "carried")
            table.warnings.append("no header in region: used the previous table's columns")
        elif fallback_names:
            table.layout = _layout_from_names(fallback_names, data_words, bbox, mh)
            table.warnings.append("no header in PDF: split columns by OCR header names")
        else:
            table.warnings.append("no header and no usable previous layout")
            return table

    layout = table.layout
    if len(layout.edges) != len(layout.names) + 1:
        table.warnings.append("column layout is inconsistent (edges != names + 1)")
        table.layout = None
        return table
    if "Midas Code" not in layout.names:
        table.warnings.append("layout has no 'Midas Code' column")
        return table

    # ---- 2. rows ---------------------------------------------------------
    bands, method, warns = compute_bands(data_words, layout, local_h, y_top, y1, mh, profile)
    table.band_method = method
    table.warnings.extend(warns)

    midas_span = layout.span("Midas Code")
    for lo, hi in bands:
        band_words = [w for w in data_words if lo <= w.cy < hi]
        if not band_words:
            continue
        cells = assign_cells(band_words, layout, mh)
        anchored = any(
            profile.midas_like_pattern.match(w.text)
            for w in band_words if midas_span[0] <= w.cx < midas_span[1]
        )

        if anchored:
            kind = "product"
        elif _has_product_evidence(band_words, profile):
            kind = "blank_midas"           # product data, but no Midas code (#4)
        elif len(group_lines(band_words, 0.6 * mh)) == 1:
            kind = "banner"                # one line, no product data: 'FRESH'
        else:
            kind = "orphan"                # stray text with no product data

        row = GeomRow(kind=kind, y0=lo, y1=hi, cells=cells)
        if kind == "blank_midas":
            row.flags.append("midas_not_found_in_row")
        elif kind == "orphan":
            row.flags.append("text_without_product_data")
        table.rows.append(row)

    # ---- 3. merged Consumer Deal groups ----------------------------------
    resolve_deal_groups(table.rows, layout, data_words, local_h, y_top, y1, mh)
    return table


def _layout_compatible(layout: ColumnLayout, bbox: tuple[float, float, float, float]) -> bool:
    """
    A carried layout fits a region only if their x-extents largely coincide.
    Tolerance: 8% of the layout width on each side.
    """

    width = layout.edges[-1] - layout.edges[0]
    tol = 0.08 * width
    return abs(bbox[0] - layout.edges[0]) <= tol and abs(bbox[2] - layout.edges[-1]) <= tol


def _layout_from_names(
    names: list[str], data_words: list[Word], bbox: tuple[float, float, float, float], mh: float
) -> ColumnLayout:
    """
    Last-resort layout: the OCR told us the column names (in order) but the
    PDF showed no header.  Edges are the N-1 widest white gutters.
    """

    names = [normalize_col(n) for n in names]
    x0, _, x1, _ = bbox
    spans = sorted((w.x0, w.x1) for w in data_words)
    gaps, cursor = [], x0
    for s0, s1 in spans:
        if s0 - cursor > 0.5 * mh:
            gaps.append(((cursor + s0) / 2, s0 - cursor))
        cursor = max(cursor, s1)
    gaps.sort(key=lambda g: -g[1])
    cuts = sorted(g[0] for g in gaps[: len(names) - 1])
    return ColumnLayout(names, [x0, *cuts, x1][: len(names) + 1], "vlm-header")
