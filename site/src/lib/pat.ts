import { useSyncExternalStore } from "react";

// fine-grained PAT はブラウザ localStorage のみに保持する（dashboard-design §7.3-3）。
// URL・データファイルには絶対に載せない。
const KEY = "spotify-dashboard-pat";
const listeners = new Set<() => void>();

export function getPat(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setPat(token: string): void {
  localStorage.setItem(KEY, token.trim());
  listeners.forEach((l) => l());
}

export function clearPat(): void {
  localStorage.removeItem(KEY);
  listeners.forEach((l) => l());
}

// スマホ等で URL からトークンを流し込むための入口。`#token=...`（推奨・サーバーに送られない）か
// `?token=...` を読んで localStorage に保存し、直後に URL から消す（履歴・アドレスバーに残さない）。
// ルートは維持する。App マウント前（main.tsx）に1回だけ呼ぶ。
export function adoptPatFromUrl(): void {
  try {
    const loc = window.location;
    const grab = (s: string) => {
      const m = /(?:[?#&]|^)(?:token|pat)=([^&]+)/.exec(s);
      return m ? decodeURIComponent(m[1]).trim() : null;
    };
    const token = grab(loc.hash) || grab(loc.search);
    if (!token) return;
    localStorage.setItem(KEY, token);
    listeners.forEach((l) => l());
    const cleanHash = loc.hash
      .replace(/(?:[?#&])(?:token|pat)=[^&]*/g, "")
      .replace(/^[#&]+/, "");
    const route = cleanHash.startsWith("/") ? "#" + cleanHash : "#/";
    window.history.replaceState(null, "", loc.pathname + route);
  } catch {
    /* localStorage 不可（プライベートブラウズ等）は黙って無視＝閲覧のみになる */
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** PAT の有無をリアクティブに購読する。未設定なら操作 UI を無効化する。 */
export function usePat(): string | null {
  return useSyncExternalStore(subscribe, getPat, () => null);
}
