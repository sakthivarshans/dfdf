"""
Reconcile
=========

Combines the independent sources of evidence for each row into ONE decision.

Sources, from most to least trustworthy
---------------------------------------
1.  **PDF text layer** (``mode == "text-layer"``): exact characters at exact
    positions.  Nothing was "read", so nothing can be misread - this is why
    FOR/POR, £/$ and digit errors cannot occur on digital PDFs.
2.  **Two independent OCR reads** (``mode == "ocr"``): the classic OCR's word
    boxes plus a VLM re-read of a tight crop.  Accepted only if they agree
    or exactly one of them is valid.
3.  **The page-level VLM table** (the original pipeline): used only for the
    parts of a page the geometry pass could not rebuild, and as a cross-check
    audit everywhere else.

Nothing here guesses.  Disagreement between two *valid* values is not
resolved by picking one - the row is sent to review.
"""

from __future__ import annotations

import json
from pathlib import Path

from config.document_profile import ACTIVE_PROFILE
from config.settings import GEOMETRY_SUBDIR
from utils.field_validators import clean_text, validate_cell

#: Columns whose second-pass read participates in consensus.
CONSENSUS_COLUMNS = ("Midas Code", "Consumer Deal", "Std RSP", "Prom WSP", "Promo POR")


def load_sidecars(json_folder: str | Path) -> dict[int, dict]:
    """Read every ``_geometry/page_NNN.json`` into {page number: sidecar}."""

    folder = Path(json_folder) / GEOMETRY_SUBDIR
    out: dict[int, dict] = {}
    if folder.is_dir():
        for file in sorted(folder.glob("page_*.json")):
            data = json.loads(file.read_text(encoding="utf-8"))
            out[int(data["page"])] = data
    return out


def consensus(column: str, primary: str, alt: str | None) -> tuple[str, str | None]:
    """
    Decide one cell from two independent reads.

    Returns (value, flag).  ``flag`` is None when the decision is safe.

    * equal                       -> accept
    * only one read is valid      -> take the valid one
    * both valid but different    -> keep the first, flag ``second_pass_disagrees``
    * neither valid               -> keep the first; validation will reject it
    * no second read              -> keep the first (no consensus possible)
    """

    primary, alt = clean_text(primary), clean_text(alt) if alt is not None else None
    if alt is None or alt == "":
        return primary, None
    if primary == alt:
        return primary, None

    ok_primary = bool(primary) and validate_cell(column, primary) is None
    ok_alt = validate_cell(column, alt) is None
    if ok_alt and not ok_primary:
        return alt, None
    if ok_primary and not ok_alt:
        return primary, None
    if ok_primary and ok_alt:
        return primary, "second_pass_disagrees"
    return primary, None


def geometry_to_rows(sidecar: dict) -> tuple[list[dict], dict]:
    """
    Convert one page sidecar into aligned row dicts ready for
    ``finalize_rows``.  Returns (rows, counters).
    """

    page = sidecar["page"]
    mode = sidecar.get("mode", "text-layer")
    verified_by = "text-layer" if mode == "text-layer" else "ocr-two-pass"

    rows: list[dict] = []
    counters = {"banners": 0}

    for t_index, table in enumerate(sidecar.get("tables", []), start=1):
        if not table.get("layout"):
            continue
        for g in table["rows"]:
            if g["kind"] == "banner":
                counters["banners"] += 1
                continue

            cells = dict(g["cells"])
            flags = list(g.get("flags", []))

            if mode == "ocr":                       # two-read consensus (#13)
                for column in CONSENSUS_COLUMNS:
                    if column in cells:
                        cells[column], flag = consensus(column, cells[column], g.get("alt", {}).get(column))
                        if flag:
                            flags.append(flag)

            if g["kind"] == "blank_midas":
                flags.append("blank_midas_product_row")
            if g.get("deal_method") == "own-cell" and not cells.get("Consumer Deal"):
                flags.append("deal_group_unresolved")

            rows.append(cells | {
                "_source_table": f"page-{page}-geometry-{t_index}",
                "_page": page, "_repaired": False,
                "_verified_by": verified_by, "_flags": flags,
            })
    return rows, counters


def covered_by_geometry(sidecar: dict | None, bbox) -> bool:
    """Did the geometry pass successfully rebuild the region around ``bbox``?"""

    if not sidecar or bbox is None:
        return False
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for t in sidecar.get("tables", []):
        b = t["bbox"]
        if t.get("layout") and any(r["kind"] in ("product", "blank_midas") for r in t["rows"]):
            if b[0] <= cx <= b[2] and b[1] <= cy <= b[3]:
                return True
    return False


def cross_check(geometry_rows: list[dict], vlm_rows: list[dict]) -> list[dict]:
    """
    Compare the geometry result with the page-level VLM read, keyed by Midas
    code, and list every disagreement.  Purely informational: the geometry
    value is what goes to the CSV; this file shows where the VLM would have
    been wrong, and flags VLM rows that geometry did not find.
    """

    audit: list[dict] = []
    by_midas = {r["Midas Code"]: r for r in geometry_rows if ACTIVE_PROFILE.midas_pattern.match(str(r.get("Midas Code", "")))}
    seen = set()

    for v in vlm_rows:
        midas = clean_text(v.get("Midas Code"))
        g = by_midas.get(midas)
        if g is None:
            audit.append({"page": v.get("_page", ""), "midas": midas, "column": "(row)",
                          "geometry": "", "vlm": "row only in VLM output"})
            continue
        seen.add(midas)
        for column in ACTIVE_PROFILE.output_columns:
            if column in ("Image", "Leaflet", "EAN Barcode") or column not in v:
                continue                         # these are not text-OCR fields
            gv, vv = clean_text(g.get(column)), clean_text(v.get(column))
            if vv and gv != vv:
                audit.append({"page": g.get("_page", ""), "midas": midas, "column": column,
                              "geometry": gv, "vlm": vv})
    return audit
