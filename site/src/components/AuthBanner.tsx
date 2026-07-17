import { useJson } from "../lib/data";
import type { AuthStatus } from "../lib/types";

// 全ページ共通の警告帯（dashboard-design §3-3）。トークン失効・新スコープ未付与を最上部で知らせる。
export function AuthBanner() {
  const { data } = useJson<AuthStatus>("auth_status");
  if (!data) return null;

  if (!data.token_ok) {
    return (
      <div className="auth-banner auth-banner--error">
        <strong>Spotify トークンが失効しています。</strong> ローカルで
        <code> python reauth.py </code>
        を実行し、<code>gh secret set SPOTIFY_TOKEN_CACHE &lt; .cache-spotify</code> で更新してください。
      </div>
    );
  }
  if (data.missing_scopes.length > 0) {
    return (
      <div className="auth-banner auth-banner--warn">
        一部機能が未有効です（{data.missing_scopes.join(", ")}）。
        <code> python reauth.py </code>
        で再認証すると、聴取ログ・公式 Top・新譜ウォッチが有効になります。
      </div>
    );
  }
  return null;
}
