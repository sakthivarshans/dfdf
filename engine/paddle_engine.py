"""
Paddle Engine
=============

The ONLY module that talks to PaddleOCR directly.

What changed, and why
---------------------
* **#15 - decoding is now controlled.**  ``predict()`` used to be called with
  nothing but the image path.  Every call now passes explicit, deterministic
  VLM parameters (temperature 0, a token budget big enough for a whole table,
  a mild repetition penalty, a pixel budget that does not downscale the page
  before recognition).  See ``config/settings.py`` for the reasoning behind
  each value.

* **#14 - ``layout_nms=False`` is no longer hard-coded.**  It was an
  experiment that fixed one page and could, on other pages, let overlapping
  detections through as *duplicate* tables.  The default is now the
  library's own default; the geometry pass (``scripts/geometry_pass.py``)
  repairs split / missed tables by checking every Midas code on the page is
  covered, which fixes the cause instead of one symptom.  The old behaviour
  is still available via ``LAYOUT_NMS=false``.

* **#13 - a second, independent read.**  ``read_crop_text`` re-reads a small
  image region (a single cell or a merged group) with layout analysis turned
  OFF and the plain-OCR prompt.  The model then sees one tight crop at
  larger scale instead of a whole page, so it cannot repeat the page-level
  mistake.  ``read_crop_words`` runs the classic detector+recogniser
  (a different network) to get word boxes for scanned PDFs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

import numpy as np

from paddleocr import PaddleOCRVL

from config.settings import (
    CPU_THREADS,
    ENABLE_HPI,
    ENABLE_MKLDNN,
    LAYOUT_NMS,
    LAYOUT_THRESHOLD,
    MKLDNN_CACHE_CAPACITY,
    OCR_DEVICE,
    OCR_PIPELINE_VERSION,
    PRECISION,
    USE_TENSORRT,
    VLM_MAX_NEW_TOKENS,
    VLM_MAX_PIXELS,
    VLM_MIN_PIXELS,
    VLM_REPETITION_PENALTY,
    VLM_TEMPERATURE,
)

_log = logging.getLogger(__name__)


def _vlm_params() -> dict:
    """
    The decoding parameters passed to EVERY PaddleOCR-VL call (#15).

    Only parameters that are set are passed.  The local backend already
    decodes greedily (deterministically); ``temperature`` and
    ``repetition_penalty`` matter only for a remote vLLM backend.
    """

    params = {
        "temperature": VLM_TEMPERATURE,
        "max_new_tokens": VLM_MAX_NEW_TOKENS,
        "repetition_penalty": VLM_REPETITION_PENALTY,
        "min_pixels": VLM_MIN_PIXELS,
        "max_pixels": VLM_MAX_PIXELS,
    }
    # Only pass what is actually set: an unsupported parameter makes the
    # local backend print a warning, and an oversized one can exhaust memory.
    return {k: v for k, v in params.items() if v is not None}


def _layout_nms_arg() -> dict:
    """``layout_nms`` constructor argument, or nothing for the library default (#14)."""

    mode = str(LAYOUT_NMS).lower()
    if mode in ("true", "1", "yes"):
        return {"layout_nms": True}
    if mode in ("false", "0", "no"):
        return {"layout_nms": False}
    return {}          # "default": do not override the library


def extract_result_text(result) -> str:
    """
    Pull the recognised text out of one PaddleOCR-VL result object.

    ``result.json`` is ``{"res": {...}}`` in memory but the same dict
    *without* the ``res`` wrapper once written by ``save_to_json`` - both
    shapes are accepted.
    """

    data = getattr(result, "json", None) or {}
    data = data.get("res", data) if isinstance(data, dict) else {}
    blocks = data.get("parsing_res_list", []) or []
    parts = [str(b.get("block_content", "")).strip() for b in blocks]
    return " ".join(p for p in parts if p)


class PaddleEngine:
    """
    Wrapper around PaddleOCR-VL (page understanding) and, lazily, classic
    PaddleOCR (word boxes for the second pass).
    """

    def __init__(self):

        print("Loading PaddleOCR-VL...")

        self.pipeline = PaddleOCRVL(
            pipeline_version=OCR_PIPELINE_VERSION,
            device=OCR_DEVICE,
            cpu_threads=CPU_THREADS,
            enable_mkldnn=ENABLE_MKLDNN,
            mkldnn_cache_capacity=MKLDNN_CACHE_CAPACITY,
            enable_hpi=ENABLE_HPI,
            use_tensorrt=USE_TENSORRT,
            precision=PRECISION,
            layout_threshold=LAYOUT_THRESHOLD,
            **_layout_nms_arg(),
        )

        # classic detector+recogniser, created on first use (it is only
        # needed for scanned PDFs, so digital PDFs never pay for loading it)
        self._classic = None

        print("PaddleOCR-VL Loaded Successfully.")

    # ------------------------------------------------------------------
    # Page-level prediction (pass 1)
    # ------------------------------------------------------------------

    def predict_image(self, image_path: str | Path, **overrides):
        """
        Process one page image with the controlled VLM parameters.

        ``overrides`` replace individual parameters for this call only.
        """

        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(image_path)

        params = {**_vlm_params(), **overrides}
        return self.pipeline.predict(str(image_path), **params)

    def predict_images(self, image_paths: List[str | Path], **overrides):
        """Process several page images with the controlled VLM parameters."""

        params = {**_vlm_params(), **overrides}
        return self.pipeline.predict([str(Path(p)) for p in image_paths], **params)

    # ------------------------------------------------------------------
    # Crop-level second pass (#13)
    # ------------------------------------------------------------------

    def read_crop_text(self, image: np.ndarray, *, max_new_tokens: int = 64) -> str:
        """
        Re-read ONE small region (a cell or a merged-cell group).

        Layout analysis is switched off, so the whole image is treated as a
        single text block and read with the plain-OCR prompt.  A tiny token
        budget is used on purpose: a cell holds a few words, and a short
        budget prevents the model from hallucinating extra text.
        """

        if image is None or image.size == 0:
            return ""
        if image.ndim == 2:                      # the VLM expects 3 channels
            image = np.stack([image] * 3, axis=-1)

        params = {**_vlm_params(), "max_new_tokens": max_new_tokens}
        results = self.pipeline.predict(
            image,
            use_layout_detection=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            prompt_label="ocr",
            **params,
        )
        return " ".join(extract_result_text(r) for r in results).strip()

    def read_crop_words(self, image: np.ndarray):
        """
        Word/line boxes from the classic PaddleOCR detector+recogniser.

        This is a DIFFERENT network from the VLM, so agreement between the
        two is real evidence and disagreement is a reliable alarm.  Used to
        rebuild geometry for scanned PDFs, which have no text layer.

        Returns a list of ``(text, x0, y0, x1, y1)`` in the crop's pixel space.
        """

        from paddleocr import PaddleOCR

        if self._classic is None:
            self._classic = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device=OCR_DEVICE,
            )

        if image.ndim == 2:
            image = np.stack([image] * 3, axis=-1)

        out = []
        for page in self._classic.predict(image):
            data = page.json.get("res", page.json) if isinstance(page.json, dict) else {}
            texts = data.get("rec_texts", []) or []
            boxes = data.get("rec_boxes", None)
            if boxes is None:
                boxes = [
                    [min(p[0] for p in poly), min(p[1] for p in poly),
                     max(p[0] for p in poly), max(p[1] for p in poly)]
                    for poly in data.get("rec_polys", [])
                ]
            for text, box in zip(texts, boxes):
                x0, y0, x1, y1 = (float(v) for v in box)
                out.append((str(text), x0, y0, x1, y1))
        return out


if __name__ == "__main__":

    print("=" * 80)
    print("Paddle Engine Manual Test")
    print("=" * 80)

    engine = PaddleEngine()

    image_path = "storage/pages/sample/page_006.png"

    results = engine.predict_image(image_path)

    for result in results:

        print("\nPrediction Successful!\n")

        result.print()

        result.save_to_json("storage/json/sample")

        print("\nJSON saved successfully.")

    print("\nManual test completed successfully.")
