"""
Geometry Pass  (the verification stage between OCR and CSV)
===========================================================

For every page this stage rebuilds the product table from the PDF itself and
writes the result as a *sidecar* file::

    storage/json/<doc>/_geometry/page_006.json

The CSV stage (``scripts/json_to_csv.py``) then reconciles those sidecars
with the OCR JSON.  Keeping this stage separate means the CSV step stays a
fast, GPU-free, replayable post-processing step.

What this stage fixes
---------------------
#4  rows with a blank Midas code are located by their drawn row border
#5  continuation tables with no header reuse the previous table's columns
#6  the Consumer Deal group comes from the drawn borders, not OCR rowspan
#7  ...so a missing/damaged first row cannot lose the group
#10 barcodes are DECODED from the bars (checksum-validated), not OCR'd
#12 the Leaflet icon is detected from pixels, not read as text
#13 on scans, risky cells are re-read from tight crops (second pass)
#14 tables the layout model split or missed are recovered: every Midas code
    on the page must belong to some rebuilt table

Two modes, chosen per page
--------------------------
``text-layer``  The PDF contains real text.  Words are EXACT, so no OCR
                guesswork is involved in any text field.
``ocr``         A scan.  Words come from a second, independent OCR network
                (classic PaddleOCR) and risky cells are re-read by the VLM
                from crops; the CSV stage requires the reads to agree.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.document_profile import ACTIVE_PROFILE
from config.settings import GEOMETRY_SUBDIR, PDF_DPI, TABLE_BBOX_MARGIN_PX
from utils.barcode_reader import best_valid_barcode
from utils.field_validators import (
    clean_text,
    normalize_ean,
    validate_cell,
)
from utils.headers import normalize_col
from utils.json_utils import get_tables, load_json
from utils.page_source import PageSource
from utils.pdf_geometry import (
    ColumnLayout,
    GeomRow,
    Word,
    median_height,
    rebuild_table,
)
from utils.table_reader import _promote_header_row, parse_html_tables

_log = logging.getLogger(__name__)

_PAGE_RE = re.compile(r"page_(\d+)")

#: Columns re-read from a crop on scanned pages (the ones the OCR gets wrong).
SECOND_PASS_COLUMNS = ("Midas Code", "Consumer Deal", "Std RSP", "Prom WSP", "Promo POR")


# --------------------------------------------------------------------------
# table boxes
# --------------------------------------------------------------------------


def _expand(b, margin, page_w, page_h):
    return (max(0, b[0] - margin), max(0, b[1] - margin),
            min(page_w, b[2] + margin), min(page_h, b[3] + margin))


def merge_boxes(boxes: list[tuple]) -> list[tuple]:
    """
    Merge layout boxes that describe ONE table (#14).

    * boxes that overlap strongly (duplicates produced when NMS is off) are
      united;
    * boxes side by side with the same vertical extent (a wide table the
      layout model cut into a left and a right half) are united.

    Boxes stacked on top of each other stay separate - the lower one is a
    continuation and is read with the previous layout.
    """

    boxes = [tuple(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ix = min(a[2], b[2]) - max(a[0], b[0])
                iy = min(a[3], b[3]) - max(a[1], b[1])
                inter = max(0, ix) * max(0, iy)
                smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
                y_overlap = iy / (min(a[3] - a[1], b[3] - b[1]) or 1)
                x_gap = -ix                                   # <0 means overlapping
                duplicate = inter / smaller > 0.5
                side_by_side = y_overlap > 0.7 and x_gap < 0.03 * max(a[2], b[2])
                if duplicate or side_by_side:
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return sorted(boxes, key=lambda b: (b[1], b[0]))


def _ocr_header_names(block: dict) -> list[str] | None:
    """Column names from the OCR table's header row, as a last-resort fallback."""

    html = block.get("block_content") or ""
    for frame in parse_html_tables(html):
        frame = _promote_header_row(frame)
        names = [str(c) for c in frame.columns if not re.fullmatch(r"\d+|Unnamed.*", str(c))]
        if names:
            return [normalize_col(n) for n in names]
    return None


# --------------------------------------------------------------------------
# words for scans (second independent OCR)
# --------------------------------------------------------------------------


def _ocr_words(source: PageSource, box, engine) -> list[Word]:
    """Word boxes for a scanned page region from the classic OCR network."""

    crop = source.crop(box, dpi=source.dpi)
    if crop is None:
        return []
    words = []
    for text, x0, y0, x1, y1 in engine.read_crop_words(crop):
        words.append(Word(text, x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1]))
    return words


# --------------------------------------------------------------------------
# per-row enrichment: barcode, Leaflet, second pass
# --------------------------------------------------------------------------


def _cell_box(layout: ColumnLayout, column: str, row: GeomRow):
    span = layout.span(column)
    return None if span is None else (span[0], row.y0, span[1], row.y1)


def enrich_row(row: GeomRow, layout: ColumnLayout, source: PageSource, engine, mode: str) -> None:
    """Add everything pixels can say about one product row (modifies ``row``)."""

    # ---- EAN: decode the bars, compare with the printed digits (#10) ------
    box = _cell_box(layout, "EAN Barcode", row)
    if box is not None:
        printed = normalize_ean(row.cells.get("EAN Barcode", ""))
        printed_ok = bool(printed) and validate_cell("EAN Barcode", printed) is None

        inset = (box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3)
        crop = source.crop(inset)                       # re-rendered sharp from the PDF
        decoded = best_valid_barcode(crop) if crop is not None else None

        if decoded and printed_ok:
            if decoded.value == printed or decoded.value.lstrip("0") == printed.lstrip("0"):
                row.cells["EAN Barcode"] = decoded.value
            else:
                # two independent sources, both checksum-valid, different
                row.cells["EAN Barcode"] = printed
                row.flags.append("ean_conflict")
                row.alt["EAN Barcode"] = decoded.value
        elif decoded:
            row.cells["EAN Barcode"] = decoded.value
            row.flags.append("ean_from_bars_only")
        elif printed_ok:
            row.cells["EAN Barcode"] = printed
            row.flags.append("ean_from_text_only")
        # else: leave whatever was read; validation will reject it

    # ---- Leaflet: icon present?  decided from pixels (#12) ----------------
    box = _cell_box(layout, "Leaflet", row)
    if box is not None:
        from config.settings import LEAFLET_INK_THRESHOLD
        ink = source.ink_ratio(box)
        had_text = bool(row.cells.get("Leaflet"))
        row.cells["Leaflet"] = (
            ACTIVE_PROFILE.leaflet_true if ink >= LEAFLET_INK_THRESHOLD
            else ACTIVE_PROFILE.leaflet_false
        )
        if had_text:
            row.flags.append("leaflet_text_ignored")

    # ---- second-pass crop OCR, scans only (#13) ---------------------------
    if mode == "ocr" and engine is not None:
        for column in SECOND_PASS_COLUMNS:
            box = _cell_box(layout, column, row)
            if box is None:
                continue
            crop = source.crop((box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2), dpi=450)
            if crop is not None:
                row.alt[column] = clean_text(engine.read_crop_text(crop))


# --------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------


def process_page(pdf_path, png_path: Path, json_data: dict | None, page_number: int,
                 engine=None, carried: ColumnLayout | None = None, dpi: int = PDF_DPI):
    """
    Rebuild every table on one page.  Returns (sidecar dict, layout to carry on).
    """

    with PageSource(pdf_path, page_number, png_path, dpi) as source:
        words = source.words()
        mode = "text-layer" if words else ("ocr" if engine is not None else "none")
        h_rules, v_rules = source.rules()

        page_gray = source.crop(None, dpi=dpi)
        page_h, page_w = (page_gray.shape if page_gray is not None else (10 ** 6, 10 ** 6))

        blocks = get_tables(json_data) if json_data else []
        boxes = [tuple(float(v) for v in b["block_bbox"]) for b in blocks if b.get("block_bbox")]
        fallback = {}
        for block in blocks:
            if block.get("block_bbox"):
                fallback[tuple(float(v) for v in block["block_bbox"])] = _ocr_header_names(block)

        boxes = merge_boxes([_expand(b, TABLE_BBOX_MARGIN_PX, page_w, page_h) for b in boxes])

        if mode == "ocr":
            words = []
            for box in boxes:
                words.extend(_ocr_words(source, box, engine))
            h_rules, v_rules = source.rules()

        sidecar = {"page": page_number, "mode": mode, "tables": [], "warnings": [],
                   "anchors_on_page": 0, "rows_rebuilt": 0}

        if mode == "none":
            sidecar["warnings"].append("no text layer and no OCR engine: geometry unavailable")
            return sidecar, carried

        # ---- coverage check (#14) ----------------------------------------
        # Every Midas-looking word on the page must lie inside some table box.
        # A layout model that missed or cut a table leaves anchors outside.
        layout_for_scan = carried
        all_anchors = [w for w in words if ACTIVE_PROFILE.midas_like_pattern.match(w.text)]
        uncovered = [w for w in all_anchors
                     if not any(b[0] <= w.cx <= b[2] and b[1] <= w.cy <= b[3] for b in boxes)]
        if uncovered and layout_for_scan is not None:
            span = layout_for_scan.span("Midas Code")
            uncovered = [w for w in uncovered if span and span[0] <= w.cx < span[1]]
        if uncovered:
            mh = median_height(words)
            pad = 6 * mh
            x0 = layout_for_scan.edges[0] if layout_for_scan else min(w.x0 for w in words)
            x1 = layout_for_scan.edges[-1] if layout_for_scan else max(w.x1 for w in words)
            recovered = (x0, max(0, min(w.y0 for w in uncovered) - pad), x1,
                         min(page_h, max(w.y1 for w in uncovered) + pad))
            boxes = merge_boxes(boxes + [recovered])
            sidecar["warnings"].append(
                f"{len(uncovered)} Midas code(s) lay outside every detected table; recovered by geometry")

        # ---- rebuild each table -----------------------------------------
        for box in boxes:
            names = next((n for n in fallback.values() if n), None)
            table = rebuild_table(words, h_rules, v_rules, box,
                                  carried_layout=carried, fallback_names=names)
            if table.layout is not None:
                carried = table.layout
                for row in table.rows:
                    if row.kind in ("product", "blank_midas"):
                        enrich_row(row, table.layout, source, engine, mode)
            sidecar["tables"].append(table.to_dict())
            sidecar["rows_rebuilt"] += sum(1 for r in table.rows if r.kind == "product")

        # ---- proof of completeness --------------------------------------
        sidecar["anchors_on_page"] = len(all_anchors)
        rebuilt = sidecar["rows_rebuilt"]
        if rebuilt != len(all_anchors):
            sidecar["warnings"].append(
                f"{len(all_anchors)} Midas-like words on the page but {rebuilt} rows rebuilt")
        return sidecar, carried


def run_geometry_pass(pdf_file, pages_folder, json_folder, engine=None, dpi: int = PDF_DPI) -> dict:
    """
    Run the pass over every page image of a document and write the sidecars.
    Returns a small summary dict.
    """

    pdf_file = Path(pdf_file) if pdf_file else None
    pages_folder, json_folder = Path(pages_folder), Path(json_folder)
    out_dir = json_folder / GEOMETRY_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()

    carried = None
    summary = {"pages": 0, "text_layer_pages": 0, "ocr_pages": 0, "rows": 0, "warnings": 0}

    for png in sorted(pages_folder.glob("*.png")):
        m = _PAGE_RE.search(png.stem)
        if not m:
            continue
        number = int(m.group(1))
        res = json_folder / f"{png.stem}_res.json"
        data = load_json(res) if res.exists() else None

        sidecar, carried = process_page(pdf_file, png, data, number, engine, carried, dpi)
        (out_dir / f"page_{number:03d}.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")

        summary["pages"] += 1
        summary["rows"] += sidecar["rows_rebuilt"]
        summary["warnings"] += len(sidecar["warnings"])
        summary["text_layer_pages" if sidecar["mode"] == "text-layer" else "ocr_pages"] += 1
        print(f"  geometry {png.name}: mode={sidecar['mode']} rows={sidecar['rows_rebuilt']} "
              f"warnings={len(sidecar['warnings'])}")
    return summary


if __name__ == "__main__":
    from config.settings import JSON_DIR, PAGES_DIR
    run_geometry_pass("storage/input/sample.pdf", PAGES_DIR / "sample", JSON_DIR / "sample")
