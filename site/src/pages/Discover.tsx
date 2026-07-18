import { useState } from "react";
import { useJson } from "../lib/data";
import type { AuthStatus, ReleaseItem, Releases, Top } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { PlayButton } from "../lib/player";

const TERMS: { key: string; label: string }[] = [
  { key: "short_term", label: "最近（約4週間）" },
  { key: "medium_term", label: "半年" },
  { key: "long_term", label: "長期" },
];

const ALBUM_TYPE: Record<string, string> = {
  album: "アルバム",
  single: "シングル",
  compilation: "コンピ",
  ep: "EP",
};
const albumType = (t: string) => ALBUM_TYPE[t] ?? t;

// 未有効（要再認証）か、単にデータが無いだけかを分けて伝えるカード。
function DisabledOrEmpty({ disabled, empty }: { disabled: boolean; empty: string }) {
  if (disabled) {
    return (
      <Empty>
        <div className="t-body-bold" style={{ color: "var(--warning)" }}>未有効</div>
        <div style={{ marginTop: 4 }}>再認証（ローカルで <code>python reauth.py</code>）すると有効になります。</div>
      </Empty>
    );
  }
  return <Empty>{empty}</Empty>;
}

export function Discover() {
  const releases = useJson<Releases>("releases");
  const top = useJson<Top>("top");
  const auth = useJson<AuthStatus>("auth_status");
  const disabled = (auth.data?.missing_scopes.length ?? 0) > 0;

  return (
    <>
      <Section title="新譜ウォッチ">
        {releases.loading ? (
          <Loading />
        ) : !releases.data || releases.data.items.length === 0 ? (
          <DisabledOrEmpty disabled={disabled} empty="直近14日の新譜はありません（フォロー中＋在籍アーティストを毎晩チェック）。" />
        ) : (
          <ReleaseWatch items={releases.data.items} />
        )}
      </Section>

      <Section title="Spotify 公式 Top">
        {top.loading ? <Loading /> : <TopBlock top={top.data} disabled={disabled} />}
      </Section>
    </>
  );
}

function ReleaseWatch({ items }: { items: ReleaseItem[] }) {
  const [tab, setTab] = useState<"western" | "japanese">("western");
  const jp = items.filter((r) => r.class === "japanese");
  const west = items.filter((r) => r.class !== "japanese"); // class 未設定（旧データ）は western 側へ
  const shown = tab === "japanese" ? jp : west;

  return (
    <>
      <div className="seg" role="tablist" aria-label="新譜の言語">
        <button role="tab" aria-selected={tab === "western"} className={tab === "western" ? "is-active" : ""} onClick={() => setTab("western")}>
          Western{west.length > 0 && <span className="seg-count">{west.length}</span>}
        </button>
        <button role="tab" aria-selected={tab === "japanese"} className={tab === "japanese" ? "is-active" : ""} onClick={() => setTab("japanese")}>
          Japanese{jp.length > 0 && <span className="seg-count">{jp.length}</span>}
        </button>
      </div>

      {shown.length === 0 ? (
        <Empty>{tab === "japanese" ? "邦楽の新譜はありません。" : "洋楽の新譜はありません。"}</Empty>
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
              <PlayButton uri={`spotify:album:${r.album_id}`} label={`${r.album_name} を再生`} />
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function TopBlock({ top, disabled }: { top: Top | null; disabled: boolean }) {
  const hasAny = top && TERMS.some((t) => (top.tracks[t.key]?.length ?? 0) > 0);
  if (!hasAny)
    return <DisabledOrEmpty disabled={disabled} empty="公式 Top のデータがまだありません（Spotify が計算したあなたの Top 曲・アーティスト）。" />;
  return (
    <div className="top-row">
      {TERMS.map((t) => {
        const tracks = top!.tracks[t.key] ?? [];
        if (!tracks.length) return null;
        return (
          <div className="card top-col" key={t.key}>
            <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>{t.label}</div>
            {tracks.slice(0, 10).map((tr) => (
              <div className="list-row top-item" key={tr.id}>
                <span className="list-rank">{tr.rank}</span>
                <span className="list-main">
                  <div className="name clamp-1">{tr.name}</div>
                  <div className="t-small clamp-1">{(tr.artists ?? []).join(", ")}</div>
                </span>
                <PlayButton uri={`spotify:track:${tr.id}`} label={`${tr.name} を再生`} />
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
