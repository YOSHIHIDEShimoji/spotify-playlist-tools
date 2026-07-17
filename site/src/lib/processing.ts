import { useSyncExternalStore } from "react";

// 楽観的 UI（dashboard-design §7.4・レビュー M2）。dispatch した対象 id を localStorage に
// 「処理中」として保持し、ボタンを無効化・二重送信を防ぐ。データ更新で当該グループが消えたら
// 呼び出し側が clearProcessing で解消する。
const KEY = "spotify-dashboard-processing";
const EMPTY: Record<string, string> = {}; // 安定参照（L-4: SSR/再描画で新オブジェクトを返さない）
export const PROCESSING_TIMEOUT_MS = 30 * 60 * 1000; // 30分で「反映確認できず」と見なす（M-1）
const listeners = new Set<() => void>();

function read(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

function write(map: Record<string, string>) {
  localStorage.setItem(KEY, JSON.stringify(map));
  listeners.forEach((l) => l());
}

export function markProcessing(id: string): void {
  const m = read();
  m[id] = new Date().toISOString();
  write(m);
}

export function clearProcessing(ids: string[]): void {
  const m = read();
  let changed = false;
  for (const id of ids) {
    if (id in m) {
      delete m[id];
      changed = true;
    }
  }
  if (changed) write(m);
}

let cachedRaw = "";
let cachedObj: Record<string, string> = {};

function getSnapshot(): Record<string, string> {
  const raw = (() => {
    try {
      return localStorage.getItem(KEY) || "{}";
    } catch {
      return "{}";
    }
  })();
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cachedObj = JSON.parse(raw);
    } catch {
      cachedObj = {};
    }
  }
  return cachedObj;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useProcessing(): Record<string, string> {
  return useSyncExternalStore(subscribe, getSnapshot, () => EMPTY);
}

/** 経過時間が PROCESSING_TIMEOUT_MS を超えた id 群（反映確認できず＝手詰まり防止・M-1）。 */
export function stuckIds(map: Record<string, string>, now: number): string[] {
  return Object.entries(map)
    .filter(([, iso]) => now - new Date(iso).getTime() > PROCESSING_TIMEOUT_MS)
    .map(([id]) => id);
}
