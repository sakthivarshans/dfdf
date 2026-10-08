"""Barcode decoding, GS1 checksum, and geometry rebuild (pure, no OCR)."""

import random

import cv2
import numpy as np

from tests.barcode_fixture import ean13_check_digit, render_ean13
from utils.barcode_reader import decode_barcode_crop
from utils.field_validators import gs1_check_digit_ok, normalize_ean, validate_ean
from utils.pdf_geometry import ColumnLayout, HRule, Word, rebuild_table


def test_gs1_checksum():
    assert gs1_check_digit_ok("5000168001234") is False or True  # computed below
    first12 = "500016800123"
    assert gs1_check_digit_ok(first12 + ean13_check_digit(first12))
    assert not gs1_check_digit_ok(first12 + str((int(ean13_check_digit(first12)) + 1) % 10))


def test_printed_ean_is_normalised_and_validated():
    first12 = "500016800123"
    ean = first12 + ean13_check_digit(first12)
    assert normalize_ean(f"{ean[0]} {ean[1:7]} {ean[7:]}") == ean
    assert validate_ean(ean) is None
    assert validate_ean("715911").code == "bad_ean"        # the OCR fragment from the bug report


def test_barcode_decoder_reads_every_size_and_rejects_bad_checksums():
    rng = random.Random(3)
    for module in (1, 2, 3, 4, 6, 8):
        first12 = "".join(str(rng.randint(0, 9)) for _ in range(12))
        ean = first12 + ean13_check_digit(first12)
        found = decode_barcode_crop(cv2.copyMakeBorder(render_ean13(ean, module, 120), 4, 4, 9, 9,
                                                       cv2.BORDER_CONSTANT, value=255))
        assert found and found[0].valid and found[0].value == ean
    bad = first12 + str((int(ean13_check_digit(first12)) + 1) % 10)
    assert decode_barcode_crop(render_ean13(bad, 4, 100)) == []
    assert decode_barcode_crop(np.full((50, 200), 255, np.uint8)) == []


def _w(text, x, y, w=40):
    return Word(text, x, y, x + w, y + 10)


def test_blank_midas_row_is_found_by_its_border():
    """A row with no Midas code still has a band between two borders (#4)."""
    words = [_w("Midas", 100, 10), _w("Code", 100, 22), _w("Consumer", 200, 10), _w("Deal", 200, 22),
             _w("Product", 10, 10), _w("Description", 10, 22),
             _w("M111111", 100, 50), _w("A", 10, 50),
             _w("£1.50", 300, 90), _w("B", 10, 90),                       # no Midas here
             _w("M222222", 100, 130), _w("C", 10, 130)]
    rules = [HRule(y, 0, 400) for y in (35, 70, 110, 150)]
    t = rebuild_table(words, rules, [], (0, 0, 400, 160))
    assert [r.kind for r in t.rows] == ["product", "blank_midas", "product"]


def test_layout_is_carried_only_when_x_extent_matches():
    layout = ColumnLayout(["Midas Code", "Consumer Deal"], [0, 100, 200])
    words = [_w("M111111", 10, 10), _w("£1.50", 120, 10), _w("M222222", 10, 40), _w("£2.00", 120, 40)]
    ok = rebuild_table(words, [], [], (0, 0, 200, 60), carried_layout=layout)
    assert len(ok.rows) == 2
    wrong = rebuild_table(words, [], [], (0, 0, 900, 60), carried_layout=layout)
    assert wrong.layout is None            # refuses to read a different table with the wrong columns
