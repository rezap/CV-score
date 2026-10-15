// Types and calls for the cvscore API (cvscore/web/app.py).

export type DocType = "cv" | "cover_letter" | "application_form" | "other";

export const DOC_TYPES: { value: DocType; label: string }[] = [
  { value: "cv", label: "CV" },
  { value: "cover_letter", label: "Cover letter" },
  { value: "application_form", label: "Application form" },
  { value: "other", label: "Other" },
];

export interface Status {
  ready: boolean;
  problems: string[];
  provider?: string;
  models?: Record<string, string | null>;
  langfuse?: boolean;
  browse_root: string;
}

export interface FolderListing {
  path: string;
  parent: string | null;
  root: string;
  folders: { name: string; path: string; documents: number }[];
  documents: string[];
}

export interface FileSummary {
  path: string;
  doc_type: DocType;
  reason: string;
  names: string[];
  quality: number | null;
  warnings: string[];
  error: string | null;
}

export type Region = "norway" | "eu" | "europe" | "world";
export type RegionKey = Region | "unknown";

export const REGIONS: { key: RegionKey; label: string; short: string }[] = [
  { key: "norway", label: "Norway", short: "NO" },
  { key: "eu", label: "EU", short: "EU" },
  { key: "europe", label: "Europe (non-EU)", short: "Europe" },
  { key: "world", label: "Rest of the world", short: "World" },
  { key: "unknown", label: "Unknown", short: "?" },
];

/** Where the documents say the candidate lives (cvscore/location.py). */
export interface Location {
  region: Region | null;
  country: string | null;
  country_name: string | null;
  place: string | null;
  source: string | null;
  confident: boolean;
  relocation: boolean;
}

export interface CandidateSummary {
  id: string;
  label: string;
  folder: string | null;
  grouped_by: string[];
  warnings: string[];
  files: FileSummary[];
  location?: Location; // missing in runs made before locations were detected
}

export const regionOf = (c: CandidateSummary | undefined): RegionKey =>
  c?.location?.region ?? "unknown";

export interface Requirement {
  id: string;
  description: string;
  weight: number;
}

export interface Trait {
  id: string;
  name: string;
  definition: string;
  evidence_examples: string[];
  weight: number;
}

export interface ScreeningCriterion {
  id: string;
  name: string;
  terms: string[];
  required: boolean;
  weight: number;
}

export interface CriterionResult {
  id: string;
  name: string;
  required: boolean;
  weight: number;
  score: number;
  keyword_score: number;
  semantic_score: number | null;
  matched: string[];
  snippets: string[];
}

export interface ScreenResult {
  candidate_id: string;
  score: number | null;
  rank: number | null;
  screened_out: boolean;
  criteria: CriterionResult[];
  years_hint: number | null;
  notes: string[];
}

export type RunMode = "screen" | "full";

export type Dimension = "requirements" | "experience" | "skills" | "achievements" | "traits";

export interface JobProfile {
  id: string;
  title: string;
  seniority: string | null;
  domain: string | null;
  summary: string;
  responsibilities: string[];
  must_have: Requirement[];
  nice_to_have: Requirement[];
  key_skills: string[];
  traits: Trait[];
  weights: Record<Dimension, number>;
  screening: ScreeningCriterion[];
}

export interface Evidence {
  doc_type: DocType;
  quote: string;
  location: string | null;
  label: string | null;
}

export interface RequirementCheck {
  requirement_id: string;
  description: string;
  must_have: boolean;
  status: "pass" | "fail" | "unclear";
  rationale: string;
  evidence: Evidence[];
}

export interface DimensionScore {
  dimension: Dimension;
  score: number | null;
  confidence: number;
  rationale: string;
  evidence: Evidence[];
}

export interface TraitScore {
  trait_id: string;
  name: string;
  score: number | null;
  rationale: string;
  evidence: Evidence[];
}

export interface ScoreCard {
  candidate_id: string;
  requirement_checks: RequirementCheck[];
  scores: DimensionScore[];
  trait_scores: TraitScore[];
  features: Record<string, number | null>;
  total: number | null;
  rank: number | null;
  flags: string[];
  notes: string[];
}

export interface RunMeta {
  id: string;
  status: "reading" | "assessing" | "done" | "failed";
  mode: RunMode;
  shortlist: number | null;
  created: string;
  folder: string;
  job_title: string;
  total: number;
  done: number;
  error: string | null;
}

export interface RunData {
  meta: RunMeta;
  results: {
    job: JobProfile;
    candidates: { score_card: ScoreCard; profile: Record<string, unknown> }[];
  } | null;
  identity: Record<string, string>;
  candidates: CandidateSummary[];
  screening: { criteria: ScreeningCriterion[]; candidates: ScreenResult[] } | null;
  job: JobProfile | null; // the profile the run used, as edited
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // not JSON
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  status: () => request<Status>("/api/status"),
  browse: (path?: string) =>
    request<FolderListing>(`/api/fs${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  scan: (folder: string, overrides: Record<string, DocType>, exclude: string[]) =>
    request<{ folder: string; candidates: CandidateSummary[] }>(
      "/api/scan",
      json({ folder, overrides, exclude }),
    ),
  job: (input: File | string) => {
    const form = new FormData();
    if (typeof input === "string") form.append("text", input);
    else form.append("file", input);
    return request<{ profile: JobProfile; source: string }>("/api/job", {
      method: "POST",
      body: form,
    });
  },
  startRun: (
    folder: string,
    job: JobProfile,
    overrides: Record<string, DocType>,
    exclude: string[],
    mode: RunMode,
    shortlist: number | null,
  ) =>
    request<RunMeta>(
      "/api/runs",
      json({ folder, job, overrides, exclude, mode, shortlist: mode === "full" ? shortlist : null }),
    ),
  runs: () => request<RunMeta[]>("/api/runs"),
  run: (id: string) => request<RunData>(`/api/runs/${encodeURIComponent(id)}`),
  redacted: (id: string, candidate: string) =>
    request<Record<string, string>>(
      `/api/runs/${encodeURIComponent(id)}/redacted/${encodeURIComponent(candidate)}`,
    ),
  fileUrl: (id: string, name: string) => `/api/runs/${encodeURIComponent(id)}/files/${name}`,
  originalUrl: (id: string, candidate: string, file: string) =>
    `/api/runs/${encodeURIComponent(id)}/original/${encodeURIComponent(candidate)}` +
    `?file=${encodeURIComponent(file)}`,
};
