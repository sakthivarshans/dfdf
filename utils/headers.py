"""
Header canonicalisation
=======================

Maps every header spelling the OCR (or the PDF text layer) can produce onto
one canonical column name.

This lives in its own tiny module - separate from ``table_normalizer`` - so
that both the OCR-HTML path (``table_normalizer``) and the PDF-geometry path
(``pdf_geometry``) can use exactly the same mapping without importing each
other (which would be a circular import).
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

#: Header spellings seen in the wild -> the one name we keep.  Any spelling
#: missing from here silently becomes its own column, so unmapped headers
#: are logged by ``normalize_col``.
CANONICAL: dict[str, str] = {
    "midas code": "Midas Code",
    "midascode": "Midas Code",
    "consumer deal": "Consumer Deal",
    "consumerdeal": "Consumer Deal",
    "product description": "Product Description",
    "productdescription": "Product Description",
    "case size": "Case Size",
    "casesize": "Case Size",
    "pack size": "Case Size",
    "packsize": "Case Size",
    "ean barcode": "EAN Barcode",
    "eanbarcode": "EAN Barcode",
    "barcode": "EAN Barcode",
    "prom wsp": "Prom WSP",
    "promwsp": "Prom WSP",
    "wsp": "Prom WSP",
    "std rsp": "Std RSP",
    "stdrsp": "Std RSP",
    "promo por": "Promo POR",
    "promopor": "Promo POR",
    "prom por": "Promo POR",
    "por": "Promo POR",
    "por%": "Promo POR",
    "leaflet": "Leaflet",
    "feature space": "Feature Space",
    "featurespace": "Feature Space",
    "image": "Image",
    "shelf": "Shelf",
}


def _camel_to_spaced(name: str) -> str:
    """'MidasCode' -> 'Midas Code'."""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", name)


def lookup_header(text: str) -> str | None:
    """
    Return the canonical name for a header string, or None if it is not a
    known header.  Never logs - use ``normalize_col`` when a fallback name
    is wanted.
    """

    name = re.sub(r"\s+", " ", str(text).strip())
    return CANONICAL.get(_camel_to_spaced(name).lower())


def normalize_col(name: str) -> str:
    """Map one raw header onto its canonical name (title-case fallback)."""

    name = re.sub(r"\s+", " ", str(name).strip())
    name = _camel_to_spaced(name)

    canonical = CANONICAL.get(name.lower())
    if canonical:
        return canonical

    # Not a spelling we know.  Title-case it so it is at least stable, and
    # say so - an unmapped variant of a real column forks a duplicate column
    # and is invisible otherwise.
    fallback = " ".join(w.capitalize() for w in name.split())
    if fallback:
        _log.info("unmapped column header %r -> %r", name, fallback)
    return fallback
