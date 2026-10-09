"""
Document Profile
================

Everything that is *specific to this kind of document* (a UK/GBP supplier
"trading pack") lives here and nowhere else.

Why a separate module?
----------------------
Before this module existed, the knowledge "prices are £, Midas codes are M +
6 digits, a Consumer Deal looks like ..." was scattered through regexes in
``table_normalizer.py`` and was far too permissive (``^M\\d+$`` accepts any
number of digits, ``[£$€]`` accepts three currencies).  Permissive validators
are the root cause of errors #1, #2, #3 and #8: the OCR produced a wrong value
(``POR`` for ``FOR``, ``$`` for ``£``, ``M12345`` for ``M123456``) and the
validator *agreed* with it, so nothing ever triggered a second look.

The rule of this profile is the opposite: **a value is only accepted if it is
an exact member of the grammar below.**  Anything else is not "fixed by
guessing" - it is sent to a second verification pass, and if that cannot
settle it, to the needs-review file.

To support a different document family, copy this file, change the values,
and point ``ACTIVE_PROFILE`` (bottom of file) at the new one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Building blocks used by several patterns
# --------------------------------------------------------------------------

#: The only currency symbol this document family uses.  Anything else
#: ($, €, ...) is *never* valid here - it is an OCR confusion (error #2).
CURRENCY = "£"

#: Symbols that must be rejected/flagged rather than silently accepted.
FORBIDDEN_CURRENCY_SYMBOLS = ("$", "€", "¥", "₹")

# A GBP amount.  ``£5`` (no pence) is allowed because deal text such as
# "2 FOR £5" is printed that way; shelf prices always carry two decimals and
# use the stricter PRICE_2DP below.
_AMOUNT = r"£\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
_PRICE_2DP = r"£\d{1,3}(?:,\d{3})*\.\d{2}"


@dataclass(frozen=True)
class DocumentProfile:
    """Immutable bundle of all document-specific rules."""

    name: str

    # ---- identity of a product row ------------------------------------
    #: Exactly M + 6 digits (error #3).  ``^M\d+$`` used to accept M1, M12345
    #: and M1234567, i.e. any OCR dropout or hallucinated digit.
    midas_pattern: re.Pattern = re.compile(r"^M\d{6}$")

    #: Looser shape used ONLY to *find* a Midas-like token on the page (so a
    #: damaged code such as ``M12345`` is located and reported, instead of the
    #: row silently vanishing).  Never used to accept a value.
    midas_like_pattern: re.Pattern = re.compile(r"^M\d{3,9}$")

    # ---- per-column strict grammars -----------------------------------
    #: Column name -> compiled full-match regex.  Applied to EVERY non-empty
    #: cell of EVERY row (error #8), not just to the Midas column.
    column_patterns: dict = field(default_factory=lambda: {
        # e.g. 15 x 90G   6 x 4 x 440ML   12 x 1.5LTR   (decimals occur: 1.5LTR)
        "Case Size": re.compile(r"^\d+(?:\.\d+)?(?:\s*[xX]\s*\d+(?:\.\d+)?)*\s*(?:[A-Za-z]{1,4})?$"),
        "Prom WSP": re.compile(rf"^{_PRICE_2DP}$"),
        "Std RSP": re.compile(rf"^{_PRICE_2DP}$"),
        # percentage with optional sign / decimals, e.g. 15.14%  26.5%  -3%
        "Promo POR": re.compile(r"^-?\d{1,3}(?:\.\d{1,2})?%$"),
        "Shelf": re.compile(r"^\d{1,3}$"),
        "Image": re.compile(r"^\d{1,3}$"),
        # Leaflet is an icon: only the two literals below (or empty) are legal.
        # Anything else is a value from a neighbouring column that slid in.
        "Leaflet": re.compile(r"^(?:Yes|No)$"),
        # Free text, but a price or percentage here is a shifted cell.
        "Feature Space": re.compile(r"^(?!.*(?:£\s?\d|\d\s?%)).*$"),
    })

    #: Consumer Deal grammar (error #1).  A deal is only valid if the WHOLE
    #: text matches one of these.  ``FOR`` is part of the grammar, ``POR`` is
    #: not - so ``2 POR £5`` can never be accepted by accident.
    #: EXTEND THIS LIST when the supplier introduces a new deal wording; an
    #: unknown wording is sent to review (never guessed).
    deal_patterns: tuple = (
        re.compile(rf"^{_AMOUNT}$"),                                   # £6.00
        re.compile(rf"^HALF PRICE\s*:?\s*{_AMOUNT}$", re.I),           # HALF PRICE: £3.27
        re.compile(rf"^(?:ANY\s+|BUY\s+)?\d{{1,2}}\s+FOR\s+{_AMOUNT}$", re.I),  # ANY 2 FOR £3.00
        re.compile(r"^\d{1,2}\s+FOR\s+\d{1,2}$", re.I),                # 3 FOR 2
        re.compile(r"^BUY\s+\d{1,2}\s+GET\s+\d{1,2}\s+FREE$", re.I),   # BUY 1 GET 1 FREE
        re.compile(rf"^SAVE\s+{_AMOUNT}$", re.I),                      # SAVE £1.00
        re.compile(rf"^{_AMOUNT}\s+EACH$", re.I),                      # £1.50 EACH
        re.compile(r"^\d{1,3}P$", re.I),                                # 50P  (pence; present in Test-Sheet.pdf)
    )

    #: Words that are OCR confusions of a grammar keyword.  Used ONLY to give
    #: a precise error message and to propose a fix to the second pass; the
    #: value is never rewritten from this table alone unless
    #: ``ALLOW_GRAMMAR_AUTOFIX`` is switched on in settings.
    keyword_confusions: dict = field(default_factory=lambda: {
        "POR": "FOR", "F0R": "FOR", "FQR": "FOR", "EOR": "FOR", "FDR": "FOR",
    })

    # ---- barcode -------------------------------------------------------
    #: Valid GS1 lengths (EAN-8, UPC-A, EAN-13, ITF-14).
    barcode_lengths: tuple = (8, 12, 13, 14)

    # ---- which columns must be present / non-empty --------------------
    #: A row missing any of these goes to needs-review.
    #: Chosen from the fields the business treats as essential.  EAN Barcode is
    #: deliberately NOT here: in Test-Sheet.pdf the OCR returns it blank for every
    #: row (it is artwork), so making it mandatory sent 100% of rows to review.
    #: It is still validated (length + check digit) whenever a value exists.
    mandatory_columns: tuple = (
        "Midas Code", "Product Description", "Consumer Deal",
    )

    #: Columns where a ``$`` immediately before a digit may be corrected to the
    #: document currency (see ``field_validators.apply_profile_corrections``).
    currency_columns: tuple = ("Prom WSP", "Std RSP", "Consumer Deal")

    # ---- Leaflet column ------------------------------------------------
    #: The Leaflet column holds an ICON, not text (error #12).  We output
    #: these two literals depending on whether ink is detected in the cell.
    leaflet_true: str = "Yes"
    leaflet_false: str = "No"

    # ---- final CSV column order ---------------------------------------
    output_columns: tuple = (
        "Image", "Product Description", "Case Size", "Midas Code",
        "EAN Barcode", "Prom WSP", "Std RSP", "Consumer Deal", "Promo POR",
        "Leaflet", "Feature Space", "Shelf",
    )


ACTIVE_PROFILE = DocumentProfile(name="uk-gbp-trading-pack")
