"""Reusable Qt widget classes for AudioBookSlicer GUI."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDropEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableWidget,
)

from gui_constants import CHAPTER_PREVIEW_PROGRESS_ROLE, CHAPTER_PROGRESS_ROLE


class ChapterProgressDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if painter is None:
            return
        preview_progress_data = index.data(CHAPTER_PREVIEW_PROGRESS_ROLE)
        encode_progress_data = index.data(CHAPTER_PROGRESS_ROLE)

        progress_data: int | float | None = None
        fill_color = QColor(70, 140, 90, 140)
        if isinstance(preview_progress_data, (int, float)) and preview_progress_data > 0:
            progress_data = preview_progress_data
            fill_color = QColor(60, 120, 200, 165)
        elif isinstance(encode_progress_data, (int, float)) and encode_progress_data > 0:
            progress_data = encode_progress_data

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
    previewSeekRequested = pyqtSignal(int, float)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._drag_row = -1

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        seek_row = -1
        seek_fraction = 0.0
        if e is not None and e.button() == Qt.MouseButton.LeftButton:
            pt = e.position().toPoint()
            self._drag_row = self.rowAt(pt.y())
            col = self.columnAt(pt.x())
            if self._drag_row >= 0 and col == 1:
                _model = self.model()
                if _model is None:
                    super().mousePressEvent(e)
                    return
                idx = _model.index(self._drag_row, col)
                rect = self.visualRect(idx)
                if rect.width() > 0:
                    seek_row = self._drag_row
                    seek_fraction = (pt.x() - rect.left()) / rect.width()
                    seek_fraction = max(0.0, min(1.0, float(seek_fraction)))
        super().mousePressEvent(e)
        if seek_row >= 0:
            self.previewSeekRequested.emit(seek_row, seek_fraction)

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


