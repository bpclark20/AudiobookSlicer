#!/usr/bin/env python3
"""Prepend a book title to every filename in a folder.

Example: "Chapter 1.wav" -> "Role Model - Chapter 1.wav"
Files that already start with "<title> - " are skipped.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rename_utils import RenameItem, fail, list_files, run_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepend '<Book Title> - ' to every file in a folder."
    )
    parser.add_argument("path", nargs="?", type=Path, default=Path("."), help="Folder (default: .)")
    parser.add_argument("--title", help="Book title to prepend (prompted if omitted)")
    parser.add_argument("--filter", default="*", help="Filename glob filter (default: *)")
    parser.add_argument("--apply", action="store_true", help="Perform the rename (default: preview only)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    title = args.title if args.title is not None else input("Enter the book title to prepend: ")
    title = title.strip()
    if not title:
        fail("Book title cannot be empty.")

    files = list_files(args.path, args.filter)
    if not files:
        print(f"No files found in '{args.path}' matching filter: {args.filter}")
        return

    prefix = f"{title} - "
    plan = [
        RenameItem(f, f.name, "SkipAlreadyPrefixed")
        if f.name.casefold().startswith(prefix.casefold())
        else RenameItem(f, prefix + f.name)
        for f in files
    ]

    run_plan(plan, args.apply, show_action=True)

    skipped = sum(1 for item in plan if not item.is_rename)
    if args.apply and skipped:
        print(f"Skipped {skipped} files that already had the title prefix.")


if __name__ == "__main__":
    main()
