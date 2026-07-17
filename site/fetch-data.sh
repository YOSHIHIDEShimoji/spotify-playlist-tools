#!/usr/bin/env bash
# Vercel ビルド時に data ブランチを取得して public/data へ同梱する。
# public リポジトリなので codeload の tarball をトークンなしで取れる。
# リポジトリ名は Vercel の環境変数から取り、リネーム（Phase 5）に自動追従する。
set -euo pipefail

owner="${VERCEL_GIT_REPO_OWNER:-YOSHIHIDEShimoji}"
slug="${VERCEL_GIT_REPO_SLUG:-spotify-playlist-tools}"

# 本番（Vercel）では取得失敗を fallback しない。フィクスチャ（dry-run 由来の擬似データ）が
# 本番に出るのを防ぐため、ビルドごと失敗させる（レビュー M8）。ローカル dev は fetch-data を
# 呼ばず public/data のフィクスチャを使うので影響しない。
rm -rf public/data
mkdir -p public/data
url="https://codeload.github.com/${owner}/${slug}/tar.gz/refs/heads/data"
echo "fetching data branch: ${url}"
curl -fsSL "$url" | tar -xz --strip-components=2 -C public/data "${slug}-data/data"
echo "data branch を public/data に展開しました"
