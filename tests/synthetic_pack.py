"""
Synthetic trading-pack PDF generator (test fixture)
===================================================

Draws a PDF that has the same *structure* as the real documents - a header,
section banners, vertically merged Consumer Deal cells, vector barcodes with
printed digits, a Leaflet icon column, and a continuation page whose table
has NO header - and returns the exact ground-truth rows.

Because the ground truth is known, tests can compare the pipeline's CSV to it
cell by cell and report a real accuracy figure instead of a guess.

Options let a test knock out a feature (e.g. ``draw_rules=False``) to prove
the system degrades to *needs-review* rather than to wrong data.
"""

from __future__ import annotations

import random
from pathlib import Path

import pymupdf

from tests.barcode_fixture import ean13_check_digit, ean13_modules

# column name -> width in PDF points (sum = 802; table starts at x=20)
COLUMNS = [
    ("Image", 40), ("Product Description", 170), ("Case Size", 60),
    ("Midas Code", 60), ("EAN Barcode", 105), ("Prom WSP", 50),
    ("Std RSP", 50), ("Consumer Deal", 90), ("Promo POR", 50),
    ("Leaflet", 40), ("Feature Space", 55), ("Shelf", 32),
]
TABLE_X0 = 20.0
ROW_H = 30.0
HEADER_Y = 40.0
HEADER_H = 24.0

PRODUCTS = [
    "AERO GIANT MILK PM165", "AERO GIANT ORANGE PM165", "KIT KAT 4 FINGER PM165",
    "CARLSBERG PILSNER PM525", "CARLSBERG EXPORT PM525", "WALKERS CRISPS PM129",
    "PRINGLES ORIGINAL PM229", "PRINGLES SOUR CREAM PM229", "PRINGLES BBQ PM229",
    "PRINGLES HOT PAPRIKA PM229", "DORITOS CHILLI PM229", "DORITOS TANGY PM229",
    "COCA COLA ZERO PM139", "FANTA ORANGE PM139", "SPRITE ZERO PM139",
    "ECHO FALLS CHARDONNAY", "JACOBS CREEK SHIRAZ",
]
CASE_SIZES = ["15 x 90G", "24 x 40G", "6 x 4 x 440ML", "12 x 1L", "18 x 150G"]


def _col_edges() -> list[float]:
    edges = [TABLE_X0]
    for _, w in COLUMNS:
        edges.append(edges[-1] + w)
    return edges


EDGES = _col_edges()


def build_ground_truth(n_rows: int = 18, seed: int = 7) -> list[dict]:
    """Deterministic fake product rows (final, correct CSV values)."""

    rng = random.Random(seed)
    # deal groups: (size, deal text) cycled until n_rows is filled
    groups = [(3, "£1.50"), (2, "2 FOR £5"), (1, "HALF PRICE: £3.27"),
              (4, "ANY 2 FOR £3.00"), (3, "£2.19"), (2, "3 FOR 2")]
    rows: list[dict] = []
    g = 0
    while len(rows) < n_rows:
        size, deal = groups[g % len(groups)]
        g += 1
        for _ in range(size):
            if len(rows) >= n_rows:
                break
            i = len(rows)
            first12 = "50" + "".join(str(rng.randint(0, 9)) for _ in range(10))
            rows.append({
                "Image": str(1 + i % 4),
                "Product Description": PRODUCTS[i % len(PRODUCTS)],
                "Case Size": rng.choice(CASE_SIZES),
                "Midas Code": f"M{300000 + rng.randint(1000, 99999):06d}",
                "EAN Barcode": first12 + ean13_check_digit(first12),
                "Prom WSP": f"£{rng.randint(5, 30)}.{rng.randint(0, 99):02d}",
                "Std RSP": f"£{rng.randint(1, 4)}.{rng.randint(0, 99):02d}",
                "Consumer Deal": deal,
                "Promo POR": f"{rng.randint(5, 40)}.{rng.randint(0, 99):02d}%",
                "Leaflet": "Yes" if rng.random() < 0.4 else "No",
                "Feature Space": rng.choice(["", "", "ENDCAP", "GONDOLA"]),
                "Shelf": str(rng.randint(1, 9)),
                "_group_id": g,
            })
    return rows


def _text(page, x, y, s, size=7.0):
    page.insert_text((x, y), s, fontsize=size, fontname="helv")


def _draw_barcode(page, x, y, digits13: str):
    """Vector barcode (one filled rect per bar run) + printed digits below."""
    bits = ean13_modules(digits13)
    module = 0.9                      # pt  (~0.32 mm, the nominal EAN size)
    x_start = x + 4
    i = 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            page.draw_rect(pymupdf.Rect(x_start + i * module, y, x_start + j * module, y + 17),
                           color=None, fill=(0, 0, 0))
            i = j
        else:
            i += 1
    _text(page, x + 6, y + 25, f"{digits13[0]} {digits13[1:7]} {digits13[7:]}", 6.0)


def _draw_header(page, y):
    for (name, w), x in zip(COLUMNS, EDGES):
        # Real headers wrap onto two lines ("Midas" over "Code") - stacked
        parts = name.split(" ")
        if len(parts) == 2 and name in ("Midas Code", "Consumer Deal", "Std RSP", "Prom WSP"):
            _text(page, x + 3, y + 9, parts[0], 7.5)
            _text(page, x + 3, y + 19, parts[1], 7.5)
        else:
            _text(page, x + 3, y + 14, name, 7.5)


def _draw_rows(page, rows, y_start, *, banner_before=None, draw_rules=True,
               leaflet_icons=True, blank_midas_index=None, drop_deal_rules=False):
    """
    Draw ``rows`` starting at ``y_start``.  Returns {row index -> (y0, y1)}.
    ``banner_before``: {row index: 'FRESH'} inserts a banner row above it.
    """

    banner_before = banner_before or {}
    extents = {}
    y = y_start
    h_lines = [y]                                  # separators for non-deal columns
    deal_lines = [y]                               # separators inside the deal column
    previous_group = None

    for idx, row in enumerate(rows):
        if idx in banner_before:
            _text(page, EDGES[1] + 3, y + 18, banner_before[idx], 9)
            y += ROW_H
            h_lines.append(y)
            deal_lines.append(y)
            previous_group = None

        y0, y1 = y, y + ROW_H
        extents[idx] = (y0, y1)

        _text(page, EDGES[0] + 12, y0 + 18, row["Image"])
        # product description may wrap to 2 lines (real documents do)
        words = row["Product Description"].split(" ")
        half = max(1, len(words) // 2) if len(row["Product Description"]) > 22 else len(words)
        _text(page, EDGES[1] + 3, y0 + (11 if half < len(words) else 18), " ".join(words[:half]))
        if half < len(words):
            _text(page, EDGES[1] + 3, y0 + 22, " ".join(words[half:]))
        _text(page, EDGES[2] + 3, y0 + 18, row["Case Size"])
        if idx != blank_midas_index:
            _text(page, EDGES[3] + 3, y0 + 18, row["Midas Code"])
        _draw_barcode(page, EDGES[4], y0 + 4, row["EAN Barcode"])
        _text(page, EDGES[5] + 3, y0 + 18, row["Prom WSP"])
        _text(page, EDGES[6] + 3, y0 + 18, row["Std RSP"])
        _text(page, EDGES[8] + 3, y0 + 18, row["Promo POR"])
        if leaflet_icons and row["Leaflet"] == "Yes":
            page.draw_circle(pymupdf.Point(EDGES[9] + 20, y0 + 15), 6, color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
        _text(page, EDGES[10] + 3, y0 + 18, row["Feature Space"])
        _text(page, EDGES[11] + 8, y0 + 18, row["Shelf"])

        y = y1
        h_lines.append(y1)
        if previous_group is not None and row["_group_id"] != previous_group:
            deal_lines.append(y0)
        previous_group = row["_group_id"]

    deal_lines.append(y)

    # ---- merged Consumer Deal text: printed ONCE, vertically centred ------
    by_group: dict[int, list[int]] = {}
    for idx, row in enumerate(rows):
        by_group.setdefault(row["_group_id"], []).append(idx)
    for members in by_group.values():
        top, bottom = extents[members[0]][0], extents[members[-1]][1]
        _text(page, EDGES[7] + 3, (top + bottom) / 2 + 3, rows[members[0]]["Consumer Deal"])

    # ---- rules -------------------------------------------------------------
    if draw_rules:
        for yy in h_lines:
            # every column except Consumer Deal
            page.draw_line((EDGES[0], yy), (EDGES[7], yy), width=0.5)
            page.draw_line((EDGES[8], yy), (EDGES[-1], yy), width=0.5)
        if not drop_deal_rules:
            for yy in sorted(set(deal_lines) | {h_lines[0], h_lines[-1]}):
                page.draw_line((EDGES[7], yy), (EDGES[8], yy), width=0.5)
        for x in EDGES:
            page.draw_line((x, h_lines[0]), (x, y), width=0.5)
    return extents, y


def build_pdf(path: str | Path, *, draw_rules=True, leaflet_icons=True,
              blank_midas_row: int | None = None, drop_deal_rules=False,
              seed: int = 7) -> list[dict]:
    """
    Write a 2-page PDF to ``path`` and return the ground-truth rows.

    Page 1: header + banner + 11 rows.   Page 2: 7 more rows, NO header
    (a continuation of the same table).
    """

    truth = build_ground_truth(18, seed)
    doc = pymupdf.open()
    page1 = doc.new_page(width=842, height=595)
    _text(page1, 20, 25, "TRADING PACK NP10 - GROCERY", 11)
    _draw_header(page1, HEADER_Y)
    page1.draw_line((EDGES[0], HEADER_Y), (EDGES[-1], HEADER_Y), width=0.5) if draw_rules else None
    page1.draw_line((EDGES[0], HEADER_Y + HEADER_H), (EDGES[-1], HEADER_Y + HEADER_H), width=0.5) if draw_rules else None
    _draw_rows(page1, truth[:11], HEADER_Y + HEADER_H, banner_before={0: "FRESH"},
               draw_rules=draw_rules, leaflet_icons=leaflet_icons,
               blank_midas_index=blank_midas_row, drop_deal_rules=drop_deal_rules)

    page2 = doc.new_page(width=842, height=595)
    # the group straddling the page break keeps its group id; on page 2 it
    # simply starts a new merged cell (as real PDFs do)
    _draw_rows(page2, truth[11:], 30.0, draw_rules=draw_rules, leaflet_icons=leaflet_icons,
               drop_deal_rules=drop_deal_rules)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return truth
