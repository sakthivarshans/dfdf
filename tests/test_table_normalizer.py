"""
Tests for the strict normalizer (OCR-HTML path) - one group per reported issue.
Each test uses a fully VALID baseline row and breaks exactly one thing.
"""

import pandas as pd

from tests.barcode_fixture import ean13_check_digit
from utils.field_validators import clean_text, strip_image_artifacts, validate_deal, validate_cell
from utils.table_normalizer import clean_rows, normalize_table, merge
from utils.table_reader import ROW_MERGES, _promote_header_row, parse_html_tables

_E = "500016800123"
EAN = _E + ean13_check_digit(_E)
COLS = ["Image", "Product Description", "Case Size", "Midas Code", "EAN Barcode",
        "Prom WSP", "Std RSP", "Consumer Deal", "Promo POR", "Leaflet", "Feature Space", "Shelf"]


def good(midas="M123456", deal="2 FOR £5", **over):
    row = {"Image": "1", "Product Description": "AERO MILK", "Case Size": "15 x 90G",
           "Midas Code": midas, "EAN Barcode": EAN, "Prom WSP": "£10.00", "Std RSP": "£1.65",
           "Consumer Deal": deal, "Promo POR": "15.14%", "Leaflet": "Yes",
           "Feature Space": "", "Shelf": "2"}
    row.update(over)
    return row


def frame(rows, merges=None):
    df = pd.DataFrame(rows, columns=COLS).replace("", pd.NA)
    df["source_table"] = "t"
    if merges:
        df.attrs[ROW_MERGES] = merges
    return df


def run(rows, merges=None):
    return clean_rows(frame(rows, merges))


# ---- #1 FOR -> POR ---------------------------------------------------------
def test_POR_is_rejected_when_corrections_are_off(monkeypatch):
    monkeypatch.setattr("utils.table_normalizer.PROFILE_CORRECTIONS", False)
    kept, review, _ = run([good(deal="2 POR £5")])
    assert kept.empty and "FOR" in review.iloc[0]["reason"]


def test_POR_is_corrected_only_in_context_and_recorded():
    from utils.field_validators import apply_profile_corrections as fix
    assert fix("Consumer Deal", "ANY 2 POR £1.75") == ("ANY 2 FOR £1.75", "'ANY 2 POR £1.75' -> 'ANY 2 FOR £1.75'")
    assert fix("Consumer Deal", "2 POR")[1] is None           # corrected text still invalid: untouched
    assert fix("Product Description", "POR 2 £5")[1] is None   # other columns are never touched
    kept, review, stats = clean_rows(frame([good(deal="2 POR £5")]))
    assert kept.iloc[0]["Consumer Deal"] == "2 FOR £5" and "profile-correction" in kept.iloc[0]["verified_by"]


def test_valid_deal_wordings_pass():
    for deal in ["£6.00", "£3", "HALF PRICE: £3.27", "ANY 2 FOR £3.00", "2 FOR £5", "3 FOR 2", "50P"]:
        assert validate_deal(deal) is None, deal


# ---- #2 currency -----------------------------------------------------------
def test_dollar_and_euro_are_rejected_by_the_validator():
    for bad in ["$1.65", "€1.65"]:
        assert validate_cell("Std RSP", bad).code == "wrong_currency"


def test_dollar_is_corrected_only_before_a_digit_and_euro_never():
    from utils.field_validators import apply_profile_corrections as fix
    assert fix("Std RSP", "$1.65")[0] == "£1.65"
    assert fix("Std RSP", "€1.65")[1] is None
    assert fix("Std RSP", "$")[1] is None
    kept, review, stats = clean_rows(frame([good(**{"Std RSP": "$1.65"}), good("M222222", **{"Std RSP": "€1.65"})]))
    assert list(kept["Std RSP"]) == ["£1.65"] and len(review) == 1
    assert stats["needs_review"] == 1


# ---- #3 Midas --------------------------------------------------------------
def test_midas_must_be_m_plus_six_digits():
    kept, review, _ = run([good("M12345"), good("M1234567"), good("M123456")])
    assert list(kept["Midas Code"]) == ["M123456"] and len(review) == 2


# ---- #4 blank Midas --------------------------------------------------------
def test_blank_midas_product_row_is_not_dropped():
    kept, review, stats = run([good(), good(midas=None)])
    assert len(kept) == 1 and len(review) == 1
    assert "blank_midas_product_row" in review.iloc[0]["reason"]


def test_section_header_row_is_structure_not_data():
    banner = {c: None for c in COLS}
    banner["Product Description"] = "FRESH"
    kept, review, stats = run([good(), banner])
    assert len(kept) == 1 and review.empty and stats["blank_midas"] == 1


# ---- #5 continuation tables -------------------------------------------------
def test_headerless_continuation_inherits_columns():
    head = parse_html_tables("<table><tr>" + "".join(f"<td>{c}</td>" for c in COLS) + "</tr>"
                             "<tr>" + "".join(f"<td>{v}</td>" for v in good().values()) + "</tr></table>")[0]
    first = normalize_table(_promote_header_row(head), "t1")
    cont = parse_html_tables("<table><tr>" + "".join(f"<td>{v}</td>" for v in good("M222222").values()) + "</tr>"
                             "<tr>" + "".join(f"<td>{v}</td>" for v in good("M333333").values()) + "</tr></table>")[0]
    cont = _promote_header_row(cont)             # stays headerless (data-like first row)
    assert normalize_table(cont, "t2") is None                       # old behaviour: lost
    second = normalize_table(cont, "t2", first.attrs["raw_schema"])  # now: recovered
    assert second is not None and len(second) == 2


# ---- #7 deal group without its first row ------------------------------------
def test_group_recovers_when_first_row_is_missing_or_blank():
    rows = [good("M111111", deal=None), good("M222222", deal=None), good("M333333", deal="2 FOR £5")]
    kept, review, _ = run(rows, merges=[(0, 2, "Consumer Deal")])
    assert len(kept) == 3 and set(kept["Consumer Deal"]) == {"2 FOR £5"}


def test_group_value_is_taken_from_consumer_deal_not_the_marked_cell():
    rows = [good("M111111", deal=None, **{"Std RSP": "£1.65"}), good("M222222", deal=None)]
    rows[1]["Consumer Deal"] = "£2.00"
    kept, _, _ = run(rows, merges=[(0, 1, "Consumer Deal")])
    assert list(kept["Consumer Deal"]) == ["£2.00", "£2.00"]


def test_row_with_its_own_deal_is_not_overwritten():
    kept, _, _ = run([good("M111111", deal="£1.00"), good("M222222", deal="£2.00")],
                     merges=[(0, 1, "Consumer Deal")])
    assert list(kept["Consumer Deal"]) == ["£1.00", "£2.00"]


# ---- #8 shifted rows with valid Midas ----------------------------------------
def test_valid_midas_does_not_excuse_a_shifted_tail():
    shifted = good(**{"Std RSP": "2 FOR £5", "Consumer Deal": "15.14%", "Promo POR": "Yes", "Leaflet": None})
    kept, review, _ = run([shifted])
    assert kept.empty and len(review) == 1       # refused rather than guessed


def test_blank_cell_shift_is_repaired_only_when_fully_valid():
    row = good(**{"Leaflet": None})
    row["Feature Space"], row["Shelf"] = "ENDCAP", "3"
    shifted = dict(row)
    shifted["Promo POR"], shifted["Leaflet"] = None, row["Promo POR"]   # POR slid right by one
    kept, review, stats = run([shifted])
    assert len(kept) == 1 and kept.iloc[0]["Promo POR"] == "15.14%" and stats["repaired"] == 1


# ---- #9 / #11 text hygiene ---------------------------------------------------
def test_literal_backslash_n_is_removed():
    assert clean_text("AERO\\nMILK  PM165") == "AERO MILK PM165"
    cell = parse_html_tables("<table><tr><td>a\\nb</td><td>c</td></tr></table>")[0].iloc[0, 0]
    assert cell == "a b"


def test_image_artifacts_are_stripped():
    for junk in ["[f47]", "[°25]", "[F87] 2"]:
        assert "[" not in strip_image_artifacts(junk)
    kept, _, _ = run([good(Image="[f47]")])
    assert kept.iloc[0]["Image"] == ""


def test_merge_never_loses_a_row_silently():
    frames = [normalize_table(frame([good(), good("M12345"), good(midas=None)]).drop(columns="source_table"), "t")]
    merged, review, stats = merge(frames)
    assert len(merged) + len(review) == 3


# ---- regression found on the real Test-Sheet JSON ----------------------------
def test_blank_deal_covered_by_rowspan_is_not_mistaken_for_a_shifted_row():
    """
    Second row of a 2-row group has a blank Consumer Deal (covered by the
    rowspan).  It must receive the group's deal - NOT have its Std RSP slid
    into the blank cell (a plain price is a valid deal wording too).
    """
    rows = [good("M111111", deal="ANY 2 FOR £1.75", **{"Std RSP": "£1.25"}),
            good("M222222", deal=None, **{"Std RSP": "£1.25"})]
    kept, review, _ = run(rows, merges=[(0, 1, "Consumer Deal")])
    assert list(kept["Consumer Deal"]) == ["ANY 2 FOR £1.75"] * 2
    assert list(kept["Std RSP"]) == ["£1.25", "£1.25"]


def test_blank_deal_without_a_group_goes_to_review_not_to_a_guess():
    kept, review, _ = run([good("M222222", deal=None)])
    assert kept.empty and "blank_consumer_deal" in review.iloc[0]["issue_codes"]
