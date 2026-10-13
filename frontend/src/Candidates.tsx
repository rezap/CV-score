import { CandidateSummary, DOC_TYPES, DocType } from "./api";

interface Props {
  candidates: CandidateSummary[];
  overrides: Record<string, DocType>;
  exclude: string[];
  showNames: boolean;
  onChange: (overrides: Record<string, DocType>, exclude: string[]) => void;
}

/** How the folder's files were grouped into candidates, with the document types to check. */
export function Candidates({ candidates, overrides, exclude, showNames, onChange }: Props) {
  const setType = (path: string, value: DocType) =>
    onChange({ ...overrides, [path]: value }, exclude);
  const toggle = (path: string) =>
    onChange(
      overrides,
      exclude.includes(path) ? exclude.filter((p) => p !== path) : [...exclude, path],
    );

  return (
    <div className="candidates">
      {candidates.map((c, i) => (
        <div className="candidate" key={c.id}>
          <div className="row between">
            <strong>{showNames ? c.label : `Candidate ${i + 1}`}</strong>
            <span className="muted small">
              {c.id}
              {c.grouped_by.length > 0 && ` · matched by ${c.grouped_by.join(", ")}`}
            </span>
          </div>
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
                        onChange={() => toggle(f.path)}
                      />{" "}
                      use
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
