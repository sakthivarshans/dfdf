"""
Field Validators
================

Strict, document-profile-driven validation of every cell of a product row.

Design rules (these are what close errors #1, #2, #3, #8, #9, #11):

1.  **Exact grammars, not shapes.**  A value is valid only if it is a full
    member of the grammar in ``config/document_profile.py``.  There is no
    "looks roughly right".
2.  **Validate every field of every row** (#8).  The old code only looked at
    ``Midas Code``; a row whose Midas was fine but whose prices had slid one
    column to the right went straight into the CSV.
3.  **Never guess silently.**  A validator returns an ``Issue`` describing
    *what* is wrong and *why*; the caller decides whether a second pass can
    resolve it or the row must go to needs-review.
4.  **Pure functions.**  No I/O, no OCR, no pandas - so every rule is unit
    testable in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from config.document_profile import (
    ACTIVE_PROFILE,
    CURRENCY,
    FORBIDDEN_CURRENCY_SYMBOLS,
    DocumentProfile,
)

# --------------------------------------------------------------------------
# Text hygiene
# --------------------------------------------------------------------------

#: PaddleOCR-VL sometimes emits the TWO-CHARACTER sequence backslash + 'n'
#: (and \r, \t) inside cell text instead of a real newline (#9).  A regex
#: such as ``\s+`` does NOT match it because it is not whitespace at all -
#: it is a literal backslash followed by the letter n.
_LITERAL_ESCAPES = re.compile(r"\\[nrt]")

#: Bracketed junk the VLM invents when it "reads" a product thumbnail or the
#: shelf-position glyph in the Image column: ``[f47]``, ``[°25]``, ``[F87]``,
#: ``[ 3 ]`` (#11).  Real data in this document never uses square brackets.
_IMAGE_ARTIFACT = re.compile(r"\[[^\]\[]{0,8}\]")

_MULTI_SPACE = re.compile(r"\s+")


def is_blank(value) -> bool:
    """True for None, NaN, pandas NA, '' and whitespace-only strings."""

    if value is None:
        return True
    try:
        # pandas.NA / numpy.nan both fail ``value == value`` or raise
        if value != value:  # NaN
            return True
    except Exception:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "<na>"}


def normalize_whitespace(text: str) -> str:
    """
    Make every kind of 'whitespace' a single plain space (#9).

    Order matters: the literal ``\\n`` sequences must be replaced BEFORE the
    generic whitespace collapse, otherwise they survive into the CSV.  This
    function does NOT treat 'nan'/'None' specially - use ``clean_text`` for
    cell values that may be missing.
    """

    text = _LITERAL_ESCAPES.sub(" ", text)           # '\' 'n'  -> space
    text = text.replace("\u00a0", " ")               # non-breaking space
    text = text.replace("\u200b", "")                # zero-width space
    return _MULTI_SPACE.sub(" ", text).strip()


def clean_text(value) -> str:
    """Canonical cleanup of one cell value; missing values become ''."""

    if is_blank(value):
        return ""
    return normalize_whitespace(str(value))


def strip_image_artifacts(value) -> str:
    """Remove ``[f47]``-style bracket tokens and tidy what is left (#11)."""

    text = _IMAGE_ARTIFACT.sub(" ", clean_text(value))
    return _MULTI_SPACE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Issue object
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """One thing wrong with one cell."""

    column: str
    code: str       # stable machine-readable id, e.g. "bad_midas"
    message: str    # human-readable explanation for the review file
    value: str = ""

    def __str__(self) -> str:  # used when joining reasons into one cell
        return f"{self.column}: {self.message} (got {self.value!r})"


# --------------------------------------------------------------------------
# GS1 barcode checksum
# --------------------------------------------------------------------------


def gs1_check_digit_ok(digits: str) -> bool:
    """
    Validate a GS1 check digit (EAN-8, UPC-A, EAN-13, ITF-14 - all use the
    same mod-10 scheme).

    Starting from the digit *left of* the check digit and moving left, the
    weights alternate 3, 1, 3, 1 ...  The check digit makes the weighted sum
    a multiple of 10.
    """

    if not digits.isdigit() or len(digits) < 2:
        return False
    body, check = digits[:-1], int(digits[-1])
    total = 0
    for position, char in enumerate(reversed(body)):
        total += int(char) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def normalize_ean(value) -> str:
    """
    Reduce printed barcode text ('5 000168 001234', '5000168-001234') to its
    digits.  Returns '' if anything other than digits/spaces/hyphens is
    present - a barcode cell with letters in it is garbage, not a barcode.
    """

    text = clean_text(value)
    if not text:
        return ""
    if re.search(r"[^\d\s\-]", text):
        return ""
    return re.sub(r"[\s\-]", "", text)


# --------------------------------------------------------------------------
# Individual validators
# --------------------------------------------------------------------------


def validate_midas(value, profile: DocumentProfile = ACTIVE_PROFILE) -> Issue | None:
    """Midas code must be exactly ``M`` + 6 digits (#3)."""

    text = clean_text(value)
    if not text:
        return Issue("Midas Code", "blank_midas", "Midas Code is blank", "")
    if profile.midas_pattern.match(text):
        return None
    digits = re.sub(r"\D", "", text)
    if profile.midas_like_pattern.match(text):
        return Issue(
            "Midas Code", "bad_midas",
            f"Midas Code must be M + 6 digits, found {len(digits)} digits", text,
        )
    return Issue("Midas Code", "bad_midas", "not a Midas code", text)


def validate_ean(value, profile: DocumentProfile = ACTIVE_PROFILE) -> Issue | None:
    """Barcode digits must have a GS1-legal length AND a correct check digit (#10)."""

    text = clean_text(value)
    if not text:
        return Issue("EAN Barcode", "blank_ean", "EAN Barcode is blank", "")
    digits = normalize_ean(text)
    if not digits:
        return Issue("EAN Barcode", "bad_ean", "barcode contains non-digit characters", text)
    if len(digits) not in profile.barcode_lengths:
        return Issue("EAN Barcode", "bad_ean",
                     f"{len(digits)} digits is not a valid barcode length", text)
    if not gs1_check_digit_ok(digits):
        return Issue("EAN Barcode", "bad_ean", "GS1 check digit does not match", text)
    return None


def propose_deal_fix(value, profile: DocumentProfile = ACTIVE_PROFILE) -> str | None:
    """
    Grammar-constrained correction of a Consumer Deal.

    Replaces a known confusable keyword (``POR`` -> ``FOR``) word by word and
    returns the result ONLY if the corrected text is a valid deal.  Returns
    None when no such fix produces a valid deal.

    This is a *proposal*.  It is applied automatically only when
    ``settings.ALLOW_GRAMMAR_AUTOFIX`` is on; otherwise it is used to enrich
    the review message and to seed the second-pass OCR.
    """

    text = clean_text(value)
    if not text:
        return None
    words = text.split(" ")
    fixed = [profile.keyword_confusions.get(w.upper(), w) for w in words]
    candidate = " ".join(fixed)
    if candidate != text and any(p.match(candidate) for p in profile.deal_patterns):
        return candidate
    return None


def validate_deal(value, profile: DocumentProfile = ACTIVE_PROFILE) -> Issue | None:
    """
    Consumer Deal must match the deal grammar (#1).

    The old validator had NO rule for this column ("it would only add
    noise"), so a deal read as ``2 POR £5`` was accepted.  Because the OCR
    model repeats the same misreading every time you re-run it, the error
    looked systematic.  A grammar makes it detectable.
    """

    text = clean_text(value)
    if not text:
        return Issue("Consumer Deal", "blank_deal", "Consumer Deal is blank", "")

    # A '$' or '€' can never be right in a GBP document (#2)
    if any(s in text for s in FORBIDDEN_CURRENCY_SYMBOLS):
        return Issue("Consumer Deal", "wrong_currency",
                     "deal contains a non-GBP currency symbol", text)

    if any(p.match(text) for p in profile.deal_patterns):
        return None

    if re.search(r"\bPOR\b", text, re.I):
        hint = propose_deal_fix(text, profile)
        extra = f"; did you mean {hint!r}?" if hint else ""
        return Issue("Consumer Deal", "deal_keyword",
                     f"'POR' is not valid deal wording (OCR confusion of 'FOR'){extra}", text)

    return Issue("Consumer Deal", "bad_deal",
                 "deal text does not match any known deal wording", text)


def validate_cell(column: str, value, profile: DocumentProfile = ACTIVE_PROFILE) -> Issue | None:
    """
    Validate one cell against its column's grammar.

    Empty cells are NOT an error here (mandatory-ness is checked by
    ``validate_row``); a non-empty cell must be valid.
    """

    if column == "Midas Code":
        return validate_midas(value, profile)
    if column == "EAN Barcode":
        return None if is_blank(value) else validate_ean(value, profile)
    if column == "Consumer Deal":
        return None if is_blank(value) else validate_deal(value, profile)

    if is_blank(value):
        return None

    text = clean_text(value)

    # Currency columns: say *why* a '$' fails instead of a generic mismatch (#2)
    if column in ("Prom WSP", "Std RSP"):
        if any(s in text for s in FORBIDDEN_CURRENCY_SYMBOLS):
            return Issue(column, "wrong_currency",
                         f"only '{CURRENCY}' is valid in this document", text)

    pattern = profile.column_patterns.get(column)
    if pattern is not None and not pattern.match(text):
        return Issue(column, "bad_format", "value does not match the expected format", text)
    return None


def validate_row(row: Mapping, profile: DocumentProfile = ACTIVE_PROFILE) -> list[Issue]:
    """
    Validate EVERY field of one row (#8).

    Returns the full list of issues (empty list = row is clean).  Mandatory
    columns that are blank are reported too.
    """

    issues: list[Issue] = []

    for column in profile.mandatory_columns:
        if is_blank(row.get(column)):
            issues.append(Issue(column, f"blank_{column.lower().replace(' ', '_')}",
                                f"{column} is blank", ""))

    for column, value in row.items():
        if column in ("source_table", "repaired"):
            continue
        if is_blank(value):
            continue            # blanks already handled by the mandatory check
        issue = validate_cell(column, value, profile)
        if issue is not None:
            issues.append(issue)

    return issues
