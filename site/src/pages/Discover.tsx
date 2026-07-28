// おすすめ。どのブロックも「何を基準に出しているか」を必ず書く。
//
// 重要な前提: Spotify 公式の推薦 API は 2024-11 に新規アプリ向けへ閉じられ、このアプリからは
// 使えない（/v1/recommendations と related-artists が 404、audio-features が 403。2026-07-29 実測）。
// そのため「似ている」系は Last.fm の類似度に生涯再生回数を掛けて出している。
import { useState } from "react";
import { useJson } from "../lib/data";
import type { AuthStatus, RecArtist, RecTrack, Recs, ReleaseItem, Releases, Top, Upcoming } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { PlayButton } from "../lib/player";
import { useLifetimeArtists } from "../lib/lifetime";
import { useT } from "../lib/i18n";

const TERMS: { key: string; en: string; ja: string }[] = [
  { key: "short_term", en: "Last 4 weeks", ja: "最近（約4週間）" },
  { key: "medium_term", en: "6 months", ja: "半年" },
  { key: "long_term", en: "Long term", ja: "長期" },
];

const ALBUM_TYPE: Record<string, { en: string; ja: string }> = {
  album: { en: "Album", ja: "アルバム" },
  single: { en: "Single", ja: "シングル" },
  compilation: { en: "Compilation", ja: "コンピ" },
  ep: { en: "EP", ja: "EP" },
};

type Tab = "similar" | "releases" | "upcoming" | "top";

// 未有効（要再認証）か、単にデータが無いだけかを分けて伝えるカード。
function DisabledOrEmpty({ disabled, empty }: { disabled: boolean; empty: string }) {
  const tx = useT();
  if (disabled) {
    return (
      <Empty>
        <div className="t-body-bold" style={{ color: "var(--warning)" }}>{tx("Disabled", "未有効")}</div>
        <div style={{ marginTop: 4 }}>
          {tx("Re-authenticate (run ", "再認証（ローカルで ")}<code>python reauth.py</code>
          {tx(" locally) to enable.", "）すると有効になります。")}
        </div>
      </Empty>
    );
  }
  return <Empty>{empty}</Empty>;
}

/** そのブロックの判定基準。おすすめは根拠が分からないと使えないので必ず添える。 */
function Basis({ children }: { children: React.ReactNode }) {
  const tx = useT();
  return (
    <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
      <b>{tx("Basis: ", "基準: ")}</b>{children}
    </p>
  );
}

export function Discover() {
  const tx = useT();
  const releases = useJson<Releases>("releases");
  const top = useJson<Top>("top");
  const recs = useJson<Recs>("recs");
  const upcoming = useJson<Upcoming>("upcoming");
  const auth = useJson<AuthStatus>("auth_status");
  const disabled = (auth.data?.missing_scopes.length ?? 0) > 0;
  const [tab, setTab] = useState<Tab>("similar");

  const items = releases.data?.items ?? [];
  const jp = items.filter((r) => r.class === "japanese");
  const west = items.filter((r) => r.class !== "japanese"); // class 未設定（旧データ）は western 側へ

  return (
    <Section title={tx("Discover", "おすすめ")}>
      <div className="seg" role="tablist" aria-label={tx("Discover type", "おすすめの種類")}>
        <button role="tab" aria-selected={tab === "similar"} className={tab === "similar" ? "is-active" : ""}
          onClick={() => setTab("similar")}>
          {tx("Similar to you", "テイストが似ている")}
        </button>
        <button role="tab" aria-selected={tab === "releases"} className={tab === "releases" ? "is-active" : ""}
          onClick={() => setTab("releases")}>
          {tx("New releases", "新譜")}{items.length > 0 && <span className="seg-count">{items.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "upcoming"} className={tab === "upcoming" ? "is-active" : ""}
          onClick={() => setTab("upcoming")}>
          {tx("Coming soon", "リリース予定")}
          {(upcoming.data?.items.length ?? 0) > 0 && <span className="seg-count">{upcoming.data!.items.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "top"} className={tab === "top" ? "is-active" : ""}
          onClick={() => setTab("top")}>
          {tx("Spotify Official Top", "Spotify 公式 Top")}
        </button>
      </div>

      {tab === "similar" ? (
        recs.loading ? <Loading /> : <SimilarBlock recs={recs.data} />
      ) : tab === "upcoming" ? (
        upcoming.loading ? <Loading /> : <UpcomingBlock data={upcoming.data} />
      ) : tab === "top" ? (
        top.loading ? <Loading /> : <TopBlock top={top.data} disabled={disabled} />
      ) : releases.loading ? (
        <Loading />
      ) : (
        <ReleaseBlock jp={jp} west={west} totalEmpty={items.length === 0} disabled={disabled} />
      )}
    </Section>
  );
}

/** 似ているアーティスト/曲（Last.fm）。1件ごとに「◯◯に似ている」という根拠を出す。 */
function SimilarBlock({ recs }: { recs: Recs | null }) {
  const tx = useT();
  const [kind, setKind] = useState<"artists" | "tracks">("artists");
  const { byName } = useLifetimeArtists();

  if (!recs || !recs.available) {
    return (
      <>
        <Basis>
          {tx(
            "Last.fm similarity, weighted by how much you've actually played each seed artist or song.",
            "Last.fm の類似度に、あなたがその種のアーティスト・曲を実際にどれだけ聴いたかを掛けて重み付け。",
          )}
        </Basis>
        <Empty>
          {recs?.reason
            ? tx(`Not available: ${recs.reason}`, `出せません: ${recs.reason}`)
            : tx(
                "Similar artists and songs appear after the next nightly update.",
                "似ているアーティスト・曲は次回の夜間更新後に出ます。",
              )}
        </Empty>
      </>
    );
  }

  const artists = recs.artists ?? [];
  const tracks = recs.tracks ?? [];
  const rows = kind === "artists" ? artists : tracks;

  return (
    <>
      <Basis>
        {tx(
          "Last.fm similarity × how much you've played the seed. Artists and songs you already play are excluded. Spotify's own recommendation API was retired, so this is computed here.",
          "Last.fm の類似度 × その種をどれだけ聴いたか。すでに聴いているアーティスト・曲は除外。Spotify 公式の推薦 API は提供終了しているため、ここで独自に計算しています。",
        )}
      </Basis>
      <div className="seg" role="tablist" aria-label={tx("Similar kind", "似ているものの種類")}>
        <button role="tab" aria-selected={kind === "artists"} className={kind === "artists" ? "is-active" : ""}
          onClick={() => setKind("artists")}>
          {tx("Artists", "アーティスト")}<span className="seg-count">{artists.length}</span>
        </button>
        <button role="tab" aria-selected={kind === "tracks"} className={kind === "tracks" ? "is-active" : ""}
          onClick={() => setKind("tracks")}>
          {tx("Songs", "曲")}<span className="seg-count">{tracks.length}</span>
        </button>
      </div>
      {rows.length === 0 ? (
        <Empty>{tx("Nothing new to suggest right now.", "いま出せる新しい候補がありません。")}</Empty>
      ) : (
        <div className="card">
          {kind === "artists"
            ? artists.map((a) => <RecArtistRow key={a.name} rec={a} known={byName} />)
            : tracks.map((t) => <RecTrackRow key={`${t.artist}-${t.name}`} rec={t} />)}
        </div>
      )}
    </>
  );
}

function RecArtistRow({ rec, known }: { rec: RecArtist; known: Map<string, { image?: string | null }> }) {
  const tx = useT();
  // 根拠になった種アーティストの画像を借りて並べる（候補本人の画像は未取得のため）
  const seedImage = rec.because.map((b) => known.get(b.name.toLowerCase())?.image).find(Boolean);
  return (
    <div className="list-row">
      {seedImage ? (
        <img className="cand-art top-art art-round" src={seedImage} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art art-round" aria-hidden />
      )}
      <span className="list-main">
        <div className="name">{rec.name}</div>
        <div className="rec-why">
          {tx("Because you play ", "よく聴いている ")}
          {rec.because.map((b, i) => (
            <span key={b.name}>
              {i > 0 && tx(" and ", "・")}
              <b>{b.name}</b>
            </span>
          ))}
          {tx("", " に似ています")}
        </div>
      </span>
      <a className="pill" href={`https://open.spotify.com/search/${encodeURIComponent(rec.name)}`}
        target="_blank" rel="noreferrer">
        {tx("Find", "探す")}
      </a>
    </div>
  );
}

function RecTrackRow({ rec }: { rec: RecTrack }) {
  const tx = useT();
  return (
    <div className="list-row">
      {rec.image ? (
        <img className="cand-art top-art" src={rec.image} alt="" loading="lazy" width={40} height={40} />
      ) : (
        <span className="cand-art cand-art--ph top-art" aria-hidden />
      )}
      <span className="list-main">
        <div className="name">{rec.name}</div>
        <div className="t-small">{rec.artist}</div>
        <div className="rec-why">
          {tx("Because you play ", "よく聴いている ")}<b>{rec.because.name}</b>
          {tx(` (${rec.because.count} plays)`, `（${rec.because.count}回）に似ています`)}
        </div>
      </span>
      {rec.id ? (
        <PlayButton uri={`spotify:track:${rec.id}`} label={tx(`Play ${rec.name}`, `${rec.name} を再生`)} />
      ) : (
        <a className="pill" target="_blank" rel="noreferrer"
          href={`https://open.spotify.com/search/${encodeURIComponent(`${rec.name} ${rec.artist}`)}`}>
          {tx("Find", "探す")}
        </a>
      )}
    </div>
  );
}

/** これから出るリリース。Spotify は未発売を返さないので MusicBrainz から拾っている。 */
function UpcomingBlock({ data }: { data: Upcoming | null }) {
  const tx = useT();
  const items = data?.items ?? [];
  const known = data?.known ?? 0;
  const followed = data?.followed ?? 0;
  return (
    <>
      <Basis>
        {tx(
          "Announced release dates from MusicBrainz for artists you follow. Spotify has no API for unreleased music, so this comes from an external database and only shows dates that are fixed to the day.",
          "フォロー中アーティストの発売予定を MusicBrainz から取得しています。Spotify には未発売のリリースを返す API が無いため外部データベースを使っており、日付が日まで確定しているものだけを出しています。",
        )}
        {followed > 0 && (
          <>
            {" "}
            {tx(
              `Matched ${known} of ${followed} followed artists so far (a few more each night).`,
              `フォロー中 ${followed} 人のうち ${known} 人を照合済み（毎晩少しずつ増えます）。`,
            )}
          </>
        )}
      </Basis>
      {items.length === 0 ? (
        <Empty>
          {tx(
            "No announced release dates right now. This fills in as artists get matched and labels announce dates.",
            "いま公表されている発売予定はありません。照合が進み、レーベルが日付を発表すると出てきます。",
          )}
        </Empty>
      ) : (
        <div className="card">
          {items.map((r) => (
            <div className="list-row" key={`${r.artist_id}-${r.title}-${r.date}`}>
              <span className="cand-art cand-art--ph top-art" aria-hidden />
              <span className="list-main">
                <div className="name">{r.title}</div>
                <div className="t-small">{r.artist}{r.type ? ` · ${r.type}` : ""}</div>
              </span>
              <span className="list-count num">{r.date}</span>
              <a className="pill" target="_blank" rel="noreferrer"
                href={`https://open.spotify.com/artist/${r.artist_id}`}>
                {tx("Artist", "アーティスト")}
              </a>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function ReleaseBlock(
  { jp, west, totalEmpty, disabled }:
    { jp: ReleaseItem[]; west: ReleaseItem[]; totalEmpty: boolean; disabled: boolean },
) {
  const tx = useT();
  const [side, setSide] = useState<"western" | "japanese">("western");
  return (
    <>
      <Basis>
        {tx(
          "Albums and singles released in the last 14 days by artists you follow on Spotify, plus artists already in your playlists. Checked every night.",
          "Spotify でフォロー中のアーティスト＋あなたのプレイリストに在籍するアーティストが、直近14日にリリースしたアルバム・シングル。毎晩チェックしています。",
        )}
      </Basis>
      <div className="seg" role="tablist" aria-label={tx("Release side", "邦洋")}>
        <button role="tab" aria-selected={side === "western"} className={side === "western" ? "is-active" : ""}
          onClick={() => setSide("western")}>
          Western{west.length > 0 && <span className="seg-count">{west.length}</span>}
        </button>
        <button role="tab" aria-selected={side === "japanese"} className={side === "japanese" ? "is-active" : ""}
          onClick={() => setSide("japanese")}>
          Japanese{jp.length > 0 && <span className="seg-count">{jp.length}</span>}
        </button>
      </div>
      <ReleaseList shown={side === "japanese" ? jp : west} isJapanese={side === "japanese"}
        totalEmpty={totalEmpty} disabled={disabled} />
    </>
  );
}

// 新譜の一覧（邦/洋タブ共通の行）。均一な高さのサムネイル行で並べる。
function ReleaseList(
  { shown, isJapanese, totalEmpty, disabled }:
    { shown: ReleaseItem[]; isJapanese: boolean; totalEmpty: boolean; disabled: boolean },
) {
  const tx = useT();
  const albumType = (typ: string) => (ALBUM_TYPE[typ] ? tx(ALBUM_TYPE[typ].en, ALBUM_TYPE[typ].ja) : typ);
  if (totalEmpty) {
    return (
      <DisabledOrEmpty
        disabled={disabled}
        empty={tx(
          "No new releases in the last 14 days (checked nightly across followed + in-library artists).",
          "直近14日の新譜はありません（フォロー中＋在籍アーティストを毎晩チェック）。",
        )}
      />
    );
  }
  return shown.length === 0 ? (
    <Empty>{isJapanese ? tx("No Japanese new releases.", "邦楽の新譜はありません。") : tx("No Western new releases.", "洋楽の新譜はありません。")}</Empty>
  ) : (
    <div className="card">
      {shown.map((r) => (
        <div className="list-row rel-row" key={r.album_id}>
          {r.image ? (
            <img className="rel-art" src={r.image} alt="" loading="lazy" width={48} height={48} />
          ) : (
            <span className="rel-art rel-art--ph" aria-hidden />
          )}
          <span className="list-main">
            <div className="name">
              {r.album_name}
              {r.is_new && <span className="badge badge-b" style={{ marginLeft: 6 }}>NEW</span>}
            </div>
            <div className="t-small">{r.artist} · {albumType(r.album_type)} · {r.release_date}</div>
          </span>
          <PlayButton uri={`spotify:album:${r.album_id}`} label={tx(`Play ${r.album_name}`, `${r.album_name} を再生`)} />
        </div>
      ))}
    </div>
  );
}

function TopBlock({ top, disabled }: { top: Top | null; disabled: boolean }) {
  const tx = useT();
  const hasAny = top && TERMS.some((t) => (top.tracks[t.key]?.length ?? 0) > 0);
  if (!hasAny)
    return (
      <DisabledOrEmpty
        disabled={disabled}
        empty={tx(
          "No official Top data yet (your Top tracks and artists as computed by Spotify).",
          "公式 Top のデータがまだありません（Spotify が計算したあなたの Top 曲・アーティスト）。",
        )}
      />
    );
  return (
    <>
      <Basis>
        {tx(
          "Spotify's own ranking of your top tracks, over three time windows. Their weighting is not published.",
          "Spotify 自身が計算したあなたの Top 曲を3つの期間で。重み付けの中身は公開されていません。",
        )}
      </Basis>
      <div className="top-row">
        {TERMS.map((term) => {
          const tracks = top!.tracks[term.key] ?? [];
          if (!tracks.length) return null;
          return (
            <div className="card top-col" key={term.key}>
              <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>{tx(term.en, term.ja)}</div>
              {tracks.slice(0, 10).map((tr) => (
                <div className="list-row top-item" key={tr.id}>
                  <span className="list-rank">{tr.rank}</span>
                  {tr.image ? (
                    <img className="cand-art top-art" src={tr.image} alt="" loading="lazy" width={40} height={40} />
                  ) : (
                    <span className="cand-art cand-art--ph top-art" aria-hidden />
                  )}
                  <span className="list-main">
                    <div className="name clamp-1">{tr.name}</div>
                    <div className="t-small clamp-1">{(tr.artists ?? []).join(", ")}</div>
                  </span>
                  <PlayButton uri={`spotify:track:${tr.id}`} label={tx(`Play ${tr.name}`, `${tr.name} を再生`)} />
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </>
  );
}
