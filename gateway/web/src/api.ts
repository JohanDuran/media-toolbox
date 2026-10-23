export type Worker = {
  name: string;
  title: string;
  gpu: boolean;
  up: boolean;
  busy: boolean;
  job_id: string | null;
  model_loaded: boolean;
  error: string | null;
  info: Record<string, unknown> | null;
  checked: number | null;
};

export type Gpu = {
  name?: string;
  memory_used_mb?: number;
  memory_total_mb?: number;
  utilization?: number;
  temperature_c?: number;
  error?: string;
};

export type State = {
  workers: Worker[];
  gpu: Gpu | null;
  counts: Record<string, number>;
  config: {
    host_home: string;
    outputs_dir: string;
    sfx_library: string;
    protected_dirs: string[];
    mcp_url: string;
  };
};

export type Param = {
  name: string;
  kind: string;
  label: string;
  required: boolean;
  default: unknown;
  help: string;
  options: string[] | null;
};

export type Tool = {
  name: string;
  title: string;
  worker: string;
  action: string;
  gpu: boolean;
  description: string;
  params: Param[];
};

export type Job = {
  id: string;
  tool: string;
  worker: string;
  gpu: number;
  source: string | null;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";
  params: Record<string, unknown> | null;
  result: unknown;
  error: string | null;
  logs?: string;
  progress: number | null;
  created: number;
  started: number | null;
  finished: number | null;
};

export type Sound = {
  file: string;
  path: string;
  prompt?: string;
  seconds?: number;
  seed?: number;
  variation?: number;
  peak_db?: number;
  created?: string;
  status?: "new" | "keep" | "reject";
  used_in?: string[];
  tags?: string[];
  notes?: string;
};

export type Built = { path: string; exists: boolean; mtime: number | null };

export type ShortProject = {
  project_dir: string;
  name: string;
  title: string;
  length_s?: number;
  shots?: number;
  error: string | null;
  preview: Built;
};

export type ShortShot = {
  name: string;
  start: number;
  end: number;
  record_frame: number;
  frames: number;
  layers: number;
  note: string;
  file: Built;
  thumb: Built;
};

export type ShortInfo = {
  title: string;
  project_dir: string;
  length_s: number;
  frames: number;
  anchors: Record<string, number>;
  shots: ShortShot[];
  graphics_list: { start: number; end: number; text: string }[];
  outputs: Record<"overlay" | "narration" | "bed" | "preview" | "sheet", Built>;
};

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep the status text */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  state: () => call<State>("/api/state"),
  tools: () => call<Tool[]>("/api/tools"),
  jobs: (limit = 100) => call<Job[]>(`/api/jobs?limit=${limit}`),
  job: (id: string) => call<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  submit: (tool: string, params: Record<string, unknown>) => call<Job>("/api/jobs", post({ tool, params })),
  cancel: (id: string) => call<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  unload: (worker: string) => call<unknown>(`/api/workers/${worker}/unload`, { method: "POST" }),
  library: (q: string, status: string) =>
    call<Sound[]>(`/api/library?q=${encodeURIComponent(q)}&status=${encodeURIComponent(status)}`),
  mark: (file: string, fields: { status?: string; used_in?: string; tags?: string[]; note?: string }) =>
    call<Sound[]>("/api/library/mark", post({ file, ...fields })),
  scan: () => call<{ cataloged: string[] }>("/api/library/scan", { method: "POST" }),
  shorts: () => call<ShortProject[]>("/api/shorts"),
  shortInfo: (project: string) => call<ShortInfo>(`/api/shorts/info?project=${encodeURIComponent(project)}`),
};

export const fileUrl = (path: string) => `/api/file?path=${encodeURIComponent(path)}`;
