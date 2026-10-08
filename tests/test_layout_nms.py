from pathlib import Path

from paddleocr import PaddleOCRVL


IMAGE_PATH = (
    "storage/pages/test2/page_005.png"
)


def run_test():

    print("=" * 80)
    print("PADDLEOCR LAYOUT NMS TEST")
    print("=" * 80)

    print()
    print("Configuration:")
    print("  layout_nms = False")
    print("  layout_threshold = 0.3")
    print("  layout_unclip_ratio = (1.0, 1.0)")

    print()
    print("=" * 80)
    print("INITIALIZING PADDLEOCR-VL")
    print("=" * 80)

    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",

        device="cpu",

        # Keep the current threshold unchanged
        layout_threshold=0.3,

        # IMPORTANT:
        # Disable layout NMS for this experiment
        layout_nms=False,

        # Keep current value unchanged
        layout_unclip_ratio=(
            1.0,
            1.0,
        ),

        # Keep current behavior
        merge_layout_blocks=True,
    )

    print()
    print("=" * 80)
    print("RUNNING OCR")
    print("=" * 80)

    results = pipeline.predict(
        IMAGE_PATH
    )

    for result in results:

        data = result.json

        if not isinstance(
            data,
            dict,
        ):
            print(
                "Unexpected result format."
            )
            continue

        data = data.get(
            "res",
            {}
        )

        print()
        print("=" * 80)
        print("RESULT")
        print("=" * 80)

        print(
            "Input:",
            data.get("input_path"),
        )

        print(
            "Width:",
            data.get("width"),
        )

        print(
            "Height:",
            data.get("height"),
        )

        # ----------------------------------------------------
        # Layout detection result
        # ----------------------------------------------------

        layout_det_res = data.get(
            "layout_det_res",
            {}
        )

        boxes = layout_det_res.get(
            "boxes",
            []
        )

        print()
        print("=" * 80)
        print("LAYOUT DETECTION")
        print("=" * 80)

        print(
            "Total boxes:",
            len(boxes),
        )

        # ----------------------------------------------------
        # Print only table detections
        # ----------------------------------------------------

        table_boxes = [
            box
            for box in boxes
            if box.get("label") == "table"
        ]

        print(
            "Table detections:",
            len(table_boxes),
        )

        print()

        for index, box in enumerate(
            table_boxes,
            start=1,
        ):

            print(
                f"TABLE {index}"
            )

            print(
                "  Class ID:",
                box.get("cls_id"),
            )

            print(
                "  Score:",
                box.get("score"),
            )

            print(
                "  Coordinate:",
                box.get("coordinate"),
            )

            print()

        # ----------------------------------------------------
        # Parsing result
        # ----------------------------------------------------

        parsing_blocks = data.get(
            "parsing_res_list",
            []
        )

        parsed_tables = [
            block
            for block in parsing_blocks
            if block.get(
                "block_label"
            ) == "table"
        ]

        print("=" * 80)
        print("PARSING RESULT")
        print("=" * 80)

        print(
            "Parsed table count:",
            len(parsed_tables),
        )

        for index, table in enumerate(
            parsed_tables,
            start=1,
        ):

            print(
                f"\nTABLE {index}"
            )

            print(
                "  BBox:",
                table.get(
                    "block_bbox"
                ),
            )

            print(
                "  HTML length:",
                len(
                    table.get(
                        "block_content",
                        "",
                    )
                ),
            )

    print()
    print("=" * 80)
    print("TEST COMPLETED")
    print("=" * 80)


if __name__ == "__main__":

    run_test()