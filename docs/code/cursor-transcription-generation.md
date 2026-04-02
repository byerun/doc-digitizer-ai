---
name: litellm-chunk-transcriber
overview: Add a single integration-friendly CLI script that transcribes one chunk PDF via Gemini/LiteLLM using a provided prompt markdown file, validates JSON response against schema, and writes markdown output plus an AI run log to transcriptions/.
todos:
  - id: add-transcribe-cli
    content: Implement transcribe-chunk-pdf.py with LiteLLM Gemini call, schema validation, and markdown output write.
    status: pending
  - id: add-transcribe-tests
    content: Add true integration tests that call Gemini via LiteLLM and validate end-to-end output.
    status: pending
  - id: update-deps-docs
    content: Update requirements and README for new transcription script usage and environment setup.
    status: pending
  - id: verify-transcribe-flow
    content: Run live integration tests and CLI checks with real API calls when GEMINI_API_KEY is present.
    status: pending
isProject: false
---

# Implement LiteLLM Chunk PDF Transcriber

## Scope

Build one new CLI script to transcribe a selected file from `chunk-pdfs/` using Gemini via LiteLLM, with a prompt read from a markdown file passed to the script.

Expected behavior:

- Input chunk PDF by filename (not full path), resolved from `chunk-pdfs/` in the working directory.
- Input prompt file via CLI argument (markdown text).
- Send prompt + PDF to LiteLLM (`gemini/gemini-2.5-flash` default).
- Expect strict JSON response, validate using schema.
- Write only the `transcription` field to `transcriptions/<chunk_pdf_stem>.md`.
- Preserve markdown formatting from the model output.

## Files to add/update

- New CLI script: `[doc-digitizer-ai/transcribe-chunk-pdf.py](doc-digitizer-ai/transcribe-chunk-pdf.py)`
- Schema (reuse/adapt): `[doc-digitizer-ai/transcription.schema.json](doc-digitizer-ai/transcription.schema.json)`
- Tests (true integration): `[doc-digitizer-ai/tests/test_transcribe_chunk_pdf.py](doc-digitizer-ai/tests/test_transcribe_chunk_pdf.py)`
- Prompt: `[doc-digitizer-ai/prompt.md](doc-digitizer-ai/prompt.md)`
- Dependency updates: `[doc-digitizer-ai/requirements.txt](doc-digitizer-ai/requirements.txt)`
- Usage docs: `[doc-digitizer-ai/README.md](doc-digitizer-ai/README.md)`

## CLI design

Primary arguments:

- `--chunk-pdf <filename.pdf>` (required; filename only)
- `--prompt-md <path/to/prompt.md>` (required)
- `--working-dir <path>` (default `.`)
- `--model <provider/model>` (default `gemini/gemini-2.5-flash`)
- `--temperature <float>` (default `0.0`)

Environment:

- Requires `GEMINI_API_KEY`.

## Validation and output rules

- Validate `chunk-pdfs/` exists in working directory.
- Validate provided PDF filename exists under `chunk-pdfs/` and ends with `.pdf`.
- Validate prompt markdown file exists.
- Parse model response JSON (strip code fences if present).
- Build payload with required schema keys (`transcription`, `confidence_score`, `confidence_label`, `model`; optional `notes`, `configuration`) and validate with JSON Schema.
- Ensure output dir `transcriptions/` exists.
- Write markdown transcription to `transcriptions/<stem>.md`.
- Write reproducibility log to `transcriptions/<stem>-ai-log.md` including chunk PDF filename, model, configuration, confidence score, confidence label, notes, and prompt used.

## Testing approach

Create true integration tests that pass CLI args and call Gemini through LiteLLM:

- live success path writes markdown file with expected filename/content
- live success path writes run log markdown with required reproducibility fields
- missing `GEMINI_API_KEY` returns clear error
- invalid `chunk-pdfs` filename/path handling
- if `GEMINI_API_KEY` is not set in CI/local environment, mark live integration tests as skipped with clear reason
- use explicit invocation in docs/verification: `pytest -q tests/test_transcribe_chunk_pdf.py`

## Design decision on split

Use a **single script** for now (as you suggested), because:

- behavior is primarily I/O orchestration (CLI args, env, file paths, API call, output write)
- direct end-to-end integration testing via CLI args is preferred for debugging
- can extract a shared module later if logic grows (e.g., retries, batch mode, prompt templating)

## Terminology

Use clearer terms in CLI/docs:

- `source_pdf`/`review_pdf` instead of `chunk`
- keep compatibility with your current folder names (`chunk-pdfs`, `transcriptions`)

