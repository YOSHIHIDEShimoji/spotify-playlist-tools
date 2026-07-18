import { useMemo, useState } from "react";
import { useJson } from "../lib/data";
import type { SearchIndex } from "../lib/types";
import { Empty, Loading, Section } from "../components/ui";
import { PlayButton } from "../lib/player";
import { useT } from "../lib/i18n";

export function SearchPage() {
  const tx = useT();
  const idx = useJson<SearchIndex>("search_index");
  const [q, setQ] = useState("");

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
    <Section title={tx("Search across all playlists", "全プレイリスト横断検索")}>
      <input
        className="input-search"
        placeholder={tx("Search by title or artist (where is this song?)", "曲名・アーティストで検索（この曲どこに入ってる?）")}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ marginBottom: "var(--sp-4)" }}
      />
      {idx.loading ? (
        <Loading />
      ) : !q.trim() ? (
        <Empty>
          {idx.data ? tx(`Searching ${idx.data.tracks.length} ${idx.data.tracks.length === 1 ? "song" : "songs"}.`, `${idx.data.tracks.length} 曲から検索します。`) : "…"}
        </Empty>
      ) : results.length === 0 ? (
        <Empty>{tx("No matching songs.", "一致する曲がありません。")}</Empty>
      ) : (
        <div className="card">
          {results.map((t) => (
            <div className="list-row" key={t.id}>
              {t.image ? (
                <img className="cand-art top-art" src={t.image} alt="" loading="lazy" width={40} height={40} />
              ) : (
                <span className="cand-art cand-art--ph top-art" aria-hidden />
              )}
              <span className="list-main">
                <div className="name">{t.name}</div>
                <div className="t-small">{t.artists.join(", ")}</div>
                <div className="t-small search-in">{tx("In: ", "収録: ")}{t.playlists.join(" / ")}</div>
              </span>
              <PlayButton uri={`spotify:track:${t.id}`} label={tx(`Play ${t.name}`, `${t.name} を再生`)} />
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
