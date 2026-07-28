// 逆引きの中心になる詳細ダイアログ。曲/アーティストをタップすると
// 「生涯で何回・何位・いつからいつまで・年ごとにどれだけ」がここで全部分かる。
import { useMemo } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Modal } from "./Modal";
import { TrackPlayButton } from "../lib/player";
import { useJson } from "../lib/data";
import { monthDay, useLang, useT } from "../lib/i18n";
import { finishRate, formatDuration, useLifetimeTracks, yearSeries } from "../lib/lifetime";
import type { RankedLifetimeArtist, RankedLifetimeTrack } from "../lib/lifetime";
import type { Recs, SearchIndex } from "../lib/types";

const GREEN = "#1ed760";
const DIM = "#535353";
const TIP = { background: "#282828", border: "1px solid #4d4d4d", borderRadius: 8 };

/** 詳細ダイアログ共通の数値ブロック。 */
function Facts({ items }: { items: { k: string; v: string; s?: string }[] }) {
  return (
    <div className="fact-grid">
      {items.map((f) => (
        <div className="fact" key={f.k}>
          <div className="fact-k">{f.k}</div>
          <div className="fact-v num">{f.v}</div>
          {f.s && <div className="fact-s">{f.s}</div>}
        </div>
      ))}
    </div>
  );
}

/** 年ごとの再生数。最大の年だけ緑にして「いちばん聴いた年」を一目で分かるようにする。 */
function YearBars({ years, label }: { years: Record<string, number>; label: string }) {
  const data = useMemo(() => yearSeries(years), [years]);
  if (data.length === 0) return null;
  const max = Math.max(...data.map((d) => d.count));
  return (
    <div className="year-bars">
      <ResponsiveContainer width="100%" height={110}>
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
          <XAxis dataKey="year" stroke="#b3b3b3" fontSize={10} tickLine={false} axisLine={false} interval={0} />
          <Tooltip contentStyle={TIP} labelStyle={{ color: "#fff" }} cursor={{ fill: "#ffffff10" }}
            formatter={(v: number) => [v.toLocaleString(), label]} />
          <Bar dataKey="count" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.year} fill={d.count === max ? GREEN : DIM} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TrackDetail({ track, onClose }: { track: RankedLifetimeTrack; onClose: () => void }) {
  const tx = useT();
  const { lang } = useLang();
  const search = useJson<SearchIndex>("search_index");
  const inPlaylists = search.data?.tracks.find((t) => t.id === track.id)?.playlists ?? [];
  const rate = finishRate(track);

  return (
    <Modal
      title={track.name}
      subtitle={track.artists.join(", ")}
      onClose={onClose}
      className="modal-dialog--wide"
      footer={
        <>
          <TrackPlayButton id={track.id} name={track.name} artists={track.artists}
            label={tx("Play", "再生")} />
          <a className="pill pill-green" href={`https://open.spotify.com/track/${track.id}`}
            target="_blank" rel="noreferrer">
            {tx("Open in Spotify", "Spotify で開く")}
          </a>
        </>
      }
    >
      <Facts items={[
        { k: tx("Lifetime plays", "生涯の再生"), v: `${track.count.toLocaleString()}`,
          s: tx(`#${track.rank} of all time`, `生涯 ${track.rank}位`) },
        { k: tx("Time spent", "聴いた時間"), v: formatDuration(track.ms, lang) },
        ...(rate != null
          ? [{ k: tx("Finish rate", "完走率"), v: `${rate}%`,
               s: tx(`skipped ${track.short} times`, `${track.short}回は途中でやめた`) }]
          : []),
      ]} />
      <div className="t-small detail-span">
        {tx("First played", "初めて聴いたのは")} <b>{monthDay(track.first, lang)}, {track.first.slice(0, 4)}</b>
        {" · "}
        {tx("last played", "最後に聴いたのは")} <b>{monthDay(track.last, lang)}, {track.last.slice(0, 4)}</b>
      </div>
      <YearBars years={track.years} label={tx("plays", "再生")} />
      {inPlaylists.length > 0 && (
        <div className="t-small detail-span">
          {tx("In: ", "収録: ")}{inPlaylists.join(" / ")}
        </div>
      )}
    </Modal>
  );
}

export function ArtistDetail(
  { artist, onClose, onTrack }:
    { artist: RankedLifetimeArtist; onClose: () => void; onTrack?: (t: RankedLifetimeTrack) => void },
) {
  const tx = useT();
  const { lang } = useLang();
  const { tracks } = useLifetimeTracks();
  const recs = useJson<Recs>("recs");

  const mine = useMemo(
    () => tracks.filter((t) => t.artists.some((a) => a.toLowerCase() === artist.name.toLowerCase())),
    [tracks, artist.name],
  );
  // このアーティストが「なぜ似ているか」の根拠になっているおすすめを拾う
  const similar = (recs.data?.artists ?? [])
    .filter((r) => r.because.some((b) => b.name.toLowerCase() === artist.name.toLowerCase()))
    .slice(0, 6);

  return (
    <Modal
      title={artist.name}
      subtitle={tx(`#${artist.rank} artist of all time`, `生涯 ${artist.rank}位のアーティスト`)}
      onClose={onClose}
      className="modal-dialog--wide"
      footer={
        <a className="pill pill-green"
          href={artist.id
            ? `https://open.spotify.com/artist/${artist.id}`
            : `https://open.spotify.com/search/${encodeURIComponent(artist.name)}`}
          target="_blank" rel="noreferrer">
          {tx("Open in Spotify", "Spotify で開く")}
        </a>
      }
    >
      <div className="artist-head">
        {artist.image ? (
          <img className="artist-avatar" src={artist.image} alt="" width={96} height={96} loading="lazy" />
        ) : (
          <span className="artist-avatar artist-avatar--ph" aria-hidden />
        )}
        <div className="grow">
          <Facts items={[
            { k: tx("Lifetime plays", "生涯の再生"), v: artist.count.toLocaleString() },
            { k: tx("Time spent", "聴いた時間"), v: formatDuration(artist.ms, lang) },
            { k: tx("Distinct songs", "曲数"), v: artist.tracks.toLocaleString() },
          ]} />
        </div>
      </div>
      <div className="t-small detail-span">
        {tx("First played", "初めて聴いたのは")} <b>{monthDay(artist.first, lang)}, {artist.first.slice(0, 4)}</b>
        {" · "}
        {tx("last played", "最後に聴いたのは")} <b>{monthDay(artist.last, lang)}, {artist.last.slice(0, 4)}</b>
      </div>
      <YearBars years={artist.years} label={tx("plays", "再生")} />

      <div className="t-heading detail-head">{tx("Most played songs", "よく聴いた曲")}</div>
      <div className="modal-list">
        {mine.slice(0, 15).map((t, i) => (
          <button className="list-row list-row--tap" key={t.id} onClick={() => onTrack?.(t)}>
            <span className="list-rank">{i + 1}</span>
            <span className="list-main">
              <div className="name">{t.name}</div>
              <div className="t-small">{tx(`#${t.rank} all-time`, `生涯 ${t.rank}位`)}</div>
            </span>
            <span className="list-count">{tx(`${t.count} plays`, `${t.count}回`)}</span>
          </button>
        ))}
      </div>

      {similar.length > 0 && (
        <>
          <div className="t-heading detail-head">{tx("If you like this artist", "このアーティストに似ている")}</div>
          <div className="chip-row">
            {similar.map((s) => (
              <a className="pill" key={s.name} target="_blank" rel="noreferrer"
                href={`https://open.spotify.com/search/${encodeURIComponent(s.name)}`}>
                {s.name}
              </a>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}

/** 曲の行（生涯ランキング表示用）。順位・回数・時間を出し、タップで詳細を開く。 */
export function LifetimeRow(
  { track, rank, onOpen }: { track: RankedLifetimeTrack; rank?: number; onOpen: () => void },
) {
  const tx = useT();
  const { lang } = useLang();
  return (
    <div className="list-row">
      <span className="list-rank">{rank ?? track.rank}</span>
      {track.image ? (
        <img className="cand-art top-art" src={track.image} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art" aria-hidden />
      )}
      <button className="list-main list-main--tap" onClick={onOpen}>
        <div className="name">{track.name}</div>
        <div className="t-small">{track.artists.join(", ")}</div>
      </button>
      <span className="list-count">
        <span className="num">{tx(`${track.count} plays`, `${track.count}回`)}</span>
        <span className="list-sub">{formatDuration(track.ms, lang)}</span>
      </span>
      <TrackPlayButton id={track.id} name={track.name} artists={track.artists}
        label={tx(`Play ${track.name}`, `${track.name} を再生`)} />
    </div>
  );
}
