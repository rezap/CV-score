# CV-score

Tools to rank candidates' CVs, cover letters and application forms against a job
description, with an evidence-backed, explainable score for each candidate.

Built on LangChain / LangGraph, Azure OpenAI models in an Azure AI Foundry project
(Entra ID auth), and Langfuse for tracing. See [`docs/PLAN.md`](docs/PLAN.md) for the design
and roadmap.

> Status: early development. Config, Azure models, Langfuse tracing and document ingestion
> are in place; structured extraction, scoring and ranking are next.

## Setup

Requires Python 3.11+.

**With [uv](https://docs.astral.sh/uv/) (recommended):**

```bash
uv sync                 # creates .venv and installs the package + dev tools
uv run pytest           # run the tests
```

**With pip:**

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

The tests need no Azure or Langfuse access, and they ignore any `cvscore.yaml`, `.env`,
or `CVSCORE_*` / `LANGFUSE_*` / `AZURE_*` variables on your machine.

To use the tool against your own services:

```bash
cp cvscore.example.yaml cvscore.yaml     # fill in endpoint + deployment names
cp .env.example .env                     # Langfuse keys
az login                                 # Entra ID for Azure OpenAI
```

Your identity needs the **Cognitive Services OpenAI User** role on the Azure resource
behind your Foundry project.

Check the setup:

```bash
cvscore doctor          # show resolved config
cvscore doctor --live   # call each deployment and Langfuse
```

## Reading candidate documents

Put each candidate's files in their own folder:

```
candidates/
  Jane Doe/        cv.pdf  cover_letter.docx  form.json
  Sam Roe/         resume.docx
```

```bash
cvscore ingest candidates/ --out work/
```

This writes one JSON file per candidate to `work/candidates/`, named by a pseudonymous ID
(`c-…`), with the text, its structure (headings, list items, sections, pages), a quality
score, and warnings, e.g. for scanned PDFs. `work/identity_map.json` maps IDs back to folder
names. Treat the whole `work/` folder as personal data: the JSON files still hold the full
document text, with contact details flagged (`is_contact`) but not yet removed. Redaction
before scoring comes in a later milestone.

- **Formats:** PDF, DOCX, TXT/MD; `.doc`, `.rtf` and `.odt` if LibreOffice is installed.
  Scanned PDFs (no text layer) are flagged, not OCR'd.
- **Document type** comes from the file name (`cv`, `resume`, `lebenslauf`, `cover_letter`,
  `anschreiben`, `form`, ...). A single unnamed document in a folder is taken as the CV.
- **Other layouts:** files directly in `candidates/` named `<who>_cv.pdf`,
  `<who>_cover_letter.docx`; or `--manifest manifest.csv` with `candidate,cv,cover_letter,form`
  columns.
- **Application forms:** JSON/YAML (`{"question": "answer"}` or a list of
  `{question, answer}`) or CSV in the candidate's folder, or one export for everyone with
  `--forms export.csv --form-key <column matching the folder name>`.

## Configuration

`cvscore.yaml` maps each model role to a deployment in your Foundry project:

| Role | Used for |
|---|---|
| `extractor` | High-volume structured extraction from documents |
| `scorer` | Rubric scoring (default for roles you don't set) |
| `agent` | Tool-calling agents |
| `judge` | Pairwise comparison and shortlist review |

Any value can be overridden by environment variables with the `CVSCORE_` prefix and `__`
for nesting, e.g. `CVSCORE_AZURE__ENDPOINT`. Use `CVSCORE_CONFIG` to point at another file.

## Development

The same checks CI runs (prefix each with `uv run` if you use uv):

```bash
ruff check . && ruff format --check . && mypy cvscore && pytest
```
