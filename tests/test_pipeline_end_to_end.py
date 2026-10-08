"""
Whole pipeline (geometry pass + CSV) on a synthetic PDF with a deliberately
faulty OCR JSON.  The CSV must equal the ground truth CELL FOR CELL.
"""

import pandas as pd
import pytest

from scripts.geometry_pass import run_geometry_pass
from scripts.json_to_csv import convert_json_folder
from tests.e2e_helpers import compare, faulty_ocr_json, render_pages
from tests.synthetic_pack import build_pdf


def _run(tmp_path, geometry=True, **pdf_options):
    truth = build_pdf(tmp_path / "doc.pdf", **pdf_options)
    render_pages(tmp_path / "doc.pdf", tmp_path / "pages")
    faulty_ocr_json(truth, tmp_path / "json")
    if geometry:
        run_geometry_pass(tmp_path / "doc.pdf", tmp_path / "pages", tmp_path / "json")
    result = convert_json_folder(tmp_path / "json", tmp_path / "out" / "doc.csv")
    return truth, result


def test_csv_is_100_percent_accurate_despite_a_faulty_ocr(tmp_path):
    truth, result = _run(tmp_path)
    rows, cells, diffs = compare(result.merged_csv_path, truth)
    assert rows == len(truth) == 18 and cells == 18 * 12
    assert diffs == []
    assert result.rows_needs_review == 0 and result.midas_codes_on_pages == 18


def test_without_pdf_geometry_nothing_wrong_reaches_the_csv(tmp_path):
    truth, result = _run(tmp_path, geometry=False)
    _, _, diffs = compare(result.merged_csv_path, truth)
    assert diffs == []                       # wrong rows are in review, never in the CSV
    assert result.rows_needs_review > 0


@pytest.mark.parametrize("option", [{"draw_rules": False}, {"drop_deal_rules": True}])
def test_missing_borders_degrade_to_review_not_to_wrong_data(tmp_path, option):
    truth, result = _run(tmp_path, **option)
    _, _, diffs = compare(result.merged_csv_path, truth)
    assert diffs == []
    assert result.rows_extracted + result.rows_needs_review == 18   # nothing vanished


def test_a_row_with_no_midas_is_reported_not_lost(tmp_path):
    truth, result = _run(tmp_path, blank_midas_row=4)
    _, _, diffs = compare(result.merged_csv_path, truth)
    review = pd.read_csv(result.rejected_csv_path, dtype=str, keep_default_na=False)
    assert diffs == [] and result.rows_extracted == 17 and len(review) == 1
    assert "blank_midas" in review.iloc[0]["issue_codes"]
