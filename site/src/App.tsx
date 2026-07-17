import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { AuthBanner } from "./components/AuthBanner";
import { clearPat, setPat, usePat } from "./lib/pat";
import { verifyPat } from "./lib/github";
import { Home } from "./pages/Home";
import { Organize } from "./pages/Organize";
import { StatsPage } from "./pages/Stats";
import { Discover } from "./pages/Discover";
import { Memories } from "./pages/Memories";
import { SearchPage } from "./pages/Search";

const NAV = [
  { to: "/", label: "ホーム", end: true },
  { to: "/organize", label: "整理" },
  { to: "/stats", label: "統計" },
  { to: "/discover", label: "おすすめ" },
  { to: "/memories", label: "思い出" },
  { to: "/search", label: "検索" },
];

export function App() {
  const pat = usePat();
  const [showSettings, setShowSettings] = useState(false);

  return (
    <div className="shell">
      <header className="app-header">
        <span className="app-logo"><span className="mark" aria-hidden>◗</span>Spotify Dashboard</span>
        <span className="spacer" />
        <button
          className={"pill" + (pat ? " is-active" : "")}
          onClick={() => setShowSettings((s) => !s)}
          title={pat ? "操作が有効" : "PAT 未設定（閲覧のみ）"}
        >
          {pat ? "操作 ON" : "操作 OFF"}
        </button>
      </header>

      {showSettings && <PatSettings onDone={() => setShowSettings(false)} hasPat={!!pat} />}

      <nav className="nav">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => "pill" + (isActive ? " is-active" : "")}
          >
            {n.label}
          </NavLink>
        ))}
      </nav>

      <AuthBanner />

      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/organize" element={<Organize />} />
        <Route path="/stats" element={<StatsPage />} />
        <Route path="/discover" element={<Discover />} />
        <Route path="/memories" element={<Memories />} />
        <Route path="/search" element={<SearchPage />} />
      </Routes>
    </div>
  );
}

function PatSettings({ onDone, hasPat }: { onDone: () => void; hasPat: boolean }) {
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setMsg(null);
    const ok = await verifyPat(value.trim());
    setBusy(false);
    if (ok) {
      setPat(value.trim());
      setMsg("保存しました。操作が有効になりました。");
      setTimeout(onDone, 800);
    } else {
      setMsg("疎通に失敗。PAT の権限（Actions: Read and write）と対象リポジトリを確認してください。");
    }
  }

  return (
    <div className="card" style={{ marginBottom: "var(--sp-4)" }}>
      <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>操作トークン（fine-grained PAT）</div>
      <p className="t-small" style={{ marginTop: 0 }}>
        重複削除・unknown 振り分けをサイトから実行するための GitHub PAT。ブラウザ内（localStorage）にのみ保存し、
        リポジトリや URL には送りません。権限は対象リポジトリの <code>Actions: Read and write</code> のみで十分です。
      </p>
      <div className="pat-box">
        <input
          className="pat-input"
          type="password"
          placeholder="github_pat_..."
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button className="pill pill-green" disabled={!value.trim() || busy} onClick={save}>
          {busy ? "確認中…" : "保存"}
        </button>
        {hasPat && (
          <button className="pill" onClick={() => { clearPat(); onDone(); }}>
            削除
          </button>
        )}
      </div>
      {msg && <div className="t-small" style={{ marginTop: "var(--sp-2)" }}>{msg}</div>}
    </div>
  );
}
