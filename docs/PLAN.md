# CV-score — Implementation Plan (proposal)

Status: **draft for review**. Nothing here is built yet; open questions are listed at the end.

## 1. Goal

A set of Python tools that take a **job** (description + requirements) and a pool of
**candidates** (CV, cover letter, optional application form) and produce a
**ranked, explainable shortlist**. Each score comes with the evidence (quotes from the
candidate's documents) behind it, so a human reviewer can check it and override it.

Ranking factors, in order of weight by default:

1. **Experience**: how relevant, how recent, how senior, and for how long.
2. **Achievements**: concrete, ideally measurable results that relate to the role.
3. **Skills & qualifications**: hard skills, tools, certifications, education.
4. **Personal characteristics** (optional, low weight): motivation, communication,
   and values alignment, *only* where the documents show evidence of them.

## 2. Pipeline overview

```
 job.md / job.pdf ──► [Job parser] ──► JobProfile (requirements + weights, editable YAML)
                                              │
 candidates/                                  ▼
   alice/cv.pdf      ┌──────────┐   ┌──────────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐
   alice/cover.docx ─► Ingest   ├──►│ Extract      ├──►│ Redact  ├──►│ Score    ├──►│ Rank &  │
   alice/form.json   │ (text)   │   │ (structured  │   │ (bias   │   │ (per     │   │ Report  │
   bob/...           └──────────┘   │  profile)    │   │ control)│   │ dimension│   └─────────┘
                                    └──────────────┘   └─────────┘   └──────────┘
```

Each stage reads and writes plain, typed artefacts (Pydantic models serialised to JSON),
so stages can be cached, re-run on their own, inspected, and tested.

## 3. Components

### 3.1 Ingestion (`cvscore.ingest`)
- **Formats:** PDF (`pypdf` / `pdfplumber`), DOCX (`python-docx`), TXT/MD. OCR fallback
  (`pytesseract`) for scanned PDFs, optional extra.
- **Grouping documents by candidate:** use one folder per candidate by default, or a
  `manifest.csv` (`candidate_id, cv_path, cover_letter_path, form_path`). A filename
  convention (`<id>_cv.pdf`) is a fallback.
- **Application forms:** the structure is TBD, so they go through a pluggable
  `FormAdapter` interface. It starts with generic JSON/YAML/CSV-row adapters that map
  form fields to `{question, answer}` pairs, and adds specific adapters later
  (e.g. an ATS export) once the format is known.
- Output: `CandidateDocuments` (raw text per document + metadata).

### 3.2 Job profile (`cvscore.job`)
- Parse the job description into a structured **`JobProfile`**:
  - `must_have`: knock-out criteria (e.g. "5+ yrs backend", "right to work in EU")
  - `nice_to_have`: weighted preferences
  - `responsibilities`, `seniority`, `domain`, `key_skills`
  - `traits`: personal characteristics that matter for *this* role, each with a definition
  - `weights` per scoring dimension
- An LLM drafts it, and it is saved as **human-editable YAML**. The recruiter reviews or
  edits it once, and it drives every candidate's scoring. This is the main lever for
  consistency and control.

### 3.3 Structured extraction (`cvscore.extract`)
Turn free text into a **`CandidateProfile`**:
- `experience[]`: title, organisation, start/end dates, duration, seniority,
  responsibilities, technologies, domain
- `achievements[]`: statement, metric (if any), scope, linked experience entry, source quote
- `skills[]`, `education[]`, `certifications[]`, `languages[]`
- `motivation_signals[]` / `trait_evidence[]`: quotes from the cover letter and form answers
- Every extracted item keeps a **source span** (document + quote) for traceability.

Implementation: an LLM call with structured outputs (JSON schema from the Pydantic models),
plus deterministic post-processing such as date normalisation, total and recency-weighted
years, and de-duplicating skills against a normalisation map.

### 3.4 Redaction / bias control (`cvscore.redact`)
Runs **before scoring**. It removes or masks name, photo references, gender markers,
age / date of birth, nationality, marital status, address, and similar fields. Scorers
see an anonymised profile, and the mapping back to identity is kept separately. Each
field can be switched on or off in config.

### 3.5 Scoring (`cvscore.score`)
Every dimension is a pluggable `Scorer` that returns `score ∈ [0, 1]`, `confidence`,
`rationale`, and `evidence[]`.

| Dimension | Method |
|---|---|
| **Hard requirements** | Rule checks + LLM verification per `must_have` → pass / fail / unclear. A fail flags the candidate or knocks them out (configurable), never silently. |
| **Experience relevance** | Embedding similarity between experience entries and the job responsibilities, weighted by recency (decay) and duration, plus a seniority-fit term. |
| **Skills match** | Exact/normalised matching + semantic matching (embeddings) against `key_skills` / `nice_to_have`, weighted by where the skill was shown (in a role > in a list). |
| **Achievements** | LLM rubric scoring (1–5 anchored scale): relevance to the role, magnitude/impact, quantification, and ownership. |
| **Personal characteristics** | LLM rubric scoring per `trait` from the JobProfile, **evidence-required**: no quote means "insufficient evidence" (neutral), not a low score. Off or low weight by default. |

**Hybrid by design:** embeddings and rules are cheap, deterministic, and good for
pre-filtering large pools. LLM judging catches nuance, for example achievements and
whether experience is transferable. LLM calls use a fixed rubric, temperature 0,
and anonymised input, and they cite evidence.

### 3.6 Ranking (`cvscore.rank`)
- Weighted aggregate of the dimension scores (weights from the JobProfile), with knock-out handling.
- Uncertainty is shown alongside the score: low-confidence candidates are flagged for human review.
- Optional **pairwise re-ranking** of the top-K (LLM compares two anonymised profiles
  directly, aggregated with Bradley-Terry). This often gives better ordering at the top
  than absolute scores.
- Optional **two-stage mode** for large pools: cheap embedding/rule pre-filter, then the full LLM scoring on the top N.

### 3.7 Reporting (`cvscore.report`)
- `ranking.csv` / `.xlsx`: one row per candidate with the dimension scores, total, flags
- `report.html`: a ranked table and a per-candidate drill-down showing evidence quotes
- `results.json`: the full machine-readable output for downstream tools
- An audit log of model, prompts version, config hash, and timestamps, so runs are reproducible.

### 3.8 Interfaces
- **CLI** (`typer`):
  - `cvscore job parse job.pdf -o job.yaml`
  - `cvscore ingest candidates/ -o work/`
  - `cvscore score --job job.yaml --candidates candidates/ -o out/`
  - `cvscore report out/`
- **Python API** (`from cvscore import Pipeline`) for notebooks and integration.
- A small web UI (Streamlit) could come later. It is not in the MVP.

## 4. Proposed repository layout

```
cvscore/
  __init__.py
  models.py          # Pydantic: JobProfile, CandidateDocuments, CandidateProfile, ScoreCard, ...
  config.py          # settings, weights, provider selection
  ingest/            # readers (pdf, docx, txt), form adapters, candidate grouping
  job/               # JD → JobProfile
  extract/           # text → CandidateProfile (+ post-processing)
  redact/
  score/             # base Scorer + one module per dimension
  rank/              # aggregation, pairwise, two-stage
  report/            # csv/xlsx/html/json + templates
  llm/               # provider abstraction, prompt templates, caching, retries
  embed/             # embedding backends (local sentence-transformers / API)
  cli.py
tests/
  fixtures/          # synthetic CVs, cover letters, forms, job descriptions
examples/
docs/
pyproject.toml       # uv/hatch, ruff, mypy, pytest
```

## 5. Technology choices (proposed defaults)

- Python 3.11+, `pydantic` v2, `typer`, `rich`, `jinja2` (HTML report), `pandas` / `openpyxl`.
- **LLM:** a thin provider interface. The default is the Anthropic Claude API (a Sonnet-class
  model for bulk extraction and scoring, an Opus-class model for job parsing and pairwise
  top-K), with structured outputs and prompt caching. The job profile and rubric are a
  shared prefix, which makes caching cheap. It can be swapped for a local model when the
  data must not leave the premises.
- **Embeddings:** local `sentence-transformers` by default (no data egress, free), API optional.
- Disk cache keyed by (document hash, prompt version, model), so re-runs cost nothing.
- Tooling: `ruff`, `mypy`, `pytest`, GitHub Actions CI.

## 6. Evaluation & quality

- **Synthetic fixture set:** about 20–30 fake candidates per job, with known "ground truth" tiers.
- **Agreement with humans:** Spearman/Kendall correlation versus recruiter rankings, and precision@K for the shortlist.
- **Consistency:** score variance across repeated runs and across document formats (PDF vs DOCX of the same CV).
- **Bias probes:** counterfactual tests that swap names, genders, ages, or schools in otherwise
  identical CVs and check that the ranking does not move.
- **Golden tests** for parsers and extraction, and snapshot tests for prompts.

## 7. Legal / ethical guardrails (important)

Automated CV ranking is regulated in many places. It is **high-risk under the EU AI Act**
(Annex III, employment), GDPR Art. 22 restricts solely-automated decisions, and NYC Local
Law 144 requires bias audits. The design therefore assumes:
- **Decision support, not decision making:** the output is a ranked shortlist with evidence, and a human decides.
- Redaction before scoring, and personal characteristics only when evidenced and job-relevant.
- Full explainability and an audit trail for every score.
- Data minimisation and retention settings, plus a local-only mode (local LLM + embeddings).

## 8. Milestones

| # | Milestone | Deliverable |
|---|---|---|
| M0 | Scaffolding | `pyproject`, package skeleton, models, CI, synthetic fixtures |
| M1 | Ingestion | PDF/DOCX/TXT readers, candidate grouping, generic form adapter |
| M2 | Job profile | JD → editable `job.yaml` |
| M3 | Extraction | CandidateProfile with source spans + caching |
| M4 | Baseline scoring | Requirements gate, skills and experience scorers (embeddings + rules), weighted ranking, CSV output. **First usable end-to-end version.** |
| M5 | LLM scoring | Achievements + personal-characteristics rubric scorers, redaction |
| M6 | Reporting | HTML report with evidence drill-down, JSON, audit log |
| M7 | Evaluation | Metrics harness, bias probes, consistency checks |
| M8 | Extras | Pairwise top-K re-ranking, two-stage mode, specific form adapters, optional UI |

## 9. Open questions

1. **Volume & languages:** how many candidates per role, and are documents in languages other than English?
2. **Data privacy:** is sending CVs to a cloud LLM API acceptable, or must everything run locally?
3. **Application forms:** what produces them (an ATS, Google Forms, a custom web form)? Any samples?
4. **Personal characteristics:** which traits matter, and should they affect the score or only be shown?
5. **Output consumers:** recruiters (needs the HTML/Excel report) or another system (JSON/API)?
6. **Ground truth:** are there past hiring decisions or recruiter rankings to evaluate against?
7. **Knock-outs:** should failing a must-have exclude a candidate, or only flag and down-rank them?
