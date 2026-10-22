// The settings panel: every cvscore.yaml setting, plus the look of the app.
import { ReactNode, useEffect, useState } from "react";
import { api, SettingsData, SettingsInfo } from "./api";
import { Theme } from "./ui";

const ROLES = [
  { key: "scorer", label: "Scorer", hint: "Scores candidates (other roles fall back to it)" },
  { key: "extractor", label: "Extractor", hint: "Reads CVs into profiles; OCR by default" },
  { key: "agent", label: "Agent", hint: "Drafts the job profile" },
  { key: "judge", label: "Judge", hint: "Compares near-tied candidates head to head" },
];

const EFFORTS = ["", "minimal", "low", "medium", "high"];

type Path = (string | number)[];

function getIn(obj: SettingsData, path: Path): any { // eslint-disable-line @typescript-eslint/no-explicit-any
  return path.reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function setIn(obj: SettingsData, path: Path, value: unknown): SettingsData {
  const copy = structuredClone(obj);
  let o = copy;
  for (const k of path.slice(0, -1)) {
    if (o[k] == null || typeof o[k] !== "object") o[k] = {};
    o = o[k];
  }
  o[path[path.length - 1]] = value;
  return copy;
}

export function SettingsPanel(props: {
  theme: Theme;
  onTheme: (t: Theme) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [info, setInfo] = useState<SettingsInfo | null>(null);
  const [draft, setDraft] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api.settings().then(
      (i) => {
        setInfo(i);
        setDraft(i.settings);
      },
      (e: Error) => setError(e.message),
    );
  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props]);

  const dirty = info && draft && JSON.stringify(info.settings) !== JSON.stringify(draft);
  const set = (path: Path, value: unknown) => {
    setSaved(null);
    setDraft((d) => (d ? setIn(d, path, value) : d));
  };
  const save = () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    api
      .saveSettings(draft)
      .then((i) => {
        setInfo(i);
        setDraft(i.settings);
        setSaved(i.backup ? `Saved. The previous file is in ${i.backup}.` : "Saved.");
        props.onSaved();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const provider: string = draft?.provider ?? "azure";
  const p = provider === "openai" ? "openai" : "azure";
  const nameKey = p === "openai" ? "model" : "deployment";

  return (
    <div className="drawer-backdrop" onClick={props.onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-label="Settings"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drawer-head">
          <strong>Settings</strong>
          <button className="icon-btn" aria-label="Close" onClick={props.onClose}>
            ×
          </button>
        </header>
        <div className="drawer-body">
          <Group title="Appearance" open>
            <div className="segmented" role="radiogroup" aria-label="Theme">
              {(["system", "light", "dark"] as Theme[]).map((t) => (
                <button
                  key={t}
                  role="radio"
                  aria-checked={props.theme === t}
                  className={props.theme === t ? "on" : ""}
                  onClick={() => props.onTheme(t)}
                >
                  {t === "system" ? "System" : t === "light" ? "Light" : "Dark"}
                </button>
              ))}
            </div>
            <p className="muted small">Kept in this browser. System follows your computer.</p>
          </Group>

          {!draft ? (
            <p className="muted">{error ?? "Loading settings..."}</p>
          ) : (
            <>
              <p className="muted small">
                Saved to <span className="mono">{info?.path}</span>
                {info && !info.exists && " (a new file)"}. Changes apply to the next run; no
                restart needed. API keys are never stored here: they come from the environment
                or <code>.env</code>.
              </p>
              {info?.problem && <p className="error small">The file has a problem: {info.problem}</p>}
              {info && info.env_overrides.length > 0 && (
                <p className="warning small">
                  Set in the environment, so they win over these settings:{" "}
                  {info.env_overrides.join(", ")}
                </p>
              )}

              <Group title="Models" open>
                <Field label="Provider">
                  <select value={provider} onChange={(e) => set(["provider"], e.target.value)}>
                    <option value="azure">Azure AI Foundry (Entra ID)</option>
                    <option value="openai">OpenAI API (key)</option>
                  </select>
                </Field>
                {p === "azure" ? (
                  <>
                    <Text label="Endpoint" path={["azure", "endpoint"]} draft={draft} set={set}
                      placeholder="https://<resource>.openai.azure.com/openai/v1" />
                    <Text label="API version (classic endpoint only)" path={["azure", "api_version"]}
                      draft={draft} set={set} />
                  </>
                ) : (
                  <>
                    <Text label="API key variable" path={["openai", "api_key_env"]} draft={draft} set={set} />
                    <Secret name={draft.openai?.api_key_env ?? "OPENAI_API_KEY"} info={info} />
                    <Text label="Base URL (optional)" path={["openai", "base_url"]} draft={draft} set={set}
                      nullable placeholder="https://api.openai.com/v1" />
                  </>
                )}
                <table className="roles">
                  <thead>
                    <tr>
                      <th>Role</th>
                      <th>{p === "openai" ? "Model" : "Deployment"}</th>
                      <th title="Blank for reasoning models, which take no temperature">Temp.</th>
                      <th>Reasoning</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ROLES.map((r) => {
                      const base = [p, "models", r.key];
                      const model = getIn(draft, base);
                      return (
                        <tr key={r.key} title={r.hint}>
                          <td>
                            {r.label}
                            <div className="muted small">{r.hint}</div>
                          </td>
                          <td>
                            <input
                              value={model?.[nameKey] ?? ""}
                              placeholder={r.key === "scorer" ? "required" : "as scorer"}
                              onChange={(e) =>
                                set(base, e.target.value
                                  ? { ...(model ?? {}), [nameKey]: e.target.value }
                                  : undefined)
                              }
                            />
                          </td>
                          <td>
                            <input
                              type="number"
                              className="num"
                              step={0.1}
                              min={0}
                              disabled={!model}
                              value={model?.temperature ?? ""}
                              onChange={(e) =>
                                set([...base, "temperature"], e.target.value === "" ? null : Number(e.target.value))
                              }
                            />
                          </td>
                          <td>
                            <select
                              disabled={!model}
                              value={model?.reasoning_effort ?? ""}
                              onChange={(e) => set([...base, "reasoning_effort"], e.target.value || null)}
                            >
                              {EFFORTS.map((x) => (
                                <option key={x} value={x}>
                                  {x || "—"}
                                </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <Field label={`Embeddings ${p === "openai" ? "model" : "deployment"} (semantic screening)`}>
                  <input
                    value={getIn(draft, [p, "embeddings", nameKey]) ?? ""}
                    placeholder="none: keywords only"
                    onChange={(e) =>
                      set([p, "embeddings"], e.target.value ? { [nameKey]: e.target.value } : null)
                    }
                  />
                </Field>
                <div className="row">
                  <Num label="Candidates at once" path={[p, "max_concurrency"]} draft={draft} set={set} min={1} />
                  <Num label="Retries" path={[p, "max_retries"]} draft={draft} set={set} min={0} />
                  <Num label="Timeout (s)" path={[p, "timeout_s"]} draft={draft} set={set} min={1} />
                </div>
              </Group>

              <Group title="Ranking">
                <Field label="A failed must-have">
                  <select
                    value={draft.ranking?.knockout ?? "flag"}
                    onChange={(e) => set(["ranking", "knockout"], e.target.value)}
                  >
                    <option value="flag">ranks the candidate last (score kept)</option>
                    <option value="exclude">removes the score (still listed)</option>
                  </select>
                </Field>
                <div className="row">
                  <Num label="Second readings of the best" path={["ranking", "repeat_top"]} draft={draft} set={set} min={0}
                    hint="0 = off" />
                  <Num label="Judge compares near-ties among the best" path={["ranking", "pairwise_top"]} draft={draft}
                    set={set} min={0} hint="0 = off" />
                </div>
                <div className="row">
                  <Num label="Tie margin" path={["ranking", "tie_margin"]} draft={draft} set={set} min={0} step={0.01}
                    hint="totals closer than this share a tier" />
                  <Num label="Low-confidence flag below" path={["ranking", "low_confidence"]} draft={draft} set={set}
                    min={0} step={0.05} />
                  <Num label="Flag after unverifiable quotes" path={["ranking", "max_dropped_quotes"]} draft={draft}
                    set={set} min={1} />
                </div>
              </Group>

              <Group title="Screening">
                <Check label="Semantic matching (needs an embeddings model)" path={["screening", "semantic"]}
                  draft={draft} set={set} />
                <div className="row">
                  <Num label="No match below similarity" path={["screening", "similarity_floor"]} draft={draft} set={set}
                    step={0.05} />
                  <Num label="Full match from" path={["screening", "similarity_full"]} draft={draft} set={set} step={0.05} />
                  <Num label="Required passes from" path={["screening", "required_semantic"]} draft={draft} set={set}
                    step={0.05} />
                </div>
              </Group>

              <Group title="Scanned CVs (OCR)">
                <Check label="Read scanned pages and images with the vision model" path={["ocr", "enabled"]}
                  draft={draft} set={set} />
                <div className="row">
                  <Field label="Model role">
                    <select value={draft.ocr?.role ?? "extractor"} onChange={(e) => set(["ocr", "role"], e.target.value)}>
                      {ROLES.map((r) => (
                        <option key={r.key} value={r.key}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Num label="DPI" path={["ocr", "dpi"]} draft={draft} set={set} min={72} />
                  <Num label="Pages per document" path={["ocr", "max_pages"]} draft={draft} set={set} min={1} />
                </div>
              </Group>

              <Group title="Location">
                <Text label="Home country (ISO code or name)" path={["location", "home_country"]} draft={draft} set={set}
                  placeholder="NO" />
              </Group>

              <Group title="Tracing (Langfuse)">
                <Check label="Trace runs to Langfuse" path={["langfuse", "enabled"]} draft={draft} set={set} />
                <Check label="Mask personal data in traces" path={["langfuse", "mask_pii"]} draft={draft} set={set} />
                <Text label="Host" path={["langfuse", "host"]} draft={draft} set={set} nullable
                  placeholder="from LANGFUSE_BASE_URL" />
                <Text label="Environment" path={["langfuse", "environment"]} draft={draft} set={set} nullable />
                <Secret name="LANGFUSE_PUBLIC_KEY" info={info} />
                <Secret name="LANGFUSE_SECRET_KEY" info={info} />
              </Group>
            </>
          )}
        </div>
        <footer className="drawer-foot">
          {error && <span className="error small">{error}</span>}
          {saved && !dirty && <span className="muted small">{saved}</span>}
          <span className="grow" />
          <button disabled={!dirty || busy} onClick={() => info && setDraft(info.settings)}>
            Discard
          </button>
          <button className="primary" disabled={!dirty || busy} onClick={save}>
            {busy ? "Saving..." : "Save settings"}
          </button>
        </footer>
      </aside>
    </div>
  );
}

function Group({ title, open, children }: { title: string; open?: boolean; children: ReactNode }) {
  return (
    <details className="fold" open={open}>
      <summary>{title}</summary>
      {children}
    </details>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="setting">
      <span className="setting-label">
        {label}
        {hint && <span className="muted small"> · {hint}</span>}
      </span>
      {children}
    </label>
  );
}

interface FieldProps {
  label: string;
  path: Path;
  draft: SettingsData;
  set: (path: Path, value: unknown) => void;
  hint?: string;
}

function Text(props: FieldProps & { nullable?: boolean; placeholder?: string }) {
  return (
    <Field label={props.label} hint={props.hint}>
      <input
        value={getIn(props.draft, props.path) ?? ""}
        placeholder={props.placeholder}
        onChange={(e) =>
          props.set(props.path, props.nullable && !e.target.value ? null : e.target.value)
        }
      />
    </Field>
  );
}

function Num(props: FieldProps & { min?: number; step?: number }) {
  return (
    <Field label={props.label} hint={props.hint}>
      <input
        type="number"
        className="num"
        min={props.min}
        step={props.step ?? 1}
        value={getIn(props.draft, props.path) ?? ""}
        onChange={(e) => props.set(props.path, e.target.value === "" ? null : Number(e.target.value))}
      />
    </Field>
  );
}

function Check(props: FieldProps) {
  return (
    <label className="setting check-setting">
      <input
        type="checkbox"
        checked={Boolean(getIn(props.draft, props.path))}
        onChange={(e) => props.set(props.path, e.target.checked)}
      />{" "}
      {props.label}
    </label>
  );
}

function Secret({ name, info }: { name: string; info: SettingsInfo | null }) {
  const set = info?.secrets[name];
  return (
    <p className="small secret">
      <span className="mono">{name}</span>:{" "}
      {set ? (
        <span className="ok-text">set in the environment</span>
      ) : (
        <span className="error">not set (add it to .env or the environment)</span>
      )}
    </p>
  );
}
