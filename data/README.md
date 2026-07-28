# data ブランチ

ダッシュボードの生成データ専用（dashboard-design §5.2）。
GitHub Actions（listen-log / lastfm-log / nightly の sitegen）だけが書き込む単一ライター。
main の履歴を高頻度コミットで汚さないため分離している。

## 聴取の元データ（入力）

- `history/YYYY.jsonl.gz` 拡張ストリーミング履歴（2019〜・Spotify のプライバシーエクスポート由来）。
  1行1再生（`track_id` / `name` / `artists` / `played_at` / `ms`）。`import_history.py` が生成する。
- `history/extra.json` 30秒未満で終わった再生の曲別集計（完走率の分母）
- `listening/YYYY-MM.jsonl` 自前の recently-played ログ（3時間毎）
- `scrobbles/YYYY-MM.jsonl` Last.fm scrobble（30分毎・50件制限が無いのでこちらが正）

## 集計（毎晩 sitegen が再生成）

- `runs.jsonl` 実行サマリ / `dupes.json` / `unknown.json` / `stats*.json` / `heatmap.json`
- `listening_stats.json` 週間・累計・streak
- `lifetime_tracks.json` 全曲の生涯ランキング（配列の並び＝順位。再生回数・総再生時間・
  初回/最終再生日・年別内訳・短再生回数）
- `lifetime_artists.json` 全アーティストの生涯ランキング（画像つき）
- `rediscover.json` 忘れられた名曲（生涯10回以上かつ直近365日ゼロ）
- `on_this_day.json` 同じ月日に過去の年で聴いていた曲
- `wrapped/YYYY-MM.json` 月間 Wrapped / `wrapped/YYYY.json` 年間 Wrapped / `wrapped/index.json`
- `top.json` / `releases.json` / `archive_weekly.json` / `search_index.json`
- `recs.json` 似ているアーティスト・曲（Last.fm 類似度 × 生涯再生回数）
- `upcoming.json` 発売予定（MusicBrainz 由来）

## キャッシュ（消しても再生成されるが、消すと API 消費が増える）

- `artist_meta.json` アーティストの画像・ジャンル・フォロワー（名前キー）
- `rec_resolve_cache.json` おすすめ曲 → Spotify track id（空振りも記憶する）
- `mb_cache.json` Spotify アーティストID → MusicBrainz MBID と取得済みの発売予定
- `releases_seen.json` 既読アルバム（新着バッジ用）

壊れたら消して再生成できる（`history` / `listening` / `scrobbles` / `*_seen` 以外は毎晩再生成）。
`history` の再取得には Spotify への請求（数日待ち）が必要なので消さないこと。
