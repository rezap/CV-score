import { useState } from "react";
import { api, Dimension, JobProfile, Requirement, ScreeningCriterion } from "./api";

const DIMENSIONS: { key: Dimension; label: string }[] = [
  { key: "experience", label: "Experience" },
  { key: "achievements", label: "Achievements" },
  { key: "skills", label: "Skills" },
  { key: "requirements", label: "Requirements" },
  { key: "traits", label: "Traits" },
];

/** Upload a job ad (or a saved job.yaml) and get a draft hiring profile. */
export function JobInput({ onJob }: { onJob: (job: JobProfile) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (input: File | string) => {
    setBusy(true);
    setError(null);
    api
      .job(input)
      .then((r) => onJob(r.profile))
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="job-input">
      <label className="upload">
        <input
          type="file"
          accept=".pdf,.docx,.doc,.odt,.rtf,.txt,.md,.yaml,.yml,.json"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) submit(file);
            e.target.value = "";
          }}
        />
        <span>Upload the job ad</span>
        <span className="muted small">PDF, Word or text; or a job.yaml saved earlier</span>
      </label>
      <div className="or muted">or paste it</div>
      <textarea
        rows={6}
        placeholder="Paste the job description here"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
      />
      <div className="row between">
        <span className="muted small">The model drafts a hiring profile for you to review.</span>
        <button className="primary" disabled={busy || !text.trim()} onClick={() => submit(text)}>
          {busy ? "Drafting..." : "Draft profile"}
        </button>
      </div>
      {busy && <p className="muted">Reading the ad and drafting the profile...</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** Review and edit the hiring profile before ranking. */
export function JobEditor({ job, onChange }: { job: JobProfile; onChange: (j: JobProfile) => void }) {
  const set = (patch: Partial<JobProfile>) => onChange({ ...job, ...patch });
  const weightSum = DIMENSIONS.reduce((s, d) => s + (job.weights[d.key] ?? 0), 0);

  return (
    <div className="job-editor">
      <div className="grid2">
        <label>
          Title
          <input value={job.title} onChange={(e) => set({ title: e.target.value })} />
        </label>
        <label>
          Seniority
          <input
            value={job.seniority ?? ""}
            onChange={(e) => set({ seniority: e.target.value || null })}
          />
        </label>
      </div>
      <label>
        Summary
        <textarea rows={2} value={job.summary} onChange={(e) => set({ summary: e.target.value })} />
      </label>

      <RequirementList
        title="Must-haves"
        hint="A clear fail flags the candidate and ranks them last."
        prefix="M"
        items={job.must_have}
        onChange={(must_have) => set({ must_have })}
      />
      <RequirementList
        title="Nice-to-haves"
        hint="Weight = importance within this list."
        prefix="N"
        items={job.nice_to_have}
        withWeight
        onChange={(nice_to_have) => set({ nice_to_have })}
      />

      <SkillsInput key={job.id} skills={job.key_skills} onChange={(key_skills) => set({ key_skills })} />

      {job.traits.length > 0 && (
        <fieldset>
          <legend>Personal characteristics</legend>
          <p className="muted small">
            Assessed from evidence in the documents. They only change the ranking if the Traits
            weight below is above 0.
          </p>
          {job.traits.map((t, i) => (
            <div className="row" key={t.id}>
              <span className="grow" title={t.definition}>
                {t.name} <span className="muted small">{t.definition}</span>
              </span>
              <input
                type="number"
                className="num"
                min={0}
                step={0.5}
                value={t.weight}
                onChange={(e) => {
                  const traits = [...job.traits];
                  traits[i] = { ...t, weight: Number(e.target.value) };
                  set({ traits });
                }}
              />
              <button
                className="link"
                onClick={() => set({ traits: job.traits.filter((_, j) => j !== i) })}
              >
                remove
              </button>
            </div>
          ))}
        </fieldset>
      )}

      <ScreeningEditor
        key={`screening-${job.id}`}
        criteria={job.screening ?? []}
        onChange={(screening) => set({ screening })}
      />

      <fieldset>
        <legend>Score weights</legend>
        <div className="weights">
          {DIMENSIONS.map((d) => (
            <label key={d.key}>
              {d.label}
              <input
                type="number"
                className="num"
                min={0}
                step={0.05}
                value={job.weights[d.key] ?? 0}
                onChange={(e) =>
                  set({ weights: { ...job.weights, [d.key]: Number(e.target.value) } })
                }
              />
            </label>
          ))}
        </div>
        <p className="muted small">
          Shares of the total score (sum {weightSum.toFixed(2)}; they are normalised).
        </p>
      </fieldset>
    </div>
  );
}

function RequirementList(props: {
  title: string;
  hint: string;
  prefix: string;
  items: Requirement[];
  withWeight?: boolean;
  onChange: (items: Requirement[]) => void;
}) {
  const { items, onChange } = props;
  const update = (i: number, patch: Partial<Requirement>) => {
    const next = [...items];
    next[i] = { ...items[i], ...patch };
    onChange(next);
  };
  const nextId = () => {
    const used = new Set(items.map((r) => r.id));
    let n = items.length + 1;
    while (used.has(`${props.prefix}${n}`)) n++;
    return `${props.prefix}${n}`;
  };
  return (
    <fieldset>
      <legend>{props.title}</legend>
      <p className="muted small">{props.hint}</p>
      {items.map((r, i) => (
        <div className="row" key={r.id}>
          <span className="muted small mono">{r.id}</span>
          <input
            className="grow"
            value={r.description}
            onChange={(e) => update(i, { description: e.target.value })}
          />
          {props.withWeight && (
            <input
              type="number"
              className="num"
              min={0}
              step={0.5}
              value={r.weight}
              title="Weight"
              onChange={(e) => update(i, { weight: Number(e.target.value) })}
            />
          )}
          <button className="link" onClick={() => onChange(items.filter((_, j) => j !== i))}>
            remove
          </button>
        </div>
      ))}
      <button
        className="link"
        onClick={() => onChange([...items, { id: nextId(), description: "", weight: 1 }])}
      >
        + add
      </button>
    </fieldset>
  );
}

function SkillsInput({ skills, onChange }: { skills: string[]; onChange: (s: string[]) => void }) {
  const [raw, setRaw] = useState(skills.join(", "));
  return (
    <label>
      Key skills <span className="muted small">(comma separated)</span>
      <input
        value={raw}
        onChange={(e) => {
          setRaw(e.target.value);
          onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean));
        }}
      />
    </label>
  );
}

/** The fast screen's criteria: terms searched in the CVs, without a model reading them. */
function ScreeningEditor({
  criteria,
  onChange,
}: {
  criteria: ScreeningCriterion[];
  onChange: (c: ScreeningCriterion[]) => void;
}) {
  // Terms are edited as text so commas can be typed; parsed on every change.
  const [raw, setRaw] = useState(criteria.map((c) => c.terms.join(", ")));
  const update = (i: number, patch: Partial<ScreeningCriterion>) => {
    const next = [...criteria];
    next[i] = { ...criteria[i], ...patch };
    onChange(next);
  };
  const nextId = () => {
    const used = new Set(criteria.map((c) => c.id));
    let n = criteria.length + 1;
    while (used.has(`S${n}`)) n++;
    return `S${n}`;
  };
  return (
    <fieldset>
      <legend>Screening criteria</legend>
      <p className="muted small">
        Searched in the CVs for a fast, cheap first ranking: any of the terms counts (whole
        words, any case; one- and two-letter terms such as R match exactly). A required
        criterion with no match screens the CV out. Empty list: the key skills are used.
      </p>
      {criteria.map((c, i) => (
        <div className="criterion" key={c.id}>
          <div className="row">
            <span className="muted small mono">{c.id}</span>
            <input
              className="grow"
              value={c.name}
              placeholder="Competence"
              onChange={(e) => update(i, { name: e.target.value })}
            />
            <label className="small inline">
              <input
                type="checkbox"
                checked={c.required}
                onChange={(e) => update(i, { required: e.target.checked })}
              />{" "}
              required
            </label>
            <input
              type="number"
              className="num"
              min={0}
              step={0.5}
              value={c.weight}
              title="Weight"
              onChange={(e) => update(i, { weight: Number(e.target.value) })}
            />
            <button
              className="link"
              onClick={() => {
                onChange(criteria.filter((_, j) => j !== i));
                setRaw(raw.filter((_, j) => j !== i));
              }}
            >
              remove
            </button>
          </div>
          <input
            className="terms"
            value={raw[i] ?? ""}
            placeholder="Terms, comma separated: Python, R, pandas, statistikk"
            onChange={(e) => {
              const copy = [...raw];
              copy[i] = e.target.value;
              setRaw(copy);
              update(i, { terms: e.target.value.split(",").map((t) => t.trim()).filter(Boolean) });
            }}
          />
        </div>
      ))}
      <button
        className="link"
        onClick={() => {
          onChange([...criteria, { id: nextId(), name: "", terms: [], required: false, weight: 1 }]);
          setRaw([...raw, ""]);
        }}
      >
        + add
      </button>
    </fieldset>
  );
}
