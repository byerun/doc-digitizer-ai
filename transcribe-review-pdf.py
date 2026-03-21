#!/usr/bin/env python3

import argparse
import base64
import json
import os
import re
import shutil
import sys
import termios
import tty
from pathlib import Path

import jsonschema
from litellm import completion

from review_pdf_generator import ReviewPdfGenerator

DEFAULT_MODEL = 'gemini/gemini-2.5-flash'
SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCRIPT_DIR / 'transcription.schema.json'


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


def resolve_review_pdf(working_dir: Path, review_pdf_filename: str) -> Path:
    review_dir = working_dir / 'review-pdfs'
    if not review_dir.exists():
        raise ValueError(
            f'Missing directory: {review_dir}. '
            'Create review-pdfs and place a review PDF in it.'
        )

    filename = review_pdf_filename.strip()
    if not filename:
        raise ValueError('Review PDF filename is required.')
    if Path(filename).name != filename:
        raise ValueError('Provide only the review PDF filename, not a path.')
    if not filename.lower().endswith('.pdf'):
        raise ValueError("Review PDF filename must end with '.pdf'.")

    review_pdf_path = review_dir / filename
    if not review_pdf_path.exists():
        raise ValueError(f'Review PDF not found: {review_pdf_path}')

    return review_pdf_path


def prompt_with_default(label: str, default: str) -> str:
    prompt = f'{label} [{default}]: ' if default else f'{label}: '
    value = input(prompt).strip()
    return value if value else default


def list_review_pdf_filenames(review_dir: Path) -> list[str]:
    if not review_dir.exists() or not review_dir.is_dir():
        return []
    return sorted(
        file_path.name
        for file_path in review_dir.iterdir()
        if file_path.is_file() and file_path.suffix.lower() == '.pdf'
    )


def truncate_for_terminal(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ''
    if len(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    return f'{text[:max_width - 3]}...'


def prompt_select_filename(label: str, default: str, options: list[str]) -> str:
    if not options:
        return prompt_with_default(label, default)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        while True:
            selected = prompt_with_default(label, default)
            if selected in options:
                return selected
            print(f"Please choose one of: {', '.join(options)}")

    selected_index = options.index(default) if default in options else 0

    def render():
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        content_width = max(10, columns - 2)
        sys.stdout.write('\x1b[2J\x1b[H')
        sys.stdout.write(f'Select {label} with up/down arrows and press Enter:\n\n')
        for index, option in enumerate(options):
            prefix = '> ' if index == selected_index else '  '
            display_name = truncate_for_terminal(option, content_width)
            sys.stdout.write(f'{prefix}{display_name}\n')
        sys.stdout.write('\nPress Ctrl+C to cancel.\n')
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            render()
            key = sys.stdin.read(1)
            if key in ('\r', '\n'):
                sys.stdout.write('\x1b[2J\x1b[H')
                selected = options[selected_index]
                sys.stdout.write(f'{label}: {selected}\n')
                sys.stdout.flush()
                return selected
            if key == '\x03':
                raise KeyboardInterrupt
            if key == '\x1b':
                next_one = sys.stdin.read(1)
                next_two = sys.stdin.read(1)
                if next_one == '[':
                    if next_two == 'A':
                        selected_index = (selected_index - 1) % len(options)
                    elif next_two == 'B':
                        selected_index = (selected_index + 1) % len(options)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def resolve_review_pdf_filename(working_dir: Path) -> str:
    review_dir = working_dir / 'review-pdfs'
    review_filenames = list_review_pdf_filenames(review_dir)

    state = {}
    try:
        state = ReviewPdfGenerator(working_dir=working_dir).load_state()
    except ValueError:
        state = {}

    last_generated = state.get('last_generated_output')
    default_filename = ''
    if isinstance(last_generated, str) and last_generated.strip():
        default_filename = Path(last_generated).name
    if default_filename not in review_filenames:
        default_filename = review_filenames[0] if review_filenames else default_filename

    if not review_filenames:
        print(
            f'No PDF files found in {review_dir}. '
            'Falling back to manual filename entry.'
        )

    return prompt_select_filename(
        label='Review PDF filename',
        default=default_filename,
        options=review_filenames,
    )


def resolve_prompt_md(working_dir: Path) -> Path:
    prompt_candidates = sorted(
        path for path in working_dir.glob('*prompt*.md') if path.is_file()
    )
    if not prompt_candidates:
        raise ValueError(
            f'No prompt markdown files found in {working_dir} matching *prompt*.md'
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


def build_messages(prompt_text: str, base64_url: str) -> list[dict]:
    instruction = (
        'Transcribe this review PDF to markdown and respond with JSON only. '
        'Use this key order: confidence_score, confidence_label, notes, transcription. '
        "confidence_score must be a number from 0.0 to 1.0. "
        "confidence_label must be one of: 'low', 'medium', 'high'. "
        'Always include notes explaining the confidence score, including uncertainty '
        'sources when confidence is not high. Preserve structure and formatting.'
    )

    return [
        {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': instruction},
                {'type': 'text', 'text': prompt_text},
                {'type': 'file', 'file': {'file_data': base64_url, 'detail': 'high'}},
            ],
        }
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Transcribe one review PDF via Gemini/LiteLLM.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--review-pdf',
        required=False,
        default=None,
        help='Filename from review-pdfs/ (filename only).',
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
        help='Optional working directory containing review-pdfs/ and transcriptions/.',
    )
    parser.add_argument(
        '--model',
        default=os.environ.get('IDP_MODEL', DEFAULT_MODEL),
        help='Optional LiteLLM model string.',
    )
    parser.add_argument(
        '--temperature',
        type=float,
        default=0.0,
        help='Optional sampling temperature.',
    )
    return parser.parse_args()


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


def build_ai_log_markdown(
    review_pdf_filename: str,
    model: str,
    configuration: str,
    confidence_score: object,
    confidence_label: object,
    notes: object,
    prompt_text: str,
) -> str:
    confidence_score_text = '' if confidence_score is None else str(confidence_score)
    confidence_label_text = '' if confidence_label is None else str(confidence_label)
    notes_text = '' if notes is None else str(notes)

    return (
        '# AI transcription run log\n\n'
        f'- Review PDF file: `{review_pdf_filename}`\n'
        f'- Model: `{model}`\n'
        f'- Configuration: `{configuration}`\n'
        f'- Confidence score: `{confidence_score_text}`\n'
        f'- Confidence label: `{confidence_label_text}`\n'
        f'- Notes: {notes_text}\n\n'
        '## Prompt used\n\n'
        '````markdown\n'
        f'{prompt_text}\n'
        '````\n'
    )


def main() -> int:
    args = parse_args()
    working_dir = args.working_dir.resolve()
    schema = load_schema()

    if not os.environ.get('GEMINI_API_KEY'):
        print('Error: GEMINI_API_KEY environment variable is not set.', file=sys.stderr)
        return 2

    if args.prompt_md is not None:
        prompt_md = args.prompt_md.resolve()
    else:
        try:
            prompt_md = resolve_prompt_md(working_dir)
        except ValueError as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 2

    if not prompt_md.exists():
        print(f'Error: Prompt file not found: {prompt_md}', file=sys.stderr)
        return 2

    review_pdf_filename = args.review_pdf
    if review_pdf_filename is None:
        review_pdf_filename = resolve_review_pdf_filename(working_dir)

    try:
        review_pdf_path = resolve_review_pdf(working_dir, review_pdf_filename)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2

    prompt_text = prompt_md.read_text(encoding='utf-8')
    encoded_pdf = base64.b64encode(review_pdf_path.read_bytes()).decode('utf-8')
    pdf_data_url = f'data:application/pdf;base64,{encoded_pdf}'
    print(
        f'Transcribing {review_pdf_path.name} with {args.model}; this can take a while...',
        flush=True,
    )

    try:
        response = completion(
            model=args.model,
            messages=build_messages(prompt_text, pdf_data_url),
            temperature=args.temperature,
            response_format=build_response_format(schema),
        )
    except Exception as exc:
        print(f'LiteLLM request failed: {exc}', file=sys.stderr)
        return 1

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
        'model': args.model,
        'configuration': f'temperature={args.temperature}, detail=high',
    }

    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        print(f'Schema validation failed: {exc}', file=sys.stderr)
        return 1

    transcriptions_dir = working_dir / 'transcriptions'
    transcriptions_dir.mkdir(parents=True, exist_ok=True)
    output_md = transcriptions_dir / f'{review_pdf_path.stem}.md'
    output_ai_log_md = transcriptions_dir / f'{review_pdf_path.stem}-ai-log.md'
    output_md.write_text(payload['transcription'], encoding='utf-8')
    output_ai_log_md.write_text(
        build_ai_log_markdown(
            review_pdf_filename=review_pdf_path.name,
            model=args.model,
            configuration=payload['configuration'],
            confidence_score=payload['confidence_score'],
            confidence_label=payload['confidence_label'],
            notes=payload['notes'],
            prompt_text=prompt_text,
        ),
        encoding='utf-8',
    )

    print(f'Created transcription: {output_md}')
    print(f'Created AI log: {output_ai_log_md}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
