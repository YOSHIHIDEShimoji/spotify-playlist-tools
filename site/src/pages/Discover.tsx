import { useJson } from "../lib/data";
import type { AuthStatus, Releases, Top } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { PlayButton } from "../lib/player";

const TERMS: { key: string; label: string }[] = [
  { key: "short_term", label: "最近（約4週間）" },
  { key: "medium_term", label: "半年" },
  { key: "long_term", label: "長期" },
];

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
          <div className="grid-cards">
            {releases.data.items.map((r) => (
              <div className="card" key={r.album_id}>
                <div className="t-body-bold">
                  {r.album_name}
                  {r.is_new && <span className="badge badge-b" style={{ marginLeft: 6 }}>NEW</span>}
                </div>
                <div className="t-small" style={{ marginBottom: "var(--sp-2)" }}>
                  {r.artist} · {r.album_type} · {r.release_date}
                </div>
                <iframe
                  title={`al-${r.album_id}`}
                  src={`https://open.spotify.com/embed/album/${r.album_id}`}
                  width="100%"
                  height={152}
                  style={{ border: 0, borderRadius: "var(--r-panel)" }}
                  loading="lazy"
                  allow="encrypted-media"
                />
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Spotify 公式 Top">
        {top.loading ? <Loading /> : <TopBlock top={top.data} disabled={disabled} />}
      </Section>
    </>
  );
}

function TopBlock({ top, disabled }: { top: Top | null; disabled: boolean }) {
  const hasAny = top && TERMS.some((t) => (top.tracks[t.key]?.length ?? 0) > 0);
  if (!hasAny)
    return <DisabledOrEmpty disabled={disabled} empty="公式 Top のデータがまだありません（Spotify が計算したあなたの Top 曲・アーティスト）。" />;
  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
      {TERMS.map((t) => {
        const tracks = top!.tracks[t.key] ?? [];
        if (!tracks.length) return null;
        return (
          <div className="card" key={t.key} style={{ flex: "1 1 260px" }}>
            <div className="t-heading" style={{ marginBottom: "var(--sp-2)" }}>{t.label}</div>
            {tracks.slice(0, 10).map((tr) => (
              <div className="list-row" key={tr.id}>
                <span className="list-rank">{tr.rank}</span>
                <span className="list-main">
                  <div className="name">{tr.name}</div>
                  <div className="t-small">{(tr.artists ?? []).join(", ")}</div>
                </span>
                <PlayButton uri={`spotify:track:${tr.id}`} />
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
