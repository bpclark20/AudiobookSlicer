"""Shared constants for AudioBookSlicer GUI."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(__file__).parent  # type: ignore[attr-defined]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from PyQt6.QtCore import Qt

_has_qt_multimedia: bool = False
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer  # noqa: E402
    _has_qt_multimedia = True
except ModuleNotFoundError:
    pass

THUMB_SIZE = 48  # cover thumbnail height/width in pixels
CHAPTER_PROGRESS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
CHAPTER_KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 2
CHAPTER_PREVIEW_PROGRESS_ROLE = int(Qt.ItemDataRole.UserRole) + 3

DEFAULT_USE_VBR = True
DEFAULT_VBR_QUALITY = 5
DEFAULT_CBR_BITRATE = 64
DEFAULT_CHANNELS = 1
DEFAULT_SIZE_LIMIT_TEXT = "1000000000"
DEFAULT_SIZE_UNIT = "B"
DEFAULT_PREVIEW_DEVICE = ""

VBR_QUALITY_BITRATE_HINTS: dict[int, str] = {
    0: "220-260 kbps",
    1: "190-230 kbps",
    2: "170-210 kbps",
    3: "150-195 kbps",
    4: "140-185 kbps",
    5: "120-165 kbps",
    6: "100-140 kbps",
    7: "85-120 kbps",
    8: "70-105 kbps",
    9: "55-85 kbps",
}


