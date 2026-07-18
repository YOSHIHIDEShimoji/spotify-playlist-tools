// 常駐プレイヤー。画面下に Spotify 埋め込みを1つ常駐させ、曲/アーティストを
// タップすると差し替える。アプリ直下（ルーティングの外）に置くので、タブ移動で
// unmount されず、次の曲を選ぶまで再生が止まらない。
//
// 再生方式は2段構え:
//  1) Spotify IFrame API が読めれば controller 経由で、タップ直後に play() を呼び
//     「ワンタップ再生」する（ユーザー操作の直後なので自動再生ポリシーを満たす）。
//  2) API が読めない/自動再生がブロックされても、埋め込み自体は表示されるので
//     最悪でも「埋め込み内の再生ボタンをもう一度押す」= 従来挙動にフォールバックする。
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useT } from "./i18n";

interface PlayerCtx {
  play: (uri: string) => void;
  close: () => void;
}
const Ctx = createContext<PlayerCtx>({ play: () => {}, close: () => {} });
export const usePlayer = () => useContext(Ctx);

// "spotify:track:ID" / "spotify:artist:ID" → 埋め込み URL（フォールバック用）
function embedUrl(uri: string): string {
  const parts = uri.split(":");
  const kind = parts.length >= 3 ? parts[1] : "track";
  const id = parts[parts.length - 1];
  return `https://open.spotify.com/embed/${kind}/${id}?utm_source=dashboard`;
}

interface SpotifyController {
  loadUri: (uri: string) => void;
  play: () => void;
  destroy: () => void;
}
interface SpotifyIframeApi {
  createController: (
    el: HTMLElement,
    opts: { uri: string; width?: string | number; height?: string | number },
    cb: (c: SpotifyController) => void,
  ) => void;
}
declare global {
  interface Window {
    onSpotifyIframeApiReady?: (api: SpotifyIframeApi) => void;
  }
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const t = useT();
  const [uri, setUri] = useState<string | null>(null);
  const [apiReady, setApiReady] = useState(false);
  const [fallback, setFallback] = useState(false); // API が読めない→素の iframe に切替
  const hostRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<SpotifyIframeApi | null>(null);
  const controllerRef = useRef<SpotifyController | null>(null);

  useEffect(() => {
    document.body.classList.toggle("has-player", !!uri);
  }, [uri]);

  // IFrame API スクリプトを一度だけ読み込む
  useEffect(() => {
    if (apiRef.current) {
      setApiReady(true);
      return;
    }
    // 既存の読み込みに相乗り（既にタグがある場合）
    const prev = window.onSpotifyIframeApiReady;
    window.onSpotifyIframeApiReady = (api) => {
      prev?.(api);
      apiRef.current = api;
      setApiReady(true);
    };
    if (!document.getElementById("spotify-iframe-api")) {
      const s = document.createElement("script");
      s.id = "spotify-iframe-api";
      s.src = "https://open.spotify.com/embed/iframe-api/v1";
      s.async = true;
      s.onerror = () => setFallback(true);
      document.body.appendChild(s);
    }
    const timer = setTimeout(() => {
      if (!apiRef.current) setFallback(true);
    }, 5000);
    return () => clearTimeout(timer);
  }, []);

  // controller 経由の再生: uri が変わるたびに loadUri→play（初回は controller を生成）
  useEffect(() => {
    if (!uri || fallback || !apiReady) return;
    const host = hostRef.current;
    if (!host || !apiRef.current) return;
    if (controllerRef.current) {
      controllerRef.current.loadUri(uri);
      controllerRef.current.play();
    } else {
      apiRef.current.createController(host, { uri, width: "100%", height: 80 }, (c) => {
        controllerRef.current = c;
        c.play(); // タップ直後なのでワンタップ再生できる
      });
    }
  }, [uri, apiReady, fallback]);

  const value: PlayerCtx = {
    play: (u) => setUri(u),
    close: () => {
      if (controllerRef.current) {
        controllerRef.current.destroy();
        controllerRef.current = null;
      }
      setUri(null);
    },
  };

  // controller が使える描画モードか（API 準備済みかつフォールバックでない）
  const useController = apiReady && !fallback;

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className={"player-bar" + (uri ? " is-visible" : "")} aria-hidden={!uri}>
        {/* 非表示時は中身ごと unmount。閉じるボタンがフォーカスに残らない（a11y） */}
        {uri && (
          <div className="player-inner">
            <div className="player-embed">
              {useController ? (
                <div ref={hostRef} />
              ) : (
                <iframe
                  title="player"
                  src={embedUrl(uri)}
                  width="100%"
                  height={80}
                  style={{ border: 0, borderRadius: 8, display: "block" }}
                  loading="lazy"
                  allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
                />
              )}
            </div>
            <button className="player-close" onClick={value.close} aria-label={t("Close player", "プレイヤーを閉じる")}>
              <CloseIcon />
            </button>
          </div>
        )}
      </div>
    </Ctx.Provider>
  );
}

/** どこからでも使える再生ボタン。行の onClick を止めて常駐プレイヤーに送る。 */
export function PlayButton({ uri, label }: { uri: string; label?: string }) {
  const { play } = usePlayer();
  const t = useT();
  const lbl = label ?? t("Play", "再生");
  return (
    <button
      className="play-btn"
      aria-label={lbl}
      title={lbl}
      onClick={(e) => {
        e.stopPropagation();
        play(uri);
      }}
    >
      <PlayIcon />
    </button>
  );
}

/** Spotify の track id（22文字 base62）か。Last.fm 由来の未解決 id（"lastfm:..."）を弾く。 */
export function isSpotifyTrackId(id: string): boolean {
  return /^[A-Za-z0-9]{22}$/.test(id);
}

/** ランキング行の再生ボタン。Spotify に解決済みなら常駐プレイヤーで再生、未解決（Last.fm のみで
 *  Spotify に無い曲）は Spotify 検索を新規タブで開くフォールバックにする。 */
export function TrackPlayButton(
  { id, name, artists, label }: { id: string; name: string; artists: string[]; label?: string },
) {
  const t = useT();
  const lbl = label ?? t("Play", "再生");
  if (isSpotifyTrackId(id)) return <PlayButton uri={`spotify:track:${id}`} label={lbl} />;
  const url = `https://open.spotify.com/search/${encodeURIComponent([name, ...artists].join(" "))}`;
  return (
    <a
      className="play-btn"
      href={url}
      target="_blank"
      rel="noreferrer"
      title={lbl}
      aria-label={lbl}
      onClick={(e) => e.stopPropagation()}
    >
      <PlayIcon />
    </a>
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
