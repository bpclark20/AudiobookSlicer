"""Dialog classes for AudioBookSlicer GUI."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui_constants import _HERE, _has_qt_multimedia, DEFAULT_SIZE_UNIT, VBR_QUALITY_BITRATE_HINTS

if _has_qt_multimedia:
    from PyQt6.QtMultimedia import QMediaDevices  # noqa: F811


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About AudioBook Slicer")
        self.setMinimumWidth(620)

        icon_path = _HERE / "icon.png"
        self._icon_base = QPixmap(str(icon_path)) if icon_path.is_file() else QPixmap()

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        icon_wrap = QWidget(self)
        icon_layout = QVBoxLayout(icon_wrap)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.addStretch(1)

        self._icon_label = QLabel(icon_wrap)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setMinimumSize(1, 1)
        self._icon_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        icon_layout.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        icon_layout.addStretch(1)

        text_wrap = QWidget(self)
        text_layout = QVBoxLayout(text_wrap)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(10)

        self._title_label = QLabel("AudioBook Slicer")
        self._title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        text_layout.addWidget(self._title_label)

        self._body_label = QLabel(
            "AudioBook Slicer is a desktop utility for building chaptered audiobook output.\n\n"
            "You can split M4B files into chapter WAVs, load one or many WAV files, reorder and edit chapter titles, "
            "manage chapter artwork, and encode a final MP3 with chapter metadata.\n\n"
            "The application is designed to keep long operations visible and controllable so you can cancel quickly "
            "and continue working without restarting."
        )
        self._body_label.setWordWrap(True)
        self._body_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._body_label.setMinimumWidth(430)
        text_layout.addWidget(self._body_label, 1)

        root.addWidget(icon_wrap, 1)
        root.addWidget(text_wrap, 3)

        text_height = (
            self._title_label.sizeHint().height()
            + text_layout.spacing()
            + self._body_label.sizeHint().height()
        )
        frame_height = root.contentsMargins().top() + root.contentsMargins().bottom() + 8
        initial_height = max(220, text_height + frame_height)
        self.resize(640, initial_height)
        self._refresh_icon()
        self.setFixedSize(self.size())

    def _refresh_icon(self) -> None:
        if self._icon_base.isNull():
            self._icon_label.setText("No icon")
            return

        max_by_height = max(48, self.height() - 80)
        max_by_width = max(48, self.width() // 4)
        side_cap = max(100, self.height() // 3)
        side = max(48, min(max_by_height, max_by_width, side_cap))
        scaled = self._icon_base.scaled(
            side,
            side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._icon_label.setText("")
        self._icon_label.setPixmap(scaled)


class OptionsDialog(QDialog):
    def __init__(
        self,
        *,
        use_vbr: bool,
        vbr_quality: int,
        cbr_bitrate_kbps: int,
        channels: int,
        size_limit_text: str,
        size_unit: str,
        preview_device: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        encoding_row = QHBoxLayout()
        encoding_label = QLabel("Encoding")
        encoding_label.setFixedWidth(90)
        encoding_row.addWidget(encoding_label)

        self._vbr_radio = QRadioButton("VBR")
        self._cbr_radio = QRadioButton("CBR")
        self._encode_mode_group = QButtonGroup(self)
        self._encode_mode_group.addButton(self._vbr_radio)
        self._encode_mode_group.addButton(self._cbr_radio)
        encoding_row.addWidget(self._vbr_radio)
        encoding_row.addWidget(self._cbr_radio)

        self._vbr_quality_label = QLabel("quality level")
        encoding_row.addWidget(self._vbr_quality_label)

        self._vbr_q_combo = QComboBox()
        self._vbr_q_combo.setMinimumContentsLength(16)
        self._vbr_q_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._vbr_q_combo.setMinimumWidth(170)
        for value in range(10):
            approx = VBR_QUALITY_BITRATE_HINTS.get(value, "")
            if approx:
                label = f"{value} (~{approx})"
            else:
                label = str(value)
            self._vbr_q_combo.addItem(label, value)
        encoding_row.addWidget(self._vbr_q_combo)

        self._cbr_bitrate_label = QLabel("kbps")
        encoding_row.addWidget(self._cbr_bitrate_label)

        self._cbr_bitrate_spin = QSpinBox()
        self._cbr_bitrate_spin.setRange(48, 384)
        self._cbr_bitrate_spin.setSingleStep(8)
        self._cbr_bitrate_spin.setFixedWidth(88)
        encoding_row.addWidget(self._cbr_bitrate_spin)
        encoding_row.addStretch()
        root.addLayout(encoding_row)

        channels_row = QHBoxLayout()
        channels_label = QLabel("Channels")
        channels_label.setFixedWidth(90)
        channels_row.addWidget(channels_label)

        self._mono_radio = QRadioButton("Mono")
        self._stereo_radio = QRadioButton("Stereo")
        self._channel_group = QButtonGroup(self)
        self._channel_group.addButton(self._mono_radio)
        self._channel_group.addButton(self._stereo_radio)
        channels_row.addWidget(self._mono_radio)
        channels_row.addWidget(self._stereo_radio)
        channels_row.addStretch()
        root.addLayout(channels_row)

        size_limit_row = QHBoxLayout()
        size_limit_label = QLabel("Size Limit")
        size_limit_label.setFixedWidth(90)
        size_limit_row.addWidget(size_limit_label)

        self._size_limit_edit = QLineEdit()
        self._size_limit_edit.setToolTip("e.g. 1,500,000,000 (blank or 0 = no limit)")
        self._size_limit_edit.setFixedWidth(185)
        size_limit_row.addWidget(self._size_limit_edit)

        self._size_unit_group = QButtonGroup(self)
        for _unit_label in ("b", "kb", "Mb", "Gb", "B", "KB", "MB", "GB"):
            _rb = QRadioButton(_unit_label)
            self._size_unit_group.addButton(_rb)
            size_limit_row.addWidget(_rb)

        size_limit_row.addStretch()
        root.addLayout(size_limit_row)

        preview_device_row = QHBoxLayout()
        preview_device_label = QLabel("Preview Device")
        preview_device_label.setFixedWidth(90)
        preview_device_row.addWidget(preview_device_label)

        self._preview_device_combo = QComboBox()
        self._preview_device_combo.setMinimumWidth(320)
        self._preview_device_combo.setToolTip(
            "Automatically probed output devices for chapter preview playback."
        )
        preview_device_row.addWidget(self._preview_device_combo)

        self._refresh_devices_btn = QPushButton("Refresh Devices")
        self._refresh_devices_btn.clicked.connect(self._on_refresh_devices_clicked)
        preview_device_row.addWidget(self._refresh_devices_btn)
        preview_device_row.addStretch()
        root.addLayout(preview_device_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._vbr_q_combo.setCurrentIndex(max(0, min(9, int(vbr_quality))))
        self._cbr_bitrate_spin.setValue(max(48, min(384, int(cbr_bitrate_kbps))))
        self._size_limit_edit.setText(size_limit_text)
        self._populate_preview_devices(selected_device=preview_device)

        if channels == 2:
            self._stereo_radio.setChecked(True)
        else:
            self._mono_radio.setChecked(True)

        found_unit = False
        for btn in self._size_unit_group.buttons():
            if btn.text() == size_unit:
                btn.setChecked(True)
                found_unit = True
                break
        if not found_unit:
            for btn in self._size_unit_group.buttons():
                if btn.text() == DEFAULT_SIZE_UNIT:
                    btn.setChecked(True)
                    break

        if use_vbr:
            self._vbr_radio.setChecked(True)
        else:
            self._cbr_radio.setChecked(True)

        self._vbr_radio.toggled.connect(self._on_encode_mode_changed)
        self._cbr_radio.toggled.connect(self._on_encode_mode_changed)
        self._on_encode_mode_changed()

    def _on_encode_mode_changed(self) -> None:
        use_vbr = self._vbr_radio.isChecked()
        self._vbr_quality_label.setVisible(use_vbr)
        self._vbr_q_combo.setVisible(use_vbr)
        self._cbr_bitrate_label.setVisible(not use_vbr)
        self._cbr_bitrate_spin.setVisible(not use_vbr)

    def values(self) -> tuple[bool, int, int, int, str, str, str]:
        checked = self._size_unit_group.checkedButton()
        unit = checked.text() if checked is not None else DEFAULT_SIZE_UNIT
        selected_device = self._preview_device_combo.currentData()
        preview_device = str(selected_device).strip() if selected_device is not None else ""
        return (
            self._vbr_radio.isChecked(),
            int(self._vbr_q_combo.currentData()),
            int(self._cbr_bitrate_spin.value()),
            2 if self._stereo_radio.isChecked() else 1,
            self._size_limit_edit.text().strip(),
            unit,
            preview_device,
        )

    def _populate_preview_devices(self, selected_device: str) -> None:
        self._preview_device_combo.blockSignals(True)
        self._preview_device_combo.clear()

        discovered_names: list[str] = []
        default_name = ""
        if _has_qt_multimedia:
            try:
                devices = QMediaDevices.audioOutputs()
                default_name = QMediaDevices.defaultAudioOutput().description().strip()
                for device in devices:
                    name = device.description().strip()
                    if not name or name in discovered_names:
                        continue
                    discovered_names.append(name)
                    label = name
                    self._preview_device_combo.addItem(label, name)
            except Exception:
                pass

        # Empty value means ffplay uses the system default output.
        default_label = "System Default"
        if default_name:
            default_label = f"System Default ({default_name})"
        self._preview_device_combo.insertItem(0, default_label, "")

        target = selected_device.strip()

        if target:
            idx = self._preview_device_combo.findData(target)
            if idx >= 0:
                self._preview_device_combo.setCurrentIndex(idx)
            else:
                self._preview_device_combo.addItem(f"{target} (Unavailable)", target)
                self._preview_device_combo.setCurrentIndex(self._preview_device_combo.count() - 1)
        else:
            self._preview_device_combo.setCurrentIndex(0)

        self._preview_device_combo.blockSignals(False)

    def _on_refresh_devices_clicked(self) -> None:
        current_data = self._preview_device_combo.currentData()
        selected_device = str(current_data).strip() if current_data is not None else ""
        self._populate_preview_devices(selected_device=selected_device)


