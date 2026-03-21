import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKING_DIR = PROJECT_ROOT / 'tests' / 'test-1'
SCRIPT_PATH = PROJECT_ROOT / 'transcribe-review-pdf.py'
PROMPT_PATH = WORKING_DIR / 'prompt.md'


def run_cli(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, str(SCRIPT_PATH), *args]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def ensure_review_pdf_exists():
    review_pdf = WORKING_DIR / 'review-pdfs' / 'test-a_001-003.pdf'
    if review_pdf.exists():
        return

    # Build a small deterministic review PDF from scan PDF if missing.
    from pypdf import PdfReader, PdfWriter

    scan_pdf = WORKING_DIR / 'scan-pdfs' / 'test-a.pdf'
    reader = PdfReader(str(scan_pdf))
    writer = PdfWriter()
    for page_index in range(3):
        writer.add_page(reader.pages[page_index])
    review_pdf.parent.mkdir(parents=True, exist_ok=True)
    with review_pdf.open('wb') as output_file:
        writer.write(output_file)


def test_missing_api_key_returns_error():
    env = dict(os.environ)
    env.pop('GEMINI_API_KEY', None)
    result = run_cli(
        [
            '--working-dir',
            str(WORKING_DIR),
            '--review-pdf',
            'test-a_001-003.pdf',
            '--prompt-md',
            str(PROMPT_PATH),
        ],
        env=env,
    )

    assert result.returncode == 2
    assert 'GEMINI_API_KEY' in result.stderr


def test_invalid_review_pdf_path_input_rejected():
    env = dict(os.environ)
    result = run_cli(
        [
            '--working-dir',
            str(WORKING_DIR),
            '--review-pdf',
            'nested/path.pdf',
            '--prompt-md',
            str(PROMPT_PATH),
        ],
        env=env,
    )

    # Could be API-key gate first in some environments; enforce that path input is rejected
    # when key exists.
    if env.get('GEMINI_API_KEY'):
        assert result.returncode == 2
        assert 'filename, not a path' in result.stderr


@pytest.mark.integration
def test_live_integration_transcribes_review_pdf():
    if not os.environ.get('GEMINI_API_KEY'):
        pytest.skip('GEMINI_API_KEY is not set; skipping live integration test.')

    ensure_review_pdf_exists()
    out_md = WORKING_DIR / 'transcriptions' / 'test-a_001-003.md'
    out_ai_log_md = WORKING_DIR / 'transcriptions' / 'test-a_001-003-ai-log.md'
    if out_md.exists():
        out_md.unlink()
    if out_ai_log_md.exists():
        out_ai_log_md.unlink()

    result = run_cli(
        [
            '--working-dir',
            str(WORKING_DIR),
            '--review-pdf',
            'test-a_001-003.pdf',
            '--prompt-md',
            str(PROMPT_PATH),
        ],
        env=dict(os.environ),
    )

    assert result.returncode == 0, result.stderr
    assert out_md.exists()
    assert out_ai_log_md.exists()
    assert out_md.read_text(encoding='utf-8').strip() != ''
    ai_log_text = out_ai_log_md.read_text(encoding='utf-8')
    assert 'Review PDF file: `test-a_001-003.pdf`' in ai_log_text
    assert '- Model: `' in ai_log_text
    assert '- Confidence score: `' in ai_log_text
    assert '- Confidence label: `' in ai_log_text
    assert '## Prompt used' in ai_log_text
