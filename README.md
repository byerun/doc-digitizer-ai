# Document digitizing using AI

This repository provides script tooling for transcribing handwritten and typewritten PDF content using AI.

## Goals

- Leverage AI as much as possible for transcribing handwritten and typewritten text.
- Make large transcription projects manageable by splitting source PDFs into chunk PDFs.
- Improve quality and efficiency by fixing prompts early based on human transcription corrections before generating chunks for later sections.
- Avoid processing too many pages at once, which can hit model token limits.

## Install

```bash
python -m pip install -r requirements.txt
```

## Working directory layout

Create one dedicated working directory per work/book/manuscript and run the scripts from there.

### Directories

| Path | Purpose |
| --- | --- |
| `source-pdfs/` | Source PDFs to transcribe. |
| `chunk-pdfs/` | Chunk PDFs generated from page ranges in a source PDF. |
| `transcriptions/` | Raw/final JSON transcriptions and AI run logs. Created automatically as needed. |

### Files

| Path | Purpose |
| --- | --- |
| `prompt.md` | Prompt used during transcription (`--prompt-md` can override). |
| `.chunk-state.json` | Created automatically to store defaults such as last selected source and generated output. |
| `transcribe.config.json` | Optional per-work config to override the repository default model/settings. |

### Example layout

```text
my-work/
├── source-pdfs/
│   ├── volume-1.pdf
│   └── volume-2.pdf
├── chunk-pdfs/
├── transcriptions/
├── prompt.md
├── transcribe.config.json
└── .chunk-state.json
```

Run commands from the repository root and point to your working directory with `--working-dir`, or `cd` into your working directory and pass `--working-dir .`.

## Generate a chunk PDF

`generate-chunk-pdf.py` extracts selected pages from a source PDF in `source-pdfs/` and writes a chunk PDF to `chunk-pdfs/`.

```bash
python generate-chunk-pdf.py --working-dir tests/test-1
```

The script prompts for:

- Source PDF filename (filename only, chosen from `source-pdfs/`)
- Start PDF page
- End PDF page
- Output chunk PDF filename (editable default)

Default output naming:

- `<scan_chunk_stem>_<start:03d>-<end:03d>.pdf`
- Example: `test-a_001-005.pdf`

## Transcribe a chunk PDF

`transcribe-chunk-pdf.py` transcribes a file from `chunk-pdfs/` into:

- `transcriptions/<chunk_pdf_stem>_raw.json` — per-line text with `box_2d` coordinates (Pass 1)
- `transcriptions/<chunk_pdf_stem>-ai-log.md`

By default a Gemini model is used to do the transcription. 
To create a Gemini API key: [Google AI Studio - Get API key](https://ai.google.dev/gemini-api/docs/api-key)

### Specifying the API key to use

The environment variable `GEMINI_API_KEY` is used for storing the API key to use.

```bash
export GEMINI_API_KEY=...
```

### Example run

```bash
export GEMINI_API_KEY=...
python transcribe-chunk-pdf.py --working-dir tests/test-1
```

### Notes

- `transcriptions/` is created automatically if it does not exist.
- `--chunk-pdf` is optional. If omitted, you choose from `chunk-pdfs/` interactively. The default selection uses `.chunk-state.json` (`last_generated_output`) when available.
- `--chunk-pdf` must be a filename only (no path).
- `--prompt-md` is optional. If omitted, the script searches for `*prompt*.md` in the working directory:
  - if exactly one file matches, it is used automatically
  - if multiple files match, you can choose interactively
  - if none match, the script exits with an error
- Transcribe config is loaded from `transcribe.config.json` with this precedence:
  - `<working-dir>/transcribe.config.json`
  - `<script-dir>/transcribe.config.json` (fallback)
- The `-ai-log.md` file includes chunk filename, run timing, confidence score/label, notes, full config JSON used (including `sys_instructions`), and the full prompt used.

## Review and correct transcriptions (human pass)

This step does **not** call the model. You still run `transcribe-chunk-pdf.py` first (Pass 1) to produce `transcriptions/<stem>_raw.json`. The PySide6 app (`review-chunk-lines.py`) loads that JSON, shows each line’s crop next to editable text, and saves `transcriptions/<stem>_final.json`.

**System dependency:** [Poppler](https://poppler.freedesktop.org/) must be installed so `pdf2image` can rasterize the PDF (on Ubuntu: `sudo apt install poppler-utils`).

`--working-dir` is the same as for `transcribe-chunk-pdf.py`: the directory that contains `chunk-pdfs/` and `transcriptions/` (not those subfolders themselves).

```bash
python review-chunk-lines.py --working-dir . --chunk-pdf your-chunk.pdf
```

Example using the `tests/test-1` fixture (after the chunk PDF and `tests/test-1/transcriptions/..._raw.json` exist):

```bash
python review-chunk-lines.py --working-dir tests/test-1 --chunk-pdf test-a_001-003.pdf
```

- `--raw-json` is optional; defaults to `<working-dir>/transcriptions/<stem>_raw.json`. Relative paths are resolved under `--working-dir`.
- If `_final.json` already exists for that stem, it is loaded so you can resume editing.

## Build PDFs from transcriptions (AsciiDoc)

`transcribe-chunk-pdf.py` does not emit `.adoc` files; it writes `*_raw.json`. You can later stitch corrected `*_final.json` content into AsciiDoc for publishing. This script is for when you already have `.adoc` sources under `transcriptions/`.

`build-transcribed-chunk-pdfs.py` walks `--working-dir`, finds every directory named `transcriptions`, and runs [Asciidoctor PDF](https://asciidoctor.org/docs/asciidoctor-pdf/) on each `.adoc` file in that directory. It writes `<stem>-transcription.pdf` beside `<stem>.adoc` (for example `chunk-1.adoc` to `chunk-1-transcription.pdf`).

Prerequisite: the `asciidoctor-pdf` command (Ruby gem) must be installed and on your `PATH`.

```bash
python build-transcribed-chunk-pdfs.py --working-dir tests/test-1
```

## Developer docs

Developer-oriented content (tests, fixtures, implementation notes) is in `docs/code/`, starting with `docs/code/developer-usage.md`.
