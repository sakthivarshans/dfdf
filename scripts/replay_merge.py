"""
Replay the merge over OCR JSON that already exists on disk.

Extraction is slow and GPU-bound, but every post-processing change can be
checked against output from a previous run in under a second. Use this
rather than re-running extraction when working on the normaliser.

    python scripts/replay_merge.py                 # every document
    python scripts/replay_merge.py "BUD NP10*"     # one of them
"""

import sys
from pathlib import Path

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import JSON_DIR
from utils.table_normalizer import merge, normalize_table
from utils.table_reader import read_tables


def replay(json_folder: Path) -> None:
    tables = read_tables(json_folder)

    frames = []
    for record in tables:
        normalized = normalize_table(record.dataframe, f"table-{record.table_index}")
        if normalized is not None:
            frames.append(normalized)

    merged, rejected, stats = merge(frames)

    print("=" * 78)
    print(json_folder.name)
    print(f"  tables       : {len(frames)} qualified of {len(tables)} found")
    print(f"  rows in      : {stats['rows_in']}")
    print(f"  rows out     : {stats['rows_out']}  (repaired {stats['repaired']})")
    print(f"  filled       : {stats['filled']} cells from merged groups")
    print(f"  dropped      : {stats['blank_midas']} blank Midas, "
          f"{stats['banners']} banners")
    print(f"  rejected     : {stats['rejected']}")
    if not merged.empty:
        print(f"  columns ({len(merged.columns)}): {list(merged.columns)}")
    for _, row in rejected.iterrows():
        print(f"    rejected: {row['reason']}")


def main() -> None:
    pattern = sys.argv[1] if len(sys.argv) > 1 else "*"
    folders = [d for d in sorted(JSON_DIR.glob(pattern)) if d.is_dir()]

    if not folders:
        print(f"No JSON folders matching {pattern!r} under {JSON_DIR}")
        return

    for folder in folders:
        try:
            replay(folder)
        except FileNotFoundError as e:
            print(f"skip {folder.name}: {e}")


if __name__ == "__main__":
    main()
