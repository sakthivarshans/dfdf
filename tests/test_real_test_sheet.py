"""
Regression test on the REAL PaddleOCR-VL output of Test-Sheet.pdf (4 pages).

Facts established from this data:
  * the raw OCR JSON contains all 72 products (18+18+17+19) - PaddleOCR did
    not lose any of the 27 rows later reported missing;
  * the OCR itself emits "$" for "£" and "POR" for "FOR", and literal "\\n";
  * every Midas code is M + 6 digits; none is duplicated;
  * the EAN column is blank in the OCR output (barcodes are artwork).
"""

import shutil
from pathlib import Path

import pandas as pd

from scripts.json_to_csv import convert_json_folder

FIXTURES = Path(__file__).parent / "fixtures" / "test_sheet"

PREVIOUSLY_MISSING = """M284967 M284969 M127014 M127015 M127017 M203447 M239447 M302395 M276912
M256723 M268185 M298079 M797027 M203067 M178265 M178264 M307882 M307859 M307858 M224338 M234759
M234772 M234774 M290326 M290409 M290407 M298037""".split()

EXPECTED_DEALS = {  # from the user's list of the 27 missing rows
    "M284967": "50P", "M284969": "50P", "M268185": "2 FOR £3", "M298079": "£8.99",
    "M797027": "2 FOR £1.50", "M203067": "2 FOR £1.50",
    "M178265": "ANY 2 FOR £1.75", "M178264": "ANY 2 FOR £1.75",
}


def _run(tmp_path):
    json_dir = tmp_path / "json"
    shutil.copytree(FIXTURES, json_dir)
    result = convert_json_folder(json_dir, tmp_path / "out" / "t.csv")
    df = pd.read_csv(result.merged_csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return result, df


def test_all_72_products_are_extracted_and_nothing_is_missing(tmp_path):
    result, df = _run(tmp_path)
    assert len(df) == 72 and result.rows_needs_review == 0
    assert result.extraction_status == "COMPLETE" and result.expected_rows == 72
    assert set(PREVIOUSLY_MISSING) <= set(df["Midas Code"])
    assert df.groupby("page").size().to_dict() == {"1": 18, "2": 18, "3": 17, "4": 19}


def test_field_level_quality(tmp_path):
    _, df = _run(tmp_path)
    assert df["Midas Code"].str.match(r"^M\d{6}$").all() and not df["Midas Code"].duplicated().any()
    assert (df["Product Description"] != "").all() and (df["Consumer Deal"] != "").all()
    everything = " ".join(df.astype(str).values.ravel())
    assert "$" not in everything and "\\n" not in everything
    assert not df["Consumer Deal"].str.contains(r"\bPOR\b").any()
    for midas, deal in EXPECTED_DEALS.items():
        assert df.loc[df["Midas Code"] == midas, "Consumer Deal"].iloc[0] == deal, midas


def test_every_automatic_correction_is_audited(tmp_path):
    result, df = _run(tmp_path)
    audit = pd.read_csv(result.audit_csv_path, dtype=str, keep_default_na=False)
    corrected = audit["ocr_value_or_note"].str.contains("corrected by document profile").sum()
    assert corrected > 0 and df["verified_by"].str.contains("profile-correction").sum() > 0


def test_without_corrections_rows_are_flagged_not_lost(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.table_normalizer.PROFILE_CORRECTIONS", False)
    result, df = _run(tmp_path)
    assert result.rows_needs_review > 0                       # $ / POR rows await review
    assert len(df) + result.rows_needs_review == 72            # none vanished
    assert result.extraction_status == "NEEDS_REVIEW"
