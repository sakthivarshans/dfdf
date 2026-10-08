"""
Test helper: draw a real, scannable EAN-13 symbol with numpy.

Used by the tests (and by the synthetic trading-pack PDF) so the barcode
decoder is verified against genuine bar patterns, not mocked.
"""

import numpy as np

from utils.barcode_reader import EAN_G as _G, EAN_L as _L, EAN_PARITY as _PARITY, EAN_R as _R


def ean13_check_digit(first12: str) -> str:
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(first12))
    return str((10 - total % 10) % 10)


def ean13_modules(digits13: str) -> str:
    """Return the 95-module bar pattern ('1' = bar) for a 13-digit EAN."""
    parity = _PARITY[int(digits13[0])]
    bits = "101"
    for i, ch in enumerate(digits13[1:7]):
        bits += (_L if parity[i] == "L" else _G)[int(ch)]
    bits += "01010"
    for ch in digits13[7:]:
        bits += _R[int(ch)]
    return bits + "101"


def render_ean13(digits13: str, module_px: int = 3, height_px: int = 90) -> np.ndarray:
    """Grayscale image (white background, black bars, no quiet zone)."""
    bits = ean13_modules(digits13)
    row = np.repeat(np.array([0 if b == "1" else 255 for b in bits], np.uint8), module_px)
    return np.tile(row, (height_px, 1))
