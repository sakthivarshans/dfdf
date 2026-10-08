"""
Barcode Reader
==============

Dedicated barcode decoding for the *EAN Barcode* column (error #10).

Why OCR cannot do this
----------------------
A barcode is a pattern of bars, not text.  PaddleOCR-VL treats it as picture
artwork and, at best, reads a fragment of the human-readable digits printed
beneath it (``715911``) or invents characters.  No amount of prompt or
parameter tuning turns a VLM into a barcode scanner.

What this module does instead
-----------------------------
1.  Decode the bars themselves.  PRIMARY: a deterministic scan-line EAN
    decoder written for machine-rendered symbols (this file, section 1).
    FALLBACK: OpenCV's barcode detector and, if libzbar is installed,
    ``pyzbar``, tried over several pre-processed variants.
2.  Accept a result ONLY if it has a GS1-legal length and a correct GS1
    check digit.  A decode with a wrong check digit is discarded - a
    misread bar is far more likely than a mis-printed barcode.

No OCR model is involved; the result is therefore independent evidence that
can be compared with the digits printed under the bars.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from config.document_profile import ACTIVE_PROFILE
from utils.field_validators import gs1_check_digit_ok

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecodedBarcode:
    """A barcode read from pixels."""

    value: str          # digits only
    symbology: str      # "EAN_13", "EAN_8", "UPC_A", ...
    valid: bool         # GS1 length + check digit both correct


def _is_valid(digits: str) -> bool:
    return (
        digits.isdigit()
        and len(digits) in ACTIVE_PROFILE.barcode_lengths
        and gs1_check_digit_ok(digits)
    )


# ==========================================================================
# 1. Deterministic scan-line EAN/UPC decoder  (primary reader)
# ==========================================================================
#
# The barcodes in a PDF are machine-rendered: perfectly straight, sharp, and
# printed at a known symbology.  That makes a classic scan-line decoder both
# simpler and MORE reliable than a general "find a barcode anywhere in a
# photo" detector.  (Measured: OpenCV's detector failed on ~30% of clean
# synthetic EAN-13 symbols; this decoder reads all of them.)
#
# An EAN-13 symbol is 95 modules wide:
#     start guard (3) | 6 left digits (42) | centre guard (5) | 6 right digits (42) | end guard (3)
# Every digit is 7 modules wide and made of 4 alternating runs (bar/space).
# Left digits use the "L" or "G" pattern set (the choice of L/G per digit
# encodes the 13th, leading digit); right digits use "R".

# Bit patterns, '1' = bar, '0' = space (GS1 General Specifications, EAN-13)
EAN_L = ["0001101", "0011001", "0010011", "0111101", "0100011",
         "0110001", "0101111", "0111011", "0110111", "0001011"]
EAN_G = ["0100111", "0110011", "0011011", "0100001", "0011101",
         "0111001", "0000101", "0010001", "0001001", "0010111"]
EAN_R = ["1110010", "1100110", "1101100", "1000010", "1011100",
         "1001110", "1010000", "1000100", "1001000", "1110100"]
#: Which of L/G the six left digits use, indexed by the leading digit.
EAN_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
              "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def _bits_to_runs(bits: str) -> tuple[int, ...]:
    """'0001101' -> (3, 2, 1, 1): lengths of the alternating runs."""
    runs, count = [], 1
    for previous, current in zip(bits, bits[1:]):
        if current == previous:
            count += 1
        else:
            runs.append(count)
            count = 1
    runs.append(count)
    return tuple(runs)


# (run-length pattern, set name, digit) for all 30 digit encodings
_DIGIT_PATTERNS = [
    (_bits_to_runs(table[d]), name, d)
    for name, table in (("L", EAN_L), ("G", EAN_G), ("R", EAN_R))
    for d in range(10)
]


def _match_digit(widths: list[float], allowed: str) -> tuple[str, int, float] | None:
    """
    Classify four measured run widths as one digit.

    The widths are rescaled so they sum to 7 modules (making the result
    independent of pixel size) and compared with every legal pattern; the
    closest wins.  Returns (set name, digit, error) or None when nothing is
    close enough - a smudged digit is rejected, never guessed.
    """

    total = sum(widths)
    if total <= 0:
        return None
    scaled = [w * 7.0 / total for w in widths]
    best = None
    for pattern, name, digit in _DIGIT_PATTERNS:
        if name not in allowed:
            continue
        error = sum(abs(a - b) for a, b in zip(scaled, pattern))
        if best is None or error < best[2]:
            best = (name, digit, error)
    if best is None or best[2] > 1.6:        # tolerance: ~0.4 module per run
        return None
    return best


def _runs_of(row_is_bar: np.ndarray) -> list[tuple[bool, int]]:
    """Run-length encode a boolean scan-line into [(is_bar, length)]."""
    change = np.flatnonzero(np.diff(row_is_bar.astype(np.int8))) + 1
    edges = np.concatenate(([0], change, [row_is_bar.size]))
    return [(bool(row_is_bar[a]), int(b - a)) for a, b in zip(edges[:-1], edges[1:])]


def _decode_ean13_runs(runs: list[tuple[bool, int]]) -> str | None:
    """
    Find and decode an EAN-13 symbol inside one scan-line's runs.

    Tries every position as a possible start guard (bar-space-bar of equal
    width), so surrounding quiet-zone or border noise does not matter.
    """

    needed = 59                                    # 3 + 24 + 5 + 24 + 3 runs
    for start in range(len(runs) - needed + 1):
        if not runs[start][0]:
            continue                               # a symbol starts with a BAR
        window = [length for _, length in runs[start:start + needed]]

        guard = window[0:3]
        unit = sum(guard) / 3.0
        if max(guard) > 1.8 * unit or min(guard) < 0.5 * unit:
            continue                               # not a 1:1:1 start guard

        digits: list[int] = []
        parity = ""

        for half, offset, allowed in (("L", 3, "LG"), ("R", 32, "R")):
            for k in range(6):
                quad = window[offset + 4 * k: offset + 4 * k + 4]
                match = _match_digit(quad, allowed)
                if match is None:
                    break
                name, digit, _ = match
                digits.append(digit)
                if half == "L":
                    parity += name
            else:
                continue
            break
        else:
            lead = EAN_PARITY.index(parity) if parity in EAN_PARITY else None
            if lead is None:
                continue
            centre = window[27:32]
            if max(centre) > 1.8 * unit or min(centre) < 0.5 * unit:
                continue                           # centre guard not 1:1:1:1:1
            return str(lead) + "".join(map(str, digits))
    return None


def scanline_decode_ean(gray: np.ndarray) -> str | None:
    """
    Decode an EAN-13 (or UPC-A, which is EAN-13 with a leading 0) from a
    grayscale crop by scanning several horizontal lines.

    Several scan-lines and both reading directions are tried; the first
    result with a valid GS1 check digit is returned.  Rotated (vertical)
    symbols are handled by also trying the 90-degree-rotated crop.
    """

    import cv2

    if gray.ndim != 2 or gray.size == 0:
        return None

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    is_bar_image = binary < 128

    for image in (is_bar_image, np.rot90(is_bar_image)):
        height = image.shape[0]
        for fraction in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8):
            line = image[min(height - 1, int(height * fraction))]
            for oriented in (line, line[::-1]):    # left-to-right and upside-down
                value = _decode_ean13_runs(_runs_of(oriented))
                if value and gs1_check_digit_ok(value):
                    return value
    return None


# ==========================================================================
# 2. OpenCV / pyzbar fallback  (secondary reader)
# ==========================================================================

#: Bar ("module") widths, in pixels, to normalise a crop to before decoding,
#: best first.  Measured on synthetic EAN-13 symbols from 4 to 12 px/module:
#: OpenCV's detector is reliable at ~1.6 px/module and frequently FAILS at
#: 2-3 px, so blindly up-scaling a crop (the usual advice) makes it worse.
#: Several targets are tried because real renders add blur and rounding.
_TARGET_MODULE_PX = (1.6, 1.3, 1.9, 2.4, 1.0)

#: Fallback scale sweep used when the bar width cannot be estimated.
_FALLBACK_SCALES = (1.0, 0.75, 0.5, 0.35, 0.25, 1.5, 2.0)


def _estimate_module_width(gray: np.ndarray) -> float | None:
    """
    Estimate the width of the narrowest bar (one 'module') in pixels.

    Binarises the image, takes the middle scan-lines, and measures the
    run-lengths of dark/light pixels.  The narrowest runs are single
    modules.  Returns None when no bar-like structure is found.
    """

    import cv2

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    height = binary.shape[0]
    runs: list[int] = []
    for y in (height // 2, height // 2 - height // 6, height // 2 + height // 6):
        row = binary[max(0, min(height - 1, y))] > 127
        change = np.flatnonzero(np.diff(row.astype(np.int8))) + 1
        edges = np.concatenate(([0], change, [row.size]))
        runs.extend(np.diff(edges).tolist())
    runs = [r for r in runs if r >= 1]
    if len(runs) < 20:                      # a barcode has ~60 runs per scan-line
        return None
    smallest = sorted(runs)[: max(10, len(runs) // 3)]
    return float(np.median(smallest))


def _variants(gray: np.ndarray):
    """
    Yield scaled copies of a crop, most promising first.

    Every copy gets a white 'quiet zone' border: decoders need blank space on
    both sides of the bars, and a crop taken exactly on the cell border has
    none.
    """

    import cv2

    def pad(img):
        border = max(12, img.shape[1] // 8)
        return cv2.copyMakeBorder(img, border, border, border, border,
                                  cv2.BORDER_CONSTANT, value=255)

    def scaled(img, factor):
        if abs(factor - 1.0) < 1e-6:
            return img
        interp = cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC
        return cv2.resize(img, None, fx=factor, fy=factor, interpolation=interp)

    factors: list[float] = []
    module = _estimate_module_width(gray)
    if module:
        factors.extend(target / module for target in _TARGET_MODULE_PX)
    factors.extend(f for f in _FALLBACK_SCALES if f not in factors)

    for factor in factors:
        resized = scaled(gray, factor)
        yield pad(resized)
        # Otsu binarisation removes anti-aliasing noise from rendered PDFs
        _, binary = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield pad(binary)


def _decode_opencv(img: np.ndarray) -> list[tuple[str, str]]:
    """Run OpenCV's barcode detector on one image; return (value, type) pairs."""

    import cv2

    if not hasattr(cv2, "barcode"):
        return []   # opencv-python without contrib: caller falls back to pyzbar
    detector = cv2.barcode.BarcodeDetector()
    try:
        ok, infos, types, _ = detector.detectAndDecodeWithType(img)
    except cv2.error:
        return []
    if not ok:
        return []
    return [(i, str(t)) for i, t in zip(infos, types) if i]


def _decode_pyzbar(img: np.ndarray) -> list[tuple[str, str]]:
    """Optional fallback; needs the system library libzbar."""

    try:
        from pyzbar.pyzbar import decode
    except Exception:  # ImportError or missing shared library
        return []
    return [(d.data.decode("ascii", "ignore"), d.type) for d in decode(img)]


def decode_barcode_crop(image: np.ndarray) -> list[DecodedBarcode]:
    """
    Decode every barcode found in ``image`` (BGR, BGRA, or grayscale).

    Returns all candidates, valid ones first.  Callers should use only
    ``valid`` results.  An empty list means "could not read" - the caller
    must then flag the row for review rather than invent a value.
    """

    import cv2

    if image is None or image.size == 0:
        return []

    if image.ndim == 3:
        code = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        gray = cv2.cvtColor(image, code)
    else:
        gray = image

    found: dict[str, DecodedBarcode] = {}

    # Primary reader: deterministic scan-line decoder (see section 1)
    primary = scanline_decode_ean(gray)
    if primary:
        return [DecodedBarcode(primary, "EAN_13", _is_valid(primary))]

    # Secondary readers: only reached if the primary one found nothing
    for variant in _variants(gray):
        for value, symbology in _decode_opencv(variant) + _decode_pyzbar(variant):
            digits = "".join(ch for ch in value if ch.isdigit())
            if not digits or digits in found:
                continue
            found[digits] = DecodedBarcode(digits, symbology, _is_valid(digits))
        # Stop at the first VALID read - later variants only add risk.
        if any(b.valid for b in found.values()):
            break

    return sorted(found.values(), key=lambda b: not b.valid)


def best_valid_barcode(image: np.ndarray) -> DecodedBarcode | None:
    """Convenience: the first checksum-valid barcode in the crop, or None."""

    for candidate in decode_barcode_crop(image):
        if candidate.valid:
            return candidate
    return None
