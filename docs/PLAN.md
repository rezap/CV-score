# CV-score — Implementation Plan

Status: **in development.** The pipeline runs end to end (ingestion with OCR, job profile,
screening, redaction, extraction, scoring, ranking, report) from the CLI and a local web
app. ✅ marks what is built; [§11](#11-status-what-is-built-and-what-is-not) lists what is
built and what is not. Open questions are in §10.

**Stack:** Python · LangChain + **LangGraph** (agentic orchestration) · **Azure OpenAI
models via an Azure AI Foundry project** or the **OpenAI API** · **Langfuse** (tracing,
prompt management, evaluation) · FastAPI + React (web app).

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

## 2. Architecture: a deterministic skeleton with agentic nodes

A ranking tool has to be **reproducible and auditable**, so "agentic" here does not mean
one free-roaming agent. The design is a **LangGraph state graph**:

- The **outer flow is fixed**: ingest → job profile → per-candidate assessment → rank → report.
- **Agents work inside nodes** where judgement and adaptive investigation pay off:
  - drafting the job profile
  - investigating unclear evidence for a candidate
  - comparing close candidates
  - critiquing the final shortlist
- Every LLM output is a **typed Pydantic object** (`with_structured_output`), and the final
  numeric aggregation is plain Python, never an LLM.
- **Human-in-the-loop** checkpoints use LangGraph `interrupt()`.

```
                         ┌───────────────────────── LangGraph (checkpointed) ─────────────────────────┐
 job.pdf ──► JobProfiler agent ──► [HITL: approve/edit job.yaml]                                       │
                                            │                                                         │
 candidates/ ──► Ingest ──► Extract ──► Redact ──► fan-out per candidate (Send API, parallel)         │
                                                    │                                                 │
                                                    ▼                                                 │
                                    ┌──────── Candidate Assessor (subgraph) ────────┐                 │
                                    │ 1. baseline scorers (rules + embeddings)       │                 │
                                    │ 2. rubric scorers (LLM, structured)            │                 │
                                    │ 3. Evidence agent ◄─ tools ─► loop while       │                 │
                                    │    low-confidence / unclear must-haves         │                 │
                                    │ 4. ScoreCard (scores + evidence + confidence)  │                 │
                                    └───────────────────────────────────────────────┘                 │
                                                    │ fan-in                                          │
                                                    ▼                                                 │
                              Aggregate & rank ──► Pairwise judge (top-K) ──► Shortlist critic        │
                                                    │                                                 │
                                    [HITL: review borderline / flagged candidates]                    │
                                                    ▼                                                 │
                                                 Report                                               │
                         └─────────────────── every node/LLM/tool call traced in Langfuse ────────────┘
```

## 3. Components

### 3.1 Ingestion (`cvscore.ingest`) ✅
- **Extractors (built):** `pdfplumber` for PDF and `python-docx` for DOCX, used directly
  rather than through LangChain loaders, because those discard layout (column order,
  headings, text boxes) that evidence quotes and section-aware scoring rely on.
  - PDF: detects a gutter. With section headings on both sides (sidebar CV) it reads
    column by column; otherwise (date or label column) row by row. It also re-joins wrapped
    lines and end-of-line hyphenation, drops running headers/footers, recognises bullets
    including unmapped `(cid:N)` and Symbol-font glyphs, and finds headings by font size or
    section name.
  - DOCX: body order including tables, heading/list styles, manual bullets after soft breaks,
    text boxes (without Word's duplicate fallback copy), layout tables read cell by cell,
    and headers/footers flagged as contact text.
  - TXT/MD directly; `.doc`/`.rtf`/`.odt` converted with LibreOffice when installed.
- **Output per document:** normalised markdown text, typed blocks (heading / paragraph /
  list item / table row, with section, page and an `is_contact` flag for redaction), SHA-256,
  a 0–1 quality score and warnings (scanned pages, broken font encoding, garbled text).
- **Grouping documents by candidate (built):** one folder per candidate, everyone's files
  in one folder, or files sorted by type (`CVs/`, `Letters/`); or a `manifest.csv`. The
  document type comes from the file name (EN/DE/NO keywords) and the content (section
  headings and dates make a CV; salutation, closing and prose make a letter). Owners come
  from the name at the top of a CV, a letter's signature, a form's "Name" answer, the email
  and the file name; files are merged by shared name, email or file stem, never across two
  CVs that disagree. Labels become pseudonymous IDs; the mapping is written separately.
- **Application forms:** the structure is TBD, so they get generic adapters: JSON/YAML
  mappings or question/answer lists, single-candidate CSV, and multi-candidate CSV exports
  (`--forms --form-key`). Answers to personal-data questions are flagged as contact text.
  Specific adapters (ATS exports) can come later.
- **OCR (built):** pages without a text layer (or only a little text over a large image),
  near-empty documents, and image files named as a CV/letter/form are rendered locally
  (`pypdfium2`) and transcribed by the vision model of the `ocr.role` deployment, page by
  page as a fallback. It is the one call that sees unredacted data, so it is never traced;
  results are cached. Azure AI Document Intelligence is no longer needed for this.
- **Extraction cache (built):** extracted documents are cached by file content, document
  type and a fingerprint of the extractor code and libraries.
- **Location (built, `cvscore.location`):** where each candidate lives (the configured home
  country, EU, Europe outside the EU, rest of the world), read without a model from the
  address/town in the CV's top area, a letter's sender lines, "I live in ..." sentences and
  the phone's country code. Used by the web app to filter candidates; it is residence, not
  nationality or right to work.

### 3.2 Job profile: `JobProfiler` agent (`cvscore.job`) ✅ (MVP)
- **Built:** one structured-output call drafts the `JobProfile` (including screening
  criteria), saved as editable YAML or edited in the web app; drafts are cached.
- **Planned:** a small ReAct-style agent with tools: `read_job_description`, `lookup_skill_taxonomy`
  (normalises skills and synonyms), and `propose_weights`.
- Produces a structured **`JobProfile`**:
  - `must_have` (knock-outs)
  - `nice_to_have` (weighted)
  - `responsibilities`, `seniority`, `domain`, `key_skills`
  - `traits`, each with a definition and examples of evidence
  - `weights` per dimension
  - `screening` criteria (built): competences with search terms, some `required`, for
    the fast screen (§3.5a)
- Saved as **editable YAML**. The graph then **interrupts** so the recruiter can approve
  or edit it before any candidate is scored. This is the main lever for consistency.
  (Today the review is editing the YAML or the profile in the web app before a run; the
  LangGraph interrupt is not built.)

### 3.3 Structured extraction (`cvscore.extract`) ✅
- Runs as a chain (`prompt | llm.with_structured_output(CandidateProfile)`), not an agent,
  because it's a single, well-defined transformation.
- Produces a **`CandidateProfile`**:
  - `experience[]`: title, organisation, dates, seniority, responsibilities, tools, domain
  - `achievements[]`: statement, metric, scope, linked role
  - `skills[]`, `education[]`, `certifications[]`, `languages[]`
  - `trait_evidence[]`
- Every item carries a **source quote + document reference**.
- Deterministic post-processing: date normalisation, total and recency-weighted years,
  and skill normalisation.
- Documents are embedded (`AzureOpenAIEmbeddings`) into a **per-run in-memory vector
  store** (FAISS / Chroma) that the evidence agent uses for retrieval. (Not built: there is
  no evidence agent yet. Screening embeds CV passages with an on-disk cache instead.)

### 3.4 Redaction / bias control (`cvscore.redact`) ✅ (rules)
- Runs **before scoring**. It masks name, gender markers, age / date of birth,
  nationality, photo, address and marital status, and the identity mapping is kept outside
  the graph state that scorers see.
  - **Built (rules):** contact blocks (name banner, header/footer, contact lines), emails,
    phones, profile URLs, the candidate's names wherever they appear, and lines such as
    date of birth, nationality or marital status. Images are never passed to scoring.
  - **Not built:** NER (Presidio) for free text, gendered pronouns in letters and
    references, and age inferred from graduation years.
- Removed text is replaced by visible markers (`[REDACTED: name]`,
  `[REDACTED: contact details]`, ...), so reviewers and models see that something was
  there.
- The same masking is applied to Langfuse traces (Langfuse `mask` function), so PII is
  not stored in the observability backend.

### 3.5a Screening (`cvscore.screen`) ✅
A cheap first ranking without a chat model reading the CVs: each screening criterion's
terms are searched in the redacted documents (whole words; short terms such as `R` match
case-sensitively), plus semantic similarity to CV passages when an embeddings model is
configured (capped below a keyword match). A `required` criterion with no match screens the
CV out (listed last, with the reason). Used alone (`cvscore screen`, "Quick screen"), or to
pick the best N for in-depth assessment (`run --shortlist N`).

### 3.5 Candidate assessment subgraph (`cvscore.assess`) — partly built
**Built:** a LangGraph fan-out per candidate (redact → extract → one scoring call →
quote verification). The scoring call returns many small judgements: an evidence level
(none / mentioned / applied / proven) per requirement and per key skill, judged with each
item's match mode (exact or equivalent); relevance and seniority 1-5 plus the relevant
roles, whose years and recency are computed from their dates; achievements 1-5. Python
turns levels into credit against the level each item asks for (`evidence`, job default
`applied`), counts nice-to-haves, shrinks unsure scores towards the middle, and uses where
a skill's name occurs (work history or a skills list) as a deterministic floor. The best N
are scored a second time and averaged (`ranking.repeat_top`). **Not built:** the
embedding-based baseline scorers and the evidence agent (step 3).

For each candidate, run in parallel with the LangGraph `Send` API and concurrency limits:

1. **Baseline scorers (no LLM, cheap, deterministic)**
   - Skills match: exact/normalised match + embedding similarity
   - Experience relevance: embedding similarity of roles to responsibilities × recency decay × duration, plus seniority fit
   - Rule-based must-have checks (years, degrees, certifications)
2. **Rubric scorers (LLM, structured output, temperature 0)**
   - Achievements: anchored 1–5 scale for relevance, impact, quantification, ownership
   - Personal characteristics: per trait, **evidence-required**. No quote means
     `insufficient_evidence` (neutral), not a low score.
   - Must-have verification for items rules couldn't settle → `pass | fail | unclear`
3. **Evidence agent (agentic loop):** triggered only when something is `unclear`, confidence
   is low, or the scorers disagree. It has a bounded tool budget (max N steps) and these tools:
   - `search_candidate_docs(query)`: retrieval over that candidate's documents
   - `get_section(doc, section)`: read a specific CV section or a form answer
   - `check_timeline()`: detect overlaps and gaps, and compute tenure
   - `verify_claim(claim)`: check whether a claim is supported elsewhere in the documents
   - `rescore(dimension, evidence)`: submit a revised structured score with quotes

   It may only cite text that is in the candidate's documents. Quotes are validated by
   string match before they are accepted, which guards against hallucinated evidence.
4. Output: a **`ScoreCard`** with, per dimension, `score ∈ [0, 1]`, `confidence`,
   `rationale`, `evidence[]`, plus flags (knock-out failed, inconsistency, needs review).

### 3.6 Ranking & review (`cvscore.rank`) — aggregation built
**Built:** the weighted sum with knock-out handling (`ranking.knockout: flag | exclude`)
and flags. **Built:** tiers (totals within `ranking.tie_margin`, or within their spreads, are too close
to call) and the pairwise judge for same-tier neighbours among the best
`ranking.pairwise_top`, judged in both orders and applied only when both agree (adjacent
swaps rather than Bradley-Terry). **Not built:** the shortlist critic and the review
interrupt.

- **Aggregate:** a weighted sum in Python using the JobProfile weights, with knock-out
  handling (exclude or flag, configurable).
- **Pairwise judge (agentic, optional):** for the top-K and for near-ties, an LLM compares
  two anonymised ScoreCards and profiles directly. The results are combined with
  Bradley-Terry. Each pair is judged in both orders to cancel position bias.
- **Shortlist critic:** a reviewer agent checks the final list. It looks for ranking
  inversions that contradict the evidence, candidates over-rewarded for keyword stuffing,
  and missing must-haves. It can only *flag* problems for human review, not change scores.
- **HITL interrupt:** borderline, flagged and low-confidence candidates are surfaced for
  a recruiter decision before the report is finalised.

### 3.7 Reporting (`cvscore.report`) ✅ (MVP)
**Built:** `report.html`, `ranking.csv`, `results.json`, and `screening.html/.csv/.json`;
pseudonymous unless names are asked for. **Not built:** `.xlsx`, trace links (the
ScoreCard's `trace_id` is not filled), location columns.

- `ranking.csv` / `.xlsx`
- `report.html` (Jinja2): a ranked table and a per-candidate drill-down with evidence quotes and agent investigation notes
- `results.json`
- Every row links to its **Langfuse trace**, so any score can be traced back to the exact prompts, tool calls and model outputs.

### 3.8 Interfaces
- **CLI** (`typer`), built: `doctor`, `ingest`, `inspect` (what was read from one file),
  `job draft`, `screen`, `run` (`--shortlist`, `--ocr/--no-ocr`, `--no-cache`,
  `--reveal-names`) and `serve`. Planned: `cvscore resume <run-id>` (continue after a HITL
  interrupt) and `cvscore report out/`.
- **Python API:** `build_graph(config)` returns a compiled LangGraph, for use from notebooks and integrations.
- **Checkpointing:** SQLite checkpointer locally (Postgres later), so long runs can be resumed and HITL steps can pause for days.
- **Web app** ✅ (`cvscore serve`): FastAPI back end (`cvscore.web`) and React front end
  (`frontend/`): browse to a folder, check how files were grouped into candidates and fix
  document types, filter by location, upload the job ad and edit the drafted profile
  (drag requirements between must-haves and nice-to-haves), choose quick screen /
  screen-then-assess / assess everyone, follow progress, and read the results in foldable
  score segments with search, location filter, evidence, the redacted text each model saw
  and links to the original documents. State is kept in the browser; runs on disk.
  Results are read-only: recording decisions or overrides is not built.
- Optional later: LangGraph Studio for visual debugging.

## 4. Azure AI Foundry integration (`cvscore.llm`) ✅

Built as described, plus an **OpenAI API provider** (`provider: openai`, key from the
environment) and support for the Azure **v1 endpoint** (`.../openai/v1`) besides the
classic one. Reasoning models get no temperature unless one is set. Concurrency is a fixed
`max_concurrency`, not yet sized from the TPM quota.


- A **single factory** builds every model from config, so nodes never construct clients directly:
  - **Chat:** `langchain_openai.AzureChatOpenAI` against the Foundry project's Azure OpenAI
    deployment (`azure_endpoint`, `azure_deployment`, `api_version`). An alternative is
    `langchain-azure-ai`'s `AzureAIChatCompletionsModel` if you want the Foundry model
    inference endpoint, for example to use non-OpenAI models from the same catalogue.
  - **Embeddings:** `AzureOpenAIEmbeddings` (e.g. a `text-embedding-3-large` deployment).
- **Auth:** **Entra ID only** (decided), via `azure-identity` (`DefaultAzureCredential` +
  `get_bearer_token_provider`): `az login` locally, managed identity in Azure, or a
  service principal through `AZURE_*` env vars. No API keys. Settings come from
  `cvscore.yaml` plus `CVSCORE_*` env overrides via `pydantic-settings`.
- **Model roles**, each mapped to a deployment name in `cvscore.yaml` (decided). Roles without their own deployment reuse the `scorer` deployment:
  - `extractor`: a cheaper or faster model, high volume
  - `scorer`
  - `agent`: tool calling
  - `judge`: the strongest model, used for pairwise (built) and critic work
- **Reliability:** retries with backoff on 429s, a concurrency semaphore sized to the
  deployment's TPM quota, request timeouts, and a disk/SQLite LLM cache keyed by
  (prompt version, model, input hash) so re-runs are cheap.
- **Data residency:** CV data stays in your Azure tenant and region. Abuse-monitoring and
  content-logging settings should be reviewed for PII, and an opt-out applied for if needed.

## 5. Langfuse observability (`cvscore.observability`) — tracing built

**Built:** tracing with the LangChain callback handler, PII masking of traces, run and
candidate tags, and prompt versions recorded in each ScoreCard (prompts live in the repo).
**Not built:** prompts managed in Langfuse, scores and recruiter overrides logged as
Langfuse scores, datasets and experiments.


- **Tracing:** pass `langfuse.langchain.CallbackHandler` into the graph's `config`
  (`callbacks=[handler]`). Each run is one Langfuse **session** and each candidate
  assessment is a **trace**, tagged with `job_id`, `candidate_id` (pseudonymous),
  `run_id` and config hash. LLM calls, tool calls, token usage, cost and latency are
  captured automatically, including for the Azure OpenAI models.
- **Prompt management:** every prompt is stored in Langfuse with versions and labels
  (`production`, `staging`) and fetched at runtime (`langfuse.get_prompt(...)
  .get_langchain_prompt()`), with a local fallback copy in the repo. The prompt version
  is recorded in each ScoreCard for reproducibility.
- **Scores:** each dimension score and the final rank are logged as Langfuse **scores** on
  the trace. Recruiter overrides from the HITL step are logged as human-annotation scores,
  which gives a built-in feedback dataset.
- **Datasets & experiments:** the synthetic and historical evaluation sets are stored as
  Langfuse datasets. Every prompt or model change is run as an experiment and compared
  there (see §7).
- **Privacy:** **self-host Langfuse** (Docker / Azure Container Apps) or use an EU-region
  cloud. Redaction and the mask function keep raw PII out of traces.

## 6. Repository layout (as proposed; the actual layout is flatter)

Built so far: `config.py`, `models.py`, `llm/` (factory, structured calls with cache,
embeddings), `observability/`, `ingest/` (extractors, OCR, cache, discovery, forms),
`job.py`, `extract.py`, `redact.py`, `pii.py`, `screen.py`, `score.py`, `evidence.py`
(quote verification), `rank.py`, `graph.py`, `service.py`, `report.py`, `location.py`,
`web/`, `cli.py`; `frontend/`; `tests/` (offline, with a fake model) and `tests/live/`.
The proposal:

```
cvscore/
  __init__.py
  config.py            # pydantic-settings: Azure, Langfuse, model roles, weights, limits
  models.py            # Pydantic: JobProfile, CandidateDocuments, CandidateProfile, ScoreCard, RunResult
  llm/                 # Azure model factory, auth, cache, rate limiting
  observability/       # Langfuse handler, masking, score logging, prompt registry
  prompts/             # local fallback copies of Langfuse prompts
  ingest/              # loaders, form adapters, candidate grouping
  job/                 # JobProfiler agent + tools
  extract/             # extraction chain + post-processing + per-candidate vector store
  redact/
  assess/
    baseline.py        # rule + embedding scorers
    rubric.py          # LLM rubric scorers
    evidence_agent.py  # agentic investigation loop + tools
    subgraph.py
  rank/                # aggregation, pairwise judge, shortlist critic
  graph.py             # top-level LangGraph: state, nodes, edges, interrupts, checkpointer
  report/              # csv/xlsx/html/json + templates
  cli.py
tests/
  fixtures/            # synthetic CVs, cover letters, forms, job descriptions
  unit/  integration/  # integration tests use recorded / fake LLM responses
evals/                 # Langfuse dataset sync + experiment runners
examples/
docs/
pyproject.toml         # uv, ruff, mypy, pytest
.env.example
```

**Key dependencies:** `langchain-core`, `langchain-openai`, `langchain-community`,
`langgraph`, `langgraph-checkpoint-sqlite`, `langchain-azure-ai` (optional), `azure-identity`,
`langfuse`, `pydantic`, `pydantic-settings`, `faiss-cpu` or `chromadb`, `pypdf`,
`docx2txt`, `presidio-analyzer`, `typer`, `rich`, `jinja2`, `pandas`, `openpyxl`.

## 7. Evaluation & quality

- **Synthetic fixture set:** about 20–30 fake candidates per job with known tiers, stored as a Langfuse dataset.
- **Agreement with humans:** Spearman/Kendall correlation versus recruiter rankings, and precision@K for the shortlist.
- **Consistency:** score variance across repeated runs and across file formats of the same CV.
- **Bias probes:** counterfactual tests that swap names, genders, ages or schools in otherwise
  identical CVs. The ranking must not move, and this is tracked as an experiment metric.
- **Agent-specific checks:**
  - evidence-quote validity rate (every quote must exist in the source)
  - average number of tool steps
  - how often the agent changes scores, and whether those changes agree with humans
- **Offline tests (built):** the LLM is swapped for a fake structured model, so CI runs
  without Azure. A gated job (`tests/live`, manual or `live-test` label) runs the whole
  pipeline against a real OpenAI model on fictional candidates and checks ranking order,
  verified quotes and that no names, emails or phones reach any prompt.
- **Evaluation set (built):** `evals/cloud-architect` holds 10 fictional candidates with
  expected tiers, controlled pairs (Terraform used in work vs only listed, equivalent IaC,
  keyword stuffing) and a renamed duplicate; `cvscore eval` runs it N times without the
  cache and reports tier-order accuracy, Kendall's tau, the pairs, and the spread between
  runs. An offline test runs it with a rule-based fake model; the live CI job runs it
  against the real model.
- **Not built:** Langfuse datasets and experiments, agreement with recruiter rankings,
  format invariance, broader bias probes (gender, age, school), agent checks.

## 8. Legal / ethical guardrails (important)

Automated CV ranking is regulated. It is **high-risk under the EU AI Act** (Annex III,
employment), GDPR Art. 22 restricts solely-automated decisions, and NYC Local Law 144
requires bias audits. The design therefore assumes:
- **Decision support, not decision making:** the output is a ranked shortlist with
  evidence, with mandatory human review steps (built in via LangGraph interrupts).
- Redaction before scoring, and personal characteristics only when evidenced and job-relevant.
- Explainability: every score has quotes plus a Langfuse trace of exactly how it was produced.
- Data minimisation: data stays in the Azure tenant, Langfuse is self-hosted, and retention is configurable.

## 9. Milestones

| # | Milestone | Deliverable |
|---|---|---|
| M0 | Scaffolding ✅ | `pyproject`, package skeleton, models, config, CI, synthetic fixtures |
| M1 | Platform ✅ | Azure model factory (Entra ID, v1 and classic endpoints), OpenAI provider, Langfuse tracing + masking, `cvscore doctor` |
| M2 | Ingestion ✅ | PDF (column-aware), DOCX (text boxes, layout tables, headers), TXT/MD, .doc/.rtf/.odt via LibreOffice, images; content-based document types and candidate grouping; generic form adapters; vision OCR fallback; extraction cache; `cvscore ingest`, `cvscore inspect` |
| M3 | Job profile ✅ (MVP) | `cvscore job draft` → editable `job.yaml` with screening criteria (one structured call, cached). Tool-using JobProfiler agent + LangGraph interrupt still to do |
| M4 | Extraction ✅ | CandidateProfile with verified quotes, computed years of experience, on-disk LLM cache |
| M5 | Baseline graph ✅ | LangGraph fan-out per candidate: redact → extract → rubric scoring (one call) → plain-Python ranking; `cvscore run`. Embedding-based scorers not built (lexical skill coverage instead) |
| M5a | Screening ✅ | Keyword + semantic screening against the profile's criteria, required gate, `cvscore screen`, `run --shortlist N` |
| M6 | Agentic layer | Evidence agent + tools (quote validation and rule-based redaction are built) |
| M7 | Ranking agents & review | Pairwise judge for near-ties ✅; shortlist critic, review interrupt, recording decisions/overrides in the web app still to do |
| M8 | Reporting ✅ (MVP) | `report.html` (evidence per score, pseudonymous by default), `ranking.csv`, `results.json`, screening reports. Still to do: `.xlsx`, trace links, location columns |
| M9 | Evaluation (partly ✅) | Evaluation set + `cvscore eval` (tiers, controlled pairs, name invariance, spread between runs) ✅; Langfuse datasets/experiments and broader bias probes still to do |
| M10 | Web app ✅ (MVP) | `cvscore serve`: folder picker, grouping review, location filter (configurable home country), profile editor, screening/assessment modes, foldable results, links to originals |
| M11 | Extras | Specific form/ATS adapters, data retention and run deletion, NER redaction |

## 10. Open questions

1. **Volume & languages:** ~~how many candidates per role?~~ About 200–300; documents in
   English and Norwegian (other languages are read, keyword lists cover EN/DE/NO).
2. **Azure:** ~~Entra ID or API keys?~~ Entra ID; deployment names go in `cvscore.yaml`
   (the OpenAI API is supported too). Still open: TPM quotas, to size concurrency.
3. **Langfuse:** self-hosted or Langfuse Cloud (which region)?
4. **Application forms:** what produces them (an ATS, Microsoft Forms, a custom web form)? Any samples?
5. **Personal characteristics:** which traits matter, and should they affect the score or only be shown?
6. **Review flow:** ~~CLI or UI?~~ A web app exists; still open: what recruiters need to
   record there (decisions, overrides, notes) and whether that feeds Langfuse.
7. **Ground truth:** are there past hiring decisions or recruiter rankings to evaluate against?
8. **Knock-outs:** ~~exclude or flag?~~ Configurable (`ranking.knockout`); default flag and
   rank last.
9. **Data retention:** how long may runs and caches (full CV text) be kept, and who deletes
   them?

## 11. Status: what is built and what is not

**Built:** ingestion (layout-aware PDF/DOCX, legacy formats, images, OCR fallback,
content-based grouping, forms, cache), job profile drafting with screening criteria,
keyword/semantic screening with a required gate and shortlist, rule-based redaction,
structured extraction with verified quotes, decomposed scoring with evidence levels and
match modes, nice-to-haves counted, computed experience duration and recency, repeated
readings with spread, tiers and a pairwise judge for near-ties, plain-Python ranking with
knock-outs and flags, an evaluation set with `cvscore eval`, HTML/CSV/JSON reports, Langfuse tracing with masking, Azure
(Entra ID) and OpenAI providers, location detection with a configurable home country, the
CLI, and the web app.

**Not built, in suggested order:**
1. **Human review loop:** record decisions, overrides and notes in the web app; log them
   as Langfuse scores; a LangGraph interrupt/resume for the CLI.
2. **Evaluation depth:** more evaluation sets, Langfuse datasets and experiments,
   broader counterfactual bias probes, format invariance, agreement with recruiter
   rankings.
3. **Evidence agent** for unclear must-haves and low-confidence scores.
4. **Shortlist critic.**
5. **Langfuse depth:** managed prompts, scores, trace links in the report.
6. **Redaction depth:** NER, pronouns, age from graduation years.
7. **Data retention:** a retention setting and deleting runs and caches.
8. **Smaller items:** tool-using JobProfiler agent, embedding-based experience scorer,
   `.xlsx` export, location in the CSV/HTML reports, ATS adapters, TPM-based concurrency.
