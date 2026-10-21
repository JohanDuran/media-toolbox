import { useState } from "react";
import { api, type State, type Worker } from "../api";
import { ago, CopyButton } from "../util";

function WorkerCard({ w }: { w: Worker }) {
  const [busy, setBusy] = useState(false);
  const info = (w.info ?? {}) as Record<string, unknown>;
  const status = !w.up ? "down" : w.busy ? "busy" : "idle";
  const facts: [string, unknown][] = [
    ["Device", info.device],
    ["Version", info.version ?? info.hyperframes],
    ["Model", info.loaded_model],
    ["VRAM used", info.vram_used_by_worker_mb != null ? `${info.vram_used_by_worker_mb} MB` : undefined],
    ["ffmpeg", typeof info.ffmpeg === "string" ? info.ffmpeg.replace(/^ffmpeg version /, "").split(" ")[0] : undefined],
    ["Node", info.node],
  ];
  return (
    <div className={`card worker ${status}`}>
      <div className="card-head">
        <span className={`dot ${status}`} />
        <div>
          <div className="card-title">{w.title}</div>
          <div className="muted mono small">{w.name}</div>
        </div>
        <span className="spacer" />
        {w.gpu && <span className="tag">GPU</span>}
      </div>
      <div className="worker-status">
        {!w.up && <span className="err">Not reachable{w.error ? `: ${w.error}` : ""}</span>}
        {w.up && w.busy && (
          <span>
            Running <a href={`#/jobs/${w.job_id}`} className="mono">{w.job_id}</a>
          </span>
        )}
        {w.up && !w.busy && <span className="muted">Idle</span>}
        {w.up && w.gpu && <span className={w.model_loaded ? "ok" : "muted"}>{w.model_loaded ? "Model in VRAM" : "Model not loaded"}</span>}
      </div>
      <dl className="facts">
        {facts
          .filter(([, v]) => v != null && v !== "")
          .map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
      </dl>
      <div className="card-foot">
        <span className="muted small">Checked {ago(w.checked)}</span>
        {w.gpu && w.model_loaded && !w.busy && (
          <button
            className="btn small"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.unload(w.name);
              } finally {
                setBusy(false);
              }
            }}
          >
            Free VRAM
          </button>
        )}
      </div>
    </div>
  );
}

export default function Overview({ state }: { state: State | null }) {
  if (!state) return <p className="muted">Loading…</p>;
  const g = state.gpu;
  const used = g?.memory_used_mb ?? 0;
  const total = g?.memory_total_mb ?? 1;
  const register = `claude mcp add --scope user --transport http media-toolbox ${state.config.mcp_url}`;
  const c = state.counts;

  return (
    <div className="stack">
      <section className="grid stats">
        <div className="card stat">
          <div className="stat-label">GPU</div>
          {g?.error ? (
            <div className="err small">{g.error}</div>
          ) : (
            <>
              <div className="stat-value">{g?.name ?? "–"}</div>
              <div className="meter">
                <div style={{ width: `${(used / total) * 100}%` }} />
              </div>
              <div className="muted small">
                {(used / 1024).toFixed(1)} / {(total / 1024).toFixed(1)} GB VRAM · {g?.utilization ?? 0}% busy ·{" "}
                {g?.temperature_c ?? "–"}°C
              </div>
            </>
          )}
        </div>
        <div className="card stat">
          <div className="stat-label">Queue</div>
          <div className="stat-value">
            {c.running ?? 0} running · {c.queued ?? 0} waiting
          </div>
          <div className="muted small">
            {c.done ?? 0} done · {c.failed ?? 0} failed · {c.cancelled ?? 0} cancelled
          </div>
        </div>
        <div className="card stat">
          <div className="stat-label">Claude (MCP)</div>
          <div className="stat-value mono small-value">{state.config.mcp_url}</div>
          <div className="row">
            <CopyButton text={register} label="Copy register command" />
          </div>
        </div>
      </section>

      <section>
        <h2>Tools</h2>
        <div className="grid workers">
          {state.workers.map((w) => (
            <WorkerCard key={w.name} w={w} />
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Folders</h2>
        <dl className="facts wide">
          <div>
            <dt>Reachable</dt>
            <dd className="mono">{state.config.host_home}</dd>
          </div>
          <div>
            <dt>Default outputs</dt>
            <dd className="mono">{state.config.outputs_dir}</dd>
          </div>
          <div>
            <dt>Sound library</dt>
            <dd className="mono">{state.config.sfx_library}</dd>
          </div>
          <div>
            <dt>Never written</dt>
            <dd className="mono">{state.config.protected_dirs.join(", ") || "–"}</dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
