"""Main application window for AudioBookSlicer."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, cast

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QProcess, QSettings, QThread, QTimer, Qt, QUrl, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QAction, QCloseEvent, QColor, QDropEvent, QIcon, QMouseEvent, QPainter, QPixmap, QResizeEvent  # noqa: E402
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
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
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

from wav_markers_to_mp3 import (
    Chapter,
    build_chapters,
    encode_output,
    ensure_executable,
    format_bytes,
    format_seconds_hms,
    probe_audio_info,
    read_riff_chunks,
    resolve_cover_images,
)

from gui_constants import (
    _HERE,
    _has_qt_multimedia,
    THUMB_SIZE,
    CHAPTER_PROGRESS_ROLE,
    CHAPTER_KEY_ROLE,
    CHAPTER_PREVIEW_PROGRESS_ROLE,
    DEFAULT_USE_VBR,
    DEFAULT_VBR_QUALITY,
    DEFAULT_CBR_BITRATE,
    DEFAULT_CHANNELS,
    DEFAULT_SIZE_LIMIT_TEXT,
    DEFAULT_SIZE_UNIT,
    DEFAULT_PREVIEW_DEVICE,
)
from gui_helpers import (
    _format_duration_label,
    _chapter_key,
    _format_counter_label,
    _sanitize_output_stem,
    _book_title_from_chapter,
    _find_covers_by_title,
    _find_cover_for_source_file,
    _run_capturing_errors,
)
from gui_widgets import ChapterProgressDelegate, ReorderableTableWidget
from gui_dialogs import AboutDialog, OptionsDialog
from encode_worker import EncodeWorker
from mp3_editor_window import Mp3ChapterEditorWindow

if _has_qt_multimedia:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AudioBook Slicer")
        icon_path = _HERE / "icon.png"
        if icon_path.exists() and icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(900, 600)

        self._wav_path: Path | None = None
        self._output_dir: Path | None = None
        self._chapters: list[Chapter] = []
        self._original_chapters: list[Chapter] = []
        self._missing_cover_rows: set[int] = set()
        self._row_cover_paths: dict[int, Path] = {}
        self._source_files: dict[int, Path] = {}  # Maps row -> individual WAV file
        self._is_individual_files_mode: bool = False  # True if using individual files
        self._cached_intermediate_wav: Path | None = None
        self._cached_intermediate_signature: str | None = None
        self._cached_intermediate_source_chapters: list[Chapter] = []
        self._cached_intermediate_encode_chapters: list[Chapter] = []
        self._cached_intermediate_source_files: dict[int, Path] = {}
        self._cached_intermediate_cover_paths: dict[int, Path] = {}
        self._cached_intermediate_missing_rows: set[int] = set()
        self._preview_base_pixmap: QPixmap | None = None
        self._next_cover_action = "all"
        self._encode_mode_group: QButtonGroup | None = None
        self._channel_group: QButtonGroup | None = None
        self._size_unit_group: QButtonGroup | None = None
        self._encode_thread: QThread | None = None
        self._encode_worker: EncodeWorker | None = None
        self._initial_splitter_sizes: tuple[int, int] = (180, 460)
        self._top_panel: QWidget | None = None
        self._splitter_top_padding_px = 8
        self._default_splitter_top_px: int | None = None
        self._splitter_reset_pending = False
        self._encode_progress_row: int | None = None
        self._encode_progress_timeline: list[Chapter] = []
        self._is_populating_table = False
        self._was_maximized = self.isMaximized()
        self._remembered_conflict_path: Path | None = None
        self._remembered_conflict_choice: str | None = None
        self._settings = QSettings("AudioBookSlicer", "AudioBookSlicer")
        self._use_vbr = DEFAULT_USE_VBR
        self._vbr_quality = DEFAULT_VBR_QUALITY
        self._cbr_bitrate_kbps = DEFAULT_CBR_BITRATE
        self._channels = DEFAULT_CHANNELS
        self._size_limit_text = DEFAULT_SIZE_LIMIT_TEXT
        self._size_limit_unit = DEFAULT_SIZE_UNIT
        self._preview_device = DEFAULT_PREVIEW_DEVICE
        self._load_options_from_settings()
        self._action_open_wav: QAction | None = None
        self._action_add_wav_files: QAction | None = None
        self._action_import_playlist: QAction | None = None
        self._action_export_playlist: QAction | None = None
        self._action_reset: QAction | None = None
        self._action_reset_markers: QAction | None = None
        self._action_remove_selected: QAction | None = None
        self._action_split_m4b: QAction | None = None
        self._action_rename_files: QAction | None = None
        self._action_mp3_chapter_editor: QAction | None = None
        self._action_options: QAction | None = None
        self._action_exit: QAction | None = None
        self._action_about: QAction | None = None
        self._mp3_chapter_editor_window: Mp3ChapterEditorWindow | None = None
        self._split_process: QProcess | None = None
        self._split_cancelled: bool = False
        self._split_output_lines: list[str] = []
        self._split_output_files: list[Path] = []
        self._split_output_buffer: str = ""
        self._split_output_dir: Path | None = None
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

        # ── Central widget ────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Top bar: file picker ──────────────────────────────────────────────
        top = QHBoxLayout()

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setFixedWidth(70)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        top.addWidget(self._reset_btn)

        self._reset_markers_btn = QPushButton("Reset Markers")
        self._reset_markers_btn.setFixedWidth(100)
        self._reset_markers_btn.setEnabled(False)
        self._reset_markers_btn.clicked.connect(self._on_reset_markers_clicked)
        top.addWidget(self._reset_markers_btn)

        self._remove_chapter_btn = QPushButton("Remove Selected")
        self._remove_chapter_btn.setFixedWidth(120)
        self._remove_chapter_btn.setEnabled(False)
        self._remove_chapter_btn.clicked.connect(self._on_remove_selected_chapter)
        top.addWidget(self._remove_chapter_btn)

        self._covers_btn = QPushButton("Process Covers")
        self._covers_btn.setFixedWidth(120)
        self._covers_btn.setEnabled(False)
        self._covers_btn.clicked.connect(self._on_process_covers)
        top.addWidget(self._covers_btn)

        self._file_label = QLabel("No file selected")
        self._file_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        top.addWidget(self._file_label)

        # ── Output settings + cover preview ─────────────────────────────────
        top_settings_row = QHBoxLayout()
        top_settings_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        top_settings_row.setContentsMargins(0, 0, 0, 0)
        top_settings_row.setSpacing(6)

        settings_left = QVBoxLayout()
        settings_left.setSpacing(6)
        settings_left.setAlignment(Qt.AlignmentFlag.AlignTop)
        settings_left.addLayout(top)

        title_row = QHBoxLayout()

        title_label = QLabel("Book Title")
        title_label.setFixedWidth(90)
        title_row.addWidget(title_label)

        self._book_title_edit = QLineEdit()
        self._book_title_edit.setPlaceholderText("Book title for MP3 tags")
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
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        preview_row.addWidget(self._preview_btn)

        self._preview_counter_label = QLabel(_format_counter_label(0.0, 0.0))
        preview_row.addWidget(self._preview_counter_label)
        preview_row.addStretch()

        settings_left.addLayout(preview_row)

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
        self._top_panel = top_panel

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

        # Apply tight splitter sizing after Qt has finalized widget geometry.
        QTimer.singleShot(0, self._apply_pending_splitter_reset)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._build_menu_bar()
        self._status.showMessage("Open a WAV file to begin.")
        self._save_options_to_settings()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        assert menu_bar is not None

        file_menu = cast(Any, menu_bar.addMenu("File"))
        self._action_open_wav = cast(QAction, file_menu.addAction("Open WAV File..."))
        self._action_open_wav.triggered.connect(self._on_open_wav)
        self._action_add_wav_files = cast(QAction, file_menu.addAction("Add WAV Files..."))
        self._action_add_wav_files.triggered.connect(self._on_add_wav_files)
        file_menu.addSeparator()
        self._action_import_playlist = cast(QAction, file_menu.addAction("Import Playlist..."))
        self._action_import_playlist.triggered.connect(self._on_import_playlist)
        self._action_export_playlist = cast(QAction, file_menu.addAction("Export Playlist..."))
        self._action_export_playlist.triggered.connect(self._on_export_playlist)
        file_menu.addSeparator()
        self._action_exit = cast(QAction, file_menu.addAction("Exit"))
        self._action_exit.triggered.connect(self._on_exit_requested)

        edit_menu = cast(Any, menu_bar.addMenu("Edit"))
        self._action_reset = cast(QAction, edit_menu.addAction("Reset"))
        self._action_reset.triggered.connect(self._on_reset_clicked)
        self._action_reset_markers = cast(QAction, edit_menu.addAction("Reset Markers"))
        self._action_reset_markers.triggered.connect(self._on_reset_markers_clicked)
        self._action_remove_selected = cast(QAction, edit_menu.addAction("Remove Selected"))
        self._action_remove_selected.triggered.connect(self._on_remove_selected_chapter)

        tools_menu = cast(Any, menu_bar.addMenu("Tools"))
        self._action_split_m4b = cast(
            QAction,
            tools_menu.addAction("Split M4B to WAV Chapters"),
        )
        self._action_split_m4b.triggered.connect(self._on_tools_split_m4b_chapters)
        self._action_rename_files = cast(
            QAction,
            tools_menu.addAction("Rename Files From Table"),
        )
        self._action_rename_files.triggered.connect(self._on_tools_rename_files_from_table)
        self._action_mp3_chapter_editor = cast(
            QAction,
            tools_menu.addAction("MP3 Chapter editor"),
        )
        self._action_mp3_chapter_editor.triggered.connect(self._on_tools_mp3_chapter_editor)
        tools_menu.addSeparator()
        self._action_options = cast(QAction, tools_menu.addAction("Options"))
        self._action_options.triggered.connect(self._on_tools_options)

        help_menu = cast(Any, menu_bar.addMenu("Help"))
        self._action_about = cast(QAction, help_menu.addAction("About"))
        self._action_about.triggered.connect(self._on_help_about)

        self._set_controls_enabled(True)

    def _on_tools_split_m4b_chapters(self) -> None:
        start_dir = ""
        remembered_dir = self._settings.value("lastSplitM4bDir", "")
        remembered_text = str(remembered_dir).strip() if remembered_dir is not None else ""
        if remembered_text:
            remembered_path = Path(remembered_text)
            if remembered_path.is_dir():
                start_dir = str(remembered_path)

        if not start_dir and self._output_dir is not None and self._output_dir.is_dir():
            start_dir = str(self._output_dir)

        input_str, _ = QFileDialog.getOpenFileName(
            self,
            "Split M4B to WAV Chapters",
            start_dir,
            "Audiobook Files (*.m4b *.m4a);;All Files (*)",
        )
        if not input_str:
            return

        input_path = Path(input_str)
        if input_path.parent.is_dir():
            self._settings.setValue("lastSplitM4bDir", str(input_path.parent))

        _is_frozen = hasattr(sys, "_MEIPASS")
        if not _is_frozen:
            script_path = _HERE / "split_m4b_chapters.py"
            if not script_path.exists():
                QMessageBox.critical(
                    self,
                    "Split Tool Missing",
                    f"Could not find split tool script:\n{script_path}",
                )
                return

        output_dir = input_path.parent / f"{input_path.stem}_chapters"

        # Check whether output files would conflict with existing WAV files.
        use_overwrite = False
        if output_dir.exists() and any(output_dir.glob("*.wav")):
            conflict_box = QMessageBox(self)
            conflict_box.setWindowTitle("Output Files Already Exist")
            conflict_box.setIcon(QMessageBox.Icon.Question)
            conflict_box.setText(
                f"The output folder already contains WAV files:\n{output_dir}\n\n"
                "What would you like to do?"
            )
            overwrite_btn = conflict_box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
            unique_btn = conflict_box.addButton("Use Unique Names", QMessageBox.ButtonRole.AcceptRole)
            conflict_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            conflict_box.setDefaultButton(unique_btn)
            conflict_box.exec()
            clicked = conflict_box.clickedButton()
            if clicked is overwrite_btn:
                use_overwrite = True
            elif clicked is unique_btn:
                use_overwrite = False
            else:
                return

        self._status.showMessage("Splitting M4B into WAV chapters\u2026")

        if _is_frozen:
            cmd_args = [
                "--split-worker",
                str(input_path),
                "--output-dir",
                str(output_dir),
            ]
        else:
            cmd_args = [
                str(script_path),
                str(input_path),
                "--output-dir",
                str(output_dir),
            ]
        if use_overwrite:
            cmd_args.append("--overwrite")

        self._split_cancelled = False
        self._split_output_lines = []
        self._split_output_files = []
        self._split_output_buffer = ""
        self._split_output_dir = output_dir

        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(cmd_args)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_split_stdout_ready)
        process.finished.connect(self._on_split_process_finished)
        self._split_process = process

        self._set_controls_enabled(False)
        self._set_encode_btn_state("cancel_split")
        self._encode_btn.setEnabled(True)

        process.start()

    def _handle_split_output_line(self, line: str) -> None:
        if not line:
            return
        self._split_output_lines.append(line)

        if line.startswith("[") and "] Converting:" in line:
            self._status.showMessage(f"Splitting\u2026 {line}")
            output_dir = self._split_output_dir
            if output_dir is not None:
                _, _, out_part = line.partition(" -> ")
                out_name = out_part.strip()
                if out_name:
                    self._split_output_files.append(output_dir / out_name)
        elif line.startswith("Done. Created "):
            self._status.showMessage(line)
        elif line.startswith("Found ") and " chapters in:" in line:
            self._status.showMessage(line)

    def _on_split_stdout_ready(self) -> None:
        if self._split_process is None:
            return
        chunk = self._split_process.readAllStandardOutput().data().decode(
            "utf-8", errors="replace"
        )
        if not chunk:
            return

        self._split_output_buffer += chunk
        while "\n" in self._split_output_buffer:
            raw_line, self._split_output_buffer = self._split_output_buffer.split("\n", 1)
            self._handle_split_output_line(raw_line.strip())

    def _on_split_process_finished(
        self, exit_code: int, _exit_status: QProcess.ExitStatus
    ) -> None:
        if self._split_output_buffer.strip():
            self._handle_split_output_line(self._split_output_buffer.strip())
        self._split_output_buffer = ""

        output_dir = self._split_output_dir
        output_lines = list(self._split_output_lines)
        split_output_files = list(self._split_output_files)

        self._split_output_lines.clear()
        self._split_output_files.clear()
        self._split_output_dir = None

        process = self._split_process
        self._split_process = None
        if process is not None:
            process.deleteLater()

        self._set_controls_enabled(True)
        self._set_encode_btn_state("encode")
        self._encode_btn.setEnabled(self._wav_path is not None or bool(self._source_files))

        if self._split_cancelled:
            cancel_status = self._prompt_split_cancel_cleanup(output_dir, split_output_files)
            self._status.showMessage(cancel_status)
            return

        if exit_code != 0:
            details = ("\n".join(output_lines[-8:]) or "Unknown error").strip()
            self._status.showMessage("Split M4B failed.")
            QMessageBox.critical(
                self,
                "Split M4B Failed",
                f"Could not split audiobook.\n\n{details}",
            )
            return

        if output_dir is None:
            self._status.showMessage("Split complete.")
            return

        self._status.showMessage(f"Split complete \u2014 {output_dir}")

        # Use the exact files referenced by this split run, preserving order.
        files_to_add: list[Path] = []
        seen: set[Path] = set()
        for path in split_output_files:
            if path in seen:
                continue
            seen.add(path)
            if path.exists() and path.is_file():
                files_to_add.append(path)

        if not files_to_add:
            return

        add_reply = QMessageBox.question(
            self,
            "Add Split Files",
            f"Split completed with {len(files_to_add)} WAV file(s).\n\n"
            "Would you like to add them to the chapter listing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if add_reply == QMessageBox.StandardButton.Yes:
            self._add_chapters_from_files(files_to_add)

    def _prompt_split_cancel_cleanup(
        self,
        output_dir: Path | None,
        split_output_files: list[Path],
    ) -> str:
        if output_dir is None:
            return "Split cancelled."

        existing_created_files: list[Path] = []
        seen: set[Path] = set()
        for path in split_output_files:
            if path in seen:
                continue
            seen.add(path)
            if path.exists() and path.is_file():
                existing_created_files.append(path)

        created_count = len(existing_created_files)
        if not existing_created_files:
            return "Split cancelled. No created files were found."

        prompt = QMessageBox(self)
        prompt.setWindowTitle("Split Cancelled")
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText(
            f"Split was cancelled and {len(existing_created_files)} WAV file(s) were created.\n\n"
            "What would you like to do with them?"
        )
        delete_folder_btn = prompt.addButton(
            "Delete Folder and All Files", QMessageBox.ButtonRole.DestructiveRole
        )
        delete_created_btn = prompt.addButton(
            "Delete Created Files Only", QMessageBox.ButtonRole.AcceptRole
        )
        do_nothing_btn = prompt.addButton("Do Nothing", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(cast(QPushButton, do_nothing_btn))
        prompt.exec()

        clicked = prompt.clickedButton()
        if clicked is do_nothing_btn:
            return f"Split cancelled. {created_count} created file(s) were kept."

        errors: list[str] = []
        if clicked is delete_folder_btn:
            if output_dir.exists() and output_dir.is_dir():
                try:
                    shutil.rmtree(output_dir)
                    return (
                        "Split cancelled. "
                        f"{created_count} created file(s) were deleted by removing the folder."
                    )
                except OSError as exc:
                    errors.append(str(exc))
            else:
                return "Split cancelled. Output folder no longer exists."
        elif clicked is delete_created_btn:
            deleted = 0
            for path in existing_created_files:
                try:
                    path.unlink(missing_ok=True)
                    deleted += 1
                except OSError as exc:
                    errors.append(f"{path.name}: {exc}")
            if not errors:
                return f"Split cancelled. {deleted} created file(s) were deleted."

        if errors:
            QMessageBox.warning(
                self,
                "Cleanup Incomplete",
                "Some items could not be removed.\n\n" + "\n".join(errors[:8]),
            )
            if clicked is delete_created_btn:
                deleted_count = created_count - len(errors)
                return (
                    "Split cancelled. "
                    f"{deleted_count}/{created_count} created file(s) were deleted; "
                    f"{len(errors)} could not be removed."
                )
            return "Split cancelled. Folder cleanup was incomplete due to file errors."

        return "Split cancelled."

    def _on_help_about(self) -> None:
        dialog = AboutDialog(self)
        dialog.exec()

    def _on_tools_rename_files_from_table(self) -> None:
        """Rename each source WAV file to match its Title column entry."""
        if not self._source_files:
            self._status.showMessage("No individual WAV files to rename.")
            return

        confirm = QMessageBox.question(
            self,
            "Rename Files From Table",
            "This will rename WAV files on disk to match the titles entered in the chapter table.\n\n"
            "Files whose name already matches their title will be skipped.\n\n"
            "Are you sure you want to proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        import re

        def _safe_stem(title: str) -> str:
            """Strip characters illegal in Windows/POSIX filenames."""
            cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(". ")
            return cleaned or "untitled"

        renamed = 0
        skipped = 0
        errors: list[str] = []

        for row in sorted(self._source_files):
            old_path = self._source_files[row]
            title_item = self._table.item(row, 1)
            title = title_item.text().strip() if title_item is not None else ""
            if not title:
                skipped += 1
                continue

            new_stem = _safe_stem(title)
            new_path = old_path.with_name(new_stem + old_path.suffix)

            if new_path == old_path:
                skipped += 1
                continue

            if new_path.exists():
                errors.append(
                    f"Row {row + 1}: target already exists \u2014 \"{new_path.name}\""
                )
                continue

            try:
                old_path.rename(new_path)
            except OSError as exc:
                errors.append(f"Row {row + 1}: {exc}")
                continue

            self._source_files[row] = new_path
            renamed += 1

        lines = [f"Renamed: {renamed}  Skipped (already correct): {skipped}"]
        if errors:
            lines.append("\nErrors:")
            lines.extend(f"  {e}" for e in errors)
            QMessageBox.warning(
                self,
                "Rename Files From Table",
                "\n".join(lines),
            )
        else:
            self._status.showMessage(
                f"Rename complete \u2014 {renamed} renamed, {skipped} already correct."
            )

    def _on_tools_mp3_chapter_editor(self) -> None:
        if self._mp3_chapter_editor_window is None:
            self._mp3_chapter_editor_window = Mp3ChapterEditorWindow(
                preview_device_getter=lambda: self._preview_device,
                parent=self,
            )
            self._mp3_chapter_editor_window.destroyed.connect(
                self._on_mp3_chapter_editor_destroyed
            )

        self._mp3_chapter_editor_window.show()
        self._mp3_chapter_editor_window.raise_()
        self._mp3_chapter_editor_window.activateWindow()

    def _on_mp3_chapter_editor_destroyed(self, _obj: QObject | None = None) -> None:
        self._mp3_chapter_editor_window = None

    def _on_tools_options(self) -> None:
        dialog = OptionsDialog(
            use_vbr=self._use_vbr,
            vbr_quality=self._vbr_quality,
            cbr_bitrate_kbps=self._cbr_bitrate_kbps,
            channels=self._channels,
            size_limit_text=self._size_limit_text,
            size_unit=self._size_limit_unit,
            preview_device=self._preview_device,
            parent=self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return

        (
            self._use_vbr,
            self._vbr_quality,
            self._cbr_bitrate_kbps,
            self._channels,
            self._size_limit_text,
            self._size_limit_unit,
            self._preview_device,
        ) = dialog.values()
        self._save_options_to_settings()
        self._status.showMessage("Options saved.")

    def _load_options_from_settings(self) -> None:
        self._use_vbr = self._settings.value("options/useVbr", DEFAULT_USE_VBR, type=bool)
        self._vbr_quality = self._settings.value(
            "options/vbrQuality",
            DEFAULT_VBR_QUALITY,
            type=int,
        )
        self._cbr_bitrate_kbps = self._settings.value(
            "options/cbrBitrateKbps",
            DEFAULT_CBR_BITRATE,
            type=int,
        )
        self._channels = self._settings.value("options/channels", DEFAULT_CHANNELS, type=int)
        size_limit_value = self._settings.value("options/sizeLimitText", DEFAULT_SIZE_LIMIT_TEXT)
        self._size_limit_text = str(size_limit_value).strip() if size_limit_value is not None else DEFAULT_SIZE_LIMIT_TEXT
        size_unit_value = self._settings.value("options/sizeLimitUnit", DEFAULT_SIZE_UNIT)
        self._size_limit_unit = str(size_unit_value).strip() if size_unit_value is not None else DEFAULT_SIZE_UNIT
        preview_device_value = self._settings.value("options/previewDevice", DEFAULT_PREVIEW_DEVICE)
        self._preview_device = str(preview_device_value).strip() if preview_device_value is not None else DEFAULT_PREVIEW_DEVICE

        self._vbr_quality = max(0, min(9, int(self._vbr_quality)))
        self._cbr_bitrate_kbps = max(48, min(384, int(self._cbr_bitrate_kbps)))
        if int(self._channels) not in (1, 2):
            self._channels = DEFAULT_CHANNELS
        valid_units = {"b", "kb", "Mb", "Gb", "B", "KB", "MB", "GB"}
        if self._size_limit_unit not in valid_units:
            self._size_limit_unit = DEFAULT_SIZE_UNIT
        self._preview_device = self._preview_device.strip()

    def _save_options_to_settings(self) -> None:
        self._settings.setValue("options/useVbr", self._use_vbr)
        self._settings.setValue("options/vbrQuality", int(self._vbr_quality))
        self._settings.setValue("options/cbrBitrateKbps", int(self._cbr_bitrate_kbps))
        self._settings.setValue("options/channels", int(self._channels))
        self._settings.setValue("options/sizeLimitText", self._size_limit_text)
        self._settings.setValue("options/sizeLimitUnit", self._size_limit_unit)
        self._settings.setValue("options/previewDevice", self._preview_device)

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
            self, "Open WAV File", start_dir, "WAV Files (*.wav)"
        )
        if path_str:
            selected_parent = Path(path_str).parent
            if selected_parent.is_dir():
                self._settings.setValue("lastOpenWavDir", str(selected_parent))
            self._load_wav(Path(path_str))

    def _on_reset_clicked(self) -> None:
        self._stop_preview(update_status=False)
        self._clear_cached_intermediate(delete_file=True)

        self._wav_path = None
        self._output_dir = None
        self._chapters.clear()
        self._original_chapters.clear()
        self._missing_cover_rows.clear()
        self._row_cover_paths.clear()
        self._source_files.clear()
        self._is_individual_files_mode = False
        self._next_cover_action = "all"

        self._file_label.setText("No file selected")
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
        self._reset_markers_btn.setEnabled(False)

        self._use_vbr = DEFAULT_USE_VBR
        self._vbr_quality = DEFAULT_VBR_QUALITY
        self._cbr_bitrate_kbps = DEFAULT_CBR_BITRATE
        self._channels = DEFAULT_CHANNELS
        self._size_limit_text = DEFAULT_SIZE_LIMIT_TEXT
        self._size_limit_unit = DEFAULT_SIZE_UNIT
        self._preview_device = DEFAULT_PREVIEW_DEVICE
        self._save_options_to_settings()

        self._set_encode_btn_state("encode")
        self._encode_btn.setEnabled(False)
        self._encode_progress.setValue(0)

        self._reset_main_splitter_to_initial()
        self._status.showMessage("Open a WAV file to begin.")

    def _build_intermediate_signature(
        self,
        chapters: list[Chapter],
        source_files: dict[int, Path],
    ) -> str:
        lines: list[str] = []
        if source_files:
            lines.append("mode=playlist")
            for row in range(len(chapters)):
                src = source_files.get(row)
                lines.append(f"src={src.resolve() if src is not None else ''}")
        else:
            lines.append("mode=single")
            lines.append(f"input={self._wav_path.resolve() if self._wav_path is not None else ''}")

        for chapter in chapters:
            lines.append(
                f"{chapter.title}\x1f{chapter.start_ms}\x1f{chapter.end_ms}"
            )
        return "\n".join(lines)

    def _current_intermediate_signature(self) -> str:
        return self._build_intermediate_signature(self._chapters, self._source_files)

    def _has_cached_intermediate(self) -> bool:
        return (
            self._cached_intermediate_wav is not None
            and self._cached_intermediate_wav.exists()
            and self._cached_intermediate_signature is not None
        )

    def _clear_cached_intermediate(self, delete_file: bool) -> None:
        cached = self._cached_intermediate_wav
        if delete_file and cached is not None:
            try:
                cached.unlink(missing_ok=True)
            except OSError:
                pass

        self._cached_intermediate_wav = None
        self._cached_intermediate_signature = None
        self._cached_intermediate_source_chapters = []
        self._cached_intermediate_encode_chapters = []
        self._cached_intermediate_source_files = {}
        self._cached_intermediate_cover_paths = {}
        self._cached_intermediate_missing_rows = set()

    def _cache_intermediate_for_reuse(
        self,
        wav_path: Path,
        chapters_for_encode: list[Chapter],
    ) -> None:
        self._cached_intermediate_wav = wav_path
        self._cached_intermediate_signature = self._current_intermediate_signature()
        self._cached_intermediate_source_chapters = list(self._chapters)
        self._cached_intermediate_encode_chapters = list(chapters_for_encode)
        self._cached_intermediate_source_files = dict(self._source_files)
        self._cached_intermediate_cover_paths = dict(self._row_cover_paths)
        self._cached_intermediate_missing_rows = set(self._missing_cover_rows)

    def _delete_cached_intermediate_for_quit(self) -> bool:
        """Delete any saved intermediate WAV before app quit.

        Returns True when no cached intermediate exists or deletion succeeds.
        Returns False if a cached file still exists after deletion attempts.
        """
        cached = self._cached_intermediate_wav
        if cached is None:
            self._clear_cached_intermediate(delete_file=False)
            return True

        try:
            cached.unlink(missing_ok=True)
        except OSError:
            pass

        if cached.exists():
            return False

        self._clear_cached_intermediate(delete_file=False)
        return True

    def _on_reset_markers_clicked(self) -> None:
        if not self._chapters or not self._original_chapters:
            return

        reply = QMessageBox.question(
            self,
            "Reset Chapter Markers",
            "Reset chapter titles/markers in the list back to the original WAV values?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._status.showMessage("Marker reset cancelled.")
            return

        self._chapters = list(self._original_chapters)
        self._encode_progress_timeline.clear()
        self._missing_cover_rows.clear()
        self._row_cover_paths.clear()
        self._next_cover_action = "all"
        self._populate_table(self._chapters)
        self._update_cover_preview(None)
        self._status.showMessage("Chapter list reset to original WAV markers.")

    def _on_remove_selected_chapter(self) -> None:
        if not self._source_files:
            self._status.showMessage("Remove is only available in manual files mode.")
            return
        if not self._chapters:
            return

        self._stop_preview(update_status=False)

        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._status.showMessage("Select a chapter row to remove.")
            return
        remove_row = selected_ranges[0].topRow()
        if remove_row < 0 or remove_row >= len(self._chapters):
            return

        old_chapters = list(self._chapters)
        old_source_files = dict(self._source_files)
        old_cover_paths = dict(self._row_cover_paths)
        old_missing_rows = set(self._missing_cover_rows)

        del old_chapters[remove_row]

        new_chapters: list[Chapter] = []
        new_source_files: dict[int, Path] = {}
        new_cover_paths: dict[int, Path] = {}
        new_missing_rows: set[int] = set()
        cursor_ms = 0

        for old_row, chapter in enumerate(old_chapters):
            # old_row indexes into list with removed row already deleted.
            source_row = old_row if old_row < remove_row else old_row + 1
            duration_ms = max(1, chapter.end_ms - chapter.start_ms)
            start_ms = cursor_ms
            end_ms = start_ms + duration_ms
            new_row = len(new_chapters)

            new_chapters.append(
                Chapter(
                    index=new_row + 1,
                    title=chapter.title,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )
            cursor_ms = end_ms

            source_path = old_source_files.get(source_row)
            if source_path is not None:
                new_source_files[new_row] = source_path

            cover_path = old_cover_paths.get(source_row)
            if cover_path is not None:
                new_cover_paths[new_row] = cover_path

            if source_row in old_missing_rows:
                new_missing_rows.add(new_row)

        self._chapters = new_chapters
        self._source_files = new_source_files
        self._row_cover_paths = new_cover_paths
        self._missing_cover_rows = new_missing_rows
        self._next_cover_action = "missing" if self._missing_cover_rows else "prompt_force_all"

        self._populate_table(self._chapters)

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

        if self._chapters:
            select_row = min(remove_row, len(self._chapters) - 1)
            self._table.selectRow(select_row)
            self._update_cover_preview(select_row)
            self._duration_edit.setText(_format_duration_label(self._chapters[-1].end_ms / 1000.0))
            self._status.showMessage("Removed selected chapter.")
        else:
            self._update_cover_preview(None)
            self._duration_edit.clear()
            self._covers_btn.setEnabled(False)
            self._encode_btn.setEnabled(False)
            self._reset_markers_btn.setEnabled(False)
            self._remove_chapter_btn.setEnabled(False)
            self._status.showMessage("Removed selected chapter. Chapter list is now empty.")

    def _on_add_wav_files(self) -> None:
        """Open file dialog to add individual WAV files as chapters."""
        start_dir = ""
        if self._output_dir is not None and self._output_dir.is_dir():
            start_dir = str(self._output_dir)
        
        raw_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add WAV Files as Chapters",
            start_dir,
            "WAV Files (*.wav);;All Files (*)",
        )

        if raw_paths:
            # Remember the directory where files were selected from
            self._output_dir = Path(raw_paths[0]).parent
            self._add_chapters_from_files([Path(p) for p in raw_paths])

    def _default_playlist_dir(self) -> str:
        remembered_dir = self._settings.value("lastPlaylistDir", "")
        remembered_text = str(remembered_dir).strip() if remembered_dir is not None else ""
        if remembered_text:
            remembered_path = Path(remembered_text)
            if remembered_path.is_dir():
                return str(remembered_path)
        if self._output_dir is not None and self._output_dir.is_dir():
            return str(self._output_dir)
        return ""

    def _on_export_playlist(self) -> bool:
        if not self._chapters or not self._source_files:
            self._status.showMessage("Export playlist requires manually added WAV files.")
            return False

        ordered_paths: list[Path] = []
        for row in range(len(self._chapters)):
            source_path = self._source_files.get(row)
            if source_path is None:
                self._status.showMessage(
                    "Cannot export playlist: one or more chapter rows have no source file."
                )
                return False
            ordered_paths.append(source_path)

        default_dir = self._default_playlist_dir()
        default_name = f"{_sanitize_output_stem(self._current_book_title() or 'chapter_order')}.abspl"
        start_path = str(Path(default_dir) / default_name) if default_dir else default_name
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Playlist",
            start_path,
            "AudioBook Slicer Playlist (*.abspl);;M3U Playlist (*.m3u8);;Text Files (*.txt)",
        )
        if not path_str:
            return False

        playlist_path = Path(path_str)
        if not playlist_path.suffix:
            playlist_path = playlist_path.with_suffix(".abspl")

        lines = ["# AudioBookSlicer playlist v1", *[str(path) for path in ordered_paths]]
        playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if playlist_path.parent.is_dir():
            self._settings.setValue("lastPlaylistDir", str(playlist_path.parent))
        self._status.showMessage(
            f"Exported playlist with {len(ordered_paths)} file(s) — {playlist_path.name}"
        )
        return True

    def _on_import_playlist(self) -> None:
        default_dir = self._default_playlist_dir()
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Import Playlist",
            default_dir,
            "Playlists (*.abspl *.m3u *.m3u8 *.txt);;All Files (*)",
        )
        if not path_str:
            return

        playlist_path = Path(path_str)
        if playlist_path.parent.is_dir():
            self._settings.setValue("lastPlaylistDir", str(playlist_path.parent))

        try:
            raw_lines = playlist_path.read_text(encoding="utf-8").splitlines()
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

        missing_paths = [path for path in paths if not path.exists()]
        non_wav_paths = [path for path in paths if path.exists() and path.suffix.lower() != ".wav"]
        if missing_paths or non_wav_paths:
            details: list[str] = []
            if missing_paths:
                preview = ", ".join(str(path) for path in missing_paths[:3])
                extra = len(missing_paths) - 3
                if extra > 0:
                    preview = f"{preview} (+{extra} more)"
                details.append(f"Missing files: {preview}")
            if non_wav_paths:
                preview = ", ".join(str(path) for path in non_wav_paths[:3])
                extra = len(non_wav_paths) - 3
                if extra > 0:
                    preview = f"{preview} (+{extra} more)"
                details.append(f"Non-WAV entries: {preview}")
            QMessageBox.warning(
                self,
                "Playlist Import Failed",
                "Cannot import playlist due to invalid entries.\n\n" + "\n".join(details),
            )
            self._status.showMessage("Playlist import failed due to invalid entries.")
            return

        if self._has_cached_intermediate():
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Intermediate WAV Present")
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setText(
                "A saved intermediate WAV file is currently available.\n\n"
                "What would you like to do before importing a new playlist?"
            )
            load_delete_btn = prompt.addButton(
                "Load New Playlist and Delete WAV",
                QMessageBox.ButtonRole.AcceptRole,
            )
            cancel_keep_btn = prompt.addButton(
                "Cancel Playlist Load and Keep WAV",
                QMessageBox.ButtonRole.RejectRole,
            )
            prompt.setDefaultButton(cast(QPushButton, cancel_keep_btn))
            prompt.exec()

            clicked = prompt.clickedButton()
            if clicked is load_delete_btn:
                self._clear_cached_intermediate(delete_file=True)
            else:
                self._status.showMessage(
                    "Playlist import cancelled. Saved intermediate WAV was kept."
                )
                return

        if self._chapters:
            reply = QMessageBox.question(
                self,
                "Replace Current Chapters",
                "Importing a playlist will replace the current chapter list. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._status.showMessage("Playlist import cancelled.")
                return

        self._on_reset_clicked()
        self._add_chapters_from_files(paths)
        self._file_label.setText(f"Playlist: {playlist_path.name}")
        self._status.showMessage(f"Imported playlist with {len(paths)} file(s).")

    def _add_chapters_from_files(self, file_paths: list[Path]) -> None:
        """Add individual WAV files as chapters to the list."""
        self._stop_preview(update_status=False)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._status.showMessage("Probing audio files…")
            QApplication.processEvents()

            ffprobe, err = _run_capturing_errors(ensure_executable, "ffprobe")
            if err:
                self._status.showMessage(f"ffprobe not found — {err}")
                return

            new_chapters: list[Chapter] = list(self._chapters)
            added_count = 0

            for file_path in file_paths:
                # Probe file duration
                result, err = _run_capturing_errors(probe_audio_info, ffprobe, file_path)
                if err:
                    self._status.showMessage(f"Cannot probe {file_path.name} — {err}")
                    continue
                assert result is not None
                duration_sec, _ = result

                # Create chapter from file
                chapter_idx = len(new_chapters) + 1
                title = file_path.stem
                start_ms = 0 if not new_chapters else new_chapters[-1].end_ms
                end_ms = start_ms + int(duration_sec * 1000)

                new_chapter = Chapter(
                    index=chapter_idx,
                    title=title,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
                new_chapters.append(new_chapter)

                # Track source file by row
                self._source_files[len(new_chapters) - 1] = file_path
                added_count += 1

            if added_count == 0:
                self._status.showMessage("No files were added.")
                return

            # Update state
            if not self._chapters:
                # First time adding files - initialize
                self._output_dir = file_paths[0].parent
                self._chapters = new_chapters
                self._original_chapters = list(new_chapters)
                self._is_individual_files_mode = True
                self._populate_table(self._chapters)
                self._covers_btn.setEnabled(True)
                self._save_as_btn.setEnabled(True)
                self._reset_markers_btn.setEnabled(True)
                self._remove_chapter_btn.setEnabled(True)
                self._encode_btn.setEnabled(True)
                self._encode_progress.setValue(0)
                self._set_controls_enabled(True)
                self._status.showMessage(f"Added {added_count} chapter(s).")
            else:
                # Adding to existing list
                self._chapters = new_chapters
                self._populate_table(self._chapters)
                self._remove_chapter_btn.setEnabled(True)
                self._set_controls_enabled(True)
                self._status.showMessage(f"Added {added_count} chapter(s) to existing list.")

            # Update duration display
            if self._chapters:
                total_duration = self._chapters[-1].end_ms / 1000.0
                self._duration_edit.setText(_format_duration_label(total_duration))
        finally:
            QApplication.restoreOverrideCursor()


    def _load_wav(self, wav_path: Path) -> None:
        self._stop_preview(update_status=False)
        self._clear_cached_intermediate(delete_file=True)

        self._status.showMessage("Reading markers…")
        QApplication.processEvents()

        # Clear individual files mode when loading traditional WAV
        self._source_files.clear()
        self._is_individual_files_mode = False

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
        self._encode_progress_timeline.clear()
        self._missing_cover_rows.clear()
        self._row_cover_paths.clear()
        self._next_cover_action = "all"
        self._file_label.setText(str(wav_path))
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
        self._reset_markers_btn.setEnabled(True)
        self._encode_btn.setEnabled(True)
        self._encode_progress.setValue(0)
        self._set_controls_enabled(True)

        count = len(chapters)
        noun = "chapter" if count == 1 else "chapters"
        self._status.showMessage(f"Loaded {count} {noun} — {wav_path.name}")

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
        return self._output_dir / f"{stem}.mp3"

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
            candidate = parent / f"{stem}({counter}).mp3"
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
            stem = output_path.stem
            parent = output_path.parent
            counter = 1
            while True:
                candidate = parent / f"{stem}({counter}).mp3"
                if not candidate.exists():
                    break
                counter += 1
            output_path = candidate
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

    def _apply_pending_splitter_reset(self) -> None:
        self._splitter_reset_pending = False
        self._reset_main_splitter_to_initial()

    def _reset_main_splitter_to_initial(self) -> None:
        top_default, _bottom_default = self._initial_splitter_sizes
        current_height = self._main_splitter.height()
        if current_height <= 0:
            self._main_splitter.setSizes([top_default, 460])
            return

        if self._default_splitter_top_px is None:
            computed_top = top_default
            if self._top_panel is not None:
                top_hint = self._top_panel.minimumSizeHint().height()
                if top_hint > 0:
                    computed_top = top_hint + int(self._splitter_top_padding_px)
            self._default_splitter_top_px = computed_top

        top_size = self._default_splitter_top_px

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

    def _on_exit_requested(self) -> None:
        self.close()

    def _is_split_running(self) -> bool:
        return (
            self._split_process is not None
            and self._split_process.state() != QProcess.ProcessState.NotRunning
        )

    def _cancel_split_operation(self) -> None:
        if not self._is_split_running():
            return

        self._split_cancelled = True
        process = self._split_process
        if process is None:
            return

        # Fast cancellation: on Windows, kill the full process tree (python + ffmpeg).
        if sys.platform == "win32":
            pid = int(process.processId())
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
                except OSError:
                    process.kill()
                    return

        # Non-Windows or fallback path.
        process.kill()

    def _has_active_operations(self) -> bool:
        split_running = self._is_split_running()
        encode_running = self._encode_thread is not None and self._encode_thread.isRunning()
        return split_running or encode_running

    def _request_cancel_active_operations(self) -> None:
        if self._is_split_running():
            self._cancel_split_operation()

        if self._encode_thread is not None and self._encode_thread.isRunning():
            if self._encode_worker is not None:
                self._encode_worker.request_cancel()

        self._encode_btn.setEnabled(False)
        self._set_encode_btn_state("cancelling")
        self._status.showMessage("Stopping active operations…")

    def _wait_for_active_operations_to_stop(self, timeout_sec: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_sec
        while self._has_active_operations():
            QApplication.processEvents()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_preview(update_status=False)

        if self._has_active_operations():
            reply = QMessageBox.question(
                self,
                "Active Operations Running",
                "An encode or split operation is currently running.\n\n"
                "If you quit now, the operation will be cancelled.\n\n"
                "Do you want to proceed and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            self._request_cancel_active_operations()
            if not self._wait_for_active_operations_to_stop():
                QMessageBox.warning(
                    self,
                    "Unable To Quit Yet",
                    "The active operation could not be stopped in time.\n"
                    "Please wait a moment and try again.",
                )
                event.ignore()
                return

            if not self._delete_cached_intermediate_for_quit():
                QMessageBox.warning(
                    self,
                    "Unable To Quit Yet",
                    "A saved intermediate WAV file could not be deleted.\n"
                    "Close any app using that file and try quitting again.",
                )
                event.ignore()
                return

            event.accept()
            return

        if self._chapters:
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Quit")
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setText(
                "There are items in the chapter list.\n\n"
                "Would you like to export the list before quitting?"
            )
            export_btn = prompt.addButton("Export List First", QMessageBox.ButtonRole.ActionRole)
            quit_btn = prompt.addButton("Quit", QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = prompt.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            prompt.setDefaultButton(cast(QPushButton, export_btn))
            prompt.exec()

            clicked = prompt.clickedButton()
            if clicked is cancel_btn:
                event.ignore()
                return

            if clicked is export_btn:
                exported = self._on_export_playlist()
                if not exported:
                    event.ignore()
                    return

            if clicked is quit_btn or clicked is export_btn:
                if not self._delete_cached_intermediate_for_quit():
                    QMessageBox.warning(
                        self,
                        "Unable To Quit Yet",
                        "A saved intermediate WAV file could not be deleted.\n"
                        "Close any app using that file and try quitting again.",
                    )
                    event.ignore()
                    return
                event.accept()
                return

            event.ignore()
            return

        if not self._delete_cached_intermediate_for_quit():
            QMessageBox.warning(
                self,
                "Unable To Quit Yet",
                "A saved intermediate WAV file could not be deleted.\n"
                "Close any app using that file and try quitting again.",
            )
            event.ignore()
            return

        event.accept()

    def _on_table_selection_changed(self) -> None:
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._stop_preview(update_status=False)
            self._update_cover_preview(None)
            self._update_preview_controls(None)
            return
        selected_row = selected_ranges[0].topRow()
        if self._is_preview_running() and self._preview_row != selected_row:
            self._stop_preview(update_status=False)
        self._update_cover_preview(selected_row)
        self._update_preview_controls(selected_row)

    def _on_save_as(self) -> None:
        if self._output_dir is None:
            return

        default_path = self._output_path_for_current_title()
        start_path = str(default_path) if default_path else str(self._output_dir)
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save Output MP3 As",
            start_path,
            "MP3 Files (*.mp3)",
        )
        if not path_str:
            return

        selected_path = Path(path_str)
        if selected_path.suffix.lower() != ".mp3":
            selected_path = selected_path.with_suffix(".mp3")
        self._output_dir = selected_path.parent
        self._set_book_title(selected_path.stem, auto_unique_output=False)
        self._set_output_path(selected_path)

    def _set_controls_enabled(self, enabled: bool) -> None:
        if not enabled and self._is_preview_running():
            self._stop_preview(update_status=False)

        self._reset_btn.setEnabled(enabled)
        self._reset_markers_btn.setEnabled(enabled and bool(self._chapters))
        self._remove_chapter_btn.setEnabled(
            enabled and bool(self._chapters) and bool(self._source_files)
        )
        self._save_as_btn.setEnabled(
            enabled and (self._wav_path is not None or bool(self._source_files))
        )
        self._covers_btn.setEnabled(enabled and bool(self._chapters))
        self._book_title_edit.setEnabled(enabled)
        selected_row = self._selected_chapter_row()
        self._preview_btn.setEnabled(enabled and selected_row is not None)

        if self._action_open_wav is not None:
            self._action_open_wav.setEnabled(enabled)
        if self._action_add_wav_files is not None:
            self._action_add_wav_files.setEnabled(enabled)
        if self._action_import_playlist is not None:
            self._action_import_playlist.setEnabled(enabled)
        if self._action_export_playlist is not None:
            self._action_export_playlist.setEnabled(enabled and bool(self._source_files))
        if self._action_reset is not None:
            self._action_reset.setEnabled(enabled)
        if self._action_reset_markers is not None:
            self._action_reset_markers.setEnabled(enabled and bool(self._chapters))
        if self._action_remove_selected is not None:
            self._action_remove_selected.setEnabled(
                enabled and bool(self._chapters) and bool(self._source_files)
            )
        if self._action_split_m4b is not None:
            self._action_split_m4b.setEnabled(enabled)
        if self._action_rename_files is not None:
            self._action_rename_files.setEnabled(enabled and bool(self._source_files))
        if self._action_mp3_chapter_editor is not None:
            self._action_mp3_chapter_editor.setEnabled(enabled)
        if self._action_options is not None:
            self._action_options.setEnabled(enabled)

    def _get_size_limit_value(self) -> tuple[Decimal, str] | None:
        text = self._size_limit_text.replace(",", "").replace(" ", "").strip()
        if not text:
            return None
        try:
            value = Decimal(text)
        except InvalidOperation:
            return None
        if value <= Decimal("0"):
            return None
        unit = self._size_limit_unit
        if not unit:
            return None
        return value, unit

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
        """state: 'encode' | 'cancel' | 'cancel_split' | 'cancelling'"""
        if state == "encode":
            self._encode_btn.setText("Encode MP3")
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
        elif state == "cancel_split":
            self._encode_btn.setText("Cancel Split")
            self._encode_btn.setStyleSheet(
                "QPushButton { background-color: #e65100; color: white; font-weight: bold; }"
                "QPushButton:hover { background-color: #ef6c00; }"
                "QPushButton:disabled { background-color: #9e9e9e; color: #e0e0e0; }"
            )
        else:  # cancelling
            self._encode_btn.setText("Cancelling\u2026")
            self._encode_btn.setStyleSheet(
                "QPushButton { background-color: #9e9e9e; color: #e0e0e0; font-weight: bold; }"
            )

    def _on_encode_clicked(self) -> None:
        if self._is_preview_running():
            self._stop_preview(update_status=False)

        # Handle cancellation of an in-progress M4B split
        if self._is_split_running():
            self._cancel_split_operation()
            self._encode_btn.setEnabled(False)
            self._encode_btn.setText("Cancelling\u2026")
            self._status.showMessage("Cancelling split…")
            return

        if self._encode_thread is not None and self._encode_thread.isRunning():
            if self._encode_worker is not None:
                self._encode_worker.request_cancel()
            self._encode_btn.setEnabled(False)
            self._encode_btn.setText("Cancelling…")
            self._status.showMessage("Cancellation requested…")
            return

        # Check if we have data to encode: either traditional WAV or individual files
        if self._wav_path is None and not self._source_files:
            self._status.showMessage("Load a WAV file or add WAV files first.")
            return
        
        if not self._chapters:
            self._status.showMessage("No chapters loaded.")
            return

        source_signature = self._current_intermediate_signature()
        reuse_intermediate_wav: Path | None = None
        chapters_for_worker = list(self._chapters)
        if self._has_cached_intermediate():
            if self._cached_intermediate_signature == source_signature:
                reuse_intermediate_wav = self._cached_intermediate_wav
                if self._cached_intermediate_encode_chapters:
                    chapters_for_worker = list(self._cached_intermediate_encode_chapters)
            else:
                prompt = QMessageBox(self)
                prompt.setWindowTitle("Chapter List Changed")
                prompt.setIcon(QMessageBox.Icon.Question)
                prompt.setText(
                    "A saved intermediate WAV exists, but chapter rows were modified since it was created.\n\n"
                    "A new intermediate WAV must be generated before encoding."
                )
                regenerate_btn = prompt.addButton(
                    "Delete Old WAV and Generate New WAV",
                    QMessageBox.ButtonRole.AcceptRole,
                )
                bail_btn = prompt.addButton(
                    "Bail Out (Keep Modified List and WAV)",
                    QMessageBox.ButtonRole.RejectRole,
                )
                restore_btn = prompt.addButton(
                    "Cancel Encode and Restore List Matching Saved WAV",
                    QMessageBox.ButtonRole.ActionRole,
                )
                prompt.setDefaultButton(cast(QPushButton, bail_btn))
                prompt.exec()

                clicked = prompt.clickedButton()
                if clicked is regenerate_btn:
                    self._clear_cached_intermediate(delete_file=True)
                    chapters_for_worker = list(self._chapters)
                elif clicked is restore_btn:
                    if self._cached_intermediate_source_chapters:
                        self._chapters = list(self._cached_intermediate_source_chapters)
                        self._source_files = dict(self._cached_intermediate_source_files)
                        self._row_cover_paths = dict(self._cached_intermediate_cover_paths)
                        self._missing_cover_rows = set(self._cached_intermediate_missing_rows)
                        self._is_individual_files_mode = bool(self._source_files)
                        self._populate_table(self._chapters)
                        self._update_cover_preview(None)
                        if self._chapters:
                            total_duration = self._chapters[-1].end_ms / 1000.0
                            self._duration_edit.setText(_format_duration_label(total_duration))
                    self._status.showMessage(
                        "Encode cancelled. Chapter list restored to match saved intermediate WAV."
                    )
                    return
                else:
                    self._status.showMessage(
                        "Encode cancelled. Modified chapter list and saved intermediate WAV were kept."
                    )
                    return

        output_text = self._output_path_edit.text().strip()
        if not output_text:
            self._status.showMessage("Output file path is empty.")
            return

        output_path = Path(output_text)
        if output_path.suffix.lower() != ".mp3":
            output_path = output_path.with_suffix(".mp3")
            self._set_output_path(output_path)

        unpopulated_rows = self._get_unpopulated_artwork_rows()
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

        channels = self._channels
        vbr_quality = self._vbr_quality
        cbr_bitrate = None if self._use_vbr else f"{self._cbr_bitrate_kbps}k"

        # Preserve cover selections that were already resolved in the GUI table.
        cover_map_override: dict[str, Path] = {}
        for row, chapter in enumerate(self._chapters):
            book_title = _book_title_from_chapter(chapter.title)
            cover_path = self._row_cover_paths.get(row)
            if book_title and cover_path is not None:
                cover_map_override.setdefault(book_title, cover_path)

        self._set_controls_enabled(False)
        self._encode_btn.setEnabled(True)
        self._set_encode_btn_state("cancel")
        self._encode_progress.setValue(0)
        self._encode_progress_timeline = self._build_encode_progress_timeline(chapters_for_worker)
        self._status.showMessage("Starting encode…")

        self._encode_thread = QThread(self)
        self._encode_worker = EncodeWorker(
            input_wav=(
                self._wav_path
                if self._wav_path is not None
                else next(iter(self._source_files.values()))
            ),
            output_mp3=output_path,
            chapters=list(chapters_for_worker),
            channels=channels,
            vbr_quality=vbr_quality,
            cbr_bitrate=cbr_bitrate,
            source_files=dict(self._source_files) if self._source_files else None,
            cover_map_override=cover_map_override,
            reuse_intermediate_wav=reuse_intermediate_wav,
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

    def _on_encode_finished(self, message: str, actual_bytes: int, payload: object) -> None:
        self._encode_progress.setValue(100)
        self._status.showMessage(message)
        self._clear_table_encode_progress(reset_timeline=True)

        intermediate_path: Path | None = None
        chapters_for_encode: list[Chapter] = list(self._chapters)
        if isinstance(payload, dict):
            raw_intermediate = str(payload.get("intermediate_wav", "")).strip()
            if raw_intermediate:
                intermediate_path = Path(raw_intermediate)
            raw_chapters = payload.get("chapters_for_encode")
            if isinstance(raw_chapters, list) and all(
                isinstance(ch, Chapter) for ch in raw_chapters
            ):
                chapters_for_encode = list(raw_chapters)

        if intermediate_path is not None and intermediate_path.exists():
            prompt = QMessageBox(self)
            prompt.setWindowTitle("Keep Intermediate WAV?")
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setText(
                "Encoding is complete.\n\n"
                "Keep the generated intermediate WAV for potential re-encodes?"
            )
            prompt.setInformativeText(str(intermediate_path))
            keep_btn = prompt.addButton(
                "Keep Intermediate WAV",
                QMessageBox.ButtonRole.AcceptRole,
            )
            delete_btn = prompt.addButton(
                "Delete Intermediate WAV",
                QMessageBox.ButtonRole.DestructiveRole,
            )
            prompt.setDefaultButton(cast(QPushButton, keep_btn))
            prompt.exec()

            if prompt.clickedButton() is keep_btn:
                if (
                    self._cached_intermediate_wav is not None
                    and self._cached_intermediate_wav != intermediate_path
                ):
                    self._clear_cached_intermediate(delete_file=True)
                self._cache_intermediate_for_reuse(intermediate_path, chapters_for_encode)
                self._status.showMessage(
                    f"Encode complete. Saved intermediate WAV for reuse: {intermediate_path.name}"
                )
            else:
                try:
                    intermediate_path.unlink(missing_ok=True)
                except OSError:
                    pass

        limit_config = self._get_size_limit_value()
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
        self._update_preview_controls(self._selected_chapter_row())

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

    def _update_preview_controls(self, selected_row: int | None) -> None:
        if selected_row is None:
            self._preview_btn.setText("Start Preview")
            self._preview_btn.setEnabled(False)
            self._preview_counter_label.setText(_format_counter_label(0.0, 0.0))
            return

        enabled = self._reset_btn.isEnabled()
        self._preview_btn.setEnabled(enabled)

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
        if row < 0 or row >= len(self._chapters):
            return None

        chapter = self._chapters[row]
        duration_sec = max(0.1, (chapter.end_ms - chapter.start_ms) / 1000.0)

        source_file = self._source_files.get(row)
        if source_file is not None:
            return source_file, 0.0, duration_sec

        if self._wav_path is None:
            return None

        return self._wav_path, chapter.start_ms / 1000.0, duration_sec

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
        if selected_row < 0 or selected_row >= len(self._chapters):
            return False

        target = self._preview_target_for_row(selected_row)
        if target is None:
            self._status.showMessage("Preview source is not available.")
            return False

        input_file, start_sec, duration_sec = target

        if self._preview_player is not None and self._preview_audio_output is not None:
            selected_device = self._preview_device.strip()
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
                        f"Selected preview device unavailable. Using system default: {target_device.description()}"
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
                self._status.showMessage(f"ffplay not found — {err}")
                return
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
            if self._preview_device:
                self._status.showMessage(
                    "Explicit preview device selection requires QtMultimedia playback. Falling back to system default."
                )

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
                self._status.showMessage(f"Could not start preview — {exc}")
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
        self._status.showMessage(f"Previewing chapter {selected_row + 1}…")
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

        old_chapters = list(self._chapters)
        cover_by_key: dict[str, Path] = {}
        missing_by_key: set[str] = set()
        source_by_key: dict[str, Path] = {}  # Track source files
        for old_row, old_ch in enumerate(old_chapters):
            key = _chapter_key(old_ch)
            if old_row in self._row_cover_paths:
                cover_by_key[key] = self._row_cover_paths[old_row]
            if old_row in self._missing_cover_rows:
                missing_by_key.add(key)
            if old_row in self._source_files:
                source_by_key[key] = self._source_files[old_row]

        moved = old_chapters.pop(source_row)
        insert_row = target_row
        if insert_row > source_row:
            insert_row -= 1
        old_chapters.insert(insert_row, moved)

        new_chapters: list[Chapter] = []
        new_cover_paths: dict[int, Path] = {}
        new_missing_rows: set[int] = set()
        new_source_files: dict[int, Path] = {}
        # Individual files are concatenated in row order, so their timeline must
        # be rebuilt sequentially. Single-WAV mode keeps source positions, which
        # the encoder uses to reorder audio.
        retime = bool(self._source_files)
        cursor_ms = 0
        for new_row, chapter in enumerate(old_chapters):
            old_key = _chapter_key(chapter)
            if retime:
                duration_ms = max(1, chapter.end_ms - chapter.start_ms)
                chapter = replace(
                    chapter,
                    index=new_row + 1,
                    start_ms=cursor_ms,
                    end_ms=cursor_ms + duration_ms,
                )
                cursor_ms += duration_ms
            else:
                chapter = replace(chapter, index=new_row + 1)
            new_chapters.append(chapter)
            if old_key in cover_by_key:
                new_cover_paths[new_row] = cover_by_key[old_key]
            if old_key in missing_by_key:
                new_missing_rows.add(new_row)
            if old_key in source_by_key:
                new_source_files[new_row] = source_by_key[old_key]

        self._chapters = new_chapters
        self._row_cover_paths = new_cover_paths
        self._missing_cover_rows = new_missing_rows
        self._source_files = new_source_files
        self._populate_table(self._chapters)

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

        self._table.selectRow(insert_row)
        self._update_cover_preview(insert_row)

    def _on_process_covers(self) -> None:
        if not self._chapters:
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

        # Check if we're processing individual files mode or traditional mode
        is_individual_files = any(row in self._source_files for row in target_rows)

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

            # Handle individual files mode vs traditional mode
            if is_individual_files and row in self._source_files:
                # Individual files mode: look for cover next to the source file
                source_file = self._source_files[row]
                cover_path = _find_cover_for_source_file(source_file, book_title)
            else:
                # Traditional mode: look for cover in the main directory
                if not self._wav_path:
                    next_missing_rows.add(row)
                    continue
                
                if not book_title:
                    next_missing_rows.add(row)
                    continue

                covers_by_title, err = _run_capturing_errors(
                    _find_covers_by_title, self._wav_path.parent
                )
                if err:
                    next_missing_rows.add(row)
                    continue
                assert covers_by_title is not None
                cover_path = covers_by_title.get(book_title.casefold())

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


