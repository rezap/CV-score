# CV-score

Tools to rank candidates' CVs, cover letters and application forms against a job
description, with an evidence-backed, explainable score for each candidate.

Built on LangChain / LangGraph, with models from an Azure AI Foundry project (Entra ID
auth) or the OpenAI API (API key), and Langfuse for tracing. See [`docs/PLAN.md`](docs/PLAN.md) for the design
and roadmap.

> Status: early development. The full pipeline runs end to end (ingestion with OCR, job
> profile, fast screening, redaction, extraction, scoring, ranking, report), from the
> command line or a local web app. Not built yet: recording review decisions, bias probes
> beyond the evaluation set, and the remaining agentic refinements (evidence agent,
> shortlist critic). See [the plan's status section](docs/PLAN.md#11-status-what-is-built-and-what-is-not).

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

1. **Applications:** browse to the folder with the applications and pick it. Each candidate
   is one line (open it to see how the files were grouped and what each document was taken
   for, with the reason); correct a type or leave a file out, then "Apply changes". "Only
   the N to check" lists the candidates with warnings. **Location** chips (the home country,
   EU, Europe outside the EU, rest of the world, unknown) choose whom to include in the run:
   from all, a click picks one location, further clicks add or remove others.
2. **Job:** upload the job ad (PDF, Word or text) or paste it. The model drafts a hiring
   profile: must-haves, nice-to-haves, key skills, traits, screening criteria and score
   weights. Drag a requirement by its grip between must-haves and nice-to-haves (or to
   reorder it), or use its arrow button; add and remove key skills as chips. The draft is
   cached (the same ad is drafted once), and your edits are kept in the browser. A saved
   `job.yaml` can be uploaded instead, and "use its job" under *Earlier runs* reloads the
   profile a run used.
3. **Ranking:** shows progress, then the ranking, grouped into segments that fold away:
   strong (70+), good (50-69), partial (30-49), weak, and screened out / must-have failed.
   Filter by location, find a candidate by ID (or name, with names shown), and expand or
   collapse every segment at once. Click a candidate for the requirement checks, reasons
   and quotes, and for the redacted text the model saw. "CV ↗" next to each candidate (in
   the quick screen too) opens the original document; the expanded row links all of their
   documents. Download `report.html`, `ranking.csv` and `results.json`.

Each step folds to a one-line summary (steps 1 and 2 fold when a run starts), and the
folder, job, run options and last run come back after a reload.

**Settings** (⚙ in the top bar) shows and edits every setting in `cvscore.yaml`: provider
and models per role, embeddings, ranking, screening, OCR, location and Langfuse. Saving
writes the file (keeping the previous one as `cvscore.yaml.bak`, so comments in a
hand-written file are not lost) and applies to the next run without a restart. API keys
are never shown or stored there: the panel only says whether they are set in the
environment. `CVSCORE_*` variables still win over the file, and the panel lists any that
are set. **Theme:** Light, Dark or System (follows your computer), from the button next to
Settings or in the panel; kept in the browser.

The home country is `location.home_country` in `cvscore.yaml` (default `NO`; an ISO code
or a name such as `Germany`). **Location** is read from the documents without a model: the address or town in the CV's
top area and contact lines, the sender lines of a cover letter, "I live in ..." sentences,
and the phone number's country code. A phone code alone, or towns only found among the
CV's roles, give a guess (shown with "?" and an outline: check the CV). ✈ marks candidates
who mention relocating. It is where the person lives, not their nationality or right to
work, and it can be wrong: check before rejecting anyone, and keep in mind that filtering
by location may need a lawful, job-related reason where you hire.

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
- **Formats:** PDF, DOCX, TXT/MD; `.doc`, `.rtf` and `.odt` if LibreOffice is installed;
  images (PNG, JPEG, TIFF, ...) whose file name says they are a CV, letter or form
  (`Jane_CV.jpg`; other pictures, such as portraits, are ignored). Password-protected,
  damaged or mislabelled files get a message saying so.
- **Scanned CVs (OCR):** as a fallback, PDF pages without a text layer (scans, text turned
  into outlines), pages with only a little text over a large image (a scan with a stamped
  page number), documents with almost no text, and image files are read by your configured
  model, which must accept images (e.g. gpt-4.1,
  gpt-4o, gpt-5). The page image is rendered locally and sent to the model of the `ocr.role`
  deployment; the transcript is then read, grouped and redacted like any other text.
  This is the one model call that sees personal data, since an image can't be redacted
  before it is read: it is never traced to Langfuse, and results are cached so each page
  is read once. OCR'd documents carry a warning to check
  names and numbers against the original. Switch it off with `ocr.enabled: false` or
  `--no-ocr`.
- **Caching:** extracted text is cached in the output folder's `.cache/` (the web app's
  `cvscore-runs/.cache/`), keyed by each file's content and the extractor version, so a
  folder is parsed once: rescanning, screening and running again reuse it, and a changed
  file or an upgraded extractor is read again. Model results (including OCR) and
  embeddings are cached there too. The cache holds the full text: treat it as personal
  data.
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
# 2. review and edit job.yaml: must-haves, nice-to-haves, key skills, traits, weights,
#    screening criteria
cvscore screen --job job.yaml --candidates candidates/ --out screen/   # 3a. fast first look
cvscore run --job job.yaml --candidates candidates/ --out out/ --shortlist 15  # 3b. in depth
```

There are two levels of assessment:

- **Screening (fast, cheap):** `cvscore screen`, or "Quick screen" in the web app. The job
  profile's screening criteria (drafted with the profile from the ad and general knowledge
  of the field, e.g. "Programming for data analysis": Python, R, pandas, SQL, ...) are
  searched in each candidate's redacted documents. Whole-word keyword matches give a score
  per criterion (more distinct terms, higher score), and with an embeddings model
  configured, a semantic match finds competences described in other words (it can give at
  most 80%: keywords are needed for full credit). A criterion marked `required` with no
  match screens the CV out (ranked last, still listed, with the reason). The output shows
  which terms matched and where, and a rough years-of-experience span. No chat model reads
  the CVs; only scanned CVs need OCR. Costs nothing or almost nothing; seconds per folder.
- **In-depth assessment:** `cvscore run` (below), 2 model calls per candidate. With
  `--shortlist N` ("Screen, then assess the best N" in the web app), everyone is screened
  and only the best N not screened out are assessed in depth; the others are listed after
  them with their screening score.

Screening is a first impression, not a verdict: a keyword search favours CVs that use the
expected words. Keep `required` for what practically every qualified CV mentions, and look
at the screened-out list before discarding anyone.

For each candidate, `run`:

1. reads the documents (as `ingest` does);
2. **redacts** them before any model sees the text: contact details, the candidate's name
   (as found in the documents, the folder or file name), and lines such as date of birth
   or nationality are replaced by visible markers such as `[REDACTED: name]` and
   `[REDACTED: contact details]`;
3. **extracts** a structured profile (roles, dates, achievements, skills), computing years
   of experience itself;
4. **scores** it against the profile with many small judgements rather than one overall
   impression (see *How scoring works* below). Every judgement must quote the documents;
   quotes that cannot be found are discarded, and a judgement without a verifiable quote
   counts as "not shown";
5. **ranks** in plain Python: a weighted total, with flags for failed or unclear
   must-haves, low confidence and unreadable documents. A failed must-have ranks the
   candidate last (or removes the score, with `ranking.knockout: exclude`), but it is still
   shown for a person to overrule;
6. **checks close calls:** the best `ranking.repeat_top` (10) are scored a second time,
   with documents and lists in another order, and the two readings averaged; candidates
   whose totals are too close to tell apart share a **tier**, and the judge model compares
   same-tier neighbours head to head, in both orders, moving one up only when both orders
   agree (`ranking.pairwise_top`).

### How scoring works

| Part | Default weight | How it is scored |
|---|---|---|
| Requirements | 30% | Each must-have and nice-to-have gets the evidence level the documents show. Must-haves count 2/3, nice-to-haves (by their weights) 1/3 |
| Experience | 30% | Relevance (45%) and seniority (25%) judged 1-5; years in the relevant roles against the years asked for (20%) and how recent they are (10%), computed from the dates |
| Skills | 25% | Each key skill gets the evidence level shown |
| Achievements | 15% | 1-5, judged on substance rather than on whether numbers are quoted |
| Traits | 0% | 1-5, only with evidence; shown unless you give them a weight |

**Evidence levels.** Each requirement and key skill must show at a level to count in full:
*listed* (named anywhere, e.g. a skills section), *used in work* (in a described role or
project; the default), or *proven* (applied with ownership, scale or results). Below it,
credit is partial: listed when use in work is asked for earns half; not shown at all earns
a quarter for a requirement (unknown, not failed) and nothing for a skill. Where each
skill's name occurs (work history or a skills list) is found without the model and also
counts. Set the level for the whole job (`evidence_default`) and per requirement
(`evidence`); credentials a CV can only state (degrees, certifications, languages) count as
used once clearly stated. **Match:** `equivalent` (the default) lets comparable
alternatives count (Pulumi or Bicep for Terraform); `exact` does not. In the web app these
are the *How evidence counts* switch and the two selectors under each requirement.

Scores the model was unsure about are pulled towards the middle, so an unsupported 5
cannot top the list. Years of experience asked for come from `target_years` in the
profile (drafted from the ad), or from the seniority.

### Evaluating the ranking

`cvscore eval evals/cloud-architect --repeats 3` runs a set of fictional candidates with
known answers and reports how well the ranking matches the expected tiers, whether
controlled pairs hold (e.g. the same CV with Terraform used in work beats it with Terraform
only listed; keyword stuffing does not pay; renaming a candidate changes nothing), and how
much each total moves between runs. It makes fresh model calls. See
[`evals/README.md`](evals/README.md).

Output in `out/`: `report.html` (ranking plus evidence for every score, with tiers and the
spread between readings), `ranking.csv`,
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

`cvscore.example.yaml` lists every setting with comments. `cvscore.yaml` maps each model
role to a deployment (Azure) or model (OpenAI):

| Role | Used for |
|---|---|
| `extractor` | High-volume structured extraction from documents; OCR by default |
| `scorer` | Rubric scoring (default for roles you don't set) |
| `agent` | Drafting the job profile; tool-calling agents later |
| `judge` | Head-to-head comparison of near-tied candidates (`ranking.pairwise_top`) |

Reasoning models (o-series, gpt-5 and later) get no temperature unless you set one;
`reasoning_effort` can be set per role.

| Section | Settings |
|---|---|
| `azure` / `openai` | endpoint (Azure) or `api_key_env` / `base_url` (OpenAI), `models`, `embeddings` (used by screening's semantic match), `max_concurrency` (candidates assessed at once), `max_retries`, `timeout_s` |
| `langfuse` | `enabled`, `host`, `environment`, `release`, `sample_rate`, `mask_pii` (keys from `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`) |
| `ocr` | `enabled`, `role` (whose vision model reads the pages), `dpi`, `max_pages` |
| `screening` | `semantic`, `similarity_floor`, `similarity_full`, `required_semantic` |
| `location` | `home_country`: ISO code or name, default `NO` |
| `ranking` | `knockout` (`flag` or `exclude`), `low_confidence` (threshold for the low-confidence flag), `max_dropped_quotes`, `repeat_top` (second readings of the best N; 0 = off), `tie_margin`, `pairwise_top` (judge comparisons among the best N; 0 = off) |

Any value can be overridden by environment variables with the `CVSCORE_` prefix and `__`
for nesting, e.g. `CVSCORE_AZURE__ENDPOINT`. Use `CVSCORE_CONFIG` to point at another file.

## Development

The same checks CI runs (prefix each with `uv run` if you use uv):

```bash
ruff check . && ruff format --check . && mypy cvscore && pytest
cd frontend && npm ci && npm run build   # front end: type-check and build
```
