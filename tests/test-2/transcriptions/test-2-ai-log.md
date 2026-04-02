# AI transcription run log

- Chunk PDF file: `test-2.pdf`
- Run started at: `2026-04-02 06:45`
- Total pages: `1`
- Total inference time (minutes): `0.37`
- Average time per page (seconds): `22.33`
- Prompt tokens (input): `1786`
- Completion tokens (output): `2064`
- Total tokens: `3850`
- Confidence score: `1.0`
- Confidence label: `high`
- Notes: 
## Transcribe config used

```json
{
  "model": "gemini/gemini-3.1-pro-preview",
  "temperature": 0.0,
  "reasoning_effort": "high",
  "media_resolution": "high",
  "sys_instructions": "Transcribe this chunk PDF to AsciiDoc and respond with JSON only. Use this key order: confidence_score, confidence_label, notes, transcription. confidence_score must be a number from 0.0 to 1.0. confidence_label must be one of: 'low', 'medium', 'high'. Preserve structure and formatting. For every confidence score below 1.0, the 'notes' field must contain a diagnostic list of specific ambiguities. For each instance, specify the line number or the word snippet followed by the conflict (for example, 'Line 8: \"s\" or \"f\" in \"blessing\"?'). Strictly avoid general descriptions of the document or praise for formatting. If the score is 1.0, the 'notes' field should be an empty string."
}
```

## Prompt used

````markdown
# Transcription Instructions

**Role:** Archival Transcription Assistant.
**Task:** Literal transcription of historical text for archival and research purposes.

**Context:** The provided images contain pages from a historical document. This material is being digitized to support academic study and translation.

**Instructions:**
- Transcribe the text exactly as it appears on the page.
- **Formatting:**
    - Use AsciiDoc for structure.
    - **Paragraph Numbers:** Do not use list formatting for paragraph or verse numbers. Prefix the number with `{empty}` (e.g., `{empty}123.`) to prevent the editor or renderer from re-indexing them as a new list.
    - **Paragraphing:** AsciiDoc requires a blank line between all paragraphs and around all headers. You MUST separate all paragraphs and headers with a blank line in your output, even if they appear continuous in the source. However, if a single paragraph continues across a page break, you MUST still insert the `// Page X` comment at the exact point of the page break. To ensure AsciiDoc treats it as a single continuous paragraph, place the comment on its own line but do NOT insert a blank line before or after the comment.
- **Structure:**
    - Use AsciiDoc headers (`==`, `===`, `====`) for titles and major section headings found in the text.
    - Transcribe the page number as an AsciiDoc comment (e.g., `// Page 1`).
- **Preserve:**
    - All archaic spellings, punctuation, and theological/historical vocabulary. Do not modernize or "fix" the text.
    - **Font Styles:** Preserve all italic and bold text found in the source using AsciiDoc syntax (`_italic_` and `*bold*`).
    - **Character Conversion:** Convert the historical "long s" (`ſ`) to a standard `s`.
    - **Initial Capitals:** If a paragraph starts with a word in ALL CAPS, convert it to Sentence case (e.g., `THESE things` becomes `These things`) unless it is a proper noun that should remain capitalized.
- **Ignore:**
    - Running heads (text at the very top of pages used for navigation).
    - Printer’s ornaments or decorative horizontal lines.
    - Signature marks (letters/numbers at the bottom center of some printed pages).
    - Catchwords (the single word often found at the bottom right of a page that is repeated at the top of the next).

````
