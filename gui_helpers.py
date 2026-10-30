"""Pure utility functions for AudioBookSlicer GUI."""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Callable

from wav_markers_to_mp3 import Chapter, format_seconds_hms  # noqa: E402


def _format_duration_label(duration_sec: float) -> str:
    total_seconds = max(0, int(duration_sec))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _chapter_key(chapter: Chapter) -> str:
    return f"{chapter.start_ms}:{chapter.end_ms}"


def _format_counter_label(elapsed_sec: float, duration_sec: float) -> str:
    return f"{_format_duration_label(elapsed_sec)} / {_format_duration_label(duration_sec)}"



def _sanitize_output_stem(title: str) -> str:
    sanitized = "".join(
        "_" if ch in '<>:"/\\|?*' else ch
        for ch in title.strip().rstrip(". ")
    )
    sanitized = sanitized.strip()
    return sanitized or "output"


def _book_title_from_chapter(chapter_title: str) -> str | None:
    parts = chapter_title.split(" - ", 1)
    if len(parts) != 2:
        return None
    title = parts[0].strip()
    return title or None


def _find_covers_by_title(input_dir: Path) -> dict[str, Path]:
    """Map case-insensitive image stem -> preferred image path."""
    image_files = [
        child
        for child in input_dir.iterdir()
        if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    by_stem: dict[str, list[Path]] = {}
    for image in image_files:
        by_stem.setdefault(image.stem.casefold(), []).append(image)

    preferred_ext = {".jpg": 0, ".jpeg": 1, ".png": 2}
    resolved: dict[str, Path] = {}
    for stem, candidates in by_stem.items():
        candidates.sort(key=lambda p: preferred_ext.get(p.suffix.lower(), 99))
        resolved[stem] = candidates[0]
    return resolved


def _find_cover_for_source_file(source_file: Path, book_title: str | None) -> Path | None:
    """Find cover image for a specific source file.
    
    Looks in the source file's directory for:
    1. A file named after the book_title (if provided)
    2. A file named "Cover"
    
    Prefers .jpg over .jpeg over .png.
    """
    file_dir = source_file.parent
    if not book_title:
        # Only look for generic "cover" names
        search_names = ["cover"]
    else:
        # Look for title-specific first, then generic
        search_names = [book_title.casefold(), "cover"]
    
    preferred_ext = {".jpg": 0, ".jpeg": 1, ".png": 2}
    
    for search_name in search_names:
        candidates: list[Path] = []
        for ext in [".jpg", ".jpeg", ".png"]:
            candidate = file_dir / f"{search_name}{ext}"
            if candidate.exists() and candidate.is_file():
                candidates.append(candidate)
        
        if candidates:
            # Sort by preferred extension
            candidates.sort(key=lambda p: preferred_ext.get(p.suffix.lower(), 99))
            return candidates[0]
    
    return None



def _run_capturing_errors(
    fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> tuple[Any, None] | tuple[None, str]:
    """Call fn(*args, **kwargs).

    Returns (result, None) on success.
    Returns (None, error_message) if the function raises SystemExit (via fail())
    or KeyboardInterrupt (can be fired spuriously by Python signal handling
    when subprocess.run is called on the Qt main thread on Windows).
    """
    buf = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = buf
    try:
        result = fn(*args, **kwargs)
        return result, None
    except (SystemExit, KeyboardInterrupt):
        msg = buf.getvalue().strip()
        if msg.startswith("ERROR: "):
            msg = msg[len("ERROR: "):]
        return None, msg or "Operation cancelled"
    finally:
        sys.stderr = old_stderr


