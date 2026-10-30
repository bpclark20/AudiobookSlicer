"""MP3 Chapter Editor window for AudioBookSlicer."""
from __future__ import annotations

import importlib
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, cast

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QCloseEvent, QIcon, QPixmap, QResizeEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from wav_markers_to_mp3 import Chapter, ensure_executable, format_seconds_hms

from gui_constants import (
    _HERE,
    _has_qt_multimedia,
    CHAPTER_KEY_ROLE,
    CHAPTER_PREVIEW_PROGRESS_ROLE,
    THUMB_SIZE,
)
from gui_helpers import _chapter_key, _format_counter_label, _run_capturing_errors
from gui_widgets import ChapterProgressDelegate, ReorderableTableWidget

if _has_qt_multimedia:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer


class Mp3ChapterEditorWindow(QMainWindow):
    def __init__(
        self,
        *,
        preview_device_getter: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("MP3 Chapter Editor")
        icon_path = _HERE / "icon.png"
        if icon_path.exists() and icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(980, 620)

        self._preview_device_getter = preview_device_getter
        self._id3_bindings: dict[str, Any] | None = None
        self._mp3_path: Path | None = None
        self._id3_tags: Any = None
        self._chapters: list[Chapter] = []
        self._chapter_records: list[dict[str, Any]] = []
        self._fallback_cover_mime: str | None = None
        self._fallback_cover_data: bytes | None = None
        self._is_populating_table = False
        self._preview_base_pixmap: QPixmap | None = None

        self._preview_process: subprocess.Popen[str] | None = None
        self._preview_player: QMediaPlayer | None = None
        self._preview_audio_output: QAudioOutput | None = None
        self._preview_row: int | None = None
        self._preview_started_monotonic = 0.0
        self._preview_duration_sec = 0.0
        self._preview_start_offset_ms = 0
        self._preview_end_offset_ms = 0
        self._preview_wait_start_deadline = 0.0
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._on_preview_timer_tick)
        if _has_qt_multimedia:
            self._preview_player = QMediaPlayer(self)
            self._preview_audio_output = QAudioOutput(self)
            self._preview_player.setAudioOutput(self._preview_audio_output)

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top_row = QHBoxLayout()
        self._open_mp3_btn = QPushButton("Open MP3")
        self._open_mp3_btn.clicked.connect(self._on_open_mp3_clicked)
        top_row.addWidget(self._open_mp3_btn)

        self._save_metadata_btn = QPushButton("Save Metadata")
        self._save_metadata_btn.setEnabled(False)
        self._save_metadata_btn.clicked.connect(self._on_save_metadata_clicked)
        top_row.addWidget(self._save_metadata_btn)

        self._replace_cover_btn = QPushButton("Set Selected Cover")
        self._replace_cover_btn.setEnabled(False)
        self._replace_cover_btn.clicked.connect(self._on_replace_selected_cover_clicked)
        top_row.addWidget(self._replace_cover_btn)

        self._file_label = QLabel("No MP3 selected")
        self._file_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        top_row.addWidget(self._file_label)

        root.addLayout(top_row)

        preview_controls = QHBoxLayout()
        preview_label = QLabel("Preview")
        preview_label.setFixedWidth(90)
        preview_controls.addWidget(preview_label)

        self._preview_btn = QPushButton("Start Preview")
        self._preview_btn.setEnabled(False)
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        preview_controls.addWidget(self._preview_btn)

        self._preview_counter_label = QLabel(_format_counter_label(0.0, 0.0))
        preview_controls.addWidget(self._preview_counter_label)
        preview_controls.addStretch()
        root.addLayout(preview_controls)

        rules_row = QHBoxLayout()
        self._readonly_hint_label = QLabel(
            "Read-only: chapter order, start/end offsets. Editable: chapter title and artwork only."
        )
        self._readonly_hint_label.setWordWrap(True)
        self._readonly_hint_label.setStyleSheet("color: #666666;")
        rules_row.addWidget(self._readonly_hint_label)
        rules_row.addStretch()
        root.addLayout(rules_row)

        content = QHBoxLayout()
        content.setSpacing(8)

        self._table = ReorderableTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["#", "Title", "Start", "End", "Cover"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setDragEnabled(False)
        self._table.setAcceptDrops(False)
        self._table.setDropIndicatorShown(False)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.itemChanged.connect(self._on_table_item_changed)
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

        content.addWidget(self._table, 4)

        preview_panel = QVBoxLayout()
        preview_panel.setContentsMargins(0, 0, 0, 0)
        preview_panel.setSpacing(4)
        preview_panel.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        cover_label = QLabel("Cover Preview")
        cover_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        preview_panel.addWidget(cover_label)

        self._cover_preview = QLabel("No cover selected")
        self._cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_preview.setMinimumSize(260, 180)
        self._cover_preview.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        preview_panel.addWidget(self._cover_preview, 1)

        preview_wrap = QWidget()
        preview_wrap.setLayout(preview_panel)
        content.addWidget(preview_wrap, 2)

        root.addLayout(content, 1)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("Select an MP3 file with chapter markers.")

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        self._stop_preview(update_status=False)
        super().closeEvent(a0)

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self._refresh_cover_preview_pixmap()

    def _load_id3_bindings(self) -> dict[str, Any] | None:
        if self._id3_bindings is not None:
            return self._id3_bindings
        try:
            id3_module = importlib.import_module("mutagen.id3")
        except ImportError:
            QMessageBox.critical(
                self,
                "Missing Dependency",
                "MP3 chapter editing requires the 'mutagen' package in the active Python environment.",
            )
            self._status.showMessage("mutagen package not available.")
            return None

        required_names = (
            "APIC",
            "CHAP",
            "CTOC",
            "CTOCFlags",
            "ID3",
            "PictureType",
            "TIT2",
        )
        bindings: dict[str, Any] = {}
        for name in required_names:
            value = getattr(id3_module, name, None)
            if value is None:
                QMessageBox.critical(
                    self,
                    "Unsupported mutagen Version",
                    f"mutagen.id3 is missing required symbol: {name}",
                )
                self._status.showMessage("mutagen.id3 API is incomplete for chapter editing.")
                return None
            bindings[name] = value
        self._id3_bindings = bindings
        return bindings

    def _iter_subframes(self, sub_frames: Any) -> list[Any]:
        frames: list[Any] = []
        if sub_frames is None:
            return frames

        if isinstance(sub_frames, (list, tuple)):
            for frame in cast(list[Any] | tuple[Any, ...], sub_frames):
                frame_obj = cast(Any, frame)
                if hasattr(frame_obj, "FrameID"):
                    frames.append(frame_obj)
            return frames

        values = getattr(sub_frames, "values", None)
        if callable(values):
            try:
                values_result = cast(Any, values())
                for value in values_result:
                    value_obj = cast(Any, value)
                    if isinstance(value_obj, (list, tuple)):
                        for frame in cast(list[Any] | tuple[Any, ...], value_obj):
                            frame_obj = cast(Any, frame)
                            if hasattr(frame_obj, "FrameID"):
                                frames.append(frame_obj)
                    elif hasattr(value_obj, "FrameID"):
                        frames.append(value_obj)
            except Exception:
                return frames
        return frames

    def _extract_title_from_subframes(self, sub_frames: Any) -> str | None:
        for frame in self._iter_subframes(sub_frames):
            if str(getattr(frame, "FrameID", "")).upper() != "TIT2":
                continue
            text_value = getattr(frame, "text", None)
            if isinstance(text_value, (list, tuple)) and text_value:
                text = str(text_value[0]).strip()
                if text:
                    return text
            elif text_value is not None:
                text = str(text_value).strip()
                if text:
                    return text
        return None

    def _extract_apic_from_subframes(self, sub_frames: Any) -> tuple[str, bytes] | None:
        for frame in self._iter_subframes(sub_frames):
            if str(getattr(frame, "FrameID", "")).upper() != "APIC":
                continue
            mime = str(getattr(frame, "mime", "")).strip() or "image/jpeg"
            data = getattr(frame, "data", None)
            if isinstance(data, (bytes, bytearray)) and data:
                return mime, bytes(data)
        return None

    def _display_cover_for_row(self, row: int) -> tuple[str, bytes] | None:
        if row < 0 or row >= len(self._chapter_records):
            return None
        record = self._chapter_records[row]
        cover_data = record.get("explicit_cover_data")
        cover_mime = record.get("explicit_cover_mime")
        if isinstance(cover_data, bytes) and cover_data:
            return str(cover_mime or "image/jpeg"), cover_data
        if isinstance(self._fallback_cover_data, bytes) and self._fallback_cover_data:
            return str(self._fallback_cover_mime or "image/jpeg"), self._fallback_cover_data
        return None

    def _clear_loaded_data(self, message: str) -> None:
        self._stop_preview(update_status=False)
        self._mp3_path = None
        self._id3_tags = None
        self._chapters = []
        self._chapter_records = []
        self._fallback_cover_mime = None
        self._fallback_cover_data = None
        self._is_populating_table = True
        self._table.blockSignals(True)
        self._table.clearContents()
        self._table.setRowCount(0)
        self._table.blockSignals(False)
        self._is_populating_table = False
        self._preview_base_pixmap = None
        self._cover_preview.clear()
        self._cover_preview.setText("No cover selected")
        self._preview_counter_label.setText(_format_counter_label(0.0, 0.0))
        self._preview_btn.setText("Start Preview")
        self._preview_btn.setEnabled(False)
        self._save_metadata_btn.setEnabled(False)
        self._replace_cover_btn.setEnabled(False)
        self._file_label.setText(message)

    def _on_open_mp3_clicked(self) -> None:
        start_dir = ""
        if self._mp3_path is not None and self._mp3_path.parent.is_dir():
            start_dir = str(self._mp3_path.parent)

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open MP3 File",
            start_dir,
            "MP3 Files (*.mp3);;All Files (*)",
        )
        if not path_str:
            return
        self._load_mp3(Path(path_str))

    def _load_mp3(self, mp3_path: Path) -> None:
        bindings = self._load_id3_bindings()
        if bindings is None:
            return

        ID3 = bindings["ID3"]
        try:
            tags = ID3(str(mp3_path))
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Open MP3 Failed",
                f"Could not read ID3 tags from file:\n{mp3_path}\n\n{exc}",
            )
            self._status.showMessage("Could not read MP3 metadata.")
            return

        chapter_frames = tags.getall("CHAP")
        if not chapter_frames:
            QMessageBox.warning(
                self,
                "No Chapter Markers",
                "The selected MP3 file does not contain chapter markers (CHAP frames).",
            )
            self._status.showMessage("Selected MP3 has no chapter markers.")
            self._clear_loaded_data("No MP3 selected")
            return

        fallback_cover_mime: str | None = None
        fallback_cover_data: bytes | None = None
        fallback_apic = tags.getall("APIC")
        if fallback_apic:
            apic = fallback_apic[0]
            apic_data = getattr(apic, "data", None)
            if isinstance(apic_data, (bytes, bytearray)) and apic_data:
                fallback_cover_data = bytes(apic_data)
                fallback_cover_mime = str(getattr(apic, "mime", "")).strip() or "image/jpeg"

        sorted_frames = sorted(
            chapter_frames,
            key=lambda frame: (
                int(getattr(frame, "start_time", 0)),
                str(getattr(frame, "element_id", "")),
            ),
        )

        chapter_records: list[dict[str, Any]] = []
        chapters: list[Chapter] = []
        for index, frame in enumerate(sorted_frames, start=1):
            start_ms = int(getattr(frame, "start_time", 0))
            end_ms = int(getattr(frame, "end_time", start_ms + 1))
            if end_ms <= start_ms:
                end_ms = start_ms + 1

            title = self._extract_title_from_subframes(getattr(frame, "sub_frames", None))
            if not title:
                title = f"Chapter {index:03d}"

            apic = self._extract_apic_from_subframes(getattr(frame, "sub_frames", None))
            explicit_cover_mime: str | None = None
            explicit_cover_data: bytes | None = None
            if apic is not None:
                explicit_cover_mime, explicit_cover_data = apic

            chapter_records.append(
                {
                    "element_id": str(getattr(frame, "element_id", f"chp{index:04d}")),
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "start_offset": int(getattr(frame, "start_offset", 0xFFFFFFFF)),
                    "end_offset": int(getattr(frame, "end_offset", 0xFFFFFFFF)),
                    "title": title,
                    "explicit_cover_mime": explicit_cover_mime,
                    "explicit_cover_data": explicit_cover_data,
                }
            )
            chapters.append(
                Chapter(
                    index=index,
                    title=title,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )

        self._stop_preview(update_status=False)
        self._mp3_path = mp3_path
        self._id3_tags = tags
        self._chapters = chapters
        self._chapter_records = chapter_records
        self._fallback_cover_mime = fallback_cover_mime
        self._fallback_cover_data = fallback_cover_data
        self._file_label.setText(str(mp3_path))

        self._populate_table(chapters)
        if chapters:
            self._table.selectRow(0)
            self._update_cover_preview(0)
            self._update_preview_controls(0)
            self._save_metadata_btn.setEnabled(True)
            self._replace_cover_btn.setEnabled(True)

        chapter_count = len(chapters)
        noun = "chapter" if chapter_count == 1 else "chapters"
        self._status.showMessage(f"Loaded {chapter_count} {noun} from {mp3_path.name}.")

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

            display_cover = self._display_cover_for_row(row)
            if display_cover is not None:
                _, cover_data = display_cover
                pixmap = QPixmap()
                if pixmap.loadFromData(cover_data):
                    cover_lbl.setPixmap(
                        pixmap.scaled(
                            THUMB_SIZE,
                            THUMB_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )

        self._table.blockSignals(False)
        self._is_populating_table = False

    def _selected_chapter_row(self) -> int | None:
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            return None
        row = selected_ranges[0].topRow()
        if row < 0 or row >= len(self._chapters):
            return None
        return row

    def _chapter_duration_sec(self, row: int) -> float:
        if row < 0 or row >= len(self._chapters):
            return 0.0
        chapter = self._chapters[row]
        return max(0.1, (chapter.end_ms - chapter.start_ms) / 1000.0)

    def _update_cover_preview(self, row: int | None) -> None:
        if row is None:
            self._preview_base_pixmap = None
            self._cover_preview.clear()
            self._cover_preview.setText("No cover selected")
            return

        display_cover = self._display_cover_for_row(row)
        if display_cover is None:
            self._preview_base_pixmap = None
            self._cover_preview.clear()
            self._cover_preview.setText("No artwork available for selected chapter")
            return

        _, cover_data = display_cover
        px = QPixmap()
        if not px.loadFromData(cover_data):
            self._preview_base_pixmap = None
            self._cover_preview.clear()
            self._cover_preview.setText("Could not load cover preview")
            return

        self._preview_base_pixmap = px
        self._refresh_cover_preview_pixmap()

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

    def _on_table_selection_changed(self) -> None:
        selected_row = self._selected_chapter_row()
        if selected_row is None:
            self._stop_preview(update_status=False)
            self._update_cover_preview(None)
            self._update_preview_controls(None)
            self._replace_cover_btn.setEnabled(False)
            return
        if self._is_preview_running() and self._preview_row != selected_row:
            self._stop_preview(update_status=False)
        self._replace_cover_btn.setEnabled(True)
        self._update_cover_preview(selected_row)
        self._update_preview_controls(selected_row)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._is_populating_table:
            return
        row = item.row()
        col = item.column()
        if col != 1:
            return
        if row < 0 or row >= len(self._chapters) or row >= len(self._chapter_records):
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
        self._chapter_records[row]["title"] = new_title

    def _on_replace_selected_cover_clicked(self) -> None:
        row = self._selected_chapter_row()
        if row is None or row < 0 or row >= len(self._chapter_records):
            self._status.showMessage("Select a chapter row first.")
            return

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select Chapter Artwork",
            str(self._mp3_path.parent) if self._mp3_path is not None else "",
            "Image Files (*.jpg *.jpeg *.png)",
        )
        if not path_str:
            return

        image_path = Path(path_str)
        suffix = image_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        else:
            QMessageBox.warning(
                self,
                "Unsupported Image",
                "Only JPG/JPEG/PNG images are supported for chapter artwork.",
            )
            return

        try:
            data = image_path.read_bytes()
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Read Image Failed",
                f"Could not read artwork file:\n{image_path}\n\n{exc}",
            )
            return

        if not data:
            QMessageBox.warning(
                self,
                "Invalid Image",
                "Selected image file is empty.",
            )
            return

        self._chapter_records[row]["explicit_cover_mime"] = mime
        self._chapter_records[row]["explicit_cover_data"] = data

        cover_label = self._table.cellWidget(row, 4)
        if isinstance(cover_label, QLabel):
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                cover_label.setPixmap(
                    pixmap.scaled(
                        THUMB_SIZE,
                        THUMB_SIZE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                cover_label.clear()

        self._update_cover_preview(row)
        self._status.showMessage(f"Artwork updated for chapter {row + 1}.")

    def _on_save_metadata_clicked(self) -> None:
        if self._mp3_path is None:
            return

        bindings = self._load_id3_bindings()
        if bindings is None:
            return

        ID3 = bindings["ID3"]
        CHAP = bindings["CHAP"]
        CTOC = bindings["CTOC"]
        CTOCFlags = bindings["CTOCFlags"]
        TIT2 = bindings["TIT2"]
        APIC = bindings["APIC"]
        PictureType = bindings["PictureType"]

        try:
            tags = ID3(str(self._mp3_path))
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not re-open tags before saving:\n{self._mp3_path}\n\n{exc}",
            )
            self._status.showMessage("Save failed while loading metadata.")
            return

        tags.delall("CHAP")
        tags.delall("CTOC")

        chapter_ids: list[str] = []
        for index, record in enumerate(self._chapter_records, start=1):
            element_id = str(record.get("element_id") or f"chp{index:04d}")
            chapter_ids.append(element_id)

            title = str(record.get("title") or f"Chapter {index:03d}")
            start_ms = int(record.get("start_ms", 0))
            end_ms = int(record.get("end_ms", start_ms + 1))
            if end_ms <= start_ms:
                end_ms = start_ms + 1

            sub_frames: list[Any] = [
                TIT2(encoding=3, text=[title]),
            ]

            cover_data = record.get("explicit_cover_data")
            cover_mime = str(record.get("explicit_cover_mime") or "image/jpeg")
            if isinstance(cover_data, bytes) and cover_data:
                sub_frames.append(
                    APIC(
                        encoding=3,
                        mime=cover_mime,
                        type=PictureType.COVER_FRONT,
                        desc=f"chapter-{index:04d}",
                        data=cover_data,
                    )
                )

            tags.add(
                CHAP(
                    element_id=element_id,
                    start_time=start_ms,
                    end_time=end_ms,
                    start_offset=int(record.get("start_offset", 0xFFFFFFFF)),
                    end_offset=int(record.get("end_offset", 0xFFFFFFFF)),
                    sub_frames=sub_frames,
                )
            )

        tags.add(
            CTOC(
                element_id="toc",
                flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                child_element_ids=chapter_ids,
                sub_frames=[TIT2(encoding=3, text=["Table of Contents"])],
            )
        )

        try:
            tags.save(str(self._mp3_path), v2_version=3)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not save metadata changes:\n{self._mp3_path}\n\n{exc}",
            )
            self._status.showMessage("Save failed while writing tags.")
            return

        self._id3_tags = tags
        self._status.showMessage("Chapter metadata saved without re-encoding.")

    def _update_preview_controls(self, selected_row: int | None) -> None:
        if selected_row is None:
            self._preview_btn.setText("Start Preview")
            self._preview_btn.setEnabled(False)
            self._preview_counter_label.setText(_format_counter_label(0.0, 0.0))
            return

        self._preview_btn.setEnabled(True)
        if self._is_preview_running() and self._preview_row == selected_row:
            self._preview_btn.setText("Stop Preview")
            return

        self._preview_btn.setText("Start Preview")
        self._preview_counter_label.setText(
            _format_counter_label(0.0, self._chapter_duration_sec(selected_row))
        )

    def _is_preview_running(self) -> bool:
        if self._preview_player is not None:
            return (
                self._preview_player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            )
        return self._preview_process is not None and self._preview_process.poll() is None

    def _preview_target_for_row(self, row: int) -> tuple[Path, float, float] | None:
        if row < 0 or row >= len(self._chapters) or self._mp3_path is None:
            return None
        chapter = self._chapters[row]
        start_sec = chapter.start_ms / 1000.0
        duration_sec = max(0.1, (chapter.end_ms - chapter.start_ms) / 1000.0)
        return self._mp3_path, start_sec, duration_sec

    def _kill_preview_process(self) -> None:
        process = self._preview_process
        if process is None:
            return
        if process.poll() is not None:
            return

        if sys.platform == "win32":
            pid = int(process.pid)
            if pid > 0:
                run_kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        **run_kwargs,
                    )
                    return
                except OSError:
                    pass

        process.kill()

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
        if row < 0 or row >= self._table.rowCount():
            return
        title_item = self._table.item(row, 1)
        if title_item is None:
            return
        title_item.setData(CHAPTER_PREVIEW_PROGRESS_ROLE, max(0.0, min(1.0, progress)))
        viewport = self._table.viewport()
        if viewport is not None:
            viewport.update()

    def _stop_preview(
        self,
        *,
        update_status: bool,
        reset_counter: bool = True,
    ) -> None:
        self._preview_timer.stop()
        if self._preview_player is not None:
            self._preview_player.stop()
        self._kill_preview_process()
        self._preview_process = None
        self._preview_row = None
        self._preview_started_monotonic = 0.0
        self._preview_duration_sec = 0.0
        self._preview_start_offset_ms = 0
        self._preview_end_offset_ms = 0
        self._preview_wait_start_deadline = 0.0
        self._clear_table_preview_progress()

        selected_row = self._selected_chapter_row()
        self._update_preview_controls(selected_row)
        if not reset_counter:
            return

        if selected_row is None:
            self._preview_counter_label.setText(_format_counter_label(0.0, 0.0))
        else:
            self._preview_counter_label.setText(
                _format_counter_label(0.0, self._chapter_duration_sec(selected_row))
            )

        if update_status:
            self._status.showMessage("Preview stopped.")

    def _on_preview_clicked(self) -> None:
        selected_row = self._selected_chapter_row()
        if selected_row is None:
            self._status.showMessage("Select a chapter first.")
            return

        if self._is_preview_running() and self._preview_row == selected_row:
            self._stop_preview(update_status=True)
            return

        if self._is_preview_running():
            self._stop_preview(update_status=False)

        self._start_preview_for_row(selected_row)

    def _start_preview_for_row(self, selected_row: int) -> bool:
        target = self._preview_target_for_row(selected_row)
        if target is None:
            self._status.showMessage("Preview source is not available.")
            return False

        input_file, start_sec, duration_sec = target
        if self._preview_player is not None and self._preview_audio_output is not None:
            selected_device = self._preview_device_getter().strip()
            target_device = QMediaDevices.defaultAudioOutput()
            if selected_device:
                matched = None
                for device in QMediaDevices.audioOutputs():
                    if device.description().strip() == selected_device:
                        matched = device
                        break
                if matched is not None:
                    target_device = matched
                else:
                    self._status.showMessage(
                        "Selected preview device unavailable. Using system default output."
                    )
            self._preview_audio_output.setDevice(target_device)

            self._preview_process = None
            self._preview_player.stop()
            self._preview_player.setSource(QUrl.fromLocalFile(str(input_file)))
            start_ms = int(max(0.0, start_sec) * 1000.0)
            self._preview_player.setPosition(start_ms)
            self._preview_player.play()

            self._preview_start_offset_ms = start_ms
            self._preview_end_offset_ms = start_ms + int(duration_sec * 1000.0)
            self._preview_wait_start_deadline = time.monotonic() + 2.0
        else:
            ffplay_bin, err = _run_capturing_errors(ensure_executable, "ffplay")
            if err:
                self._status.showMessage(f"ffplay not found - {err}")
                return False
            assert isinstance(ffplay_bin, str)

            cmd = [
                ffplay_bin,
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

            popen_kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            try:
                self._preview_process = subprocess.Popen(cmd, **popen_kwargs)
            except OSError as exc:
                self._preview_process = None
                self._status.showMessage(f"Could not start preview - {exc}")
                return False

            self._preview_start_offset_ms = 0
            self._preview_end_offset_ms = int(duration_sec * 1000.0)
            self._preview_wait_start_deadline = 0.0

        self._preview_row = selected_row
        self._preview_started_monotonic = time.monotonic()
        self._preview_duration_sec = duration_sec
        self._update_table_preview_progress(selected_row, 0.0)
        self._preview_btn.setText("Stop Preview")
        self._preview_counter_label.setText(_format_counter_label(0.0, duration_sec))
        self._preview_timer.start()
        self._status.showMessage(f"Previewing chapter {selected_row + 1}...")
        return True

    def _advance_preview_to_next_row(self) -> bool:
        current_row = self._preview_row
        if current_row is None:
            return False
        next_row = current_row + 1
        if next_row >= len(self._chapters):
            return False

        self._stop_preview(update_status=False, reset_counter=False)
        self._table.selectRow(next_row)
        return self._start_preview_for_row(next_row)

    def _on_preview_seek_requested(self, row: int, fraction: float) -> None:
        if not self._is_preview_running() or self._preview_row is None:
            return
        if row != self._preview_row:
            return
        if self._preview_player is None:
            return
        if self._preview_end_offset_ms <= self._preview_start_offset_ms:
            return

        target_ms = self._preview_start_offset_ms + int(
            (self._preview_end_offset_ms - self._preview_start_offset_ms) * fraction
        )
        self._preview_player.setPosition(target_ms)
        elapsed = (target_ms - self._preview_start_offset_ms) / 1000.0
        elapsed = max(0.0, min(elapsed, self._preview_duration_sec))
        progress = 0.0
        if self._preview_duration_sec > 0:
            progress = elapsed / self._preview_duration_sec
        self._update_table_preview_progress(row, progress)
        self._preview_counter_label.setText(
            _format_counter_label(elapsed, self._preview_duration_sec)
        )

    def _on_preview_timer_tick(self) -> None:
        if self._preview_player is not None and self._preview_row is not None:
            pos_ms = max(0, int(self._preview_player.position()))
            if pos_ms >= self._preview_end_offset_ms > self._preview_start_offset_ms:
                duration = self._preview_duration_sec
                self._update_table_preview_progress(self._preview_row, 1.0)
                self._preview_counter_label.setText(_format_counter_label(duration, duration))
                if self._advance_preview_to_next_row():
                    return
                self._stop_preview(update_status=False, reset_counter=False)
                self._status.showMessage("Preview finished.")
                return

            if self._is_preview_running():
                elapsed = max(0.0, (pos_ms - self._preview_start_offset_ms) / 1000.0)
                elapsed = min(elapsed, self._preview_duration_sec)
                progress = 0.0
                if self._preview_duration_sec > 0:
                    progress = elapsed / self._preview_duration_sec
                self._update_table_preview_progress(self._preview_row, progress)
                self._preview_counter_label.setText(
                    _format_counter_label(elapsed, self._preview_duration_sec)
                )
                return

            if time.monotonic() < self._preview_wait_start_deadline:
                return

            if pos_ms >= self._preview_end_offset_ms - 120:
                duration = self._preview_duration_sec
                self._update_table_preview_progress(self._preview_row, 1.0)
                self._preview_counter_label.setText(_format_counter_label(duration, duration))
                if self._advance_preview_to_next_row():
                    return
                self._stop_preview(update_status=False, reset_counter=False)
                self._status.showMessage("Preview finished.")
                return

            self._stop_preview(update_status=False, reset_counter=False)
            self._status.showMessage("Preview could not start. Check playback device selection.")
            return

        if not self._is_preview_running():
            duration = self._preview_duration_sec
            if self._preview_row is not None:
                self._update_table_preview_progress(self._preview_row, 1.0)
            self._preview_counter_label.setText(_format_counter_label(duration, duration))
            if self._advance_preview_to_next_row():
                return
            self._stop_preview(update_status=False, reset_counter=False)
            self._status.showMessage("Preview finished.")
            return

        elapsed = max(0.0, time.monotonic() - self._preview_started_monotonic)
        elapsed = min(elapsed, self._preview_duration_sec)
        if self._preview_row is not None and self._preview_duration_sec > 0:
            self._update_table_preview_progress(
                self._preview_row,
                elapsed / self._preview_duration_sec,
            )
        self._preview_counter_label.setText(
            _format_counter_label(elapsed, self._preview_duration_sec)
        )


