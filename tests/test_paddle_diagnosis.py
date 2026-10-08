from pathlib import Path
import sys


# ============================================================
# Allow running directly
# ============================================================

project_root = Path(__file__).resolve().parents[1]

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


from engine.paddle_engine import PaddleEngine


# ============================================================
# Configuration
# ============================================================

IMAGE_PATH = (
    "storage/pages/test2/page_005.png"
)


# ============================================================
# Diagnostic
# ============================================================
def inspect_result(result):

    print()
    print("=" * 80)
    print("PADDLEOCR RESULT DIAGNOSTICS")
    print("=" * 80)

    # --------------------------------------------------------
    # Result attributes
    # --------------------------------------------------------

    print("\nRESULT ATTRIBUTES")
    print("-" * 80)

    print(
        [
            name
            for name in dir(result)
            if not name.startswith("_")
        ]
    )

    # --------------------------------------------------------
    # Raw JSON
    # --------------------------------------------------------

    if not hasattr(result, "json"):

        print(
            "\nNo result.json attribute found."
        )

        return

    raw_json = result.json

    print("\nRAW JSON TYPE")
    print("-" * 80)

    print(type(raw_json))

    print("\nRAW JSON KEYS")
    print("-" * 80)

    if isinstance(raw_json, dict):
        print(list(raw_json.keys()))

    # --------------------------------------------------------
    # Extract actual `res`
    # --------------------------------------------------------

    if not isinstance(raw_json, dict):

        print(
            "\n⚠ Unexpected JSON structure."
        )

        return

    data = raw_json.get("res")

    if not isinstance(data, dict):

        print(
            "\n⚠ `res` object not found."
        )

        print(
            "Available keys:",
            list(raw_json.keys()),
        )

        return

    print("\nRES KEYS")
    print("-" * 80)

    print(
        list(data.keys())
    )

    # --------------------------------------------------------
    # Page information
    # --------------------------------------------------------

    print("\nPAGE INFORMATION")
    print("-" * 80)

    print(
        "Input path:",
        data.get("input_path"),
    )

    print(
        "Page index:",
        data.get("page_index"),
    )

    print(
        "Width:",
        data.get("width"),
    )

    print(
        "Height:",
        data.get("height"),
    )

    # --------------------------------------------------------
    # Model settings
    # --------------------------------------------------------

    print("\nMODEL SETTINGS")
    print("-" * 80)

    model_settings = data.get(
        "model_settings"
    )

    if isinstance(
        model_settings,
        dict,
    ):

        for key, value in model_settings.items():

            print(
                f"{key}: {value}"
            )

    else:

        print(
            "No model_settings found."
        )
    # --------------------------------------------------------
    # Raw layout detection result
    # --------------------------------------------------------

    layout_det_res = data.get(
        "layout_det_res"
    )

    print("\nLAYOUT DETECTION RESULT")
    print("-" * 80)

    if layout_det_res is None:

        print(
            "No layout_det_res found."
        )

    else:

        print(
            "Type:",
            type(layout_det_res),
        )

        if isinstance(
            layout_det_res,
            dict,
        ):

            print(
                "Keys:",
                list(
                    layout_det_res.keys()
                ),
            )

            for key, value in layout_det_res.items():

                print()
                print(
                    f"{key}:"
                )

                print(
                    value
                )

        else:

            print(
                layout_det_res
            )

    # --------------------------------------------------------
    # Parsing blocks
    # --------------------------------------------------------

    blocks = data.get(
        "parsing_res_list",
        [],
    )

    print("\nLAYOUT BLOCKS")
    print("-" * 80)

    print(
        "Total blocks:",
        len(blocks),
    )

    # --------------------------------------------------------
    # Print all blocks
    # --------------------------------------------------------

    tables = []

    for index, block in enumerate(
        blocks
    ):

        label = block.get(
            "block_label"
        )

        print()
        print(
            f"BLOCK {index + 1}"
        )

        print("-" * 80)

        print(
            "Label:",
            label,
        )

        print(
            "ID:",
            block.get("block_id"),
        )

        print(
            "Order:",
            block.get("block_order"),
        )

        print(
            "Group:",
            block.get("group_id"),
        )

        print(
            "BBox:",
            block.get("block_bbox"),
        )

        # ----------------------------------------------------
        # Table-specific information
        # ----------------------------------------------------

        if label == "table":

            tables.append(
                block
            )

            html = block.get(
                "block_content",
                "",
            )

            print(
                "HTML length:",
                len(html),
            )

            print(
                "Contains <table>:",
                "<table"
                in html.lower(),
            )

    # --------------------------------------------------------
    # Table summary
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("TABLE SUMMARY")
    print("=" * 80)

    print(
        "Detected tables:",
        len(tables),
    )

    for index, table in enumerate(
        tables,
        start=1,
    ):

        print()
        print(
            f"TABLE {index}"
        )

        print("-" * 80)

        print(
            "BBox:",
            table.get(
                "block_bbox"
            ),
        )

        print(
            "Block ID:",
            table.get(
                "block_id"
            ),
        )

        print(
            "Block order:",
            table.get(
                "block_order"
            ),
        )

        print(
            "Group ID:",
            table.get(
                "group_id"
            ),
        )

        html = table.get(
            "block_content",
            "",
        )

        print(
            "HTML length:",
            len(html),
        )

    # --------------------------------------------------------
    # Final diagnosis
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("DIAGNOSIS SUMMARY")
    print("=" * 80)

    if len(tables) == 1:

        print(
            "✓ PaddleOCR detected ONE table."
        )

    elif len(tables) > 1:

        print(
            "⚠ PaddleOCR detected "
            f"{len(tables)} tables."
        )

        print(
            "This is the page we need "
            "to investigate."
        )

    else:

        print(
            "⚠ No table detected."
        )

# ============================================================
# Main
# ============================================================

def main():

    print("=" * 80)
    print("PADDLEOCR-VL PHASE 1 DIAGNOSTIC")
    print("=" * 80)

    print(
        "\nImage:",
        IMAGE_PATH,
    )

    # --------------------------------------------------------
    # Load engine
    # --------------------------------------------------------

    engine = PaddleEngine()

    # --------------------------------------------------------
    # Run prediction
    # --------------------------------------------------------

    results = engine.predict_image(
        IMAGE_PATH
    )

    # --------------------------------------------------------
    # Inspect results
    # --------------------------------------------------------

    for result in results:

        inspect_result(
            result
        )

    print()
    print("=" * 80)
    print("DIAGNOSTIC COMPLETED")
    print("=" * 80)


if __name__ == "__main__":
    main()