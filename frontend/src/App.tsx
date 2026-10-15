import { useEffect, useRef, useState } from "react";
import {
  api,
  CandidateSummary,
  DocType,
  DEFAULT_HOME,
  Home,
  JobProfile,
  RegionKey,
  RunMeta,
  Status,
  regionList,
  regionOf,
} from "./api";
import { Candidates } from "./Candidates";
import { FolderPicker } from "./FolderPicker";
import { JobEditor, JobInput } from "./JobEditor";
import { RunView } from "./Results";
import { ALL_REGIONS, HomeContext, Step, knownRegions, usePersistent } from "./ui";

type Mode = "screen" | "shortlist" | "all";

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [showNames, setShowNames] = useState(false);

  // Kept in this browser, so a reload (or tomorrow) picks up where you left off.
  const [folder, setFolder] = usePersistent<string | null>("folder", null);
  const [overrides, setOverrides] = usePersistent<Record<string, DocType>>("overrides", {});
  const [exclude, setExclude] = usePersistent<string[]>("exclude", []);
  const [savedRegions, setRegions] = usePersistent<RegionKey[]>("regions", ALL_REGIONS);
  const regions = knownRegions(savedRegions);
  const [job, setJobState] = usePersistent<JobProfile | null>("job", null);
  const [mode, setMode] = usePersistent<Mode>("mode", "screen");
  const [shortlist, setShortlist] = usePersistent<number>("shortlist", 10);
  const [runId, setRunId] = usePersistent<string | null>("run", null);

  const [candidates, setCandidates] = useState<CandidateSummary[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [jobVersion, setJobVersion] = useState(0);
  const [runError, setRunError] = useState<string | null>(null);
  const [history, setHistory] = useState<RunMeta[]>([]);
  const [open, setOpen] = useState({ files: true, job: true, run: true });
  const results = useRef<HTMLDivElement>(null);

  // A new profile (drafted, loaded or reused) replaces the editor's local state.
  const replaceJob = (j: JobProfile | null) => {
    setJobState(j);
    setJobVersion((v) => v + 1);
  };

  const scan = (path: string, o: Record<string, DocType>, x: string[]) => {
    setFolder(path);
    setScanning(true);
    setScanError(null);
    api
      .scan(path, o, x)
      .then((r) => setCandidates(r.candidates))
      .catch((e: Error) => {
        setScanError(e.message);
        setCandidates(null);
      })
      .finally(() => setScanning(false));
  };

  useEffect(() => {
    api.status().then(setStatus, () => setStatus(null));
    api.runs().then(setHistory, () => setHistory([]));
    if (folder) scan(folder, overrides, exclude); // reopen the last folder (extraction is cached)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const inRegion = (c: CandidateSummary) => regions.includes(regionOf(c));
  const usable =
    candidates?.filter((c) => inRegion(c) && c.files.some((f) => !exclude.includes(f.path))) ?? [];

  const start = () => {
    if (!folder || !job || !candidates) return;
    setRunError(null);
    const cleaned: JobProfile = {
      ...job,
      must_have: job.must_have.filter((r) => r.description.trim()),
      nice_to_have: job.nice_to_have.filter((r) => r.description.trim()),
      screening: (job.screening ?? []).filter((c) => c.name.trim() && c.terms.length),
    };
    // Candidates outside the chosen locations are left out like excluded files.
    const outside = candidates.filter((c) => !inRegion(c)).flatMap((c) => c.files.map((f) => f.path));
    api
      .startRun(
        folder,
        cleaned,
        overrides,
        [...new Set([...exclude, ...outside])],
        mode === "screen" ? "screen" : "full",
        mode === "shortlist" ? shortlist : null,
      )
      .then((meta) => {
        setRunId(meta.id);
        setHistory((h) => [meta, ...h.filter((m) => m.id !== meta.id)]);
        setOpen({ files: false, job: false, run: true });
        window.setTimeout(() => results.current?.scrollIntoView({ behavior: "smooth" }), 50);
      })
      .catch((e: Error) => setRunError(e.message));
  };

  const reuseJob = (id: string) =>
    api.run(id).then(
      (d) => {
        if (d.job) {
          replaceJob(d.job);
          setOpen((o) => ({ ...o, job: true }));
        }
      },
      (e: Error) => setRunError(e.message),
    );

  const home: Home = status
    ? { code: status.home_country, name: status.home_name, inEU: status.home_in_eu }
    : DEFAULT_HOME;
  const regionNote =
    regions.length < ALL_REGIONS.length
      ? ` in ${regionList(home)
          .filter((r) => regions.includes(r.key))
          .map((r) => r.label)
          .join(", ")}`
      : "";

  return (
    <HomeContext.Provider value={home}>
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">
            cv
          </span>
          <span>
            <strong>cvscore</strong>
            <span className="muted small"> candidate ranking</span>
          </span>
        </div>
        <div className="row">
          <label className="switch small">
            <input
              type="checkbox"
              checked={showNames}
              onChange={(e) => setShowNames(e.target.checked)}
            />
            <span className="track" aria-hidden="true" />
            Show names
          </label>
          {status && (
            <span
              className={`status-pill ${status.ready ? "ok" : "bad"}`}
              title={status.problems.join("\n")}
            >
              <span className="dot" />
              {status.provider ?? "no config"}
              {status.models?.scorer ? ` · ${status.models.scorer}` : ""}
              {!status.ready && " · not ready"}
            </span>
          )}
        </div>
      </header>
      <main>
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

        <Step
          n={1}
          title="Applications"
          open={open.files}
          onToggle={() => setOpen({ ...open, files: !open.files })}
          summary={
            folder && candidates ? (
              <>
                <span className="mono">{folder}</span> · {usable.length} of {candidates.length}{" "}
                candidates{regionNote}
              </>
            ) : (
              "Pick a folder"
            )
          }
        >
          {!folder || !candidates ? (
            <>
              <p className="muted">
                Pick the folder with the applications. It can hold one folder per candidate, or
                everyone's CVs and cover letters together; files are matched to candidates by
                the name and email in them and by their file names.
              </p>
              <FolderPicker initial={folder ?? undefined} onChoose={(p) => scan(p, {}, [])} />
              {scanning && <p className="muted">Reading the documents...</p>}
              {scanError && <p className="error">{scanError}</p>}
            </>
          ) : (
            <>
              <div className="row between folder-bar">
                <span>
                  <span className="mono">{folder}</span>
                  <span className="muted">
                    {" "}
                    · {usable.length} of {candidates.length} candidates
                  </span>
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
                regions={regions}
                showNames={showNames}
                onChange={(o, x) => {
                  setOverrides(o);
                  setExclude(x);
                }}
                onRegions={setRegions}
              />
            </>
          )}
        </Step>

        <Step
          n={2}
          title="Job"
          open={open.job}
          onToggle={() => setOpen({ ...open, job: !open.job })}
          summary={
            job
              ? `${job.title} · ${job.must_have.length} must-haves, ${job.nice_to_have.length} nice-to-haves, ${job.screening?.length ?? 0} screening criteria`
              : "Add the job ad"
          }
        >
          {job ? (
            <>
              <div className="row between">
                <span className="muted">
                  Review the profile: every candidate is assessed against it. Your edits are
                  kept in this browser.
                </span>
                <button className="link" onClick={() => replaceJob(null)}>
                  Use another job ad
                </button>
              </div>
              <JobEditor key={jobVersion} job={job} onChange={setJobState} />
            </>
          ) : (
            <JobInput onJob={replaceJob} />
          )}
        </Step>

        <Step
          n={3}
          title="Ranking"
          open={open.run}
          onToggle={() => setOpen({ ...open, run: !open.run })}
          summary={runId ? `run ${runId}` : undefined}
        >
          <div className="run-bar">
            <div className="modes" role="radiogroup" aria-label="How to rank">
              <label className={`mode ${mode === "screen" ? "on" : ""}`}>
                <input
                  type="radio"
                  checked={mode === "screen"}
                  onChange={() => setMode("screen")}
                />
                <b>Quick screen</b>
                <span className="muted small">
                  keyword (and meaning) search for the screening criteria; seconds, no model
                  reads the CVs
                </span>
              </label>
              <label className={`mode ${mode === "shortlist" ? "on" : ""}`}>
                <input
                  type="radio"
                  checked={mode === "shortlist"}
                  onChange={() => setMode("shortlist")}
                />
                <b>
                  Screen, then assess the best{" "}
                  <input
                    type="number"
                    className="num"
                    min={1}
                    value={shortlist}
                    onChange={(e) => setShortlist(Math.max(1, Number(e.target.value)))}
                    onFocus={() => setMode("shortlist")}
                  />
                </b>
                <span className="muted small">in depth (2 model calls each)</span>
              </label>
              <label className={`mode ${mode === "all" ? "on" : ""}`}>
                <input type="radio" checked={mode === "all"} onChange={() => setMode("all")} />
                <b>Assess everyone in depth</b>
                <span className="muted small">{usable.length * 2} model calls</span>
              </label>
            </div>
            <div className="row between">
              <span className="muted">
                {!candidates
                  ? "Pick a folder first."
                  : !job
                    ? "Add the job first."
                    : `${usable.length} candidates${regionNote} for "${job.title}".`}
              </span>
              <button
                className="primary big"
                disabled={!candidates || !job || !usable.length}
                onClick={start}
              >
                {mode === "screen" ? "Screen candidates" : "Rank candidates"}
              </button>
            </div>
          </div>
          {runError && <p className="error">{runError}</p>}
          <div ref={results}>{runId && <RunView key={runId} runId={runId} showNames={showNames} />}</div>
          {history.length > 0 && (
            <details className="history">
              <summary>Earlier runs ({history.length})</summary>
              <ul>
                {history.map((m) => (
                  <li key={m.id} className={m.id === runId ? "current" : ""}>
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
                    </span>{" "}
                    <button
                      className="link small"
                      title="Load the job profile this run used into step 2"
                      onClick={() => reuseJob(m.id)}
                    >
                      use its job
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Step>
      </main>
    </div>
    </HomeContext.Provider>
  );
}
