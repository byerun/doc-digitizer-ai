#!/usr/bin/env python3
"""
Line-by-line transcription review (PySide6): PDF line crops with editable text.

Requires Poppler (system) for pdf2image — see README.md.

``--working-dir`` matches ``transcribe-chunk-pdf.py``: the directory that contains
``chunk-pdfs/`` and ``transcriptions/``.

  python review-chunk-lines.py --working-dir <dir> --chunk-pdf <file.pdf>

Optional: ``--raw-json`` (defaults to ``transcriptions/<stem>_raw.json`` under the working dir).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Prompt-injected markers (not printed on the page); skip in the review UI.
_PAGE_MARKER_PATTERN = re.compile(r'^\s*//\s*Page\s+\d+\s*$', re.IGNORECASE)


def is_injected_page_marker(text: object) -> bool:
    if not isinstance(text, str):
        return False
    t = text.strip()
    if t.startswith('{empty}'):
        t = t[len('{empty}') :].strip()
    return bool(_PAGE_MARKER_PATTERN.match(t))


def reviewable_line_indices(lines: list) -> list[int]:
    return [i for i, ln in enumerate(lines) if not is_injected_page_marker(ln.get('text', ''))]


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace | None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return None

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
        '--chunk-pdf',
        required=True,
        help='Chunk PDF filename only (must exist under chunk-pdfs/).',
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


@dataclass(frozen=True)
class ReviewPaths:
    working_dir: Path
    chunk_pdf_path: Path
    raw_path: Path
    final_path: Path
    chunk_name: str
    stem: str


def resolve_review_paths(cli: argparse.Namespace) -> ReviewPaths | str:
    working_dir = cli.working_dir.resolve()
    chunk_pdfs_dir = working_dir / 'chunk-pdfs'
    transcriptions_dir = working_dir / 'transcriptions'
    if not chunk_pdfs_dir.is_dir():
        return (
            f'Expected a chunk-pdfs directory at {chunk_pdfs_dir}. '
            '--working-dir should be the project/work folder that contains '
            'chunk-pdfs/ (and usually transcriptions/), same as '
            'transcribe-chunk-pdf.py.'
        )

    chunk_name = cli.chunk_pdf.strip()
    if Path(chunk_name).name != chunk_name:
        return 'Use chunk PDF filename only, not a path.'
    if not chunk_name.lower().endswith('.pdf'):
        return "Chunk PDF filename must end with '.pdf'."

    chunk_pdf_path = chunk_pdfs_dir / chunk_name
    if not chunk_pdf_path.is_file():
        return f'Chunk PDF not found: {chunk_pdf_path}'

    stem = Path(chunk_name).stem
    if cli.raw_json is not None:
        raw_candidate = cli.raw_json
        raw_path = (
            (working_dir / raw_candidate).resolve()
            if not raw_candidate.is_absolute()
            else raw_candidate.resolve()
        )
    else:
        raw_path = transcriptions_dir / f'{stem}_raw.json'
    final_path = transcriptions_dir / f'{stem}_final.json'

    if not raw_path.is_file():
        return f'Raw JSON not found: {raw_path}'

    return ReviewPaths(
        working_dir=working_dir,
        chunk_pdf_path=chunk_pdf_path,
        raw_path=raw_path,
        final_path=final_path,
        chunk_name=chunk_name,
        stem=stem,
    )


def clamp_box_2d_to_pixels(
    box_2d: list,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    ymin, xmin, ymax, xmax = (int(box_2d[0]), int(box_2d[1]), int(box_2d[2]), int(box_2d[3]))
    left = int(round(xmin / 1000.0 * width))
    upper = int(round(ymin / 1000.0 * height))
    right = int(round(xmax / 1000.0 * width))
    lower = int(round(ymax / 1000.0 * height))
    left = max(0, min(left, width))
    right = max(0, min(right, width))
    upper = max(0, min(upper, height))
    lower = max(0, min(lower, height))
    if right <= left:
        right = min(width, left + 1)
    if lower <= upper:
        lower = min(height, upper + 1)
    box_h = lower - upper
    box_w = right - left
    pad_top = min(8, max(0, box_h // 14))
    pad_bot = min(28, max(3, box_h // 5 + 2))
    pad_x = min(24, max(1, box_w // 50 + 1))
    left = max(0, left - pad_x)
    upper = max(0, upper - pad_top)
    right = min(width, right + pad_x)
    lower = min(height, lower + pad_bot)
    if right <= left:
        right = min(width, left + 1)
    if lower <= upper:
        lower = min(height, upper + 1)
    return left, upper, right, lower


def estimate_transcription_font_px(text: str, crop_width: int | None) -> int:
    t = text.rstrip() if isinstance(text, str) else text
    n = max(len(t), 1)
    w = min(1100, max(crop_width or 640, 320))
    return max(13, min(160, int(w / (n * 0.48))))


def rstrip_line_text(value: object) -> object:
    if isinstance(value, str):
        return value.rstrip()
    return value


def load_page_images(pdf_path: Path) -> list[Image.Image]:
    return convert_from_path(str(pdf_path))


def load_payload(raw_path: Path, final_path: Path) -> dict:
    if final_path.exists():
        return json.loads(final_path.read_text(encoding='utf-8'))
    return json.loads(raw_path.read_text(encoding='utf-8'))


def save_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def pil_to_qpixmap(im: Image.Image) -> QPixmap:
    if im.mode != 'RGB':
        im = im.convert('RGB')
    w, h = im.size
    bpl = 3 * w
    buf = im.tobytes('raw', 'RGB')
    qimg = QImage(buf, w, h, bpl, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


def fit_line_edit_font(line_edit: QLineEdit, max_text_width: int) -> None:
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


class ReviewMainWindow(QMainWindow):
    def __init__(
        self,
        paths: ReviewPaths,
        page_images: list | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths = paths
        self.setWindowTitle(f'Line review — {paths.chunk_name}')
        self.resize(880, 620)
        self._page_images = page_images if page_images is not None else load_page_images(
            paths.chunk_pdf_path,
        )
        self._payload = load_payload(paths.raw_path, paths.final_path)
        self._source_raw_path = str(paths.raw_path)
        self._final_path = paths.final_path
        lines = self._payload.get('lines')
        if not isinstance(lines, list) or not lines:
            raise ValueError('Invalid payload: missing or empty "lines"')
        self._lines: list = lines
        self._review_indices = reviewable_line_indices(lines)
        if not self._review_indices:
            raise ValueError(
                'No lines to review: every entry looks like a synthetic '
                '`// Page N` marker.'
            )
        self._ridx = 0
        self._crop_pixmap: QPixmap | None = None
        self._raw_crop: Image.Image | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        n_skip = len(lines) - len(self._review_indices)
        self._header = QLabel(
            f'Chunk: {paths.chunk_name} · Raw: {paths.raw_path.name} · '
            f'Final: {paths.final_path.name}'
        )
        self._header.setWordWrap(True)
        root.addWidget(self._header)
        if n_skip:
            skip_lbl = QLabel(
                f'Skipping {n_skip} synthetic page marker line(s) (`// Page …`). '
                'They remain in the saved JSON.'
            )
            skip_lbl.setWordWrap(True)
            root.addWidget(skip_lbl)

        self._page_lbl = QLabel()
        self._line_lbl = QLabel()
        f = self._line_lbl.font()
        f.setPointSizeF(f.pointSizeF() + 2)
        f.setBold(True)
        self._line_lbl.setFont(f)
        root.addWidget(self._page_lbl)
        root.addWidget(self._line_lbl)

        self._err_lbl = QLabel()
        self._err_lbl.setStyleSheet('color: #b06000;')
        self._err_lbl.setWordWrap(True)
        root.addWidget(self._err_lbl)

        self._crop_label = QLabel()
        self._crop_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        root.addWidget(self._crop_label)

        self._line_edit = QLineEdit()
        self._line_edit.setStyleSheet(
            'QLineEdit { padding: 6px 8px; border: 1px solid #bbb; border-radius: 4px; }'
        )
        root.addWidget(self._line_edit)

        self._plain = QPlainTextEdit()
        self._plain.setFixedHeight(88)
        self._plain.setStyleSheet(
            'QPlainTextEdit { padding: 6px 8px; border: 1px solid #bbb; border-radius: 4px; }'
        )
        self._plain.hide()
        root.addWidget(self._plain)

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

        self._btn_prev.clicked.connect(self._on_prev)
        self._btn_next.clicked.connect(self._on_next)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_reload.clicked.connect(self._on_reload)
        self._line_edit.textChanged.connect(self._schedule_fit_font)
        self._plain.textChanged.connect(self._schedule_fit_font)

        self._show_line()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._apply_crop_scale_and_font)

    def _schedule_fit_font(self) -> None:
        QTimer.singleShot(0, self._fit_editor_font_only)

    def _max_crop_display_width(self) -> int:
        w = self.centralWidget().width() if self.centralWidget() else 800
        return max(320, min(1000, w - 32))

    def _commit_current(self) -> None:
        idx = self._review_indices[self._ridx]
        if self._plain.isVisible():
            self._lines[idx]['text'] = rstrip_line_text(self._plain.toPlainText())
        else:
            self._lines[idx]['text'] = rstrip_line_text(self._line_edit.text())

    def _show_line(self) -> None:
        n_review = len(self._review_indices)
        self._ridx = max(0, min(self._ridx, n_review - 1))
        idx = self._review_indices[self._ridx]
        line = self._lines[idx]
        page_number = line.get('page_number')
        box_2d = line.get('box_2d')
        text = line.get('text', '')
        if not isinstance(text, str):
            text = ''
        text = text.rstrip()

        self._err_lbl.clear()
        self._raw_crop = None
        self._crop_pixmap = None
        self._crop_label.clear()

        n_pages = len(self._page_images)
        err: str | None = None
        if not isinstance(page_number, int) or page_number < 1:
            err = f'Invalid page_number: {page_number!r}'
        elif page_number > n_pages:
            err = (
                f'page_number {page_number} is out of range '
                f'(chunk has {n_pages} page(s)).'
            )
        elif not isinstance(box_2d, list) or len(box_2d) != 4:
            err = f'Invalid box_2d: {box_2d!r}'
        else:
            page_img = self._page_images[page_number - 1]
            w, h = page_img.size
            left, upper, right, lower = clamp_box_2d_to_pixels(box_2d, w, h)
            self._raw_crop = page_img.crop((left, upper, right, lower))
            self._crop_pixmap = pil_to_qpixmap(self._raw_crop)

        if err:
            self._err_lbl.setText(err)

        pn = str(page_number) if isinstance(page_number, int) and page_number >= 1 else '—'
        self._page_lbl.setText(f'Page {pn}')
        self._line_lbl.setText(f'Line {self._ridx + 1} / {n_review}')

        multiline = '\n' in text
        if multiline:
            self._plain.setPlainText(text)
            self._plain.show()
            self._line_edit.hide()
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
            self._line_edit.show()
            self._plain.hide()

        self._btn_prev.setEnabled(self._ridx > 0)
        self._btn_next.setEnabled(self._ridx < n_review - 1)

        QTimer.singleShot(0, self._apply_crop_scale_and_font)

    def _apply_crop_scale_and_font(self) -> None:
        if self._raw_crop is None or self._crop_pixmap is None or self._crop_pixmap.isNull():
            return
        max_w = self._max_crop_display_width()
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
        if self._line_edit.isVisible():
            self._fit_editor_font_only()

    def _fit_editor_font_only(self) -> None:
        if not self._line_edit.isVisible():
            return
        inner = max(80, self._line_edit.width() - 16)
        fit_line_edit_font(self._line_edit, inner)

    def _on_prev(self) -> None:
        if self._ridx <= 0:
            return
        self._commit_current()
        self._ridx -= 1
        self._show_line()

    def _on_next(self) -> None:
        if self._ridx >= len(self._review_indices) - 1:
            return
        self._commit_current()
        self._ridx += 1
        self._show_line()

    def _on_save(self) -> None:
        self._commit_current()
        save_payload(self._final_path, self._payload)
        self.statusBar().showMessage(f'Wrote {self._final_path}', 6000)

    def _on_reload(self) -> None:
        self._commit_current()
        reply = QMessageBox.question(
            self,
            'Reload from raw',
            'Discard edits in memory and reload from raw JSON on disk?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._payload = json.loads(
            Path(self._source_raw_path).read_text(encoding='utf-8'),
        )
        self._lines = self._payload['lines']
        self._review_indices = reviewable_line_indices(self._lines)
        if not self._review_indices:
            QMessageBox.warning(self, 'Reload', 'No reviewable lines after reload.')
            return
        self._ridx = 0
        self._show_line()


def main() -> int:
    cli = parse_cli_args()
    if cli is None:
        print(
            'Usage: python review-chunk-lines.py '
            '--working-dir <dir> --chunk-pdf <filename.pdf>\n'
            'Optional: --raw-json <path>',
            file=sys.stderr,
        )
        return 2

    resolved = resolve_review_paths(cli)
    if isinstance(resolved, str):
        print(resolved, file=sys.stderr)
        return 1

    try:
        page_images = load_page_images(resolved.chunk_pdf_path)
    except Exception as exc:
        print(f'Could not rasterize PDF (is Poppler installed?). {exc}', file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    try:
        win = ReviewMainWindow(resolved, page_images=page_images)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
