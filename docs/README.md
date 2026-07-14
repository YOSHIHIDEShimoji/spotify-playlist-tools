# docs — プロジェクトレビュー（2026-07-14）

Claude Code（Fable 5）による read-only レビュー。コードは一切変更していない。
実行・API 呼び出しによる実測検証込み。

**2026-07-14 追記:** 本人決定（GitHub Actions 移行・エラーは Issue 起票・local LLM 不採用）を
反映した統合実装プランを [implementation-plan.md](implementation-plan.md) に作成。
レビュー4ファイルは分析の一次資料として残すが、修正方針は実装プランが正。

| ファイル | 内容 |
|---|---|
| [implementation-plan.md](implementation-plan.md) | **実装プラン（統合版・最新）** — GitHub Actions 移行 + 全指摘の修正。実装セッションはこれだけ読めばよい |
| [inbox-error-analysis.md](inbox-error-analysis.md) | `./inbox.sh` のエラー原因特定（Gemini 429 + Spotify genres 空化）と修正案6件 |
| [bugs-and-risks.md](bugs-and-risks.md) | 修正点 — バグ・データ損失リスク 12件（launchd 実行環境の確認結果含む） |
| [improvements.md](improvements.md) | 改善点 — 重複排除・テスト・ログ・ドキュメント整合 8項目 |
| [fable5-redesign.md](fable5-redesign.md) | Fable 5 ならこうする — 分類パイプライン再設計・夜間ジョブ1本化・実施順序 |

## 最重要 3 点（先に読むならここ）

1. **inbox.sh のエラー** = Gemini 無料枠 10 req/分の 429。根本は Spotify の artist genres が
   ほぼ全アーティストで空になったこと（実測）。アーティスト判定キャッシュ + ISRC 国コード判定
   （実測: Japanese Musics の 97% が `JP`、Western Musics は `JP` ゼロ）でほぼ解消できる。
2. **launchd 3ジョブが 0:00 同時起動**しており、sort.py の全置換が inbox/sync の追加曲を
   消しうる競合が毎晩ありうる。夜間ジョブの1本化を推奨。
3. **inbox.sh だけエラー通知がない**。OAuth 失効時に silent fail する（過去ログに実績あり）。
