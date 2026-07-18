import { useJson } from "../lib/data";
import type { AuthStatus } from "../lib/types";
import { useT } from "../lib/i18n";

// 全ページ共通の警告帯。トークン失効・新スコープ未付与を1行で知らせ、
// 具体的な復旧コマンドは開閉（details）に畳んで初期ビューを食わないようにする。

export function AuthBanner() {
  const { data } = useJson<AuthStatus>("auth_status");
  const t = useT();
  const scopeFeatures = t("listening log, official Top, new releases", "聴取ログ・公式 Top・新譜ウォッチ");
  if (!data) return null;

  if (!data.token_ok) {
    return (
      <details className="auth-banner auth-banner--error auth-banner--compact">
        <summary>
          <strong>{t("Spotify token has expired", "Spotify トークンが失効しています")}</strong>
          <span className="disclosure" />
        </summary>
        <div className="steps">
          {t("Run ", "ローカルで ")}<code>python reauth.py</code>
          {t(" locally, then update with ", " を実行し、")}
          <code>gh secret set SPOTIFY_TOKEN_CACHE &lt; .cache-spotify</code>
          {t(".", " で更新してください。")}
        </div>
      </details>
    );
  }
  if (data.missing_scopes.length > 0) {
    return (
      <details className="auth-banner auth-banner--warn auth-banner--compact">
        <summary>
          <span>{t(`Some features are disabled (${scopeFeatures})`, `一部機能が未有効です（${scopeFeatures}）`)}</span>
          <span className="disclosure" />
        </summary>
        <div className="steps">
          {t("Run ", "ローカルで ")}<code>python reauth.py</code>
          {t(" locally to re-authenticate and enable them.", " を実行して再認証すると有効になります。")}
          <br />
          <span className="muted">{t("Missing scopes: ", "未付与スコープ: ")}{data.missing_scopes.join(", ")}</span>
        </div>
      </details>
    );
  }
  return null;
}
