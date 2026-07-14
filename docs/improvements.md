# 改善点 — 品質・保守性（プロジェクト全体）

作成: 2026-07-14（Claude Code によるプロジェクトレビュー）
バグ・リスクは [bugs-and-risks.md](bugs-and-risks.md)、inbox のエラーは [inbox-error-analysis.md](inbox-error-analysis.md) 参照。

## 1. 4スクリプト間の重複コードを spotify_utils.py に集約

現在ほぼ同一のコードが4ファイルにコピーされている:

| 重複している処理 | 存在箇所 |
|---|---|
| `build_spotify_client()`（env チェック + SpotifyOAuth） | inbox.py / sort.py / sync.py / archive.py |
| プレイリストのページング取得ループ | 4ファイルに計6実装 |
| 100件バッチでの `playlist_add_items` | inbox.py / sync.py / archive.py |
| `KEY=VALUE` 形式の設定ファイルパーサ | inbox.py / sync.py / archive.py |
| macOS 通知（Python 版とシェル版が別々に存在） | inbox.py / sync.sh / sort.sh / archive.sh |

`spotify_utils.py` は既にあるのに `free_redirect_port()` しか入っていない。
`build_client(scope)` / `iter_playlist_tracks(sp, pid, fields)` / `add_in_batches(...)` /
`parse_config(path)` / `notify(title, msg)` を移すだけで、各スクリプトは
「固有ロジックだけの 100 行未満」になり、修正が1箇所で済むようになる。

## 2. シェルラッパー4本を1本に統合

inbox.sh / sort.sh / archive.sh / sync.sh は「cd → ログ準備 → ネット確認 → 実行 → 通知」の
同型コピー（しかも inbox.sh だけエラー処理が欠けている）。共通ラッパー1本にすると
挙動のばらつき（今回の inbox 通知欠落のような）が構造的に起きなくなる:

```bash
# run.sh <name>  — 例: ./run.sh inbox
NAME=$1
output=$("$PYTHON" -u "$NAME.py" 2>&1)
...共通のログ・通知処理...
```

## 3. requirements.txt のバージョン固定

現在は名前のみ（インストール実績: spotipy 2.26.0 / google-genai 2.0.1 / python-dotenv 1.2.2 /
matplotlib 3.10.9）。再セットアップ時に別バージョンが入って挙動が変わるのを防ぐため、
最低限 `spotipy>=2.26,<3` 程度の範囲指定を推奨。`pip freeze > requirements.lock` を
置いておくのも手。matplotlib は `--analyze` でしか使わないので、コメントで
「分析機能用・任意」と明示すると venv が軽くなる選択肢も示せる。

## 4. print → logging（Python 側にタイムスタンプがない)

現在、日時はシェルラッパーが書く行にしかなく、Python の出力（曲ごとの判定結果など）が
いつのものかログから特定しにくい。`logging` に切り替えて
`[2026-07-14 00:00:03] [western] ...` 形式にすれば、ログだけで障害調査が完結する。
ログローテーションがない点も、`log/` が既に 9,000 行を超えているので
いずれ `logging.handlers.RotatingFileHandler` か月次で切るとよい。

## 5. テストがゼロ

外部 API に依存しない純関数だけでも pytest を置く価値が高い:

- `classify()` の判定順序（ISRC / 日本語文字 / genres のフォールスルー）
- `JP_CHAR_RE` の境界（ひらがな・カタカナ・漢字のみ・中国語・半角カナ）
- `sort_tracks()` のキー（曲数降順 → 名前 → リリース日、コラボ曲の代表アーティスト選択）
- `_normalize_date("2023")` → `"2023-01-01"` などの正規化
- 設定パーサ（コメント行・空行・`=` を含む値）

launchd で毎晩無人実行される性質上、「壊れたことに翌朝まで気づけない」コストが高いので、
テストの投資対効果は通常のプロジェクトより大きい。

## 6. README と実挙動の同期

- README:204「OAuth トークンが失効した場合は macOS 通知で警告される」→ inbox は通知されない（要修正）
- README:162 の判定順①「Spotify ジャンル」→ 現在ほぼ機能していない（実測で大半のアーティストが
  genres 空）。判定ロジックを直したら README も更新
- README:55 `cp .env.example .env` → ファイルが存在しない（[bugs-and-risks.md](bugs-and-risks.md) 9.）
- launchd の表に「3ジョブとも 0:00」とあるが、競合リスクの注記がない。スケジュール変更時に更新

## 7. 設定ファイルの一貫性

- `WESTERN_MUSICS_ID` が [inbox.py:31](../inbox.py) にハードコードされている一方、
  同じ ID が [sync.txt:10](../sync.txt) では `SOURCE_PLAYLIST_ID` として設定ファイル管理。
  inbox.txt に `WESTERN_MUSICS_ID=` を追加して揃えるべき
- sort.txt には URL、inbox.txt / sync.txt / archive.txt には ID と、形式が混在している。
  どちらでも受け付けるよう `extract_playlist_id()`（sort.py に既にある）を共通化すると
  ユーザーの貼り間違いが減る

## 8. その他小さな磨き込み

- [inbox.py:51](../inbox.py) `notify()` の `open -W` は通知アプリの終了まで**ブロック**する。
  意図がなければ `-W` を外すか `subprocess.Popen` に
- sort.sh のエラー判定 `grep -qi "oauth\|token\|auth"` は `author` など無関係の文字列にも
  マッチする。`grep -qiE "oauth|token|unauthorized|401"` 程度に絞ると誤分類が減る
- `.python-version`（pyenv virtualenv 名）はマシン依存の値だが public リポジトリに
  コミットされている。README に手順があるので実害はないが、認識はしておく
- `spotify_icon.icns`（110KB）はリポジトリ内で参照されていない（Notifier.app 作成時の素材と
  推測）。用途をコメントか README に一言残すと将来の自分が迷わない
