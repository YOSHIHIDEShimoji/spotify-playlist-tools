// 生涯履歴の逆引き。「この曲は生涯何回・何位か」「このアーティストは何回か」を引く画面。
// 検索が空のときは生涯ランキングそのものを出し、下までスクロールすると全4500曲を遡れる。
import { useEffect, useMemo, useRef, useState } from "react";
import { useJson } from "../lib/data";
import type { SearchIndex, SearchTrack } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { ArtistDetail, LifetimeRow, TrackDetail } from "../components/Detail";
import { PlayButton } from "../lib/player";
import { formatDuration, useLifetimeArtists, useLifetimeTracks } from "../lib/lifetime";
import type { RankedLifetimeArtist, RankedLifetimeTrack } from "../lib/lifetime";
import { useLang, useT } from "../lib/i18n";

const PAGE = 60; // 一度に描く行数（下端に来たら継ぎ足す）

export function SearchPage() {
  const tx = useT();
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"tracks" | "artists">("tracks");
  const [track, setTrack] = useState<RankedLifetimeTrack | null>(null);
  const [artist, setArtist] = useState<RankedLifetimeArtist | null>(null);

  const life = useLifetimeTracks();
  const arts = useLifetimeArtists();
  const search = useJson<SearchIndex>("search_index");
  const query = q.trim().toLowerCase();

  // 再生履歴のある曲（生涯ランキング）。検索語があれば曲名・アーティスト名で絞る。
  const trackHits = useMemo(() => {
    if (!query) return life.tracks;
    return life.tracks.filter(
      (t) => t.name.toLowerCase().includes(query) || t.artists.some((a) => a.toLowerCase().includes(query)),
    );
  }, [query, life.tracks]);

  // プレイリストにはあるが再生履歴が無い曲（＝まだ聴いていない曲）も引けるようにする。
  const unplayed = useMemo(() => {
    if (!query || !search.data) return [];
    const known = new Set(life.tracks.map((t) => t.id));
    return search.data.tracks
      .filter((t) => !known.has(t.id))
      .filter(
        (t) => t.name.toLowerCase().includes(query) || t.artists.some((a) => a.toLowerCase().includes(query)),
      )
      .slice(0, 30);
  }, [query, search.data, life.tracks]);

  const artistHits = useMemo(() => {
    if (!query) return arts.artists;
    return arts.artists.filter((a) => a.name.toLowerCase().includes(query));
  }, [query, arts.artists]);

  const rows = tab === "tracks" ? trackHits : artistHits;
  const [shown, setShown] = useState(PAGE);
  useEffect(() => setShown(PAGE), [query, tab]); // 条件が変わったら先頭から
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) setShown((s) => (s < rows.length ? s + PAGE : s));
    }, { rootMargin: "600px" });
    io.observe(el);
    return () => io.disconnect();
  }, [rows.length]);

  const loading = life.loading || arts.loading;

  return (
    <>
      <Section title={tx("Search", "検索")}>
        <input
          className="input-search"
          placeholder={tx("Search a song or artist you've played", "曲名・アーティストで検索")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />

        <div className="seg" role="tablist" aria-label={tx("Search target", "検索の対象")}
          style={{ marginTop: "var(--sp-3)" }}>
          <button role="tab" aria-selected={tab === "tracks"} className={tab === "tracks" ? "is-active" : ""}
            onClick={() => setTab("tracks")}>
            {tx("Songs", "曲")}<span className="seg-count">{trackHits.length.toLocaleString()}</span>
          </button>
          <button role="tab" aria-selected={tab === "artists"} className={tab === "artists" ? "is-active" : ""}
            onClick={() => setTab("artists")}>
            {tx("Artists", "アーティスト")}<span className="seg-count">{artistHits.length.toLocaleString()}</span>
          </button>
        </div>

        <p className="t-small" style={{ margin: "var(--sp-3) 0" }}>
          {query
            ? tx("Tap a row to see plays, rank and the years you played it.",
                 "行をタップすると、生涯の再生回数・順位・年ごとの推移が出ます。")
            : tx("Your all-time ranking, in order. Scroll to go deeper.",
                 "生涯ランキングを順位どおりに表示しています。下にスクロールするといくらでも遡れます。")}
        </p>

        {loading ? (
          <Loading />
        ) : rows.length === 0 && unplayed.length === 0 ? (
          <Empty>{tx("Nothing matched.", "一致するものがありません。")}</Empty>
        ) : (
          <div className="card">
            {tab === "tracks"
              ? (rows as RankedLifetimeTrack[])
                  .slice(0, shown)
                  .map((t) => <LifetimeRow key={t.id} track={t} onOpen={() => setTrack(t)} />)
              : (rows as RankedLifetimeArtist[])
                  .slice(0, shown)
                  .map((a) => <ArtistRow key={a.name} artist={a} onOpen={() => setArtist(a)} />)}
            <div ref={sentinel} />
            {shown < rows.length && (
              <div className="t-small" style={{ textAlign: "center", padding: "var(--sp-3)" }}>
                {tx(`${(rows.length - shown).toLocaleString()} more…`, `あと ${(rows.length - shown).toLocaleString()} 件…`)}
              </div>
            )}
          </div>
        )}

        {tab === "tracks" && unplayed.length > 0 && (
          <>
            <div className="t-heading" style={{ margin: "var(--sp-5) 0 var(--sp-2)" }}>
              {tx("In your playlists, never played", "プレイリストにあるが再生履歴なし")}
            </div>
            <div className="card">
              {unplayed.map((t) => <UnplayedRow key={t.id} track={t} />)}
            </div>
          </>
        )}
      </Section>

      {track && <TrackDetail track={track} onClose={() => setTrack(null)} />}
      {artist && (
        <ArtistDetail
          artist={artist}
          onClose={() => setArtist(null)}
          onTrack={(t) => { setArtist(null); setTrack(t); }}
        />
      )}
    </>
  );
}

function ArtistRow({ artist, onOpen }: { artist: RankedLifetimeArtist; onOpen: () => void }) {
  const tx = useT();
  const { lang } = useLang();
  return (
    <div className="list-row">
      <span className="list-rank">{artist.rank}</span>
      {artist.image ? (
        <img className="cand-art top-art art-round" src={artist.image} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art art-round" aria-hidden />
      )}
      <button className="list-main list-main--tap" onClick={onOpen}>
        <div className="name">{artist.name}</div>
        <div className="t-small">{tx(`${artist.tracks} songs`, `${artist.tracks}曲`)}</div>
      </button>
      <span className="list-count">
        <span className="num">{tx(`${artist.count.toLocaleString()} plays`, `${artist.count.toLocaleString()}回`)}</span>
        <span className="list-sub">{formatDuration(artist.ms, lang)}</span>
      </span>
      {artist.id && <PlayButton uri={`spotify:artist:${artist.id}`} label={tx(`Play ${artist.name}`, `${artist.name} を再生`)} />}
    </div>
  );
}

function UnplayedRow({ track }: { track: SearchTrack }) {
  const tx = useT();
  return (
    <div className="list-row">
      {track.image ? (
        <img className="cand-art top-art" src={track.image} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art" aria-hidden />
      )}
      <span className="list-main">
        <div className="name">{track.name}</div>
        <div className="t-small">{track.artists.join(", ")}</div>
        <div className="t-small search-in">{tx("In: ", "収録: ")}{track.playlists.join(" / ")}</div>
      </span>
      <PlayButton uri={`spotify:track:${track.id}`} label={tx(`Play ${track.name}`, `${track.name} を再生`)} />
    </div>
  );
}
