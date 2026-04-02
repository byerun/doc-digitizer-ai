import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / 'transcribe-chunk-pdf.py'
# Same file the transcribe script uses when the working dir has no *prompt*.md.
REPO_PROMPT_PATH = PROJECT_ROOT / 'prompt.md'
TEST_1_PROMPT_PATH = PROJECT_ROOT / 'tests' / 'test-1' / 'prompt.md'


def skip_if_missing_api_key():
    if not os.environ.get('GEMINI_API_KEY'):
        pytest.skip('GEMINI_API_KEY is not set; skipping live integration test.')


def run_live_transcription(
    working_dir: Path,
    chunk_pdf_filename: str,
    prompt_md: Path,
) -> subprocess.CompletedProcess:
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        '--working-dir',
        str(working_dir),
        '--chunk-pdf',
        chunk_pdf_filename,
        '--prompt-md',
        str(prompt_md),
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )


def assert_common_ai_log_fields(ai_log_text: str, chunk_pdf_filename: str):
    assert f'Chunk PDF file: `{chunk_pdf_filename}`' in ai_log_text
    assert '- Model: `' not in ai_log_text
    assert '- Configuration: `' not in ai_log_text
    assert '## Transcribe config used' in ai_log_text
    assert '"model": "gemini/gemini-2.5-flash"' in ai_log_text
    assert '"temperature": 0.0' in ai_log_text
    assert '"reasoning_effort": "medium"' in ai_log_text
    assert '"media_resolution": "high"' in ai_log_text
    assert '"sys_instructions":' in ai_log_text
    assert '- Confidence score: `' in ai_log_text
    assert '- Confidence label: `' in ai_log_text
    assert '- Prompt tokens (input): `' in ai_log_text
    assert '- Completion tokens (output): `' in ai_log_text
    assert '- Total tokens: `' in ai_log_text
    assert '## Prompt used' in ai_log_text
