#!/usr/bin/env python3
"""Subtract an offset from chapter numbers in filenames.

Example (offset 29): "030 - Chapter 30.wav" -> "001 - Chapter 1.wav"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from rename_utils import RenameItem, fail, list_files, run_plan

NAME_RE = re.compile(r"^(?P<num>\d+)\s*-\s*Chapter\s*(?P<chap>\d+)$", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Renumber '<NNN> - Chapter <N>' files by subtracting an offset."
    )
    parser.add_argument("path", nargs="?", type=Path, default=Path("."), help="Folder (default: .)")
    parser.add_argument("--offset", type=int, default=29, help="Amount to subtract (default: 29)")
    parser.add_argument("--filter", default="*.wav", help="Filename glob filter (default: *.wav)")
    parser.add_argument("--apply", action="store_true", help="Perform the rename (default: preview only)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    files = list_files(args.path, args.filter)
    if not files:
        print(f"No files found in '{args.path}' matching filter: {args.filter}")
        return

    plan: list[RenameItem] = []
    for f in files:
        match = NAME_RE.match(f.stem)
        if not match:
            fail(f"Unexpected filename format: {f.name}")
        new_num = int(match["num"]) - args.offset
        new_chap = int(match["chap"]) - args.offset
        if new_num < 1 or new_chap < 1:
            fail(f"Invalid result after subtracting offset {args.offset}: {f.name}")
        plan.append(RenameItem(f, f"{new_num:03d} - Chapter {new_chap}{f.suffix}"))

    run_plan(plan, args.apply)


if __name__ == "__main__":
    main()
