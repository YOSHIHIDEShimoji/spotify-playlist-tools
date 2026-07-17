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

// タブが可視に戻ったら再フェッチする共通フック（L-5: 楽観的 UI の解消に手動リロードを不要に）。
function useFetching<T>(name: string, fetcher: (n: string) => Promise<T>): State<T> {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetcher(name)
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
  }, [name, fetcher]);
  return state;
}

/** JSON データファイルを読む React フック。data ブランチ由来の public/data を参照。 */
export function useJson<T>(name: string): State<T> {
  return useFetching<T>(name, fetchJson);
}

/** JSONL データファイル（runs / stats_history）を読む React フック。 */
export function useJsonl<T>(name: string): State<T[]> {
  return useFetching<T[]>(name, fetchJsonl);
}
