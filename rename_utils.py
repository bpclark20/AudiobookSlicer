#!/usr/bin/env python3
"""Shared helpers for the batch file-rename tools.

Each tool builds a list of RenameItem entries, then either prints a preview
table or applies the renames using a two-pass (temp name -> final name)
strategy that avoids collisions when names are shuffled within a folder.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


@dataclass(frozen=True)
class RenameItem:
    source: Path
    new_name: str
    action: str = "Rename"

    @property
    def target(self) -> Path:
        return self.source.with_name(self.new_name)

    @property
    def is_rename(self) -> bool:
        return self.action == "Rename"


def fail(message: str, code: int = 1) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def list_files(folder: Path, pattern: str, recurse: bool = False) -> list[Path]:
    if not folder.is_dir():
        fail(f"Folder not found: {folder}")
    matches = folder.rglob(pattern) if recurse else folder.glob(pattern)
    files = [p for p in matches if p.is_file()]
    if recurse:
        files.sort(key=lambda p: str(p).casefold())
    else:
        files.sort(key=lambda p: p.name.casefold())
    return files


def validate_plan(plan: list[RenameItem]) -> None:
    """Reject plans where two files map to the same target, or a target
    collides with an existing file that is not itself being renamed."""
    renames = [item for item in plan if item.is_rename]
    sources = {str(item.source).casefold() for item in renames}

    seen: dict[str, RenameItem] = {}
    duplicates: list[str] = []
    for item in renames:
        key = str(item.target).casefold()
        if key in seen:
            duplicates.append(item.new_name)
        seen[key] = item
    if duplicates:
        fail(
            "Multiple files would be renamed to the same target: "
            + ", ".join(sorted(set(duplicates)))
        )

    conflicts = [
        item.new_name
        for item in renames
        if item.target.exists() and str(item.target).casefold() not in sources
    ]
    if conflicts:
        fail("Target file(s) already exist: " + ", ".join(conflicts))


def print_plan(plan: list[RenameItem], show_action: bool = False) -> None:
    headers = ["OldName", "NewName"] + (["Action"] if show_action else [])
    rows = [
        [item.source.name, item.new_name] + ([item.action] if show_action else [])
        for item in plan
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip())
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


def run_plan(plan: list[RenameItem], apply: bool, show_action: bool = False) -> int:
    """Preview or apply the plan. Returns the number of files renamed."""
    validate_plan(plan)

    if not apply:
        print("Preview mode only. No files will be changed.")
        print()
        print_plan(plan, show_action=show_action)
        print()
        print("Run again with --apply to perform the rename.")
        return 0

    renames = [item for item in plan if item.is_rename]
    temps: list[tuple[Path, RenameItem]] = []
    for item in renames:
        temp = item.source.with_name(f"__tmp__{uuid.uuid4().hex}{item.source.suffix}")
        item.source.rename(temp)
        temps.append((temp, item))
    for temp, item in temps:
        temp.rename(item.target)

    print(f"Renamed {len(renames)} files successfully.")
    return len(renames)
