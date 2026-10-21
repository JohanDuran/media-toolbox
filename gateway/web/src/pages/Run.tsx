import { useEffect, useState } from "react";
import { api, type Param, type Tool } from "../api";
import { go, usePoll } from "../util";

const PATH_KINDS = new Set(["path_in", "paths_in", "dir_in", "path_out", "dir_out"]);

function initial(p: Param): string | boolean {
  if (p.kind === "boolean") return Boolean(p.default);
  if (p.default == null) return "";
  return String(p.default);
}

function Field({ p, value, onChange }: { p: Param; value: string | boolean; onChange: (v: string | boolean) => void }) {
  const id = `f-${p.name}`;
  let input;
  if (p.kind === "boolean") {
    input = <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
  } else if (p.kind === "select" && p.options) {
    input = (
      <select id={id} value={String(value)} onChange={(e) => onChange(e.target.value)}>
        {p.options.map((o) => (
          <option key={o} value={o}>
            {o || "(default)"}
          </option>
        ))}
      </select>
    );
  } else if (p.kind === "longtext" || p.kind === "paths_in") {
    input = (
      <textarea
        id={id}
        rows={p.kind === "paths_in" ? 3 : 4}
        className={p.kind === "paths_in" ? "mono" : ""}
        value={String(value)}
        placeholder={p.kind === "paths_in" ? "One full Windows path per line" : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  } else {
    input = (
      <input
        id={id}
        type={p.kind === "integer" || p.kind === "number" ? "number" : "text"}
        step={p.kind === "number" ? "any" : undefined}
        className={PATH_KINDS.has(p.kind) || p.kind === "strings" ? "mono" : ""}
        value={String(value)}
        placeholder={PATH_KINDS.has(p.kind) ? "C:\\Users\\…" : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  return (
    <div className={`field ${p.kind === "boolean" ? "inline" : ""}`}>
      <label htmlFor={id}>
        {p.label}
        {p.required && <span className="req">*</span>}
      </label>
      {input}
      {p.help && <div className="help">{p.help}</div>}
    </div>
  );
}

function ToolForm({ tool }: { tool: Tool }) {
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    setValues(Object.fromEntries(tool.params.map((p) => [p.name, initial(p)])));
    setError(null);
  }, [tool]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    setError(null);
    const params: Record<string, unknown> = {};
    for (const p of tool.params) {
      const v = values[p.name];
      if (v === "" || v == null) continue;
      params[p.name] =
        p.kind === "paths_in" ? String(v).split(/\r?\n/).map((s) => s.trim().replace(/^"|"$/g, "")).filter(Boolean) : v;
    }
    try {
      const job = await api.submit(tool.name, params);
      go("jobs", job.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <form className="card form" onSubmit={submit}>
      <div className="card-head">
        <div>
          <div className="card-title">{tool.title}</div>
          <div className="muted small">
            runs on <span className="mono">{tool.worker}</span>
            {tool.gpu ? " · GPU" : ""}
          </div>
        </div>
      </div>
      <p className="desc">{tool.description}</p>
      {tool.params.map((p) => (
        <Field key={p.name} p={p} value={values[p.name] ?? ""} onChange={(v) => setValues((s) => ({ ...s, [p.name]: v }))} />
      ))}
      {error && <div className="err block">{error}</div>}
      <div className="row">
        <button className="btn primary" disabled={sending}>
          {sending ? "Queuing…" : "Run"}
        </button>
      </div>
    </form>
  );
}

export default function Run({ toolName }: { toolName?: string }) {
  const { data: tools, error } = usePoll(api.tools, 0);
  const tool = tools?.find((t) => t.name === toolName) ?? tools?.[0];

  return (
    <div className="split run">
      <section className="card list">
        <h2>Tools</h2>
        {error && <div className="err">{error}</div>}
        <ul className="toollist">
          {(tools ?? []).map((t) => (
            <li key={t.name}>
              <a href={`#/run/${t.name}`} className={t === tool ? "active" : ""}>
                <span>{t.title}</span>
                {t.gpu && <span className="tag">GPU</span>}
              </a>
            </li>
          ))}
        </ul>
      </section>
      {tool && <ToolForm tool={tool} />}
    </div>
  );
}
