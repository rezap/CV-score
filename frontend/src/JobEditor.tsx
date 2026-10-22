import { useState } from "react";
import {
  api,
  Dimension,
  EVIDENCE_LEVELS,
  EvidenceLevel,
  JobProfile,
  MATCH_MODES,
  MatchMode,
  Requirement,
  ScreeningCriterion,
} from "./api";

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

      <EvidenceDefaults job={job} onChange={set} />

      <RequirementBoard
        must={job.must_have}
        nice={job.nice_to_have}
        evidenceDefault={job.evidence_default ?? "applied"}
        matchDefault={job.match_default ?? "equivalent"}
        onChange={(must_have, nice_to_have) => set({ must_have, nice_to_have })}
      />

      <SkillsInput skills={job.key_skills} onChange={(key_skills) => set({ key_skills })} />

      {job.traits.length > 0 && (
        <details className="fold">
          <summary>
            Personal characteristics <span className="count">{job.traits.length}</span>
          </summary>
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
        </details>
      )}

      <ScreeningEditor
        key={`screening-${job.id}`}
        criteria={job.screening ?? []}
        evidenceDefault={job.evidence_default ?? "applied"}
        onChange={(screening) => set({ screening })}
      />

      <details className="fold">
        <summary>Score weights</summary>
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
      </details>
    </div>
  );
}

const levelLabel = (v: EvidenceLevel) => EVIDENCE_LEVELS.find((l) => l.value === v)!.label;
const matchLabel = (v: MatchMode) => MATCH_MODES.find((m) => m.value === v)!.label;

/** How requirements, key skills and screening terms must appear to count in full, for
 * the whole job (each requirement can override it), and the years the role calls for. */
function EvidenceDefaults({
  job,
  onChange,
}: {
  job: JobProfile;
  onChange: (patch: Partial<JobProfile>) => void;
}) {
  const level = job.evidence_default ?? "applied";
  const match = job.match_default ?? "equivalent";
  return (
    <fieldset className="evidence-defaults">
      <legend>How evidence counts</legend>
      <div className="segmented" role="radiogroup" aria-label="Evidence needed">
        {EVIDENCE_LEVELS.map((l) => (
          <button
            key={l.value}
            role="radio"
            aria-checked={level === l.value}
            className={level === l.value ? "on" : ""}
            title={l.hint}
            onClick={() => onChange({ evidence_default: l.value })}
          >
            {l.label}
          </button>
        ))}
      </div>
      <p className="muted small">
        {EVIDENCE_LEVELS.find((l) => l.value === level)!.hint}. Below this level a requirement
        or key skill earns part of its credit (listed when use in work is asked for: half).
        Each requirement can set its own level.
      </p>
      <div className="row">
        <label className="inline small">
          Alternatives{" "}
          <select
            value={match}
            onChange={(e) => onChange({ match_default: e.target.value as MatchMode })}
          >
            {MATCH_MODES.map((m) => (
              <option key={m.value} value={m.value} title={m.hint}>
                {m.value === "equivalent" ? "count (Pulumi for Terraform)" : "do not count"}
              </option>
            ))}
          </select>
        </label>
        <label className="inline small">
          Relevant years asked for{" "}
          <input
            type="number"
            className="num"
            min={0}
            step={1}
            value={job.target_years ?? ""}
            placeholder="by seniority"
            onChange={(e) =>
              onChange({ target_years: e.target.value === "" ? null : Number(e.target.value) })
            }
          />
        </label>
      </div>
    </fieldset>
  );
}

type ListKey = "must" | "nice";

const LISTS: { key: ListKey; title: string; hint: string; prefix: string }[] = [
  {
    key: "must",
    title: "Must-haves",
    hint: "A clear fail flags the candidate and ranks them last.",
    prefix: "M",
  },
  {
    key: "nice",
    title: "Nice-to-haves",
    hint: "Weight = importance within this list.",
    prefix: "N",
  },
];

/** Must-haves and nice-to-haves side by side: drag a requirement to move or reorder it
 * (or use the arrow button), edit it in place. */
function RequirementBoard(props: {
  must: Requirement[];
  nice: Requirement[];
  evidenceDefault: EvidenceLevel;
  matchDefault: MatchMode;
  onChange: (must: Requirement[], nice: Requirement[]) => void;
}) {
  const [drag, setDrag] = useState<{ list: ListKey; index: number } | null>(null);
  const [over, setOver] = useState<{ list: ListKey; index: number } | null>(null);
  // Rows can be dragged only by their grip, so text in them can still be selected.
  const [armed, setArmed] = useState<string | null>(null);
  const lists: Record<ListKey, Requirement[]> = { must: props.must, nice: props.nice };

  const emit = (next: Record<ListKey, Requirement[]>) => props.onChange(next.must, next.nice);
  const freshId = (list: ListKey, taken: Requirement[]) => {
    const prefix = LISTS.find((l) => l.key === list)!.prefix;
    const used = new Set([...props.must, ...props.nice, ...taken].map((r) => r.id));
    let n = 1;
    while (used.has(`${prefix}${n}`)) n++;
    return `${prefix}${n}`;
  };
  const move = (from: ListKey, index: number, to: ListKey, at: number) => {
    const next = { must: [...props.must], nice: [...props.nice] };
    const [item] = next[from].splice(index, 1);
    if (!item) return;
    // Within a list, removing the item shifts the drop position.
    const position = from === to && at > index ? at - 1 : at;
    const moved = from === to ? item : { ...item, id: freshId(to, next[to]) };
    next[to].splice(Math.min(position, next[to].length), 0, moved);
    emit(next);
  };
  const update = (list: ListKey, i: number, patch: Partial<Requirement>) => {
    const next = { must: [...props.must], nice: [...props.nice] };
    next[list][i] = { ...next[list][i], ...patch };
    emit(next);
  };
  const remove = (list: ListKey, i: number) =>
    emit({ ...lists, [list]: lists[list].filter((_, j) => j !== i) });
  const add = (list: ListKey) =>
    emit({
      ...lists,
      [list]: [...lists[list], { id: freshId(list, []), description: "", weight: 1 }],
    });
  const drop = (list: ListKey, index: number) => {
    if (drag) move(drag.list, drag.index, list, index);
    setDrag(null);
    setOver(null);
  };

  return (
    <div className="req-board">
      {LISTS.map((l) => {
        const items = lists[l.key];
        const other: ListKey = l.key === "must" ? "nice" : "must";
        return (
          <fieldset
            key={l.key}
            className={`req-list ${drag && over?.list === l.key ? "drop-target" : ""}`}
            onDragOver={(e) => {
              if (!drag) return;
              e.preventDefault();
              if (over?.list !== l.key || over.index !== items.length)
                setOver({ list: l.key, index: items.length });
            }}
            onDrop={(e) => {
              e.preventDefault();
              drop(l.key, over?.list === l.key ? over.index : items.length);
            }}
          >
            <legend>
              {l.title} <span className="count">{items.length}</span>
            </legend>
            <p className="muted small">{l.hint}</p>
            {items.map((r, i) => (
              <div
                key={r.id}
                className={`req ${drag?.list === l.key && drag.index === i ? "dragging" : ""} ${
                  over?.list === l.key && over.index === i && drag ? "insert-before" : ""
                }`}
                draggable={armed === r.id}
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", r.description);
                  setDrag({ list: l.key, index: i });
                }}
                onDragEnd={() => {
                  setDrag(null);
                  setOver(null);
                  setArmed(null);
                }}
                onDragOver={(e) => {
                  if (!drag) return;
                  e.preventDefault();
                  e.stopPropagation();
                  const box = e.currentTarget.getBoundingClientRect();
                  const index = e.clientY > box.top + box.height / 2 ? i + 1 : i;
                  if (over?.list !== l.key || over.index !== index) setOver({ list: l.key, index });
                }}
              >
                <span
                  className="grip"
                  title="Drag to move"
                  aria-hidden="true"
                  onMouseDown={() => setArmed(r.id)}
                  onMouseUp={() => setArmed(null)}
                >
                  ⋮⋮
                </span>
                <span className="muted small mono req-id">{r.id}</span>
                <textarea
                  className="grow req-text"
                  rows={1}
                  value={r.description}
                  placeholder="Requirement"
                  onChange={(e) => update(l.key, i, { description: e.target.value })}
                />
                {l.key === "nice" && (
                  <input
                    type="number"
                    className="num"
                    min={0}
                    step={0.5}
                    value={r.weight}
                    title="Weight"
                    onChange={(e) => update(l.key, i, { weight: Number(e.target.value) })}
                  />
                )}
                <button
                  className="icon-btn"
                  title={`Move to ${other === "must" ? "must-haves" : "nice-to-haves"}`}
                  aria-label={`Move to ${other === "must" ? "must-haves" : "nice-to-haves"}`}
                  onClick={() => move(l.key, i, other, lists[other].length)}
                >
                  {other === "must" ? "←" : "→"}
                </button>
                <button
                  className="icon-btn"
                  title="Remove"
                  aria-label="Remove"
                  onClick={() => remove(l.key, i)}
                >
                  ×
                </button>
                <div className="req-opts">
                  <select
                    aria-label="Evidence needed"
                    title="How it must show in the CV to count in full"
                    className={r.evidence ? "set" : ""}
                    value={r.evidence ?? ""}
                    onChange={(e) =>
                      update(l.key, i, { evidence: (e.target.value || null) as EvidenceLevel | null })
                    }
                  >
                    <option value="">{levelLabel(props.evidenceDefault)} (default)</option>
                    {EVIDENCE_LEVELS.map((lv) => (
                      <option key={lv.value} value={lv.value} title={lv.hint}>
                        {lv.label}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label="Alternatives"
                    title="Whether comparable alternatives count"
                    className={r.match ? "set" : ""}
                    value={r.match ?? ""}
                    onChange={(e) =>
                      update(l.key, i, { match: (e.target.value || null) as MatchMode | null })
                    }
                  >
                    <option value="">{matchLabel(props.matchDefault)} (default)</option>
                    {MATCH_MODES.map((m) => (
                      <option key={m.value} value={m.value} title={m.hint}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ))}
            {drag && over?.list === l.key && over.index === items.length && (
              <div className="insert-end" />
            )}
            {items.length === 0 && <p className="muted small empty-drop">Drop requirements here</p>}
            <button className="link" onClick={() => add(l.key)}>
              + add
            </button>
          </fieldset>
        );
      })}
    </div>
  );
}

/** Key skills as chips: type and press Enter (or a comma) to add one, × to remove. */
function SkillsInput({ skills, onChange }: { skills: string[]; onChange: (s: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const commit = (raw: string) => {
    const added = raw.split(",").map((s) => s.trim()).filter((s) => s && !skills.includes(s));
    if (added.length) onChange([...skills, ...added]);
    setDraft("");
  };
  return (
    <div className="field">
      <span className="field-label">Key skills</span>
      <div className="chip-input">
        {skills.map((s) => (
          <span className="chip editable" key={s}>
            {s}
            <button
              aria-label={`Remove ${s}`}
              onClick={() => onChange(skills.filter((x) => x !== s))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={draft}
          placeholder={skills.length ? "Add a skill" : "Python, SQL, ..."}
          onChange={(e) => (e.target.value.includes(",") ? commit(e.target.value) : setDraft(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit(draft);
            } else if (e.key === "Backspace" && !draft && skills.length) {
              onChange(skills.slice(0, -1));
            }
          }}
          onBlur={() => draft.trim() && commit(draft)}
        />
      </div>
    </div>
  );
}

/** The fast screen's criteria: terms searched in the CVs, without a model reading them. */
function ScreeningEditor({
  criteria,
  evidenceDefault,
  onChange,
}: {
  criteria: ScreeningCriterion[];
  evidenceDefault: EvidenceLevel;
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
    <details className="fold" open>
      <summary>
        Screening criteria <span className="count">{criteria.length}</span>
      </summary>
      <p className="muted small">
        Searched in the CVs for a fast, cheap first ranking: any of the terms counts (whole
        words, any case; one- and two-letter terms such as R match exactly). Unless a
        criterion is set to "Listed", a term found only in a skills list or summary counts
        half. A required criterion with no match screens the CV out. Empty list: the key
        skills are used.
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
            <select
              aria-label="Evidence needed"
              title="Under 'Used in work' or 'Proven', terms found only in a skills list count half"
              className={c.evidence ? "set" : ""}
              value={c.evidence ?? ""}
              onChange={(e) =>
                update(i, { evidence: (e.target.value || null) as EvidenceLevel | null })
              }
            >
              <option value="">{levelLabel(evidenceDefault)} (default)</option>
              {EVIDENCE_LEVELS.map((lv) => (
                <option key={lv.value} value={lv.value}>
                  {lv.label}
                </option>
              ))}
            </select>
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
    </details>
  );
}
