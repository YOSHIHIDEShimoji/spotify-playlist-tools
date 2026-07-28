// 生涯履歴（2019〜）の逆引き用フック。
//
// lifetime_tracks.json は全4500曲ぶん（gzip 後 240KB 前後）あるので、必要な画面でだけ読む。
// 配列は再生回数の降順で生成済み＝「配列の位置 + 1」がそのまま生涯順位になる。順位を JSON に
// 持たせないのは、同じ情報を二重に持って食い違うのを避けるため。
import { useMemo } from "react";
import { useJson } from "./data";
import type { LifetimeArtist, LifetimeArtists, LifetimeTrack, LifetimeTracks } from "./types";

export interface RankedLifetimeTrack extends LifetimeTrack {
  rank: number;
}
export interface RankedLifetimeArtist extends LifetimeArtist {
  rank: number;
}

/** 曲の生涯集計。id 逆引き（この曲は生涯何回・何位か）付き。 */
export function useLifetimeTracks() {
  const res = useJson<LifetimeTracks>("lifetime_tracks");
  const ranked = useMemo<RankedLifetimeTrack[]>(
    () => (res.data?.tracks ?? []).map((t, i) => ({ ...t, rank: i + 1 })),
    [res.data],
  );
  const byId = useMemo(() => new Map(ranked.map((t) => [t.id, t])), [ranked]);
  return { ...res, tracks: ranked, byId, totals: res.data?.totals ?? null };
}

/** アーティストの生涯集計。名前（小文字）で逆引きできる。 */
export function useLifetimeArtists() {
  const res = useJson<LifetimeArtists>("lifetime_artists");
  const ranked = useMemo<RankedLifetimeArtist[]>(
    () => (res.data?.artists ?? []).map((a, i) => ({ ...a, rank: i + 1 })),
    [res.data],
  );
  const byName = useMemo(
    () => new Map(ranked.map((a) => [a.name.toLowerCase(), a])),
    [ranked],
  );
  return { ...res, artists: ranked, byName };
}

/** ミリ秒 → 「◯時間」「◯分」。生涯合計のような大きい値は日数も添える。 */
export function formatDuration(ms: number, lang: string, withDays = false): string {
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return lang === "ja" ? `${minutes}分` : `${minutes} min`;
  const hours = Math.round(ms / 3600000);
  if (!withDays || hours < 48) {
    return lang === "ja" ? `${hours.toLocaleString()}時間` : `${hours.toLocaleString()} hr`;
  }
  const days = Math.floor(hours / 24);
  const rest = hours % 24;
  return lang === "ja"
    ? `${hours.toLocaleString()}時間（${days}日と${rest}時間）`
    : `${hours.toLocaleString()} hr (${days}d ${rest}h)`;
}

/** 完走率（%）。short（30秒未満でやめた回数）が無い曲は null＝表示しない。 */
export function finishRate(track: { count: number; short?: number }): number | null {
  if (!track.short) return null;
  const total = track.count + track.short;
  return total > 0 ? Math.round((track.count / total) * 100) : null;
}

/** years マップ → 年昇順の配列（グラフ用）。抜けている年は 0 で埋める。 */
export function yearSeries(years: Record<string, number>): { year: string; count: number }[] {
  const keys = Object.keys(years);
  if (keys.length === 0) return [];
  const from = Math.min(...keys.map(Number));
  const to = Math.max(...keys.map(Number));
  const out: { year: string; count: number }[] = [];
  for (let y = from; y <= to; y++) {
    const key = String(y);
    out.push({ year: key, count: years[key] ?? 0 });
  }
  return out;
}
