#!/usr/bin/env python3

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import jsonschema
import questionary
from jsonargparse import ArgumentParser as JsonArgParser
from litellm import completion
from pypdf import PdfReader

from chunk_pdf_generator import ChunkPdfGenerator

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCRIPT_DIR / 'transcription.schema.json'
TRANSCRIBE_CONFIG_FILENAME = 'transcribe.config.json'
VALID_REASONING_EFFORTS = ('none', 'disable', 'low', 'medium', 'high', 'minimal')
VALID_MEDIA_RESOLUTIONS = ('low', 'medium', 'high', 'ultra_high', 'auto')


def load_schema() -> dict:
    with SCHEMA_PATH.open('r', encoding='utf-8') as schema_file:
        return json.load(schema_file)


def build_response_format(schema: dict) -> dict:
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': 'idp_transcription_response',
            'schema': schema,
            'strict': True,
        },
    }


def strip_json_code_fence(content: str) -> str:
    text = content.strip()
    if text.startswith('```'):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return text


def resolve_chunk_pdf(working_dir: Path, chunk_pdf_filename: str) -> Path:
    chunk_dir = working_dir / 'chunk-pdfs'
    if not chunk_dir.exists():
        raise ValueError(
            f'Missing directory: {chunk_dir}. '
            'Create chunk-pdfs and place a chunk PDF in it.'
        )

    filename = chunk_pdf_filename.strip()
    if not filename:
        raise ValueError('Chunk PDF filename is required.')
    if Path(filename).name != filename:
        raise ValueError('Provide only the chunk PDF filename, not a path.')
    if not filename.lower().endswith('.pdf'):
        raise ValueError("Chunk PDF filename must end with '.pdf'.")

    chunk_pdf_path = chunk_dir / filename
    if not chunk_pdf_path.exists():
        raise ValueError(f'Chunk PDF not found: {chunk_pdf_path}')

    return chunk_pdf_path


def prompt_with_default(label: str, default: str) -> str:
    prompt = f'{label} [{default}]: ' if default else f'{label}: '
    value = input(prompt).strip()
    return value if value else default


def list_chunk_pdf_filenames(chunk_dir: Path) -> list[str]:
    if not chunk_dir.exists() or not chunk_dir.is_dir():
        return []
    return sorted(
        file_path.name
        for file_path in chunk_dir.iterdir()
        if file_path.is_file() and file_path.suffix.lower() == '.pdf'
    )


def prompt_select_filename(label: str, default: str, options: list[str]) -> str:
    if not options:
        return prompt_with_default(label, default)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        while True:
            selected = prompt_with_default(label, default)
            if selected in options:
                return selected
            print(f"Please choose one of: {', '.join(options)}")

    default_choice = default if default in options else options[0]
    selected = questionary.select(
        f'{label}:',
        choices=options,
        default=default_choice,
        qmark='>',
    ).ask()
    if selected is None:
        raise KeyboardInterrupt
    return selected


def resolve_chunk_pdf_filename(working_dir: Path) -> str:
    chunk_dir = working_dir / 'chunk-pdfs'
    chunk_filenames = list_chunk_pdf_filenames(chunk_dir)

    state = {}
    try:
        state = ChunkPdfGenerator(working_dir=working_dir).load_state()
    except ValueError:
        state = {}

    last_generated = state.get('last_generated_output')
    default_filename = ''
    if isinstance(last_generated, str) and last_generated.strip():
        default_filename = Path(last_generated).name
    if default_filename not in chunk_filenames:
        default_filename = (
            chunk_filenames[0] if chunk_filenames else default_filename
        )

    if not chunk_filenames:
        print(
            f'No PDF files found in {chunk_dir}. '
            'Falling back to manual filename entry.'
        )

    return prompt_select_filename(
        label='Chunk PDF filename',
        default=default_filename,
        options=chunk_filenames,
    )


def resolve_prompt_md(working_dir: Path) -> Path:
    prompt_candidates = sorted(
        path for path in working_dir.glob('*prompt*.md') if path.is_file()
    )
    if not prompt_candidates:
        default_prompt = SCRIPT_DIR / 'prompt.md'
        if default_prompt.exists():
            return default_prompt
        raise ValueError(
            f'No prompt markdown files found in {working_dir} matching *prompt*.md '
            f'and fallback prompt not found: {default_prompt}'
        )

    if len(prompt_candidates) == 1:
        return prompt_candidates[0]

    prompt_names = [path.name for path in prompt_candidates]
    default_name = 'prompt.md' if 'prompt.md' in prompt_names else prompt_names[0]
    selected_name = prompt_select_filename(
        label='Prompt markdown file',
        default=default_name,
        options=prompt_names,
    )
    return working_dir / selected_name


def resolve_prompt_md_auto(working_dir: Path) -> Path:
    prompt_candidates = sorted(
        path for path in working_dir.glob('*prompt*.md') if path.is_file()
    )
    if not prompt_candidates:
        default_prompt = SCRIPT_DIR / 'prompt.md'
        if default_prompt.exists():
            return default_prompt
        raise ValueError(
            f'No prompt markdown files found in {working_dir} matching *prompt*.md '
            f'and fallback prompt not found: {default_prompt}'
        )

    if len(prompt_candidates) == 1:
        return prompt_candidates[0]

    prompt_names = [path.name for path in prompt_candidates]
    default_name = 'prompt.md' if 'prompt.md' in prompt_names else prompt_names[0]
    return working_dir / default_name


def build_messages(
    sys_instructions: str,
    prompt_text: str,
    base64_url: str,
    media_resolution: str,
) -> list[dict]:
    return [
        {'role': 'system', 'content': sys_instructions},
        {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': prompt_text},
                # LiteLLM/OpenAI-style multimodal content uses the field name `detail`.
                # We expose this as `media_resolution` in config for clarity.
                {
                    'type': 'file',
                    'file': {'file_data': base64_url, 'detail': media_resolution},
                },
            ],
        },
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Transcribe chunk PDF(s) via Gemini/LiteLLM.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--chunk-pdf',
        required=False,
        default=None,
        help='Filename from chunk-pdfs/ (filename only).',
    )
    parser.add_argument(
        '--prompt-md',
        type=Path,
        default=None,
        help='Optional path to prompt markdown file.',
    )
    parser.add_argument(
        '--working-dir',
        type=Path,
        default=Path('.'),
        help='Optional working directory containing chunk-pdfs/ and transcriptions/.',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Transcribe every PDF in chunk-pdfs/ without prompting (non-interactive).',
    )
    args = parser.parse_args()
    if args.all and args.chunk_pdf is not None:
        parser.error('cannot combine --all with --chunk-pdf')
    return args


def resolve_transcribe_config_path(working_dir: Path) -> Path:
    working_dir_config = working_dir / TRANSCRIBE_CONFIG_FILENAME
    if working_dir_config.exists():
        return working_dir_config

    script_dir_config = SCRIPT_DIR / TRANSCRIBE_CONFIG_FILENAME
    if script_dir_config.exists():
        return script_dir_config

    raise ValueError(
        f'Missing transcribe config file: expected {working_dir_config} '
        f'or {script_dir_config}'
    )


def load_transcribe_config(config_path: Path) -> dict:
    parser = JsonArgParser(exit_on_error=False)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--temperature', type=float, required=True)
    parser.add_argument(
        '--reasoning_effort',
        type=str,
        choices=VALID_REASONING_EFFORTS,
        required=True,
    )
    parser.add_argument(
        '--media_resolution',
        type=str,
        choices=VALID_MEDIA_RESOLUTIONS,
        required=True,
    )
    parser.add_argument('--sys_instructions', type=str, required=True)

    try:
        config_data = json.loads(config_path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ValueError(f'Could not read config file {config_path}: {exc}') from exc

    try:
        parsed = parser.parse_object(config_data)
    except Exception as exc:
        raise ValueError(f'Invalid config file {config_path}: {exc}') from exc

    return {
        'model': parsed.model,
        'temperature': parsed.temperature,
        'reasoning_effort': parsed.reasoning_effort,
        'media_resolution': parsed.media_resolution,
        'sys_instructions': parsed.sys_instructions,
    }


def normalize_transcription_newlines(transcription: object) -> str:
    if not isinstance(transcription, str):
        return ''

    normalized = transcription.replace('\r\n', '\n')
    if '\\n' in normalized or '\\r' in normalized:
        normalized = (
            normalized.replace('\\r\\n', '\n')
            .replace('\\n', '\n')
            .replace('\\r', '\n')
        )
    return normalized


def is_notes_min_length_validation_error(exc: jsonschema.ValidationError) -> bool:
    validator_is_min_length = exc.validator == 'minLength'
    validator_value_is_one = exc.validator_value == 1
    field_is_notes = list(exc.absolute_path) == ['notes']
    schema_points_to_notes = list(exc.absolute_schema_path)[-2:] == ['notes', 'minLength']
    return (
        validator_is_min_length
        and validator_value_is_one
        and field_is_notes
        and schema_points_to_notes
    )


def get_pdf_page_count(pdf_path: Path) -> int:
    try:
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception as exc:
        raise ValueError(f'Could not read PDF page count from {pdf_path}: {exc}') from exc


def extract_usage_tokens(response) -> tuple[object, object, object]:
    """Return (prompt_tokens, completion_tokens, total_tokens) from a LiteLLM response."""
    usage = getattr(response, 'usage', None)
    if usage is None:
        return (None, None, None)
    return (
        getattr(usage, 'prompt_tokens', None),
        getattr(usage, 'completion_tokens', None),
        getattr(usage, 'total_tokens', None),
    )


def format_token_log_value(value: object) -> str:
    if value is None:
        return '(not reported)'
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def build_ai_log_markdown(
    chunk_pdf_filename: str,
    run_started_at: str,
    total_pages: int,
    inference_time_seconds: object,
    average_time_per_page_seconds: object,
    transcribe_config_text: str,
    confidence_score: object,
    confidence_label: object,
    notes: object,
    prompt_text: str,
    prompt_tokens: object = None,
    completion_tokens: object = None,
    total_tokens: object = None,
) -> str:
    confidence_score_text = '' if confidence_score is None else str(confidence_score)
    confidence_label_text = '' if confidence_label is None else str(confidence_label)
    notes_text = '' if notes is None else str(notes)
    inference_time_text = (
        ''
        if inference_time_seconds is None
        else f'{float(inference_time_seconds) / 60.0:.2f}'
    )
    average_time_per_page_text = (
        ''
        if average_time_per_page_seconds is None
        else f'{float(average_time_per_page_seconds):.2f}'
    )

    return (
        '# AI transcription run log\n\n'
        f'- Chunk PDF file: `{chunk_pdf_filename}`\n'
        f'- Run started at: `{run_started_at}`\n'
        f'- Total pages: `{total_pages}`\n'
        f'- Total inference time (minutes): `{inference_time_text}`\n'
        f'- Average time per page (seconds): `{average_time_per_page_text}`\n'
        f'- Prompt tokens (input): `{format_token_log_value(prompt_tokens)}`\n'
        f'- Completion tokens (output): `{format_token_log_value(completion_tokens)}`\n'
        f'- Total tokens: `{format_token_log_value(total_tokens)}`\n'
        f'- Confidence score: `{confidence_score_text}`\n'
        f'- Confidence label: `{confidence_label_text}`\n'
        f'- Notes: {notes_text}\n'
        '## Transcribe config used\n\n'
        '```json\n'
        f'{transcribe_config_text}\n'
        '```\n\n'
        '## Prompt used\n\n'
        '````markdown\n'
        f'{prompt_text}\n'
        '````\n'
    )


def transcribe_single_chunk(
    working_dir: Path,
    prompt_md: Path,
    transcribe_config: dict,
    config_path: Path,
    schema: dict,
    chunk_pdf_filename: str,
) -> int:
    run_started_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    try:
        chunk_pdf_path = resolve_chunk_pdf(working_dir, chunk_pdf_filename)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    try:
        total_pages = get_pdf_page_count(chunk_pdf_path)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2

    prompt_text = prompt_md.read_text(encoding='utf-8')
    transcribe_config_text = config_path.read_text(encoding='utf-8').strip()
    encoded_pdf = base64.b64encode(chunk_pdf_path.read_bytes()).decode('utf-8')
    pdf_data_url = f'data:application/pdf;base64,{encoded_pdf}'
    print(f'Using prompt file: {prompt_md}')
    print(
        f'Transcribing {chunk_pdf_path.name} with {transcribe_config["model"]}; '
        'this can take a while...',
        flush=True,
    )

    try:
        inference_start = time.perf_counter()
        response = completion(
            model=transcribe_config['model'],
            messages=build_messages(
                transcribe_config['sys_instructions'],
                prompt_text,
                pdf_data_url,
                transcribe_config['media_resolution'],
            ),
            temperature=transcribe_config['temperature'],
            reasoning_effort=transcribe_config['reasoning_effort'],
            response_format=build_response_format(schema),
        )
        inference_time_seconds = time.perf_counter() - inference_start
    except Exception as exc:
        print(f'LiteLLM request failed: {exc}', file=sys.stderr)
        return 1

    prompt_tokens, completion_tokens, total_tokens = extract_usage_tokens(response)

    average_time_per_page_seconds = (
        inference_time_seconds / total_pages if total_pages > 0 else None
    )

    try:
        content = response.choices[0].message.content
        raw = json.loads(strip_json_code_fence(content))
    except Exception as exc:
        print(f'Error parsing model response JSON: {exc}', file=sys.stderr)
        return 1

    payload = {
        'confidence_score': raw.get('confidence_score'),
        'confidence_label': raw.get('confidence_label'),
        'notes': raw.get('notes'),
        'transcription': normalize_transcription_newlines(raw.get('transcription', '')),
        'model': transcribe_config['model'],
        'configuration': (
            f'temperature={transcribe_config["temperature"]}, '
            f'media_resolution={transcribe_config["media_resolution"]}, '
            f'reasoning_effort={transcribe_config["reasoning_effort"]}'
        ),
    }

    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        if is_notes_min_length_validation_error(exc):
            print(
                'Warning: notes failed schema minLength validation; continuing '
                "because empty notes are allowed when confidence_score is 1.0.",
                file=sys.stderr,
            )
        else:
            print(f'Schema validation failed: {exc}', file=sys.stderr)
            return 1

    transcriptions_dir = working_dir / 'transcriptions'
    transcriptions_dir.mkdir(parents=True, exist_ok=True)
    output_adoc = transcriptions_dir / f'{chunk_pdf_path.stem}.adoc'
    output_ai_log_md = transcriptions_dir / f'{chunk_pdf_path.stem}-ai-log.md'
    output_adoc.write_text(payload['transcription'], encoding='utf-8')
    output_ai_log_md.write_text(
        build_ai_log_markdown(
            chunk_pdf_filename=chunk_pdf_path.name,
            run_started_at=run_started_at,
            total_pages=total_pages,
            inference_time_seconds=inference_time_seconds,
            average_time_per_page_seconds=average_time_per_page_seconds,
            transcribe_config_text=transcribe_config_text,
            confidence_score=payload['confidence_score'],
            confidence_label=payload['confidence_label'],
            notes=payload['notes'],
            prompt_text=prompt_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
        encoding='utf-8',
    )

    print(f'Created transcription: {output_adoc}')
    print(f'Created AI log: {output_ai_log_md}')
    return 0


def main() -> int:
    args = parse_args()
    working_dir = args.working_dir.resolve()
    schema = load_schema()

    try:
        config_path = resolve_transcribe_config_path(working_dir)
        transcribe_config = load_transcribe_config(config_path)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2

    if not os.environ.get('GEMINI_API_KEY'):
        print('Error: GEMINI_API_KEY environment variable is not set.', file=sys.stderr)
        return 2

    if args.prompt_md is not None:
        prompt_md = args.prompt_md.resolve()
    else:
        try:
            if args.all:
                prompt_md = resolve_prompt_md_auto(working_dir)
            else:
                prompt_md = resolve_prompt_md(working_dir)
        except ValueError as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 2

    if not prompt_md.exists():
        print(f'Error: Prompt file not found: {prompt_md}', file=sys.stderr)
        return 2

    if args.all:
        chunk_dir = working_dir / 'chunk-pdfs'
        chunk_filenames = list_chunk_pdf_filenames(chunk_dir)
        if not chunk_filenames:
            print(
                f'Error: No PDF files in {chunk_dir}. Nothing to transcribe.',
                file=sys.stderr,
            )
            return 2
        print(f'--all: transcribing {len(chunk_filenames)} chunk(s).', flush=True)
        for chunk_pdf_filename in chunk_filenames:
            print(f'--- {chunk_pdf_filename} ---', flush=True)
            rc = transcribe_single_chunk(
                working_dir,
                prompt_md,
                transcribe_config,
                config_path,
                schema,
                chunk_pdf_filename,
            )
            if rc != 0:
                return rc
        return 0

    chunk_pdf_filename = args.chunk_pdf
    if chunk_pdf_filename is None:
        chunk_pdf_filename = resolve_chunk_pdf_filename(working_dir)

    return transcribe_single_chunk(
        working_dir,
        prompt_md,
        transcribe_config,
        config_path,
        schema,
        chunk_pdf_filename,
    )


if __name__ == '__main__':
    raise SystemExit(main())
