import { useEffect, useRef, useState } from "react";
import { fileUrl, type Job } from "./api";

/** Re-run `load` every `ms` (and whenever deps change); keeps the last good value. */
export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const run = async () => {
      try {
        const d = await loadRef.current();
        if (alive) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
      if (alive && ms > 0) timer = window.setTimeout(run, ms);
    };
    run();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, tick, ...deps]);

  return { data, error, refresh: () => setTick((t) => t + 1) };
}

export function useHashRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export const go = (...parts: string[]) => {
  window.location.hash = "/" + parts.map(encodeURIComponent).join("/");
};

export function ago(ts: number | null | undefined): string {
  if (!ts) return "–";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function duration(job: Job): string {
  if (!job.started) return "–";
  const s = (job.finished ?? Date.now() / 1000) - job.started;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}

export function Progress({ value }: { value: number | null }) {
  if (value == null) return <div className="progress indeterminate"><div /></div>;
  return (
    <div className="progress" title={`${Math.round(value * 100)}%`}>
      <div style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  );
}

const AUDIO = /\.(wav|mp3|flac|ogg|m4a|aac)$/i;
const VIDEO = /\.(mp4|mov|webm)$/i;
const WIN_PATH = /^[A-Za-z]:\\/;

/** Renders a result value, turning Windows paths into previews / open links. */
export function ResultView({ value }: { value: unknown }) {
  const paths: string[] = [];
  const walk = (v: unknown) => {
    if (typeof v === "string" && WIN_PATH.test(v) && /\.[a-z0-9]{2,5}$/i.test(v)) paths.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(value);
  const unique = [...new Set(paths)];
  return (
    <div className="result">
      {unique.length > 0 && (
        <div className="files">
          {unique.map((p) => (
            <div key={p} className="file-row">
              <a href={fileUrl(p)} target="_blank" rel="noreferrer" className="mono">
                {p}
              </a>
              {AUDIO.test(p) && <audio controls preload="none" src={fileUrl(p)} />}
              {VIDEO.test(p) && <video controls preload="none" src={fileUrl(p)} />}
            </div>
          ))}
        </div>
      )}
      <details>
        <summary>Raw result</summary>
        <pre>{JSON.stringify(value, null, 2)}</pre>
      </details>
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn small"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}
