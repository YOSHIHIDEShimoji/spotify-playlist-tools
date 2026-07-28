# spotify-playlist-tools

Spotify プレイリストを自動管理する4つのツール。毎晩 GitHub Actions 上で無人実行される。

---

## ファイル構成

```
.
├── core.py       # 共通基盤: クライアント生成・ページング・バッチ・設定パーサ・ロギング
├── classify.py   # 分類パイプライン（キャッシュ→ISRC→かな→genres→Gemini一括）
│
├── inbox.py      # お気に入りの曲を邦楽/洋楽に振り分けて各プレイリストへ追加・削除
├── inbox.txt     # 振り分け設定（JAPANESE_MUSICS_ID / WESTERN_MUSICS_ID + 邦楽アーティスト）
│
├── sort.py       # プレイリストのソート・分析（--all で sort.txt 全件、--analyze で分析グラフ）
├── sort.txt      # ソート対象プレイリストURL一覧
│
├── archive.py    # Top 50 の新着曲をアーカイブ
├── archive.txt   # アーカイブ設定（SOURCE / DEST プレイリストID）
│
├── sync.py       # アーティスト別プレイリストへ自動振り分け・双方向同期
├── sync.txt      # 同期設定（SOURCE プレイリストID + アーティスト→プレイリストID）
│
├── import_history.py  # 拡張ストリーミング履歴（Spotify のエクスポート）→ 年別 gz JSONL（一度きり）
├── recommend.py  # 似ているアーティスト/曲（Last.fm 類似度 × 生涯再生回数）
├── upcoming.py   # 発売予定（MusicBrainz。Spotify に未発売を返す API が無いため）
│
├── artist_class_cache.json  # classify.py の永続キャッシュ（コミット対象）
├── sync_state.json          # sync.py のスナップショット（双方向同期用・コミット対象）
│
├── .github/workflows/
│   ├── nightly.yml   # 毎晩 01:00 JST に inbox→sync→sort→archive を直列実行
│   └── ci.yml        # push/PR で ruff + pytest
│
├── tests/        # 外部API非依存の純関数テスト
└── log/          # ローカル実行時のログ（gitignore 済み）
```

---

## セットアップ

### 仮想環境（ローカル実行・再認証用）

```bash
pyenv virtualenv 3.11.9 spotify-playlist-tools-3.11.9
pyenv local spotify-playlist-tools-3.11.9
pip install -r requirements.txt          # 本番依存のみ
pip install -r requirements-dev.txt      # テスト・分析（matplotlib/pytest/ruff）も入れる場合
```

### 認証情報

[Spotify Developer Dashboard](https://developer.spotify.com/dashboard) でアプリを作成し、`.env` を用意する。

```bash
cp .env.example .env
```

```env
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8000/callback
GEMINI_API_KEY=your_gemini_api_key_here  # オプション（判定不能曲の最終フォールバック用）
```

`GEMINI_API_KEY` は [Google AI Studio](https://aistudio.google.com/apikey) で取得できる（無料枠あり）。
未設定の場合、Gemini フォールバックはスキップされ判定不能曲は `unknown` 扱いになる。

初回実行時にブラウザが開き、OAuth 認証が走る。トークンは `.cache-spotify` にキャッシュされる。

---

## 自動実行（GitHub Actions）

`nightly.yml` が毎晩 **01:00 JST**（cron は UTC 指定）に1ジョブで
`inbox → sync → sort --all → archive` を**直列実行**する。順序が依存関係を表す
（inbox が振り分け → sync が同期 → sort が整列 → archive が独立実行）。

### 必要な Secrets

| Secret | 内容 |
|---|---|
| `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` | Spotify アプリの認証情報 |
| `GEMINI_API_KEY` | Gemini（任意。未設定でも動く） |
| `SPOTIFY_TOKEN_CACHE` | `.cache-spotify` の中身をそのまま（`gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify`） |
| `LASTFM_API_KEY` | Last.fm 読み取りキー。scrobble 取り込みと「似ている」おすすめに使う（未設定でも動く） |

### エラーは GitHub Issue で通知

macOS 通知は廃止した。失敗・要対応は Issue になる（GitHub モバイルアプリの push が実質の通知）。

- **`nightly-failure` ラベル**: バッチが致命的エラー（exit 1）や要再認証（exit 3）で失敗したとき。
  ログ末尾と復旧手順が本文に入る。同じ Issue にコメントが積まれ、直ったら手動で close する
- **`unknown-tracks` ラベル**: 振り分け判定できなかった曲があるとき。お気に入りに残るので手動振り分けか次回再判定

### 再認証（トークン失効時）

ヘッドレス実行ではブラウザ認証を開始せず即失敗する（深夜のハングを防ぐ）。
`nightly-failure` Issue の手順どおり:

```bash
cd spotify-playlist-tools
python inbox.py                                   # 対話実行 → ブラウザ認証
gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify
gh workflow run nightly --field dry_run=true      # 動作確認
```

---

## 各ツール

### inbox.py — お気に入り振り分け

お気に入りの曲を以下の順で邦楽/洋楽に分類し、各プレイリストへ追加。処理済みはお気に入りから削除、
判定不能曲は `log/unknown_tracks.txt` に書き出してお気に入りに残す。

1. **永続キャッシュ** — 一度判定したアーティストは即返す
2. **ISRC 国コード** — `JP` 始まりなら邦楽（追加APIコストなし）
3. **日本語かな** — ひらがな・カタカナ・半角カナを含めば邦楽（漢字のみは保留）
4. **Spotify genres** — 取得できた場合のみ
5. **Gemini 一括** — 残った未知アーティストをまとめて1リクエストで判定

- 邦楽 → Japanese Musics + `inbox.txt` のアーティスト別プレイリスト
- 洋楽 → Western Musics のみ（アーティスト別振り分けは sync.py が担う）

```bash
python inbox.py            # 実行
python inbox.py --dry-run  # 変更せず予定のみ表示
```

`inbox.txt`:

```
JAPANESE_MUSICS_ID=<Japanese Musics のID>
WESTERN_MUSICS_ID=<Western Musics のID>
Novelbright=<プレイリストID>
```

### sync.py — アーティスト別プレイリスト同期

ソースプレイリストを走査し各アーティストの曲を個別プレイリストへ追加（重複なし）。
`AUTO_DETECT_THRESHOLD`（20）曲以上の未設定アーティストは自動でプレイリスト作成し
`sync.txt` / `sort.txt` に追記する。

**双方向同期**: アーティストプレイリストから曲を削除すると次回実行時にソースからも削除される
（`sync_state.json` の前回スナップショットとの差分で検出。初回は順方向のみ）。

```bash
python sync.py
python sync.py --dry-run
```

### sort.py — ソート・分析

**アーティスト曲数降順 → アーティスト名順 → リリース日昇順**で並べ替える。
全置換前に `snapshot_id` を照合し、取得中に変更があればそのプレイリストは見送る（次回再ソート）。

```bash
python sort.py "https://open.spotify.com/playlist/xxxx"   # 単体
python sort.py --all                                       # sort.txt 全件
python sort.py --analyze "https://.../playlist/xxxx"       # 分析グラフ（ローカル専用・matplotlib必要）
python sort.py --all --dry-run
```

### archive.py — Top 50 アーカイバ

ソースの現在の曲を取得し、アーカイブ先に未追加の曲だけを追記する。ページング対応。

```bash
python archive.py
python archive.py --dry-run
```

`archive.txt`:

```
SOURCE_PLAYLIST_ID=<Top 50 などのID>
DEST_PLAYLIST_ID=<アーカイブ先のID>
```

---

## exit code

| code | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 致命的エラー（例外） |
| 2 | 一部スキップ（unknown あり・失敗ではない） |
| 3 | 要再認証（ヘッドレスでトークン失効） |

---

## ダッシュボードサイト（Vite + React・`site/`）

プレイリスト運用の閲覧と操作をブラウザで完結させる静的サイト。設計は
[docs/dashboard-design.md](docs/dashboard-design.md)。バックエンドサーバも DB も持たず、
**git がデータストア**（`data` ブランチ）・変更はすべて GitHub Actions が実行する。

```
収集: listen-log.yml（3時間ごと recently-played）+ nightly の sitegen.py
        → data ブランチへ commit → Vercel が取り込んでビルド
操作: ブラウザ（PAT）→ site-ops.yml を workflow_dispatch → siteops.py が Spotify を変更
```

- **機能**: 昨晩サマリ・エラー統計・重複聴き比べ&削除・unknown 振り分け・週間/累計ランキング・
  成長グラフ・分布・ヒートマップ・新譜/公式Top・1年前の今週・横断検索
- **生涯履歴の逆引き**: 2019年からの全再生（拡張ストリーミング履歴）を土台に、全曲・全アーティストの
  ランキングを順位どおり遡れる。曲やアーティストをタップすると、生涯の再生回数・順位・総再生時間・
  完走率・初回/最終再生日・年ごとの推移が出る
- **Wrapped**: 月間（2019-09〜）と年間（2019〜）。‹ › で1つずつ、セレクトで任意の時点へ
- **思い出**: ◯年前の今日・忘れられた名曲（よく聴いたのに直近1年ゼロ）
- **おすすめ**: 各ブロックに判定基準を明記。「似ている」は Last.fm の類似度 × 生涯再生回数で、
  1件ごとに根拠（どの曲・アーティストに似ているか）を出す。Spotify 公式の推薦 API は
  2024-11 に新規アプリ向けへ閉じられており、このアプリからは使えない（実測: `/v1/recommendations`
  と `related-artists` が 404、`audio-features` が 403）
- **試聴**: Spotify iframe embed（APIキー不要）。重複の聴き比べがサイト内で完結
- **操作**: fine-grained PAT をブラウザに1度貼るだけ（`Actions: Read and write`）。削除は全出現
  プレイリストから同時実行し undo を記録（[docs/dedupe-requirements.md](docs/dedupe-requirements.md) §6）。
  決定はキューに積んで一括送信するので、連打しても待たされず送信前なら取り消せる

```bash
cd site
npm install
npm run dev        # ローカル開発（public/data のフィクスチャを参照）
npm run build      # 本番ビルド（データは実行時 fetch）
```

セットアップの残タスク（再認証・Vercel デプロイ・Deploy Hook・PAT）は
[docs/dashboard-design.md](docs/dashboard-design.md) §12 の「本人の残タスク」を参照。

## 移行履歴（launchd → GitHub Actions）

launchd から GitHub Actions への移行は**完了**（2026-07-14）。

- ✅ コードを main にマージ・push、CI（ruff + pytest）green
- ✅ GitHub Secrets 4件を登録・`nightly-failure` / `unknown-tracks` ラベルを作成
- ✅ dry-run で本番相当パイプラインをクラウド検証（トークン復元 → 全パイプライン完走・変更系 API 未到達）
- ✅ 旧 launchd ジョブ（run_inbox / run_sync / run_archive）を unload
- ✅ 旧ラッパー `*.sh`・未使用の `spotify_icon.icns`・`~/dotfiles` の launchd plist を撤去

以降、自動運用は `nightly.yml`（毎晩 01:00 JST）のみ。ローカルは `python inbox.py` 等で直接実行する。

ロールバックが必要になった場合（launchd 運用へ戻す）は、`~/dotfiles` の plist 撤去コミットを revert →
`launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.yoshihide.run_<name>.plist` で再ロードする。
設計の全体像は [docs/implementation-plan.md](docs/implementation-plan.md) を参照。
