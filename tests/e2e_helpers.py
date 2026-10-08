"""Helpers shared by the end-to-end tests: render pages, fake a faulty OCR JSON."""

import json
from pathlib import Path

import pymupdf
import pandas as pd

from config.settings import PDF_DPI
from tests.synthetic_pack import build_pdf

S = PDF_DPI / 72.0
COLS = ["Image", "Product Description", "Case Size", "Midas Code", "EAN Barcode", "Prom WSP",
        "Std RSP", "Consumer Deal", "Promo POR", "Leaflet", "Feature Space", "Shelf"]


def render_pages(pdf: Path, pages_dir: Path):
    pages_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf))
    for i, page in enumerate(doc, start=1):
        page.get_pixmap(matrix=pymupdf.Matrix(S, S)).save(str(pages_dir / f"page_{i:03d}.png"))
    doc.close()


def _html(header, rows, spans=None):
    spans = spans or {}
    out = ["<table>"]
    if header:
        out.append("<tr>" + "".join(f"<td>{h}</td>" for h in header) + "</tr>")
    for r, row in enumerate(rows):
        cells = []
        for c, v in enumerate(row):
            if v is None:
                continue
            span = spans.get((r, c))
            cells.append(f'<td rowspan="{span}">{v}</td>' if span else f"<td>{v}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


def faulty_ocr_json(truth, json_dir: Path):
    """
    Write page_001_res.json / page_002_res.json containing the error classes
    the user reported.  Returns nothing; the faults are deliberate.
    """
    json_dir.mkdir(parents=True, exist_ok=True)
    p1 = [dict(r) for r in truth[:11]]
    rows = []
    for i, r in enumerate(p1):
        row = [f"[f4{i}]" if i % 3 == 0 else r["Image"], r["Product Description"] + (" \\n X" if i == 1 else ""),
               r["Case Size"], r["Midas Code"], r["EAN Barcode"][:6],
               r["Prom WSP"], r["Std RSP"], r["Consumer Deal"], r["Promo POR"], "Ⓕ", r["Feature Space"], r["Shelf"]]
        rows.append(row)
    rows[0][7] = rows[0][7].replace("FOR", "POR") if "FOR" in rows[0][7] else rows[0][7]
    rows[3][7] = "2 POR £5"                      # FOR -> POR
    rows[4][6] = rows[4][6].replace("£", "$")    # £ -> $
    rows[5][3] = rows[5][3][:-1]                 # Midas lost a digit
    rows[6][3] = ""                              # blank Midas
    del rows[8]                                  # a row vanished
    rows[7][6], rows[7][7] = rows[7][7], rows[7][6]   # shifted cells
    rows[1][7] = rows[2][7] = None               # covered by the rowspan below
    spans = {(0, 7): 3}                          # rowspan on the right col for group 1
    block = {"block_label": "table", "block_bbox": [20 * S, 40 * S, 822 * S, 424 * S],
             "block_content": _html(COLS, rows, spans)}
    (json_dir / "page_001_res.json").write_text(json.dumps({"parsing_res_list": [block]}))

    # page 2: header-less continuation, one Midas wrong
    rows2 = [[r["Image"], r["Product Description"], r["Case Size"], r["Midas Code"], "", r["Prom WSP"],
              r["Std RSP"], r["Consumer Deal"], r["Promo POR"], "", r["Feature Space"], r["Shelf"]] for r in truth[11:]]
    rows2[2][3] = "M" + rows2[2][3][1:].replace("1", "7", 1)
    block2 = {"block_label": "table", "block_bbox": [20 * S, 30 * S, 822 * S, 240 * S],
              "block_content": _html(None, rows2)}
    (json_dir / "page_002_res.json").write_text(json.dumps({"parsing_res_list": [block2]}))


def compare(csv_path, truth):
    """Return (n_cells, n_wrong, diffs) comparing the CSV to ground truth by Midas code."""
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    by = {r["Midas Code"]: r for r in truth}
    diffs, cells = [], 0
    for _, row in df.iterrows():
        t = by.get(row["Midas Code"])
        if t is None:
            diffs.append(("unknown midas", row["Midas Code"]))
            continue
        for col in COLS:
            cells += 1
            if row[col] != t[col]:
                diffs.append((row["Midas Code"], col, row[col], t[col]))
    return len(df), cells, diffs
