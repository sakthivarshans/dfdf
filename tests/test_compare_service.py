"""
Mapped comparison — the path the UI uses once the user has picked a unique
key and the fields to compare. Pure dataframe logic: no OCR, no database.
"""

import pandas as pd
import pytest

from services.compare_service import (
    STATUS_MISMATCH,
    STATUS_MISSING,
    STATUS_SAME,
    compare_mapped,
    values_equal,
)


def extracted():
    return pd.DataFrame({
        "Midas Code": ["A1", "A2", "A3"],
        "Consumer Deal": ["2 for £5", "BOGOF", "3 for £10"],
        "Price": ["1.50", "2.00", "3.00"],
    })


def existing():
    return pd.DataFrame({
        "Code": ["A1", "A3", "A9"],
        "Deal": ["2 for £5", "3 for £12", "Half price"],
        "RRP": ["1.5", "3.00", "9.99"],
    })


KEY = {"extracted": "Midas Code", "existing": "Code"}
FIELDS = [
    {"extracted": "Consumer Deal", "existing": "Deal"},
    {"extracted": "Price", "existing": "RRP"},
]
ONE_FIELD = [{"extracted": "Price", "existing": "RRP"}]


def summary_of(*args):
    return compare_mapped(*args).summary


# ── pairing ──────────────────────────────────────────────────────────────

def test_rows_pair_on_the_key_not_on_row_order():
    # A3 is row 3 on the left and row 2 on the right — a positional
    # comparison would call both rows wrong.
    result = summary_of(extracted(), existing(), KEY, FIELDS)

    assert result["rows_paired"] == 2            # A1 and A3
    assert result["only_in_existing_rows"] == 1  # A9
    assert result["only_in_extracted_rows"] == 1  # A2


def test_every_existing_row_is_accounted_for():
    result = summary_of(extracted(), existing(), KEY, FIELDS)
    assert result["rows_paired"] + result["only_in_existing_rows"] == result["total_rows_ground"]


def test_only_the_mapped_fields_decide_a_row():
    result = summary_of(extracted(), existing(), KEY, FIELDS)

    # A1 agrees on both fields; A3's deal differs
    assert result["matching_rows"] == 1
    assert result["mismatched_rows"] == 1
    assert [d["row"] for d in result["row_comparison"]] == ["A3"]
    assert result["row_comparison"][0]["differences"] == {
        "Consumer Deal → Deal": {"extracted": "3 for £10", "ground_truth": "3 for £12"}
    }


def test_key_matching_ignores_case_and_padding():
    left = pd.DataFrame({"Midas Code": ["  a1 "], "Price": ["1.50"]})
    right = pd.DataFrame({"Code": ["A1"], "RRP": ["1.50"]})

    result = summary_of(left, right, KEY, ONE_FIELD)
    assert result["rows_paired"] == 1
    assert result["matching_rows"] == 1


def test_duplicate_and_blank_keys_are_counted_not_guessed():
    left = pd.DataFrame({
        "Midas Code": ["A1", "A1", ""],
        "Price": ["1.00", "2.00", "3.00"],
    })
    right = pd.DataFrame({"Code": ["A1"], "RRP": ["1.00"]})

    result = summary_of(left, right, KEY, ONE_FIELD)
    assert result["duplicate_keys_extracted"] == 1
    assert result["blank_keys_extracted"] == 1
    assert result["rows_paired"] == 1
    # the first A1 wins, so the comparison is against 1.00 and matches
    assert result["matching_rows"] == 1
    # the blank-key row cannot be vouched for by the existing file
    assert result["only_in_extracted_rows"] == 1


def test_nothing_pairs_is_zero_not_a_crash():
    right = pd.DataFrame({"Code": ["Z9"], "Deal": ["x"], "RRP": ["1"]})
    result = summary_of(extracted(), right, KEY, FIELDS)

    assert result["rows_paired"] == 0
    assert result["accuracy_percent"] == 0.0
    assert result["only_in_extracted_rows"] == 3


# ── the two downloadable reports ─────────────────────────────────────────

def test_existing_report_keeps_every_row_and_column_it_was_given():
    result = compare_mapped(extracted(), existing(), KEY, FIELDS)
    report = result.existing_report

    assert len(report) == len(existing())
    assert list(existing().columns) == list(report.columns)[:3]
    assert report["Code"].tolist() == ["A1", "A3", "A9"]


def test_existing_report_states_the_verdict_per_row():
    result = compare_mapped(extracted(), existing(), KEY, FIELDS)
    report = result.existing_report

    assert report["Data in PDF"].tolist() == [True, True, False]
    assert report["Status"].tolist() == [STATUS_SAME, STATUS_MISMATCH, STATUS_MISSING]
    # what the PDF said, per compared field, blank where there was no row
    assert report["Deal in PDF"].tolist() == ["2 for £5", "3 for £10", ""]
    assert report["RRP in PDF"].tolist() == ["1.50", "3.00", ""]


def test_a_single_compared_field_is_just_value_in_pdf():
    report = compare_mapped(extracted(), existing(), KEY, ONE_FIELD).existing_report
    assert "Value in PDF" in report.columns
    assert report["Value in PDF"].tolist() == ["1.50", "3.00", ""]


def test_report_columns_never_overwrite_the_users_own():
    clashing = existing().rename(columns={"Deal": "Status"})
    fields = [{"extracted": "Consumer Deal", "existing": "Status"}]

    report = compare_mapped(extracted(), clashing, KEY, fields).existing_report
    assert report["Status"].tolist() == ["2 for £5", "3 for £12", "Half price"]  # untouched
    assert report["Status (comparison)"].tolist() == [STATUS_SAME, STATUS_MISMATCH, STATUS_MISSING]


def test_extracted_only_holds_the_pdf_rows_that_file_never_mentions():
    result = compare_mapped(extracted(), existing(), KEY, FIELDS)

    assert result.extracted_only["Midas Code"].tolist() == ["A2"]
    assert list(result.extracted_only.columns) == list(extracted().columns)
    assert len(result.extracted_only) == result.summary["only_in_extracted_rows"]


# ── input validation ─────────────────────────────────────────────────────

def test_a_column_missing_from_the_mapping_is_a_value_error():
    with pytest.raises(ValueError, match="Consumer Deal"):
        compare_mapped(
            existing().rename(columns={"Code": "Midas Code"}),
            existing(),
            KEY,
            [{"extracted": "Consumer Deal", "existing": "Deal"}],
        )


def test_no_fields_to_compare_is_a_value_error():
    with pytest.raises(ValueError, match="at least one field"):
        compare_mapped(extracted(), existing(), KEY, [])


def test_the_same_field_pair_twice_is_only_compared_once():
    result = summary_of(extracted(), existing(), KEY, FIELDS + [FIELDS[0]])

    assert len(result["compare_fields"]) == 2
    assert len(result["column_details"]) == 2
    assert all(d["accuracy"] <= 100.0 for d in result["column_details"])


@pytest.mark.parametrize("left,right,expected", [
    ("1", "1.0", True),
    ("1,000", "1000", True),
    ("£1.50", "1.50", True),
    (" Two for £5 ", "two for £5", True),
    ("", "nan", True),
    ("1.50", "1.51", False),
    ("BOGOF", "BOGOF x2", False),
])
def test_values_equal_handles_the_shapes_excel_and_csv_disagree_on(left, right, expected):
    assert values_equal(left, right) is expected
