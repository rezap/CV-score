// Small shared pieces: collapsible steps, region chips and state kept in the browser.
import { ReactNode, useEffect, useState } from "react";
import { CandidateSummary, Location, REGIONS, RegionKey, regionOf } from "./api";

/** State saved in this browser (localStorage), so a reload keeps the work. Storage can be
 * missing or blocked (private windows): then it is ordinary state. */
export function usePersistent<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(`cvscore:${key}`);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      if (value === null || value === undefined) window.localStorage.removeItem(`cvscore:${key}`);
      else window.localStorage.setItem(`cvscore:${key}`, JSON.stringify(value));
    } catch {
      // storage full or blocked: keep working without it
    }
  }, [key, value]);
  return [value, setValue];
}

/** A numbered step of the workflow that folds away to a one-line summary. */
export function Step(props: {
  n: number;
  title: string;
  summary?: ReactNode;
  open: boolean;
  onToggle: () => void;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`step ${props.open ? "open" : "closed"}`}>
      <header className="step-head">
        <button className="step-toggle" onClick={props.onToggle} aria-expanded={props.open}>
          <span className="step-n">{props.n}</span>
          <span className="step-title">{props.title}</span>
          {!props.open && props.summary && <span className="step-summary">{props.summary}</span>}
          <Chevron open={props.open} />
        </button>
        {props.actions && <div className="step-actions">{props.actions}</div>}
      </header>
      {props.open && <div className="step-body">{props.children}</div>}
    </section>
  );
}

export function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={`chevron ${open ? "open" : ""}`} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M5 6l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

export function countRegions(candidates: (CandidateSummary | undefined)[]) {
  const counts: Record<RegionKey, number> = { norway: 0, eu: 0, europe: 0, world: 0, unknown: 0 };
  for (const c of candidates) counts[regionOf(c)]++;
  return counts;
}

/** Toggle chips for where candidates live, with how many are in each. */
export function RegionFilter(props: {
  selected: RegionKey[];
  counts: Record<RegionKey, number>;
  onChange: (selected: RegionKey[]) => void;
  label?: string;
}) {
  const { selected, counts, onChange } = props;
  const all = REGIONS.map((r) => r.key);
  return (
    <div
      className={`region-filter ${selected.length === all.length ? "all" : ""}`}
      role="group" aria-label={props.label ?? "Location"}>
      {props.label && <span className="filter-label">{props.label}</span>}
      {REGIONS.map((r) => {
        const on = selected.includes(r.key);
        return (
          <button
            key={r.key}
            className={`filter-chip region-${r.key} ${on ? "on" : ""}`}
            aria-pressed={on}
            title={
              selected.length === all.length
                ? `Only ${r.label}`
                : on
                  ? `Hide ${r.label}`
                  : `Add ${r.label}`
            }
            onClick={() =>
              // From "everyone", a click picks that region alone; after that, clicks add
              // and remove regions.
              onChange(
                selected.length === all.length
                  ? [r.key]
                  : on
                    ? selected.length > 1
                      ? selected.filter((k) => k !== r.key)
                      : all
                    : all.filter((k) => k === r.key || selected.includes(k)),
              )
            }
          >
            {r.label} <span className="count">{counts[r.key]}</span>
          </button>
        );
      })}
      {selected.length < all.length && (
        <button className="link small" onClick={() => onChange(all)}>
          show all
        </button>
      )}
    </div>
  );
}

export const ALL_REGIONS: RegionKey[] = REGIONS.map((r) => r.key);

/** A small badge with where the candidate lives; the tooltip says how it was found. */
export function RegionBadge({ location }: { location?: Location }) {
  const key: RegionKey = location?.region ?? "unknown";
  const region = REGIONS.find((r) => r.key === key)!;
  const how = location?.region
    ? `${location.country_name}${location.place && location.place !== location.country_name ? ` (${location.place})` : ""}` +
      ` · from ${location.source}${location.confident ? "" : " · a guess, check the CV"}`
    : "No address, town or phone country code found";
  return (
    <span className={`region-badge region-${key} ${location?.confident ? "" : "unsure"}`} title={how}>
      {location?.country && key !== "norway" ? location.country : region.short}
      {location?.region && !location.confident ? "?" : ""}
      {location?.relocation && (
        <span className="reloc" title="Mentions relocating">
          {" "}
          ✈
        </span>
      )}
    </span>
  );
}
