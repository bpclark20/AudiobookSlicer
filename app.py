#!/usr/bin/env python3
"""Entry point for AudioBookSlicer GUI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

# Check PyQt6 availability before importing any gui modules.
try:
    from PyQt6.QtCore import Qt  # minimal probe  # noqa: F401
except ModuleNotFoundError:
    print(
        "\n\u274c Error: PyQt6 is not installed.\n"
        "PyQt6 is installed in the project's virtual environment (.venv).\n"
        "To run this GUI, use one of these methods:\n\n"
        "  Method 1 (Recommended - Activate venv first):\n"
        "    & '.venv\\\\Scripts\\\\Activate.ps1'\n"
        "    python app.py\n\n"
        "  Method 2 (Direct - Use venv Python):\n"
        "    & '.venv\\\\Scripts\\\\python.exe' app.py\n\n"
        "  Method 3 (NOT RECOMMENDED - Install to system Python):\n"
        "    pip install PyQt6\n"
        "    python app.py\n",
        file=sys.stderr,
    )
    sys.exit(1)

# gui_constants sets up sys.path so wav_markers_to_mp3 is importable.
from gui_constants import _HERE  # noqa: E402, F401
from main_window import MainWindow  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402

import wav_markers_to_mp3 as _wav_m2m  # noqa: E402


def _make_no_window_run_command():
    """Return a patched run_command that suppresses the console window on Windows.

    On Windows, subprocess.run() called from a GUI app with no console will
    briefly flash a cmd window and can deliver spurious SIGINT to the parent
    process.  CREATE_NO_WINDOW prevents both problems.
    """
    import wav_markers_to_mp3 as _m

    def _patched(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = dict(text=True, capture_output=True)
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return cast(subprocess.CompletedProcess[str], subprocess.run(cmd, **kwargs))

    _m.run_command = _patched  # type: ignore[assignment]


def main() -> None:
    if sys.platform == "win32":
        _make_no_window_run_command()

    app = QApplication(sys.argv)
    app.setApplicationName("AudioBook Slicer")
    icon_path = _HERE / "icon.png"
    if icon_path.exists() and icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--split-worker":
        # Frozen-build worker mode: the EXE re-invokes itself via QProcess to run
        # the M4B splitter. Shift argv so split_m4b_chapters.parse_args() sees its
        # own arguments (input, --output-dir, etc.) at the normal positions.
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from split_m4b_chapters import main as _split_main  # noqa: E402
        _split_main()
    else:
        main()
