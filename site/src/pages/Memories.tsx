import { useMemo, useState } from "react";
import { useJson } from "../lib/data";
import type { OnThisDay, Rediscover, SearchIndex, Wrapped, WrappedIndex } from "../lib/types";
import { Empty, Loading, Section, StatCard } from "../components/ui";
import { TrackPlayButton } from "../lib/player";
import { ArtistDetail, LifetimeRow, TrackDetail } from "../components/Detail";
import { formatDuration, formatDurationLong, useLifetimeArtists, useLifetimeTracks } from "../lib/lifetime";
import type { RankedLifetimeArtist, RankedLifetimeTrack } from "../lib/lifetime";
import { dowLabels, monthYear, useLang, useT } from "../lib/i18n";

// 曲ID → アルバムアート URL。search_index から補完する（管理プレイリストに在る曲は必ず出る）。
function useTrackImages(): Map<string, string | null> {
  const search = useJson<SearchIndex>("search_index");
  return useMemo(
    () => new Map((search.data?.tracks ?? []).map((t) => [t.id, t.image ?? null] as const)),
    [search.data],
  );
}



// 再生ボタン付きのトラック行（タップで画面下の常駐プレイヤーが鳴る）。アルバムアート付き。
// onOpen があれば曲名部分がタップ可能になり、生涯の詳細ダイアログを開く。
function TrackRow(
  { track, rank, image, onOpen }:
    { track: { id: string; name: string; artists: string[] }; rank?: number; image?: string | null;
      onOpen?: () => void },
) {
  const tx = useT();
  return (
    <div className="list-row">
      {rank != null && <span className="list-rank">{rank}</span>}
      {image ? (
        <img className="cand-art top-art" src={image} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art" aria-hidden />
      )}
      {onOpen ? (
        <button className="list-main list-main--tap" onClick={onOpen}>
          <div className="name">{track.name}</div>
          <div className="t-small">{track.artists.join(", ")}</div>
        </button>
      ) : (
        <span className="list-main">
          <div className="name">{track.name}</div>
          <div className="t-small">{track.artists.join(", ")}</div>
        </span>
      )}
      <TrackPlayButton id={track.id} name={track.name} artists={track.artists} label={tx(`Play ${track.name}`, `${track.name} を再生`)} />
    </div>
  );
}

export function Memories() {
  const tx = useT();
  const [track, setTrack] = useState<RankedLifetimeTrack | null>(null);
  const [artist, setArtist] = useState<RankedLifetimeArtist | null>(null);
  const life = useLifetimeTracks();

  return (
    <>
      <OnThisDayBlock onTrack={(id) => { const t = life.byId.get(id); if (t) setTrack(t); }} />

      <Section title={tx("Monthly & yearly Wrapped", "Wrapped（月間・年間）")}>
        <WrappedBlock onTrack={setTrack} onArtist={setArtist} />
      </Section>

      <RediscoverBlock onTrack={setTrack} />

      {track && <TrackDetail track={track} onClose={() => setTrack(null)} />}
      {artist && (
        <ArtistDetail artist={artist} onClose={() => setArtist(null)}
          onTrack={(t) => { setArtist(null); setTrack(t); }} />
      )}
    </>
  );
}

/** ◯年前の今日。同じ月日に過去の年で聴いていた曲を、新しい年から並べる。 */
function OnThisDayBlock({ onTrack }: { onTrack: (id: string) => void }) {
  const tx = useT();
  const otd = useJson<OnThisDay>("on_this_day");
  const img = useTrackImages();
  const [all, setAll] = useState(false);
  const years = otd.data?.years ?? [];
  // 読み込み中も節の枠を出す。null を返すと、あとから上に挿入されて Wrapped が下へ跳ねる。
  if (otd.loading) {
    return <Section title={tx("On this day", "◯年前の今日")}><Loading /></Section>;
  }
  if (years.length === 0) return null;
  const thisYear = new Date().getFullYear();
  const shown = all ? years : years.slice(0, 3);
  return (
    <Section
      title={tx("On this day", "◯年前の今日")}
      aside={
        years.length > 3 ? (
          <button className="pill" onClick={() => setAll((v) => !v)}>
            {all ? tx("Show less", "たたむ") : tx(`All ${years.length} years`, `全${years.length}年`)}
          </button>
        ) : undefined
      }
    >
      <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
        {tx("What you were playing on this date in past years.", "同じ日付に、過去の年で聴いていた曲。")}
      </p>
      {shown.map((y) => (
        <div className="card" key={y.year} style={{ marginBottom: "var(--sp-3)" }}>
          <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>
            {tx(`${thisYear - Number(y.year)} years ago (${y.year})`, `${thisYear - Number(y.year)}年前（${y.year}年）`)}
            {" · "}
            <span className="t-small">{tx(`${y.plays} plays`, `${y.plays}回の再生`)}</span>
          </div>
          {y.tracks.slice(0, 5).map((t) => (
            <TrackRow key={t.track_id} track={{ id: t.track_id, name: t.name, artists: t.artists }}
              image={t.image ?? img.get(t.track_id)} onOpen={() => onTrack(t.track_id)} />
          ))}
        </div>
      ))}
    </Section>
  );
}

/** 忘れられた名曲。よく聴いたのに最近ぱったり聴いていない曲。 */
function RediscoverBlock({ onTrack }: { onTrack: (t: RankedLifetimeTrack) => void }) {
  const tx = useT();
  const data = useJson<Rediscover>("rediscover");
  const { byId } = useLifetimeTracks();
  const [all, setAll] = useState(false);
  const rows = data.data?.tracks ?? [];
  if (data.loading) {
    return <Section title={tx("Forgotten favourites", "忘れられた名曲")}><Loading /></Section>;
  }
  if (rows.length === 0) return null;
  const shown = all ? rows : rows.slice(0, 8);
  return (
    <Section
      title={tx("Forgotten favourites", "忘れられた名曲")}
      aside={
        rows.length > 8 ? (
          <button className="pill" onClick={() => setAll((v) => !v)}>
            {all ? tx("Show less", "たたむ") : tx(`All ${rows.length}`, `全${rows.length}件`)}
          </button>
        ) : undefined
      }
    >
      <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
        {tx(
          `Played at least ${data.data?.min_plays ?? 10} times, but not once in the last year.`,
          `生涯${data.data?.min_plays ?? 10}回以上聴いたのに、この1年は一度も再生していない曲。`,
        )}
      </p>
      <div className="card">
        {shown.map((t) => {
          const ranked = byId.get(t.id);
          return ranked ? <LifetimeRow key={t.id} track={ranked} onOpen={() => onTrack(ranked)} /> : null;
        })}
      </div>
    </Section>
  );
}

/** Wrapped のナビゲーション。月/年を切り替え、‹ › で1つずつ、セレクトで任意の時点へ跳べる。 */
function WrappedBlock(
  { onTrack, onArtist }:
    { onTrack: (t: RankedLifetimeTrack) => void; onArtist: (a: RankedLifetimeArtist) => void },
) {
  const tx = useT();
  const { lang } = useLang();
  const idx = useJson<WrappedIndex>("wrapped/index");
  const [mode, setMode] = useState<"month" | "year">("month");
  const [at, setAt] = useState(0);

  if (idx.loading) return <Loading />;
  const months = idx.data?.months ?? [];
  const years = idx.data?.years ?? [];
  const list = mode === "year" && years.length > 0 ? years : months;
  if (list.length === 0)
    return (
      <Empty>
        {tx(
          "Auto-generated at the end of each month (this month's top tracks, new additions, peak time).",
          "毎月末に自動生成されます（今月のTop曲・新規追加・ピーク時間帯）。",
        )}
      </Empty>
    );

  const i = Math.min(at, list.length - 1);
  const key = list[i];
  const label = mode === "year" && years.length > 0 ? tx(key, `${key}年`) : monthYear(key, lang);

  const jump = (next: number) => setAt(Math.max(0, Math.min(next, list.length - 1)));
  const switchMode = (next: "month" | "year") => {
    setMode(next);
    setAt(0); // 期間の粒度が変わるので最新に戻す
  };

  return (
    <>
      <div className="wrapped-nav">
        <button className="icon-btn" disabled={i === 0} onClick={() => jump(i - 1)}
          aria-label={tx("Newer", "新しい方へ")} title={tx("Newer", "新しい方へ")}>‹</button>
        <div className="wrapped-title">
          <div className="t-display">{label}</div>
          <div className="t-small">{tx(`${i + 1} of ${list.length}`, `${list.length}件中 ${i + 1}件目`)}</div>
        </div>
        <button className="icon-btn" disabled={i === list.length - 1} onClick={() => jump(i + 1)}
          aria-label={tx("Older", "古い方へ")} title={tx("Older", "古い方へ")}>›</button>

        <div className="wrapped-tools">
          {years.length > 0 && (
            <div className="seg seg-sm" role="tablist" aria-label={tx("Wrapped range", "Wrapped の単位")}>
              <button role="tab" aria-selected={mode === "month"} className={mode === "month" ? "is-active" : ""}
                onClick={() => switchMode("month")}>{tx("Month", "月")}</button>
              <button role="tab" aria-selected={mode === "year"} className={mode === "year" ? "is-active" : ""}
                onClick={() => switchMode("year")}>{tx("Year", "年")}</button>
            </div>
          )}
          <select className="pill-select" value={key} onChange={(e) => jump(list.indexOf(e.target.value))}
            aria-label={tx("Jump to", "期間を選ぶ")}>
            {list.map((m) => (
              <option key={m} value={m}>{mode === "year" && years.length > 0 ? m : monthYear(m, lang)}</option>
            ))}
          </select>
        </div>
      </div>
      <WrappedPanel path={key} onTrack={onTrack} onArtist={onArtist} />
    </>
  );
}

function WrappedPanel(
  { path, onTrack, onArtist }:
    { path: string; onTrack: (t: RankedLifetimeTrack) => void; onArtist: (a: RankedLifetimeArtist) => void },
) {
  const tx = useT();
  const { lang } = useLang();
  const DOW = dowLabels(lang);
  const w = useJson<Wrapped>(`wrapped/${path}`);
  const img = useTrackImages();
  const { byId } = useLifetimeTracks();
  const { byName } = useLifetimeArtists();

  if (w.loading) return <Loading />;
  if (!w.data) return <Empty>{tx(`Could not load data for ${path}.`, `${path} のデータを読めませんでした。`)}</Empty>;
  const d = w.data;

  return (
    <>
      <div className="row" style={{ marginBottom: "var(--sp-3)" }}>
        <StatCard
          label={tx("Plays", "再生")}
          value={d.plays.toLocaleString()}
          sub={tx(`${d.new_tracks} new`, `新規 ${d.new_tracks}曲`)}
        />
        {d.ms != null && d.ms > 0 && (
          <StatCard label={tx("Time spent", "聴いた時間")} value={formatDuration(d.ms, lang)}
            sub={formatDurationLong(d.ms, lang)} />
        )}
        {d.peak && (
          <StatCard
            label={tx("Peak time", "ピーク時間帯")}
            value={tx(`${DOW[d.peak.dow]} ${d.peak.hour}:00`, `${DOW[d.peak.dow]} ${d.peak.hour}時`)}
          />
        )}
      </div>

      {d.months && d.months.length > 1 && <MonthStrip months={d.months} />}

      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="card" style={{ flex: "1 1 280px" }}>
          <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>{tx("Top songs", "Top 曲")}</div>
          {d.top_tracks.map((t, i) => {
            const ranked = byId.get(t.track_id);
            return (
              <TrackRow
                key={t.track_id}
                rank={i + 1}
                track={{ id: t.track_id, name: t.name, artists: t.artists }}
                image={t.image ?? img.get(t.track_id)}
                onOpen={ranked ? () => onTrack(ranked) : undefined}
              />
            );
          })}
        </div>
        <div className="card" style={{ flex: "1 1 280px" }}>
          <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>{tx("Top artists", "Top アーティスト")}</div>
          {d.top_artists.map((a, i) => {
            const info = byName.get(a.name.toLowerCase());
            return (
              <div className="list-row" key={a.name}>
                <span className="list-rank">{i + 1}</span>
                {info?.image ? (
                  <img className="cand-art top-art art-round" src={info.image} alt="" loading="lazy" width={40} height={40} />
                ) : (
                  <span className="cand-art cand-art--ph top-art art-round" aria-hidden />
                )}
                {info ? (
                  <button className="list-main list-main--tap" onClick={() => onArtist(info)}>
                    <div className="name">{a.name}</div>
                    <div className="t-small">{tx(`#${info.rank} all-time`, `生涯 ${info.rank}位`)}</div>
                  </button>
                ) : (
                  <span className="list-main"><div className="name">{a.name}</div></span>
                )}
                <span className="list-count">{tx(`${a.count} ${a.count === 1 ? "play" : "plays"}`, `${a.count}回`)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

/** 年間 Wrapped の月別再生数。細い帯グラフで「その年のどこで聴いていたか」を見せる。 */
function MonthStrip({ months }: { months: { month: string; count: number }[] }) {
  const tx = useT();
  const max = Math.max(...months.map((m) => m.count), 1);
  return (
    <div className="card month-strip" style={{ marginBottom: "var(--sp-3)" }}>
      <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>{tx("Plays by month", "月ごとの再生")}</div>
      <div className="strip">
        {months.map((m) => (
          <div className="strip-col" key={m.month} title={`${m.month}: ${m.count}`}>
            <i style={{ height: `${Math.max(4, (m.count / max) * 100)}%` }} />
            <span className="t-small">{Number(m.month.slice(5, 7))}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
