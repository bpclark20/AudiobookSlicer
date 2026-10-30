#!/usr/bin/env python3
"""GUI front-end for wav_markers_to_mp3.py (MP3 or M4B output), plus a Tools menu for the helper scripts."""

from __future__ import annotations

import importlib
import io
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, cast

# Ensure wav_markers_to_mp3 is importable from the same directory.
# In a PyInstaller build, bundled files live in sys._MEIPASS.
_IS_FROZEN = hasattr(sys, "_MEIPASS")
_HERE = Path(sys._MEIPASS) if _IS_FROZEN else Path(__file__).parent  # type: ignore[attr-defined]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from rename_utils import RenameItem, validate_plan  # noqa: E402
from wav_markers_to_mp3 import (  # noqa: E402
    Chapter,
    build_chapters,
    encode_output,
    ensure_executable,
    format_bytes,
    format_seconds_hms,
    probe_audio_info,
    read_riff_chunks,
)

try:
    from PyQt6.QtCore import (
        QEvent,
        QModelIndex,
        QObject,
        QProcess,
        QProcessEnvironment,
        QSettings,
        QThread,
        QTimer,
        Qt,
        QUrl,
        pyqtSignal,
    )  # noqa: E402
    from PyQt6.QtGui import (
        QAction,
        QCloseEvent,
        QColor,
        QDropEvent,
        QFontDatabase,
        QIcon,
        QKeySequence,
        QMouseEvent,
        QPainter,
        QPixmap,
        QResizeEvent,
    )  # noqa: E402
    from PyQt6.QtWidgets import (  # noqa: E402
        QAbstractItemView,
        QApplication,
        QButtonGroup,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
        QProgressBar,
        QSizePolicy,
        QSplitter,
        QSpinBox,
        QStatusBar,
        QStyledItemDelegate,
        QStyle,
        QStyleOptionViewItem,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ModuleNotFoundError as e:
    print(
        "\n❌ Error: PyQt6 is not installed.\n"
        "PyQt6 is installed in the project's virtual environment (.venv).\n"
        "To run this GUI, use one of these methods:\n\n"
        "  Method 1 (Recommended - Activate venv first):\n"
        f"    & '.venv\\Scripts\\Activate.ps1'\n"
        "    python gui.py\n\n"
        "  Method 2 (Direct - Use venv Python):\n"
        f"    & '.venv\\Scripts\\python.exe' gui.py\n\n"
        "  Method 3 (Create a shortcut):\n"
        "    Create 'run_gui.ps1' with the Method 1 commands,\n"
        "    then run: .\\run_gui.ps1\n\n"
        "  Method 4 (NOT RECOMMENDED - Install to system Python):\n"
        "    pip install PyQt6\n"
        "    python gui.py\n"
        "    (This may cause version conflicts with other projects)\n",
        file=sys.stderr,
    )
    sys.exit(1)

_has_qt_multimedia = False
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer  # noqa: E402

    _has_qt_multimedia = True
except ImportError:
    # Preview falls back to ffplay; these names are only used when the import worked.
    QAudioOutput = QMediaDevices = QMediaPlayer = cast(Any, None)

APP_NAME = "AudioBook Slicer"
THUMB_SIZE = 48  # cover thumbnail height/width in pixels
CHAPTER_PROGRESS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
CHAPTER_KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 2
CHAPTER_PREVIEW_PROGRESS_ROLE = int(Qt.ItemDataRole.UserRole) + 3
INTERMEDIATE_DIR_PREFIX = "audiobookslicer_intermediate_"
TOOL_WORKER_FLAG = "--run-tool"
TOOL_MODULES = (
    "prefix_book_title",
    "rename_chapters_offset",
    "split_m4b_chapters",
    "strip_chapter_number_prefix",
)
SIZE_UNITS = ("b", "kb", "Mb", "Gb", "B", "KB", "MB", "GB")

# Output formats: key -> (short label, description)
OUTPUT_FORMATS: dict[str, tuple[str, str]] = {
    "mp3": ("MP3", "MP3 (LAME)"),
    "m4b": ("M4B", "M4B (AAC, iPod-compatible)"),
}

# Approximate bitrates for each VBR quality level (0 = best, 9 = smallest).
VBR_QUALITY_BITRATE_HINTS: dict[str, dict[int, str]] = {
    # LAME -V levels, stereo music figures.
    "mp3": {
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
    },
    # FFmpeg AAC (see AAC_VBR_QUALITY_SCALE), measured on mono speech.
    "m4b": {
        0: "190 kbps",
        1: "160 kbps",
        2: "125 kbps",
        3: "110 kbps",
        4: "92 kbps",
        5: "86 kbps",
        6: "73 kbps",
        7: "60 kbps",
        8: "54 kbps, less treble",
        9: "43 kbps, less treble",
    },
}

CBR_BITRATE_RANGE: dict[str, tuple[int, int]] = {"mp3": (32, 320), "m4b": (24, 320)}

# Defaults used until the user saves their own in Tools -> Options.
DEFAULT_ENCODE_SETTINGS: dict[str, Any] = {
    "format": "mp3",
    "channels": 1,
    "sizeLimitText": "1000000000",
    "sizeLimitUnit": "B",
    "previewDevice": "",
    "mp3/useVbr": True,
    "mp3/vbrQuality": 5,
    "mp3/cbrKbps": 64,
    "m4b/useVbr": True,
    "m4b/vbrQuality": 7,
    "m4b/cbrKbps": 48,
}


def _format_duration_label(duration_sec: float) -> str:
    total_seconds = max(0, int(duration_sec))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _chapter_key(chapter: Chapter) -> str:
    return f"{chapter.start_ms}:{chapter.end_ms}"


def _format_counter_label(elapsed_sec: float, duration_sec: float) -> str:
    return f"{_format_duration_label(elapsed_sec)} / {_format_duration_label(duration_sec)}"


def _sequential_timeline(chapters: list[Chapter]) -> list[Chapter]:
    """Lay chapters back to back from 0 ms in list order, keeping each duration."""
    timeline: list[Chapter] = []
    cursor_ms = 0
    for row, chapter in enumerate(chapters, start=1):
        end_ms = cursor_ms + max(1, chapter.end_ms - chapter.start_ms)
        timeline.append(
            Chapter(index=row, title=chapter.title, start_ms=cursor_ms, end_ms=end_ms)
        )
        cursor_ms = end_ms
    return timeline


class ChapterProgressDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if painter is None:
            return
        preview_progress = index.data(CHAPTER_PREVIEW_PROGRESS_ROLE)
        fill_color = QColor(70, 140, 90, 140)
        if isinstance(preview_progress, (int, float)) and preview_progress > 0:
            progress_data = preview_progress
            fill_color = QColor(60, 120, 200, 165)
        else:
            progress_data = index.data(CHAPTER_PROGRESS_ROLE)
        if not isinstance(progress_data, (int, float)) or progress_data <= 0:
            super().paint(painter, option, index)
            return

        progress = max(0.0, min(1.0, float(progress_data)))
        paint_option = QStyleOptionViewItem(option)
        self.initStyleOption(paint_option, index)
        text = paint_option.text
        paint_option.text = ""

        style = paint_option.widget.style() if paint_option.widget else QApplication.style()
        if style is None:
            super().paint(painter, option, index)
            return
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, paint_option, painter, paint_option.widget)

        content_rect = paint_option.rect.adjusted(2, 2, -2, -2)
        if content_rect.width() > 0 and content_rect.height() > 0:
            fill_width = max(1, int(content_rect.width() * progress))
            fill_rect = content_rect.adjusted(0, 0, -(content_rect.width() - fill_width), 0)
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill_color)
            painter.drawRect(fill_rect)
            painter.restore()

        painter.save()
        text_rect = content_rect.adjusted(6, 0, -6, 0)
        if paint_option.state & QStyle.StateFlag.State_Selected:
            text_role = paint_option.palette.ColorRole.HighlightedText
        else:
            text_role = paint_option.palette.ColorRole.Text
        painter.setPen(paint_option.palette.color(text_role))
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        painter.restore()


class ReorderableTableWidget(QTableWidget):
    rowMoveRequested = pyqtSignal(int, int)
    # row, fraction (0-1) of the Title cell width that was clicked
    previewSeekRequested = pyqtSignal(int, float)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._drag_row = -1

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        seek: tuple[int, float] | None = None
        if e is not None and e.button() == Qt.MouseButton.LeftButton:
            pt = e.position().toPoint()
            self._drag_row = self.rowAt(pt.y())
            model = self.model()
            if self._drag_row >= 0 and self.columnAt(pt.x()) == 1 and model is not None:
                rect = self.visualRect(model.index(self._drag_row, 1))
                if rect.width() > 0:
                    fraction = (pt.x() - rect.left()) / rect.width()
                    seek = (self._drag_row, max(0.0, min(1.0, fraction)))
        super().mousePressEvent(e)
        if seek is not None:
            self.previewSeekRequested.emit(*seek)

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        source_row = self._drag_row
        self._drag_row = -1
        if source_row < 0:
            event.ignore()
            return

        drop_pos = event.position().toPoint()
        target_row = self.rowAt(drop_pos.y())
        if target_row < 0:
            target_row = self.rowCount()
        elif self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
            target_row += 1

        self.rowMoveRequested.emit(source_row, target_row)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


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
    """Find a cover image next to a source WAV: '<Book Title>.*' first, then 'Cover.*'."""
    search_names = [book_title.casefold(), "cover"] if book_title else ["cover"]
    covers = _find_covers_by_title(source_file.parent)
    for name in search_names:
        if name in covers:
            return covers[name]
    return None


PLAYLIST_HEADER = "# AudioBookSlicer playlist v1"


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


def _kill_process_tree(pid: int) -> bool:
    """Force-kill a process and its children on Windows. Returns True if attempted."""
    if sys.platform != "win32" or pid <= 0:
        return False
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True
    except OSError:
        return False


def _discard_intermediate(wav_path: Path | None) -> None:
    """Delete an intermediate WAV and the private temp folder that holds it."""
    if wav_path is None:
        return
    folder = wav_path.parent
    if folder.name.startswith(INTERMEDIATE_DIR_PREFIX):
        shutil.rmtree(folder, ignore_errors=True)
    else:
        wav_path.unlink(missing_ok=True)


class EncodeWorker(QObject):
    progress = pyqtSignal(int, str, float)
    # message, output size in bytes, path of a newly generated intermediate WAV ("" if none)
    finished = pyqtSignal(str, int, str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        output_path: Path,
        output_format: str,
        book_title: str,
        chapters: list[Chapter],
        channels: int,
        vbr_quality: int,
        cbr_bitrate: str | None,
        cover_map: dict[str, Path],
        input_wav: Path | None = None,
        source_files: list[Path] | None = None,
        reuse_intermediate: Path | None = None,
    ) -> None:
        super().__init__()
        self._output_path = output_path
        self._output_format = output_format
        self._book_title = book_title
        self._chapters = list(chapters)
        self._channels = channels
        self._vbr_quality = vbr_quality
        self._cbr_bitrate = cbr_bitrate
        self._cover_map = dict(cover_map)
        self._input_wav = input_wav
        self._source_files = list(source_files or [])
        self._reuse_intermediate = reuse_intermediate
        self._cancel_requested = False
        self._active_process: subprocess.Popen[str] | None = None

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self._force_stop_active_process()

    def _set_active_process(self, process: subprocess.Popen[str]) -> None:
        self._active_process = process

    def _clear_active_process(self) -> None:
        self._active_process = None

    def _force_stop_active_process(self) -> None:
        process = self._active_process
        if process is None or process.poll() is not None:
            return
        if not _kill_process_tree(int(process.pid)):
            process.kill()

    def _run_ffmpeg_cancellable(self, cmd: list[str], failure_prefix: str) -> None:
        with tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._set_active_process(process)  # type: ignore[arg-type]
            try:
                while True:
                    if self._cancel_requested:
                        self._force_stop_active_process()
                        process.wait()
                        raise SystemExit
                    try:
                        return_code = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                self._clear_active_process()

            stderr_file.seek(0)
            stderr_text = stderr_file.read().decode("utf-8", errors="replace").strip()

        if return_code != 0:
            raise SystemExit(f"{failure_prefix} ffmpeg stderr: {stderr_text or '(empty)'}")

    def _concatenate_source_files(self, ffmpeg_bin: str, output_wav: Path) -> None:
        """Concatenate the individual chapter WAVs, in table order, into one WAV."""
        concat_file = output_wav.with_name("concat.txt")
        # concat demuxer quoting: close the quote, emit an escaped quote, reopen.
        concat_lines = [
            "file '{}'".format(str(path).replace("'", "'\\''"))
            for path in self._source_files
        ]
        concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_wav),
        ]
        self._run_ffmpeg_cancellable(cmd, "WAV concatenation failed.")
        concat_file.unlink(missing_ok=True)

    def _build_reordered_wav(
        self,
        ffmpeg_bin: str,
        input_wav: Path,
        chapters: list[Chapter],
        output_wav: Path,
    ) -> None:
        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for idx, ch in enumerate(chapters):
            start_sec = f"{ch.start_ms / 1000.0:.6f}"
            end_sec = f"{ch.end_ms / 1000.0:.6f}"
            label = f"c{idx}"
            filter_parts.append(
                f"[0:a]atrim=start={start_sec}:end={end_sec},asetpts=PTS-STARTPTS[{label}]"
            )
            concat_inputs.append(f"[{label}]")

        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(chapters)}:v=0:a=1[outa]"
        )
        filter_complex = ";".join(filter_parts)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_wav),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outa]",
            "-c:a",
            "pcm_s16le",
            str(output_wav),
        ]
        self._run_ffmpeg_cancellable(cmd, "WAV chapter reordering failed.")

    def run(self) -> None:
        stderr_buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = stderr_buf
        generated: Path | None = None
        succeeded = False
        try:
            self.progress.emit(0, "Preparing encoder…", -1.0)
            ffmpeg_bin = ensure_executable("ffmpeg")
            ffprobe_bin = ensure_executable("ffprobe")

            chapters = list(self._chapters)
            if self._output_format == "mp3":
                # MP3 chapters each embed artwork, so every book title needs a cover.
                missing_titles = sorted(
                    {
                        title
                        for ch in chapters
                        if (title := _book_title_from_chapter(ch.title) or ch.title)
                        not in self._cover_map
                    },
                    key=str.casefold,
                )
                if missing_titles:
                    raise SystemExit("Cover image missing for: " + ", ".join(missing_titles))

            if self._reuse_intermediate is not None:
                if not self._reuse_intermediate.exists():
                    raise SystemExit(
                        f"Saved intermediate WAV is missing: {self._reuse_intermediate}"
                    )
                self.progress.emit(2, "Using saved intermediate WAV…", -1.0)
                input_for_encode = self._reuse_intermediate
            elif self._source_files:
                self.progress.emit(2, "Concatenating WAV files…", -1.0)
                generated = Path(tempfile.mkdtemp(prefix=INTERMEDIATE_DIR_PREFIX)) / "intermediate.wav"
                self._concatenate_source_files(ffmpeg_bin, generated)
                input_for_encode = generated
            else:
                assert self._input_wav is not None
                input_for_encode = self._input_wav
                needs_reorder = any(
                    chapters[i].start_ms > chapters[i + 1].start_ms
                    for i in range(len(chapters) - 1)
                )
                if needs_reorder:
                    self.progress.emit(4, "Reordering chapters in WAV…", -1.0)
                    generated = Path(tempfile.mkdtemp(prefix=INTERMEDIATE_DIR_PREFIX)) / "intermediate.wav"
                    self._build_reordered_wav(ffmpeg_bin, input_for_encode, chapters, generated)
                    input_for_encode = generated

            if input_for_encode != self._input_wav:
                # The intermediate holds the chapters back to back in table order.
                chapters = _sequential_timeline(chapters)
            if self._cancel_requested:
                raise SystemExit

            sample_rate, _markers = read_riff_chunks(input_for_encode)
            duration_sec, _ = probe_audio_info(ffprobe_bin, input_for_encode)

            self.progress.emit(5, "Encoding audio…", -1.0)

            def _progress_callback(percent: float, status_line: str, out_time_sec: float) -> None:
                pct = int(max(0.0, min(100.0, percent)))
                self.progress.emit(pct, f"Encoding… {status_line.strip()}", out_time_sec)

            actual_size_bytes = encode_output(
                ffmpeg_bin=ffmpeg_bin,
                input_file=input_for_encode,
                chapters=chapters,
                duration_sec=duration_sec,
                output_file=self._output_path,
                channels=self._channels,
                vbr_quality=self._vbr_quality,
                cbr_bitrate=self._cbr_bitrate,
                cover_map=self._cover_map,
                progress_callback=_progress_callback,
                cancel_requested=lambda: self._cancel_requested,
                on_process_started=self._set_active_process,
                on_process_finished=self._clear_active_process,
                sample_rate=sample_rate,
                output_format=self._output_format,
                book_title=self._book_title or None,
            )
            self.progress.emit(100, "Encoding complete", -1.0)
            succeeded = True
            self.finished.emit(
                f"Created: {self._output_path} ({format_bytes(actual_size_bytes)})",
                actual_size_bytes,
                str(generated) if generated is not None else "",
            )
        except SystemExit as exc:
            if self._cancel_requested:
                self.failed.emit("Encode cancelled by user.")
            else:
                msg = stderr_buf.getvalue().strip()
                if msg.startswith("ERROR: "):
                    msg = msg[len("ERROR: "):]
                if not msg and isinstance(exc.code, str):
                    msg = exc.code
                self.failed.emit(msg or "Encoding failed.")
        except Exception as exc:
            self.failed.emit(str(exc) or "Unexpected encoding error.")
        finally:
            if not succeeded and generated is not None:
                # ffmpeg may hold the file briefly after a force-kill.
                for _ in range(20):
                    _discard_intermediate(generated)
                    if not generated.parent.exists():
                        break
                    time.sleep(0.1)
            sys.stderr = old_stderr


def _load_encode_defaults(settings: QSettings) -> dict[str, Any]:
    """Read the saved default encode settings, falling back to built-in defaults."""
    values: dict[str, Any] = {}
    for key, default in DEFAULT_ENCODE_SETTINGS.items():
        raw = settings.value(f"options/{key}", default, type=type(default))
        values[key] = raw if raw is not None else default
    if values["format"] not in OUTPUT_FORMATS:
        values["format"] = DEFAULT_ENCODE_SETTINGS["format"]
    if values["channels"] not in (1, 2):
        values["channels"] = 1
    if values["sizeLimitUnit"] not in SIZE_UNITS:
        values["sizeLimitUnit"] = DEFAULT_ENCODE_SETTINGS["sizeLimitUnit"]
    for fmt, (low, high) in CBR_BITRATE_RANGE.items():
        values[f"{fmt}/vbrQuality"] = max(0, min(9, int(values[f"{fmt}/vbrQuality"])))
        values[f"{fmt}/cbrKbps"] = max(low, min(high, int(values[f"{fmt}/cbrKbps"])))
    return values


def _save_encode_defaults(settings: QSettings, values: dict[str, Any]) -> None:
    for key in DEFAULT_ENCODE_SETTINGS:
        settings.setValue(f"options/{key}", values[key])


class EncodeSettingsWidget(QWidget):
    """Output format, VBR/CBR, quality, channels and size limit controls.

    Each format remembers its own VBR/CBR choice, quality level and bitrate, so
    switching between MP3 and M4B restores that format's last settings.
    """

    formatChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._format = "mp3"
        self._per_format: dict[str, dict[str, Any]] = {
            fmt: {
                "useVbr": DEFAULT_ENCODE_SETTINGS[f"{fmt}/useVbr"],
                "vbrQuality": DEFAULT_ENCODE_SETTINGS[f"{fmt}/vbrQuality"],
                "cbrKbps": DEFAULT_ENCODE_SETTINGS[f"{fmt}/cbrKbps"],
            }
            for fmt in OUTPUT_FORMATS
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        def _row(label_text: str) -> QHBoxLayout:
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setFixedWidth(90)
            row.addWidget(label)
            layout.addLayout(row)
            return row

        format_row = _row("Format")
        self._format_group = QButtonGroup(self)
        self._format_radios: dict[str, QRadioButton] = {}
        for fmt, (_short, description) in OUTPUT_FORMATS.items():
            radio = QRadioButton(description)
            self._format_group.addButton(radio)
            self._format_radios[fmt] = radio
            format_row.addWidget(radio)
            radio.toggled.connect(self._on_format_toggled)
        format_row.addStretch()
        self._format_radios["m4b"].setToolTip(
            "AAC audio in an MP4 audiobook container. Chapters are written as a "
            "QuickTime chapter track, which iPods and Apple Books read.\n"
            "M4B holds one cover image for the whole book (the first chapter's cover)."
        )

        encoding_row = _row("Encoding")
        self._vbr_radio = QRadioButton("VBR")
        self._cbr_radio = QRadioButton("CBR")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._vbr_radio)
        self._mode_group.addButton(self._cbr_radio)
        encoding_row.addWidget(self._vbr_radio)
        encoding_row.addWidget(self._cbr_radio)

        self._vbr_quality_label = QLabel("quality level")
        encoding_row.addWidget(self._vbr_quality_label)
        self._vbr_q_combo = QComboBox()
        self._vbr_q_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        encoding_row.addWidget(self._vbr_q_combo)

        self._cbr_bitrate_label = QLabel("kbps")
        encoding_row.addWidget(self._cbr_bitrate_label)
        self._cbr_bitrate_spin = QSpinBox()
        self._cbr_bitrate_spin.setSingleStep(8)
        self._cbr_bitrate_spin.setFixedWidth(88)
        encoding_row.addWidget(self._cbr_bitrate_spin)
        encoding_row.addStretch()
        self._vbr_radio.toggled.connect(self._update_mode_visibility)

        channels_row = _row("Channels")
        self._mono_radio = QRadioButton("Mono")
        self._stereo_radio = QRadioButton("Stereo")
        self._channel_group = QButtonGroup(self)
        self._channel_group.addButton(self._mono_radio)
        self._channel_group.addButton(self._stereo_radio)
        channels_row.addWidget(self._mono_radio)
        channels_row.addWidget(self._stereo_radio)
        channels_row.addStretch()

        size_limit_row = _row("Size Limit")
        size_tip = "e.g. 1,500,000,000 (blank or 0 = no limit)"
        self._size_limit_edit = QLineEdit()
        self._size_limit_edit.setToolTip(size_tip)
        self._size_limit_edit.setFixedWidth(185)
        size_limit_row.addWidget(self._size_limit_edit)
        self._size_unit_group = QButtonGroup(self)
        self._size_unit_radios: dict[str, QRadioButton] = {}
        for unit in SIZE_UNITS:
            radio = QRadioButton(unit)
            self._size_unit_group.addButton(radio)
            self._size_unit_radios[unit] = radio
            size_limit_row.addWidget(radio)
        size_limit_row.addStretch()

        self.set_settings(DEFAULT_ENCODE_SETTINGS)

    # ── Per-format state ──────────────────────────────────────────────────────

    def _store_format_controls(self) -> None:
        self._per_format[self._format] = {
            "useVbr": self._vbr_radio.isChecked(),
            "vbrQuality": int(self._vbr_q_combo.currentData()),
            "cbrKbps": int(self._cbr_bitrate_spin.value()),
        }

    def _load_format_controls(self) -> None:
        fmt = self._format
        values = self._per_format[fmt]
        hints = VBR_QUALITY_BITRATE_HINTS[fmt]
        self._vbr_q_combo.blockSignals(True)
        self._vbr_q_combo.clear()
        for level in range(10):
            self._vbr_q_combo.addItem(f"{level} (~{hints[level]})", level)
        self._vbr_q_combo.setCurrentIndex(int(values["vbrQuality"]))
        self._vbr_q_combo.blockSignals(False)
        self._vbr_q_combo.setToolTip(
            "0 = best quality / largest file, 9 = smallest file.\n"
            + (
                "Bitrates are typical for LAME VBR on stereo music; mono speech is lower."
                if fmt == "mp3"
                else "Bitrates were measured on mono speech; stereo is higher."
            )
        )
        low, high = CBR_BITRATE_RANGE[fmt]
        self._cbr_bitrate_spin.setRange(low, high)
        self._cbr_bitrate_spin.setValue(int(values["cbrKbps"]))
        if values["useVbr"]:
            self._vbr_radio.setChecked(True)
        else:
            self._cbr_radio.setChecked(True)
        self._update_mode_visibility()

    def _on_format_toggled(self, checked: bool) -> None:
        if not checked:
            return
        new_format = next(f for f, r in self._format_radios.items() if r.isChecked())
        if new_format == self._format:
            return
        self._store_format_controls()
        self._format = new_format
        self._load_format_controls()
        self.formatChanged.emit(new_format)

    def _update_mode_visibility(self) -> None:
        use_vbr = self._vbr_radio.isChecked()
        self._vbr_quality_label.setVisible(use_vbr)
        self._vbr_q_combo.setVisible(use_vbr)
        self._cbr_bitrate_label.setVisible(not use_vbr)
        self._cbr_bitrate_spin.setVisible(not use_vbr)

    # ── Public API ────────────────────────────────────────────────────────────

    def output_format(self) -> str:
        return self._format

    def channels(self) -> int:
        return 2 if self._stereo_radio.isChecked() else 1

    def vbr_quality(self) -> int:
        return int(self._vbr_q_combo.currentData())

    def cbr_bitrate(self) -> str | None:
        """ffmpeg -b:a value in CBR mode, or None in VBR mode."""
        if self._vbr_radio.isChecked():
            return None
        return f"{self._cbr_bitrate_spin.value()}k"

    def size_limit(self) -> tuple[Decimal, str] | None:
        text = self._size_limit_edit.text().replace(",", "").replace(" ", "").strip()
        if not text:
            return None
        try:
            value = Decimal(text)
        except InvalidOperation:
            return None
        if value <= Decimal("0"):
            return None
        checked = self._size_unit_group.checkedButton()
        if checked is None:
            return None
        return value, checked.text()

    def settings(self) -> dict[str, Any]:
        self._store_format_controls()
        checked_unit = self._size_unit_group.checkedButton()
        values: dict[str, Any] = {
            "format": self._format,
            "channels": self.channels(),
            "sizeLimitText": self._size_limit_edit.text().strip(),
            "sizeLimitUnit": checked_unit.text() if checked_unit is not None else "B",
        }
        for fmt, fmt_values in self._per_format.items():
            for key, value in fmt_values.items():
                values[f"{fmt}/{key}"] = value
        return values

    def set_settings(self, values: dict[str, Any]) -> None:
        for fmt in OUTPUT_FORMATS:
            self._per_format[fmt] = {
                key: values.get(f"{fmt}/{key}", DEFAULT_ENCODE_SETTINGS[f"{fmt}/{key}"])
                for key in ("useVbr", "vbrQuality", "cbrKbps")
            }
        new_format = values.get("format", "mp3")
        previous_format = self._format
        self._format = new_format
        radio = self._format_radios[new_format]
        radio.blockSignals(True)
        radio.setChecked(True)
        radio.blockSignals(False)
        self._load_format_controls()
        if values.get("channels", 1) == 2:
            self._stereo_radio.setChecked(True)
        else:
            self._mono_radio.setChecked(True)
        self._size_limit_edit.setText(str(values.get("sizeLimitText", "")))
        unit_radio = self._size_unit_radios.get(str(values.get("sizeLimitUnit", "B")))
        if unit_radio is not None:
            unit_radio.setChecked(True)
        if new_format != previous_format:
            self.formatChanged.emit(new_format)


class OptionsDialog(QDialog):
    """Default encode settings (used at startup and on Reset) and the preview device."""

    def __init__(self, values: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(620)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        intro = QLabel(
            "Default encode settings. They are loaded when the app starts and when you "
            "choose File → Reset. Clicking OK also applies them to the current window."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._encode_settings = EncodeSettingsWidget(self)
        self._encode_settings.set_settings(values)
        root.addWidget(self._encode_settings)

        device_row = QHBoxLayout()
        device_label = QLabel("Preview Device")
        device_label.setFixedWidth(90)
        device_row.addWidget(device_label)
        self._preview_device_combo = QComboBox()
        self._preview_device_combo.setMinimumWidth(320)
        self._preview_device_combo.setToolTip("Audio output used for chapter preview playback.")
        device_row.addWidget(self._preview_device_combo)
        refresh_btn = QPushButton("Refresh Devices")
        refresh_btn.clicked.connect(self._on_refresh_devices_clicked)
        device_row.addWidget(refresh_btn)
        device_row.addStretch()
        root.addLayout(device_row)
        self._populate_preview_devices(str(values.get("previewDevice", "")))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        restore_btn = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        if restore_btn is not None:
            restore_btn.clicked.connect(self._on_restore_defaults)
        root.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        values = self._encode_settings.settings()
        device = self._preview_device_combo.currentData()
        values["previewDevice"] = str(device).strip() if device is not None else ""
        return values

    def _on_restore_defaults(self) -> None:
        self._encode_settings.set_settings(DEFAULT_ENCODE_SETTINGS)
        self._populate_preview_devices("")

    def _populate_preview_devices(self, selected_device: str) -> None:
        combo = self._preview_device_combo
        combo.clear()
        default_label = "System Default"
        if _has_qt_multimedia:
            try:
                default_name = QMediaDevices.defaultAudioOutput().description().strip()
                if default_name:
                    default_label = f"System Default ({default_name})"
                names: list[str] = []
                for device in QMediaDevices.audioOutputs():
                    name = device.description().strip()
                    if name and name not in names:
                        names.append(name)
                        combo.addItem(name, name)
            except Exception:
                pass
        # An empty value means "follow the system default output".
        combo.insertItem(0, default_label, "")

        target = selected_device.strip()
        if not target:
            combo.setCurrentIndex(0)
            return
        idx = combo.findData(target)
        if idx < 0:
            combo.addItem(f"{target} (Unavailable)", target)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)

    def _on_refresh_devices_clicked(self) -> None:
        current = self._preview_device_combo.currentData()
        self._populate_preview_devices(str(current).strip() if current is not None else "")


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon_pixmap = QPixmap(str(_HERE / "icon.png"))
        if not icon_pixmap.isNull():
            icon_label.setPixmap(
                icon_pixmap.scaled(
                    128,
                    128,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        root.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(10)
        title_label = QLabel(APP_NAME)
        title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        text_layout.addWidget(title_label)

        body_label = QLabel(
            "AudioBook Slicer is a desktop utility for building chaptered audiobooks.\n\n"
            "You can split M4B files into chapter WAVs, load an Adobe Audition WAV with markers "
            "or many individual WAV files, save and reopen the chapter order as a playlist, "
            "reorder and rename chapters, preview them, manage chapter artwork, and encode a "
            "final MP3 or iPod-compatible M4B with chapter metadata.\n\n"
            "Long operations stay visible and can be cancelled at any time."
        )
        body_label.setWordWrap(True)
        body_label.setMinimumWidth(430)
        text_layout.addWidget(body_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        text_layout.addWidget(buttons)
        root.addLayout(text_layout, 1)
        self.setFixedSize(self.sizeHint())


class ToolRunDialog(QDialog):
    """Runs one of the helper scripts and streams its console output.

    When apply_flag is given, the script is first run in preview mode; after a
    successful preview the "Apply Changes" button re-runs it with that flag.
    """

    def __init__(
        self,
        parent: QWidget,
        title: str,
        script: str,
        args: list[str],
        *,
        working_dir: Path,
        apply_flag: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(820, 480)

        self._script = _HERE / script
        self._args = list(args)
        self._apply_flag = apply_flag
        self._applied = False

        layout = QVBoxLayout(self)

        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self._output)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.setVisible(apply_flag is not None)
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply_clicked)
        buttons.addWidget(self._apply_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)
        buttons.addWidget(self._close_btn)
        layout.addLayout(buttons)

        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(working_dir))
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        self._process.setProcessEnvironment(env)
        self._process.readyReadStandardOutput.connect(self._on_output_ready)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)

    def start(self) -> None:
        self._run(self._args)

    def _run(self, args: list[str]) -> None:
        if _IS_FROZEN:
            # The bundled exe has no Python interpreter to hand the script to, so it
            # relaunches itself in tool mode (see main()).
            full_args = [TOOL_WORKER_FLAG, self._script.stem, *args]
            shown = f"> {self._script.name} {subprocess.list2cmdline(args)}\n\n"
        else:
            full_args = [str(self._script), *args]
            shown = f"> python {subprocess.list2cmdline(full_args)}\n\n"
        self._append(shown)
        self._apply_btn.setEnabled(False)
        self._process.start(sys.executable, full_args)

    def _append(self, text: str) -> None:
        self._output.moveCursor(self._output.textCursor().MoveOperation.End)
        self._output.insertPlainText(text)
        self._output.ensureCursorVisible()

    def _on_output_ready(self) -> None:
        data = bytes(self._process.readAllStandardOutput().data())
        self._append(data.decode("utf-8", errors="replace").replace("\r\n", "\n"))

    def _on_process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._append(f"\n[Process exited with code {exit_code}]\n")
        if self._apply_flag is None or self._applied:
            return
        if exit_code == 0:
            self._apply_btn.setEnabled(True)
            self._append("\nReview the preview above, then click Apply Changes to rename the files.\n")

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._append(f"\n[Failed to start: {self._process.errorString()}]\n")

    def _on_apply_clicked(self) -> None:
        if self._apply_flag is None or self._applied:
            return
        self._applied = True
        self._append("\n" + "─" * 60 + "\n")
        self._run([*self._args, self._apply_flag])

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            reply = QMessageBox.question(
                self,
                "Tool Still Running",
                "The tool is still running. Stop it and close this window?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                if a0 is not None:
                    a0.ignore()
                return
            self._process.kill()
            self._process.waitForFinished(3000)
        super().closeEvent(a0)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon_path = _HERE / "icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(900, 640)

        self._wav_path: Path | None = None
        self._output_dir: Path | None = None
        self._chapters: list[Chapter] = []
        self._original_chapters: list[Chapter] = []
        # Playlist mode: one source WAV per chapter row (empty in Audition-WAV mode).
        self._source_files: list[Path] = []
        self._original_source_files: list[Path] = []
        self._playlist_path: Path | None = None
        # Source order as last opened from / saved to a playlist file.
        self._saved_playlist_files: list[Path] = []
        self._missing_cover_rows: set[int] = set()
        self._row_cover_paths: dict[int, Path] = {}
        self._preview_base_pixmap: QPixmap | None = None
        self._next_cover_action = "all"
        self._encode_thread: QThread | None = None
        self._encode_worker: EncodeWorker | None = None
        self._initial_splitter_sizes: tuple[int, int] = (220, 420)
        self._initial_splitter_ratio: float | None = None
        self._initial_splitter_top_px: int | None = None
        self._splitter_reset_pending = False
        self._encode_progress_row: int | None = None
        self._encode_progress_timeline: list[Chapter] = []
        self._is_populating_table = False
        self._was_maximized = self.isMaximized()
        self._remembered_conflict_path: Path | None = None
        self._remembered_conflict_choice: str | None = None
        self._settings = QSettings("AudioBookSlicer", "AudioBookSlicer")
        self._encode_defaults = _load_encode_defaults(self._settings)

        # Intermediate WAV (joined/reordered chapters) kept for re-encodes.
        self._cached_intermediate: Path | None = None
        self._cached_intermediate_signature: str | None = None

        # Chapter preview playback
        self._preview_player: Any = None
        self._preview_audio_output: Any = None
        self._preview_process: subprocess.Popen[bytes] | None = None
        self._preview_row: int | None = None
        self._preview_duration_sec = 0.0
        self._preview_start_offset_ms = 0
        self._preview_end_offset_ms = 0
        self._preview_started_monotonic = 0.0
        self._preview_wait_start_deadline = 0.0
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._on_preview_timer_tick)
        if _has_qt_multimedia:
            self._preview_player = QMediaPlayer(self)
            self._preview_audio_output = QAudioOutput(self)
            self._preview_player.setAudioOutput(self._preview_audio_output)

        # ── Central widget ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._build_menu_bar()

        # ── Output settings + cover preview ─────────────────────────────────
        top_settings_row = QHBoxLayout()
        top_settings_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        top_settings_row.setContentsMargins(0, 0, 0, 0)
        top_settings_row.setSpacing(6)

        settings_left = QVBoxLayout()
        settings_left.setSpacing(6)
        settings_left.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_row = QHBoxLayout()

        title_label = QLabel("Book Title")
        title_label.setFixedWidth(90)
        title_row.addWidget(title_label)

        self._book_title_edit = QLineEdit()
        self._book_title_edit.setPlaceholderText("Book title (output file name and tags)")
        self._book_title_edit.textChanged.connect(self._on_book_title_changed)
        title_row.addWidget(self._book_title_edit)

        settings_left.addLayout(title_row)

        output_row = QHBoxLayout()

        output_label = QLabel("Output File")
        output_label.setFixedWidth(90)
        output_row.addWidget(output_label)

        self._output_path_edit = QLineEdit()
        self._output_path_edit.setReadOnly(True)
        output_row.addWidget(self._output_path_edit)

        self._save_as_btn = QPushButton("Save As")
        self._save_as_btn.setEnabled(False)
        self._save_as_btn.clicked.connect(self._on_save_as)
        output_row.addWidget(self._save_as_btn)

        settings_left.addLayout(output_row)

        duration_row = QHBoxLayout()

        duration_label = QLabel("Duration")
        duration_label.setFixedWidth(90)
        duration_row.addWidget(duration_label)

        self._duration_edit = QLineEdit()
        self._duration_edit.setReadOnly(True)
        duration_row.addWidget(self._duration_edit)
        duration_row.addStretch()

        settings_left.addLayout(duration_row)

        preview_row = QHBoxLayout()

        preview_label = QLabel("Preview")
        preview_label.setFixedWidth(90)
        preview_row.addWidget(preview_label)

        self._preview_btn = QPushButton("Start Preview")
        self._preview_btn.setEnabled(False)
        self._preview_btn.setToolTip(
            "Play the selected chapter. Playback continues into the next chapter.\n"
            "Click inside the playing chapter's title to seek."
        )
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        preview_row.addWidget(self._preview_btn)

        self._preview_counter_label = QLabel(_format_counter_label(0.0, 0.0))
        preview_row.addWidget(self._preview_counter_label)
        preview_row.addStretch()

        settings_left.addLayout(preview_row)

        self._encode_settings = EncodeSettingsWidget()
        self._encode_settings.set_settings(self._encode_defaults)
        self._encode_settings.formatChanged.connect(self._on_output_format_changed)
        settings_left.addWidget(self._encode_settings)

        encode_row = QHBoxLayout()

        encode_label = QLabel("")
        encode_label.setFixedWidth(90)
        encode_row.addWidget(encode_label)

        self._encode_btn = QPushButton("Encode MP3")
        self._encode_btn.setEnabled(False)
        self._encode_btn.clicked.connect(self._on_encode_clicked)
        self._encode_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #388e3c; }"
            "QPushButton:disabled { background-color: #9e9e9e; color: #e0e0e0; }"
        )
        encode_row.addWidget(self._encode_btn)

        self._encode_progress = QProgressBar()
        self._encode_progress.setRange(0, 100)
        self._encode_progress.setValue(0)
        self._encode_progress.setTextVisible(True)
        self._encode_progress.setFormat("%p%")
        self._encode_progress.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        encode_row.addWidget(self._encode_progress)

        settings_left.addLayout(encode_row)
        settings_left.addStretch(1)
        top_settings_row.addLayout(settings_left, 3)

        preview_right = QVBoxLayout()
        preview_right.setContentsMargins(0, 0, 0, 0)
        preview_right.setSpacing(0)
        preview_right.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        preview_label = QLabel("Cover Preview")
        preview_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        preview_right.addWidget(preview_label)

        self._cover_preview = QLabel("No cover selected")
        self._cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_preview.setMinimumSize(220, 120)
        self._cover_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        preview_right.addWidget(self._cover_preview, 1)

        top_settings_row.addLayout(preview_right, 1)
        top_panel = QWidget()
        top_panel_layout = QVBoxLayout(top_panel)
        top_panel_layout.setContentsMargins(0, 0, 0, 0)
        top_panel_layout.setSpacing(0)
        top_panel_layout.addLayout(top_settings_row)

        # ── Chapter table ─────────────────────────────────────────────────────
        self._table = ReorderableTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["#", "Title", "Start", "End", "Cover"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setDragEnabled(True)
        self._table.setAcceptDrops(True)
        self._table.setDropIndicatorShown(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.setDragDropOverwriteMode(False)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.rowMoveRequested.connect(self._on_table_row_move_requested)
        self._table.previewSeekRequested.connect(self._on_preview_seek_requested)
        self._table.setItemDelegateForColumn(1, ChapterProgressDelegate(self._table))
        vh = self._table.verticalHeader()
        if vh is not None:
            vh.setVisible(False)

        hdr = self._table.horizontalHeader()
        if hdr is None:
            raise RuntimeError("Table has no horizontal header")
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(4, THUMB_SIZE + 8)

        self._covers_btn = QPushButton("Process Covers")
        self._covers_btn.setEnabled(False)
        self._covers_btn.clicked.connect(self._on_process_covers)

        bottom_panel = QWidget()
        bottom_panel_layout = QVBoxLayout(bottom_panel)
        bottom_panel_layout.setContentsMargins(0, 0, 0, 0)
        bottom_panel_layout.setSpacing(0)
        bottom_panel_layout.addWidget(self._table)

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(8)
        self._main_splitter.addWidget(top_panel)
        self._main_splitter.addWidget(bottom_panel)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes(list(self._initial_splitter_sizes))
        self._main_splitter.splitterMoved.connect(self._on_main_splitter_moved)

        root.addWidget(self._main_splitter)

        # Capture the true initial splitter ratio after Qt applies final layout.
        QTimer.singleShot(0, self._capture_initial_splitter_ratio)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.addPermanentWidget(self._covers_btn)
        self._status.showMessage("Open a WAV file to begin.")
        self._on_output_format_changed(self._encode_settings.output_format())

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        assert menu_bar is not None

        file_menu = menu_bar.addMenu("&File")
        assert file_menu is not None

        self._open_action = QAction("&Import Adobe Audition WAV File…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self._on_open_wav)
        file_menu.addAction(self._open_action)

        self._add_files_action = QAction("&Add WAV Files…", self)
        self._add_files_action.triggered.connect(self._on_add_wav_files)
        file_menu.addAction(self._add_files_action)

        file_menu.addSeparator()

        self._open_playlist_action = QAction("Open &Playlist…", self)
        self._open_playlist_action.triggered.connect(self._on_open_playlist)
        file_menu.addAction(self._open_playlist_action)

        self._save_playlist_action = QAction("&Save Playlist…", self)
        self._save_playlist_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_playlist_action.setEnabled(False)
        self._save_playlist_action.triggered.connect(self._on_save_playlist)
        file_menu.addAction(self._save_playlist_action)

        file_menu.addSeparator()

        self._remove_chapter_action = QAction("Remove Selected &Chapter", self)
        self._remove_chapter_action.setEnabled(False)
        self._remove_chapter_action.triggered.connect(self._on_remove_selected_chapter)
        file_menu.addAction(self._remove_chapter_action)

        self._reset_markers_action = QAction("Reset &Markers", self)
        self._reset_markers_action.setEnabled(False)
        self._reset_markers_action.triggered.connect(self._on_reset_markers_clicked)
        file_menu.addAction(self._reset_markers_action)

        self._reset_action = QAction("&Reset", self)
        self._reset_action.triggered.connect(self._on_reset_clicked)
        file_menu.addAction(self._reset_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menu_bar.addMenu("&Tools")
        assert tools_menu is not None
        tools = [
            ("Prefix Book Title to Files…", self._on_tool_prefix_book_title),
            ("Offset Chapter Numbers…", self._on_tool_rename_chapters_offset),
            ("Split M4B into WAV Files…", self._on_tool_split_m4b),
            ("Strip Chapter Number Prefix From WAV Files…", self._on_tool_strip_prefix),
        ]
        for label, handler in tools:
            action = QAction(label, self)
            action.triggered.connect(handler)
            tools_menu.addAction(action)

        tools_menu.addSeparator()
        self._rename_from_table_action = QAction("Rename WAV Files From Chapter Titles…", self)
        self._rename_from_table_action.setEnabled(False)
        self._rename_from_table_action.setToolTip(
            "Rename each chapter's source WAV to match its Title column entry."
        )
        self._rename_from_table_action.triggered.connect(self._on_tool_rename_files_from_table)
        tools_menu.addAction(self._rename_from_table_action)

        tools_menu.addSeparator()
        self._options_action = QAction("&Options…", self)
        self._options_action.triggered.connect(self._on_tools_options)
        tools_menu.addAction(self._options_action)

        help_menu = menu_bar.addMenu("&Help")
        assert help_menu is not None
        about_action = QAction(f"&About {APP_NAME}", self)
        about_action.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(about_action)

    # ── Options ───────────────────────────────────────────────────────────────

    def _on_tools_options(self) -> None:
        dialog = OptionsDialog(self._encode_defaults, self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        self._encode_defaults = dialog.values()
        _save_encode_defaults(self._settings, self._encode_defaults)
        self._encode_settings.set_settings(self._encode_defaults)
        self._status.showMessage("Options saved and applied.")

    def _on_output_format_changed(self, output_format: str) -> None:
        label = OUTPUT_FORMATS[output_format][0]
        if self._encode_thread is None:
            self._set_encode_btn_state("encode")
        self._sync_output_path()
        self._status.showMessage(f"Output format: {OUTPUT_FORMATS[output_format][1]}")
        self._book_title_edit.setToolTip(f"Used for the output file name and the {label} tags.")

    def _output_suffix(self) -> str:
        return f".{self._encode_settings.output_format()}"

    # ── Rename WAV files from chapter titles ──────────────────────────────────

    def _on_tool_rename_files_from_table(self) -> None:
        if not self._source_files:
            self._status.showMessage("Renaming needs chapters added from individual WAV files.")
            return
        self._stop_preview(update_status=False)

        import re

        def _safe_stem(title: str) -> str:
            cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(". ")
            return cleaned or "untitled"

        # Each WAV, plus its Audition peak file (.pkf) when present.
        plan: list[RenameItem] = []
        wav_renames: dict[Path, Path] = {}
        for row, source in enumerate(self._source_files):
            new_stem = _safe_stem(self._chapters[row].title)
            if new_stem == source.stem:
                continue
            item = RenameItem(source, new_stem + source.suffix)
            plan.append(item)
            wav_renames[source] = item.target
            sidecar = source.with_suffix(".pkf")
            if sidecar.is_file():
                plan.append(RenameItem(sidecar, new_stem + ".pkf"))

        if not wav_renames:
            self._status.showMessage("Every WAV file already matches its chapter title.")
            return

        _, err = _run_capturing_errors(validate_plan, plan)
        if err:
            QMessageBox.warning(self, "Rename Files From Chapter Titles", err)
            return

        preview = "\n".join(f"{old.name}  →  {new.name}" for old, new in list(wav_renames.items())[:12])
        if len(wav_renames) > 12:
            preview += f"\n… and {len(wav_renames) - 12} more"
        confirm = QMessageBox.question(
            self,
            "Rename Files From Chapter Titles",
            f"Rename {len(wav_renames)} WAV file(s) on disk to match their chapter titles?\n"
            "Matching .pkf peak files are renamed too.\n\n" + preview,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        cache_was_valid = self._cache_matches_current()
        # Two passes (source -> temp -> target) so swapped names cannot collide.
        temps: list[tuple[Path, RenameItem]] = []
        try:
            for item in plan:
                temp = item.source.with_name(f"__tmp__{uuid.uuid4().hex}{item.source.suffix}")
                item.source.rename(temp)
                temps.append((temp, item))
            for temp, item in temps:
                temp.rename(item.target)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Rename Failed",
                f"{exc}\n\nSome files may still have a temporary __tmp__ name.",
            )
            return

        self._source_files = [wav_renames.get(path, path) for path in self._source_files]
        self._original_source_files = [
            wav_renames.get(path, path) for path in self._original_source_files
        ]
        if cache_was_valid:
            self._cached_intermediate_signature = self._current_intermediate_signature()
        if self._playlist_path is not None:
            self._status.showMessage(
                f"Renamed {len(wav_renames)} file(s). Save the playlist to keep the new names."
            )
        else:
            self._status.showMessage(f"Renamed {len(wav_renames)} file(s).")

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _remembered_dir(self, key: str) -> str:
        value = self._settings.value(key, "")
        text = str(value).strip() if value is not None else ""
        if text and Path(text).is_dir():
            return text
        return self._default_open_wav_dir()

    def _prompt_tool_folder(self, caption: str) -> Path | None:
        path_str = QFileDialog.getExistingDirectory(
            self, caption, self._remembered_dir("lastToolDir")
        )
        if not path_str:
            return None
        self._settings.setValue("lastToolDir", path_str)
        return Path(path_str)

    def _launch_tool(
        self,
        title: str,
        script: str,
        args: list[str],
        working_dir: Path,
        apply_flag: str | None = None,
    ) -> None:
        dialog = ToolRunDialog(
            self, title, script, args, working_dir=working_dir, apply_flag=apply_flag
        )
        dialog.show()
        dialog.start()

    def _on_tool_prefix_book_title(self) -> None:
        folder = self._prompt_tool_folder("Prefix Book Title — Select Folder")
        if folder is None:
            return
        title, ok = QInputDialog.getText(
            self,
            "Prefix Book Title",
            "Book title to prepend to every file:",
            text=self._current_book_title(),
        )
        title = title.strip()
        if not ok or not title:
            return
        self._launch_tool(
            "Prefix Book Title",
            "prefix_book_title.py",
            [str(folder), "--title", title],
            folder,
            apply_flag="--apply",
        )

    def _on_tool_rename_chapters_offset(self) -> None:
        folder = self._prompt_tool_folder("Offset Chapter Numbers — Select Folder")
        if folder is None:
            return
        last_offset = self._settings.value("lastChapterOffset", 29)
        try:
            default_offset = int(cast(Any, last_offset))
        except (TypeError, ValueError):
            default_offset = 29
        offset, ok = QInputDialog.getInt(
            self,
            "Offset Chapter Numbers",
            "Subtract this amount from each chapter number:",
            default_offset,
            -9999,
            9999,
        )
        if not ok:
            return
        self._settings.setValue("lastChapterOffset", offset)
        self._launch_tool(
            "Offset Chapter Numbers",
            "rename_chapters_offset.py",
            [str(folder), "--offset", str(offset)],
            folder,
            apply_flag="--apply",
        )

    def _on_tool_split_m4b(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Split M4B — Select Audiobook",
            self._remembered_dir("lastToolDir"),
            "Audiobooks (*.m4b *.m4a);;All Files (*)",
        )
        if not path_str:
            return
        m4b_path = Path(path_str)
        self._settings.setValue("lastToolDir", str(m4b_path.parent))

        default_output = m4b_path.parent / f"{m4b_path.stem}_chapters"
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Split M4B — Output Folder")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText(f"Where should the WAV files be written?\n\nDefault:\n{default_output}")
        default_btn = msg_box.addButton("Use Default", QMessageBox.ButtonRole.AcceptRole)
        choose_btn = msg_box.addButton("Choose Folder…", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(default_btn)
        msg_box.exec()
        clicked = msg_box.clickedButton()

        args = [str(m4b_path)]
        if clicked is choose_btn:
            output_str = QFileDialog.getExistingDirectory(
                self,
                "Split M4B — Select Output Folder",
                self._remembered_dir("lastSplitOutputDir"),
            )
            if not output_str:
                return
            self._settings.setValue("lastSplitOutputDir", output_str)
            args += ["--output-dir", output_str]
        elif clicked is not default_btn:
            return

        # Run from the audiobook's folder so the default <stem>_chapters output
        # directory is created next to the input file.
        self._launch_tool(
            "Split M4B into WAV Files",
            "split_m4b_chapters.py",
            args,
            m4b_path.parent,
        )

    def _on_tool_strip_prefix(self) -> None:
        folder = self._prompt_tool_folder("Strip Chapter Number Prefix — Select Folder")
        if folder is None:
            return
        self._launch_tool(
            "Strip Chapter Number Prefix",
            "strip_chapter_number_prefix.py",
            [str(folder)],
            folder,
            apply_flag="--apply",
        )

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _default_open_wav_dir(self) -> str:
        remembered_dir = self._settings.value("lastOpenWavDir", "")
        remembered_text = str(remembered_dir).strip() if remembered_dir is not None else ""
        if remembered_text:
            remembered_path = Path(remembered_text)
            if remembered_path.is_dir():
                return str(remembered_path)

        if self._wav_path is not None and self._wav_path.parent.is_dir():
            return str(self._wav_path.parent)

        return ""

    def _on_open_wav(self) -> None:
        start_dir = self._default_open_wav_dir()
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import Adobe Audition WAV File", start_dir, "WAV Files (*.wav)"
        )
        if path_str:
            selected_parent = Path(path_str).parent
            if selected_parent.is_dir():
                self._settings.setValue("lastOpenWavDir", str(selected_parent))
            self._load_wav(Path(path_str))

    def _on_reset_clicked(self) -> None:
        self._clear_session()
        self._encode_settings.set_settings(self._encode_defaults)
        self._reset_main_splitter_to_initial()
        self._status.showMessage("Open a WAV file to begin.")

    def _clear_session(self) -> None:
        """Unload the chapter list and its source files; encode settings are kept."""
        self._stop_preview(update_status=False)
        self._clear_cached_intermediate()
        self._wav_path = None
        self._output_dir = None
        self._chapters.clear()
        self._original_chapters.clear()
        self._source_files.clear()
        self._original_source_files.clear()
        self._saved_playlist_files.clear()
        self._playlist_path = None
        self._missing_cover_rows.clear()
        self._row_cover_paths.clear()
        self._next_cover_action = "all"

        self._update_window_title()
        self._book_title_edit.blockSignals(True)
        self._book_title_edit.clear()
        self._book_title_edit.blockSignals(False)
        self._set_output_path(None)
        self._duration_edit.clear()

        self._table.clearContents()
        self._table.setRowCount(0)
        self._clear_table_encode_progress(reset_timeline=True)
        self._update_cover_preview(None)

        self._set_controls_enabled(True)
        self._save_as_btn.setEnabled(False)
        self._covers_btn.setEnabled(False)
        self._reset_markers_action.setEnabled(False)
        self._update_playlist_actions()

        self._set_encode_btn_state("encode")
        self._encode_btn.setEnabled(False)
        self._encode_progress.setValue(0)

    def _on_reset_markers_clicked(self) -> None:
        if not self._chapters or not self._original_chapters:
            return
        self._stop_preview(update_status=False)

        if self._source_files:
            question = "Reset chapter titles and order back to how the files were loaded?"
        else:
            question = "Reset chapter titles/markers in the list back to the original WAV values?"
        reply = QMessageBox.question(
            self,
            "Reset Chapter Markers",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._status.showMessage("Marker reset cancelled.")
            return

        self._chapters = list(self._original_chapters)
        if self._source_files:
            self._source_files = list(self._original_source_files)
            self._duration_edit.setText(
                _format_duration_label(self._chapters[-1].end_ms / 1000.0)
            )
        self._encode_progress_timeline.clear()
        self._missing_cover_rows.clear()
        self._row_cover_paths.clear()
        self._next_cover_action = "all"
        self._populate_table(self._chapters)
        self._update_cover_preview(None)
        self._update_playlist_actions()
        if self._source_files:
            self._status.showMessage("Chapter list reset to the loaded file order.")
        else:
            self._status.showMessage("Chapter list reset to original WAV markers.")

    # ── Playlist / individual WAV files ───────────────────────────────────────

    def _update_playlist_actions(self) -> None:
        has_files = bool(self._source_files)
        self._save_playlist_action.setEnabled(has_files)
        self._remove_chapter_action.setEnabled(has_files)
        self._rename_from_table_action.setEnabled(has_files)

    def _playlist_has_unsaved_changes(self) -> bool:
        return bool(self._source_files) and self._source_files != self._saved_playlist_files

    def _default_playlist_dir(self) -> str:
        value = self._settings.value("lastPlaylistDir", "")
        text = str(value).strip() if value is not None else ""
        if text and Path(text).is_dir():
            return text
        if self._output_dir is not None and self._output_dir.is_dir():
            return str(self._output_dir)
        return self._default_open_wav_dir()

    def _on_add_wav_files(self) -> None:
        if self._wav_path is not None:
            reply = QMessageBox.question(
                self,
                "Replace Audition WAV",
                "Adding individual WAV files will replace the currently loaded "
                "Audition WAV and its chapter list. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        start_dir = self._remembered_dir("lastAddWavDir")
        raw_paths, _ = QFileDialog.getOpenFileNames(
            self, "Add WAV Files as Chapters", start_dir, "WAV Files (*.wav);;All Files (*)"
        )
        if not raw_paths:
            return
        self._settings.setValue("lastAddWavDir", str(Path(raw_paths[0]).parent))

        if self._wav_path is not None:
            self._clear_session()
        self._add_chapters_from_files([Path(p) for p in raw_paths])

    def _add_chapters_from_files(self, file_paths: list[Path]) -> int:
        """Append WAV files as chapters. Returns the number of files added."""
        self._stop_preview(update_status=False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._status.showMessage("Probing audio files…")
            QApplication.processEvents()

            ffprobe, err = _run_capturing_errors(ensure_executable, "ffprobe")
            if err:
                self._status.showMessage(f"ffprobe not found — {err}")
                return 0

            was_empty = not self._chapters
            new_chapters = list(self._chapters)
            new_sources = list(self._source_files)
            failures: list[str] = []
            for file_path in file_paths:
                result, err = _run_capturing_errors(probe_audio_info, ffprobe, file_path)
                if err:
                    failures.append(f"{file_path.name}: {err}")
                    continue
                assert result is not None
                duration_sec, _ = result
                start_ms = new_chapters[-1].end_ms if new_chapters else 0
                new_chapters.append(
                    Chapter(
                        index=len(new_chapters) + 1,
                        title=file_path.stem,
                        start_ms=start_ms,
                        end_ms=start_ms + max(1, int(duration_sec * 1000)),
                    )
                )
                new_sources.append(file_path)
        finally:
            QApplication.restoreOverrideCursor()

        added = len(new_sources) - len(self._source_files)
        if failures:
            QMessageBox.warning(
                self,
                "Some Files Were Skipped",
                "The following files could not be read:\n\n" + "\n".join(failures),
            )
        if added == 0:
            self._status.showMessage("No files were added.")
            return 0

        self._chapters = new_chapters
        self._source_files = new_sources
        self._original_chapters = list(new_chapters)
        self._original_source_files = list(new_sources)
        self._encode_progress_timeline.clear()

        if was_empty:
            first_dir = file_paths[0].parent
            self._output_dir = first_dir
            book_title = first_dir.name
            if book_title.endswith("_chapters"):
                book_title = book_title[: -len("_chapters")]
            self._set_book_title(book_title, auto_unique_output=False)

        self._populate_table(self._chapters)
        self._refresh_cover_thumbnails()
        self._duration_edit.setText(_format_duration_label(self._chapters[-1].end_ms / 1000.0))
        self._update_window_title()
        self._set_controls_enabled(True)
        self._encode_btn.setEnabled(True)
        self._encode_progress.setValue(0)
        self._status.showMessage(f"Added {added} chapter file(s).")
        return added

    def _on_open_playlist(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open Playlist",
            self._default_playlist_dir(),
            "Playlists (*.abspl *.m3u *.m3u8 *.txt);;All Files (*)",
        )
        if not path_str:
            return
        playlist_path = Path(path_str)
        self._settings.setValue("lastPlaylistDir", str(playlist_path.parent))

        try:
            raw_lines = playlist_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            self._status.showMessage(f"Could not read playlist — {exc}")
            return

        paths: list[Path] = []
        for line in raw_lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parsed = Path(text).expanduser()
            if not parsed.is_absolute():
                parsed = (playlist_path.parent / parsed).resolve()
            paths.append(parsed)

        if not paths:
            self._status.showMessage("Playlist contains no file paths.")
            return

        missing = [path for path in paths if not path.is_file()]
        non_wav = [path for path in paths if path.is_file() and path.suffix.lower() != ".wav"]
        if missing or non_wav:
            details: list[str] = []
            for label, bad in (("Missing files", missing), ("Non-WAV entries", non_wav)):
                if not bad:
                    continue
                preview = "\n  ".join(str(path) for path in bad[:5])
                if len(bad) > 5:
                    preview += f"\n  (+{len(bad) - 5} more)"
                details.append(f"{label}:\n  {preview}")
            QMessageBox.warning(
                self,
                "Playlist Open Failed",
                "Cannot open playlist due to invalid entries.\n\n" + "\n\n".join(details),
            )
            self._status.showMessage("Playlist open failed due to invalid entries.")
            return

        if self._chapters:
            reply = QMessageBox.question(
                self,
                "Replace Current Chapters",
                "Opening a playlist will replace the current chapter list. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._status.showMessage("Playlist open cancelled.")
                return

        self._clear_session()
        if self._add_chapters_from_files(paths) == 0:
            return
        self._playlist_path = playlist_path
        self._saved_playlist_files = list(self._source_files)
        self._output_dir = playlist_path.parent
        self._set_book_title(playlist_path.stem, auto_unique_output=False)
        self._update_window_title()
        self._status.showMessage(
            f"Opened playlist with {len(self._source_files)} file(s) — {playlist_path.name}"
        )

    def _on_save_playlist(self) -> bool:
        """Ask where to save the playlist and write it. Returns True if saved."""
        if not self._source_files:
            self._status.showMessage("Saving a playlist requires chapters added from WAV files.")
            return False

        if self._playlist_path is not None:
            start_path = str(self._playlist_path)
        else:
            default_dir = self._default_playlist_dir()
            default_name = f"{_sanitize_output_stem(self._current_book_title() or 'chapter_order')}.abspl"
            start_path = str(Path(default_dir) / default_name) if default_dir else default_name
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Playlist",
            start_path,
            "AudioBook Slicer Playlist (*.abspl);;M3U Playlist (*.m3u8);;Text Files (*.txt)",
        )
        if not path_str:
            return False

        playlist_path = Path(path_str)
        if not playlist_path.suffix:
            playlist_path = playlist_path.with_suffix(".abspl")
        lines = [PLAYLIST_HEADER, *(str(path) for path in self._source_files)]
        try:
            playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save Playlist Failed", str(exc))
            return False
        self._settings.setValue("lastPlaylistDir", str(playlist_path.parent))
        self._playlist_path = playlist_path
        self._saved_playlist_files = list(self._source_files)
        self._update_window_title()
        self._status.showMessage(
            f"Saved playlist with {len(self._source_files)} file(s) — {playlist_path.name}"
        )
        return True

    def _on_remove_selected_chapter(self) -> None:
        if not self._source_files:
            return
        self._stop_preview(update_status=False)
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._status.showMessage("Select a chapter row to remove.")
            return
        remove_row = selected_ranges[0].topRow()
        if not (0 <= remove_row < len(self._chapters)):
            return

        removed_title = self._chapters[remove_row].title
        remaining = self._chapters[:remove_row] + self._chapters[remove_row + 1 :]
        self._source_files.pop(remove_row)

        def _shift(row: int) -> int:
            return row if row < remove_row else row - 1

        self._row_cover_paths = {
            _shift(row): path
            for row, path in self._row_cover_paths.items()
            if row != remove_row
        }
        self._missing_cover_rows = {
            _shift(row) for row in self._missing_cover_rows if row != remove_row
        }
        self._chapters = self._build_encode_progress_timeline(remaining)
        self._populate_table(self._chapters)
        self._refresh_cover_thumbnails()

        if self._chapters:
            select_row = min(remove_row, len(self._chapters) - 1)
            self._table.selectRow(select_row)
            self._update_cover_preview(select_row)
            self._duration_edit.setText(
                _format_duration_label(self._chapters[-1].end_ms / 1000.0)
            )
            self._status.showMessage(f"Removed chapter: {removed_title}")
        else:
            self._clear_session()
            self._status.showMessage("Removed the last chapter. Chapter list is now empty.")

    def _refresh_cover_thumbnails(self) -> None:
        for row, cover_path in self._row_cover_paths.items():
            lbl = self._table.cellWidget(row, 4)
            if not isinstance(lbl, QLabel):
                continue
            px = QPixmap(str(cover_path))
            if px.isNull():
                continue
            lbl.setPixmap(
                px.scaled(
                    THUMB_SIZE,
                    THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _load_wav(self, wav_path: Path) -> None:
        self._status.showMessage("Reading markers…")
        QApplication.processEvents()

        # Locate ffprobe
        ffprobe, err = _run_capturing_errors(ensure_executable, "ffprobe")
        if err:
            self._status.showMessage(f"ffprobe not found — {err}")
            return

        # Get audio duration via ffprobe
        result, err = _run_capturing_errors(probe_audio_info, ffprobe, wav_path)
        if err:
            self._status.showMessage(f"Cannot probe audio — {err}")
            return
        assert result is not None
        duration_sec, _ = result

        # Parse RIFF/WAV markers
        result, err = _run_capturing_errors(read_riff_chunks, wav_path)
        if err:
            self._status.showMessage(f"Cannot read markers — {err}")
            return
        assert result is not None
        sample_rate, markers = result

        # Build chapter list from markers
        chapters, err = _run_capturing_errors(
            build_chapters, markers, sample_rate, duration_sec
        )
        if err:
            self._status.showMessage(f"Cannot build chapters — {err}")
            return
        assert chapters is not None

        self._wav_path = wav_path
        self._output_dir = wav_path.parent
        self._chapters = chapters
        self._original_chapters = list(chapters)
        self._stop_preview(update_status=False)
        self._clear_cached_intermediate()
        self._source_files.clear()
        self._original_source_files.clear()
        self._saved_playlist_files.clear()
        self._playlist_path = None
        self._update_playlist_actions()
        self._encode_progress_timeline.clear()
        self._missing_cover_rows.clear()
        self._row_cover_paths.clear()
        self._next_cover_action = "all"
        self._update_window_title()
        self._duration_edit.setText(_format_duration_label(duration_sec))
        self._set_book_title(wav_path.stem, auto_unique_output=False)
        initial_output_path = self._output_path_for_current_title()
        if initial_output_path is not None:
            resolved_output_path = self._resolve_output_conflict(
                initial_output_path,
                cancel_returns_none=False,
            )
            if resolved_output_path is not None:
                self._output_path_edit.setText(str(resolved_output_path))
        self._populate_table(chapters)
        self._update_cover_preview(None)
        self._covers_btn.setEnabled(True)
        self._save_as_btn.setEnabled(True)
        self._reset_markers_action.setEnabled(True)
        self._encode_btn.setEnabled(True)
        self._encode_progress.setValue(0)

        count = len(chapters)
        noun = "chapter" if count == 1 else "chapters"
        self._status.showMessage(f"Loaded {count} {noun} — {wav_path.name}")

    def _update_window_title(self) -> None:
        if self._wav_path is not None:
            self.setWindowTitle(f"AudioBook Slicer — {self._wav_path}")
        elif self._playlist_path is not None:
            self.setWindowTitle(f"AudioBook Slicer — {self._playlist_path}")
        elif self._source_files:
            self.setWindowTitle(f"AudioBook Slicer — {len(self._source_files)} WAV files")
        else:
            self.setWindowTitle("AudioBook Slicer")

    def _set_book_title(self, title: str, auto_unique_output: bool = False) -> None:
        self._book_title_edit.blockSignals(True)
        self._book_title_edit.setText(title)
        self._book_title_edit.blockSignals(False)
        self._sync_output_path(auto_unique=auto_unique_output)

    def _current_book_title(self) -> str:
        return self._book_title_edit.text().strip()

    def _output_path_for_current_title(self) -> Path | None:
        if self._output_dir is None:
            return None
        stem = _sanitize_output_stem(self._current_book_title())
        return self._output_dir / f"{stem}{self._output_suffix()}"

    def _clear_output_conflict_memory(self) -> None:
        self._remembered_conflict_path = None
        self._remembered_conflict_choice = None

    def _set_output_path(self, output_path: Path | None) -> None:
        old_text = self._output_path_edit.text().strip()
        new_text = str(output_path) if output_path is not None else ""
        self._output_path_edit.setText(new_text)
        if new_text != old_text:
            self._clear_output_conflict_memory()

    def _build_unique_output_path(self, output_path: Path) -> Path:
        stem = output_path.stem
        parent = output_path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}({counter}){output_path.suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _resolve_output_conflict(
        self,
        output_path: Path,
        *,
        cancel_returns_none: bool,
        use_remembered_choice: bool = False,
    ) -> Path | None:
        if not output_path.exists():
            return output_path

        if (
            use_remembered_choice
            and self._remembered_conflict_path == output_path
            and self._remembered_conflict_choice is not None
        ):
            if self._remembered_conflict_choice == "overwrite":
                return output_path
            if self._remembered_conflict_choice == "unique":
                return self._build_unique_output_path(output_path)
            if self._remembered_conflict_choice == "cancel":
                return None if cancel_returns_none else output_path

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Overwrite Output")
        msg_box.setText(f"The output file already exists:\n{output_path}\n\nWhat would you like to do?")
        msg_box.setIcon(QMessageBox.Icon.Question)
        overwrite_btn = msg_box.addButton("Overwrite", QMessageBox.ButtonRole.AcceptRole)
        unique_btn = msg_box.addButton("Create Unique", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(unique_btn)
        msg_box.exec()
        clicked = msg_box.clickedButton()

        if clicked is overwrite_btn:
            self._remembered_conflict_path = output_path
            self._remembered_conflict_choice = "overwrite"
            return output_path
        if clicked is unique_btn:
            self._remembered_conflict_path = output_path
            self._remembered_conflict_choice = "unique"
            return self._build_unique_output_path(output_path)
        self._remembered_conflict_path = output_path
        self._remembered_conflict_choice = "cancel"
        if cancel_returns_none:
            return None
        return output_path

    def _sync_output_path(self, auto_unique: bool = False) -> None:
        output_path = self._output_path_for_current_title()
        if output_path is not None and auto_unique and output_path.exists():
            output_path = self._build_unique_output_path(output_path)
        self._set_output_path(output_path)

    def _on_book_title_changed(self, _text: str) -> None:
        self._sync_output_path()

    def _get_unpopulated_artwork_rows(self) -> list[int]:
        return [row for row in range(len(self._chapters)) if row not in self._row_cover_paths]

    def _update_cover_preview(self, row: int | None) -> None:
        if row is None:
            self._preview_base_pixmap = None
            self._cover_preview.clear()
            self._cover_preview.setText("No cover selected")
            return

        cover_path = self._row_cover_paths.get(row)
        if cover_path is None:
            self._preview_base_pixmap = None
            self._cover_preview.clear()
            self._cover_preview.setText("No cover available for selected chapter")
            return

        px = QPixmap(str(cover_path))
        if px.isNull():
            self._preview_base_pixmap = None
            self._cover_preview.clear()
            self._cover_preview.setText("Could not load cover preview")
            return

        self._preview_base_pixmap = px
        self._refresh_cover_preview_pixmap()

    def _build_encode_progress_timeline(self, chapters: list[Chapter]) -> list[Chapter]:
        timeline: list[Chapter] = []
        cursor_ms = 0
        for row, chapter in enumerate(chapters, start=1):
            duration_ms = max(1, chapter.end_ms - chapter.start_ms)
            start_ms = cursor_ms
            end_ms = start_ms + duration_ms
            timeline.append(
                Chapter(
                    index=row,
                    title=chapter.title,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )
            cursor_ms = end_ms
        return timeline

    def _clear_table_encode_progress(self, reset_timeline: bool = False) -> None:
        for row in range(self._table.rowCount()):
            title_item = self._table.item(row, 1)
            if title_item is not None:
                title_item.setData(CHAPTER_PROGRESS_ROLE, 0.0)
        self._encode_progress_row = None
        if reset_timeline:
            self._encode_progress_timeline.clear()
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()

    def _update_table_encode_progress(self, out_time_sec: float) -> None:
        timeline = self._encode_progress_timeline or self._chapters
        if out_time_sec < 0 or not timeline:
            self._clear_table_encode_progress()
            return

        current_ms = int(out_time_sec * 1000.0)
        for row, chapter in enumerate(timeline):
            title_item = self._table.item(row, 1)
            if title_item is None:
                continue

            is_last = row == len(self._chapters) - 1
            if current_ms < chapter.start_ms:
                row_progress = 0.0
            elif current_ms >= chapter.end_ms and not is_last:
                row_progress = 1.0
            else:
                chapter_span = max(1, chapter.end_ms - chapter.start_ms)
                row_progress = max(
                    0.0,
                    min(1.0, (current_ms - chapter.start_ms) / chapter_span),
                )
            title_item.setData(CHAPTER_PROGRESS_ROLE, row_progress)

        self._encode_progress_row = None
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()

    def _refresh_cover_preview_pixmap(self) -> None:
        if self._preview_base_pixmap is None:
            return
        target_w = max(80, self._cover_preview.width() - 8)
        target_h = max(80, self._cover_preview.height() - 8)
        scaled = self._preview_base_pixmap.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._cover_preview.setText("")
        self._cover_preview.setPixmap(scaled)

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        if not self._splitter_reset_pending:
            self._splitter_reset_pending = True
            QTimer.singleShot(0, self._apply_pending_splitter_reset)

    def _on_main_splitter_moved(self, _pos: int, _index: int) -> None:
        self._refresh_cover_preview_pixmap()

    def _capture_initial_splitter_ratio(self) -> None:
        sizes = self._main_splitter.sizes()
        if len(sizes) != 2:
            return
        total = sizes[0] + sizes[1]
        if total <= 0:
            return
        self._initial_splitter_top_px = sizes[0]
        self._initial_splitter_ratio = sizes[0] / total

    def _apply_pending_splitter_reset(self) -> None:
        self._splitter_reset_pending = False
        self._reset_main_splitter_to_initial()

    def _reset_main_splitter_to_initial(self) -> None:
        top_default, bottom_default = self._initial_splitter_sizes
        total_default = top_default + bottom_default
        current_height = self._main_splitter.height()
        if current_height <= 0 or total_default <= 0:
            self._main_splitter.setSizes([top_default, bottom_default])
            return

        top_size = self._initial_splitter_top_px
        if top_size is None:
            top_ratio = self._initial_splitter_ratio
            if top_ratio is None:
                top_ratio = top_default / total_default
            top_size = int(current_height * top_ratio)
        top_size = max(140, min(current_height - 120, top_size))
        bottom_size = max(120, current_height - top_size)
        self._main_splitter.setSizes([top_size, bottom_size])
        self._refresh_cover_preview_pixmap()

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is None or a0.type() != QEvent.Type.WindowStateChange:
            return
        is_maximized = self.isMaximized()
        if self._was_maximized and not is_maximized:
            QTimer.singleShot(0, self._reset_main_splitter_to_initial)
        self._was_maximized = is_maximized

    def _on_table_selection_changed(self) -> None:
        selected_row = self._selected_chapter_row()
        self._update_cover_preview(selected_row)
        self._update_preview_controls(selected_row)

    def _on_save_as(self) -> None:
        if self._output_dir is None:
            return

        suffix = self._output_suffix()
        label = OUTPUT_FORMATS[self._encode_settings.output_format()][0]
        default_path = self._output_path_for_current_title()
        start_path = str(default_path) if default_path else str(self._output_dir)
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            f"Save Output {label} As",
            start_path,
            f"{label} Files (*{suffix})",
        )
        if not path_str:
            return

        selected_path = Path(path_str)
        if selected_path.suffix.lower() != suffix:
            selected_path = selected_path.with_suffix(suffix)
        self._output_dir = selected_path.parent
        self._set_book_title(selected_path.stem, auto_unique_output=False)
        self._set_output_path(selected_path)

    def _set_controls_enabled(self, enabled: bool) -> None:
        has_source = self._wav_path is not None or bool(self._source_files)
        self._open_action.setEnabled(enabled)
        self._add_files_action.setEnabled(enabled)
        self._open_playlist_action.setEnabled(enabled)
        self._save_playlist_action.setEnabled(enabled and bool(self._source_files))
        self._remove_chapter_action.setEnabled(enabled and bool(self._source_files))
        self._rename_from_table_action.setEnabled(enabled and bool(self._source_files))
        self._options_action.setEnabled(enabled)
        self._reset_action.setEnabled(enabled)
        self._reset_markers_action.setEnabled(enabled and bool(self._original_chapters))
        self._save_as_btn.setEnabled(enabled and has_source)
        self._covers_btn.setEnabled(enabled and bool(self._chapters))
        self._book_title_edit.setEnabled(enabled)
        self._encode_settings.setEnabled(enabled)
        self._update_preview_controls(self._selected_chapter_row())

    def _actual_size_in_unit(self, actual_bytes: int, unit: str) -> Decimal:
        as_decimal = Decimal(actual_bytes)
        if unit == "b":
            return as_decimal * Decimal(8)
        if unit == "kb":
            return (as_decimal * Decimal(8)) / Decimal(1000)
        if unit == "Mb":
            return (as_decimal * Decimal(8)) / Decimal(1_000_000)
        if unit == "Gb":
            return (as_decimal * Decimal(8)) / Decimal(1_000_000_000)
        if unit == "B":
            return as_decimal
        if unit == "KB":
            return as_decimal / Decimal(1000)
        if unit == "MB":
            return as_decimal / Decimal(1_000_000)
        if unit == "GB":
            return as_decimal / Decimal(1_000_000_000)
        return as_decimal

    def _set_encode_btn_state(self, state: str) -> None:
        """state: 'encode' | 'cancel' | 'cancelling'"""
        if state == "encode":
            label = OUTPUT_FORMATS[self._encode_settings.output_format()][0]
            self._encode_btn.setText(f"Encode {label}")
            self._encode_btn.setStyleSheet(
                "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #388e3c; }"
                "QPushButton:disabled { background-color: #9e9e9e; color: #e0e0e0; }"
            )
        elif state == "cancel":
            self._encode_btn.setText("Cancel Encode")
            self._encode_btn.setStyleSheet(
                "QPushButton { background-color: #c62828; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #d32f2f; }"
                "QPushButton:disabled { background-color: #9e9e9e; color: #e0e0e0; }"
            )
        else:  # cancelling
            self._encode_btn.setText("Cancelling…")
            self._encode_btn.setStyleSheet(
                "QPushButton { background-color: #9e9e9e; color: #e0e0e0; font-weight: bold; }"
            )

    # ── Intermediate WAV cache ────────────────────────────────────────────────

    def _current_intermediate_signature(self) -> str:
        """Identifies the audio an intermediate WAV holds: sources, order and cut points.

        Chapter titles are left out because renaming chapters does not change the audio.
        """
        if self._source_files:
            lines = ["files"] + [
                f"{path}|{chapter.end_ms - chapter.start_ms}"
                for path, chapter in zip(self._source_files, self._chapters)
            ]
        else:
            lines = [f"wav={self._wav_path}"] + [
                f"{chapter.start_ms}-{chapter.end_ms}" for chapter in self._chapters
            ]
        return "\n".join(lines)

    def _cache_matches_current(self) -> bool:
        return (
            self._cached_intermediate is not None
            and self._cached_intermediate.exists()
            and self._cached_intermediate_signature == self._current_intermediate_signature()
        )

    def _clear_cached_intermediate(self) -> None:
        _discard_intermediate(self._cached_intermediate)
        self._cached_intermediate = None
        self._cached_intermediate_signature = None

    # ── Encoding ──────────────────────────────────────────────────────────────

    def _build_cover_map(self) -> dict[str, Path]:
        """Map each book title to its cover: Process Covers results first, then files on disk."""
        covers_in_wav_dir: dict[str, Path] = {}
        if self._wav_path is not None and not self._source_files:
            found, _err = _run_capturing_errors(_find_covers_by_title, self._wav_path.parent)
            covers_in_wav_dir = found or {}

        cover_map: dict[str, Path] = {}
        for row, chapter in enumerate(self._chapters):
            book_title = _book_title_from_chapter(chapter.title)
            cover_path = self._row_cover_paths.get(row)
            if cover_path is None:
                if self._source_files:
                    cover_path = _find_cover_for_source_file(self._source_files[row], book_title)
                elif book_title:
                    cover_path = covers_in_wav_dir.get(book_title.casefold())
            if cover_path is not None:
                cover_map.setdefault(book_title or chapter.title, cover_path)
        return cover_map

    def _on_encode_clicked(self) -> None:
        if self._encode_thread is not None and self._encode_thread.isRunning():
            if self._encode_worker is not None:
                self._encode_worker.request_cancel()
            self._encode_btn.setEnabled(False)
            self._set_encode_btn_state("cancelling")
            self._status.showMessage("Cancellation requested…")
            return

        if self._wav_path is None and not self._source_files:
            self._status.showMessage("Open a WAV file or playlist first.")
            return

        output_text = self._output_path_edit.text().strip()
        if not output_text:
            self._status.showMessage("Output file path is empty.")
            return

        output_format = self._encode_settings.output_format()
        output_path = Path(output_text)
        if output_path.suffix.lower() != self._output_suffix():
            output_path = output_path.with_suffix(self._output_suffix())
            self._set_output_path(output_path)

        # MP3 embeds artwork in every chapter; M4B carries one cover, found automatically.
        unpopulated_rows = self._get_unpopulated_artwork_rows() if output_format == "mp3" else []
        if unpopulated_rows:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Missing Artwork")
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setText(
                f"Artwork is not populated for {len(unpopulated_rows)} chapter(s).\n\n"
                "How would you like to proceed?"
            )
            parse_btn = msg_box.addButton(
                "Parse Artwork Then Encode",
                QMessageBox.ButtonRole.AcceptRole,
            )
            encode_anyway_btn = msg_box.addButton(
                "Encode Anyway (No Artwork Handling)",
                QMessageBox.ButtonRole.ActionRole,
            )
            msg_box.addButton("Cancel Encode", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(parse_btn)
            msg_box.exec()

            clicked = msg_box.clickedButton()
            if clicked is parse_btn:
                self._on_process_covers()
                remaining = self._get_unpopulated_artwork_rows()
                if remaining:
                    self._status.showMessage(
                        f"Encode cancelled: artwork still missing for {len(remaining)} chapter(s)."
                    )
                    return
            elif clicked is encode_anyway_btn:
                pass
            else:
                self._status.showMessage("Encode cancelled.")
                return

        resolved_output_path = self._resolve_output_conflict(
            output_path,
            cancel_returns_none=True,
            use_remembered_choice=True,
        )
        if resolved_output_path is None:
            self._status.showMessage("Encode cancelled.")
            return
        output_path = resolved_output_path
        self._set_output_path(output_path)

        reuse_intermediate: Path | None = None
        if self._cache_matches_current():
            reuse_intermediate = self._cached_intermediate
        elif self._cached_intermediate is not None:
            # The chapter order or files changed, so the saved audio no longer applies.
            self._clear_cached_intermediate()

        self._stop_preview(update_status=False)
        self._set_controls_enabled(False)
        self._encode_btn.setEnabled(True)
        self._set_encode_btn_state("cancel")
        self._encode_progress.setValue(0)
        self._encode_progress_timeline = self._build_encode_progress_timeline(self._chapters)
        self._status.showMessage("Starting encode…")

        self._encode_thread = QThread(self)
        self._encode_worker = EncodeWorker(
            output_path=output_path,
            output_format=output_format,
            book_title=self._current_book_title(),
            chapters=list(self._chapters),
            channels=self._encode_settings.channels(),
            vbr_quality=self._encode_settings.vbr_quality(),
            cbr_bitrate=self._encode_settings.cbr_bitrate(),
            cover_map=self._build_cover_map(),
            input_wav=self._wav_path,
            source_files=list(self._source_files),
            reuse_intermediate=reuse_intermediate,
        )
        self._encode_worker.moveToThread(self._encode_thread)
        self._encode_thread.started.connect(self._encode_worker.run)
        self._encode_worker.progress.connect(self._on_encode_progress)
        self._encode_worker.finished.connect(self._on_encode_finished)
        self._encode_worker.failed.connect(self._on_encode_failed)
        self._encode_worker.finished.connect(self._encode_thread.quit)
        self._encode_worker.failed.connect(self._encode_thread.quit)
        self._encode_thread.finished.connect(self._on_encode_thread_finished)
        self._encode_thread.start()

    def _on_encode_progress(self, percent: int, message: str, out_time_sec: float) -> None:
        self._encode_progress.setValue(percent)
        self._status.showMessage(message)
        self._update_table_encode_progress(out_time_sec)

    def _on_encode_finished(self, message: str, actual_bytes: int, intermediate: str) -> None:
        self._encode_progress.setValue(100)
        self._status.showMessage(message)
        self._clear_table_encode_progress(reset_timeline=True)

        if intermediate:
            self._offer_to_keep_intermediate(Path(intermediate), message)

        limit_config = self._encode_settings.size_limit()
        if limit_config is not None:
            limit_value, unit = limit_config
            actual_value = self._actual_size_in_unit(actual_bytes, unit)
            if actual_value <= limit_value:
                return
            excess_value = actual_value - limit_value
            QMessageBox.warning(
                self,
                "File Size Limit Exceeded",
                f"The output file exceeds the configured size limit.\n\n"
                f"File size:   {format_bytes(actual_bytes)}\n"
                f"Size limit:  {limit_value} {unit}\n"
                f"Exceeds by:  {excess_value} {unit}",
            )

    def _offer_to_keep_intermediate(self, intermediate: Path, message: str) -> None:
        try:
            size_text = format_bytes(intermediate.stat().st_size)
        except OSError:
            _discard_intermediate(intermediate)
            return
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Keep Intermediate WAV?")
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText(
            "Encoding is complete.\n\n"
            f"Keep the joined chapter WAV ({size_text}) so another encode of the same "
            "chapter order (for example MP3 and then M4B, or a different bitrate) can skip "
            "rebuilding it?\n\nIt is deleted when you quit, reset, or change the chapter order."
        )
        prompt.setInformativeText(str(intermediate))
        keep_btn = prompt.addButton("Keep Intermediate WAV", QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton("Delete Intermediate WAV", QMessageBox.ButtonRole.DestructiveRole)
        prompt.setDefaultButton(cast(QPushButton, keep_btn))
        prompt.exec()

        if prompt.clickedButton() is keep_btn:
            self._clear_cached_intermediate()
            self._cached_intermediate = intermediate
            self._cached_intermediate_signature = self._current_intermediate_signature()
            self._status.showMessage(f"{message}. Intermediate WAV kept for re-encodes.")
        else:
            _discard_intermediate(intermediate)

    def _on_encode_failed(self, message: str) -> None:
        self._encode_progress.setValue(0)
        self._clear_table_encode_progress(reset_timeline=True)
        if "cancelled" in message.casefold():
            self._status.showMessage(message)
            return
        self._status.showMessage(f"Encode failed: {message}")
        QMessageBox.critical(self, "Encode Failed", message)

    def _on_encode_thread_finished(self) -> None:
        self._set_controls_enabled(True)
        self._set_encode_btn_state("encode")
        self._encode_btn.setEnabled(self._wav_path is not None or bool(self._source_files))
        if self._encode_thread is not None:
            self._encode_thread.deleteLater()
        if self._encode_worker is not None:
            self._encode_worker.deleteLater()
        self._encode_thread = None
        self._encode_worker = None

    # ── Quit ──────────────────────────────────────────────────────────────────

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if a0 is None:
            return
        self._stop_preview(update_status=False)

        if self._encode_thread is not None and self._encode_thread.isRunning():
            reply = QMessageBox.question(
                self,
                "Encode Running",
                "An encode is still running. Cancel it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                a0.ignore()
                return
            if self._encode_worker is not None:
                self._encode_worker.request_cancel()
            self._set_encode_btn_state("cancelling")
            self._status.showMessage("Stopping encode…")
            deadline = time.monotonic() + 15.0
            while self._encode_thread is not None and self._encode_thread.isRunning():
                QApplication.processEvents()
                if time.monotonic() >= deadline:
                    QMessageBox.warning(
                        self,
                        "Unable To Quit Yet",
                        "The encode could not be stopped in time. Please try again in a moment.",
                    )
                    a0.ignore()
                    return
                time.sleep(0.05)

        if self._playlist_has_unsaved_changes():
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Unsaved Chapter Order")
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setText("The chapter list has not been saved as a playlist. Save it before quitting?")
            save_btn = prompt.addButton("Save Playlist…", QMessageBox.ButtonRole.AcceptRole)
            prompt.addButton("Quit Without Saving", QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = prompt.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            prompt.setDefaultButton(cast(QPushButton, save_btn))
            prompt.exec()
            clicked = prompt.clickedButton()
            if clicked is cancel_btn or (clicked is save_btn and not self._on_save_playlist()):
                a0.ignore()
                return

        self._clear_cached_intermediate()
        super().closeEvent(a0)

    # ── Chapter preview playback ──────────────────────────────────────────────

    def _selected_chapter_row(self) -> int | None:
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            return None
        row = selected_ranges[0].topRow()
        return row if 0 <= row < len(self._chapters) else None

    def _chapter_duration_sec(self, row: int) -> float:
        if not (0 <= row < len(self._chapters)):
            return 0.0
        chapter = self._chapters[row]
        return max(0.1, (chapter.end_ms - chapter.start_ms) / 1000.0)

    def _clear_table_preview_progress(self) -> None:
        for row in range(self._table.rowCount()):
            title_item = self._table.item(row, 1)
            if title_item is not None:
                title_item.setData(CHAPTER_PREVIEW_PROGRESS_ROLE, 0.0)
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()

    def _update_table_preview_progress(self, row: int, progress: float) -> None:
        self._clear_table_preview_progress()
        title_item = self._table.item(row, 1) if 0 <= row < self._table.rowCount() else None
        if title_item is None:
            return
        title_item.setData(CHAPTER_PREVIEW_PROGRESS_ROLE, max(0.0, min(1.0, progress)))
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()

    def _show_preview_position(self, row: int, elapsed_sec: float) -> None:
        duration = self._preview_duration_sec
        elapsed_sec = max(0.0, min(elapsed_sec, duration))
        self._update_table_preview_progress(row, elapsed_sec / duration if duration > 0 else 0.0)
        self._preview_counter_label.setText(_format_counter_label(elapsed_sec, duration))

    def _update_preview_controls(self, selected_row: int | None) -> None:
        if selected_row is None:
            self._preview_btn.setText("Start Preview")
            self._preview_btn.setEnabled(False)
            if not self._is_preview_running():
                self._preview_counter_label.setText(_format_counter_label(0.0, 0.0))
            return

        self._preview_btn.setEnabled(self._encode_thread is None)
        if self._is_preview_running() and self._preview_row == selected_row:
            self._preview_btn.setText("Stop Preview")
            return
        self._preview_btn.setText("Start Preview")
        if not self._is_preview_running():
            self._preview_counter_label.setText(
                _format_counter_label(0.0, self._chapter_duration_sec(selected_row))
            )

    def _is_preview_running(self) -> bool:
        if self._preview_player is not None:
            return self._preview_row is not None
        return self._preview_process is not None and self._preview_process.poll() is None

    def _preview_target_for_row(self, row: int) -> tuple[Path, float, float] | None:
        """(file, start offset in seconds, duration in seconds) for a chapter row."""
        if not (0 <= row < len(self._chapters)):
            return None
        duration_sec = self._chapter_duration_sec(row)
        if self._source_files:
            return self._source_files[row], 0.0, duration_sec
        if self._wav_path is None:
            return None
        return self._wav_path, self._chapters[row].start_ms / 1000.0, duration_sec

    def _stop_preview(self, *, update_status: bool, reset_counter: bool = True) -> None:
        self._preview_timer.stop()
        if self._preview_player is not None:
            self._preview_player.stop()
        process = self._preview_process
        if process is not None and process.poll() is None:
            if not _kill_process_tree(int(process.pid)):
                process.kill()
        self._preview_process = None
        self._preview_row = None
        self._preview_duration_sec = 0.0
        self._preview_start_offset_ms = 0
        self._preview_end_offset_ms = 0
        self._preview_wait_start_deadline = 0.0
        self._clear_table_preview_progress()

        selected_row = self._selected_chapter_row()
        self._update_preview_controls(selected_row)
        if reset_counter:
            duration = self._chapter_duration_sec(selected_row) if selected_row is not None else 0.0
            self._preview_counter_label.setText(_format_counter_label(0.0, duration))
        if update_status:
            self._status.showMessage("Preview stopped.")

    def _on_preview_clicked(self) -> None:
        selected_row = self._selected_chapter_row()
        if selected_row is None:
            self._status.showMessage("Select a chapter first.")
            return
        was_playing_this_row = self._is_preview_running() and self._preview_row == selected_row
        self._stop_preview(update_status=was_playing_this_row)
        if not was_playing_this_row:
            self._start_preview_for_row(selected_row)

    def _start_preview_for_row(self, row: int) -> bool:
        target = self._preview_target_for_row(row)
        if target is None:
            self._status.showMessage("Preview source is not available.")
            return False
        input_file, start_sec, duration_sec = target

        if self._preview_player is not None and self._preview_audio_output is not None:
            wanted = str(self._encode_defaults.get("previewDevice", "")).strip()
            device = QMediaDevices.defaultAudioOutput()
            if wanted:
                matched = next(
                    (d for d in QMediaDevices.audioOutputs() if d.description().strip() == wanted),
                    None,
                )
                if matched is not None:
                    device = matched
                else:
                    self._status.showMessage(
                        f"Preview device unavailable; using {device.description()}."
                    )
            self._preview_audio_output.setDevice(device)

            start_ms = int(start_sec * 1000.0)
            self._preview_player.setSource(QUrl.fromLocalFile(str(input_file)))
            self._preview_player.setPosition(start_ms)
            self._preview_player.play()
            self._preview_start_offset_ms = start_ms
            self._preview_end_offset_ms = start_ms + int(duration_sec * 1000.0)
            self._preview_wait_start_deadline = time.monotonic() + 2.0
        else:
            ffplay_bin, err = _run_capturing_errors(ensure_executable, "ffplay")
            if err:
                self._status.showMessage(f"ffplay not found — {err}")
                return False
            cmd = [
                str(ffplay_bin),
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                "-ss",
                f"{start_sec:.3f}",
                "-t",
                f"{duration_sec:.3f}",
                "-i",
                str(input_file),
            ]
            try:
                self._preview_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                self._preview_process = None
                self._status.showMessage(f"Could not start preview — {exc}")
                return False

        self._preview_row = row
        self._preview_started_monotonic = time.monotonic()
        self._preview_duration_sec = duration_sec
        self._show_preview_position(row, 0.0)
        self._preview_btn.setText("Stop Preview")
        self._preview_timer.start()
        self._status.showMessage(f"Previewing chapter {row + 1}: {self._chapters[row].title}")
        return True

    def _finish_preview_chapter(self) -> None:
        """The current chapter reached its end: continue with the next one, or stop."""
        row = self._preview_row
        if row is not None:
            self._show_preview_position(row, self._preview_duration_sec)
            next_row = row + 1
            if next_row < len(self._chapters):
                self._stop_preview(update_status=False, reset_counter=False)
                self._table.selectRow(next_row)
                if self._start_preview_for_row(next_row):
                    return
        self._stop_preview(update_status=False, reset_counter=False)
        self._status.showMessage("Preview finished.")

    def _on_preview_seek_requested(self, row: int, fraction: float) -> None:
        if self._preview_player is None or self._preview_row != row:
            return
        span_ms = self._preview_end_offset_ms - self._preview_start_offset_ms
        if span_ms <= 0:
            return
        target_ms = self._preview_start_offset_ms + int(span_ms * fraction)
        self._preview_player.setPosition(target_ms)
        self._show_preview_position(row, (target_ms - self._preview_start_offset_ms) / 1000.0)

    def _on_preview_timer_tick(self) -> None:
        row = self._preview_row
        if row is None:
            self._preview_timer.stop()
            return

        if self._preview_player is None:
            # ffplay fallback: no position feedback, so track wall-clock time.
            if self._preview_process is None or self._preview_process.poll() is not None:
                self._finish_preview_chapter()
                return
            self._show_preview_position(row, time.monotonic() - self._preview_started_monotonic)
            return

        pos_ms = max(0, int(self._preview_player.position()))
        playing = (
            self._preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if pos_ms >= self._preview_end_offset_ms > self._preview_start_offset_ms:
            self._finish_preview_chapter()
            return
        if playing:
            self._show_preview_position(row, (pos_ms - self._preview_start_offset_ms) / 1000.0)
            return
        if time.monotonic() < self._preview_wait_start_deadline:
            return  # still loading
        if pos_ms >= self._preview_end_offset_ms - 250:
            self._finish_preview_chapter()  # reached the end of the file
            return
        self._stop_preview(update_status=False, reset_counter=False)
        error_text = self._preview_player.errorString() or "check the preview device in Tools → Options"
        self._status.showMessage(f"Preview could not play: {error_text}")

    def _populate_table(self, chapters: list[Chapter]) -> None:
        self._is_populating_table = True
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(chapters))

        for row, ch in enumerate(chapters):
            self._table.setRowHeight(row, THUMB_SIZE + 4)

            num = QTableWidgetItem(str(ch.index))
            num.setFlags(num.flags() & ~Qt.ItemFlag.ItemIsEditable)
            num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, num)

            title_item = QTableWidgetItem(ch.title)
            title_item.setFlags(title_item.flags() | Qt.ItemFlag.ItemIsEditable)
            title_item.setData(CHAPTER_KEY_ROLE, _chapter_key(ch))
            self._table.setItem(row, 1, title_item)

            start = QTableWidgetItem(format_seconds_hms(ch.start_ms / 1000.0))
            start.setFlags(start.flags() & ~Qt.ItemFlag.ItemIsEditable)
            start.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, start)

            end = QTableWidgetItem(format_seconds_hms(ch.end_ms / 1000.0))
            end.setFlags(end.flags() & ~Qt.ItemFlag.ItemIsEditable)
            end.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, end)

            cover_lbl = QLabel()
            cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setCellWidget(row, 4, cover_lbl)

        self._table.blockSignals(False)
        self._is_populating_table = False

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._is_populating_table:
            return
        row = item.row()
        col = item.column()
        if col != 1:
            return
        if row < 0 or row >= len(self._chapters):
            return

        new_title = item.text().strip()
        old_chapter = self._chapters[row]
        if not new_title:
            self._table.blockSignals(True)
            item.setText(old_chapter.title)
            self._table.blockSignals(False)
            return
        if new_title == old_chapter.title:
            return

        self._chapters[row] = replace(old_chapter, title=new_title)

    def _on_table_row_move_requested(self, source_row: int, target_row: int) -> None:
        if self._is_populating_table or not self._chapters:
            return
        self._stop_preview(update_status=False)

        row_count = len(self._chapters)
        if not (0 <= source_row < row_count):
            return
        if target_row < 0:
            target_row = 0
        if target_row > row_count:
            target_row = row_count
        if target_row == source_row or target_row == source_row + 1:
            return

        insert_row = target_row
        if insert_row > source_row:
            insert_row -= 1
        # new_order[new_row] = old_row
        new_order = list(range(row_count))
        new_order.insert(insert_row, new_order.pop(source_row))

        new_chapters = [
            replace(self._chapters[old_row], index=new_row + 1)
            for new_row, old_row in enumerate(new_order)
        ]
        if self._source_files:
            # Chapter files are concatenated in table order, so keep times contiguous.
            new_chapters = self._build_encode_progress_timeline(new_chapters)
            self._source_files = [self._source_files[old_row] for old_row in new_order]
            self._duration_edit.setText(
                _format_duration_label(new_chapters[-1].end_ms / 1000.0)
            )

        self._chapters = new_chapters
        self._row_cover_paths = {
            new_row: self._row_cover_paths[old_row]
            for new_row, old_row in enumerate(new_order)
            if old_row in self._row_cover_paths
        }
        self._missing_cover_rows = {
            new_row
            for new_row, old_row in enumerate(new_order)
            if old_row in self._missing_cover_rows
        }
        self._populate_table(self._chapters)
        self._refresh_cover_thumbnails()

        self._table.selectRow(insert_row)
        self._update_cover_preview(insert_row)

    def _on_process_covers(self) -> None:
        if not self._chapters or (self._wav_path is None and not self._source_files):
            return

        target_rows: list[int]
        mode_used = "all"

        if self._next_cover_action == "missing" and self._missing_cover_rows:
            target_rows = sorted(self._missing_cover_rows)
            mode_used = "missing"
        elif self._next_cover_action == "prompt_force_all":
            reply = QMessageBox.question(
                self,
                "Force Update Covers",
                "All chapters currently have artwork previews.\n"
                "Force update all chapters?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.No:
                self._status.showMessage("No changes made.")
                return
            target_rows = list(range(len(self._chapters)))
            mode_used = "all"
        else:
            target_rows = list(range(len(self._chapters)))
            mode_used = "all"

        self._status.showMessage("Resolving cover images…")
        QApplication.processEvents()

        covers_by_title: dict[str, Path] = {}
        if self._wav_path is not None:
            found, err = _run_capturing_errors(_find_covers_by_title, self._wav_path.parent)
            if err:
                self._status.showMessage(f"Cover error — {err}")
                return
            assert found is not None
            covers_by_title = found

        loaded = 0
        if mode_used == "all":
            next_missing_rows: set[int] = set()
        else:
            next_missing_rows = set(self._missing_cover_rows)

        for row in target_rows:
            ch = self._chapters[row]
            book_title = _book_title_from_chapter(ch.title)
            lbl = self._table.cellWidget(row, 4)
            if isinstance(lbl, QLabel):
                lbl.clear()
            self._row_cover_paths.pop(row, None)

            if self._source_files:
                cover_path = _find_cover_for_source_file(self._source_files[row], book_title)
            elif book_title:
                cover_path = covers_by_title.get(book_title.casefold())
            else:
                cover_path = None
            if not cover_path:
                next_missing_rows.add(row)
                continue

            px = QPixmap(str(cover_path))
            if px.isNull():
                next_missing_rows.add(row)
                continue
            px = px.scaled(
                THUMB_SIZE,
                THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if isinstance(lbl, QLabel):
                lbl.setPixmap(px)
            self._row_cover_paths[row] = cover_path
            next_missing_rows.discard(row)
            loaded += 1

        selected_ranges = self._table.selectedRanges()
        if selected_ranges:
            self._update_cover_preview(selected_ranges[0].topRow())
        else:
            self._update_cover_preview(None)

        self._missing_cover_rows = next_missing_rows

        if self._missing_cover_rows:
            if mode_used == "missing":
                self._next_cover_action = "prompt_force_all"
            else:
                self._next_cover_action = "missing"
        else:
            self._next_cover_action = "prompt_force_all"

        noun = "chapter" if loaded == 1 else "chapters"
        if self._missing_cover_rows:
            missing_titles: set[str] = set()
            for row in sorted(self._missing_cover_rows):
                chapter_title = self._chapters[row].title
                missing_titles.add(_book_title_from_chapter(chapter_title) or chapter_title)
            ordered = sorted(missing_titles, key=str.casefold)
            preview = ", ".join(ordered[:3])
            extra = len(ordered) - 3
            if extra > 0:
                preview = f"{preview} (+{extra} more)"
            self._status.showMessage(
                f"Processed {len(target_rows)} rows; loaded {loaded} {noun}. "
                f"One or more covers are missing: {preview}"
            )
        else:
            self._status.showMessage(
                f"Processed {len(target_rows)} rows; loaded {loaded} {noun}."
            )


def _run_tool_worker(argv: list[str]) -> None:
    """Frozen-build tool mode: `AudioBookSlicer.exe --run-tool <module> [args...]`."""
    if not argv or argv[0] not in TOOL_MODULES:
        raise SystemExit(f"Unknown tool: {argv[0] if argv else '(none)'}")
    module = importlib.import_module(argv[0])
    sys.argv = [f"{argv[0]}.py", *argv[1:]]
    module.main()


def main() -> None:
    if sys.platform == "win32":
        # Give the app its own taskbar identity so Windows shows our icon, not Python's.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AudioBookSlicer")
        except (AttributeError, OSError):
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = _HERE / "icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == TOOL_WORKER_FLAG:
        _run_tool_worker(sys.argv[2:])
    else:
        main()
