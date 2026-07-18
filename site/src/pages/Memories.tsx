import { useMemo } from "react";
import { useJson } from "../lib/data";
import type { ArchiveWeekly, SearchIndex, Wrapped, WrappedIndex } from "../lib/types";
import { Empty, Loading, Section, StatCard } from "../components/ui";
import { PlayButton } from "../lib/player";

const DOW = ["月", "火", "水", "木", "金", "土", "日"];

// 曲ID → アルバムアート URL。search_index から補完する（管理プレイリストに在る曲は必ず出る）。
function useTrackImages(): Map<string, string | null> {
  const search = useJson<SearchIndex>("search_index");
  return useMemo(
    () => new Map((search.data?.tracks ?? []).map((t) => [t.id, t.image ?? null] as const)),
    [search.data],
  );
}

// 現在の ISO 週（JST）を "YYYY-Www" で返す。1年前の同じ週を archive_weekly から探す。
function isoWeekLabel(d: Date): string {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

// "YYYY-Www" → "M/D–M/D"（その週の月〜日）。ISO 週番号より直感的に。
function isoWeekRange(isoWeek: string): string {
  const m = /^(\d{4})-W(\d{2})$/.exec(isoWeek);
  if (!m) return "";
  const year = Number(m[1]);
  const week = Number(m[2]);
  // ISO 第1週は 1/4 を含む週。その週の月曜から (week-1)*7 日進める。
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const jan4Day = jan4.getUTCDay() || 7;
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - (jan4Day - 1) + (week - 1) * 7);
  const sunday = new Date(monday);
  sunday.setUTCDate(monday.getUTCDate() + 6);
  const fmt = (x: Date) => `${x.getUTCMonth() + 1}/${x.getUTCDate()}`;
  return `${fmt(monday)}–${fmt(sunday)}`;
}

// 再生ボタン付きのトラック行（タップで画面下の常駐プレイヤーが鳴る）。アルバムアート付き。
function TrackRow(
  { track, rank, image }:
    { track: { id: string; name: string; artists: string[] }; rank?: number; image?: string | null },
) {
  return (
    <div className="list-row">
      {rank != null && <span className="list-rank">{rank}</span>}
      {image ? (
        <img className="cand-art top-art" src={image} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art" aria-hidden />
      )}
      <span className="list-main">
        <div className="name">{track.name}</div>
        <div className="t-small">{track.artists.join(", ")}</div>
      </span>
      <PlayButton uri={`spotify:track:${track.id}`} label={`${track.name} を再生`} />
    </div>
  );
}

export function Memories() {
  const weekly = useJson<ArchiveWeekly>("archive_weekly");
  const img = useTrackImages();

  const lastYear = new Date();
  lastYear.setFullYear(lastYear.getFullYear() - 1);
  const targetWeek = isoWeekLabel(lastYear);

  const match = weekly.data?.weeks.find((w) => w.iso_week === targetWeek);
  const recent = [...(weekly.data?.weeks ?? [])].reverse().slice(0, 6);

  return (
    <>
      <Section title={`1年前の今週（${isoWeekRange(targetWeek)}）`}>
        {weekly.loading ? (
          <Loading />
        ) : !match ? (
          <Empty>該当する週のデータがまだありません（Top50 アーカイブが1年分たまると出ます）。</Empty>
        ) : (
          <div className="card">
            {match.tracks.map((t) => (
              <TrackRow key={t.id} track={{ id: t.id, name: t.name, artists: t.artists }} image={img.get(t.id)} />
            ))}
          </div>
        )}
      </Section>

      <Section title="最近アーカイブ入りした週">
        {weekly.loading ? (
          <Loading />
        ) : recent.length === 0 ? (
          <Empty>まだアーカイブ週がありません。</Empty>
        ) : (
          recent.map((w) => (
            <div className="card" key={w.iso_week} style={{ marginBottom: "var(--sp-3)" }}>
              <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>
                {isoWeekRange(w.iso_week)} の週 <code className="muted">{w.iso_week}</code> · {w.tracks.length}曲
              </div>
              {w.tracks.slice(0, 8).map((t) => (
                <TrackRow key={t.id} track={{ id: t.id, name: t.name, artists: t.artists }} image={img.get(t.id)} />
              ))}
            </div>
          ))
        )}
      </Section>

      <Section title="月間 Wrapped">
        <WrappedBlock />
      </Section>
    </>
  );
}

function WrappedBlock() {
  const idx = useJson<WrappedIndex>("wrapped/index");
  if (idx.loading) return <Loading />;
  const month = idx.data?.months?.[0];
  if (!month) return <Empty>毎月末に自動生成されます（今月のTop曲・新規追加・ピーク時間帯）。</Empty>;
  return <WrappedMonth month={month} />;
}

function WrappedMonth({ month }: { month: string }) {
  const w = useJson<Wrapped>(`wrapped/${month}`);
  const img = useTrackImages();
  if (w.loading) return <Loading />;
  if (!w.data) return <Empty>{month} のデータを読めませんでした。</Empty>;
  const d = w.data;
  return (
    <>
      <div className="row" style={{ marginBottom: "var(--sp-3)" }}>
        <StatCard label={`${d.month} の再生`} value={d.plays.toLocaleString()} sub={`新規追加 ${d.new_tracks}曲`} />
        {d.peak && <StatCard label="ピーク時間帯" value={`${DOW[d.peak.dow]} ${d.peak.hour}時`} />}
      </div>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="card" style={{ flex: "1 1 260px" }}>
          <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>Top 曲</div>
          {d.top_tracks.map((t, i) => (
            <TrackRow key={t.track_id} rank={i + 1} track={{ id: t.track_id, name: t.name, artists: t.artists }} image={img.get(t.track_id)} />
          ))}
        </div>
        <div className="card" style={{ flex: "1 1 260px" }}>
          <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>Top アーティスト</div>
          {d.top_artists.map((a, i) => (
            <div className="list-row" key={a.name}>
              <span className="list-rank">{i + 1}</span>
              <span className="list-main"><div className="name">{a.name}</div></span>
              <span className="list-count">{a.count}回</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
