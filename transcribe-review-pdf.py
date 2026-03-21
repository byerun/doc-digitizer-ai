#!/usr/bin/env python3

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

import jsonschema
from litellm import completion

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
        required=True,
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
    parser.add_argument(
        '--out-json',
        type=Path,
        default=None,
        help='Optional file path to save validated JSON payload.',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    working_dir = args.working_dir.resolve()
    prompt_md = args.prompt_md if args.prompt_md is not None else working_dir / 'prompt.md'
    schema = load_schema()

    if not os.environ.get('GEMINI_API_KEY'):
        print('Error: GEMINI_API_KEY environment variable is not set.', file=sys.stderr)
        return 2

    if not prompt_md.exists():
        print(f'Error: Prompt file not found: {prompt_md}', file=sys.stderr)
        return 2

    try:
        review_pdf_path = resolve_review_pdf(working_dir, args.review_pdf)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2

    prompt_text = prompt_md.read_text(encoding='utf-8')
    encoded_pdf = base64.b64encode(review_pdf_path.read_bytes()).decode('utf-8')
    pdf_data_url = f'data:application/pdf;base64,{encoded_pdf}'

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
        'transcription': raw.get('transcription'),
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
    output_md.write_text(payload['transcription'], encoding='utf-8')

    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + '\n',
            encoding='utf-8',
        )

    print(f'Created transcription: {output_md}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
