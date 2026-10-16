// Vercel Cron から毎日0時台に呼ばれ、GitHub Actions の one-year-ago ワークフローを起動する。
//
// なぜここで起動するか: GitHub の定期実行（schedule）はこのリポジトリの実測で数時間遅れ・
// 大半の間引きが起きており、0時に設定しても朝まで動かない日がある。手動実行
// （workflow_dispatch）は数秒で起動するので、「いつ動かすか」だけを Vercel Cron に任せる。
// 処理本体（再生記録の集計・プレイリスト更新）は GitHub Actions 側にあり、ここには持たない。
//
// サイト本体は関数を持たない静的サイト（dashboard-design §7.1）で、これが唯一の例外。
// 必要な環境変数（Vercel の Production に登録）:
//   CRON_SECRET            Vercel Cron が Authorization: Bearer <値> で送ってくる合言葉
//   GITHUB_DISPATCH_TOKEN  fine-grained PAT（このリポジトリの Actions: Read and write のみ）

const WORKFLOW = "one-year-ago.yml";

export async function GET(request: Request): Promise<Response> {
  const secret = process.env.CRON_SECRET;
  // 合言葉が未設定なら誰の呼び出しも通さない（未設定＝全開放にしない）
  if (!secret || request.headers.get("authorization") !== `Bearer ${secret}`) {
    return new Response("unauthorized", { status: 401 });
  }
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return new Response("GITHUB_DISPATCH_TOKEN is not set", { status: 500 });
  }

  const owner = process.env.VERCEL_GIT_REPO_OWNER || "YOSHIHIDEShimoji";
  const repo = process.env.VERCEL_GIT_REPO_SLUG || "spotify-playlist-tools";
  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "spotify-dashboard-cron",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  );

  // 成功は 204。失敗は Vercel の Cron ログで原因が分かるよう、GitHub の応答をそのまま返す
  if (res.status !== 204) {
    const detail = (await res.text()).slice(0, 500);
    return new Response(`dispatch failed: ${res.status} ${detail}`, { status: 502 });
  }
  return new Response("dispatched", { status: 200 });
}
