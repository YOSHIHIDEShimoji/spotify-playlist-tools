import { useJson } from "../lib/data";
import type { AuthStatus } from "../lib/types";

// 全ページ共通の警告帯。トークン失効・新スコープ未付与を1行で知らせ、
// 具体的な復旧コマンドは開閉（details）に畳んで初期ビューを食わないようにする。
const SCOPE_FEATURES = "聴取ログ・公式 Top・新譜ウォッチ";

export function AuthBanner() {
  const { data } = useJson<AuthStatus>("auth_status");
  if (!data) return null;

  if (!data.token_ok) {
    return (
      <details className="auth-banner auth-banner--error auth-banner--compact">
        <summary>
          <strong>Spotify トークンが失効しています</strong>
          <span className="disclosure" />
        </summary>
        <div className="steps">
          ローカルで <code>python reauth.py</code> を実行し、
          <code>gh secret set SPOTIFY_TOKEN_CACHE &lt; .cache-spotify</code> で更新してください。
        </div>
      </details>
    );
  }
  if (data.missing_scopes.length > 0) {
    return (
      <details className="auth-banner auth-banner--warn auth-banner--compact">
        <summary>
          <span>一部機能が未有効です（{SCOPE_FEATURES}）</span>
          <span className="disclosure" />
        </summary>
        <div className="steps">
          ローカルで <code>python reauth.py</code> を実行して再認証すると有効になります。
          <br />
          <span className="muted">未付与スコープ: {data.missing_scopes.join(", ")}</span>
        </div>
      </details>
    );
  }
  return null;
}
