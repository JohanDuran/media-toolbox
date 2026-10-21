import { api } from "./api";
import Jobs from "./pages/Jobs";
import Overview from "./pages/Overview";
import Run from "./pages/Run";
import Sounds from "./pages/Sounds";
import { useHashRoute, usePoll } from "./util";

const TABS = [
  ["overview", "Overview"],
  ["jobs", "Jobs"],
  ["run", "Run a tool"],
  ["sounds", "Sound library"],
] as const;

export default function App() {
  const route = useHashRoute();
  const page = route[0] ?? "overview";
  const state = usePoll(api.state, 2000);
  const running = state.data?.counts.running ?? 0;
  const queued = state.data?.counts.queued ?? 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">M</span>
          <span>Media Toolbox</span>
        </div>
        <nav>
          {TABS.map(([id, label]) => (
            <a key={id} href={`#/${id}`} className={page === id ? "active" : ""}>
              {label}
              {id === "jobs" && running + queued > 0 && <span className="pill">{running + queued}</span>}
            </a>
          ))}
        </nav>
        <div className={`conn ${state.error ? "down" : "up"}`} title={state.error ?? "Connected to the gateway"}>
          <span className="dot" />
          {state.error ? "Gateway offline" : "Connected"}
        </div>
      </header>
      <main>
        {page === "overview" && <Overview state={state.data} />}
        {page === "jobs" && <Jobs selected={route[1]} />}
        {page === "run" && <Run toolName={route[1]} />}
        {page === "sounds" && <Sounds />}
      </main>
    </div>
  );
}
