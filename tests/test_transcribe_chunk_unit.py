import os
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKING_DIR = PROJECT_ROOT / 'tests' / 'test-1'
SCRIPT_PATH = PROJECT_ROOT / 'transcribe-chunk-pdf.py'
PROMPT_PATH = PROJECT_ROOT / 'prompt.md'


def load_transcribe_module():
    spec = spec_from_file_location('transcribe_chunk_pdf', SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load module from {SCRIPT_PATH}')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, str(SCRIPT_PATH), *args]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def write_config(path: Path, content: str):
    path.write_text(content, encoding='utf-8')


def ensure_review_pdf_exists():
    review_pdf = WORKING_DIR / 'chunk-pdfs' / 'test-a_001-003.pdf'
    if review_pdf.exists():
        return

    # Build a small deterministic chunk PDF from source PDF if missing.
    from pypdf import PdfReader, PdfWriter

    scan_pdf = WORKING_DIR / 'source-pdfs' / 'test-a.pdf'
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
            '--chunk-pdf',
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
            '--chunk-pdf',
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


def test_extract_usage_tokens_reads_litellm_usage():
    module = load_transcribe_module()
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )
    response = SimpleNamespace(usage=usage)
    assert module.extract_usage_tokens(response) == (100, 50, 150)


def test_extract_usage_tokens_returns_none_when_missing():
    module = load_transcribe_module()
    response = SimpleNamespace(usage=None)
    assert module.extract_usage_tokens(response) == (None, None, None)


def test_config_path_resolution_prefers_working_dir(tmp_path: Path):
    module = load_transcribe_module()
    working_dir = tmp_path / 'working'
    script_dir = tmp_path / 'script'
    working_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    module.SCRIPT_DIR = script_dir
    script_config_path = script_dir / module.TRANSCRIBE_CONFIG_FILENAME
    working_config_path = working_dir / module.TRANSCRIBE_CONFIG_FILENAME

    write_config(
        script_config_path,
        '{"model":"gemini/gemini-2.5-flash","temperature":0.0,'
        '"reasoning_effort":"high","media_resolution":"high",'
        '"sys_instructions":"x"}',
    )
    write_config(
        working_config_path,
        '{"model":"gemini/gemini-2.5-flash","temperature":0.3,'
        '"reasoning_effort":"medium","media_resolution":"low",'
        '"sys_instructions":"x"}',
    )
    resolved = module.resolve_transcribe_config_path(working_dir)
    assert resolved == working_config_path


def test_invalid_config_media_resolution_rejected(tmp_path: Path):
    module = load_transcribe_module()
    config_path = tmp_path / 'transcribe.config.json'
    write_config(
        config_path,
        '{"model":"gemini/gemini-2.5-flash","temperature":0.0,'
        '"reasoning_effort":"high","media_resolution":"invalid",'
        '"sys_instructions":"x"}',
    )

    with pytest.raises(ValueError, match='Invalid config file'):
        module.load_transcribe_config(config_path)


def test_resolve_prompt_md_falls_back_to_script_prompt(tmp_path: Path):
    module = load_transcribe_module()
    working_dir = tmp_path / 'working'
    script_dir = tmp_path / 'script'
    working_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    fallback_prompt = script_dir / 'prompt.md'
    fallback_prompt.write_text('fallback prompt', encoding='utf-8')
    module.SCRIPT_DIR = script_dir

    resolved = module.resolve_prompt_md(working_dir)

    assert resolved == fallback_prompt


def test_resolve_prompt_md_raises_when_no_working_or_fallback_prompt(tmp_path: Path):
    module = load_transcribe_module()
    working_dir = tmp_path / 'working'
    script_dir = tmp_path / 'script'
    working_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)
    module.SCRIPT_DIR = script_dir

    with pytest.raises(ValueError, match='fallback prompt not found'):
        module.resolve_prompt_md(working_dir)


def test_main_prints_full_prompt_path_before_inference(tmp_path: Path, monkeypatch, capsys):
    module = load_transcribe_module()
    working_dir = tmp_path / 'working'
    chunk_dir = working_dir / 'chunk-pdfs'
    chunk_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = working_dir / 'prompt.md'
    prompt_path.write_text('prompt body', encoding='utf-8')
    chunk_pdf_path = chunk_dir / 'sample.pdf'
    chunk_pdf_path.write_bytes(b'%PDF-1.4\n% fake pdf bytes')
    config_path = working_dir / module.TRANSCRIBE_CONFIG_FILENAME
    write_config(
        config_path,
        '{"model":"gemini/gemini-2.5-flash","temperature":0.0,'
        '"reasoning_effort":"medium","media_resolution":"high",'
        '"sys_instructions":"x"}',
    )

    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(module, 'get_pdf_page_count', lambda _: 1)
    monkeypatch.setattr(
        module,
        'completion',
        lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"confidence_score":1.0,"confidence_label":"high",'
                        '"notes":"ok","transcription":"hello"}'
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            ),
        ),
    )
    monkeypatch.setattr(
        sys,
        'argv',
        [
            str(SCRIPT_PATH),
            '--working-dir',
            str(working_dir),
            '--chunk-pdf',
            'sample.pdf',
            '--prompt-md',
            str(prompt_path),
        ],
    )

    exit_code = module.main()
    stdout = capsys.readouterr().out

    assert exit_code == 0
    assert f'Using prompt file: {prompt_path.resolve()}' in stdout
    ai_log = (working_dir / 'transcriptions' / 'sample-ai-log.md').read_text(
        encoding='utf-8'
    )
    assert '- Prompt tokens (input): `10`' in ai_log
    assert '- Completion tokens (output): `20`' in ai_log
    assert '- Total tokens: `30`' in ai_log
