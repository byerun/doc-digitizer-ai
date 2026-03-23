# Review PDF Generator

Interactive tooling to split source PDFs into smaller review PDFs for transcription and human QA workflows.

## Install

```bash
python -m pip install -r requirements.txt
```

## Directory layout

Run the script from a transcription working directory that contains:

- `scan-pdfs/`: source PDFs to split
- `review-pdfs/`: generated review PDFs
- `.review-chunk-state.json`: created automatically to store defaults

Example fixture working directory:

- `tests/test-1/scan-pdfs/test-a.pdf`
- `tests/test-1/scan-pdfs/test-b.pdf`
- `tests/test-1/review-pdfs/`

## Generate a review PDF

```bash
python generate-review-pdf.py --working-dir tests/test-1
```

Prompts:

- Scan PDF filename (filename only, from `scan-pdfs/`)
- Start PDF page
- End PDF page
- Output review PDF filename (editable default)

Default output naming:

- `<scan_chunk_stem>_<start:03d>-<end:03d>.pdf`
- Example: `test-a_001-005.pdf`

## Fixture PDF regeneration

If you edit fixture AsciiDoc files, regenerate PDFs with:

```bash
asciidoctor-pdf tests/test-1/test-a.adoc -o tests/test-1/scan-pdfs/test-a.pdf
asciidoctor-pdf tests/test-1/test-b.adoc -o tests/test-1/scan-pdfs/test-b.pdf
```

## Run tests

```bash
pytest -q
```

## Transcribe a review PDF

Use Gemini through LiteLLM to transcribe a file from `review-pdfs/` into
`transcriptions/<review_pdf_stem>.md`, and write a reproducibility log to
`transcriptions/<review_pdf_stem>-ai-log.md`.

```bash
export GEMINI_API_KEY=...
python transcribe-review-pdf.py \
  --working-dir tests/test-1
```

Notes:
- `transcriptions/` is created automatically if it does not exist.
- Model settings are read from `transcribe.config.json` with this precedence:
  - `<working-dir>/transcribe.config.json`
  - `<script-dir>/transcribe.config.json` (fallback)
- `--config` is not required.
- `--review-pdf` is optional. If omitted, the script prompts you to choose from `review-pdfs/` with up/down arrows. The default selection comes from `.review-chunk-state.json` (`last_generated_output`) when available.
- `--review-pdf` must be a filename only (no path) when provided.
- `--prompt-md` is optional. If omitted, the script looks for files matching `*prompt*.md` in the working directory:
  - if exactly one file matches, it is used automatically
  - if multiple files match, you can choose interactively with up/down arrows
  - if none match, the script exits with an error
- `<review_pdf_stem>-ai-log.md` includes: review PDF filename, model, configuration, confidence score, confidence label, notes, and full prompt used.

Example `-ai-log.md`:

```markdown
# AI transcription run log

- Review PDF file: `test-a_001-003.pdf`
- Model: `gemini/gemini-2.5-flash`
- Configuration: `temperature=0.0, media_resolution=high, reasoning_effort=high`
- Confidence score: `0.93`
- Confidence label: `high`
- Notes: Clear text with minor uncertainty around one table heading.

## Prompt used

````markdown
<!-- full prompt text captured verbatim -->
````
```

Live integration test:

```bash
pytest -q -k transcribe_review_pdf
```

Example `transcribe.config.json`:

```json
{
  "model": "gemini/gemini-2.5-flash",
  "temperature": 0.0,
  "reasoning_effort": "high",
  "media_resolution": "high"
}
```
