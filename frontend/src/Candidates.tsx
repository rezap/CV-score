import { useState } from "react";
import { CandidateSummary, DOC_TYPES, DocType, RegionKey, regionOf } from "./api";
import { Chevron, RegionBadge, RegionFilter, countRegions } from "./ui";

interface Props {
  candidates: CandidateSummary[];
  overrides: Record<string, DocType>;
  exclude: string[];
  regions: RegionKey[];
  showNames: boolean;
  onChange: (overrides: Record<string, DocType>, exclude: string[]) => void;
  onRegions: (regions: RegionKey[]) => void;
}

const SHORT_TYPE: Record<DocType, string> = {
  cv: "CV",
  cover_letter: "letter",
  application_form: "form",
  other: "other",
};

const hasProblems = (c: CandidateSummary) =>
  c.warnings.length > 0 ||
  !c.files.some((f) => f.doc_type === "cv") ||
  c.files.some((f) => f.error || f.warnings.length || (f.quality ?? 1) < 0.5);

/** How the folder's files were grouped into candidates: one line each, opened to check
 * or change the document types. */
export function Candidates(props: Props) {
  const { candidates, overrides, exclude, regions, showNames, onChange } = props;
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [problemsOnly, setProblemsOnly] = useState(false);

  const setType = (path: string, value: DocType) =>
    onChange({ ...overrides, [path]: value }, exclude);
  const toggleFile = (path: string) =>
    onChange(
      overrides,
      exclude.includes(path) ? exclude.filter((p) => p !== path) : [...exclude, path],
    );
  const toggleOpen = (id: string) => {
    const next = new Set(open);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setOpen(next);
  };

  const problems = candidates.filter(hasProblems).length;
  const shown = candidates
    .map((c, i) => ({ c, n: i + 1 }))
    .filter(({ c }) => !problemsOnly || hasProblems(c));

  return (
    <div className="candidates">
      <div className="toolbar">
        <RegionFilter
          label="Include candidates in"
          selected={regions}
          counts={countRegions(candidates)}
          onChange={props.onRegions}
        />
        <span className="toolbar-right">
          {problems > 0 && (
            <label className="small inline">
              <input
                type="checkbox"
                checked={problemsOnly}
                onChange={(e) => setProblemsOnly(e.target.checked)}
              />{" "}
              only the {problems} to check
            </label>
          )}
          <button
            className="link small"
            onClick={() => setOpen(open.size ? new Set() : new Set(shown.map(({ c }) => c.id)))}
          >
            {open.size ? "Collapse all" : "Expand all"}
          </button>
        </span>
      </div>
      <p className="muted small">
        Location is read from the address, town and phone number in the documents: where the
        person lives, not their nationality. Candidates outside the chosen locations are left
        out of the run.
      </p>
      <ul className="candidate-list">
        {shown.map(({ c, n }) => {
          const isOpen = open.has(c.id);
          const outside = !regions.includes(regionOf(c));
          const usedFiles = c.files.filter((f) => !exclude.includes(f.path));
          return (
            <li key={c.id} className={`candidate ${outside ? "outside" : ""} ${isOpen ? "open" : ""}`}>
              <button className="candidate-row" onClick={() => toggleOpen(c.id)} aria-expanded={isOpen}>
                <Chevron open={isOpen} />
                <span className="candidate-name">{showNames ? c.label : `Candidate ${n}`}</span>
                <RegionBadge location={c.location} />
                <span className="doc-chips">
                  {usedFiles.map((f) => (
                    <span key={f.path} className={`doc-chip ${f.error ? "bad" : ""}`}>
                      {SHORT_TYPE[overrides[f.path] ?? f.doc_type]}
                    </span>
                  ))}
                </span>
                {hasProblems(c) && <span className="attention" title="Check this candidate">!</span>}
                <span className="muted small mono candidate-id">{c.id}</span>
              </button>
              {isOpen && (
                <div className="candidate-body">
                  {c.grouped_by.length > 0 && (
                    <p className="muted small">Matched by {c.grouped_by.join(", ")}</p>
                  )}
                  {c.warnings.map((w) => (
                    <p className="warning" key={w}>
                      {w}
                    </p>
                  ))}
                  <table className="files">
                    <colgroup>
                      <col style={{ width: "34%" }} />
                      <col style={{ width: "150px" }} />
                      <col />
                      <col style={{ width: "60px" }} />
                    </colgroup>
                    <tbody>
                      {c.files.map((f) => (
                        <tr key={f.path} className={exclude.includes(f.path) ? "excluded" : ""}>
                          <td className="mono small">{f.path}</td>
                          <td>
                            <select
                              value={overrides[f.path] ?? f.doc_type}
                              onChange={(e) => setType(f.path, e.target.value as DocType)}
                            >
                              {DOC_TYPES.map((t) => (
                                <option key={t.value} value={t.value}>
                                  {t.label}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="small muted">
                            {f.error ? <span className="error">{f.error}</span> : f.reason}
                            {f.warnings.map((w) => (
                              <div className="warning" key={w}>
                                {w}
                              </div>
                            ))}
                          </td>
                          <td>
                            <label className="small">
                              <input
                                type="checkbox"
                                checked={!exclude.includes(f.path)}
                                onChange={() => toggleFile(f.path)}
                              />{" "}
                              use
                            </label>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
