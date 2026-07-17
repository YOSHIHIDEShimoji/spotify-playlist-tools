import { useEffect } from "react";
import type { ReactNode } from "react";
import { EmbedPlayer } from "./EmbedPlayer";

/** Spotify のダイアログ相当（重い影・Esc / 背景クリックで閉じる・スクロールロック）。 */
export function Modal(
  { title, subtitle, onClose, children, footer }:
    { title: string; subtitle?: string; onClose: () => void; children?: ReactNode; footer?: ReactNode },
) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="grow">
            <div className="t-heading">{title}</div>
            {subtitle && <div className="t-small" style={{ marginTop: 2 }}>{subtitle}</div>}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="閉じる">×</button>
        </div>
        {children}
        {footer && <div className="modal-actions">{footer}</div>}
      </div>
    </div>
  );
}

export interface ModalTrack {
  id: string;
  name: string;
  artists: string[];
}

/** 曲モーダル: その場で試聴（Spotify 埋め込み）＋ Spotify で開く。 */
export function TrackModal({ track, onClose }: { track: ModalTrack; onClose: () => void }) {
  return (
    <Modal
      title={track.name}
      subtitle={track.artists.join(", ")}
      onClose={onClose}
      footer={
        <a className="pill pill-green" href={`https://open.spotify.com/track/${track.id}`} target="_blank" rel="noreferrer">
          Spotify で開く
        </a>
      }
    >
      <EmbedPlayer trackId={track.id} />
    </Modal>
  );
}

export interface ModalArtist {
  name: string;
  count: number;
  id?: string;
}

/** アーティストモーダル: 人気曲を試聴（Spotify 埋め込み）＋ Spotify で開く。
 * id 未取得（旧データ）のときは名前で Spotify 検索を開く。 */
export function ArtistModal({ artist, onClose }: { artist: ModalArtist; onClose: () => void }) {
  const openUrl = artist.id
    ? `https://open.spotify.com/artist/${artist.id}`
    : `https://open.spotify.com/search/${encodeURIComponent(artist.name)}`;
  return (
    <Modal
      title={artist.name}
      subtitle={`ライブラリに ${artist.count} 曲`}
      onClose={onClose}
      footer={
        <a className="pill pill-green" href={openUrl} target="_blank" rel="noreferrer">
          Spotify で開く
        </a>
      }
    >
      {artist.id ? (
        <iframe
          title={`artist-${artist.id}`}
          src={`https://open.spotify.com/embed/artist/${artist.id}`}
          width="100%"
          height={352}
          style={{ border: 0, borderRadius: "var(--r-panel)" }}
          loading="lazy"
          allow="encrypted-media"
        />
      ) : (
        <div className="t-small">
          アーティストの直リンクは次回の夜間データ更新後に有効になります。今は名前で Spotify 検索を開きます。
        </div>
      )}
    </Modal>
  );
}
