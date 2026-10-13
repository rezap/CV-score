# CV-score

Tools to rank candidates' CVs, cover letters and application forms against a job
description, with an evidence-backed, explainable score for each candidate.

Built on LangChain / LangGraph, with models from an Azure AI Foundry project (Entra ID
auth) or the OpenAI API (API key), and Langfuse for tracing. See [`docs/PLAN.md`](docs/PLAN.md) for the design
and roadmap.

> Status: early development. The full pipeline runs end to end (job profile, ingestion,
> redaction, extraction, scoring, ranking, report), from the command line or a local web
> app. The agentic refinements (evidence agent, pairwise judge, shortlist critic) are next;
> see the plan.

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
  run `az login`. The endpoint can be the v1 API (`https://<resource>.openai.azure.com/openai/v1`)
  or the resource itself (`https://<resource>.openai.azure.com`, classic API with
  `api_version`). Your identity needs the **Cognitive Services OpenAI User** role on the
  Azure resource behind your Foundry project.
- **OpenAI** (`provider: openai`): fill in model names and set `OPENAI_API_KEY` in the
  environment or in `.env`. The key is never read from `cvscore.yaml`.

Check the setup:

```bash
cvscore doctor          # show resolved config
cvscore doctor --live   # call each deployment and Langfuse
```

## Web app

```bash
cd frontend && npm install && npm run build && cd ..   # once (needs Node 18+)
cvscore serve                                           # http://127.0.0.1:8000
```

1. **Applications:** browse to the folder with the applications and pick it. The app shows
   how the files were grouped into candidates and what each document was taken for (CV,
   cover letter, form), with the reason. Correct a type or leave a file out, then
   "Apply changes".
2. **Job:** upload the job ad (PDF, Word or text) or paste it. The model drafts a hiring
   profile: must-haves, nice-to-haves, key skills, traits and score weights. Edit it. A
   saved `job.yaml` can be uploaded instead.
3. **Ranking:** shows progress, then the ranking with a score per dimension and flags. Click
   a candidate for the requirement checks, reasons and quotes, and for the redacted text
   the model saw. Download `report.html`, `ranking.csv` and `results.json`.

Names are hidden (pseudonymous IDs) until you tick "Show names". Runs are kept in
`cvscore-runs/` (personal data: keep it private), and model results are cached there, so
re-running after changing weights costs nothing. Options: `--root` limits which folders
can be opened (default: your home folder), `--port`, `--runs-dir`, `--config`. The app
binds to 127.0.0.1; it reads your files, so don't expose it on a network you don't trust.

For front-end development, run `cvscore serve` and `npm run dev` in `frontend/` (Vite
proxies `/api` to port 8000).

## Reading candidate documents

Point the tools at any folder of applications. The layout is worked out from the files:

```
applications/                      applications/                 applications/
  Jane Doe/  cv.pdf  letter.docx     Jane_Doe_CV.pdf               CVs/      a.pdf  b.pdf
  Sam Roe/   resume.docx             Cover letter - Jane Doe.pdf   Letters/  1.pdf  2.pdf
                                     CV_final.pdf  (Sam's)
                                     application.docx  (Sam's)
```

```bash
cvscore ingest applications/ --out work/
```

- **What each document is** comes from its file name (`cv`, `resume`, `lebenslauf`,
  `cover letter`, `søknad`, `anschreiben`, `form`, ...) and its content: section headings
  and date ranges make a CV; a salutation, a closing ("Kind regards", "Med vennlig hilsen")
  and paragraphs of prose make a letter. A file named "cv" that reads like a letter is
  flagged. Identical copies are skipped.
- **Whose it is:** the name at the top of a CV, the signature under a letter, a "Name"
  answer in a form, the email address, and the file name. Files that share a name, an
  email, or a file name (minus words like "CV", "cover letter", "final", "v2") are one
  candidate. A folder holding one person's documents is one candidate; a folder named after
  a person is never merged with another person's folder. Two CVs that disagree on name and
  email are never merged.
- **Formats:** PDF, DOCX, TXT/MD; `.doc`, `.rtf` and `.odt` if LibreOffice is installed.
  Scanned PDFs (no text layer) and PDFs whose text was turned into outlines are flagged,
  not OCR'd. Password-protected, damaged or mislabelled files get a message saying so.
- **When a file is read badly:** `cvscore inspect cv.pdf` shows what was read: the
  document type and why, the name found, a quality score, warnings, and the text block by
  block (`--redacted` shows it as the models see it).
- **Application forms:** JSON/YAML (`{"question": "answer"}` or a list of
  `{question, answer}`) or CSV next to the candidate's files, or one export for everyone
  with `--forms export.csv --form-key <column with the folder or candidate name>`.
- **Manifest:** to set everything explicitly, `--manifest manifest.csv` with
  `candidate,cv,cover_letter,form` columns.

This writes one JSON file per candidate to `work/candidates/`, named by a pseudonymous ID
(`c-…`), with the text, its structure (headings, list items, sections, pages), a quality
score, and warnings. `work/identity_map.json` maps IDs back to names. Treat the whole
`work/` folder as personal data: the JSON files hold the full document text, with contact
details flagged (`is_contact`); `cvscore run` removes them before anything is sent to a
model.

## Assessing candidates

```bash
cvscore job draft job_ad.pdf -o job.yaml          # 1. draft a hiring profile from the ad
# 2. review and edit job.yaml: must-haves, nice-to-haves, key skills, traits, weights
cvscore run --job job.yaml --candidates candidates/ --out out/   # 3. assess and rank
```

For each candidate, `run`:

1. reads the documents (as `ingest` does);
2. **redacts** them before any model sees the text: contact details, the candidate's name
   (as found in the documents, the folder or file name), and lines such as date of birth
   or nationality are replaced by visible markers such as `[REDACTED: name]` and
   `[REDACTED: contact details]`;
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
cd frontend && npm ci && npm run build   # front end: type-check and build
```
