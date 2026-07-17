import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// リポジトリ名に依存しない配線。Vercel は VERCEL_GIT_REPO_* を注入する。
// __REPO__ は site-ops の workflow_dispatch 先（owner/repo）に使う（Phase 3）。
const owner = process.env.VERCEL_GIT_REPO_OWNER ?? "YOSHIHIDEShimoji";
const repo = process.env.VERCEL_GIT_REPO_SLUG ?? "spotify-playlist-tools";

export default defineConfig({
  plugins: [react()],
  define: {
    __REPO__: JSON.stringify(`${owner}/${repo}`),
  },
});
