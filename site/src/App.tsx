import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AuthBanner } from "./components/AuthBanner";
import { ScrollRow } from "./components/ui";
import { useJson } from "./lib/data";
import type { AuthStatus } from "./lib/types";
import { clearPat, setPat, usePat } from "./lib/pat";
import { verifyPat } from "./lib/github";
import { useLang, useT } from "./lib/i18n";
import { Home } from "./pages/Home";
import { Organize } from "./pages/Organize";
import { StatsPage } from "./pages/Stats";
import { Discover } from "./pages/Discover";
import { Memories } from "./pages/Memories";
import { SearchPage } from "./pages/Search";

// データ最終更新（夜間ラン等の sitegen 実行時刻）を JST の「M/D HH:MM」で（24h・言語非依存）。
function jstStamp(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-GB", {
    timeZone: "Asia/Tokyo",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

const NAV = [
  { to: "/", en: "Home", ja: "ホーム", end: true },
  { to: "/organize", en: "Organize", ja: "整理" },
  { to: "/stats", en: "Stats", ja: "統計" },
  { to: "/discover", en: "Discover", ja: "おすすめ" },
  { to: "/memories", en: "Memories", ja: "思い出" },
  { to: "/search", en: "Search", ja: "検索" },
];

export function App() {
  const pat = usePat();
  const t = useT();
  const [showSettings, setShowSettings] = useState(false);
  const location = useLocation();
  const auth = useJson<AuthStatus>("auth_status"); // 最終更新の表示に使う
  const updated = jstStamp(auth.data?.checked_at);

  // ルート変更（ディープリンク／リロード含む）時、横スクロールするナビ内でアクティブな
  // ピルを中央寄せに。scroller の scrollLeft を直接指定するのでページ縦スクロールは動かさない。
  // 初回マウントはレイアウト確定を待つため少し遅らせる。
  useEffect(() => {
    const id = window.setTimeout(() => {
      const active = document.querySelector<HTMLElement>(".nav a.is-active");
      const scroller = active?.closest<HTMLElement>(".nav");
      if (!active || !scroller) return;
      const target = active.offsetLeft - (scroller.clientWidth - active.offsetWidth) / 2;
      scroller.scrollTo({ left: Math.max(0, target), behavior: "auto" });
    }, 150);
    return () => window.clearTimeout(id);
  }, [location.pathname]);

  return (
    <div className="shell">
      <header className="app-header">
        <span className="app-logo">
          <span className="mark" aria-hidden>
            <svg viewBox="42 42 172 172" width="26" height="26">
              <circle cx="128" cy="128" r="86" fill="#1ED760" />
              <g fill="none" stroke="#121212" strokeLinecap="round">
                <path strokeWidth="15" d="M82 108 q46 -14 92 10" />
                <path strokeWidth="12.5" d="M86 134 q42 -12 82 9" />
                <path strokeWidth="10" d="M90 158 q36 -10 70 8" />
              </g>
            </svg>
          </span>
          Spotify Dashboard
        </span>
        {updated && (
          <span className="app-updated" title={t("Data last updated (JST)", "データの最終更新（JST）")}>
            <span className="upd-label">{t("Updated ", "更新 ")}</span>{updated}
          </span>
        )}
        <span className="spacer" />
        <LangToggle />
        <button
          className={"pill" + (pat ? " is-active" : "")}
          onClick={() => setShowSettings((s) => !s)}
          title={pat ? t("Operations enabled", "操作が有効") : t("No PAT (view only)", "PAT 未設定（閲覧のみ）")}
        >
          {pat ? t("Ops ON", "操作 ON") : t("Ops OFF", "操作 OFF")}
        </button>
      </header>

      {showSettings && <PatSettings onDone={() => setShowSettings(false)} hasPat={!!pat} />}

      <ScrollRow className="nav" role="navigation" ariaLabel={t("Main navigation", "メインナビ")}>
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => "pill" + (isActive ? " is-active" : "")}
          >
            {t(n.en, n.ja)}
          </NavLink>
        ))}
      </ScrollRow>

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

function LangToggle() {
  const { lang, setLang } = useLang();
  return (
    <div className="lang-toggle" role="group" aria-label="Language">
      <button className={lang === "en" ? "is-active" : ""} aria-pressed={lang === "en"} onClick={() => setLang("en")}>
        EN
      </button>
      <button className={lang === "ja" ? "is-active" : ""} aria-pressed={lang === "ja"} onClick={() => setLang("ja")}>
        日本語
      </button>
    </div>
  );
}

function PatSettings({ onDone, hasPat }: { onDone: () => void; hasPat: boolean }) {
  const t = useT();
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
      setMsg(t("Saved. Operations are now enabled.", "保存しました。操作が有効になりました。"));
      setTimeout(onDone, 800);
    } else {
      setMsg(t(
        "Could not connect. Check the PAT scope (Actions: Read and write) and the target repository.",
        "疎通に失敗。PAT の権限（Actions: Read and write）と対象リポジトリを確認してください。",
      ));
    }
  }

  return (
    <div className="card" style={{ marginBottom: "var(--sp-4)" }}>
      <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>
        {t("Operation token (fine-grained PAT)", "操作トークン（fine-grained PAT）")}
      </div>
      <p className="t-small" style={{ marginTop: 0 }}>
        {t(
          "A GitHub PAT to run dedupe removals and unknown sorting from the site. Stored only in your browser (localStorage) — never sent to the repo or a URL. Only Actions: Read and write on the target repository is required. Once saved it survives a reload (including ⌘⇧R). On mobile, paste it into the same field and save.",
          "重複削除・unknown 振り分けをサイトから実行するための GitHub PAT。ブラウザ内（localStorage）にのみ保存し、リポジトリや URL には送りません。権限は対象リポジトリの Actions: Read and write のみで十分です。一度保存すれば再読み込み（⌘⇧R 含む）では消えません。スマホでも同じ欄に貼り付けて保存すれば有効になります。",
        )}
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
          {busy ? t("Checking…", "確認中…") : t("Save", "保存")}
        </button>
        {hasPat && (
          <button className="pill" onClick={() => { clearPat(); onDone(); }}>
            {t("Remove", "削除")}
          </button>
        )}
      </div>
      {msg && <div className="t-small" style={{ marginTop: "var(--sp-2)" }}>{msg}</div>}
    </div>
  );
}
