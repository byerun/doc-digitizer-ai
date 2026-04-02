from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from transcribe_integration_helpers import (
    assert_common_ai_log_fields,
    run_live_transcription,
    skip_if_missing_api_key,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKING_DIR = PROJECT_ROOT / 'tests' / 'test-1'
TEST_1_CHUNK_PDF_FILENAME = 'test-a_001-003.pdf'
TEST_1_OUTPUT_PATH = WORKING_DIR / 'transcriptions' / 'test-a_001-003.adoc'
TEST_1_AI_LOG_PATH = WORKING_DIR / 'transcriptions' / 'test-a_001-003-ai-log.md'


def ensure_review_pdf_exists():
    review_pdf = WORKING_DIR / 'chunk-pdfs' / TEST_1_CHUNK_PDF_FILENAME
    if review_pdf.exists():
        return

    scan_pdf = WORKING_DIR / 'source-pdfs' / 'test-a.pdf'
    reader = PdfReader(str(scan_pdf))
    writer = PdfWriter()
    for page_index in range(3):
        writer.add_page(reader.pages[page_index])
    review_pdf.parent.mkdir(parents=True, exist_ok=True)
    with review_pdf.open('wb') as output_file:
        writer.write(output_file)


@pytest.mark.integration
def test_live_integration_test_1_transcribes_and_logs():
    skip_if_missing_api_key()
    ensure_review_pdf_exists()

    if TEST_1_OUTPUT_PATH.exists():
        TEST_1_OUTPUT_PATH.unlink()
    if TEST_1_AI_LOG_PATH.exists():
        TEST_1_AI_LOG_PATH.unlink()

    result = run_live_transcription(WORKING_DIR, TEST_1_CHUNK_PDF_FILENAME)

    assert result.returncode == 0, result.stderr
    assert TEST_1_OUTPUT_PATH.exists()
    assert TEST_1_AI_LOG_PATH.exists()
    assert TEST_1_OUTPUT_PATH.read_text(encoding='utf-8').strip() != ''
    ai_log_text = TEST_1_AI_LOG_PATH.read_text(encoding='utf-8')
    assert_common_ai_log_fields(ai_log_text, TEST_1_CHUNK_PDF_FILENAME)


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
