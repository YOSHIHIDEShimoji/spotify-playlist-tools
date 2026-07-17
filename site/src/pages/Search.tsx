import { useMemo, useState } from "react";
import { useJson } from "../lib/data";
import type { SearchIndex } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { EmbedPlayer } from "../components/EmbedPlayer";

export function SearchPage() {
  const idx = useJson<SearchIndex>("search_index");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const results = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query || !idx.data) return [];
    return idx.data.tracks
      .filter(
        (t) =>
          t.name.toLowerCase().includes(query) ||
          t.artists.some((a) => a.toLowerCase().includes(query)),
      )
      .slice(0, 60);
  }, [q, idx.data]);

  return (
    <Section title="全プレイリスト横断検索">
      <input
        className="input-search"
        placeholder="曲名・アーティストで検索（この曲どこに入ってる?）"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ marginBottom: "var(--sp-4)" }}
      />
      {idx.loading ? (
        <Loading />
      ) : !q.trim() ? (
        <Empty>{idx.data ? `${idx.data.tracks.length} 曲から検索します。` : "…"}</Empty>
      ) : results.length === 0 ? (
        <Empty>一致する曲がありません。</Empty>
      ) : (
        <div className="card">
          {results.map((t) => (
            <div key={t.id}>
              <div className="list-row" style={{ cursor: "pointer" }} onClick={() => setOpen(open === t.id ? null : t.id)}>
                <span className="list-main">
                  <div className="name">{t.name}</div>
                  <div className="t-small">{t.artists.join(", ")}</div>
                </span>
                <span className="t-small">{t.playlists.join(" / ")}</span>
              </div>
              {open === t.id && (
                <div style={{ padding: "0 var(--sp-3) var(--sp-2)" }}>
                  <EmbedPlayer trackId={t.id} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
