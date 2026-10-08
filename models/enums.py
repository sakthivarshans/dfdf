from enum import Enum


class ProcessingMode(str, Enum):
    """
    PDF processing mode.
    """

    ALL = "all"
    FIRST_N = "first_n"
    PAGE_RANGE = "page_range"