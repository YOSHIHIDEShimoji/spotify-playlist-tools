// 多言語対応。デフォルトは英語。URL の ?lang=ja で日本語に切り替える。
// 文言は各所で t(en, ja) を呼んで出し分ける（別辞書を持たず、呼び出し側に両言語を置く）。
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type Lang = "en" | "ja";

/** URL の ?lang=ja（jp/japanese も可）なら日本語。無ければ英語（デフォルト）。 */
export function detectLang(): Lang {
  try {
    const v = new URLSearchParams(window.location.search).get("lang");
    return v && ["ja", "jp", "japanese"].includes(v.toLowerCase()) ? "ja" : "en";
  } catch {
    return "en";
  }
}

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
}
const Ctx = createContext<LangCtx>({ lang: "en", setLang: () => {} });

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectLang);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      const url = new URL(window.location.href);
      if (l === "ja") url.searchParams.set("lang", "ja");
      else url.searchParams.delete("lang");
      window.history.replaceState(null, "", url.toString());
    } catch {
      /* URL 更新失敗は無視（表示は切り替わる） */
    }
    document.documentElement.lang = l;
  }, []);
  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang(): LangCtx {
  return useContext(Ctx);
}

/** t(en, ja) — 現在の言語の文字列を返す（デフォルト英語）。 */
export function useT(): (en: string, ja: string) => string {
  const { lang } = useContext(Ctx);
  return (en, ja) => (lang === "ja" ? ja : en);
}

// ── ロケール対応の日付ヘルパー ──
const DOW_JA = ["月", "火", "水", "木", "金", "土", "日"];
const DOW_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export function dowLabels(lang: Lang): string[] {
  return lang === "ja" ? DOW_JA : DOW_EN;
}

const MONTH_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "YYYY-MM-DD…" → ja "M月D日" / en "Mon D"。 */
export function monthDay(iso: string, lang: Lang): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const mo = Number(m[2]);
  const day = Number(m[3]);
  return lang === "ja" ? `${mo}月${day}日` : `${MONTH_EN[mo - 1]} ${day}`;
}

/** "YYYY-MM…" → ja "YYYY年M月" / en "Mon YYYY"。生涯履歴のように年をまたぐ起点の表示用。 */
export function monthYear(iso: string, lang: Lang): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const year = Number(m[1]);
  const mo = Number(m[2]);
  return lang === "ja" ? `${year}年${mo}月` : `${MONTH_EN[mo - 1]} ${year}`;
}
