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

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** PAT の有無をリアクティブに購読する。未設定なら操作 UI を無効化する。 */
export function usePat(): string | null {
  return useSyncExternalStore(subscribe, getPat, () => null);
}
