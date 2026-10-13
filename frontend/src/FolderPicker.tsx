import { useEffect, useState } from "react";
import { api, FolderListing } from "./api";

interface Props {
  initial?: string;
  onChoose: (path: string) => void;
}

/** Browse folders on the machine running `cvscore serve` and pick one. */
export function FolderPicker({ initial, onChoose }: Props) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");

  const open = (path?: string) => {
    setError(null);
    api
      .browse(path)
      .then((l) => {
        setListing(l);
        setTyped(l.path);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => open(initial), [initial]);

  if (!listing) return <p className="muted">{error ?? "Loading folders..."}</p>;

  const crumbs = breadcrumbs(listing.root, listing.path);
  return (
    <div className="picker">
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          open(typed);
        }}
      >
        <input
          className="grow mono"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          aria-label="Folder path"
        />
        <button type="submit">Go</button>
      </form>
      <nav className="crumbs">
        {crumbs.map((c, i) => (
          <span key={c.path}>
            {i > 0 && <span className="sep">/</span>}
            <button className="link" onClick={() => open(c.path)}>
              {c.name}
            </button>
          </span>
        ))}
      </nav>
      {error && <p className="error">{error}</p>}
      <ul className="folders">
        {listing.parent && (
          <li>
            <button className="link" onClick={() => open(listing.parent!)}>
              ↑ ..
            </button>
          </li>
        )}
        {listing.folders.map((f) => (
          <li key={f.path}>
            <button className="link" onClick={() => open(f.path)}>
              📁 {f.name}
            </button>
            {f.documents > 0 && <span className="muted"> {f.documents} documents</span>}
          </li>
        ))}
        {listing.folders.length === 0 && !listing.parent && (
          <li className="muted">No sub-folders.</li>
        )}
      </ul>
      <div className="row between">
        <span className="muted">
          {listing.documents.length} documents directly in this folder
          {listing.documents.length > 0 && `: ${preview(listing.documents)}`}
        </span>
        <button className="primary" onClick={() => onChoose(listing.path)}>
          Use this folder
        </button>
      </div>
    </div>
  );
}

function preview(names: string[]): string {
  const shown = names.slice(0, 4).join(", ");
  return names.length > 4 ? `${shown}, ...` : shown;
}

function breadcrumbs(root: string, path: string): { name: string; path: string }[] {
  const out = [{ name: root, path: root }];
  if (path === root) return out;
  const sep = root.includes("\\") ? "\\" : "/";
  const rest = path.slice(root.length).split(/[\\/]/).filter(Boolean);
  let current = root;
  for (const part of rest) {
    current = current.endsWith(sep) ? current + part : current + sep + part;
    out.push({ name: part, path: current });
  }
  return out;
}
