import { useEffect, useState } from "react";

const BASE = import.meta.env.BASE_URL; // 通常 "/"

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${BASE}data/${name}.json`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${name}.json: ${res.status}`);
  return (await res.json()) as T;
}

async function fetchJsonl<T>(name: string): Promise<T[]> {
  const res = await fetch(`${BASE}data/${name}.jsonl`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${name}.jsonl: ${res.status}`);
  const text = await res.text();
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as T);
}

type State<T> = { data: T | null; error: string | null; loading: boolean };

// 同じデータファイルを複数の場所から読むときに、取得を1回に束ねるキャッシュ。
// 生涯ランキング（lifetime_tracks.json）は 1MB を超えるうえ、一覧・曲の詳細・
// アーティストの詳細がそれぞれ useJson を呼ぶので、束ねないとモーダルを開くたびに
// 取り直しになる。世代番号を上げると次の load で取り直す（＝タブ復帰時の更新）。
let generation = 0;
const cache = new Map<string, { gen: number; promise: Promise<unknown> }>();

function loadShared<T>(name: string, fetcher: (n: string) => Promise<T>): Promise<T> {
  const hit = cache.get(name);
  if (hit && hit.gen === generation) return hit.promise as Promise<T>;
  const promise = fetcher(name).catch((e) => {
    // 失敗は覚えない（次に見に来たときに再試行できるように）
    if (cache.get(name)?.promise === promise) cache.delete(name);
    throw e;
  });
  cache.set(name, { gen: generation, promise });
  return promise;
}

if (typeof document !== "undefined") {
  // 各フックの listener より先に登録されるので、世代の更新が必ず先に走る。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") generation += 1;
  });
}

// タブが可視に戻ったら再フェッチする共通フック（L-5: 楽観的 UI の解消に手動リロードを不要に）。
function useFetching<T>(name: string, fetcher: (n: string) => Promise<T>, kind: string): State<T> {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    // json と jsonl は同じ名前でも別ファイルなので、キャッシュのキーを分ける
    const load = () =>
      loadShared(`${kind}:${name}`, () => fetcher(name))
        .then((data) => alive && setState({ data, error: null, loading: false }))
        .catch((e) => alive && setState((s) => ({ ...s, error: String(e), loading: false })));
    load();
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [name, fetcher, kind]);
  return state;
}

/** JSON データファイルを読む React フック。data ブランチ由来の public/data を参照。 */
export function useJson<T>(name: string): State<T> {
  return useFetching<T>(name, fetchJson, "json");
}

/** JSONL データファイル（runs / stats_history）を読む React フック。 */
export function useJsonl<T>(name: string): State<T[]> {
  return useFetching<T[]>(name, fetchJsonl, "jsonl");
}
