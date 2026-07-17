// 常駐プレイヤー。画面下に Spotify 埋め込み iframe を1つ常駐させ、曲/アーティストを
// タップすると src を差し替える。iframe はアプリ直下（ルーティングの外）に置くので、
// タブ移動・モーダル開閉で unmount されず、次の曲を選ぶまで再生が止まらない（フィードバック対応）。
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

interface PlayerCtx {
  play: (uri: string) => void;
  close: () => void;
}
const Ctx = createContext<PlayerCtx>({ play: () => {}, close: () => {} });
export const usePlayer = () => useContext(Ctx);

// "spotify:track:ID" / "spotify:artist:ID" → 埋め込み URL
function embedUrl(uri: string): string {
  const parts = uri.split(":");
  const kind = parts.length >= 3 ? parts[1] : "track";
  const id = parts[parts.length - 1];
  return `https://open.spotify.com/embed/${kind}/${id}?utm_source=dashboard`;
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    document.body.classList.toggle("has-player", !!src);
  }, [src]);

  const value: PlayerCtx = {
    play: (uri) => setSrc(embedUrl(uri)),
    close: () => setSrc(null),
  };

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className={"player-bar" + (src ? " is-visible" : "")} aria-hidden={!src}>
        <div className="player-inner">
          <div className="player-embed">
            {src && (
              <iframe
                title="player"
                src={src}
                width="100%"
                height={80}
                style={{ border: 0, borderRadius: 8, display: "block" }}
                loading="lazy"
                allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
              />
            )}
          </div>
          <button className="player-close" onClick={() => setSrc(null)} aria-label="プレイヤーを閉じる">
            <CloseIcon />
          </button>
        </div>
      </div>
    </Ctx.Provider>
  );
}

/** どこからでも使える再生ボタン。行の onClick を止めて常駐プレイヤーに送る。 */
export function PlayButton({ uri, label = "再生" }: { uri: string; label?: string }) {
  const { play } = usePlayer();
  return (
    <button
      className="play-btn"
      aria-label={label}
      title={label}
      onClick={(e) => {
        e.stopPropagation();
        play(uri);
      }}
    >
      <PlayIcon />
    </button>
  );
}

export function PlayIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M4.5 2.6v10.8a.6.6 0 0 0 .92.5l8.4-5.4a.6.6 0 0 0 0-1L5.42 2.1a.6.6 0 0 0-.92.5z" />
    </svg>
  );
}

export function CloseIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
    </svg>
  );
}
