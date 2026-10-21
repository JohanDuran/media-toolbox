import { useEffect, useRef } from "react";
import { api, type Job } from "../api";
import { ago, duration, go, Progress, ResultView, StatusBadge, usePoll } from "../util";

function JobDetail({ id }: { id: string }) {
  const { data: job, error } = usePoll(() => api.job(id), 1000, [id]);
  const logRef = useRef<HTMLPreElement>(null);
  const logs = job?.logs ?? "";

  useEffect(() => {
    const el = logRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [logs]);

  if (error && !job) return <div className="card err">{error}</div>;
  if (!job) return <div className="card muted">Loading…</div>;
  const active = job.status === "running" || job.status === "queued";

  return (
    <div className="card detail">
      <div className="card-head">
        <div>
          <div className="card-title">
            {job.tool} <StatusBadge status={job.status} />
          </div>
          <div className="muted mono small">
            {job.id} · {job.worker} · from {job.source ?? "?"} · {ago(job.created)} · {duration(job)}
          </div>
        </div>
        <span className="spacer" />
        {active && (
          <button className="btn danger small" onClick={() => api.cancel(job.id)}>
            Cancel
          </button>
        )}
        <button className="btn small" onClick={() => go("run", job.tool)} title="Open the form for this tool">
          Run again
        </button>
      </div>
      {job.status === "running" && <Progress value={job.progress} />}
      {job.error && <div className="err block">{job.error}</div>}
      <h3>Parameters</h3>
      <pre className="params">{JSON.stringify(job.params, null, 2)}</pre>
      {job.result != null && (
        <>
          <h3>Result</h3>
          <ResultView value={job.result} />
        </>
      )}
      <h3>Log</h3>
      <pre className="log" ref={logRef}>
        {logs || (job.status === "queued" ? "Waiting for its worker…" : "(empty)")}
      </pre>
    </div>
  );
}

export default function Jobs({ selected }: { selected?: string }) {
  const { data: jobs, error } = usePoll(() => api.jobs(100), 2000);

  return (
    <div className={`split ${selected ? "has-detail" : ""}`}>
      <section className="card list">
        <h2>Jobs</h2>
        {error && <div className="err">{error}</div>}
        {jobs && jobs.length === 0 && <p className="muted">No jobs yet. Start one from “Run a tool”, or let Claude call the toolbox.</p>}
        <table className="jobs">
          <thead>
            <tr>
              <th>Tool</th>
              <th>Status</th>
              <th>Started</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {(jobs ?? []).map((j: Job) => (
              <tr key={j.id} className={j.id === selected ? "selected" : ""} onClick={() => go("jobs", j.id)}>
                <td>
                  <div>{j.tool}</div>
                  <div className="muted mono small">{j.id}</div>
                </td>
                <td>
                  <StatusBadge status={j.status} />
                  {j.status === "running" && <Progress value={j.progress} />}
                </td>
                <td className="muted">{ago(j.created)}</td>
                <td className="muted">{duration(j)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {selected && <JobDetail id={selected} />}
    </div>
  );
}
