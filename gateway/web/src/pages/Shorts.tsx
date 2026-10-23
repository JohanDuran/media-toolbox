import { useState } from "react";
import { api, fileUrl, type ShortInfo, type ShortProject } from "../api";
import { ago, go, usePoll } from "../util";

/** Cache-bust built files so a rebuilt shot shows its new thumbnail / video. */
const bust = (path: string, mtime: number | null) => `${fileUrl(path)}&v=${mtime ?? 0}`;

function Detail({ project }: { project: string }) {
  const { data: info, error, refresh } = usePoll<ShortInfo>(() => api.shortInfo(project), 4000, [project]);
  const [msg, setMsg] = useState<string | null>(null);

  const run = async (tool: string, params: Record<string, unknown>, label: string) => {
    setMsg(null);
    try {
      const job = await api.submit(tool, { project_dir: project, ...params });
      setMsg(`${label} queued — job ${job.id}`);
      setTimeout(refresh, 1500);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  if (error && !info) return <div className="card err block">{error}</div>;
  if (!info) return <div className="card muted">Loading…</div>;
  const o = info.outputs;

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <div>
            <div className="card-title">{info.title}</div>
            <div className="muted mono small">{info.project_dir}</div>
          </div>
          <span className="spacer" />
          <button className="btn small" onClick={() => run("short_build", {}, "Full build")}>Build all</button>
          <button className="btn small" onClick={() => run("short_build", { parts: ["overlay"] }, "Graphics build")}>
            Graphics
          </button>
          <button className="btn small" onClick={() => run("short_build", { parts: ["audio"] }, "Audio build")}>Audio</button>
          <button className="btn small primary" onClick={() => run("short_preview", {}, "Preview")}>Preview</button>
        </div>
        <div className="muted small">
          {info.length_s.toFixed(2)} s · {info.frames} frames · {info.shots.length} shots · {info.graphics_list.length}{" "}
          graphics · 1080×1920 @ 30 fps
        </div>
        {msg && (
          <div className="small" style={{ marginTop: 8 }}>
            {msg} · <a href="#/jobs">see jobs</a>
          </div>
        )}
      </section>

      <section className="shorts-grid">
        {info.shots.map((s) => (
          <div key={s.name} className="card shot">
            {s.thumb.exists ? (
              <a href={fileUrl(s.file.path)} target="_blank" rel="noreferrer">
                <img src={bust(s.thumb.path, s.thumb.mtime)} alt={s.name} />
              </a>
            ) : (
              <div className="shot-empty">not built</div>
            )}
            <div className="shot-name mono">{s.name}</div>
            <div className="muted small">
              {s.start.toFixed(2)}–{s.end.toFixed(2)} s · {s.frames}f
            </div>
            {s.note && <div className="small shot-note">{s.note}</div>}
            <div className="row">
              <button className="btn small" onClick={() => run("short_build", { parts: ["shots"], only: [s.name] }, `Rebuild ${s.name}`)}>
                Rebuild
              </button>
              <span className="muted small">{s.file.exists ? ago(s.file.mtime) : ""}</span>
            </div>
          </div>
        ))}
      </section>

      <section className="split shorts-bottom">
        <div className="card">
          <h2>Preview</h2>
          {o.preview.exists ? (
            <>
              <video className="short-video" controls src={bust(o.preview.path, o.preview.mtime)} />
              <div className="muted small">built {ago(o.preview.mtime)}</div>
            </>
          ) : (
            <p className="muted">No preview yet. Build, then press Preview.</p>
          )}
        </div>
        <div className="card">
          <h2>Contact sheet</h2>
          {o.sheet.exists ? (
            <a href={fileUrl(o.sheet.path)} target="_blank" rel="noreferrer">
              <img className="sheet" src={bust(o.sheet.path, o.sheet.mtime)} alt="contact sheet" />
            </a>
          ) : (
            <p className="muted">Made with the preview.</p>
          )}
          <h3>Outputs</h3>
          <dl className="facts wide">
            {(["overlay", "narration", "bed"] as const).map((k) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd className="mono small">{o[k].exists ? `built ${ago(o[k].mtime)}` : "not built"}</dd>
              </div>
            ))}
          </dl>
          <h3>Edit points (from the narration)</h3>
          <div className="anchors">
            {Object.entries(info.anchors).map(([k, v]) => (
              <span key={k} className="tag">
                {k} {v.toFixed(2)}s
              </span>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

export default function Shorts({ project }: { project?: string }) {
  const { data: projects, error } = usePoll<ShortProject[]>(api.shorts, 10000);
  const selected = project ?? projects?.find((p) => !p.error)?.project_dir;

  return (
    <div className="split run">
      <section className="card list">
        <h2>Shorts</h2>
        {error && <div className="err">{error}</div>}
        {projects && projects.length === 0 && (
          <p className="muted small">No projects yet. A Shorts project is a folder with a short.json.</p>
        )}
        <ul className="toollist">
          {(projects ?? []).map((p) => (
            <li key={p.project_dir}>
              <a href="#" className={p.project_dir === selected ? "active" : ""}
                 onClick={(e) => { e.preventDefault(); go("shorts", p.project_dir); }}>
                <span>
                  {p.title}
                  <div className="muted small">{p.error ? "spec error" : `${p.length_s?.toFixed(1)} s · ${p.shots} shots`}</div>
                </span>
              </a>
            </li>
          ))}
        </ul>
      </section>
      {selected ? <Detail project={selected} /> : <div className="card muted">Pick a project.</div>}
    </div>
  );
}
