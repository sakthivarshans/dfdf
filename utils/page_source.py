"""
Page Source
===========

Everything that needs the *actual page* - the PDF text layer, the drawn table
borders, or pixels - lives here, behind one small class.  The geometry logic
(``pdf_geometry``) stays pure and never imports a PDF or image library.

A ``PageSource`` answers four questions about one page:

``words()``       Which words are on the page and where?  (exact, from the
                  PDF text layer - or [] for a scan)
``rules()``       Where are the table borders?  (from the PDF's vector
                  drawing commands, or detected from pixels for a scan)
``crop()``        Give me the pixels of this region, sharp.  (re-rendered
                  from the PDF at CROP_DPI when possible - NOT up-scaled from
                  the 300-DPI page PNG, which would only blur it)
``ink_ratio()``   How much of this cell is non-background?  (Leaflet icon)

Coordinates everywhere are **page-image pixels at ``dpi``** - the same space
as PaddleOCR's ``block_bbox`` - so results can be compared directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from config.settings import CROP_DPI, MIN_TEXT_LAYER_WORDS, PDF_DPI
from utils.pdf_geometry import HRule, VRule, Word

try:                                    # PyMuPDF >= 1.24 prefers `pymupdf`
    import pymupdf as fitz
except ImportError:                     # older installs only know `fitz`
    import fitz

_log = logging.getLogger(__name__)

#: A drawn line shorter than this (in PDF points) is a glyph/barcode bar, not
#: a table border.
_MIN_RULE_PT = 12.0


# --------------------------------------------------------------------------
# rule post-processing
# --------------------------------------------------------------------------


def merge_hrules(rules: list[HRule], y_tol: float = 1.5, gap: float = 3.0) -> list[HRule]:
    """
    Join collinear horizontal segments.  Tables are often drawn cell by cell,
    so one visual border arrives as many short segments; merging restores the
    full line while *keeping real gaps* (the missing border inside a merged
    cell stays a gap).
    """

    out: list[HRule] = []
    for r in sorted(rules, key=lambda r: (round(r.y / y_tol), r.x0)):
        if out and abs(out[-1].y - r.y) <= y_tol and r.x0 <= out[-1].x1 + gap:
            last = out[-1]
            out[-1] = HRule((last.y + r.y) / 2, last.x0, max(last.x1, r.x1))
        else:
            out.append(r)
    return out


def merge_vrules(rules: list[VRule], x_tol: float = 1.5, gap: float = 3.0) -> list[VRule]:
    """Vertical counterpart of ``merge_hrules``."""

    out: list[VRule] = []
    for r in sorted(rules, key=lambda r: (round(r.x / x_tol), r.y0)):
        if out and abs(out[-1].x - r.x) <= x_tol and r.y0 <= out[-1].y1 + gap:
            last = out[-1]
            out[-1] = VRule((last.x + r.x) / 2, last.y0, max(last.y1, r.y1))
        else:
            out.append(r)
    return out


# --------------------------------------------------------------------------
# PageSource
# --------------------------------------------------------------------------


class PageSource:
    """
    One page of one document.

    Parameters
    ----------
    pdf_path:
        The source PDF, or None if only the PNG is available (then the text
        layer and vector rules are unavailable and pixels are used instead).
    page_number:
        1-based page number in the PDF.
    png_path:
        The 300-DPI render the OCR was run on.
    dpi:
        Resolution of that render (pixels are ``points * dpi / 72``).
    """

    def __init__(self, pdf_path: str | Path | None, page_number: int,
                 png_path: str | Path | None = None, dpi: int = PDF_DPI):
        self.pdf_path = Path(pdf_path) if pdf_path else None
        self.page_number = page_number
        self.png_path = Path(png_path) if png_path else None
        self.dpi = dpi
        self.zoom = dpi / 72.0

        self._doc = None
        self._page = None
        self._words: list[Word] | None = None
        self._rules: tuple[list[HRule], list[VRule]] | None = None
        self._png = None

        if self.pdf_path and self.pdf_path.exists():
            self._doc = fitz.open(self.pdf_path)
            self._page = self._doc.load_page(page_number - 1)

    # ---- housekeeping ----------------------------------------------------

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- coordinate mapping ---------------------------------------------

    def _to_px(self, x: float, y: float) -> tuple[float, float]:
        """PDF point (as stored) -> page-image pixel."""
        point = fitz.Point(x, y) * self._page.rotation_matrix
        rect = self._page.rect
        return (point.x - rect.x0) * self.zoom, (point.y - rect.y0) * self.zoom

    # ---- 1. words --------------------------------------------------------

    def words(self) -> list[Word]:
        """
        Words from the PDF text layer, in image pixels.  Returns [] when the
        page has no usable text layer (a scan, or a layer full of garbage).
        """

        if self._words is not None:
            return self._words
        self._words = []
        if self._page is None:
            return self._words

        raw = self._page.get_text("words")
        words: list[Word] = []
        for x0, y0, x1, y1, text, *_ in raw:
            text = text.strip()
            if not text:
                continue
            ax, ay = self._to_px(x0, y0)
            bx, by = self._to_px(x1, y1)
            words.append(Word(text, min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)))

        if self._text_layer_is_usable(words):
            self._words = words
        else:
            _log.info("page %s: text layer unusable (%d words) - treated as a scan",
                      self.page_number, len(words))
        return self._words

    @staticmethod
    def _text_layer_is_usable(words: list[Word]) -> bool:
        """A layer is usable if it is big enough and not mojibake."""

        if len(words) < MIN_TEXT_LAYER_WORDS:
            return False
        garbage = sum(1 for w in words if "\ufffd" in w.text or "(cid:" in w.text)
        return garbage / len(words) < 0.02

    @property
    def has_text_layer(self) -> bool:
        return bool(self.words())

    # ---- 2. rules --------------------------------------------------------

    def rules(self) -> tuple[list[HRule], list[VRule]]:
        """Table borders: from vector drawings if the PDF has them, else from pixels."""

        if self._rules is not None:
            return self._rules

        h: list[HRule] = []
        v: list[VRule] = []
        if self._page is not None:
            h, v = self._vector_rules()
        if not h and not v:
            h, v = self._pixel_rules()

        self._rules = (merge_hrules(h), merge_vrules(v))
        return self._rules

    def _vector_rules(self) -> tuple[list[HRule], list[VRule]]:
        """Collect straight lines and thin rectangles from the PDF drawing commands."""

        h: list[HRule] = []
        v: list[VRule] = []
        min_len = _MIN_RULE_PT
        thin = 1.6          # pt: a rectangle thinner than this is a drawn line

        for drawing in self._page.get_drawings():
            for item in drawing["items"]:
                kind = item[0]
                if kind == "l":
                    p1, p2 = item[1], item[2]
                    if abs(p1.y - p2.y) < 0.6 and abs(p1.x - p2.x) >= min_len:
                        x0, y = self._to_px(min(p1.x, p2.x), p1.y)
                        x1, _ = self._to_px(max(p1.x, p2.x), p1.y)
                        h.append(HRule(y, x0, x1))
                    elif abs(p1.x - p2.x) < 0.6 and abs(p1.y - p2.y) >= min_len:
                        x, y0 = self._to_px(p1.x, min(p1.y, p2.y))
                        _, y1 = self._to_px(p1.x, max(p1.y, p2.y))
                        v.append(VRule(x, y0, y1))
                elif kind == "re":
                    rect = item[1]
                    if rect.height <= thin and rect.width >= min_len:
                        x0, y = self._to_px(rect.x0, (rect.y0 + rect.y1) / 2)
                        x1, _ = self._to_px(rect.x1, rect.y0)
                        h.append(HRule(y, x0, x1))
                    elif rect.width <= thin and rect.height >= min_len:
                        x, y0 = self._to_px((rect.x0 + rect.x1) / 2, rect.y0)
                        _, y1 = self._to_px(rect.x0, rect.y1)
                        v.append(VRule(x, y0, y1))
        return h, v

    def _page_gray(self) -> np.ndarray | None:
        """The page as a grayscale array at ``self.dpi``."""

        import cv2

        if self._png is not None:
            return self._png
        if self.png_path and self.png_path.exists():
            self._png = cv2.imread(str(self.png_path), cv2.IMREAD_GRAYSCALE)
        elif self._page is not None:
            self._png = self.crop(None, dpi=self.dpi)
        return self._png

    def _pixel_rules(self) -> tuple[list[HRule], list[VRule]]:
        """
        Detect table borders in pixels (scans).  Morphological opening with a
        long thin kernel keeps only long straight strokes; text is erased.
        """

        import cv2

        gray = self._page_gray()
        if gray is None:
            return [], []

        _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        min_px = int(_MIN_RULE_PT * self.zoom * 1.5)

        h: list[HRule] = []
        v: list[VRule] = []

        hk = cv2.getStructuringElement(cv2.MORPH_RECT, (min_px, 1))
        opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk)
        n, _, stats, _ = cv2.connectedComponentsWithStats(opened)
        for i in range(1, n):
            x, y, w, hh, _ = stats[i]
            h.append(HRule(y + hh / 2, float(x), float(x + w)))

        vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_px))
        opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk)
        n, _, stats, _ = cv2.connectedComponentsWithStats(opened)
        for i in range(1, n):
            x, y, w, hh, _ = stats[i]
            v.append(VRule(x + w / 2, float(y), float(y + hh)))
        return h, v

    # ---- 3. pixels -------------------------------------------------------

    def crop(self, bbox_px: tuple[float, float, float, float] | None, dpi: int | None = None) -> np.ndarray | None:
        """
        Grayscale pixels of ``bbox_px`` (page-image pixels).

        When the PDF is available the region is RE-RENDERED from the vector
        source at ``dpi`` (default ``CROP_DPI``).  Up-scaling the 300-DPI PNG
        instead would only interpolate: a barcode's 0.33 mm bars are ~4 px
        wide at 300 DPI, but ~8 px at 600 DPI - the difference between a
        decodable and an undecodable symbol.
        """

        import cv2

        dpi = dpi or CROP_DPI
        if self._page is not None:
            clip = None
            if bbox_px is not None:
                s = self.zoom
                clip = fitz.Rect(bbox_px[0] / s, bbox_px[1] / s, bbox_px[2] / s, bbox_px[3] / s)
                clip = clip & self._page.rect          # stay inside the page
                if clip.is_empty:
                    return None
            pix = self._page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip,
                                        colorspace=fitz.csGRAY, alpha=False)
            return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()

        # PNG only: cut the region from the stored render and up-scale it.
        page = self._page_gray()
        if page is None:
            return None
        if bbox_px is None:
            return page
        x0, y0, x1, y1 = (int(round(v)) for v in bbox_px)
        x0, y0 = max(0, x0), max(0, y0)
        part = page[y0:y1, x0:x1]
        if part.size == 0:
            return None
        scale = dpi / self.dpi
        if scale > 1:
            part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        return part

    # ---- 4. ink ----------------------------------------------------------

    def ink_ratio(self, bbox_px: tuple[float, float, float, float], inset_px: int = 4) -> float:
        """
        Fraction of the cell's pixels that differ clearly from its background.

        Used for the Leaflet column, whose content is an ICON, not text (#12).
        Text OCR cannot answer "is there an icon here?" reliably - the VLM
        either ignores it or hallucinates letters.  Pixels can.

        The region is shrunk by ``inset_px`` so the cell's own border lines
        are not counted as ink, and "background" is the cell's median gray,
        so a shaded cell is not mistaken for a filled one.
        """

        x0, y0, x1, y1 = bbox_px
        shrunk = (x0 + inset_px, y0 + inset_px, x1 - inset_px, y1 - inset_px)
        if shrunk[2] <= shrunk[0] or shrunk[3] <= shrunk[1]:
            return 0.0
        region = self.crop(shrunk, dpi=self.dpi)
        if region is None or region.size == 0:
            return 0.0
        background = float(np.median(region))
        return float(np.mean(np.abs(region.astype(np.int16) - background) > 40))
