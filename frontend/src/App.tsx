import { useEffect, useState } from "react";
import { api, CandidateSummary, DocType, JobProfile, RunMeta, Status } from "./api";
import { Candidates } from "./Candidates";
import { FolderPicker } from "./FolderPicker";
import { JobEditor, JobInput } from "./JobEditor";
import { RunView } from "./Results";

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [showNames, setShowNames] = useState(false);

  const [folder, setFolder] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<CandidateSummary[] | null>(null);
  const [overrides, setOverrides] = useState<Record<string, DocType>>({});
  const [exclude, setExclude] = useState<string[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  const [job, setJob] = useState<JobProfile | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [mode, setMode] = useState<"screen" | "shortlist" | "all">("screen");
  const [shortlist, setShortlist] = useState(10);
  const [history, setHistory] = useState<RunMeta[]>([]);

  useEffect(() => {
    api.status().then(setStatus, () => setStatus(null));
    api.runs().then(setHistory, () => setHistory([]));
  }, []);

  const scan = (path: string, o: Record<string, DocType>, x: string[]) => {
    setFolder(path);
    setScanning(true);
    setScanError(null);
    api
      .scan(path, o, x)
      .then((r) => setCandidates(r.candidates))
      .catch((e: Error) => setScanError(e.message))
      .finally(() => setScanning(false));
  };

  const start = () => {
    if (!folder || !job) return;
    setRunError(null);
    const cleaned: JobProfile = {
      ...job,
      must_have: job.must_have.filter((r) => r.description.trim()),
      nice_to_have: job.nice_to_have.filter((r) => r.description.trim()),
      screening: (job.screening ?? []).filter((c) => c.name.trim() && c.terms.length),
    };
    api
      .startRun(
        folder,
        cleaned,
        overrides,
        exclude,
        mode === "screen" ? "screen" : "full",
        mode === "shortlist" ? shortlist : null,
      )
      .then((meta) => {
        setRunId(meta.id);
        setHistory((h) => [meta, ...h.filter((m) => m.id !== meta.id)]);
      })
      .catch((e: Error) => setRunError(e.message));
  };

  const usable = candidates?.filter((c) => c.files.some((f) => !exclude.includes(f.path))) ?? [];

  return (
    <div className="app">
      <header className="row between">
        <h1>cvscore</h1>
        <div className="row">
          <label className="small">
            <input
              type="checkbox"
              checked={showNames}
              onChange={(e) => setShowNames(e.target.checked)}
            />{" "}
            Show names
          </label>
          {status && (
            <span
              className={`badge ${status.ready ? "ok" : "bad"}`}
              title={status.problems.join("\n")}
            >
              {status.provider ?? "no config"}
              {status.models?.scorer ? ` · ${status.models.scorer}` : ""}
              {!status.ready && " · not ready"}
            </span>
          )}
        </div>
      </header>
      {status && !status.ready && (
        <div className="warning box">
          <strong>The models are not configured yet.</strong>
          <ul>
            {status.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          Copy <code>cvscore.example.yaml</code> to <code>cvscore.yaml</code> and set your
          provider, then reload. You can still browse and check the folder.
        </div>
      )}

      <section>
        <h2>1. Applications</h2>
        {!folder || !candidates ? (
          <>
            <p className="muted">
              Pick the folder with the applications. It can hold one folder per candidate, or
              everyone's CVs and cover letters together; files are matched to candidates by the
              name and email in them and by their file names.
            </p>
            <FolderPicker initial={folder ?? undefined} onChoose={(p) => scan(p, {}, [])} />
            {scanning && <p className="muted">Reading the documents...</p>}
            {scanError && <p className="error">{scanError}</p>}
          </>
        ) : (
          <>
            <div className="row between">
              <span>
                <span className="mono">{folder}</span>
                <span className="muted"> · {usable.length} candidates</span>
              </span>
              <span className="row">
                <button
                  disabled={scanning}
                  onClick={() => scan(folder, overrides, exclude)}
                  title="Group the files again using your changes"
                >
                  {scanning ? "Reading..." : "Apply changes"}
                </button>
                <button
                  className="link"
                  onClick={() => {
                    setCandidates(null);
                    setOverrides({});
                    setExclude([]);
                  }}
                >
                  Change folder
                </button>
              </span>
            </div>
            {scanError && <p className="error">{scanError}</p>}
            <Candidates
              candidates={candidates}
              overrides={overrides}
              exclude={exclude}
              showNames={showNames}
              onChange={(o, x) => {
                setOverrides(o);
                setExclude(x);
              }}
            />
          </>
        )}
      </section>

      <section>
        <h2>2. Job</h2>
        {job ? (
          <>
            <div className="row between">
              <span className="muted">Review the profile: every candidate is assessed against it.</span>
              <button className="link" onClick={() => setJob(null)}>
                Use another job ad
              </button>
            </div>
            <JobEditor job={job} onChange={setJob} />
          </>
        ) : (
          <JobInput onJob={setJob} />
        )}
      </section>

      <section>
        <h2>3. Ranking</h2>
        <div className="row between">
          <span className="muted">
            {!candidates
              ? "Pick a folder first."
              : !job
                ? "Add the job first."
                : `${usable.length} candidates for "${job.title}".`}
          </span>
          <button className="primary" disabled={!candidates || !job || !usable.length} onClick={start}>
            {mode === "screen" ? "Screen candidates" : "Rank candidates"}
          </button>
        </div>
        <div className="modes">
          <label>
            <input type="radio" checked={mode === "screen"} onChange={() => setMode("screen")} />{" "}
            <b>Quick screen</b>{" "}
            <span className="muted small">
              keyword (and meaning) search for the screening criteria; seconds, no model reads
              the CVs
            </span>
          </label>
          <label>
            <input
              type="radio"
              checked={mode === "shortlist"}
              onChange={() => setMode("shortlist")}
            />{" "}
            <b>Screen, then assess the best</b>{" "}
            <input
              type="number"
              className="num"
              min={1}
              value={shortlist}
              onChange={(e) => setShortlist(Math.max(1, Number(e.target.value)))}
              onFocus={() => setMode("shortlist")}
            />{" "}
            <span className="muted small">in depth (2 model calls each)</span>
          </label>
          <label>
            <input type="radio" checked={mode === "all"} onChange={() => setMode("all")} />{" "}
            <b>Assess everyone in depth</b>{" "}
            <span className="muted small">{usable.length * 2} model calls</span>
          </label>
        </div>
        {runError && <p className="error">{runError}</p>}
        {runId && <RunView runId={runId} showNames={showNames} />}
        {history.length > 0 && (
          <details className="history">
            <summary>Earlier runs ({history.length})</summary>
            <ul>
              {history.map((m) => (
                <li key={m.id}>
                  <button className="link" onClick={() => setRunId(m.id)}>
                    {m.job_title}
                  </button>{" "}
                  <span className="muted small">
                    {m.created.replace("T", " ").slice(0, 16)} ·{" "}
                    {m.mode === "screen"
                      ? "screen"
                      : m.shortlist
                        ? `best ${m.shortlist} in depth`
                        : "in depth"}{" "}
                    · {m.status} · {m.folder}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
    </div>
  );
}
