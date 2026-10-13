# CV-score — Implementation Plan (proposal)

Status: **draft for review**. Nothing here is built yet; open questions are listed at the end.

**Stack:** Python · LangChain + **LangGraph** (agentic orchestration) · **Azure OpenAI
models via an Azure AI Foundry project** · **Langfuse** (tracing, prompt management, evaluation).

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
- **Grouping documents by candidate:** one folder per candidate, flat files named
  `<who>_cv.pdf`, or a `manifest.csv`. Document type comes from file-name keywords (EN/DE).
  Folder names become pseudonymous IDs; the mapping is written separately.
- **Application forms:** the structure is TBD, so they get generic adapters: JSON/YAML
  mappings or question/answer lists, single-candidate CSV, and multi-candidate CSV exports
  (`--forms --form-key`). Answers to personal-data questions are flagged as contact text.
  Specific adapters (ATS exports) can come later.
- **Not yet:** OCR for scanned PDFs. Azure AI Document Intelligence (`prebuilt-layout`) is
  the planned fallback for documents the quality check flags.

### 3.2 Job profile: `JobProfiler` agent (`cvscore.job`)
- A small ReAct-style agent with tools: `read_job_description`, `lookup_skill_taxonomy`
  (normalises skills and synonyms), and `propose_weights`.
- Produces a structured **`JobProfile`**:
  - `must_have` (knock-outs)
  - `nice_to_have` (weighted)
  - `responsibilities`, `seniority`, `domain`, `key_skills`
  - `traits`, each with a definition and examples of evidence
  - `weights` per dimension
- Saved as **editable YAML**. The graph then **interrupts** so the recruiter can approve
  or edit it before any candidate is scored. This is the main lever for consistency.

### 3.3 Structured extraction (`cvscore.extract`)
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
  store** (FAISS / Chroma) that the evidence agent uses for retrieval.

### 3.4 Redaction / bias control (`cvscore.redact`)
- Runs **before scoring**. It masks name, gender markers, age / date of birth,
  nationality, photo, address and marital status (rules + Presidio), and the identity
  mapping is kept outside the graph state that scorers see.
- Removed text is replaced by visible markers (`[REDACTED: name]`,
  `[REDACTED: contact details]`, ...), so reviewers and models see that something was
  there.
- The same masking is applied to Langfuse traces (Langfuse `mask` function), so PII is
  not stored in the observability backend.

### 3.5 Candidate assessment subgraph (`cvscore.assess`)
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

### 3.6 Ranking & review (`cvscore.rank`)
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

### 3.7 Reporting (`cvscore.report`)
- `ranking.csv` / `.xlsx`
- `report.html` (Jinja2): a ranked table and a per-candidate drill-down with evidence quotes and agent investigation notes
- `results.json`
- Every row links to its **Langfuse trace**, so any score can be traced back to the exact prompts, tool calls and model outputs.

### 3.8 Interfaces
- **CLI** (`typer`):
  - `cvscore job draft job.pdf -o job.yaml`
  - `cvscore run --job job.yaml --candidates candidates/ -o out/`
  - `cvscore resume <run-id>` continues after a HITL interrupt
  - `cvscore report out/`
- **Python API:** `build_graph(config)` returns a compiled LangGraph, for use from notebooks and integrations.
- **Checkpointing:** SQLite checkpointer locally (Postgres later), so long runs can be resumed and HITL steps can pause for days.
- **Web app** ✅ (`cvscore serve`): FastAPI back end (`cvscore.web`) and React front end
  (`frontend/`): browse to a folder, check how files were grouped into candidates, upload
  the job ad and edit the drafted profile, follow progress, read the ranking with
  evidence and the redacted text each model saw.
- Optional later: LangGraph Studio for visual debugging.

## 4. Azure AI Foundry integration (`cvscore.llm`)

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
  - `judge`: the strongest model, used for pairwise and critic work
- **Reliability:** retries with backoff on 429s, a concurrency semaphore sized to the
  deployment's TPM quota, request timeouts, and a disk/SQLite LLM cache keyed by
  (prompt version, model, input hash) so re-runs are cheap.
- **Data residency:** CV data stays in your Azure tenant and region. Abuse-monitoring and
  content-logging settings should be reviewed for PII, and an opt-out applied for if needed.

## 5. Langfuse observability (`cvscore.observability`)

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

## 6. Proposed repository layout

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
- **Offline tests:** the LLM is swapped for `FakeListChatModel` / recorded responses, so
  CI runs without Azure. A gated job runs live smoke tests against Azure.

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
| M1 | Platform ✅ | Azure model factory (Entra ID), Langfuse tracing + masking, `cvscore doctor`; prompt registry moves to M3 |
| M2 | Ingestion ✅ | PDF (column-aware), DOCX (text boxes, layout tables, headers), TXT/MD, .doc/.rtf/.odt via LibreOffice; candidate grouping; generic form adapters; `cvscore ingest` |
| M3 | Job profile ✅ (MVP) | `cvscore job draft` → editable `job.yaml` (structured-output chain; review = editing the YAML before `run`). Tool-using JobProfiler agent + LangGraph interrupt still to do |
| M4 | Extraction ✅ | CandidateProfile with verified quotes, computed years of experience, on-disk LLM cache |
| M5 | Baseline graph ✅ | LangGraph fan-out per candidate: redact → extract → rubric scoring (one call) → plain-Python ranking; `cvscore run`. Embedding-based scorers not built (lexical skill coverage instead) |
| M6 | Agentic layer | Evidence agent + tools + quote validation; redaction |
| M7 | Ranking agents | Pairwise judge, shortlist critic, review interrupt |
| M8 | Reporting ✅ (MVP) | `report.html` (evidence per score, pseudonymous by default), `ranking.csv`, `results.json` |
| M9 | Evaluation | Langfuse datasets/experiments, bias probes, consistency checks |
| M10 | Extras | Specific form adapters, Document Intelligence loader, review UI |

## 10. Open questions

1. **Volume & languages:** how many candidates per role, and are documents in languages other than English?
2. **Azure:** ~~Entra ID or API keys?~~ Entra ID; deployment names go in `cvscore.yaml`.
   Still open: TPM quotas, to size concurrency.
3. **Langfuse:** self-hosted or Langfuse Cloud (which region)?
4. **Application forms:** what produces them (an ATS, Microsoft Forms, a custom web form)? Any samples?
5. **Personal characteristics:** which traits matter, and should they affect the score or only be shown?
6. **Review flow:** is the CLI with resume enough for the HITL steps, or do recruiters need a UI?
7. **Ground truth:** are there past hiring decisions or recruiter rankings to evaluate against?
8. **Knock-outs:** should failing a must-have exclude a candidate, or only flag and down-rank them?
