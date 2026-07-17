// GitHub REST 経由で site-ops.yml を workflow_dispatch する（Phase 3 の操作系）。
// サーバレス関数を持たず、ブラウザから PAT で直接叩く（dashboard-design §7.1）。
declare const __REPO__: string;

export const REPO = __REPO__;

export interface DispatchResult {
  ok: boolean;
  status: number;
  message: string;
}

/** PAT の疎通確認（workflows 一覧が取れるか）。初回設定画面で使う。 */
export async function verifyPat(pat: string): Promise<boolean> {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows`, {
      headers: { Authorization: `Bearer ${pat}`, Accept: "application/vnd.github+json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** site-ops.yml を起動する。payload は JSON 文字列で渡す（dashboard-design §7.1）。 */
export async function dispatchOp(pat: string, op: string, payload: unknown): Promise<DispatchResult> {
  try {
    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/site-ops.yml/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${pat}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: { op, payload: JSON.stringify(payload) },
        }),
      },
    );
    return {
      ok: res.status === 204,
      status: res.status,
      message: res.status === 204 ? "起動しました" : `失敗: ${res.status}`,
    };
  } catch (e) {
    return { ok: false, status: 0, message: String(e) };
  }
}

export function runsUrl(): string {
  return `https://github.com/${REPO}/actions`;
}
