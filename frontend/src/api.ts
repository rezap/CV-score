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
  home_country: string; // ISO code, e.g. "NO"
  home_name: string; // e.g. "Norway"
  home_in_eu: boolean;
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

// "home" is the home country set in the config (location.home_country).
export type Region = "home" | "eu" | "europe" | "world";
export type RegionKey = Region | "unknown";

export const REGION_KEYS: RegionKey[] = ["home", "eu", "europe", "world", "unknown"];

export interface Home {
  code: string; // ISO code, e.g. "NO"
  name: string; // e.g. "Norway"
  inEU: boolean;
}

export const DEFAULT_HOME: Home = { code: "NO", name: "Norway", inEU: false };

/** The location filters, labelled for the home country ("Norway", "Germany", ...). */
export function regionList(home: Home): { key: RegionKey; label: string }[] {
  return [
    { key: "home", label: home.name },
    { key: "eu", label: home.inEU ? "Rest of the EU" : "EU" },
    { key: "europe", label: "Europe (non-EU)" },
    { key: "world", label: "Rest of the world" },
    { key: "unknown", label: "Unknown" },
  ];
}

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

export const regionOf = (c: CandidateSummary | undefined): RegionKey => {
  const region = c?.location?.region as string | null | undefined;
  // Runs made before the home country was configurable called it "norway".
  return region === "norway" ? "home" : ((region as RegionKey | null | undefined) ?? "unknown");
};

/** How a requirement or skill must show in the documents to count in full. */
export type EvidenceLevel = "mentioned" | "applied" | "proven";
export type MatchMode = "exact" | "equivalent";
export type ObservedLevel = "none" | EvidenceLevel;

export const EVIDENCE_LEVELS: { value: EvidenceLevel; label: string; hint: string }[] = [
  { value: "mentioned", label: "Listed", hint: "Named anywhere, a skills list is enough" },
  { value: "applied", label: "Used in work", hint: "Used in a described role or project" },
  { value: "proven", label: "Proven", hint: "Applied with ownership, scale or results" },
];

export const MATCH_MODES: { value: MatchMode; label: string; hint: string }[] = [
  { value: "equivalent", label: "or equivalent", hint: "Comparable alternatives count too" },
  { value: "exact", label: "exactly this", hint: "Only what is named counts" },
];

export interface Requirement {
  id: string;
  description: string;
  weight: number;
  evidence?: EvidenceLevel | null; // null: the job's default
  match?: MatchMode | null;
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
  evidence?: EvidenceLevel | null;
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
  listed_only?: string[]; // matched terms found only in a skills list or summary
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
  evidence_default?: EvidenceLevel;
  match_default?: MatchMode;
  target_years?: number | null;
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
  status: "pass" | "partial" | "fail" | "unclear";
  rationale: string;
  evidence: Evidence[];
  required_level?: EvidenceLevel | null;
  observed_level?: ObservedLevel | null;
  value?: number | null;
}

export interface SkillCheck {
  skill: string;
  required_level: EvidenceLevel;
  observed_level: ObservedLevel;
  value: number;
  found_in: string[];
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
  skill_checks?: SkillCheck[];
  scores: DimensionScore[];
  trait_scores: TraitScore[];
  features: Record<string, number | null>;
  total: number | null;
  rank: number | null;
  spread?: number | null; // difference between two readings
  tier?: number | null; // candidates too close to call share a tier
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

/** The config file's settings (cvscore.yaml), as the server reads them. */
export type SettingsData = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface SettingsInfo {
  path: string;
  exists: boolean;
  problem: string | null;
  settings: SettingsData;
  env_overrides: string[];
  secrets: Record<string, boolean>;
  backup?: string | null;
}

export const api = {
  status: () => request<Status>("/api/status"),
  settings: () => request<SettingsInfo>("/api/settings"),
  saveSettings: (settings: SettingsData) =>
    request<SettingsInfo>("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
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
