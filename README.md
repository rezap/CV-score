# CV-score

Tools to rank candidates' CVs, cover letters and application forms against a job
description, with an evidence-backed, explainable score for each candidate.

Built on LangChain / LangGraph, with models from an Azure AI Foundry project (Entra ID
auth) or the OpenAI API (API key), and Langfuse for tracing. See [`docs/PLAN.md`](docs/PLAN.md) for the design
and roadmap.

> Status: early development. The full pipeline runs end to end (job profile, ingestion,
> redaction, extraction, scoring, ranking, report). The agentic refinements (evidence
> agent, pairwise judge, shortlist critic) are next; see the plan.

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

To use the tool against your own services, copy `cvscore.example.yaml` to `cvscore.yaml`
and `.env.example` to `.env` (Langfuse keys), then pick a provider:

- **Azure AI Foundry** (`provider: azure`): fill in the endpoint and deployment names and
  run `az login`. Your identity needs the **Cognitive Services OpenAI User** role on the
  Azure resource behind your Foundry project.
- **OpenAI** (`provider: openai`): fill in model names and set `OPENAI_API_KEY` in the
  environment or in `.env`. The key is never read from `cvscore.yaml`.

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
names. Treat the whole `work/` folder as personal data: the JSON files hold the full
document text, with contact details flagged (`is_contact`); `cvscore run` removes them
before anything is sent to a model.

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

## Assessing candidates

```bash
cvscore job draft job_ad.pdf -o job.yaml          # 1. draft a hiring profile from the ad
# 2. review and edit job.yaml: must-haves, nice-to-haves, key skills, traits, weights
cvscore run --job job.yaml --candidates candidates/ --out out/   # 3. assess and rank
```

For each candidate, `run`:

1. reads the documents (as `ingest` does);
2. **redacts** them: contact details, the name (from the folder name), and lines such as
   date of birth or nationality are removed before any model sees the text;
3. **extracts** a structured profile (roles, dates, achievements, skills), computing years
   of experience itself;
4. **scores** it against the profile with a fixed 1-5 rubric: must-have and nice-to-have
   checks, experience, achievements, skills, and traits (traits count only if you give them
   a weight). Every judgement must quote the documents; quotes that cannot be found are
   discarded, a "pass" without a verifiable quote becomes "unclear", and a high score
   without evidence gets low confidence;
5. **ranks** in plain Python: a weighted total, with flags for failed or unclear
   must-haves, low confidence and unreadable documents. A failed must-have ranks the
   candidate last (or removes the score, with `ranking.knockout: exclude`), but it is still
   shown for a person to overrule.

Output in `out/`: `report.html` (ranking plus evidence for every score), `ranking.csv`,
`results.json`. The report shows pseudonymous IDs; add `--reveal-names` to show folder
names. Model results are cached in `out/.cache`, so re-running after editing the ranking
weights costs nothing (use `--no-cache` to force fresh calls). Each run is traced in
Langfuse under its run id when Langfuse is enabled.

This is decision support: read the evidence before deciding, especially for flagged
candidates.

## Live test in CI

`tests/live` runs the whole pipeline (draft the job profile, read, redact, extract, score,
rank, report) against a real OpenAI model, on fictional candidates generated by the tests:
a strong match, an adjacent profile, an unrelated one and a scanned CV. It checks what a
working system must get right whatever the model's wording: the strong candidate ranks
first and the unrelated one last, the scanned CV is never sent to the model, scores are
backed by verified quotes, and no names, emails or phone numbers appear in any prompt.

- **GitHub Actions:** add the repository secret `OPENAI_API_KEY` (and optionally the
  variable `OPENAI_MODEL`), then run **Actions > Live LLM test > Run workflow**, or add the
  `live-test` label to a pull request. The report is uploaded as the `live-report` artifact.
- **Locally:** `CVSCORE_LIVE=1 OPENAI_API_KEY=sk-... uv run pytest tests/live -v -s`

It is skipped in normal test runs and costs a few cents per run.

## Configuration

`cvscore.yaml` maps each model role to a deployment (Azure) or model (OpenAI):

| Role | Used for |
|---|---|
| `extractor` | High-volume structured extraction from documents |
| `scorer` | Rubric scoring (default for roles you don't set) |
| `agent` | Tool-calling agents |
| `judge` | Pairwise comparison and shortlist review |

`ranking` settings: `knockout` (`flag` or `exclude`), `low_confidence` (threshold for the
low-confidence flag) and `max_dropped_quotes`.

`max_concurrency` (under `azure` or `openai`) caps how many candidates are assessed at once.

Any value can be overridden by environment variables with the `CVSCORE_` prefix and `__`
for nesting, e.g. `CVSCORE_AZURE__ENDPOINT`. Use `CVSCORE_CONFIG` to point at another file.

## Development

The same checks CI runs (prefix each with `uv run` if you use uv):

```bash
ruff check . && ruff format --check . && mypy cvscore && pytest
```
