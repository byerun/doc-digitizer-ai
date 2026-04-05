"""
Chunk PDF line transcriptions: paths, JSON payload, page rasters, and box geometry.

No Qt — safe to import from CLI tools or other UIs besides the line reviewer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image

# Prompt-injected markers (not printed on the page).
# Must stay aligned with Pass 1 prompt / any transcribe normalization of line text.
_PAGE_MARKER_PATTERN = re.compile(r'^\s*//\s*Page\s+\d+\s*$', re.IGNORECASE)


def is_injected_page_marker(text: object) -> bool:
    if not isinstance(text, str):
        return False
    t = text.strip()
    if t.startswith('{empty}'):
        t = t[len('{empty}') :].strip()
    return bool(_PAGE_MARKER_PATTERN.match(t))


def editable_line_indices(lines: list) -> list[int]:
    """Indices into ``payload['lines']`` for rows that are not synthetic ``// Page N`` markers."""
    return [i for i, ln in enumerate(lines) if not is_injected_page_marker(ln.get('text', ''))]


def list_chunk_pdf_filenames(chunk_dir: Path) -> list[str]:
    if not chunk_dir.exists() or not chunk_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in chunk_dir.iterdir()
        if p.is_file() and p.suffix.lower() == '.pdf'
    )


@dataclass(frozen=True)
class TranscriptionPaths:
    """Resolved absolute paths for the chunk PDF and JSON; ``stem`` is chunk filename without .pdf."""

    working_dir: Path
    chunk_pdf_path: Path
    raw_path: Path
    final_path: Path
    chunk_name: str
    stem: str


def resolve_transcription_paths_for_chunk(
    working_dir: Path,
    chunk_name: str,
    raw_json: Path | None,
) -> TranscriptionPaths | str:
    working_dir = working_dir.resolve()
    chunk_pdfs_dir = working_dir / 'chunk-pdfs'
    transcriptions_dir = working_dir / 'transcriptions'
    if not chunk_pdfs_dir.is_dir():
        return (
            f'Expected a chunk-pdfs directory at {chunk_pdfs_dir}. '
            '--working-dir should be the project/work folder that contains '
            'chunk-pdfs/ (and usually transcriptions/), same as '
            'transcribe-chunk-pdf.py.'
        )

    chunk_name = chunk_name.strip()
    if Path(chunk_name).name != chunk_name:
        return 'Use chunk PDF filename only, not a path.'
    if not chunk_name.lower().endswith('.pdf'):
        return "Chunk PDF filename must end with '.pdf'."

    chunk_pdf_path = chunk_pdfs_dir / chunk_name
    if not chunk_pdf_path.is_file():
        return f'Chunk PDF not found: {chunk_pdf_path}'

    stem = Path(chunk_name).stem
    if raw_json is not None:
        raw_candidate = raw_json
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

    return TranscriptionPaths(
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


def crop_for_line(
    page_images: list[Image.Image],
    line: dict,
) -> tuple[Image.Image | None, str | None]:
    """Build a PIL crop for one payload line, or return (None, error_message)."""
    page_number = line.get('page_number')
    box_2d = line.get('box_2d')
    n_pages = len(page_images)

    if not isinstance(page_number, int) or page_number < 1:
        return None, f'Invalid page_number: {page_number!r}'
    if page_number > n_pages:
        return None, (
            f'page_number {page_number} is out of range '
            f'(chunk has {n_pages} page(s)).'
        )
    if not isinstance(box_2d, list) or len(box_2d) != 4:
        return None, f'Invalid box_2d: {box_2d!r}'

    page_img = page_images[page_number - 1]
    w, h = page_img.size
    left, upper, right, lower = clamp_box_2d_to_pixels(box_2d, w, h)
    return page_img.crop((left, upper, right, lower)), None


class ChunkLinesSession:
    """Mutable state for one loaded chunk: transcription JSON, page rasters, editable-line cursor."""

    def __init__(self) -> None:
        self.paths: TranscriptionPaths | None = None
        self.page_images: list[Image.Image] = []
        self.payload: dict = {}
        self.lines: list = []
        self.editable_indices: list[int] = []
        self.editable_ridx: int = 0
        self.dirty: bool = False
        self.source_raw_path: str = ''

    @property
    def is_loaded(self) -> bool:
        return self.paths is not None

    def load_chunk(
        self,
        working_dir: Path,
        chunk_name: str,
        raw_json_cli: Path | None,
    ) -> str | None:
        """Load PDF + JSON into this session. Returns an error message, or ``None`` on success."""
        resolved = resolve_transcription_paths_for_chunk(
            working_dir,
            chunk_name,
            raw_json_cli,
        )
        if isinstance(resolved, str):
            return resolved
        try:
            page_images = load_page_images(resolved.chunk_pdf_path)
            payload = load_payload(resolved.raw_path, resolved.final_path)
        except Exception as exc:
            return f'Could not read PDF or JSON. {exc}'

        lines = payload.get('lines')
        if not isinstance(lines, list) or not lines:
            return 'Invalid payload: missing or empty "lines"'

        indices = editable_line_indices(lines)
        if not indices:
            return (
                'No editable lines: every entry looks like a synthetic '
                '`// Page N` marker.'
            )

        self.paths = resolved
        self.page_images = page_images
        self.payload = payload
        self.lines = lines
        self.editable_indices = indices
        self.editable_ridx = 0
        self.dirty = False
        self.source_raw_path = str(resolved.raw_path)
        return None

    def clamp_editable_ridx(self) -> None:
        n = len(self.editable_indices)
        if n == 0:
            self.editable_ridx = 0
        else:
            self.editable_ridx = max(0, min(self.editable_ridx, n - 1))

    def line_at_editable_ridx(self) -> dict:
        self.clamp_editable_ridx()
        idx = self.editable_indices[self.editable_ridx]
        return self.lines[idx]

    def crop_for_current_editable(self) -> tuple[Image.Image | None, str | None]:
        self.clamp_editable_ridx()
        line = self.line_at_editable_ridx()
        return crop_for_line(self.page_images, line)

    def commit_editable_text(self, text: str) -> None:
        """Write ``text`` into ``payload['lines']`` for the current editable index."""
        self.clamp_editable_ridx()
        idx = self.editable_indices[self.editable_ridx]
        self.lines[idx]['text'] = rstrip_line_text(text)

    def save_to_final(self) -> None:
        if self.paths is None:
            return
        save_payload(self.paths.final_path, self.payload)

    def reload_from_raw_disk(self) -> str | None:
        """Reload ``payload`` from the raw JSON path on disk. Returns error or ``None``."""
        raw = Path(self.source_raw_path)
        self.payload = json.loads(raw.read_text(encoding='utf-8'))
        self.lines = self.payload['lines']
        self.editable_indices = editable_line_indices(self.lines)
        if not self.editable_indices:
            return 'No editable lines after reload.'
        self.editable_ridx = 0
        self.dirty = False
        return None
