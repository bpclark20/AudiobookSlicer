#!/usr/bin/env python3
"""Remove the leading numeric prefix from chapter WAV filenames.

Example: "024 - Chapter 24 - Part 1.wav" -> "Chapter 24 - Part 1.wav"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from rename_utils import RenameItem, fail, list_files, run_plan

TITLE_PATTERN = r"(?:Chapter\s*\d+|Prologue|Prolouge|Epilogue)(?:\s*-\s*Part\s*\d+)?"
NAME_RE = re.compile(rf"^(?P<prefix>\d+)\s*-\s*(?P<title>{TITLE_PATTERN})$", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strip the leading '<NNN> - ' prefix from chapter filenames."
    )
    parser.add_argument("path", nargs="?", type=Path, default=Path("."), help="Folder (default: .)")
    parser.add_argument("--filter", default="*.wav", help="Filename glob filter (default: *.wav)")
    parser.add_argument("--recurse", action="store_true", help="Include subfolders")
    parser.add_argument("--apply", action="store_true", help="Perform the rename (default: preview only)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    files = list_files(args.path, args.filter, recurse=args.recurse)
    if not files:
        print(f"No files found in '{args.path}' matching filter: {args.filter}")
        return

    plan: list[RenameItem] = []
    for f in files:
        match = NAME_RE.match(f.stem)
        if not match:
            fail(f"Unexpected filename format: {f.name}")
        plan.append(RenameItem(f, f"{match['title']}{f.suffix}"))

    run_plan(plan, args.apply)


if __name__ == "__main__":
    main()
