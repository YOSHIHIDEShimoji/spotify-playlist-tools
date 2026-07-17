#!/usr/bin/env bash
# Vercel ビルド時に data ブランチを取得して public/data へ同梱する。
# public リポジトリなので codeload の tarball をトークンなしで取れる。
# リポジトリ名は Vercel の環境変数から取り、リネーム（Phase 5）に自動追従する。
set -euo pipefail

owner="${VERCEL_GIT_REPO_OWNER:-YOSHIHIDEShimoji}"
slug="${VERCEL_GIT_REPO_SLUG:-spotify-playlist-tools}"

mkdir -p public/data
url="https://codeload.github.com/${owner}/${slug}/tar.gz/refs/heads/data"
echo "fetching data branch: ${url}"
if curl -fsSL "$url" | tar -xz --strip-components=2 -C public/data "${slug}-data/data"; then
  echo "data branch を public/data に展開しました"
else
  echo "::warning::data ブランチの取得に失敗。既存の public/data を使います"
fi
