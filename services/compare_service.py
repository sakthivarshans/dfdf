"""
Compare Service

Compares one extracted table against an existing-data CSV/Excel upload,
either positionally over shared column names or — the usual case — by
matching rows on a unique key and comparing an explicit list of fields.
Pure dataframe logic — no knowledge of the OCR pipeline or the database.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# how many mismatched rows to hand back for display; the full picture
# goes into the downloadable reports, not the JSON response
DETAIL_LIMIT = 100

STATUS_MISSING = "No data in PDF"
STATUS_SAME = "Same Values"
STATUS_MISMATCH = "Values Mismatch"

_BLANKS = {"", "nan", "none", "<na>", "null", "n/a"}
_NUMERIC_NOISE = re.compile(r"[,\s£$€%]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def load_dataframe(file_path: str) -> pd.DataFrame:
    """
    Load a CSV or Excel file as text.

    Everything here exists to be compared against OCR output, which is
    text; letting pandas infer types turns an Excel ``1`` into ``1.0``
    and an empty cell into ``NaN``, both of which read as mismatches
    against a CSV that says ``1`` and ``""``.
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(file_path, dtype=str, encoding="utf-8-sig")
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(file_path, dtype=str)
    else:
        raise ValueError(f"Unsupported file format: {ext}")
    return df.fillna("")


def dataframe_columns(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.columns]


def excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Strip the control characters Excel refuses to store.

    OCR text occasionally carries one, and openpyxl raises rather than
    writing it — which would lose the whole report over one cell.
    """
    cleaned = df.copy()
    for column in cleaned.columns:
        if cleaned[column].dtype == object:
            cleaned[column] = cleaned[column].map(
                lambda v: _CONTROL_CHARS.sub("", v) if isinstance(v, str) else v
            )
    return cleaned


def _norm(value) -> str:
    """Trim, collapse internal whitespace, and casefold a cell for comparison."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return "" if text.lower() in _BLANKS else text.casefold()


def _as_number(text: str) -> float | None:
    """Read a cell as a number, ignoring thousands separators and currency."""
    stripped = _NUMERIC_NOISE.sub("", text)
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def values_equal(left, right) -> bool:
    """
    Whether two cells say the same thing.

    Compared as normalised text, falling back to a numeric comparison so
    that ``1``, ``1.0`` and ``1,000`` vs ``1000`` — the shapes Excel and
    a CSV disagree on for the same underlying value — are not reported as
    OCR errors.
    """
    left_text, right_text = _norm(left), _norm(right)
    if left_text == right_text:
        return True
    left_num, right_num = _as_number(left_text), _as_number(right_text)
    return left_num is not None and right_num is not None and left_num == right_num


def _require_column(df: pd.DataFrame, column: str, side: str) -> str:
    if column not in df.columns:
        raise ValueError(f"{side} has no column named {column!r}")
    return column


def _key_index(df: pd.DataFrame, column: str) -> tuple[dict, int, int]:
    """
    Map each normalised key to the first row that carries it.

    A duplicate key cannot be matched unambiguously, so the first row
    wins and the rest are counted and reported rather than silently
    compared against an arbitrary partner.
    """
    index: dict[str, int] = {}
    blanks = 0
    duplicates = 0
    for position, value in enumerate(df[column]):
        key = _norm(value)
        if not key:
            blanks += 1
        elif key in index:
            duplicates += 1
        else:
            index[key] = position
    return index, blanks, duplicates


@dataclass
class MappedComparison:
    """What a mapped comparison produces: the figures, and the two reports."""

    summary: dict
    existing_report: pd.DataFrame   # the uploaded file, annotated row by row
    extracted_only: pd.DataFrame    # PDF rows whose key is not in that file


def _unique_column(df: pd.DataFrame, name: str) -> str:
    """A column name the report can add without overwriting the user's own."""
    if name not in df.columns:
        return name
    suffixed = f"{name} (comparison)"
    counter = 2
    while suffixed in df.columns:
        suffixed = f"{name} (comparison {counter})"
        counter += 1
    return suffixed


def compare_mapped(
    extracted_df: pd.DataFrame,
    existing_df: pd.DataFrame,
    key_pair: dict,
    field_pairs: list[dict],
) -> MappedComparison:
    """
    Look every existing row up in the extracted data by key, then check the
    mapped fields on the ones that are found.

    ``key_pair`` and every entry of ``field_pairs`` are
    ``{"extracted": <column>, "existing": <column>}``. The existing file is
    the reference list: every one of its rows comes back in
    ``existing_report`` with a verdict, and every extracted row whose key is
    not in it comes back in ``extracted_only``. Raises ValueError if a named
    column is not present on the side it was named for.
    """
    if not field_pairs:
        raise ValueError("Select at least one field to compare")

    extracted_df = extracted_df.fillna("").reset_index(drop=True)
    existing_df = existing_df.fillna("").reset_index(drop=True)

    ext_key = _require_column(extracted_df, key_pair["extracted"], "Extracted data")
    exi_key = _require_column(existing_df, key_pair["existing"], "Existing data")
    pairs = []
    for pair in field_pairs:
        mapping = (
            _require_column(extracted_df, pair["extracted"], "Extracted data"),
            _require_column(existing_df, pair["existing"], "Existing data"),
        )
        # the same pair chosen twice would share a label and count its hits
        # into one bucket twice, pushing that field's accuracy over 100%
        if mapping not in pairs:
            pairs.append(mapping)

    ext_index, ext_blanks, ext_dupes = _key_index(extracted_df, ext_key)
    exi_index, exi_blanks, exi_dupes = _key_index(existing_df, exi_key)
    existing_keys = set(exi_index)

    labels = [f"{e} → {x}" for e, x in pairs]
    # one value column per compared field, because one column cannot hold
    # several; with a single field it is simply "Value in PDF"
    value_columns = [
        _unique_column(existing_df, "Value in PDF" if len(pairs) == 1 else f"{exi_col} in PDF")
        for _, exi_col in pairs
    ]
    found_column = _unique_column(existing_df, "Data in PDF")
    status_column = _unique_column(existing_df, "Status")

    field_hits = dict.fromkeys(labels, 0)
    found_flags, statuses = [], []
    pdf_values: list[list[str]] = [[] for _ in pairs]
    paired = mismatched = 0
    details = []

    for position in range(len(existing_df)):
        exi_row = existing_df.iloc[position]
        key = _norm(exi_row[exi_key])
        match = ext_index.get(key) if key else None

        if match is None:
            found_flags.append(False)
            statuses.append(STATUS_MISSING)
            for column in pdf_values:
                column.append("")
            continue

        paired += 1
        ext_row = extracted_df.iloc[match]
        differences = {}

        for slot, (label, (ext_col, exi_col)) in enumerate(zip(labels, pairs)):
            ext_val, exi_val = ext_row[ext_col], exi_row[exi_col]
            pdf_values[slot].append(str(ext_val))
            if values_equal(ext_val, exi_val):
                field_hits[label] += 1
            else:
                differences[label] = {
                    "extracted": str(ext_val),
                    "ground_truth": str(exi_val),
                }

        found_flags.append(True)
        if differences:
            mismatched += 1
            statuses.append(STATUS_MISMATCH)
            if len(details) < DETAIL_LIMIT:
                # the key is what identifies the row to the user, not its offset
                details.append({"row": str(exi_row[exi_key]), "differences": differences})
        else:
            statuses.append(STATUS_SAME)

    report = existing_df.copy()
    report[found_column] = found_flags
    report[status_column] = statuses
    for name, column in zip(value_columns, pdf_values):
        report[name] = column

    extracted_only_mask = [
        _norm(value) not in existing_keys for value in extracted_df[ext_key]
    ]
    extracted_only = extracted_df[extracted_only_mask].reset_index(drop=True)

    matching = paired - mismatched
    accuracy = (matching / paired * 100) if paired else 0.0

    summary = {
        "mode": "mapped",
        "success": True,
        "total_rows_extracted": len(extracted_df),
        "total_rows_ground": len(existing_df),
        "rows_paired": paired,
        "rows_compared": paired,
        "only_in_extracted_rows": len(extracted_only),
        "only_in_existing_rows": len(existing_df) - paired,
        "matching_rows": matching,
        "mismatched_rows": mismatched,
        "accuracy_percent": round(accuracy, 2),
        "key_field": {"extracted": ext_key, "existing": exi_key},
        "compare_fields": [{"extracted": e, "existing": x} for e, x in pairs],
        "blank_keys_extracted": ext_blanks,
        "blank_keys_ground": exi_blanks,
        "duplicate_keys_extracted": ext_dupes,
        "duplicate_keys_ground": exi_dupes,
        "column_details": sorted(
            (
                {
                    "column": label,
                    "matches": hits,
                    "total": paired,
                    "accuracy": round((hits / paired * 100) if paired else 0.0, 2),
                }
                for label, hits in field_hits.items()
            ),
            key=lambda d: d["accuracy"],
        ),
        "row_comparison": details,
        "summary": (
            f"Paired {paired} of {len(existing_df)} existing row(s) on "
            f"{ext_key} → {exi_key} across {len(pairs)} field(s). "
            f"Matching: {matching}, mismatched: {mismatched}, "
            f"only in the extracted data: {len(extracted_only)}."
        ),
    }

    return MappedComparison(summary, report, extracted_only)


def compare_dataframes(extracted_df: pd.DataFrame, ground_df: pd.DataFrame) -> dict:
    """
    Compare an extracted DataFrame with a ground-truth DataFrame.
    Returns comparison statistics and row/column-level details.
    """
    extracted_df = extracted_df.copy()
    ground_df = ground_df.copy()
    extracted_df.columns = [str(c).strip().lower() for c in extracted_df.columns]
    ground_df.columns = [str(c).strip().lower() for c in ground_df.columns]

    common_cols = list(set(extracted_df.columns) & set(ground_df.columns))
    only_in_extracted = list(set(extracted_df.columns) - set(ground_df.columns))
    only_in_ground = list(set(ground_df.columns) - set(extracted_df.columns))

    result = {
        "total_rows_extracted": len(extracted_df),
        "total_rows_ground": len(ground_df),
        "total_cols_extracted": len(extracted_df.columns),
        "total_cols_ground": len(ground_df.columns),
        "common_columns": sorted(common_cols),
        "only_in_extracted": sorted(only_in_extracted),
        "only_in_ground": sorted(only_in_ground),
        "column_details": [],
        "row_comparison": [],
        "matching_rows": 0,
        "mismatched_rows": 0,
        "accuracy_percent": 0.0,
    }

    if not common_cols:
        result["summary"] = "No common columns found between extracted and ground truth data."
        return result

    min_rows = min(len(extracted_df), len(ground_df))
    match_count = 0
    mismatch_count = 0
    row_details = []

    for i in range(min_rows):
        row_match = True
        row_diff = {}
        for col in common_cols:
            ext_val = str(extracted_df.iloc[i].get(col, "")).strip()
            grd_val = str(ground_df.iloc[i].get(col, "")).strip()
            if ext_val != grd_val:
                row_match = False
                row_diff[col] = {"extracted": ext_val, "ground_truth": grd_val}

        if row_match:
            match_count += 1
        else:
            mismatch_count += 1
            if len(row_details) < 100:  # limit details to first 100 mismatches
                row_details.append({"row": i + 1, "differences": row_diff})

    extra_extracted = max(0, len(extracted_df) - len(ground_df))
    extra_ground = max(0, len(ground_df) - len(extracted_df))

    total_compared = min_rows
    accuracy = (match_count / total_compared * 100) if total_compared > 0 else 0.0

    col_details = []
    for col in common_cols:
        col_matches = 0
        for i in range(min_rows):
            ext_val = str(extracted_df.iloc[i].get(col, "")).strip()
            grd_val = str(ground_df.iloc[i].get(col, "")).strip()
            if ext_val == grd_val:
                col_matches += 1
        col_acc = (col_matches / min_rows * 100) if min_rows > 0 else 0.0
        col_details.append({
            "column": col,
            "matches": col_matches,
            "total": min_rows,
            "accuracy": round(col_acc, 2),
        })

    result["matching_rows"] = match_count
    result["mismatched_rows"] = mismatch_count
    result["extra_rows_extracted"] = extra_extracted
    result["extra_rows_ground"] = extra_ground
    result["rows_compared"] = min_rows
    result["accuracy_percent"] = round(accuracy, 2)
    result["column_details"] = sorted(col_details, key=lambda x: x["accuracy"])
    result["row_comparison"] = row_details
    result["summary"] = (
        f"Compared {min_rows} rows across {len(common_cols)} common columns. "
        f"Row-level accuracy: {accuracy:.2f}%. "
        f"Matching rows: {match_count}, Mismatched rows: {mismatch_count}."
    )

    return result
