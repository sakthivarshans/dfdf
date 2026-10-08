# """
# Table Utility Functions
# =======================

# Responsibilities
# ----------------
# 1. Read table blocks from PaddleOCR-VL JSON.
# 2. Parse table HTML.
# 3. Extract table geometry from layout_det_res.
# 4. Detect horizontally adjacent table fragments.
# 5. Expand HTML rowspan/colspan into logical grids.
# 6. Align table fragments safely.
# 7. Reconstruct horizontally split tables.
# 8. Convert reconstructed tables into pandas DataFrames.
# 9. Provide manual testing/debugging utilities.

# IMPORTANT
# ---------
# This module must NEVER silently drop rows or invent data.

# If two tables cannot be safely reconstructed, the function
# returns success=False.
# """
# from __future__ import annotations

# from dataclasses import dataclass
# from html.parser import HTMLParser
# from pathlib import Path
# from typing import Any

# import json
# import re

# import pandas as pd


# # ============================================================================
# # Data structures
# # ============================================================================


# @dataclass
# class TableGeometry:
#     """
#     Bounding box of a PaddleOCR table block.
#     """

#     x1: float
#     y1: float
#     x2: float
#     y2: float

#     @property
#     def width(self) -> float:
#         return self.x2 - self.x1

#     @property
#     def height(self) -> float:
#         return self.y2 - self.y1

#     @property
#     def center_x(self) -> float:
#         return (self.x1 + self.x2) / 2.0

#     @property
#     def center_y(self) -> float:
#         return (self.y1 + self.y2) / 2.0


# @dataclass
# class HTMLCell:
#     """
#     One HTML table cell.
#     """

#     text: str
#     rowspan: int = 1
#     colspan: int = 1


# @dataclass
# class HTMLRow:
#     """
#     One HTML table row.
#     """

#     cells: list[HTMLCell]


# @dataclass
# class ParsedHTMLTable:
#     """
#     Parsed HTML table.
#     """

#     rows: list[HTMLRow]


# # ============================================================================
# # General helpers
# # ============================================================================


# def normalize_text(value: Any) -> str:
#     """
#     Normalize cell/text content.
#     """

#     if value is None:
#         return ""

#     text = str(value)

#     text = re.sub(
#         r"\s+",
#         " ",
#         text,
#     )

#     return text.strip()


# def extract_html(table_block: dict) -> str:
#     """
#     Extract HTML from a PaddleOCR table block.
#     """

#     html = table_block.get(
#         "block_content"
#     )

#     if not html:
#         return ""

#     return str(html).strip()


# def get_table_bbox(
#     table_block: dict,
# ) -> TableGeometry | None:
#     """
#     Get table geometry from block_bbox.
#     """

#     bbox = table_block.get(
#         "block_bbox"
#     )

#     if not bbox or len(bbox) != 4:
#         return None

#     return TableGeometry(
#         x1=float(bbox[0]),
#         y1=float(bbox[1]),
#         x2=float(bbox[2]),
#         y2=float(bbox[3]),
#     )


# # ============================================================================
# # JSON helpers
# # ============================================================================


# def load_json(
#     json_file: str | Path,
# ) -> dict:
#     """
#     Load PaddleOCR JSON.
#     """

#     json_file = Path(json_file)

#     with open(
#         json_file,
#         "r",
#         encoding="utf-8",
#     ) as file:

#         return json.load(file)


# def get_table_blocks(
#     data: dict,
# ) -> list[dict]:
#     """
#     Return table blocks from parsing_res_list.
#     """

#     # Normal PaddleOCR saved JSON
#     blocks = data.get(
#         "parsing_res_list",
#         [],
#     )

#     if blocks:
#         return [
#             block
#             for block in blocks
#             if block.get(
#                 "block_label"
#             )
#             == "table"
#         ]

#     # Support result.json style:
#     # {"res": {...}}
#     res = data.get(
#         "res"
#     )

#     if isinstance(
#         res,
#         dict,
#     ):

#         blocks = res.get(
#             "parsing_res_list",
#             [],
#         )

#         return [
#             block
#             for block in blocks
#             if block.get(
#                 "block_label"
#             )
#             == "table"
#         ]

#     return []


# def get_layout_boxes(
#     data: dict,
# ) -> list[dict]:
#     """
#     Return layout detection boxes.

#     Supports:

#         data["layout_det_res"]["boxes"]

#     and:

#         data["res"]["layout_det_res"]["boxes"]
#     """

#     layout = data.get(
#         "layout_det_res"
#     )

#     if not layout:

#         res = data.get(
#             "res"
#         )

#         if isinstance(
#             res,
#             dict,
#         ):

#             layout = res.get(
#                 "layout_det_res"
#             )

#     if not isinstance(
#         layout,
#         dict,
#     ):

#         return []

#     return layout.get(
#         "boxes",
#         [],
#     )


# # ============================================================================
# # HTML parser
# # ============================================================================


# class _TableHTMLParser(
#     HTMLParser
# ):
#     """
#     Minimal HTML table parser.

#     We intentionally use Python's standard library so that
#     table_utils.py does not require BeautifulSoup.
#     """

#     def __init__(self):
#         super().__init__()

#         self.rows: list[
#             HTMLRow
#         ] = []

#         self.current_row: (
#             HTMLRow | None
#         ) = None

#         self.current_cell: (
#             HTMLCell | None
#         ) = None

#         self.in_table = False

#     def handle_starttag(
#         self,
#         tag: str,
#         attrs,
#     ):

#         tag = tag.lower()

#         if tag == "table":

#             self.in_table = True

#         elif (
#             tag == "tr"
#             and self.in_table
#         ):

#             self.current_row = HTMLRow(
#                 cells=[]
#             )

#         elif (
#             tag in ("td", "th")
#             and self.current_row is not None
#         ):

#             attributes = dict(
#                 attrs
#             )

#             rowspan = attributes.get(
#                 "rowspan",
#                 "1",
#             )

#             colspan = attributes.get(
#                 "colspan",
#                 "1",
#             )

#             try:
#                 rowspan = int(
#                     rowspan
#                 )
#             except (
#                 ValueError,
#                 TypeError,
#             ):
#                 rowspan = 1

#             try:
#                 colspan = int(
#                     colspan
#                 )
#             except (
#                 ValueError,
#                 TypeError,
#             ):
#                 colspan = 1

#             self.current_cell = HTMLCell(
#                 text="",
#                 rowspan=max(
#                     rowspan,
#                     1,
#                 ),
#                 colspan=max(
#                     colspan,
#                     1,
#                 ),
#             )

#     def handle_data(
#         self,
#         data: str,
#     ):

#         if self.current_cell is not None:

#             self.current_cell.text += data

#     def handle_endtag(
#         self,
#         tag: str,
#     ):

#         tag = tag.lower()

#         if (
#             tag in ("td", "th")
#             and self.current_cell is not None
#         ):

#             self.current_cell.text = (
#                 normalize_text(
#                     self.current_cell.text
#                 )
#             )

#             self.current_row.cells.append(
#                 self.current_cell
#             )

#             self.current_cell = None

#         elif (
#             tag == "tr"
#             and self.current_row is not None
#         ):

#             self.rows.append(
#                 self.current_row
#             )

#             self.current_row = None

#         elif tag == "table":

#             self.in_table = False


# def parse_html_table(
#     html: str,
# ) -> ParsedHTMLTable:
#     """
#     Parse HTML into rows/cells.
#     """

#     parser = _TableHTMLParser()

#     parser.feed(
#         html
#     )

#     return ParsedHTMLTable(
#         rows=parser.rows
#     )


# # ============================================================================
# # HTML grid reconstruction
# # ============================================================================

# def expand_table_to_grid(
#     parsed: ParsedHTMLTable,
# ) -> list[list[str]]:
#     """
#     Expand HTML rowspan/colspan into a rectangular grid.

#     IMPORTANT
#     ---------
#     Each HTML <tr> represents exactly one logical row.

#     rowspan only fills cells vertically across existing/future
#     logical rows. It must NOT create additional <tr> rows.

#     Example:

#         <tr>
#             <td rowspan="3">A</td>
#             <td>B</td>
#         </tr>

#         <tr>
#             <td>C</td>
#         </tr>

#         <tr>
#             <td>D</td>
#         </tr>

#     becomes:

#         A | B
#         A | C
#         A | D
#     """

#     grid: list[list[str | None]] = []

#     # ------------------------------------------------------------------
#     # Process every HTML <tr>
#     # ------------------------------------------------------------------

#     for row_index, html_row in enumerate(
#         parsed.rows
#     ):

#         # Ensure this logical row exists.
#         while len(grid) <= row_index:
#             grid.append([])

#         current_column = 0

#         for cell in html_row.cells:

#             # ----------------------------------------------------------
#             # Find the first free column in THIS HTML row.
#             #
#             # Some previous rows may have rowspan cells occupying
#             # this position.
#             # ----------------------------------------------------------

#             while True:

#                 # Make sure the column exists.
#                 while len(
#                     grid[row_index]
#                 ) <= current_column:

#                     grid[row_index].append(
#                         None
#                     )

#                 # Free cell → use it.
#                 if (
#                     grid[row_index][
#                         current_column
#                     ]
#                     is None
#                 ):
#                     break

#                 current_column += 1

#             text = normalize_text(
#                 cell.text
#             )

#             rowspan = max(
#                 int(cell.rowspan),
#                 1,
#             )

#             colspan = max(
#                 int(cell.colspan),
#                 1,
#             )

#             # ----------------------------------------------------------
#             # Make sure enough rows exist for rowspan.
#             # ----------------------------------------------------------

#             required_rows = (
#                 row_index
#                 + rowspan
#             )

#             while len(grid) < required_rows:

#                 grid.append([])

#             # ----------------------------------------------------------
#             # Make sure enough columns exist.
#             # ----------------------------------------------------------

#             required_columns = (
#                 current_column
#                 + colspan
#             )

#             for row in grid:

#                 while len(row) < required_columns:

#                     row.append(
#                         None
#                     )

#             # ----------------------------------------------------------
#             # Fill rowspan + colspan.
#             # ----------------------------------------------------------

#             for r in range(
#                 row_index,
#                 row_index + rowspan,
#             ):

#                 for c in range(
#                     current_column,
#                     current_column + colspan,
#                 ):

#                     # Only write into an empty cell.
#                     #
#                     # This prevents one rowspan from overwriting
#                     # another cell.
#                     if grid[r][c] is None:

#                         grid[r][c] = text

#             current_column += colspan

#     # ------------------------------------------------------------------
#     # Convert None → ""
#     # ------------------------------------------------------------------

#     normalized_grid = []

#     for row in grid:

#         normalized_grid.append(
#             [
#                 normalize_text(
#                     value
#                 )
#                 for value in row
#             ]
#         )

#     return normalized_grid

# def html_to_grid(
#     html: str,
# ) -> list[list[str]]:
#     """
#     Parse HTML and return expanded logical grid.
#     """

#     parsed = parse_html_table(
#         html
#     )

#     return expand_table_to_grid(
#         parsed
#     )


# def get_header_and_rows(
#     html: str,
# ) -> tuple[
#     list[str],
#     list[list[str]],
# ]:
#     """
#     Return:

#         header
#         data rows
#     """

#     grid = html_to_grid(
#         html
#     )

#     if not grid:

#         return [], []

#     header = grid[0]

#     rows = grid[1:]

#     return header, rows


# # ============================================================================
# # Table statistics
# # ============================================================================


# def table_statistics(
#     table_block: dict,
# ) -> dict:
#     """
#     Return useful table statistics.
#     """

#     html = extract_html(
#         table_block
#     )

#     geometry = get_table_bbox(
#         table_block
#     )

#     header, rows = (
#         get_header_and_rows(
#             html
#         )
#     )

#     return {
#         "geometry": geometry,
#         "raw_rows": len(
#             parse_html_table(
#                 html
#             ).rows
#         ),
#         "logical_rows": len(
#             rows
#         ),
#         "columns": len(
#             header
#         ),
#         "header": header,
#     }


# # ============================================================================
# # Fragment detection
# # ============================================================================


# def vertical_overlap_ratio(
#     a: TableGeometry,
#     b: TableGeometry,
# ) -> float:
#     """
#     Calculate vertical overlap ratio.
#     """

#     overlap = max(
#         0.0,
#         min(
#             a.y2,
#             b.y2,
#         )
#         - max(
#             a.y1,
#             b.y1,
#         ),
#     )

#     min_height = min(
#         a.height,
#         b.height,
#     )

#     if min_height <= 0:
#         return 0.0

#     return overlap / min_height


# def horizontal_gap(
#     left: TableGeometry,
#     right: TableGeometry,
# ) -> float:
#     """
#     Calculate horizontal distance.

#     Negative means overlap.
#     """

#     return (
#         right.x1
#         - left.x2
#     )


# def height_similarity(
#     a: TableGeometry,
#     b: TableGeometry,
# ) -> float:
#     """
#     Compare table heights.
#     """

#     if (
#         a.height <= 0
#         or b.height <= 0
#     ):

#         return 0.0

#     return min(
#         a.height,
#         b.height,
#     ) / max(
#         a.height,
#         b.height,
#     )


# def is_horizontal_fragment_pair(
#     left_table: dict,
#     right_table: dict,
#     min_vertical_overlap: float = 0.90,
#     max_horizontal_gap: float = 80.0,
#     min_height_similarity: float = 0.90,
# ) -> bool:
#     """
#     Determine whether two tables are likely horizontal
#     fragments of the same visual table.
#     """

#     left = get_table_bbox(
#         left_table
#     )

#     right = get_table_bbox(
#         right_table
#     )

#     if left is None or right is None:
#         return False

#     # Must be left → right.
#     if right.x1 < left.x1:
#         return False

#     overlap = vertical_overlap_ratio(
#         left,
#         right,
#     )

#     gap = horizontal_gap(
#         left,
#         right,
#     )

#     similarity = height_similarity(
#         left,
#         right,
#     )

#     return (
#         overlap
#         >= min_vertical_overlap
#         and gap
#         <= max_horizontal_gap
#         and similarity
#         >= min_height_similarity
#     )


# def find_fragment_pairs(
#     tables: list[dict],
# ) -> list[
#     tuple[dict, dict]
# ]:
#     """
#     Find horizontal table fragment candidates.
#     """

#     pairs = []

#     sorted_tables = sorted(
#         tables,
#         key=lambda table: (
#             (
#                 get_table_bbox(
#                     table
#                 ).y1
#                 if get_table_bbox(
#                     table
#                 )
#                 else float("inf")
#             ),
#             (
#                 get_table_bbox(
#                     table
#                 ).x1
#                 if get_table_bbox(
#                     table
#                 )
#                 else float("inf")
#             ),
#         ),
#     )

#     for i in range(
#         len(sorted_tables)
#     ):

#         for j in range(
#             i + 1,
#             len(sorted_tables),
#         ):

#             left = sorted_tables[i]
#             right = sorted_tables[j]

#             left_box = get_table_bbox(
#                 left
#             )

#             right_box = get_table_bbox(
#                 right
#             )

#             if (
#                 left_box is None
#                 or right_box is None
#             ):
#                 continue

#             # Ensure left-to-right order.
#             if (
#                 left_box.center_x
#                 > right_box.center_x
#             ):

#                 left, right = (
#                     right,
#                     left,
#                 )

#             if is_horizontal_fragment_pair(
#                 left,
#                 right,
#             ):

#                 pairs.append(
#                     (
#                         left,
#                         right,
#                     )
#                 )

#     return pairs


# # ============================================================================
# # Visual row anchors
# # ============================================================================


# def get_image_boxes(
#     data: dict,
# ) -> list[dict]:
#     """
#     Get image layout boxes from PaddleOCR.

#     In your document, these image boxes correspond to the
#     product images inside the left table.
#     """

#     boxes = get_layout_boxes(
#         data
#     )

#     return [
#         box
#         for box in boxes
#         if box.get(
#             "label"
#         )
#         == "image"
#     ]


# def extract_y_center(
#     coordinate: list | tuple,
# ) -> float | None:
#     """
#     Return Y center from [x1, y1, x2, y2].
#     """

#     if not coordinate:
#         return None

#     if len(coordinate) != 4:
#         return None

#     return (
#         float(coordinate[1])
#         + float(coordinate[3])
#     ) / 2.0


# def get_image_row_centers(
#     data: dict,
#     table_geometry: TableGeometry,
# ) -> list[float]:
#     """
#     Return Y centers of image boxes located inside
#     a table.

#     This is useful when the left side of a split table
#     contains product images.
#     """

#     centers = []

#     for image in get_image_boxes(
#         data
#     ):

#         coordinate = image.get(
#             "coordinate"
#         )

#         center_y = extract_y_center(
#             coordinate
#         )

#         if center_y is None:
#             continue

#         x1, y1, x2, y2 = (
#             map(
#                 float,
#                 coordinate,
#             )
#         )

#         center_x = (
#             x1 + x2
#         ) / 2.0

#         if (
#             table_geometry.x1
#             <= center_x
#             <= table_geometry.x2
#             and table_geometry.y1
#             <= center_y
#             <= table_geometry.y2
#         ):

#             centers.append(
#                 center_y
#             )

#     return sorted(
#         centers
#     )


# # ============================================================================
# # Row alignment
# # ============================================================================


# def calculate_row_centers(
#     geometry: TableGeometry,
#     row_count: int,
# ) -> list[float]:
#     """
#     Estimate row centers evenly across a table.

#     This is only a fallback when cell-level geometry
#     is unavailable.
#     """

#     if row_count <= 0:
#         return []

#     height = geometry.height

#     row_height = (
#         height / row_count
#     )

#     return [
#         geometry.y1
#         + (
#             index
#             + 0.5
#         )
#         * row_height
#         for index in range(
#             row_count
#         )
#     ]


# def nearest_row_index(
#     value: float,
#     centers: list[float],
# ) -> int:
#     """
#     Return nearest row index.
#     """

#     if not centers:
#         raise ValueError(
#             "No row centers available."
#         )

#     return min(
#         range(
#             len(centers)
#         ),
#         key=lambda index: abs(
#             centers[index]
#             - value
#         ),
#     )


# def build_row_mapping(
#     left_row_count: int,
#     right_row_count: int,
# ) -> list[list[int]]:
#     """
#     Build a proportional mapping from right rows
#     to left rows.

#     IMPORTANT
#     ---------
#     This is only used as a diagnostic fallback.

#     It does NOT pretend that a many-to-one mapping is
#     a valid reconstruction.

#     Example:

#         left  = 19
#         right = 24

#     returns groups such as:

#         left row 1 -> [right row 1]
#         left row 2 -> [right row 2]
#         ...
#         some left rows -> multiple right rows

#     Those multiple-row groups must be handled explicitly.
#     """

#     if (
#         left_row_count <= 0
#         or right_row_count <= 0
#     ):

#         return []

#     mapping = [
#         []
#         for _ in range(
#             left_row_count
#         )
#     ]

#     for right_index in range(
#         right_row_count
#     ):

#         normalized = (
#             right_index
#             + 0.5
#         ) / right_row_count

#         left_index = min(
#             int(
#                 normalized
#                 * left_row_count
#             ),
#             left_row_count - 1,
#         )

#         mapping[
#             left_index
#         ].append(
#             right_index
#         )

#     return mapping

# def align_fragment_rows(
#     left_table: dict,
#     right_table: dict,
#     data: dict,
# ) -> dict:
#     """
#     Align rows from two horizontally adjacent table fragments.

#     The two fragments may contain different numbers of HTML rows.
#     Alignment is therefore based on normalized vertical position,
#     not row number.

#     IMPORTANT:
#     This function does NOT merge the tables.
#     It only determines which right-side row corresponds to each
#     left-side row.
#     """

#     left_html = extract_html(left_table)
#     right_html = extract_html(right_table)

#     if not left_html:
#         return {
#             "success": False,
#             "method": "vertical_position",
#             "reason": "Left table HTML is missing.",
#         }

#     if not right_html:
#         return {
#             "success": False,
#             "method": "vertical_position",
#             "reason": "Right table HTML is missing.",
#         }

#     # ---------------------------------------------------------------
#     # Parse HTML
#     # ---------------------------------------------------------------

#     left_header, left_rows = get_header_and_rows(
#         left_html
#     )

#     right_header, right_rows = get_header_and_rows(
#         right_html
#     )

#     # ---------------------------------------------------------------
#     # Extract estimated/visual row positions
#     # ---------------------------------------------------------------

#     left_positions = extract_table_row_positions(
#         data,
#         left_table,
#     )

#     right_positions = extract_table_row_positions(
#         data,
#         right_table,
#     )

#     if len(left_positions) != len(left_rows):

#         return {
#             "success": False,
#             "method": "vertical_position",
#             "reason": (
#                 "Left row position count does not "
#                 "match left HTML row count."
#             ),
#             "left_rows": len(left_rows),
#             "left_positions": len(left_positions),
#         }

#     if len(right_positions) != len(right_rows):

#         return {
#             "success": False,
#             "method": "vertical_position",
#             "reason": (
#                 "Right row position count does not "
#                 "match right HTML row count."
#             ),
#             "right_rows": len(right_rows),
#             "right_positions": len(right_positions),
#         }

#     # ---------------------------------------------------------------
#     # Compare physical Y positions.
#     #
#     # Because the two tables may have slightly different top/bottom
#     # coordinates, normalize Y into the range [0, 1].
#     # ---------------------------------------------------------------

#     def normalize_y(
#         y: float,
#         table_top: float,
#         table_bottom: float,
#     ) -> float:

#         height = (
#             table_bottom
#             - table_top
#         )

#         if height <= 0:
#             return 0.0

#         return (
#             y - table_top
#         ) / height

#     left_geometry = get_table_bbox(
#         left_table
#     )

#     right_geometry = get_table_bbox(
#         right_table
#     )

#     if (
#         left_geometry is None
#         or right_geometry is None
#     ):

#         return {
#             "success": False,
#             "method": "vertical_position",
#             "reason": (
#                 "Table geometry unavailable."
#             ),
#         }

#     # ---------------------------------------------------------------
#     # Build normalized positions
#     # ---------------------------------------------------------------

#     left_normalized = []

#     for position in left_positions:

#         normalized = normalize_y(
#             position["y_center"],
#             left_geometry.y1,
#             left_geometry.y2,
#         )

#         left_normalized.append(
#             {
#                 **position,
#                 "normalized_y": normalized,
#             }
#         )

#     right_normalized = []

#     for position in right_positions:

#         normalized = normalize_y(
#             position["y_center"],
#             right_geometry.y1,
#             right_geometry.y2,
#         )

#         right_normalized.append(
#             {
#                 **position,
#                 "normalized_y": normalized,
#             }
#         )

#     # ---------------------------------------------------------------
#     # Dynamic programming alignment
#     #
#     # We do NOT simply choose the nearest row independently.
#     #
#     # This maintains monotonic ordering:
#     #
#     # left row 1 < left row 2 < left row 3
#     #
#     # must correspond to:
#     #
#     # right row A < right row B < right row C
#     #
#     # This is important when the right table contains additional
#     # rows.
#     # ---------------------------------------------------------------

#     n = len(left_normalized)
#     m = len(right_normalized)

#     if n == 0 or m == 0:

#         return {
#             "success": False,
#             "method": "vertical_position",
#             "reason": "No rows available.",
#         }

#     # ---------------------------------------------------------------
#     # Cost matrix
#     # ---------------------------------------------------------------

#     import math

#     skip_penalty = 0.20

#     dp = [
#         [
#             math.inf
#             for _ in range(m + 1)
#         ]
#         for _ in range(n + 1)
#     ]

#     parent = [
#         [
#             None
#             for _ in range(m + 1)
#         ]
#         for _ in range(n + 1)
#     ]

#     dp[0][0] = 0.0

#     # ---------------------------------------------------------------
#     # We must match every left row.
#     #
#     # Right rows may be skipped.
#     # ---------------------------------------------------------------

#     for i in range(n + 1):

#         for j in range(m + 1):

#             current = dp[i][j]

#             if math.isinf(current):
#                 continue

#             # -------------------------------------------------------
#             # Skip a right-side row.
#             # -------------------------------------------------------

#             if j < m:

#                 cost = (
#                     current
#                     + skip_penalty
#                 )

#                 if cost < dp[i][j + 1]:

#                     dp[i][j + 1] = cost

#                     parent[i][j + 1] = (
#                         i,
#                         j,
#                         "skip_right",
#                     )

#             # -------------------------------------------------------
#             # Match left row i with right row j.
#             # -------------------------------------------------------

#             if (
#                 i < n
#                 and j < m
#             ):

#                 distance = abs(
#                     left_normalized[i][
#                         "normalized_y"
#                     ]
#                     -
#                     right_normalized[j][
#                         "normalized_y"
#                     ]
#                 )

#                 cost = (
#                     current
#                     + distance
#                 )

#                 if cost < dp[i + 1][j + 1]:

#                     dp[i + 1][j + 1] = cost

#                     parent[i + 1][j + 1] = (
#                         i,
#                         j,
#                         "match",
#                     )

#     # ---------------------------------------------------------------
#     # Backtrack
#     # ---------------------------------------------------------------

#     i = n
#     j = m

#     matches = []

#     skipped_right = []

#     while i > 0 or j > 0:

#         state = parent[i][j]

#         if state is None:
#             break

#         previous_i, previous_j, action = state

#         if action == "match":

#             left_index = previous_i
#             right_index = previous_j

#             distance = abs(
#                 left_normalized[left_index][
#                     "normalized_y"
#                 ]
#                 -
#                 right_normalized[right_index][
#                     "normalized_y"
#                 ]
#             )

#             matches.append(
#                 {
#                     "left_row": left_index + 1,
#                     "right_row": right_index + 1,
#                     "left_y": left_normalized[
#                         left_index
#                     ]["y_center"],
#                     "right_y": right_normalized[
#                         right_index
#                     ]["y_center"],
#                     "left_normalized_y":
#                         left_normalized[
#                             left_index
#                         ]["normalized_y"],
#                     "right_normalized_y":
#                         right_normalized[
#                             right_index
#                         ]["normalized_y"],
#                     "distance": distance,
#                 }
#             )

#         elif action == "skip_right":

#             skipped_right.append(
#                 previous_j + 1
#             )

#         i = previous_i
#         j = previous_j

#     matches.reverse()
#     skipped_right.reverse()

#     # ---------------------------------------------------------------
#     # Validation
#     # ---------------------------------------------------------------

#     if len(matches) != n:

#         return {
#             "success": False,
#             "method": "vertical_position_dp",
#             "reason": (
#                 "Could not match every left row."
#             ),
#             "matches": matches,
#             "skipped_right_rows":
#                 skipped_right,
#         }

#     average_distance = (
#         sum(
#             match["distance"]
#             for match in matches
#         )
#         / len(matches)
#     )

#     maximum_distance = max(
#         match["distance"]
#         for match in matches
#     )

#     return {
#         "success": True,
#         "method": "vertical_position_dp",

#         "left_header": left_header,
#         "right_header": right_header,

#         "left_rows": left_rows,
#         "right_rows": right_rows,

#         "left_positions":
#             left_normalized,

#         "right_positions":
#             right_normalized,

#         "matches": matches,

#         "skipped_right_rows":
#             skipped_right,

#         "average_distance":
#             average_distance,

#         "maximum_distance":
#             maximum_distance,
#     }

# def validate_row_alignment(
#     alignment: dict,
#     max_average_distance: float = 0.03,
#     max_row_distance: float = 0.05,
#     min_match_ratio: float = 0.90,
# ) -> dict:
#     """
#     Validate the result produced by align_fragment_rows().

#     This function does NOT modify the alignment.

#     It checks:

#     1. Alignment succeeded.
#     2. Every left row has a mapping.
#     3. Row mapping is monotonic.
#     4. Average normalized Y distance is acceptable.
#     5. Maximum normalized Y distance is acceptable.
#     6. Match ratio is acceptable.

#     Returns
#     -------
#     dict
#         {
#             "valid": bool,
#             "confidence": str,
#             "reasons": list[str],
#             "metrics": dict
#         }
#     """

#     reasons = []

#     # ---------------------------------------------------------
#     # Basic alignment check
#     # ---------------------------------------------------------

#     if not alignment.get("success"):

#         return {
#             "valid": False,
#             "confidence": "low",
#             "reasons": [
#                 "Row alignment itself was unsuccessful."
#             ],
#             "metrics": {},
#         }

#     matches = alignment.get(
#         "matches",
#         [],
#     )

#     if not matches:

#         return {
#             "valid": False,
#             "confidence": "low",
#             "reasons": [
#                 "No row matches were produced."
#             ],
#             "metrics": {},
#         }

#     # ---------------------------------------------------------
#     # Extract metrics
#     # ---------------------------------------------------------

#     average_distance = alignment.get(
#         "average_distance"
#     )

#     maximum_distance = alignment.get(
#         "maximum_distance"
#     )

#     left_rows = alignment.get(
#         "left_rows",
#         []
#     )

#     right_rows = alignment.get(
#         "right_rows",
#         []
#     )

#     left_count = len(left_rows)

#     right_count = len(right_rows)

#     match_count = len(matches)

#     # ---------------------------------------------------------
#     # Match ratio
#     # ---------------------------------------------------------

#     if left_count > 0:

#         match_ratio = (
#             match_count
#             / left_count
#         )

#     else:

#         match_ratio = 0.0

#     # ---------------------------------------------------------
#     # 1. Check match ratio
#     # ---------------------------------------------------------

#     if match_ratio < min_match_ratio:

#         reasons.append(
#             (
#                 f"Match ratio too low: "
#                 f"{match_ratio:.3f}"
#             )
#         )

#     # ---------------------------------------------------------
#     # 2. Check average distance
#     # ---------------------------------------------------------

#     if (
#         average_distance is None
#         or average_distance
#         > max_average_distance
#     ):

#         reasons.append(
#             (
#                 "Average row alignment "
#                 f"distance too high: "
#                 f"{average_distance}"
#             )
#         )

#     # ---------------------------------------------------------
#     # 3. Check maximum distance
#     # ---------------------------------------------------------

#     if (
#         maximum_distance is None
#         or maximum_distance
#         > max_row_distance
#     ):

#         reasons.append(
#             (
#                 "Maximum row alignment "
#                 f"distance too high: "
#                 f"{maximum_distance}"
#             )
#         )

#     # ---------------------------------------------------------
#     # 4. Check monotonicity
#     #
#     # Left rows must map in increasing order
#     # to right rows.
#     # ---------------------------------------------------------

#     right_indices = [
#         match["right_row"]
#         for match in matches
#     ]

#     monotonic = all(
#         right_indices[i]
#         < right_indices[i + 1]
#         for i in range(
#             len(right_indices) - 1
#         )
#     )

#     if not monotonic:

#         reasons.append(
#             "Row mapping is not monotonic."
#         )

#     # ---------------------------------------------------------
#     # 5. Check duplicate mappings
#     # ---------------------------------------------------------

#     if len(
#         set(right_indices)
#     ) != len(right_indices):

#         reasons.append(
#             "Multiple left rows map "
#             "to the same right row."
#         )

#     # ---------------------------------------------------------
#     # 6. Calculate skipped rows
#     # ---------------------------------------------------------

#     skipped_rows = alignment.get(
#         "skipped_right_rows",
#         []
#     )

#     skipped_count = len(
#         skipped_rows
#     )

#     # ---------------------------------------------------------
#     # Determine validity
#     # ---------------------------------------------------------

#     valid = (
#         len(reasons) == 0
#         and monotonic
#     )

#     # ---------------------------------------------------------
#     # Confidence
#     # ---------------------------------------------------------

#     if not valid:

#         confidence = "low"

#     elif (
#         average_distance <= 0.01
#         and maximum_distance <= 0.025
#         and match_ratio >= 0.95
#     ):

#         confidence = "high"

#     elif (
#         average_distance <= 0.02
#         and maximum_distance <= 0.04
#         and match_ratio >= 0.90
#     ):

#         confidence = "medium"

#     else:

#         confidence = "low"

#     return {
#         "valid": valid,
#         "confidence": confidence,
#         "reasons": reasons,
#         "metrics": {
#             "left_rows": left_count,
#             "right_rows": right_count,
#             "matched_rows": match_count,
#             "match_ratio": match_ratio,
#             "average_distance":
#                 average_distance,
#             "maximum_distance":
#                 maximum_distance,
#             "skipped_right_rows":
#                 skipped_count,
#             "skipped_row_indices":
#                 skipped_rows,
#             "monotonic": monotonic,
#         },
#     }
# # ============================================================================
# # Fragment reconstruction
# # ============================================================================

# def reconstruct_fragment_pair(
#     left_table: dict,
#     right_table: dict,
#     data: dict,
#     minimum_confidence: str = "medium",
# ) -> dict:
#     """
#     Reconstruct two horizontally adjacent table fragments.

#     The function:
#         1. Aligns rows.
#         2. Validates alignment.
#         3. Merges the two tables only when validation passes.

#     Parameters
#     ----------
#     minimum_confidence:
#         Minimum accepted confidence.

#         "medium" or "high"

#     Returns
#     -------
#     dict
#     """

#     # ---------------------------------------------------------
#     # Step 1: Align rows
#     # ---------------------------------------------------------

#     alignment = align_fragment_rows(
#         left_table=left_table,
#         right_table=right_table,
#         data=data,
#     )

#     if not alignment.get("success"):

#         return {
#             "success": False,
#             "reconstructed": False,
#             "method": "vertical_position_dp",
#             "reason": alignment.get(
#                 "reason",
#                 "Row alignment failed.",
#             ),
#             "alignment": alignment,
#         }

#     # ---------------------------------------------------------
#     # Step 2: Validate alignment
#     # ---------------------------------------------------------

#     validation = validate_row_alignment(
#         alignment
#     )

#     if not validation.get("valid"):

#         return {
#             "success": False,
#             "reconstructed": False,
#             "method": "vertical_position_dp",
#             "reason": (
#                 "Row alignment validation failed."
#             ),
#             "alignment": alignment,
#             "validation": validation,
#         }

#     # ---------------------------------------------------------
#     # Step 3: Check confidence
#     # ---------------------------------------------------------

#     confidence = validation.get(
#         "confidence",
#         "low",
#     )

#     confidence_order = {
#         "low": 0,
#         "medium": 1,
#         "high": 2,
#     }

#     required_level = confidence_order.get(
#         minimum_confidence,
#         1,
#     )

#     current_level = confidence_order.get(
#         confidence,
#         0,
#     )

#     if current_level < required_level:

#         return {
#             "success": False,
#             "reconstructed": False,
#             "method": "vertical_position_dp",
#             "reason": (
#                 f"Alignment confidence "
#                 f"'{confidence}' is below "
#                 f"required confidence "
#                 f"'{minimum_confidence}'."
#             ),
#             "alignment": alignment,
#             "validation": validation,
#         }

#     # ---------------------------------------------------------
#     # Step 4: Merge tables
#     # ---------------------------------------------------------

#     try:

#         reconstructed_dataframe = (
#             merge_aligned_tables(
#                 alignment
#             )
#         )

#     except Exception as exc:

#         return {
#             "success": False,
#             "reconstructed": False,
#             "method": "vertical_position_dp",
#             "reason": (
#                 "Table merge failed."
#             ),
#             "error": str(exc),
#             "alignment": alignment,
#             "validation": validation,
#         }

#     # ---------------------------------------------------------
#     # Step 5: Final result
#     # ---------------------------------------------------------

#     return {
#         "success": True,
#         "reconstructed": True,
#         "method": "vertical_position_dp",
#         "confidence": confidence,
#         "reason": None,
#         "dataframe": reconstructed_dataframe,
#         "alignment": alignment,
#         "validation": validation,
#     }
# def merge_aligned_tables(
#     alignment: dict,
# ):
#     """
#     Merge aligned left and right table rows
#     into one pandas DataFrame.
#     """

#     import pandas as pd

#     left_header = alignment[
#         "left_header"
#     ]

#     right_header = alignment[
#         "right_header"
#     ]

#     left_rows = alignment[
#         "left_rows"
#     ]

#     right_rows = alignment[
#         "right_rows"
#     ]

#     matches = alignment[
#         "matches"
#     ]

#     final_rows = []

#     for match in matches:

#         left_index = (
#             match["left_row"] - 1
#         )

#         right_index = (
#             match["right_row"] - 1
#         )

#         left_row = left_rows[
#             left_index
#         ]

#         right_row = right_rows[
#             right_index
#         ]

#         final_row = (
#             list(left_row)
#             + list(right_row)
#         )

#         final_rows.append(
#             final_row
#         )

#     columns = (
#         list(left_header)
#         + list(right_header)
#     )

#     return pd.DataFrame(
#         final_rows,
#         columns=columns,
#     )


# # ============================================================================
# # DataFrame conversion
# # ============================================================================


# def reconstruction_to_dataframe(
#     reconstruction: dict,
# ) -> pd.DataFrame:
#     """
#     Convert successful reconstruction into DataFrame.
#     """

#     if not reconstruction.get(
#         "success"
#     ):

#         raise ValueError(
#             "Cannot create DataFrame "
#             "from failed reconstruction."
#         )

#     if "dataframe" in reconstruction:
#         return reconstruction["dataframe"]

#     headers = reconstruction.get("headers", [])
#     rows = reconstruction.get("rows", [])

#     return pd.DataFrame(
#         rows,
#         columns=headers if headers else None,
#     )


# # ============================================================================
# # Normal single table conversion
# # ============================================================================


# def table_to_dataframe(
#     table_block: dict,
# ) -> pd.DataFrame:
#     """
#     Convert one PaddleOCR HTML table into DataFrame.
#     """

#     html = extract_html(
#         table_block
#     )

#     if not html:

#         raise ValueError(
#             "Table contains no HTML."
#         )

#     header, rows = (
#         get_header_and_rows(
#             html
#         )
#     )

#     return pd.DataFrame(
#         rows,
#         columns=header,
#     )


# # ============================================================================
# # Reconstruct all tables on a page
# # ============================================================================


# def reconstruct_tables(
#     data: dict,
# ) -> list[dict]:
#     """
#     Process all tables on a page.

#     Returns a list where every item contains:

#         {
#             "table": ...,
#             "reconstructed": bool,
#             ...
#         }

#     Tables with ambiguous alignment remain separate.
#     """

#     tables = get_table_blocks(
#         data
#     )

#     if not tables:

#         return []

#     pairs = find_fragment_pairs(
#         tables
#     )

#     paired_ids = set()

#     reconstructed = []

#     for left_table, right_table in pairs:

#         left_id = id(
#             left_table
#         )

#         right_id = id(
#             right_table
#         )

#         if (
#             left_id in paired_ids
#             or right_id in paired_ids
#         ):

#             continue

#         result = reconstruct_fragment_pair(
#             left_table,
#             right_table,
#             data=data,
#             minimum_confidence="medium",
#         )

#         print(
#             f"Success      : {result['success']}"
#         )

#         print(
#             f"Reconstructed: {result['reconstructed']}"
#         )

#         print(
#             f"Confidence   : "
#             f"{result.get('confidence', '-')}"
#         )

#         print(
#             f"Method       : "
#             f"{result.get('method', '-')}"
#         )

#         print(
#             f"Reason       : "
#             f"{result.get('reason', '-')}"
#         )

#         if result.get("dataframe") is not None:

#             df = result["dataframe"]

#             print(
#                 f"\nRows    : {len(df)}"
#             )

#             print(
#                 f"Columns : {len(df.columns)}"
#             )

#             print("\nDataFrame:")
#             print(df.to_string(index=False))
#         if result.get(
#             "success"
#         ):

#             reconstructed.append(
#                 {
#                     "reconstructed": True,
#                     "source_tables": [
#                         left_table,
#                         right_table,
#                     ],
#                     "result": result,
#                 }
#             )

#             paired_ids.add(
#                 left_id
#             )

#             paired_ids.add(
#                 right_id
#             )

#         else:

#             # Keep the original tables.
#             reconstructed.append(
#                 {
#                     "reconstructed": False,
#                     "source_tables": [
#                         left_table,
#                         right_table,
#                     ],
#                     "result": result,
#                 }
#             )

#             paired_ids.add(
#                 left_id
#             )

#             paired_ids.add(
#                 right_id
#             )

#     # Add tables that were not involved in any pair.
#     for table in tables:

#         table_id = id(
#             table
#         )

#         if table_id in paired_ids:
#             continue

#         reconstructed.append(
#             {
#                 "reconstructed": False,
#                 "source_tables": [
#                     table
#                 ],
#                 "result": {
#                     "success": True,
#                     "method": "single_table",
#                     "dataframe": table_to_dataframe(
#                         table
#                     ),
#                 },
#             }
#         )

#     return reconstructed


# # ============================================================================
# # Diagnostic printing
# # ============================================================================


# def print_table_grid(
#     grid: list[list[str]],
#     title: str,
# ):
#     """
#     Print a logical table grid.
#     """

#     print(
#         "\n"
#         + "=" * 80
#     )

#     print(
#         title
#     )

#     print(
#         "=" * 80
#     )

#     for row_index, row in enumerate(
#         grid,
#         start=1,
#     ):

#         print(
#             f"ROW {row_index:02d}:"
#         )

#         for column_index, value in enumerate(
#             row,
#             start=1,
#         ):

#             print(
#                 f"    COL {column_index}: "
#                 f"{value!r}"
#             )


# def print_fragment_analysis(
#     left_table: dict,
#     right_table: dict,
# ):
#     """
#     Print detailed fragment information.
#     """

#     left_geometry = get_table_bbox(
#         left_table
#     )

#     right_geometry = get_table_bbox(
#         right_table
#     )

#     left_html = extract_html(
#         left_table
#     )

#     right_html = extract_html(
#         right_table
#     )

#     left_grid = html_to_grid(
#         left_html
#     )

#     right_grid = html_to_grid(
#         right_html
#     )

#     print(
#         "\n"
#         + "=" * 80
#     )

#     print(
#         "FRAGMENT ANALYSIS"
#     )

#     print(
#         "=" * 80
#     )

#     print(
#         "\nLEFT TABLE"
#     )

#     print(
#         "Geometry:",
#         left_geometry,
#     )

#     print(
#         "Logical rows:",
#         max(
#             len(left_grid) - 1,
#             0,
#         ),
#     )

#     print(
#         "Columns:",
#         len(
#             left_grid[0]
#         )
#         if left_grid
#         else 0,
#     )

#     print(
#         "\nRIGHT TABLE"
#     )

#     print(
#         "Geometry:",
#         right_geometry,
#     )

#     print(
#         "Logical rows:",
#         max(
#             len(right_grid) - 1,
#             0,
#         ),
#     )

#     print(
#         "Columns:",
#         len(
#             right_grid[0]
#         )
#         if right_grid
#         else 0,
#     )

#     if (
#         left_geometry
#         and right_geometry
#     ):

#         print(
#             "\nFragment metrics:"
#         )

#         print(
#             "Vertical overlap:",
#             round(
#                 vertical_overlap_ratio(
#                     left_geometry,
#                     right_geometry,
#                 ),
#                 4,
#             ),
#         )

#         print(
#             "Height similarity:",
#             round(
#                 height_similarity(
#                     left_geometry,
#                     right_geometry,
#                 ),
#                 4,
#             ),
#         )

#         print(
#             "Horizontal gap:",
#             round(
#                 horizontal_gap(
#                     left_geometry,
#                     right_geometry,
#                 ),
#                 2,
#             ),
#         )

#     print_table_grid(
#         left_grid,
#         "LEFT TABLE GRID",
#     )

#     print_table_grid(
#         right_grid,
#         "RIGHT TABLE GRID",
#     )

# def extract_table_row_positions(
#     data: dict,
#     table_block: dict,
# ) -> list[dict]:
#     """
#     Estimate the vertical position of each logical data row
#     inside a PaddleOCR table.

#     The current PaddleOCR JSON does not expose cell-level
#     row coordinates directly, so we use the table geometry
#     together with visual image boxes when available.

#     Returns
#     -------
#     list[dict]

#         [
#             {
#                 "row_index": 1,
#                 "y_center": 575.0,
#                 "y_top": 510.0,
#                 "y_bottom": 640.0,
#                 "source": "image_anchor"
#             },
#             ...
#         ]

#     Notes
#     -----
#     The first HTML row is assumed to be the table header.
#     Therefore row_index starts from 1 for the first data row.
#     """

#     html = extract_html(
#         table_block
#     )

#     if not html:
#         return []

#     header, rows = get_header_and_rows(
#         html
#     )

#     data_row_count = len(rows)

#     if data_row_count == 0:
#         return []

#     geometry = get_table_bbox(
#         table_block
#     )

#     if geometry is None:
#         return []

#     # ---------------------------------------------------------------
#     # First try to use image boxes as visual anchors.
#     #
#     # This is particularly useful for your current document because
#     # the left table contains product images.
#     # ---------------------------------------------------------------

#     image_centers = get_image_row_centers(
#         data,
#         geometry,
#     )

#     if len(image_centers) == data_row_count:

#         table_height = geometry.height

#         approximate_row_height = (
#             table_height / data_row_count
#         )

#         positions = []

#         for index, center_y in enumerate(
#             image_centers,
#             start=1,
#         ):

#             positions.append(
#                 {
#                     "row_index": index,
#                     "y_center": float(
#                         center_y
#                     ),
#                     "y_top": float(
#                         center_y
#                         - approximate_row_height / 2
#                     ),
#                     "y_bottom": float(
#                         center_y
#                         + approximate_row_height / 2
#                     ),
#                     "source": "image_anchor",
#                 }
#             )

#         return positions

#     # ---------------------------------------------------------------
#     # If image count does not match the number of rows, do not
#     # pretend that the image boxes are row anchors.
#     #
#     # Fall back to evenly distributed rows.
#     # ---------------------------------------------------------------

#     centers = calculate_row_centers(
#         geometry,
#         data_row_count,
#     )

#     row_height = (
#         geometry.height
#         / data_row_count
#     )

#     positions = []

#     for index, center_y in enumerate(
#         centers,
#         start=1,
#     ):

#         positions.append(
#             {
#                 "row_index": index,
#                 "y_center": float(
#                     center_y
#                 ),
#                 "y_top": float(
#                     center_y
#                     - row_height / 2
#                 ),
#                 "y_bottom": float(
#                     center_y
#                     + row_height / 2
#                 ),
#                 "source": "estimated",
#             }
#         )

#     return positions
# # ============================================================================
# # Manual test
# # ============================================================================


# if __name__ == "__main__":

#     JSON_FILE = (
#         "storage/json/test2/page_005_res.json"
#     )

#     print(
#         "=" * 80
#     )

#     print(
#         "TABLE UTILS MANUAL TEST"
#     )

#     print(
#         "=" * 80
#     )

#     json_path = Path(
#         JSON_FILE
#     )

#     if not json_path.exists():

#         print(
#             f"\nJSON file not found:"
#             f"\n{json_path}"
#         )

#         raise SystemExit(
#             1
#         )

#     data = load_json(
#         json_path
#     )

#     tables = get_table_blocks(
#         data
#     )

#     print(
#         f"\nDetected tables: "
#         f"{len(tables)}"
#     )

#     print(
#         "=" * 80
#     )

#     # ------------------------------------------------------------------------
#     # Print individual tables.
#     # ------------------------------------------------------------------------

#     for index, table in enumerate(
#         tables,
#         start=1,
#     ):

#         statistics = table_statistics(
#             table
#         )

#         print(
#             f"\nTable {index}"
#         )

#         print(
#             "BBox:",
#             statistics[
#                 "geometry"
#             ],
#         )

#         print(
#             "Raw HTML rows:",
#             statistics[
#                 "raw_rows"
#             ],
#         )

#         print(
#             "Logical rows:",
#             statistics[
#                 "logical_rows"
#             ],
#         )

#         print(
#             "Columns:",
#             statistics[
#                 "columns"
#             ],
#         )

#         print(
#             "Header:",
#             statistics[
#                 "header"
#             ],
#         )
#     print(
#         "\n"
#         + "=" * 80
#     )

#     print(
#         "TABLE ROW POSITIONS"
#     )

#     print(
#         "=" * 80
#     )

#     for table_index, table in enumerate(
#         tables,
#         start=1,
#     ):

#         print(
#             f"\nTable {table_index}"
#         )

#         positions = extract_table_row_positions(
#             data,
#             table,
#         )

#         print(
#             f"Detected row positions: "
#             f"{len(positions)}"
#         )

#         for position in positions:

#             print(
#                 f"  Row "
#                 f"{position['row_index']:02d} "
#                 f"| "
#                 f"Y={position['y_center']:.2f} "
#                 f"| "
#                 f"source={position['source']}"
#             )
#     # ------------------------------------------------------------------------
#     # Find fragment candidates.
#     # ------------------------------------------------------------------------

#     pairs = find_fragment_pairs(
#         tables
#     )

#     print(
#         "\n"
#         + "=" * 80
#     )

#     print(
#         "Possible fragment pairs:",
#         len(pairs),
#     )

#     print(
#         "=" * 80
#     )

#     for pair_index, (
#         left,
#         right,
#     ) in enumerate(
#         pairs,
#         start=1,
#     ):

#         print(
#             f"\nCandidate pair {pair_index}"
#         )

#         print_fragment_analysis(
#             left,
#             right,
#         )

#         alignment = align_fragment_rows(
#             left,
#             right,
#             data=data,
#         )

#         print(
#             "\n"
#             + "=" * 80
#         )

#         print(
#             "ROW ALIGNMENT"
#         )

#         print(
#             "=" * 80
#         )

#         print(
#             "Success:",
#             alignment.get(
#                 "success"
#             ),
#         )

#         print(
#             "Method:",
#             alignment.get(
#                 "method"
#             ),
#         )

#         print(
#             "Reason:",
#             alignment.get(
#                 "reason"
#             ),
#         )

#         print(
#             "Left rows:",
#             alignment.get(
#                 "left_row_count",
#                 alignment.get(
#                     "row_count"
#                 ),
#             ),
#         )

#         print(
#             "Right rows:",
#             alignment.get(
#                 "right_row_count",
#                 alignment.get(
#                     "row_count"
#                 ),
#             ),
#         )

#         print(
#             "\nCandidate mapping:"
#         )

#         for index, mapping in enumerate(
#             alignment.get(
#                 "candidate_mapping",
#                 [],
#             ),
#             start=1,
#         ):

#             print(
#                 f"  Left row {index:02d}"
#                 f" -> Right rows "
#                 f"{[
#                     value + 1
#                     for value in mapping
#                 ]}"
#             )

#         # ------------------------------------------------------------
#         # Reconstruction
#         # ------------------------------------------------------------

#         reconstruction = (
#             reconstruct_fragment_pair(
#                 left,
#                 right,
#                 data=data,
#             )
#         )

#         print(
#             "\n"
#             + "=" * 80
#         )

#         print(
#             "RECONSTRUCTION RESULT"
#         )

#         print(
#             "=" * 80
#         )

#         print(
#             "Success:",
#             reconstruction.get(
#                 "success"
#             ),
#         )

#         print(
#             "Reason:",
#             reconstruction.get(
#                 "reason"
#             ),
#         )

#         if reconstruction.get(
#             "success"
#         ):

#             print(
#                 "Method:",
#                 reconstruction.get(
#                     "method"
#                 ),
#             )

#             print(
#                 "Rows:",
#                 reconstruction.get(
#                     "row_count"
#                 ),
#             )

#             print(
#                 "Columns:",
#                 reconstruction.get(
#                     "column_count"
#                 ),
#             )

#             dataframe = (
#                 reconstruction_to_dataframe(
#                     reconstruction
#                 )
#             )

#             print(
#                 "\nDataFrame:"
#             )

#             print(
#                 dataframe.to_string(
#                     index=False
#                 )
#             )

#     # ------------------------------------------------------------------------
#     # Process all tables.
#     # ------------------------------------------------------------------------

#     print(
#         "\n"
#         + "=" * 80
#     )

#     print(
#         "FINAL TABLE PROCESSING"
#     )

#     print(
#         "=" * 80
#     )

#     results = reconstruct_tables(
#         data
#     )

#     for index, item in enumerate(
#         results,
#         start=1,
#     ):

#         print(
#             f"\nTable result {index}"
#         )

#         print(
#             "Reconstructed:",
#             item.get(
#                 "reconstructed"
#             ),
#         )

#         result = item.get(
#             "result",
#             {},
#         )

#         print(
#             "Success:",
#             result.get(
#                 "success"
#             ),
#         )

#         if not result.get(
#             "success"
#         ):

#             print(
#                 "Reason:",
#                 result.get(
#                     "reason"
#                 ),
#             )

#     print(
#         "\n"
#         + "=" * 80
#     )

#     print(
#         "Manual test completed."
#     )

#     print(
#         "=" * 80
#     )