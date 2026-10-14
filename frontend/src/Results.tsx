import { Fragment, useEffect, useState } from "react";
import {
  api,
  Dimension,
  DimensionScore,
  Evidence,
  RunData,
  ScoreCard,
  ScreenResult,
  ScreeningCriterion,
} from "./api";

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

/** Polls a run until it finishes, then shows the ranking. */
export function RunView({ runId, showNames }: { runId: string; showNames: boolean }) {
  const [data, setData] = useState<RunData | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      <div>
        <p>
          {meta.status === "reading"
            ? "Reading documents..."
            : meta.mode === "screen"
              ? "Screening..."
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
      <Screening data={data} showNames={showNames} />
    ) : (
      <p className="muted">No results.</p>
    );
  }
  return (
    <>
      <Ranking data={data} showNames={showNames} />
      {data.screening && (
        <details className="screening-details">
          <summary>Screening of all candidates</summary>
          <Screening data={data} showNames={showNames} />
        </details>
      )}
    </>
  );
}

/** The fast screen: score per criterion, matched terms and where they were found. */
function Screening({ data, showNames }: { data: RunData; showNames: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const screening = data.screening!;
  const criteria: ScreeningCriterion[] = screening.criteria.length
    ? screening.criteria
    : (screening.candidates[0]?.criteria ?? []).map((c) => ({ ...c, terms: [] }));
  const label = (r: ScreenResult) =>
    showNames ? (data.identity[r.candidate_id] ?? r.candidate_id) : r.candidate_id;
  const out = screening.candidates.filter((r) => r.screened_out).length;

  return (
    <div>
      <div className="row between">
        <span className="muted small">
          {screening.candidates.length} screened{out ? `, ${out} screened out` : ""} · keyword
          {screening.candidates.some((r) => r.criteria.some((c) => c.semantic_score !== null))
            ? " and meaning"
            : ""}{" "}
          search, no model read the CVs
        </span>
        <span className="row">
          {["screening.html", "screening.csv"].map((f) => (
            <a key={f} href={api.fileUrl(data.meta.id, f)} target="_blank" rel="noreferrer">
              {f}
            </a>
          ))}
        </span>
      </div>
      <table className="ranking">
        <thead>
          <tr>
            <th>#</th>
            <th>Candidate</th>
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
        <tbody>
          {screening.candidates.map((r) => {
            const isOpen = open === r.candidate_id;
            const byId = Object.fromEntries(r.criteria.map((c) => [c.id, c]));
            return (
              <Fragment key={r.candidate_id}>
                <tr
                  className={`clickable ${isOpen ? "open" : ""} ${r.screened_out ? "dim" : ""}`}
                  onClick={() => setOpen(isOpen ? null : r.candidate_id)}
                >
                  <td>{r.rank}</td>
                  <td>
                    {label(r)}{" "}
                    {r.screened_out && <span className="flag knockout_failed">screened out</span>}
                  </td>
                  <td>
                    <strong>{pct(r.score)}</strong>
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
                    <td colSpan={criteria.length + 4}>
                      <div className="detail">
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
                                c.matched.map((t) => (
                                  <span className="chip" key={t}>
                                    {t}
                                  </span>
                                ))
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
      </table>
      <p className="muted small">
        A first impression from keyword search, not a verdict: CVs that describe a skill in
        other words can be missed. Check the screened-out candidates before discarding anyone.
      </p>
    </div>
  );
}

function Ranking({ data, showNames }: { data: RunData; showNames: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const results = data.results!;
  const traitsWeighted = (results.job.weights.traits ?? 0) > 0;
  const columns = COLUMNS.filter((c) => c.key !== "traits" || traitsWeighted);
  const label = (card: ScoreCard) =>
    showNames ? (data.identity[card.candidate_id] ?? card.candidate_id) : card.candidate_id;

  return (
    <div>
      <div className="row between">
        <span className="muted small">
          {results.candidates.length} candidates · {data.meta.job_title} · run {data.meta.id}
        </span>
        <span className="row">
          {["report.html", "ranking.csv", "results.json", "job.yaml"].map((f) => (
            <a key={f} href={api.fileUrl(data.meta.id, f)} target="_blank" rel="noreferrer">
              {f}
            </a>
          ))}
        </span>
      </div>
      <table className="ranking">
        <thead>
          <tr>
            <th>#</th>
            <th>Candidate</th>
            <th>Total</th>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
            <th>Flags</th>
          </tr>
        </thead>
        <tbody>
          {results.candidates.map(({ score_card: card }) => {
            const isOpen = open === card.candidate_id;
            return (
              <Fragment key={card.candidate_id}>
                <tr
                  className={`clickable ${isOpen ? "open" : ""}`}
                  onClick={() => setOpen(isOpen ? null : card.candidate_id)}
                >
                  <td>{card.rank ?? "–"}</td>
                  <td>{label(card)}</td>
                  <td>
                    <strong>{pct(card.total)}</strong>
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
                    <td colSpan={columns.length + 4}>
                      <Detail runId={data.meta.id} card={card} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      <p className="muted small">
        Decision support only: read the evidence before deciding, especially for flagged
        candidates. Click a row for the reasons and quotes.
      </p>
    </div>
  );
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
              <div className="small">{r.rationale}</div>
              <Quotes evidence={r.evidence} />
            </div>
          ))}
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
