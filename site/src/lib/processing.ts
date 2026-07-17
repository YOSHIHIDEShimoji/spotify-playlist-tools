import { useSyncExternalStore } from "react";

// 楽観的 UI（dashboard-design §7.4・レビュー M2）。dispatch した対象 id を localStorage に
// 「処理中」として保持し、ボタンを無効化・二重送信を防ぐ。データ更新で当該グループが消えたら
// 呼び出し側が clearProcessing で解消する。
const KEY = "spotify-dashboard-processing";
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
  return useSyncExternalStore(subscribe, getSnapshot, () => ({}));
}
