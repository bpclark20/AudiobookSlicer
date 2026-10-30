# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for AudioBookSlicer -- onedir build
#
# Usage (from project root with venv active):
#   pyinstaller AudioBookSlicer.spec
#
# Output: dist/AudioBookSlicer/AudioBookSlicer.exe  (+ _internal/ folder)
#
# Notes:
#   - ffmpeg and ffprobe are NOT bundled; they must be on PATH or placed next to
#     the exe by the end user.
#   - mutagen is added as a hidden import because it is loaded via importlib at
#     runtime rather than a normal top-level import.
#   - The Tools menu scripts are hidden imports: the frozen exe runs them by
#     relaunching itself as `AudioBookSlicer.exe --run-tool <module> ...`.
#   - PyQt6.QtMultimedia powers chapter preview playback (ffplay is the fallback).

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("icon.png", "."),   # window icon, placed into _internal/
        ("icon.ico", "."),   # ICO copy for taskbar/explorer (used by bootloader)
    ],
    hiddenimports=[
        "mutagen",
        "mutagen.id3",
        "mutagen.id3._tags",
        "mutagen.id3._frames",
        "mutagen.mp4",
        "PyQt6.QtMultimedia",
        "rename_utils",
        "prefix_book_title",
        "rename_chapters_offset",
        "split_m4b_chapters",
        "strip_chapter_number_prefix",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioBookSlicer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # no console window (windowed app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AudioBookSlicer",
)
