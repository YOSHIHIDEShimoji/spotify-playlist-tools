// Spotify iframe 埋め込みプレイヤー。API キー不要。Spotify ログイン済みブラウザでフル再生、
// 未ログインでも30秒プレビュー（dashboard-design §4）。重複の聴き比べの核。

export function EmbedPlayer({ trackId, compact = true }: { trackId: string; compact?: boolean }) {
  return (
    <iframe
      title={`spotify-${trackId}`}
      src={`https://open.spotify.com/embed/track/${trackId}?utm_source=dashboard`}
      width="100%"
      height={compact ? 80 : 152}
      style={{ border: 0, borderRadius: "var(--r-panel)" }}
      loading="lazy"
      allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
    />
  );
}
