// 生涯履歴（2019〜）の逆引き用フック。
//
// lifetime_tracks.json は全4500曲ぶん（gzip 後 240KB 前後）あるので、必要な画面でだけ読む。
// 配列は再生回数の降順で生成済み＝「配列の位置 + 1」がそのまま生涯順位になる。順位を JSON に
// 持たせないのは、同じ情報を二重に持って食い違うのを避けるため。
import { useMemo } from "react";
import { useJson } from "./data";
import type { LifetimeArtist, LifetimeArtists, LifetimeTrack, LifetimeTracks, SearchIndex } from "./types";

export interface RankedLifetimeTrack extends LifetimeTrack {
  rank: number;
}
export interface RankedLifetimeArtist extends LifetimeArtist {
  rank: number;
}

/** 曲ID → アルバムアート。生涯データに画像が入るまでの間、検索インデックスから補完する。
 * （拡張履歴は曲名と ID しか持たないので、夜間に /v1/tracks で埋まるまでは無印になる） */
export function useTrackArt(): (id: string, image?: string | null) => string | null {
  const search = useJson<SearchIndex>("search_index");
  const byId = useMemo(
    () => new Map((search.data?.tracks ?? []).map((t) => [t.id, t.image ?? null] as const)),
    [search.data],
  );
  return (id, image) => image ?? byId.get(id) ?? null;
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

/** ミリ秒 → 「◯時間」「◯分」。数字を大きく1行で見せる用（主表示）。 */
export function formatDuration(ms: number, lang: string): string {
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return lang === "ja" ? `${minutes}分` : `${minutes} min`;
  const hours = Math.round(ms / 3600000);
  return lang === "ja" ? `${hours.toLocaleString()}時間` : `${hours.toLocaleString()} hr`;
}

/** ミリ秒 → 「◯年◯日」「◯日◯時間」「◯時間◯分」。主表示の下に添える体感スケール。
 * 3,455時間と言われてもピンと来ないので、日や年に直したものを併記する。 */
export function formatDurationLong(ms: number, lang: string): string {
  const totalMinutes = Math.floor(ms / 60000);
  if (totalMinutes < 60) return "";
  const totalHours = Math.floor(totalMinutes / 60);
  if (totalHours < 24) {
    const m = totalMinutes % 60;
    return lang === "ja" ? `${totalHours}時間${m}分` : `${totalHours}h ${m}m`;
  }
  const totalDays = Math.floor(totalHours / 24);
  if (totalDays < 365) {
    const h = totalHours % 24;
    return lang === "ja" ? `${totalDays}日と${h}時間` : `${totalDays}d ${h}h`;
  }
  const years = Math.floor(totalDays / 365);
  const days = totalDays % 365;
  return lang === "ja" ? `${years}年と${days}日` : `${years}y ${days}d`;
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
