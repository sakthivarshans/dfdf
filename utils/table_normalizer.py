"""
Table Normalizer
================

Turns the generic tables ``table_reader`` produces into one merged,
**validated** product table.

This module is the strict gate between "something the OCR said" and "a row
in the final CSV".  The guiding rule, which closes errors #1-#8, is:

    A row reaches the CSV only if EVERY field of it is a valid member of the
    document grammar (``config/document_profile.py``).  Every other row goes
    to the needs-review file with the exact reason - none is silently
    dropped, silently "repaired" by guessing, or silently accepted.

What happens to a row (in order)
--------------------------------
1.  **Sanitise** every cell (literal ``\\n``, ``[f47]`` image artefacts).
2.  **Classify**: category banner / section header / product / product with
    no Midas code.
3.  **Realign** a row whose cells slid sideways, but only when the result is
    *verifiably* better (shift of the whole row if the Midas code is out of
    place; a blank-cell shift of the tail if prices/deal are out of place).
4.  **Fill merged groups** (one Consumer Deal against several products).
5.  **Validate every field.**  Clean -> merged CSV.  Anything else ->
    needs-review CSV.

All trading-pack-specific knowledge lives in ``config/document_profile.py``
and ``utils/field_validators.py``; this module only orchestrates.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from config.document_profile import ACTIVE_PROFILE, DocumentProfile
from config.settings import PROFILE_CORRECTIONS
from utils.field_validators import (
    apply_profile_corrections,
    is_blank,
    strip_image_artifacts,
    validate_cell,
    validate_deal,
    validate_row,
)
from utils.headers import CANONICAL, normalize_col  # noqa: F401  (CANONICAL re-exported for old imports)
from utils.table_reader import ROW_MERGES

_log = logging.getLogger(__name__)

REQUIRED_COLS = {"Midas Code", "Consumer Deal"}

#: Strict Midas pattern (M + exactly 6 digits).  Kept as a module constant
#: because older callers import it.
MIDAS_PATTERN = ACTIVE_PROFILE.midas_pattern

_NA_STRINGS = {"nan", "None", "NaN", "<NA>", ""}

#: Metadata keys carried on a row dict next to the real columns.
META_KEYS = ("_source_table", "_page", "_repaired", "_verified_by", "_flags", "_source_row")


# ==========================================================================
# 1. Per-table normalisation (headers, gate)
# ==========================================================================


def _fix_split_case_size(df: pd.DataFrame) -> pd.DataFrame:
    """
    Repair 'Case Size' arriving as separate 'Size' and 'Case' columns:
    rename 'Size' -> 'Case Size', and move Midas-looking values out of
    'Case' into 'Midas Code' before dropping it.
    """

    cols = set(df.columns)
    if "Size" not in cols or "Case Size" in cols:
        return df

    df = df.rename(columns={"Size": "Case Size"})

    if "Case" in df.columns:
        if "Midas Code" not in df.columns:
            df = df.rename(columns={"Case": "Midas Code"})
        else:
            mask = (
                df["Midas Code"].isna() | (df["Midas Code"].str.strip() == "")
            ) & df["Case"].str.strip().str.match(ACTIVE_PROFILE.midas_like_pattern)
            df.loc[mask, "Midas Code"] = df.loc[mask, "Case"]
            df = df.drop(columns=["Case"])

    return df


def _sanitise_frame(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean every cell of an OCR table (#9, #11): literal escape sequences,
    ``[f47]`` image artefacts and surplus whitespace go; text that becomes
    empty becomes NA.
    """

    def clean(value):
        text = strip_image_artifacts(value)
        return pd.NA if text in _NA_STRINGS else text

    return df.astype(object).map(clean)


def normalize_table(
    table_df: pd.DataFrame,
    source_name: str,
    inherit_schema: list[str | None] | None = None,
) -> pd.DataFrame | None:
    """
    Canonicalise one table's headers and decide whether it is a product table.

    Returns None for anything that is not one - banner tables, contents
    pages, and the like.

    ``inherit_schema`` fixes error #5 (tail rows lost from split tables).
    When a table continues on the next page or block, the continuation has
    NO header row of its own; the old code then found neither ``Midas Code``
    nor ``Consumer Deal`` and discarded the whole fragment.  If the previous
    product table's per-position column names are passed in, and this table
    has the same number of columns and contains Midas-looking values, it is
    read with those names instead.
    """

    merges = table_df.attrs.get(ROW_MERGES, [])

    df = table_df.copy()
    df.columns = df.columns.astype(str)

    positional = all(re.fullmatch(r"\d+", c) for c in df.columns)
    if (
        inherit_schema is not None
        and positional
        and len(df.columns) == len(inherit_schema)
        and _contains_midas_like(df)
    ):
        # a header-less continuation: take the previous table's columns
        df.columns = [name if name else f"Unnamed: {i}" for i, name in enumerate(inherit_schema)]
        merges = [(a, b, inherit_schema[c] if isinstance(c, int) and c < len(inherit_schema) else c)
                  for a, b, c in merges]
        _log.info("%s: continuation table - inherited %d column names", source_name, len(df.columns))

    df = _sanitise_frame(df)

    # per-position canonical names, remembered for the NEXT table's benefit
    raw_schema: list[str | None] = []
    for c in df.columns:
        if re.match(r"^Unnamed.*|\d+$|^\s*$", str(c)):
            raw_schema.append(None)
        else:
            raw_schema.append(normalize_col(c))

    # drop unnamed / purely positional / blank columns
    df = df.loc[:, ~df.columns.str.match(r"^Unnamed.*|\d+$|^\s*$")]
    df.columns = [normalize_col(c) for c in df.columns]

    # two raw headers can normalise to the same name; concat needs them unique
    df = df.loc[:, ~df.columns.duplicated()]

    df = _fix_split_case_size(df)

    if not REQUIRED_COLS.issubset(set(df.columns)):
        return None

    df["source_table"] = source_name

    # Carry the vertical merges across the renames, dropping any whose column
    # did not survive them.
    df.attrs[ROW_MERGES] = [
        (first, last, normalize_col(column))
        for first, last, column in merges
        if normalize_col(column) in set(df.columns)
    ]
    df.attrs["raw_schema"] = raw_schema
    return df


def _contains_midas_like(df: pd.DataFrame) -> bool:
    """Does any cell of the table look like a Midas code (M + digits)?"""

    pattern = ACTIVE_PROFILE.midas_like_pattern
    return any(
        isinstance(v, str) and pattern.match(v.strip())
        for column in df.columns for v in df[column].tolist()
    )


# ==========================================================================
# 2. Row-level repair
# ==========================================================================


def _is_banner(values: list) -> bool:
    """
    A category banner ('FRESH', 'GROCERY') is one full-width merged cell, so
    the table parser replicates the same text into every column of the row.
    """

    present = [str(v).strip() for v in values if pd.notna(v)]
    return len(present) > 1 and len(set(present)) == 1


def _score(values: list, columns: list[str]) -> int:
    """
    Net count of values that are valid for the column they sit in: +1 for a
    valid value, -1 for an invalid one.  Empty cells are ignored.  Used to
    compare candidate realignments of the SAME row.
    """

    score = 0
    for column, value in zip(columns, values):
        if is_blank(value):
            continue
        score += 1 if validate_cell(column, value) is None else -1
    return score


def _candidates(values: list, shift: int, anchor: int) -> list[tuple[list, list]]:
    """
    Ways to realign a row so the real Midas code lands in 'Midas Code',
    each paired with the cells that realignment discards.

    shift > 0: the row has ``shift`` spurious cells somewhere left of the
    anchor - drop them.  Which ones is not knowable up front, so every
    position is offered and the caller picks.

    shift < 0: the row is missing cells before the anchor - pad instead,
    discarding nothing.
    """

    width = len(values)
    out = []

    if shift > 0:
        for p in range(0, anchor - shift + 1):
            candidate = values[:p] + values[p + shift:] + [pd.NA] * shift
            out.append((candidate[:width], values[p:p + shift]))
    elif shift < 0:
        pad = -shift
        for p in range(anchor, -1, -1):
            candidate = values[:p] + [pd.NA] * pad + values[p:]
            out.append((candidate[:width], []))

    return out


def _discard_cost(dropped: list) -> tuple[int, int]:
    """
    How much information a repair throws away: how many non-empty cells, then
    how much text.  Candidates frequently tie on score because the column
    that would separate them (Product Description) is free text; preferring
    to discard an empty cell over a filled one breaks the tie correctly.
    """

    filled = [str(v).strip() for v in dropped if pd.notna(v) and str(v).strip()]
    return len(filled), sum(len(v) for v in filled)


def repair_row(values: list, columns: list[str]) -> tuple[list | None, str]:
    """
    Try to realign a row whose 'Midas Code' cell is not a Midas code.

    Returns (repaired values, reason).  None means the row could not be
    verified as improved and must be rejected rather than guessed at - a
    repair that fixes Midas Code by pushing the product name into another
    column is worse than no repair.
    """

    if _is_banner(values):
        return None, "category banner row"

    midas_ix = columns.index("Midas Code")
    hits = [
        i for i, v in enumerate(values)
        if pd.notna(v) and ACTIVE_PROFILE.midas_pattern.match(str(v).strip())
    ]

    if not hits:
        return None, "no Midas code anywhere in the row"
    if len(hits) > 1:
        return None, "several Midas-looking values, alignment ambiguous"

    anchor = hits[0]
    shift = anchor - midas_ix
    if shift == 0:
        return None, "Midas Code column holds a non-Midas value"

    baseline = _score(values, columns)
    ranked = []

    for candidate, dropped in _candidates(values, shift, anchor):
        if str(candidate[midas_ix]).strip() != str(values[anchor]).strip():
            continue  # did not actually land the anchor
        candidate_score = _score(candidate, columns)
        if candidate_score <= baseline:
            continue  # not an improvement - refuse rather than guess
        ranked.append(((-candidate_score,) + _discard_cost(dropped), candidate, candidate_score))

    if not ranked:
        return None, f"no realignment improved the row (score {baseline})"

    _, best, best_score = min(ranked, key=lambda item: item[0])
    return best, f"realigned by {shift:+d} (score {baseline} -> {best_score})"


def repair_tail(values: list, columns: list[str]) -> list | None:
    """
    Fix a row whose Midas code is right but whose LATER cells slid sideways
    (error #8: a valid Midas used to make the whole row pass unchecked).

    Strategy: move cells only across BLANK cells, so no text is ever
    discarded, and accept a candidate only if the resulting row is **fully
    valid**.  Anything less is refused; the row then goes to review with its
    original values and the exact validation issues.
    """

    midas_ix = columns.index("Midas Code")
    named = dict(zip(columns, values))
    issues = validate_row(named)
    if not issues:
        return None
    # Evidence of a shift = a cell that HOLDS a wrong value.  A merely BLANK
    # mandatory cell is not evidence: grammars overlap (a plain "£1.25" is a
    # valid Std RSP AND a valid Consumer Deal), so "repairing" a blank by
    # sliding a neighbour into it can silently put the wrong value in the
    # wrong column.  Blank cells are for group-fill or review, not for guessing.
    if all(i.code.startswith("blank_") for i in issues):
        return None

    width = len(values)
    best = None

    for p in range(midas_ix + 1, width):
        for k in (1, 2):
            # (a) delete k BLANK cells at p, shifting the rest left
            if p + k <= width and all(is_blank(v) for v in values[p:p + k]):
                cand = values[:p] + values[p + k:] + [pd.NA] * k
                best = _better(best, cand, columns)
            # (b) insert k blanks at p, shifting the rest right (tail must be blank)
            if all(is_blank(v) for v in values[width - k:]):
                cand = values[:p] + [pd.NA] * k + values[p:width - k]
                best = _better(best, cand, columns)

    # (c) slide ONE value into an adjacent blank cell (a single misplaced cell)
    for i in range(midas_ix + 1, width - 1):
        for a, b in ((i, i + 1), (i + 1, i)):
            if is_blank(values[a]) and not is_blank(values[b]):
                cand = list(values)
                cand[a], cand[b] = values[b], pd.NA
                best = _better(best, cand, columns)

    return best


def _better(current, candidate, columns):
    """Keep ``candidate`` only if it is fully valid (and ``current`` is not)."""

    if current is not None:
        return current
    return candidate if not validate_row(dict(zip(columns, candidate))) else None


# ==========================================================================
# 3. Merged groups (one Consumer Deal against several products)
# ==========================================================================


def fill_merged_groups(rows: list[dict], merges: list[tuple]) -> int:
    """
    Spread a vertically merged value down the rows the document grouped.

    ``rows`` are the realigned rows of ONE table in table order, each with a
    ``_source_row`` index.  ``merges`` are the ``(first, last, column)``
    spans the table parser saw.

    What changed (errors #6 and #7)
    -------------------------------
    * The **extent** of a group is taken from the OCR span (the only record
      of which rows were drawn together) - but the **value** is never taken
      from the cell the OCR marked.  PaddleOCR hangs the span on the wrong
      column (Std RSP instead of Consumer Deal) whenever its layout was
      misread.
    * The value is searched for across the WHOLE group, not just the first
      row.  Previously, when the first row was missing, blank or rejected,
      the entire group was abandoned (error #7).  Now the first row of the
      group, in order, that holds a *valid* value supplies it.
    * If no row of the group holds a valid value, the first non-blank text
      is still spread, so the review file shows what the document said.
    * Only blanks are written.  A row that stated its own value keeps it.

    When PDF geometry is available this function is not used at all - the
    group comes straight from the drawn borders (``pdf_geometry``).
    """

    if not rows or not merges:
        return 0

    by_source = {row["_source_row"]: row for row in rows}
    filled = 0

    for first, last, column in merges:
        group = [by_source[i] for i in range(first, last + 1) if i in by_source]
        if len(group) < 2:
            continue

        def valid(value) -> bool:
            if is_blank(value):
                return False
            if column == "Consumer Deal":
                return validate_deal(value) is None
            return validate_cell(column, value) is None

        source_value = next((r.get(column) for r in group if valid(r.get(column))), None)
        if source_value is None:
            source_value = next((r.get(column) for r in group if not is_blank(r.get(column))), None)
        if source_value is None:
            continue

        for row in group:
            if is_blank(row.get(column)):
                row[column] = source_value
                filled += 1

    return filled


# ==========================================================================
# 4. Validation and the kept / needs-review split
# ==========================================================================


def sanitise_row(row: dict, profile: DocumentProfile = ACTIVE_PROFILE) -> dict:
    """
    Final per-cell tidy of one row dict (in place; also returned).

    The ``Image`` column is **positional shelf information**, not text (#11):
    only a plain small integer survives; anything else the OCR "read" from
    the product thumbnail is dropped.
    """

    for key in list(row):
        if key in META_KEYS:
            continue
        row[key] = strip_image_artifacts(row[key])

    if "Image" in row and not profile.column_patterns["Image"].match(row["Image"] or ""):
        row["Image"] = ""
    return row


def finalize_rows(
    rows: list[dict], profile: DocumentProfile = ACTIVE_PROFILE
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Validate every field of every row and split into (kept, needs_review).

    Used by BOTH extraction paths - rows rebuilt from PDF geometry and rows
    read from the OCR HTML - so the same strict rules apply to each.
    """

    stats = {"validated": len(rows), "kept": 0, "needs_review": 0, "autofixed": 0,
             "corrections": []}
    kept: list[dict] = []
    review: list[dict] = []

    for row in rows:
        sanitise_row(row, profile)
        flags = list(row.get("_flags", []))

        # Controlled correction of known OCR confusions ($ for £, POR for FOR).
        # Only applied when the corrected value is fully valid; every change is
        # recorded for the audit file.  Skipped for rows read from the PDF text
        # layer, whose characters are exact.
        if PROFILE_CORRECTIONS and row.get("_verified_by", "vlm-only") != "text-layer":
            for column in profile.currency_columns:
                if column in row and row[column]:
                    new, note = apply_profile_corrections(column, row[column], profile)
                    if note:
                        row[column] = new
                        stats["autofixed"] += 1
                        stats["corrections"].append({
                            "page": row.get("_page", ""), "midas": row.get("Midas Code", ""),
                            "column": column, "change": note})
                        if not str(row.get("_verified_by", "")).endswith("+profile-correction"):
                            row["_verified_by"] = row.get("_verified_by", "vlm-only") + "+profile-correction"

        issues = validate_row({k: v for k, v in row.items() if k not in META_KEYS}, profile)

        # Flags raised upstream that make a row untrustworthy on their own
        blocking = [f for f in flags if f in BLOCKING_FLAGS]

        record = {c: row.get(c, "") for c in profile.output_columns}
        record.update({
            "source_table": row.get("_source_table", ""),
            "page": row.get("_page", ""),
            "verified_by": row.get("_verified_by", ""),
            "repaired": bool(row.get("_repaired", False)),
        })

        if not issues and not blocking:
            kept.append(record)
            stats["kept"] += 1
        else:
            record["reason"] = "; ".join([str(i) for i in issues] + blocking)
            record["issue_codes"] = ",".join(sorted({i.code for i in issues} | set(blocking)))
            review.append(record)
            stats["needs_review"] += 1

    kept_df = pd.DataFrame(kept, columns=_kept_columns(profile))
    review_df = pd.DataFrame(review, columns=_review_columns(profile))
    return kept_df, review_df, stats


#: Upstream flags that send a row to review even if every field validates.
BLOCKING_FLAGS = {
    "ean_conflict",             # bars and printed digits disagree
    "second_pass_disagrees",    # two OCR reads valid but different
    "deal_group_unresolved",    # could not tell which rows share a deal
}


def _kept_columns(profile: DocumentProfile) -> list[str]:
    return list(profile.output_columns) + ["source_table", "page", "verified_by", "repaired"]


def _review_columns(profile: DocumentProfile) -> list[str]:
    return _kept_columns(profile) + ["reason", "issue_codes"]


# ==========================================================================
# 5. One OCR-HTML table -> kept / needs-review  (the VLM-only path)
# ==========================================================================


def _row_has_product_data(values: list) -> bool:
    """Any price, percentage or long digit string means 'this is a product row'."""

    for v in values:
        if is_blank(v):
            continue
        text = str(v)
        if re.search(r"£\s?\d|\d\s?%|\d{8,}", text) or ACTIVE_PROFILE.midas_like_pattern.match(text.strip()):
            return True
    return False


def clean_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Classify, realign, group-fill and validate the rows of ONE OCR table.

    Must run per table, before any concat: realignment is positional and a
    concat unions columns into an order that belongs to no single table.

    Changes against the old behaviour:

    * **#4** A row with a blank Midas code is no longer ``continue``d past.
      If it has no product data it is a section header (counted, harmless).
      If it HAS product data it is a real product that lost its code: it
      goes to needs-review with ``blank_midas_product_row`` so the geometry
      or second-pass stage can recover it - never silently lost.
    * **#8** Every row, not only rows with a bad Midas, is validated field by
      field; rows whose prices / deal slid sideways are repaired only when
      the repair yields a fully valid row, otherwise sent to review.
    * **#3** The Midas pattern is exact (M + 6 digits).
    """

    stats = {"rows_in": len(df), "blank_midas": 0, "banners": 0, "repaired": 0,
             "rejected": 0, "filled": 0, "needs_review": 0}

    columns = list(df.columns)
    # ``source_table`` and ``page`` are metadata added by the caller; counting
    # them as cells made a banner row ("FRESH" x 11) look like it held two
    # different values, so banners were rejected instead of recognised.
    data_columns = [c for c in columns if c not in ("source_table", "page")]
    source_table = df["source_table"].iloc[0] if "source_table" in df.columns and len(df) else ""

    aligned: list[dict] = []
    early_review: list[dict] = []

    for source_row, row in df.iterrows():
        values = [row[c] for c in data_columns]
        meta = {"_source_table": row.get("source_table", source_table),
                "_source_row": source_row, "_verified_by": "vlm-only", "_flags": [],
                "_repaired": False}
        midas = row["Midas Code"]

        # ---- blank Midas (#4) ---------------------------------------------
        if is_blank(midas):
            if _row_has_product_data(values) and not _is_banner(values):
                # maybe the whole row slid so the Midas code sits elsewhere
                repaired, _ = repair_row(values, data_columns)
                if repaired is not None:
                    aligned.append(dict(zip(data_columns, repaired)) | meta | {"_repaired": True})
                    stats["repaired"] += 1
                    continue
                early_review.append(dict(zip(data_columns, values)) | meta |
                                    {"_flags": ["blank_midas_product_row"]})
                stats["needs_review"] += 1
            else:
                stats["blank_midas"] += 1           # section header: structure, not data
            continue

        # ---- Midas present and strictly valid -----------------------------
        if ACTIVE_PROFILE.midas_pattern.match(str(midas).strip()):
            # NOTE: tail repair is deliberately NOT done here.  It runs after
            # merged groups are filled (below): a blank Consumer Deal covered
            # by a rowspan is expected and must not look like a shifted row.
            aligned.append(dict(zip(data_columns, values)) | meta)
            continue

        # ---- Midas cell holds something else ------------------------------
        text = str(midas).strip()
        if ACTIVE_PROFILE.midas_like_pattern.match(text):
            # right place, wrong digit count (e.g. M12345): content error, not
            # an alignment error - shifting cells cannot fix it
            aligned.append(dict(zip(data_columns, values)) | meta)
            continue

        repaired, reason = repair_row(values, data_columns)
        if repaired is not None:
            stats["repaired"] += 1
            aligned.append(dict(zip(data_columns, repaired)) | meta | {"_repaired": True})
        elif reason == "category banner row":
            stats["banners"] += 1
        else:
            stats["rejected"] += 1
            early_review.append(dict(zip(data_columns, values)) | meta | {"_flags": [reason]})

    stats["filled"] = fill_merged_groups(aligned, df.attrs.get(ROW_MERGES, []))

    # Tail repair (#8), only now that merged groups are filled.
    for row in aligned:
        if row["_repaired"] or "Midas Code" not in row:
            continue
        cells = {c: row.get(c) for c in data_columns}
        tail = repair_tail([cells[c] for c in data_columns], data_columns)
        if tail is not None:
            row.update(dict(zip(data_columns, tail)))
            row["_repaired"] = True
            stats["repaired"] += 1

    # The Leaflet column holds an ICON (#12).  Whatever text the OCR "read"
    # from it is noise, so it is cleared here; the geometry pass sets the real
    # Yes/No from pixels.
    for row in aligned:
        if "Leaflet" in row:
            row["Leaflet"] = ""

    kept_df, review_df, fin = finalize_rows(aligned)

    if early_review:
        early_df = _early_review_frame(early_review)
        review_df = pd.concat([early_df, review_df], ignore_index=True) if not review_df.empty else early_df

    stats["needs_review"] += fin["needs_review"]
    stats["rejected"] += fin["needs_review"]
    stats["corrections"] = fin["corrections"]
    return kept_df, review_df, stats


def _early_review_frame(rows: list[dict]) -> pd.DataFrame:
    """Rows rejected before validation: keep their raw cells and the reason."""

    out = []
    for row in rows:
        sanitise_row(row)
        record = {c: row.get(c, "") for c in ACTIVE_PROFILE.output_columns}
        record.update({
            "source_table": row.get("_source_table", ""), "page": row.get("_page", ""),
            "verified_by": row.get("_verified_by", ""), "repaired": False,
            "reason": "; ".join(row.get("_flags", [])) or "rejected",
            "issue_codes": ",".join(row.get("_flags", [])),
        })
        out.append(record)
    return pd.DataFrame(out, columns=_review_columns(ACTIVE_PROFILE))


def merge(frames: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Clean each qualifying table, then concatenate into one product table.

    Returns (merged, needs_review, stats).  Rows are never dropped silently:
    a row lands in ``merged``, or in ``needs_review`` with the reason, or is
    counted in ``stats`` as structural noise (banner / section header).
    """

    stats = {"rows_in": 0, "rows_out": 0, "blank_midas": 0, "banners": 0,
             "repaired": 0, "rejected": 0, "filled": 0, "needs_review": 0}
    if not frames:
        return pd.DataFrame(columns=_kept_columns(ACTIVE_PROFILE)), pd.DataFrame(columns=_review_columns(ACTIVE_PROFILE)), stats

    kept_frames, review_frames = [], []
    for frame in frames:
        kept, review, frame_stats = clean_rows(frame)
        for key, value in frame_stats.items():
            if isinstance(value, (int, float)):
                stats[key] = stats.get(key, 0) + value
            elif key == "corrections":
                stats.setdefault("corrections", []).extend(value)
        if not kept.empty:
            kept_frames.append(kept)
        if not review.empty:
            review_frames.append(review)

    merged_df = pd.concat(kept_frames, ignore_index=True) if kept_frames else pd.DataFrame(columns=_kept_columns(ACTIVE_PROFILE))
    review_df = pd.concat(review_frames, ignore_index=True) if review_frames else pd.DataFrame(columns=_review_columns(ACTIVE_PROFILE))
    stats["rows_out"] = len(merged_df)
    return merged_df, review_df, stats
