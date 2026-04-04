#!/usr/bin/env python3
"""
Line-by-line transcription review (PySide6): PDF line crops with editable text.

Requires Poppler (system) for pdf2image — see README.md.

``--working-dir`` matches ``transcribe-chunk-pdf.py``: the directory that contains
``chunk-pdfs/`` and ``transcriptions/``.

  python review-chunk-lines.py --working-dir <dir> --chunk-pdf <file.pdf>

Optional: ``--raw-json`` (defaults to ``transcriptions/<stem>_raw.json`` under the working dir).

**Editing this file:** Skim the ``# ---`` block below imports for data flow and UI split. JSON
shape matches Pass 1 output (see ``transcription.schema.json`` / ``prompt.md``). Crop math is
isolated in ``clamp_box_2d_to_pixels``; Qt layout and font fitting in ``ReviewMainWindow`` and
``fit_line_edit_font``. ``main()`` wires CLI → validate paths → rasterize once → window.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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

# --- Architecture (quick map for future changes) ---
# Data: ``payload['lines']`` is the full Pass 1/2 list. ``reviewable_line_indices`` skips
#   synthetic ``// Page N`` markers (see ``_PAGE_MARKER_PATTERN``); UI index ``_ridx`` walks
#   *that* subset only. Saving always writes the whole ``payload`` to ``*_final.json``.
# Raster: ``_page_images[i]`` is PIL RGB for chunk page i+1; crops use ``clamp_box_2d_to_pixels``
#   then ``Image.crop`` → ``pil_to_qpixmap`` for the QLabel.
# Editors: Default path is single-line ``QLineEdit`` + ``fit_line_edit_font`` (binary search on
#   pixel size vs ``QFontMetrics.horizontalAdvance``). If ``'\\n' in text``, multiline
#   ``QPlainTextEdit`` + ``estimate_transcription_font_px`` only (no per-keystroke refit).
# Layout: Crop and editor widths are forced equal after optional ``scaledToWidth`` so the text
#   column lines up under the bitmap; resize uses ``QTimer.singleShot(0, ...)`` to run after
#   geometry is known.

# Prompt-injected markers (not printed on the page); skip in the review UI.
# Must stay aligned with Pass 1 prompt / any transcribe normalization of line text.
_PAGE_MARKER_PATTERN = re.compile(r'^\s*//\s*Page\s+\d+\s*$', re.IGNORECASE)


def is_injected_page_marker(text: object) -> bool:
    if not isinstance(text, str):
        return False
    t = text.strip()
    if t.startswith('{empty}'):
        t = t[len('{empty}') :].strip()
    return bool(_PAGE_MARKER_PATTERN.match(t))


def reviewable_line_indices(lines: list) -> list[int]:
    # Indices into ``payload['lines']`` for rows the human should edit (markers still in JSON).
    return [i for i, ln in enumerate(lines) if not is_injected_page_marker(ln.get('text', ''))]


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace | None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return None

    # Keep flags compatible with README examples; ``main()`` requires ``--chunk-pdf``.
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
    # Resolved absolute paths for the chunk PDF and JSON; ``stem`` is chunk filename without .pdf.
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

    # ``final_path`` is not required to exist; ``load_payload`` prefers it when present.
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
    """Turn a model line box into a PIL crop rectangle in pixel coordinates.

    Pass 1 stores ``box_2d`` as four integers ``[ymin, xmin, ymax, xmax]`` on a
    0–1000 grid aligned to the rasterized page (same aspect as ``width`` × ``height``).
    This function maps that box to ``(left, upper, right, lower)`` for ``Image.crop``,
    where ``right`` and ``lower`` are **exclusive** Pillow indices (see Pillow docs).

    Steps: scale to pixels → clamp to the page (model noise / rounding can sit on or
    outside edges) → ensure a non-empty box → add a little padding so ascenders,
    descenders, and side bearings are not clipped → clamp again after padding.

    Padding is derived from the **box size**, not the full page, so tall raster pages
    do not add huge strips that pull in the next line.
    """
    ymin, xmin, ymax, xmax = (int(box_2d[0]), int(box_2d[1]), int(box_2d[2]), int(box_2d[3]))

    # Map normalized 0–1000 edges to pixel columns/rows on this page image.
    left = int(round(xmin / 1000.0 * width))
    upper = int(round(ymin / 1000.0 * height))
    right = int(round(xmax / 1000.0 * width))
    lower = int(round(ymax / 1000.0 * height))

    # Clamp every edge into the image bounds. The model can overshoot slightly, or
    # rounding can land past the last row/column; without this, PIL gets invalid boxes.
    # Using ``min(..., width)`` / ``min(..., height)`` allows exclusive right/lower to
    # reach ``width`` / ``height``, which is valid for a full-bleed crop.
    left = max(0, min(left, width))
    right = max(0, min(right, width))
    upper = max(0, min(upper, height))
    lower = max(0, min(lower, height))

    # Collapsed or inverted box (e.g. bad model output): force a 1×1 region inside the page.
    if right <= left:
        right = min(width, left + 1)
    if lower <= upper:
        lower = min(height, upper + 1)

    box_h = lower - upper
    box_w = right - left

    # Expand the rect slightly. Amounts scale with the line box so we do not add
    # page-sized padding on tall DPI rasters (which would include the next line).
    pad_top = min(8, max(0, box_h // 14))
    pad_bot = min(28, max(3, box_h // 5 + 2))
    pad_x = min(24, max(1, box_w // 50 + 1))

    left = max(0, left - pad_x)
    upper = max(0, upper - pad_top)
    right = min(width, right + pad_x)
    lower = min(height, lower + pad_bot)

    # Padding can push an edge back across a corner case; enforce non-empty again.
    if right <= left:
        right = min(width, left + 1)
    if lower <= upper:
        lower = min(height, upper + 1)
    return left, upper, right, lower


def estimate_transcription_font_px(text: str, crop_width: int | None) -> int:
    """Rough initial ``QFont`` pixel size for multiline editor rows (not used for single-line fit).

    Single-line fields get ``fit_line_edit_font`` after layout. This helper only seeds
    ``QPlainTextEdit`` when the JSON line contains newlines, where we do not run the
    metric binary search. Heuristic: wider crops and shorter strings → larger px, capped
    so the first paint is in a sane range before any user interaction.
    """
    t = text.rstrip() if isinstance(text, str) else text
    n = max(len(t), 1)
    # Nominal column width for the heuristic (crop width, clamped so tiny/huge crops behave).
    w = min(1100, max(crop_width or 640, 320))
    # Empirical divisor ~chars-per-em at the target density; result clamped for readability.
    return max(13, min(160, int(w / (n * 0.48))))


def rstrip_line_text(value: object) -> object:
    # Avoid trailing-space noise when saving; keeps diff noise down in ``*_final.json``.
    if isinstance(value, str):
        return value.rstrip()
    return value


def load_page_images(pdf_path: Path) -> list[Image.Image]:
    # One PIL image per page of the chunk PDF; DPI is pdf2image/poppler defaults unless changed.
    return convert_from_path(str(pdf_path))


def load_payload(raw_path: Path, final_path: Path) -> dict:
    # Resume editing: if a final file exists, load it instead of raw.
    if final_path.exists():
        return json.loads(final_path.read_text(encoding='utf-8'))
    return json.loads(raw_path.read_text(encoding='utf-8'))


def save_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def _review_app_icon() -> QIcon:
    """Window icon: ``icons/review-chunk-lines.png`` beside this script (optional file)."""
    p = Path(__file__).resolve().parent / 'icons' / 'review-chunk-lines.png'
    if p.is_file():
        return QIcon(str(p))
    return QIcon()


def pil_to_qpixmap(im: Image.Image) -> QPixmap:
    """Convert a Pillow image to a ``QPixmap`` for ``QLabel`` without writing temp files.

    Builds a tightly packed RGB888 buffer: **bytes per line** = 3 × width (no row padding).
    ``QImage`` is created from those bytes, then copied into a ``QPixmap`` for display.
    """
    # Qt does not handle palette/LA/etc. uniformly for this path; normalize to 8-bit RGB.
    if im.mode != 'RGB':
        im = im.convert('RGB')
    w, h = im.size
    bpl = 3 * w
    buf = im.tobytes('raw', 'RGB')
    qimg = QImage(buf, w, h, bpl, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


def fit_line_edit_font(line_edit: QLineEdit, max_text_width: int) -> None:
    """Set the largest pixel font size so the full line fits in ``max_text_width`` pixels.

    ``max_text_width`` should match the drawable text width inside the field (roughly widget
    width minus padding and frame). Uses integer pixel sizes and ``QFontMetrics.horizontalAdvance``
    for the whole string so the transcription line visually matches the crop width above.
    """
    text = line_edit.text()
    font = QFont(line_edit.font())
    font.setStyleHint(QFont.SansSerif)
    if not text:
        font.setPixelSize(12)
        line_edit.setFont(font)
        return
    # Binary search on pixel size: largest size such that rendered width <= budget.
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
    """Main window: one reviewable line at a time — crop image, editor, prev/next/save/reload."""

    # State: ``_ridx`` indexes ``_review_indices``; ``_lines`` is alias to ``payload['lines']`` (mutated in place).

    def __init__(
        self,
        paths: ReviewPaths,
        page_images: list | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths = paths
        self.setWindowTitle(f'Line review — {paths.chunk_name}')
        _ic = _review_app_icon()
        if not _ic.isNull():
            self.setWindowIcon(_ic)
        self.resize(880, 480)
        # Optional preloaded raster avoids double pdf2image work when main() already validated Poppler.
        self._page_images = page_images if page_images is not None else load_page_images(
            paths.chunk_pdf_path,
        )
        self._payload = load_payload(paths.raw_path, paths.final_path)
        self._source_raw_path = str(paths.raw_path)  # Used only by Reload (re-read raw from disk).
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

        # UI: caption, optional skip notice, crop QLabel, single-line QLineEdit vs multiline plain.
        central = QWidget()
        self.setCentralWidget(central)
        central.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        n_skip = len(lines) - len(self._review_indices)
        paths_wrap = QWidget()
        paths_grid = QGridLayout(paths_wrap)
        paths_grid.setContentsMargins(0, 0, 0, 0)
        paths_grid.setHorizontalSpacing(12)
        paths_grid.setVerticalSpacing(2)
        paths_grid.setColumnStretch(0, 0)
        paths_grid.setColumnStretch(1, 0)
        align_lv = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for r, (label, value) in enumerate(
            (
                ('Chunk', paths.chunk_name),
                ('Raw', paths.raw_path.name),
                ('Final', paths.final_path.name),
            ),
        ):
            lab = QLabel(label)
            val = QLabel(value)
            lab.setAlignment(align_lv)
            val.setAlignment(align_lv)
            paths_grid.addWidget(lab, r, 0)
            paths_grid.addWidget(val, r, 1)
        paths_wrap.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Maximum,
        )
        root.addWidget(paths_wrap, alignment=Qt.AlignmentFlag.AlignLeft)

        if n_skip:
            skip_lbl = QLabel(
                f'Skipping {n_skip} synthetic page marker line(s) (`// Page …`). '
                'They remain in the saved JSON.'
            )
            skip_lbl.setWordWrap(False)
            skip_lbl.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Maximum,
            )
            root.addWidget(skip_lbl, alignment=Qt.AlignmentFlag.AlignLeft)

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

        # Single-line path is default; ``_plain`` is for rare JSON lines with embedded newlines.
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

        # Isolate crop ↔ editor gap at 4px (avoid root column spacing stacking with style margins).
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

        self._btn_prev.clicked.connect(self._on_prev)
        self._btn_next.clicked.connect(self._on_next)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_reload.clicked.connect(self._on_reload)
        self._line_edit.textChanged.connect(self._schedule_fit_font)
        self._plain.textChanged.connect(self._schedule_fit_font)

        self._show_line()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Defer rescale until after layout has the new size; singleShot(0) runs next event-loop tick.
        QTimer.singleShot(0, self._apply_crop_scale_and_font)

    def _schedule_fit_font(self) -> None:
        """Coalesce rapid keystrokes: refit font once the line edit has settled for this tick."""
        QTimer.singleShot(0, self._fit_editor_font_only)

    def _max_crop_display_width(self) -> int:
        """Max width for the scaled crop (and matching editor), so huge scans don't overflow the window."""
        w = self.centralWidget().width() if self.centralWidget() else 800
        return max(320, min(1000, w - 32))

    def _commit_current(self) -> None:
        """Write the active editor’s text into ``payload['lines'][idx]`` for the current review index."""
        idx = self._review_indices[self._ridx]
        if self._editor_stack.currentIndex() == 1:
            self._lines[idx]['text'] = rstrip_line_text(self._plain.toPlainText())
        else:
            self._lines[idx]['text'] = rstrip_line_text(self._line_edit.text())

    def _show_line(self) -> None:
        """Load ``self._ridx`` into the UI: crop from PDF page, labels, and editor text.

        Does not commit the previous line — callers must ``_commit_current`` before changing
        ``_ridx`` (navigation buttons do that). Schedules ``_apply_crop_scale_and_font`` so
        pixmap scaling and font fit run after the widget has a real width.
        """
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
        # Validate JSON line, then crop the page image; on failure show message and skip pixmap.
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
            # ``page_number`` is 1-based in JSON; ``_page_images`` is 0-based.
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

        # Multiline JSON lines use a fixed-height plain editor with a heuristic font only.
        multiline = '\n' in text
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

        self._btn_prev.setEnabled(self._ridx > 0)
        self._btn_next.setEnabled(self._ridx < n_review - 1)

        QTimer.singleShot(0, self._apply_crop_scale_and_font)

    def _apply_crop_scale_and_font(self) -> None:
        """Scale the crop to fit the window, match editor width to the **scaled** image, refit font.

        Called after resize and after showing a new line so ``QLabel`` and ``QLineEdit`` share
        one column width (visual alignment between bitmap and transcription).
        """
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

        # Same pixel width for crop QLabel and editors so columns align (no independent stretch).
        self._line_edit.setFixedWidth(scaled.width())
        self._plain.setFixedWidth(scaled.width())
        self._editor_stack.setFixedWidth(scaled.width())
        if self._editor_stack.currentIndex() == 0:
            self._fit_editor_font_only()

    def _fit_editor_font_only(self) -> None:
        """Recompute single-line font size from the line edit’s current width minus padding fudge."""
        if self._editor_stack.currentIndex() != 0:
            return
        # ~8px margin per side for stylesheet padding + frame; keeps text from touching the border.
        inner = max(80, self._line_edit.width() - 16)
        fit_line_edit_font(self._line_edit, inner)

    def _on_prev(self) -> None:
        """Persist current line, then move to the previous reviewable index."""
        if self._ridx <= 0:
            return
        self._commit_current()
        self._ridx -= 1
        self._show_line()

    def _on_next(self) -> None:
        """Persist current line, then move to the next reviewable index."""
        if self._ridx >= len(self._review_indices) - 1:
            return
        self._commit_current()
        self._ridx += 1
        self._show_line()

    def _on_save(self) -> None:
        """Flush editor text to payload and write ``*_final.json``."""
        self._commit_current()
        save_payload(self._final_path, self._payload)
        self.statusBar().showMessage(f'Wrote {self._final_path}', 6000)

    def _on_reload(self) -> None:
        """Optional: re-read raw JSON from disk and reset review state (after confirmation)."""
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
        # Rebuild from raw file on disk; discards in-memory edits and any ``*_final`` content not saved.
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
    # Exit codes: 2 = bad/missing CLI, 1 = path/validation/PDF/errors, 0 = normal Qt exit.
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
    app.setApplicationName('Line review')
    _ic = _review_app_icon()
    if not _ic.isNull():
        app.setWindowIcon(_ic)
    try:
        # Pass rasters in so we do not call pdf2image twice (here + window ctor).
        win = ReviewMainWindow(resolved, page_images=page_images)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    win.show()
    _install_terminal_interrupt_handlers(app)
    return app.exec()


def _install_terminal_interrupt_handlers(app: QApplication) -> None:
    """Let Ctrl-C in the shell quit the app.

    ``app.exec()`` runs Qt's native event loop; without this, Python often never handles
    SIGINT, so ``^C`` appears but the process stays running. A periodic no-op timer lets the
    interpreter run signal handlers on Linux/macOS. ``kill -TERM <pid>`` also exits cleanly.
    """
    def _quit(_signum=None, _frame=None) -> None:
        app.quit()

    if hasattr(signal, 'SIGINT'):
        signal.signal(signal.SIGINT, _quit)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _quit)

    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)
    # Keep reference: if the timer is GC'd, the workaround stops working.
    app._sigint_poll_timer = timer


if __name__ == '__main__':
    sys.exit(main())
