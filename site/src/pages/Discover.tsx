import { useState } from "react";
import { useJson } from "../lib/data";
import type { AuthStatus, ReleaseItem, Releases, Top } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { PlayButton } from "../lib/player";
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

export function Discover() {
  const tx = useT();
  const releases = useJson<Releases>("releases");
  const top = useJson<Top>("top");
  const auth = useJson<AuthStatus>("auth_status");
  const disabled = (auth.data?.missing_scopes.length ?? 0) > 0;
  const [tab, setTab] = useState<"western" | "japanese" | "top">("western");

  const items = releases.data?.items ?? [];
  const jp = items.filter((r) => r.class === "japanese");
  const west = items.filter((r) => r.class !== "japanese"); // class 未設定（旧データ）は western 側へ

  return (
    <Section title={tx("Discover", "おすすめ")}>
      <div className="seg" role="tablist" aria-label={tx("Discover type", "おすすめの種類")}>
        <button role="tab" aria-selected={tab === "western"} className={tab === "western" ? "is-active" : ""} onClick={() => setTab("western")}>
          Western{west.length > 0 && <span className="seg-count">{west.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "japanese"} className={tab === "japanese" ? "is-active" : ""} onClick={() => setTab("japanese")}>
          Japanese{jp.length > 0 && <span className="seg-count">{jp.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "top"} className={tab === "top" ? "is-active" : ""} onClick={() => setTab("top")}>
          {tx("Spotify Official Top", "Spotify 公式 Top")}
        </button>
      </div>

      {tab === "top" ? (
        top.loading ? <Loading /> : <TopBlock top={top.data} disabled={disabled} />
      ) : releases.loading ? (
        <Loading />
      ) : (
        <ReleaseList
          shown={tab === "japanese" ? jp : west}
          isJapanese={tab === "japanese"}
          totalEmpty={items.length === 0}
          disabled={disabled}
        />
      )}
    </Section>
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
  return (
    <>
      <p className="t-small" style={{ margin: "0 0 var(--sp-3)" }}>
        {tx("New releases in the last 14 days (followed + in-library artists).", "直近14日の新譜（フォロー中＋在籍アーティスト）。")}
      </p>
      {shown.length === 0 ? (
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
      )}
    </>
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
  );
}
