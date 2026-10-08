"""
Application Settings

Central place for all configurable values.

Changing CPU -> GPU or Development -> Production
should only require editing this file.
"""

import os
from pathlib import Path

# ==========================================================
# Project Paths
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

STORAGE_DIR = PROJECT_ROOT / "storage"

INPUT_DIR = STORAGE_DIR / "input"
PAGES_DIR = STORAGE_DIR / "pages"
JSON_DIR = STORAGE_DIR / "json"
OUTPUT_DIR = STORAGE_DIR / "output"
TMP_DIR = STORAGE_DIR / "tmp"  # scratch space for ground-truth uploads etc.

# ==========================================================
# PDF Settings
# ==========================================================

PDF_DPI = 300

# None = process every page
MAX_PAGES = None

# ==========================================================
# PaddleOCR Settings
# ==========================================================

OCR_PIPELINE_VERSION = "v1.6"

# cpu
# gpu:0
# gpu:1
# Overridable via the OCR_DEVICE env var so the same image can run
# CPU locally and GPU in the Docker deployment without code changes.
OCR_DEVICE = os.getenv("OCR_DEVICE", "cpu")

# ----------------------------------------------------------
# VLM decoding controls (error #15)
#
# Previously predict() was called with only the image path, so the
# model ran with its defaults: unbounded sampling freedom, no repetition
# control, and an automatic pixel budget that may downscale a dense
# 300-DPI table page until "F" and "P" look alike.
#
# These values are passed to every PaddleOCR-VL call.  They can be
# overridden per environment without code changes.
# ----------------------------------------------------------

def _opt(name: str, cast):
    """Read an optional env setting; unset or empty means 'use the library default'."""
    raw = os.getenv(name, "").strip()
    return cast(raw) if raw else None


# The LOCAL PaddleOCR-VL backend (what you run with `paddlepaddle`) ignores
# `temperature`, `repetition_penalty` and `top_p` and prints a warning for
# each (it already decodes greedily).  They only take effect on a remote
# vLLM / server backend, so they are OFF by default and opt-in via env vars.
VLM_TEMPERATURE = _opt("VLM_TEMPERATURE", float)
VLM_REPETITION_PENALTY = _opt("VLM_REPETITION_PENALTY", float)

# Honoured by the local backend.  A table with ~25 rows x 12 columns of HTML
# needs many tokens; too low silently truncates it (missing tail rows).
VLM_MAX_NEW_TOKENS = int(os.getenv("VLM_MAX_NEW_TOKENS", "8192"))

# Pixel budget for the vision encoder (each 28x28 patch = 1 token).  Left at
# the library default unless set: raising it improves small-glyph accuracy
# but multiplies memory use and run time, which can crash a laptop/CPU run.
# Try VLM_MAX_PIXELS=4014080 (5120*28*28) only on a machine with spare memory.
VLM_MIN_PIXELS = _opt("VLM_MIN_PIXELS", int)
VLM_MAX_PIXELS = _opt("VLM_MAX_PIXELS", int)

# ----------------------------------------------------------
# Layout detection (error #14)
# ----------------------------------------------------------

# Do NOT rely on layout_nms=False as a global fix.  Leave NMS at the
# library default (True) and instead let the geometry pass (see
# scripts/geometry_pass.py) detect tables the layout model missed or split,
# by checking every Midas code on the page is covered by some table.
LAYOUT_NMS = os.getenv("LAYOUT_NMS", "default")   # "default" | "true" | "false"
LAYOUT_THRESHOLD = float(os.getenv("LAYOUT_THRESHOLD", "0.3"))

# ----------------------------------------------------------
# Verification / geometry pass (errors #5-#7, #10-#13)
# ----------------------------------------------------------

# Where the per-page geometry sidecar files are written (inside the
# document's JSON folder, so they travel with the OCR JSON).
GEOMETRY_SUBDIR = "_geometry"

# Extra margin (pixels @ PDF_DPI) added around every layout-detected table
# box when collecting words.  Layout boxes are routinely a few pixels tight
# and clip the first/last row.
TABLE_BBOX_MARGIN_PX = 24

# Resolution used to re-render a barcode / small cell from the PDF for
# decoding and second-pass OCR.
CROP_DPI = 600

# A PDF page whose text layer holds fewer words than this is treated as a
# scan (no usable text layer) and the OCR-based second pass is used instead.
MIN_TEXT_LAYER_WORDS = 25

# Fraction of a Leaflet cell that must differ from its background before an
# icon is reported as present.
LEAFLET_INK_THRESHOLD = float(os.getenv("LEAFLET_INK_THRESHOLD", "0.02"))

# If True, a grammar-constrained fix (e.g. "2 POR £5" -> "2 FOR £5") may be
# applied when the second pass is unavailable.  Default False: the system
# prefers a needs-review row over a guessed value.
ALLOW_GRAMMAR_AUTOFIX = os.getenv("ALLOW_GRAMMAR_AUTOFIX", "0") == "1"

# ==========================================================
# CPU Settings
# ==========================================================

CPU_THREADS = int(os.getenv("CPU_THREADS", "8"))

ENABLE_MKLDNN = True

MKLDNN_CACHE_CAPACITY = 10

# ==========================================================
# GPU Settings
# ==========================================================

ENABLE_HPI = False

USE_TENSORRT = False

PRECISION = os.getenv("OCR_PRECISION", "fp32")
# fp32
# fp16

# ==========================================================
# Output Settings
# ==========================================================

SAVE_JSON = True

SAVE_MARKDOWN = False

SAVE_HTML = False

SAVE_EXCEL = False

# ==========================================================
# Excel Settings
# ==========================================================

EXCEL_SHEET_NAME = "Tables"

INCLUDE_PAGE_NUMBER = True

INCLUDE_DOCUMENT_NAME = True

INCLUDE_TABLE_TITLE = True

# ==========================================================
# Logging
# ==========================================================

LOG_LEVEL = "INFO"

# ==========================================================
# API
# ==========================================================

API_HOST = "0.0.0.0"

API_PORT = 8000

API_TITLE = "Table Data Extraction API"

API_DESCRIPTION = "PaddleOCR-VL-powered extraction of tables from PDF documents."

API_VERSION = "1.1.0"

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB

# ==========================================================
# Database (extraction history / UI)
#
# Overridable via DB_PATH so the Docker deployment can point it at a
# mounted volume and keep history across container restarts.
# ==========================================================

DB_PATH = Path(os.getenv("DB_PATH", PROJECT_ROOT / "table_extractor.db"))

# ==========================================================
# Default seed user (UI login)
#
# Overridable via env vars so a real password never has to live in git.
# ==========================================================

DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")

DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")