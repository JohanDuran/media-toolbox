import { useState } from "react";
import { api, fileUrl, type Sound } from "../api";
import { go, usePoll } from "../util";

function SoundRow({ s, onChange }: { s: Sound; onChange: () => void }) {
  const [note, setNote] = useState("");
  const [usedIn, setUsedIn] = useState("");
  const [open, setOpen] = useState(false);
  const mark = async (fields: Parameters<typeof api.mark>[1]) => {
    await api.mark(s.file, fields);
    onChange();
  };
  const status = s.status ?? "new";
  return (
    <div className={`sound card ${status}`}>
      <div className="sound-main">
        <div className="sound-text">
          <div className="sound-prompt">{s.prompt || <span className="muted">(no prompt)</span>}</div>
          <div className="muted mono small">
            {s.file} · {s.seconds ?? "?"}s · peak {s.peak_db ?? "?"} dB{s.seed != null ? ` · seed ${s.seed}` : ""}
          </div>
          <div className="chips">
            <span className={`badge badge-${status === "keep" ? "done" : status === "reject" ? "failed" : "queued"}`}>{status}</span>
            {(s.used_in ?? []).map((u) => (
              <span key={u} className="tag">used in {u}</span>
            ))}
            {(s.tags ?? []).map((t) => (
              <span key={t} className="tag">#{t}</span>
            ))}
          </div>
          {s.notes && <div className="notes small">{s.notes}</div>}
        </div>
        <audio controls preload="none" src={fileUrl(s.path)} />
        <div className="sound-actions">
          <button className={`btn small ${status === "keep" ? "primary" : ""}`} onClick={() => mark({ status: "keep" })}>
            Keep
          </button>
          <button className={`btn small ${status === "reject" ? "danger" : ""}`} onClick={() => mark({ status: "reject" })}>
            Reject
          </button>
          <button className="btn small ghost" onClick={() => setOpen((o) => !o)}>
            {open ? "Close" : "More"}
          </button>
        </div>
      </div>
      {open && (
        <div className="sound-more">
          <input placeholder="Used in (Resolve project)" value={usedIn} onChange={(e) => setUsedIn(e.target.value)} />
          <input placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />
          <button
            className="btn small"
            disabled={!note && !usedIn}
            onClick={async () => {
              await mark({ note: note || undefined, used_in: usedIn || undefined });
              setNote("");
              setUsedIn("");
            }}
          >
            Save
          </button>
        </div>
      )}
    </div>
  );
}

export default function Sounds() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const { data, error, refresh } = usePoll(() => api.library(q, status), 0, [q, status]);

  return (
    <div className="stack">
      <section className="card toolbar">
        <input className="search" placeholder="Search prompts, tags, notes…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>
          <option value="keep">Kept</option>
          <option value="new">New</option>
          <option value="reject">Rejected</option>
        </select>
        <span className="spacer" />
        <button
          className="btn small"
          onClick={async () => {
            const r = await api.scan();
            setScanMsg(r.cataloged.length ? `Cataloged ${r.cataloged.length} new file(s)` : "Nothing new to catalog");
            refresh();
          }}
        >
          Scan folder
        </button>
        <button className="btn small primary" onClick={() => go("run", "generate_sfx")}>
          Generate…
        </button>
      </section>
      {scanMsg && <div className="muted small">{scanMsg}</div>}
      {error && <div className="err">{error}</div>}
      <div className="muted small">{data ? `${data.length} sound(s)` : "Loading…"}</div>
      {(data ?? []).map((s) => (
        <SoundRow key={s.file} s={s} onChange={refresh} />
      ))}
    </div>
  );
}
