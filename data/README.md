# data ブランチ

ダッシュボードの生成データ専用（dashboard-design §5.2）。
GitHub Actions（listen-log / nightly の sitegen）だけが書き込む単一ライター。
main の履歴を高頻度コミットで汚さないため分離している。

- listening/YYYY-MM.jsonl 聴取ログ
- runs.jsonl 実行サマリ / dupes.json / unknown.json / stats*.json / heatmap.json
- top.json / releases.json / archive_weekly.json / search_index.json / wrapped/

壊れたら消して再生成できる（listening と *_seen 以外は毎晩再生成）。
