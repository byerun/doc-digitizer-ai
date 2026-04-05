#!/usr/bin/env python3
"""
Line-by-line transcription review (PySide6): PDF line crops with editable text.

Requires Poppler (system) for pdf2image — see README.md.

``--working-dir`` matches ``transcribe-chunk-pdf.py``: the directory that contains
``chunk-pdfs/`` and ``transcriptions/``. Choose the chunk PDF from the window dropdown.

  python review-chunk-lines.py --working-dir <dir>

Optional: ``--raw-json`` (defaults to ``transcriptions/<stem>_raw.json`` under the working dir).

Domain logic lives in ``chunk_lines_model.py``; this file is View + Controller + entrypoint.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chunk_lines_model import ChunkLinesSession, list_chunk_pdf_filenames


class StackedSizeToCurrentWidget(QStackedWidget):
    """``QStackedWidget`` uses the max of all pages' size hints; we only need the visible page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self.updateGeometry)

    def sizeHint(self) -> QSize:
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


def estimate_transcription_font_px(text: str, crop_width: int | None) -> int:
    """Rough initial ``QFont`` pixel size for multiline editor rows (not used for single-line fit)."""
    t = text.rstrip() if isinstance(text, str) else text
    n = max(len(t), 1)
    w = min(1100, max(crop_width or 640, 320))
    return max(13, min(160, int(w / (n * 0.48))))


def pil_to_qpixmap(im: Image.Image) -> QPixmap:
    """Convert a Pillow image to a ``QPixmap`` for ``QLabel`` without writing temp files."""
    if im.mode != 'RGB':
        im = im.convert('RGB')
    w, h = im.size
    bpl = 3 * w
    buf = im.tobytes('raw', 'RGB')
    qimg = QImage(buf, w, h, bpl, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


def fit_line_edit_font(line_edit: QLineEdit, max_text_width: int) -> None:
    """Set the largest pixel font size so the full line fits in ``max_text_width`` pixels."""
    text = line_edit.text()
    font = QFont(line_edit.font())
    font.setStyleHint(QFont.SansSerif)
    if not text:
        font.setPixelSize(12)
        line_edit.setFont(font)
        return
    lo, hi = 8, 400
    best = 8
    while lo <= hi:
        mid = (lo + hi) // 2
        font.setPixelSize(mid)
        fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) <= max_text_width:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    font.setPixelSize(best)
    line_edit.setFont(font)


def _review_app_icon() -> QIcon:
    """Window icon: ``icons/review-chunk-lines.png`` beside this script (optional file)."""
    p = Path(__file__).resolve().parent / 'icons' / 'review-chunk-lines.png'
    if p.is_file():
        return QIcon(str(p))
    return QIcon()


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        description='Review and correct per-line transcriptions for a chunk PDF.',
    )
    parser.add_argument(
        '--working-dir',
        type=Path,
        default=Path('.'),
        help=(
            'Same as transcribe-chunk-pdf.py: directory containing '
            'chunk-pdfs/ and transcriptions/'
        ),
    )
    parser.add_argument(
        '--raw-json',
        type=Path,
        default=None,
        help=(
            'Path to *_raw.json; relative paths are under --working-dir '
            '(default: transcriptions/<stem>_raw.json)'
        ),
    )
    return parser.parse_args(argv)


class ReviewMainWindow(QMainWindow):
    """View: chunk selector, crop, editors, navigation — no transcription domain logic."""

    def __init__(
        self,
        working_dir: Path,
        chunk_pdf_names: list[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._working_dir = working_dir.resolve()
        self._chunk_pdf_names = chunk_pdf_names
        self._crop_pixmap: QPixmap | None = None
        self._raw_crop: Image.Image | None = None

        self.setWindowTitle('Line review')
        _ic = _review_app_icon()
        if not _ic.isNull():
            self.setWindowIcon(_ic)
        self.resize(880, 480)

        central = QWidget()
        self.setCentralWidget(central)
        central.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        chunk_row = QHBoxLayout()
        chunk_row.setSpacing(8)
        chunk_row.addWidget(QLabel('Chunk PDF'))
        self._chunk_combo = QComboBox()
        self._chunk_combo.setMinimumWidth(280)
        self._chunk_combo.addItems(chunk_pdf_names)
        chunk_row.addWidget(self._chunk_combo)
        chunk_row.addStretch()
        root.addLayout(chunk_row)

        self._n_skip_lbl = QLabel()
        self._n_skip_lbl.setWordWrap(False)
        self._n_skip_lbl.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        root.addWidget(self._n_skip_lbl, alignment=Qt.AlignmentFlag.AlignLeft)
        self._n_skip_lbl.hide()

        paths_wrap = QWidget()
        paths_grid = QGridLayout(paths_wrap)
        paths_grid.setContentsMargins(0, 0, 0, 0)
        paths_grid.setHorizontalSpacing(12)
        paths_grid.setVerticalSpacing(2)
        paths_grid.setColumnStretch(0, 0)
        paths_grid.setColumnStretch(1, 0)
        align_lv = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        self._raw_path_lbl = QLabel('—')
        self._final_path_lbl = QLabel('—')
        for lab, val, r in (
            ('Raw', self._raw_path_lbl, 0),
            ('Final', self._final_path_lbl, 1),
        ):
            a = QLabel(lab)
            a.setAlignment(align_lv)
            val.setAlignment(align_lv)
            paths_grid.addWidget(a, r, 0)
            paths_grid.addWidget(val, r, 1)
        paths_wrap.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Maximum,
        )
        root.addWidget(paths_wrap, alignment=Qt.AlignmentFlag.AlignLeft)

        self._page_lbl = QLabel()
        self._line_lbl = QLabel()
        page_font = self._page_lbl.font()
        page_font.setPointSizeF(page_font.pointSizeF() + 3)
        page_font.setBold(True)
        self._page_lbl.setFont(page_font)
        line_font = self._line_lbl.font()
        line_font.setPointSizeF(max(8.0, line_font.pointSizeF() - 0.5))
        line_font.setBold(False)
        self._line_lbl.setFont(line_font)
        _lbl_pol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._page_lbl.setSizePolicy(_lbl_pol)
        self._line_lbl.setSizePolicy(_lbl_pol)
        root.addWidget(self._page_lbl, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._line_lbl, alignment=Qt.AlignmentFlag.AlignLeft)

        self._err_lbl = QLabel()
        self._err_lbl.setStyleSheet('color: #b06000;')
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        root.addWidget(self._err_lbl, alignment=Qt.AlignmentFlag.AlignLeft)

        self._crop_label = QLabel()
        self._crop_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._crop_label.setContentsMargins(0, 0, 0, 0)
        self._crop_label.setStyleSheet('QLabel { margin: 0px; padding: 0px; }')
        self._crop_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )

        self._line_edit = QLineEdit()
        self._line_edit.setStyleSheet(
            'QLineEdit { padding: 6px 8px; border: 1px solid #bbb; border-radius: 4px; '
            'margin-top: 0px; margin-bottom: 0px; }'
        )
        self._line_edit.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        self._plain = QPlainTextEdit()
        self._plain.setFixedHeight(88)
        self._plain.setStyleSheet(
            'QPlainTextEdit { padding: 6px 8px; border: 1px solid #bbb; border-radius: 4px; '
            'margin-top: 0px; margin-bottom: 0px; }'
        )
        self._plain.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self._editor_stack = StackedSizeToCurrentWidget()
        self._editor_stack.addWidget(self._line_edit)
        self._editor_stack.addWidget(self._plain)
        self._editor_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self._editor_stack.setContentsMargins(0, 0, 0, 0)

        crop_editor = QVBoxLayout()
        crop_editor.setSpacing(4)
        crop_editor.setContentsMargins(0, 0, 0, 0)
        crop_editor.addWidget(self._crop_label, alignment=Qt.AlignmentFlag.AlignLeft)
        crop_editor.addWidget(self._editor_stack, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addLayout(crop_editor)

        btn_row = QHBoxLayout()
        self._btn_prev = QPushButton('◀ Prev')
        self._btn_next = QPushButton('Next ▶')
        self._btn_save = QPushButton('Save to final JSON')
        self._btn_reload = QPushButton('Reload from raw')
        btn_row.addWidget(self._btn_prev)
        btn_row.addWidget(self._btn_next)
        btn_row.addWidget(self._btn_save)
        btn_row.addWidget(self._btn_reload)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self.set_review_controls_enabled(False)

    @property
    def working_dir(self) -> Path:
        return self._working_dir

    @property
    def chunk_pdf_names(self) -> list[str]:
        return self._chunk_pdf_names

    @property
    def chunk_combo(self) -> QComboBox:
        return self._chunk_combo

    def connect_controller_signals(self, ctrl: 'ReviewChunkLinesController') -> None:
        self._chunk_combo.currentIndexChanged.connect(ctrl._on_chunk_combo_index_changed)
        self._btn_prev.clicked.connect(ctrl._on_prev)
        self._btn_next.clicked.connect(ctrl._on_next)
        self._btn_save.clicked.connect(ctrl._on_save)
        self._btn_reload.clicked.connect(ctrl._on_reload)
        self._line_edit.textChanged.connect(ctrl._on_text_changed)
        self._plain.textChanged.connect(ctrl._on_text_changed)

    def max_crop_display_width(self) -> int:
        w = self.centralWidget().width() if self.centralWidget() else 800
        return max(320, min(1000, w - 32))

    def sync_combo_to_chunk_name(self, chunk_name: str | None) -> None:
        if chunk_name is None:
            return
        idx = self._chunk_combo.findText(chunk_name)
        if idx >= 0:
            self._chunk_combo.blockSignals(True)
            self._chunk_combo.setCurrentIndex(idx)
            self._chunk_combo.blockSignals(False)

    def set_path_labels(self, raw_name: str, final_name: str) -> None:
        self._raw_path_lbl.setText(raw_name)
        self._final_path_lbl.setText(final_name)

    def set_skip_notice_visible(self, n_skip: int) -> None:
        if n_skip:
            self._n_skip_lbl.setText(
                f'Skipping {n_skip} synthetic page marker line(s) (`// Page …`). '
                'They remain in the saved JSON.',
            )
            self._n_skip_lbl.show()
        else:
            self._n_skip_lbl.clear()
            self._n_skip_lbl.hide()

    def set_review_controls_enabled(self, enabled: bool) -> None:
        self._btn_prev.setEnabled(enabled)
        self._btn_next.setEnabled(enabled)
        self._btn_save.setEnabled(enabled)
        self._btn_reload.setEnabled(enabled)
        self._line_edit.setEnabled(enabled)
        self._plain.setEnabled(enabled)
        self._crop_label.setEnabled(enabled)

    def populate_editable_line(
        self,
        raw_crop: Image.Image | None,
        crop_pixmap: QPixmap | None,
        err: str | None,
        page_display: str,
        line_display: str,
        text: str,
        multiline: bool,
        prev_enabled: bool,
        next_enabled: bool,
    ) -> None:
        self._err_lbl.clear()
        self._raw_crop = raw_crop
        self._crop_pixmap = crop_pixmap
        self._crop_label.clear()

        if err:
            self._err_lbl.setText(err)

        self._page_lbl.setText(page_display)
        self._line_lbl.setText(line_display)

        self._line_edit.blockSignals(True)
        self._plain.blockSignals(True)
        try:
            if multiline:
                self._plain.setPlainText(text)
                self._editor_stack.setCurrentIndex(1)
                px = estimate_transcription_font_px(
                    text,
                    self._raw_crop.width if self._raw_crop else None,
                )
                pf = QFont()
                pf.setPixelSize(px)
                pf.setStyleHint(QFont.SansSerif)
                self._plain.setFont(pf)
            else:
                self._line_edit.setText(text)
                self._editor_stack.setCurrentIndex(0)
        finally:
            self._line_edit.blockSignals(False)
            self._plain.blockSignals(False)

        self._btn_prev.setEnabled(prev_enabled)
        self._btn_next.setEnabled(next_enabled)

        QTimer.singleShot(0, self.apply_crop_scale_and_font)

    def apply_crop_scale_and_font(self) -> None:
        if self._raw_crop is None or self._crop_pixmap is None or self._crop_pixmap.isNull():
            return
        max_w = self.max_crop_display_width()
        ow = self._crop_pixmap.width()
        if ow > max_w:
            scaled = self._crop_pixmap.scaledToWidth(
                max_w,
                Qt.SmoothTransformation,
            )
        else:
            scaled = self._crop_pixmap
        self._crop_label.setPixmap(scaled)
        self._crop_label.setFixedWidth(scaled.width())

        self._line_edit.setFixedWidth(scaled.width())
        self._plain.setFixedWidth(scaled.width())
        self._editor_stack.setFixedWidth(scaled.width())
        if self._editor_stack.currentIndex() == 0:
            self.fit_editor_font_only()

    def fit_editor_font_only(self) -> None:
        if self._editor_stack.currentIndex() != 0:
            return
        inner = max(80, self._line_edit.width() - 16)
        fit_line_edit_font(self._line_edit, inner)

    def schedule_fit_font(self) -> None:
        QTimer.singleShot(0, self.fit_editor_font_only)

    def commit_text_from_editors(self) -> str:
        if self._editor_stack.currentIndex() == 1:
            return self._plain.toPlainText()
        return self._line_edit.text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self.apply_crop_scale_and_font)


class ReviewChunkLinesController:
    """Connects ``ChunkLinesSession`` to ``ReviewMainWindow`` actions."""

    def __init__(
        self,
        session: ChunkLinesSession,
        view: ReviewMainWindow,
        raw_json_cli: Path | None,
    ) -> None:
        self._session = session
        self._view = view
        self._raw_json_cli = raw_json_cli
        view.connect_controller_signals(self)

    def try_initial_chunk(self) -> None:
        names = self._view.chunk_pdf_names
        for name in names:
            if self._load_chunk(name, show_error=False):
                self._view.sync_combo_to_chunk_name(
                    self._session.paths.chunk_name if self._session.paths else None,
                )
                return
        self._view.chunk_combo.setCurrentIndex(0)
        self._load_chunk(names[0], show_error=True)

    def _on_text_changed(self) -> None:
        self._session.dirty = True
        self._view.schedule_fit_font()

    def _sync_combo_to_loaded_chunk(self) -> None:
        if self._session.paths is None:
            return
        self._view.sync_combo_to_chunk_name(self._session.paths.chunk_name)

    def _on_chunk_combo_index_changed(self, index: int) -> None:
        if index < 0:
            return
        name = self._view.chunk_combo.itemText(index)
        if self._session.paths is not None and name == self._session.paths.chunk_name:
            return
        self._switch_to_chunk(name)

    def _switch_to_chunk(self, chunk_name: str) -> None:
        if self._session.paths is not None and self._session.dirty:
            box = QMessageBox(self._view)
            box.setWindowTitle('Unsaved changes')
            box.setText('You have unsaved edits for this chunk.')
            box.setInformativeText('Save them before opening another chunk?')
            box.setStandardButtons(
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            box.setDefaultButton(QMessageBox.Save)
            reply = box.exec()
            if reply == QMessageBox.Cancel:
                self._sync_combo_to_loaded_chunk()
                return
            if reply == QMessageBox.Save:
                self._commit_current()
                self._session.save_to_final()
                self._session.dirty = False
        if not self._load_chunk(chunk_name, show_error=True):
            self._sync_combo_to_loaded_chunk()

    def _commit_current(self) -> None:
        text = self._view.commit_text_from_editors()
        self._session.commit_editable_text(text)

    def _load_chunk(self, chunk_name: str, show_error: bool) -> bool:
        err = self._session.load_chunk(
            self._view.working_dir,
            chunk_name,
            self._raw_json_cli,
        )
        if err is not None:
            if show_error:
                QMessageBox.warning(self._view, 'Cannot load chunk', err)
            return False

        paths = self._session.paths
        assert paths is not None

        self._view.setWindowTitle(f'Line review — {paths.chunk_name}')
        self._view.set_path_labels(paths.raw_path.name, paths.final_path.name)
        n_skip = len(self._session.lines) - len(self._session.editable_indices)
        self._view.set_skip_notice_visible(n_skip)

        self._view.set_review_controls_enabled(True)
        self._show_line()
        return True

    def _show_line(self) -> None:
        self._session.clamp_editable_ridx()
        s = self._session
        n_editable = len(s.editable_indices)
        ridx = s.editable_ridx
        line = s.line_at_editable_ridx()
        page_number = line.get('page_number')
        text = line.get('text', '')
        if not isinstance(text, str):
            text = ''
        text = text.rstrip()

        raw_crop, cerr = s.crop_for_current_editable()
        pixmap = pil_to_qpixmap(raw_crop) if raw_crop is not None else None

        pn = str(page_number) if isinstance(page_number, int) and page_number >= 1 else '—'
        page_display = f'Page {pn}'
        line_display = f'Line {ridx + 1} / {n_editable}'

        multiline = '\n' in text
        self._view.populate_editable_line(
            raw_crop,
            pixmap,
            cerr,
            page_display,
            line_display,
            text,
            multiline,
            ridx > 0,
            ridx < n_editable - 1,
        )

    def _on_prev(self) -> None:
        if not self._session.is_loaded or self._session.editable_ridx <= 0:
            return
        self._commit_current()
        self._session.editable_ridx -= 1
        self._show_line()

    def _on_next(self) -> None:
        s = self._session
        if not s.is_loaded or s.editable_ridx >= len(s.editable_indices) - 1:
            return
        self._commit_current()
        s.editable_ridx += 1
        self._show_line()

    def _on_save(self) -> None:
        if not self._session.is_loaded:
            return
        self._commit_current()
        paths = self._session.paths
        assert paths is not None
        self._session.save_to_final()
        self._session.dirty = False
        self._view.statusBar().showMessage(f'Wrote {paths.final_path}', 6000)

    def _on_reload(self) -> None:
        if not self._session.is_loaded:
            return
        self._commit_current()
        reply = QMessageBox.question(
            self._view,
            'Reload from raw',
            'Discard edits in memory and reload from raw JSON on disk?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        err = self._session.reload_from_raw_disk()
        if err is not None:
            QMessageBox.warning(self._view, 'Reload', err)
            return
        self._show_line()


def main() -> int:
    cli = parse_cli_args()
    working_dir = cli.working_dir.resolve()
    chunk_pdfs_dir = working_dir / 'chunk-pdfs'
    if not chunk_pdfs_dir.is_dir():
        print(
            f'Expected a chunk-pdfs directory at {chunk_pdfs_dir}. '
            '--working-dir should be the folder that contains chunk-pdfs/ '
            'and transcriptions/.',
            file=sys.stderr,
        )
        return 1

    pdf_names = list_chunk_pdf_filenames(chunk_pdfs_dir)
    if not pdf_names:
        print(f'No .pdf files found in {chunk_pdfs_dir}', file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName('Line review')
    _ic = _review_app_icon()
    if not _ic.isNull():
        app.setWindowIcon(_ic)

    session = ChunkLinesSession()
    win = ReviewMainWindow(working_dir, pdf_names)
    ctrl = ReviewChunkLinesController(session, win, raw_json_cli=cli.raw_json)
    win.show()
    ctrl.try_initial_chunk()
    _install_terminal_interrupt_handlers(app)
    return app.exec()


def _install_terminal_interrupt_handlers(app: QApplication) -> None:
    def _quit(_signum=None, _frame=None) -> None:
        app.quit()

    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, _quit)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _quit)

    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)
    app._sigint_poll_timer = timer


if __name__ == '__main__':
    sys.exit(main())
