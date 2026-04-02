from pathlib import Path

import pytest

from transcribe_integration_helpers import run_live_transcription, skip_if_missing_api_key


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKING_DIR_TEST_2 = PROJECT_ROOT / 'tests' / 'test-2'
TEST_2_CHUNK_PDF_FILENAME = 'test-2.pdf'
TEST_2_EXPECTED_PATH = WORKING_DIR_TEST_2 / 'test-2-expected.adoc'
TEST_2_OUTPUT_PATH = WORKING_DIR_TEST_2 / 'transcriptions' / 'test-2.adoc'


@pytest.mark.integration
def test_live_integration_test_2_matches_expected_adoc():
    skip_if_missing_api_key()

    if TEST_2_OUTPUT_PATH.exists():
        TEST_2_OUTPUT_PATH.unlink()

    result = run_live_transcription(WORKING_DIR_TEST_2, TEST_2_CHUNK_PDF_FILENAME)

    assert result.returncode == 0, result.stderr
    assert TEST_2_OUTPUT_PATH.exists()
    assert TEST_2_OUTPUT_PATH.read_text(encoding='utf-8') == TEST_2_EXPECTED_PATH.read_text(
        encoding='utf-8'
    )


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
