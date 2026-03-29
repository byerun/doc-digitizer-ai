---
name: chunk PDF generator
overview: Build an interactive Python CLI to split source PDF ranges into chunk PDFs, with editable default filenames and local persisted defaults in the working directory.
todos:
  - id: add-script
    content: Create interactive Python script for prompt, validation, extraction, and naming.
    status: completed
  - id: extract-core
    content: Extract non-UI logic into a reusable class module used by CLI and tests.
    status: completed
  - id: add-state
    content: Implement local persisted state in the current working directory.
    status: completed
  - id: prepare-fixtures
    content: Update test-1 fixture AsciiDocs and PDF layout to model a real working directory.
    status: completed
  - id: document-usage
    content: Add dependency and usage notes in requirements/readme.
    status: completed
  - id: verify-flow
    content: Run CLI and automated tests against test-1 fixture and confirm defaults/output naming.
    status: completed
isProject: false
---

# Implement Interactive Chunk PDF Generator

## Scope

Create a new command-line Python script that:

- prompts for `scan_chunk_path`, `start_pdf_page`, and `end_pdf_page`
- persists defaults between runs in the current working directory
- expects source PDFs under `source-pdfs/` and prompts for filename only
- extracts selected PDF page ranges into `chunk-pdfs/`
- proposes a default output filename using zero-padded 3-digit page numbers, and lets the reviewer edit it before write
- uses a shared core class for business logic so non-UI behavior is automatically testable

## Files to add/update

- Main script: `[doc-digitizer-ai/generate-chunk-pdf.py](doc-digitizer-ai/generate-chunk-pdf.py)`
- Core module: `[doc-digitizer-ai/chunk_pdf_generator.py](doc-digitizer-ai/chunk_pdf_generator.py)` (class used by CLI and tests)
- Test module: `[doc-digitizer-ai/tests/test_generate_chunk_pdf.py](doc-digitizer-ai/tests/test_generate_chunk_pdf.py)`
- Dependency note: `[doc-digitizer-ai/requirements.txt](doc-digitizer-ai/requirements.txt)` (add `pypdf` if missing)
- Usage doc: `[doc-digitizer-ai/README.md](doc-digitizer-ai/README.md)`
- Fixture updates:
  - `[doc-digitizer-ai/tests/test-1/test-a.adoc](doc-digitizer-ai/tests/test-1/test-a.adoc)`
  - `[doc-digitizer-ai/tests/test-1/test-b.adoc](doc-digitizer-ai/tests/test-1/test-b.adoc)`
  - `[doc-digitizer-ai/tests/test-1/source-pdfs/](doc-digitizer-ai/tests/test-1/source-pdfs/)`
  - `[doc-digitizer-ai/tests/test-1/chunk-pdfs/](doc-digitizer-ai/tests/test-1/chunk-pdfs/)`

## Behavior details

- Interactive prompts with defaults:
  - `source_pdf_filename`: default to last filename used from `source-pdfs/`
  - `start_pdf_page`: default to `last_end_pdf_page + 1` (or `1` if no history)
  - `end_pdf_page`: no computed default unless same as start is desired; enforce `end >= start`
- Validation:
  - ensure `source-pdfs/` exists in current working directory
  - ensure selected file exists in `source-pdfs/` and has `.pdf` extension
  - ensure start/end are positive integers
  - ensure end page does not exceed total PDF pages
- Output:
  - destination directory: `chunk-pdfs/`
  - output filename default format: `<scan_chunk_stem>_<start:03d>-<end:03d>.pdf`
  - output filename prompt allows editing that default before file creation
  - example: `book-part-a_001-010.pdf`
- Persisted state (local to this work directory):
  - file path: `.chunk-state.json` in current working directory
  - fields: last source filename, last end page, last generated output, updated timestamp
- Core architecture:
- `ChunkPdfGenerator` class encapsulates state loading/saving, filename defaults, validation, and PDF extraction
- `generate-chunk-pdf.py` handles interactive prompts and delegates operations to `ChunkPdfGenerator`
  - tests call `ChunkPdfGenerator` directly for non-UI behaviors

## Implementation approach

- Use `argparse` only for optional future extension and script description; core flow remains prompt-based.
- Use `pathlib` for path handling and directory creation.
- Use `pypdf.PdfReader`/`PdfWriter` for page extraction.
- Keep functions small and testable:
  - Core class methods:
    - `load_state()`
    - `save_state(state)`
    - `resolve_source_pdf(filename)`
    - `build_default_filename(scan_pdf, start_page, end_page)`
    - `extract_pages(scan_pdf, start_page, end_page, output_pdf)`
    - `create_chunk_pdf(source_filename, start_page, end_page, output_filename)`
  - CLI-only helper:
    - `prompt_with_default(label, default)`
- Follow PEP 8 and your style preferences (single quotes, no unnecessary type hints for no-return methods).
- Terminology update: prefer `source_pdf` and `chunk_pdf` in prompts/docs.

## Verification steps

- Prepare fixture content in `tests/test-1/`:
  - use `test-a.adoc` and `test-b.adoc` as the AsciiDoc sources
  - update titles to represent different parts of the same work
  - add explicit page breaks so each AsciiDoc renders to 5 PDF pages
  - generate PDFs via `asciidoctor-pdf` and place them in `tests/test-1/source-pdfs/`
- Manual run from `tests/test-1/` working directory:
  - choose source filename from `source-pdfs/`
  - create `001-005`
  - rerun and confirm start defaults to `006`
  - create `006-010`
- Confirm generated default filename is editable and custom names are accepted.
- Confirm files are generated in sorted order when default naming is used.
- Confirm `.chunk-state.json` is updated in the current directory.
- Confirm script accepts filename-only input and resolves to `source-pdfs/<filename>`.
- Run automated tests for core class behavior (state handling, validation, default naming, extraction range logic).

