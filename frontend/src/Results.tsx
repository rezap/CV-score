import { Fragment, useEffect, useState } from "react";
import {
  api,
  DocType,
  RegionKey,
  regionOf,
  Dimension,
  DimensionScore,
  Evidence,
  RunData,
  ScoreCard,
  ScreenResult,
  ScreeningCriterion,
} from "./api";
import { ALL_REGIONS, Chevron, RegionBadge, RegionFilter, countRegions } from "./ui";

const COLUMNS: { key: Dimension; label: string }[] = [
  { key: "experience", label: "Experience" },
  { key: "achievements", label: "Achievements" },
  { key: "skills", label: "Skills" },
  { key: "requirements", label: "Requirements" },
  { key: "traits", label: "Traits" },
];

const FLAG_TEXT: Record<string, string> = {
  knockout_failed: "must-have failed",
  needs_review: "needs review",
  low_confidence: "low confidence",
  inconsistency: "inconsistent",
};

const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}`);

const LEVEL_TEXT: Record<string, string> = {
  none: "not shown",
  mentioned: "listed",
  applied: "used in work",
  proven: "proven",
};
const levelText = (v: string) => LEVEL_TEXT[v] ?? v;

/** The candidate shares a tier with the one ranked just above it. */
function sameTier(rows: ScoreCard[], card: ScoreCard): boolean {
  const i = rows.indexOf(card);
  return i > 0 && card.tier != null && rows[i - 1].tier === card.tier;
}

/** Polls a run until it finishes, then shows the ranking. */
export function RunView({ runId, showNames }: { runId: string; showNames: boolean }) {
  const [data, setData] = useState<RunData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewState>({ query: "", regions: ALL_REGIONS });

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const poll = () =>
      api
        .run(runId)
        .then((d) => {
          if (stop) return;
          setData(d);
          if (d.meta.status === "reading" || d.meta.status === "assessing")
            timer = window.setTimeout(poll, 1000);
        })
        .catch((e: Error) => !stop && setError(e.message));
    setData(null);
    poll();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [runId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading...</p>;
  const { meta } = data;
  if (meta.status === "failed") return <p className="error">The run failed: {meta.error}</p>;
  if (meta.status !== "done") {
    const share = meta.total ? meta.done / meta.total : 0;
    return (
      <div className="run-progress">
        <p>
          {meta.status === "reading"
            ? "Reading documents..."
            : meta.mode === "screen"
              ? "Screening..."
              : meta.total > 0 && meta.done >= meta.total
                ? "Checking close calls: second readings of the best and head-to-head comparisons..."
                : `Assessing candidates: ${meta.done} of ${meta.total}`}
        </p>
        <div className="progress">
          <div style={{ width: `${Math.max(3, share * 100)}%` }} />
        </div>
      </div>
    );
  }
  if (!data.results) {
    return data.screening ? (
      <Screening data={data} showNames={showNames} view={view} onView={setView} />
    ) : (
      <p className="muted">No results.</p>
    );
  }
  return (
    <>
      <Ranking data={data} showNames={showNames} view={view} onView={setView} />
      {data.screening && (
        <details className="screening-details">
          <summary>Screening of all candidates</summary>
          <Screening data={data} showNames={showNames} view={view} onView={setView} />
        </details>
      )}
    </>
  );
}

// --- Filters and segments --------------------------------------------------------------

interface ViewState {
  query: string;
  regions: RegionKey[];
}

interface Band {
  key: string;
  label: string;
  hint: string;
  open: boolean;
}

const BANDS: (Band & { min: number })[] = [
  { key: "strong", label: "Strong match", hint: "70 and above", min: 0.7, open: true },
  { key: "good", label: "Good match", hint: "50 to 69", min: 0.5, open: true },
  { key: "partial", label: "Partial match", hint: "30 to 49", min: 0.3, open: false },
  { key: "weak", label: "Weak match", hint: "below 30", min: -Infinity, open: false },
];

function bandOf(score: number | null): Band {
  return BANDS.find((b) => (score ?? 0) >= b.min)!;
}

/** Rows grouped into score bands (plus one set-aside group), each one folding away. */
function useSegments<T>(
  rows: T[],
  segmentOf: (row: T) => Band,
  setAside: Band,
): { segments: { band: Band; rows: T[] }[]; isOpen: (b: Band) => boolean; toggle: (b: Band) => void; setAll: (open: boolean) => void } {
  const [state, setState] = useState<Record<string, boolean>>({});
  const order = [...BANDS, setAside];
  const segments = order
    .map((band) => ({ band, rows: rows.filter((r) => segmentOf(r).key === band.key) }))
    .filter((s) => s.rows.length > 0);
  const isOpen = (b: Band) => state[b.key] ?? b.open;
  return {
    segments,
    isOpen,
    toggle: (b) => setState({ ...state, [b.key]: !isOpen(b) }),
    setAll: (open) => setState(Object.fromEntries(order.map((b) => [b.key, open]))),
  };
}

function Toolbar(props: {
  data: RunData;
  ids: string[];
  view: ViewState;
  onView: (v: ViewState) => void;
  showNames: boolean;
  setAll: (open: boolean) => void;
  shown: number;
}) {
  const { data, view, onView } = props;
  const byId = Object.fromEntries(data.candidates.map((c) => [c.id, c]));
  return (
    <div className="toolbar results-toolbar">
      <input
        className="search"
        type="search"
        placeholder={props.showNames ? "Find by name or ID" : "Find by ID"}
        value={view.query}
        onChange={(e) => onView({ ...view, query: e.target.value })}
      />
      <RegionFilter
        selected={view.regions}
        counts={countRegions(props.ids.map((id) => byId[id]))}
        onChange={(regions) => onView({ ...view, regions })}
      />
      <span className="toolbar-right">
        <span className="muted small">
          {props.shown} of {props.ids.length}
        </span>
        <button className="link small" onClick={() => props.setAll(true)}>
          Expand all
        </button>
        <button className="link small" onClick={() => props.setAll(false)}>
          Collapse all
        </button>
      </span>
    </div>
  );
}

function matches(data: RunData, id: string, view: ViewState, showNames: boolean): boolean {
  const c = data.candidates.find((x) => x.id === id);
  if (!view.regions.includes(regionOf(c))) return false;
  const q = view.query.trim().toLowerCase();
  if (!q) return true;
  return (
    id.toLowerCase().includes(q) ||
    (showNames && (data.identity[id] ?? "").toLowerCase().includes(q))
  );
}

function SegmentHead(props: {
  band: Band;
  count: number;
  open: boolean;
  colSpan: number;
  onToggle: () => void;
}) {
  return (
    <tr className={`segment segment-${props.band.key}`}>
      <td colSpan={props.colSpan}>
        <button className="segment-toggle" onClick={props.onToggle} aria-expanded={props.open}>
          <Chevron open={props.open} />
          <span className="segment-label">{props.band.label}</span>
          <span className="segment-count">{props.count}</span>
          <span className="muted small">{props.band.hint}</span>
        </button>
      </td>
    </tr>
  );
}

const SCREENED_OUT: Band = {
  key: "out",
  label: "Screened out",
  hint: "no match for a required criterion",
  open: false,
};

const KNOCKED_OUT: Band = {
  key: "knockout",
  label: "Must-have failed",
  hint: "a must-have clearly not met",
  open: false,
};

/** The fast screen: score per criterion, matched terms and where they were found. */
function Screening(props: {
  data: RunData;
  showNames: boolean;
  view: ViewState;
  onView: (v: ViewState) => void;
}) {
  const { data, showNames, view } = props;
  const [open, setOpen] = useState<string | null>(null);
  const screening = data.screening!;
  const criteria: ScreeningCriterion[] = screening.criteria.length
    ? screening.criteria
    : (screening.candidates[0]?.criteria ?? []).map((c) => ({ ...c, terms: [] }));
  const label = (r: ScreenResult) =>
    showNames ? (data.identity[r.candidate_id] ?? r.candidate_id) : r.candidate_id;
  const out = screening.candidates.filter((r) => r.screened_out).length;
  const rows = screening.candidates.filter((r) => matches(data, r.candidate_id, view, showNames));
  const seg = useSegments(rows, (r) => (r.screened_out ? SCREENED_OUT : bandOf(r.score)), SCREENED_OUT);
  const cols = criteria.length + 5;

  return (
    <div>
      <div className="row between results-head">
        <span className="muted small">
          {screening.candidates.length} screened{out ? `, ${out} screened out` : ""} · keyword
          {screening.candidates.some((r) => r.criteria.some((c) => c.semantic_score !== null))
            ? " and meaning"
            : ""}{" "}
          search, no model read the CVs
        </span>
        <span className="row downloads">
          {["screening.html", "screening.csv"].map((f) => (
            <a key={f} href={api.fileUrl(data.meta.id, f)} target="_blank" rel="noreferrer">
              {f}
            </a>
          ))}
        </span>
      </div>
      <Toolbar
        data={data}
        ids={screening.candidates.map((r) => r.candidate_id)}
        view={view}
        onView={props.onView}
        showNames={showNames}
        setAll={seg.setAll}
        shown={rows.length}
      />
      <div className="table-wrap">
        <table className="ranking">
          <thead>
            <tr>
              <th>#</th>
              <th>Candidate</th>
              <th>Location</th>
              <th>Score</th>
              {criteria.map((c) => (
                <th key={c.id} title={c.terms.join(", ")}>
                  {c.name}
                  {c.required ? " *" : ""}
                </th>
              ))}
              <th title="Span of dated entries outside education">Years</th>
            </tr>
          </thead>
          {seg.segments.map(({ band, rows: segRows }) => (
            <tbody key={band.key}>
              <SegmentHead
                band={band}
                count={segRows.length}
                open={seg.isOpen(band)}
                colSpan={cols}
                onToggle={() => seg.toggle(band)}
              />
              {seg.isOpen(band) &&
                segRows.map((r) => {
                  const isOpen = open === r.candidate_id;
                  const byId = Object.fromEntries(r.criteria.map((c) => [c.id, c]));
                  return (
                    <Fragment key={r.candidate_id}>
                      <tr
                        className={`clickable ${isOpen ? "open" : ""} ${r.screened_out ? "dim" : ""}`}
                        onClick={() => setOpen(isOpen ? null : r.candidate_id)}
                      >
                        <td className="rank">{r.rank}</td>
                        <td>
                          {label(r)} <CvLink data={data} candidate={r.candidate_id} />
                        </td>
                        <td>
                          <RegionBadge location={summaryOf(data, r.candidate_id)?.location} />
                        </td>
                        <td>
                          <ScorePill value={r.score} />
                        </td>
                        {criteria.map((c) => {
                          const m = byId[c.id];
                          return (
                            <td key={c.id} title={m?.matched.join(", ")}>
                              {m ? (
                                <span className="bar">
                                  <span style={{ width: `${Math.round(m.score * 100)}%` }} />
                                  <em>{m.matched.length ? m.matched.slice(0, 2).join(", ") : "–"}</em>
                                </span>
                              ) : (
                                "–"
                              )}
                            </td>
                          );
                        })}
                        <td>{r.years_hint ?? "–"}</td>
                      </tr>
                      {isOpen && (
                        <tr className="detail-row">
                          <td colSpan={cols}>
                            <div className="detail">
                              <Documents data={data} candidate={r.candidate_id} showNames={showNames} />
                              {r.notes.map((n) => (
                                <p className="warning" key={n}>
                                  {n}
                                </p>
                              ))}
                              {r.criteria.map((c) => (
                                <div className="check" key={c.id}>
                                  <strong>{c.name}</strong>: {pct(c.score)}
                                  {c.semantic_score !== null && (
                                    <span className="muted small"> (meaning {pct(c.semantic_score)})</span>
                                  )}
                                  <div className="small">
                                    {c.matched.length ? (
                                      c.matched.map((t) => {
                                        const listed = c.listed_only?.includes(t);
                                        return (
                                          <span
                                            className={`chip ${listed ? "listed" : ""}`}
                                            key={t}
                                            title={
                                              listed
                                                ? "Only in a skills list or summary: counts half unless the criterion asks for a mention only"
                                                : "Found in described work"
                                            }
                                          >
                                            {t}
                                            {listed ? " · listed" : ""}
                                          </span>
                                        );
                                      })
                                    ) : (
                                      <span className="muted">no terms found</span>
                                    )}
                                  </div>
                                  <ul className="quotes">
                                    {c.snippets.map((sn, i) => (
                                      <li key={i}>{highlightRedactions(sn)}</li>
                                    ))}
                                  </ul>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
            </tbody>
          ))}
        </table>
      </div>
      {rows.length === 0 && <p className="muted">No candidates match the filters.</p>}
      <p className="muted small">
        A first impression from keyword search, not a verdict: CVs that describe a skill in
        other words can be missed. Check the screened-out candidates before discarding anyone.
      </p>
    </div>
  );
}

function Ranking(props: {
  data: RunData;
  showNames: boolean;
  view: ViewState;
  onView: (v: ViewState) => void;
}) {
  const { data, showNames, view } = props;
  const [open, setOpen] = useState<string | null>(null);
  const results = data.results!;
  const traitsWeighted = (results.job.weights.traits ?? 0) > 0;
  const columns = COLUMNS.filter((c) => c.key !== "traits" || traitsWeighted);
  const label = (card: ScoreCard) =>
    showNames ? (data.identity[card.candidate_id] ?? card.candidate_id) : card.candidate_id;
  const cards = results.candidates
    .map((c) => c.score_card)
    .filter((card) => matches(data, card.candidate_id, view, showNames));
  const seg = useSegments(
    cards,
    (card) => (card.flags.includes("knockout_failed") ? KNOCKED_OUT : bandOf(card.total)),
    KNOCKED_OUT,
  );
  const cols = columns.length + 5;

  return (
    <div>
      <div className="row between results-head">
        <span className="muted small">
          {results.candidates.length} assessed · {data.meta.job_title} · run {data.meta.id}
        </span>
        <span className="row downloads">
          {["report.html", "ranking.csv", "results.json", "job.yaml"].map((f) => (
            <a key={f} href={api.fileUrl(data.meta.id, f)} target="_blank" rel="noreferrer">
              {f}
            </a>
          ))}
        </span>
      </div>
      <Toolbar
        data={data}
        ids={results.candidates.map((c) => c.score_card.candidate_id)}
        view={view}
        onView={props.onView}
        showNames={showNames}
        setAll={seg.setAll}
        shown={cards.length}
      />
      <div className="table-wrap">
        <table className="ranking">
          <thead>
            <tr>
              <th>#</th>
              <th>Candidate</th>
              <th>Location</th>
              <th>Total</th>
              {columns.map((c) => (
                <th key={c.key}>{c.label}</th>
              ))}
              <th>Flags</th>
            </tr>
          </thead>
          {seg.segments.map(({ band, rows }) => (
            <tbody key={band.key}>
              <SegmentHead
                band={band}
                count={rows.length}
                open={seg.isOpen(band)}
                colSpan={cols}
                onToggle={() => seg.toggle(band)}
              />
              {seg.isOpen(band) &&
                rows.map((card) => {
                  const isOpen = open === card.candidate_id;
                  return (
                    <Fragment key={card.candidate_id}>
                      <tr
                        className={`clickable ${isOpen ? "open" : ""}`}
                        onClick={() => setOpen(isOpen ? null : card.candidate_id)}
                      >
                        <td className="rank">{card.rank ?? "–"}</td>
                        <td>
                          {label(card)} <CvLink data={data} candidate={card.candidate_id} />
                        </td>
                        <td>
                          <RegionBadge location={summaryOf(data, card.candidate_id)?.location} />
                        </td>
                        <td className="total-cell">
                          <ScorePill value={card.total} />
                          {card.spread ? (
                            <span
                              className="spread small muted"
                              title="Half the difference between two independent readings"
                            >
                              {" "}
                              ±{pct(card.spread / 2)}
                            </span>
                          ) : null}
                          {sameTier(rows, card) && (
                            <span
                              className="tie small"
                              title="Too close to the candidate above to tell apart from the scores"
                            >
                              {" "}
                              ≈
                            </span>
                          )}
                        </td>
                        {columns.map((c) => (
                          <td key={c.key}>
                            <ScoreCell score={card.scores.find((s) => s.dimension === c.key)} />
                          </td>
                        ))}
                        <td>
                          {card.flags.map((f) => (
                            <span key={f} className={`flag ${f}`}>
                              {FLAG_TEXT[f] ?? f}
                            </span>
                          ))}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="detail-row">
                          <td colSpan={cols}>
                            <Documents data={data} candidate={card.candidate_id} showNames={showNames} />
                            <Detail runId={data.meta.id} card={card} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
            </tbody>
          ))}
        </table>
      </div>
      {cards.length === 0 && <p className="muted">No candidates match the filters.</p>}
      <p className="muted small">
        Decision support only: read the evidence before deciding, especially for flagged
        candidates. Click a row for the reasons and quotes.
      </p>
    </div>
  );
}

function ScorePill({ value }: { value: number | null }) {
  if (value == null) return <span className="muted">–</span>;
  return <span className={`score-pill band-${bandOf(value).key}`}>{pct(value)}</span>;
}

function ScoreCell({ score }: { score?: DimensionScore }) {
  if (!score || score.score == null) return <span className="muted">–</span>;
  const width = Math.round(score.score * 100);
  return (
    <span className="bar" title={`${width} (confidence ${pct(score.confidence)})`}>
      <span style={{ width: `${width}%` }} className={score.confidence < 0.5 ? "unsure" : ""} />
      <em>{width}</em>
    </span>
  );
}

function Quotes({ evidence }: { evidence: Evidence[] }) {
  if (!evidence.length) return null;
  return (
    <ul className="quotes">
      {evidence.map((e, i) => (
        <li key={i}>
          <span className="muted small">{e.doc_type.replace("_", " ")}:</span> “{e.quote}”
        </li>
      ))}
    </ul>
  );
}

function Detail({ runId, card }: { runId: string; card: ScoreCard }) {
  const [docs, setDocs] = useState<Record<string, string> | null>(null);
  const [showDocs, setShowDocs] = useState(false);

  useEffect(() => {
    if (showDocs && !docs) api.redacted(runId, card.candidate_id).then(setDocs, () => setDocs({}));
  }, [showDocs, docs, runId, card.candidate_id]);

  const years = card.features.years_experience;
  return (
    <div className="detail">
      {card.notes.map((n) => (
        <p className="warning" key={n}>
          {n}
        </p>
      ))}
      {years != null && (
        <p className="muted small">
          {years} years of dated experience · skill coverage {pct(card.features.skill_coverage)}%
        </p>
      )}
      {card.requirement_checks.length > 0 && (
        <>
          <h4>Requirements</h4>
          {card.requirement_checks.map((r) => (
            <div key={r.requirement_id} className="check">
              <span className={`status ${r.status}`}>{r.status}</span>{" "}
              <strong>{r.must_have ? "Must" : "Nice"}:</strong> {r.description}
              {r.required_level && (
                <span className="level small muted">
                  {" "}
                  · shown: {levelText(r.observed_level ?? "none")}, asked: {levelText(r.required_level)}
                </span>
              )}
              <div className="small">{r.rationale}</div>
              <Quotes evidence={r.evidence} />
            </div>
          ))}
        </>
      )}
      {(card.skill_checks ?? []).length > 0 && (
        <>
          <h4>Key skills</h4>
          <div className="skill-checks">
            {card.skill_checks!.map((sk) => (
              <span
                key={sk.skill}
                className={`skill-check ${sk.value >= 1 ? "full" : sk.value > 0 ? "part" : "none"}`}
                title={`Shown: ${levelText(sk.observed_level)}; asked: ${levelText(sk.required_level)}. Word found in: ${sk.found_in.join(", ") || "nowhere"}.`}
              >
                {sk.skill} <em>{levelText(sk.observed_level)}</em>
              </span>
            ))}
          </div>
        </>
      )}
      {card.scores
        .filter((s) => s.dimension !== "requirements")
        .map((s) => (
          <div key={s.dimension}>
            <h4>
              {s.dimension[0].toUpperCase() + s.dimension.slice(1)}: {pct(s.score)}
              <span className="muted small"> (confidence {pct(s.confidence)})</span>
            </h4>
            <div className="small">{s.rationale}</div>
            <Quotes evidence={s.evidence} />
          </div>
        ))}
      {card.trait_scores.length > 0 && (
        <>
          <h4>Personal characteristics</h4>
          {card.trait_scores.map((t) => (
            <div key={t.trait_id} className="check">
              <strong>{t.name}</strong>: {pct(t.score)}
              <div className="small">{t.rationale}</div>
              <Quotes evidence={t.evidence} />
            </div>
          ))}
        </>
      )}
      <button className="link" onClick={() => setShowDocs(!showDocs)}>
        {showDocs ? "Hide" : "Show"} what the model saw (redacted)
      </button>
      {showDocs &&
        (docs ? (
          Object.entries(docs).map(([type, text]) => (
            <div key={type}>
              <h4>{type.replace("_", " ")}</h4>
              <pre className="redacted">{highlightRedactions(text)}</pre>
            </div>
          ))
        ) : (
          <p className="muted">Loading...</p>
        ))}
    </div>
  );
}

function highlightRedactions(text: string) {
  return text.split(/(\[REDACTED(?:: [^\]]+)?\])/g).map((part, i) =>
    part.startsWith("[REDACTED") ? (
      <mark key={i} className="redaction">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

const DOC_LABEL: Record<DocType, string> = {
  cv: "CV",
  cover_letter: "Cover letter",
  application_form: "Application form",
  other: "Document",
};

function summaryOf(data: RunData, candidate: string) {
  return data.candidates.find((c) => c.id === candidate);
}

function filesOf(data: RunData, candidate: string) {
  return summaryOf(data, candidate)?.files ?? [];
}

/** Opens the candidate's original CV in a new tab, without expanding the row. */
function CvLink({ data, candidate }: { data: RunData; candidate: string }) {
  const files = filesOf(data, candidate);
  const cv = files.find((f) => f.doc_type === "cv") ?? files[0];
  if (!cv) return null;
  return (
    <a
      className="cv-link small"
      href={api.originalUrl(data.meta.id, candidate, cv.path)}
      target="_blank"
      rel="noreferrer"
      title="Open the original document"
      onClick={(e) => e.stopPropagation()}
    >
      {cv.doc_type === "cv" ? "CV" : "file"} ↗
    </a>
  );
}

/** Links to all of a candidate's original documents. File names often contain the
 * candidate's name, so they are shown only with "Show names". */
function Documents({
  data,
  candidate,
  showNames,
}: {
  data: RunData;
  candidate: string;
  showNames: boolean;
}) {
  const files = filesOf(data, candidate);
  if (!files.length) return null;
  return (
    <p className="small documents">
      Original documents:{" "}
      {files.map((f, i) => {
        const ext = f.path.split(".").pop()?.toUpperCase() ?? "";
        return (
          <span key={f.path}>
            {i > 0 && " · "}
            <a
              href={api.originalUrl(data.meta.id, candidate, f.path)}
              target="_blank"
              rel="noreferrer"
            >
              {DOC_LABEL[f.doc_type]} ({showNames ? f.path.split("/").pop() : ext})
            </a>
          </span>
        );
      })}
    </p>
  );
}
