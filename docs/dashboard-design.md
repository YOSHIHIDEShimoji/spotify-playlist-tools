# ダッシュボードサイト 設計書 兼 実装指示書

作成: 2026-07-17（Claude Code / Fable 5）。機能一覧・アーキテクチャ・未決定事項はすべて本人と対話で確定済み。

> **実装状況（2026-07-17 追記）:** Phase 1〜3 を実装・main へマージ済み。
> - Phase 1（データ層）: クラウド dry-run で data ブランチ全ファイル生成を検証済み
> - Phase 2（閲覧サイト）: 全6ページをモバイル実データでレンダリング検証済み・CI green
> - Phase 3（操作系）: siteops.py + site-ops.yml。検証ロジックを単体テスト済み。
>   **実削除の初回は本人立ち会いで**（§14-1・下記「残タスク」参照）
> - **未完（本人作業）:** Phase 0 の再認証（`python reauth.py`）・PAT 発行・Vercel デプロイ + Deploy Hook。
>   Phase 4（wrapped/streak の熟成）・Phase 5（リネーム）は後続。詳細は §12 の表。

---

## 0. 実装者への指示（最初に読む）

### 0.1 読む順序

1. この設計書（全部）
2. **[DESIGN-spotify.md](DESIGN-spotify.md) — ビジュアルデザインの正典。サイトの見た目に関する判断はすべてこのファイルに従う**
   （色・タイポグラフィ・レイアウト・コンポーネントの見た目で迷ったらこのファイルが答え）
3. [dedupe-requirements.md](dedupe-requirements.md) — 重複検出エンジンの要件（正規化ルール・安全要件・sync 整合）
4. 既存コード: `core.py` `classify.py` `inbox.py` `sync.py` — 共通基盤は必ず再利用する

**`docs/DESIGN-spotify.md` がまだ存在しない場合**: 実装を止めない。CSS 変数のプレースホルダテーマ
（ダーク基調・Spotify 緑 #1DB954 アクセント）で進め、完了報告に「デザイン未適用。DESIGN-spotify.md 配置後に
`site/src/theme.css` のトークンを差し替えること」と明記する。デザイン適用が theme.css の差し替えだけで
済むよう、**色・余白・角丸・フォントのハードコードを禁止**する（すべて CSS 変数経由）。

### 0.2 確認なしで進めてよい範囲・止まる箇所

- **進めてよい**: Phase 1〜5 の全実装・テスト・コミット・main へのマージ（CI green が条件）・
  Vercel プロジェクト作成（`vercel` CLI・本人アカウント・プロジェクト名 `spotify-dashboard`）・
  data ブランチ作成・GitHub ラベル作成
- **本人に依頼して止まる**（credential は実装者が扱わない）:
  1. Spotify 統合スコープでの再認証と `gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify`（§11 手順を提示）
  2. fine-grained PAT の発行（サイトの設定画面に本人が貼る。**リポジトリの Secret には入れない**）
  3. Vercel Deploy Hook の作成（ダッシュボードで1クリック）と `gh secret set VERCEL_DEPLOY_HOOK`
- スコープ不足の間も実装・検証が止まらないよう、新スコープ依存の処理は **graceful skip**（§6.4）で書く

### 0.3 作業規約

- ブランチ `feat/dashboard` で作業し、**Phase 単位で main へマージ**（CI green + その Phase の完了条件を満たすこと）
- コミット: 日本語・既存スタイル（`feat:` `ci:` `docs:`）・**AI 署名/Co-Authored-By を入れない**・
  `git add` はパス指定（無関係ファイル混入禁止）
- `rm` 禁止（`trash -v` を使う）。Python は既存 venv `spotify-playlist-tools-3.11.9`（`.python-version` 参照）
- プレイリストへの変更系 API は site-ops 経由のみ。**nightly 拡張・listen-log は読み取り + data コミットだけ**
- 変更系のテストは必ず自分のダミープレイリスト or `--dry-run` で実測してからマージ

---

## 1. 一言で・存在理由

Spotify 風デザインの静的サイトで、プレイリスト運用の**閲覧と操作をすべて完結**させる。
重複の聴き比べ・削除、unknown 曲の振り分けまでサイト内で行い、**CLI はもう叩かない**。
バックエンドサーバも DB も持たない — 変更はすべて GitHub Actions が実行し、git がデータストア。

## 2. 決定事項(すべて 2026-07-17 本人確認済み)

| 論点 | 決定 |
|---|---|
| ホスティング | **Vercel**(静的ビルドのみ・サーバレス関数なし)。GitHub Pages 不採用 |
| プロジェクト名 | **`spotify-dashboard`**。完成後の Phase 5 で**リポジトリ名とローカルディレクトリ名も `spotify-dashboard` に変更** |
| DB | **使わない。git が DB**(追記型 JSONL + JSON、書き込みは Actions のみ) |
| 聴取ログの公開 | **public リポジトリにそのままコミットして良い**(音楽の好み・聴取時間帯の公開を本人が受容) |
| 操作の認証 | **fine-grained PAT**(本人発行→サイトに1度貼って localStorage 保存) |
| サイト内操作 | **v1 の核**(重複削除・unknown 振り分け・undo までサイトで完結) |
| 聴取ログ頻度 | **3時間ごと**の専用 cron |
| おすすめ | 新譜ウォッチ + 忘れかけ再発掘 + Spotify 公式 Top の3本(公式 recommendations API は死亡済みのため) |
| デザイン | **docs/DESIGN-spotify.md が正典**(本人が配置。未配置の間はプレースホルダで進める — §0.1) |

## 3. 機能一覧(確定15 + 見送り1)

| # | 機能 | データ源 | 種別 |
|---|---|---|---|
| 1 | 昨晩のサマリ(実行タイムライン・履歴遡り) | runs.jsonl | 閲覧 |
| 2 | エラー統計(成功率・失敗履歴・連続成功日数) | runs.jsonl | 閲覧 |
| 3 | 要再認証バナー(復旧手順つき) | auth_status.json | 閲覧 |
| 4 | 重複整理: embed 聴き比べ→残す選択→削除 | dupes.json | **変更** |
| 5 | unknown 曲: embed で聴いて邦楽/洋楽ボタン | unknown.json | **変更** |
| 6 | 「両方残す」管理(一覧・取り消し) | dedupe_keep.json | **変更** |
| 7 | 週で一番聴いた曲(週間 Top) | listening/*.jsonl | 閲覧 |
| 8 | 累計再生ランキング(サイト稼働開始日から) | listening/*.jsonl | 閲覧 |
| 9 | プレイリスト成長グラフ | stats_history.jsonl | 閲覧 |
| 10 | アーティスト分布・リリース年代分布 | stats.json | 閲覧 |
| 11 | 聴取ヒートマップ(曜日×時間帯・JST) | heatmap.json | 閲覧 |
| 12 | 連続聴取 streak・マイルストーン | listening/*.jsonl | 閲覧 |
| 13 | おすすめ3種 | releases.json, top.json | 閲覧 |
| 14 | 1年前の今週の Top50(archive の added_at から) | archive_weekly.json | 閲覧 |
| 15 | 全プレイリスト横断検索 | search_index.json | 閲覧 |
| 16 | 月間 Wrapped 風まとめ | wrapped/*.json | 閲覧 |
| — | 灰色曲アラート | 本人判断で**不採用**。実装しない |

**実装しない(制約として本人合意済み)**: 「今聴いてる曲」リアルタイム表示(トークンをブラウザに晒せない)。
「最後に聴いた曲(最大3時間遅れ)」で代替。聴取履歴の過去遡りは API 制約上不可能。

## 4. 全体アーキテクチャ

```
【読み経路】                              【書き経路】
Spotify API                               ブラウザ(静的サイト)
  │ 3時間ごと / nightly                     │ fine-grained PAT(localStorage)
  ▼                                        ▼
GitHub Actions ──commit──→ data ブランチ   GitHub REST workflow_dispatch(site-ops.yml)
  │                          │              │ inputs: op + payload(JSON文字列)
  │ deploy hook (curl)       │              ▼
  ▼                          │            GitHub Actions(Secrets で Spotify を叩く)
Vercel ビルド ←─codeload────┘              │ 検証→削除/振り分け/undo→undo記録
  │ data/*.json を public/ に同梱           └→ data 更新 → deploy hook → 2〜4分でサイト反映
  ▼                                            (サイト側は楽観的 UI で「処理中」表示)
ブラウザ(閲覧は認証なし)
```

- ブラウザが直接叩く外部 API は GitHub REST(CORS 対応)のみ。試聴は Spotify iframe embed
  (`https://open.spotify.com/embed/track/{id}` — API キー不要。Spotify ログイン済みブラウザでフル再生、未ログイン30秒)
- 秘密情報(Spotify トークン・Gemini キー・Deploy Hook)は GitHub Secrets から出ない

## 5. リポジトリ構成・データ設計

### 5.1 新規ファイル配置

```
(リポジトリ直下・既存の flat 構成に合わせる)
├── dedupe.py        # 検出エンジン: scan(→dupes.json) + apply(削除実行)。dedupe-requirements.md 準拠
├── listen_log.py    # recently-played 取得→JSONL 追記(3時間ごと)
├── sitegen.py       # data/*.json 一式の生成(nightly 後段)
├── siteops.py       # site-ops の op ディスパッチャ(検証→実行→undo 記録)
├── site/            # Vite + React + TS の SPA(§8)
└── .github/workflows/
    ├── listen-log.yml   # 新規
    └── site-ops.yml     # 新規(nightly.yml は拡張)
```

### 5.2 data ブランチ

- **orphan ブランチ `data`** に `data/` ディレクトリのみを置く(Phase 1 で作成)。
  main の履歴を1日6〜9件の自動コミットで汚さないため
- Actions からは dual-checkout: `actions/checkout@v5`(main・コード) + `actions/checkout@v5 with {ref: data, path: _data}`
- push は rebase リトライ(`git pull --rebase && git push` を最大3回)。追記型なので衝突しない
- nightly.yml の既存「Commit state files」(sync.txt 等 → main)はそのまま。**データ系は data ブランチ**と分離

### 5.3 データスキーマ(実装者はこの形を厳守。サイト側 TS 型もここから起こす)

```jsonc
// data/listening/2026-07.jsonl — 1再生1行・追記のみ(3時間ごと)
{"played_at":"2026-07-17T13:02:11Z","track_id":"1QV6tiMFM6fSOKOGLMHYYg","name":"Poker Face",
 "artists":[{"id":"1HY2Jd0NmPuamShAr6KMms","name":"Lady Gaga"}],"duration_ms":237200}

// data/runs.jsonl — nightly 1実行1行
{"date":"2026-07-17","run_id":29349247667,"status":"success","dry_run":false,
 "steps":{"inbox":{"processed":4,"japanese":1,"western":3,"unknown":0},
          "sync":{"added":3,"removed":0,"new_playlists":0},
          "sort":{"playlists":8,"skipped":0},"archive":{"added":0}},"duration_s":43}

// data/auth_status.json
{"token_ok":true,"checked_at":"2026-07-17T16:05:00Z","missing_scopes":[]}

// data/dupes.json — group.id は「トラックID昇順連結の sha1 先頭12桁」(再生成しても安定)
{"generated_at":"...","groups":[{"id":"g-3f9a1c2b4d5e","tier":"B","reason":"isrc",
  "tracks":[{"id":"...","name":"Photograph","artists":["Ed Sheeran"],"album":"x (Deluxe)",
    "album_type":"album","release_date":"2014-06-20","duration_ms":258987,"popularity":74,
    "isrc":"GBAHS1400099","playlists":[{"id":"3gW...","name":"Western Musics"}]}]}]}

// data/unknown.json
{"generated_at":"...","tracks":[{"id":"...","name":"...","artists":[{"id":"...","name":"..."}],"isrc":""}]}

// data/dedupe_keep.json — 「両方残す」決定(group.id と同じ規則)
{"groups":[{"id":"g-...","track_ids":["...","..."],"decided_at":"2026-07-17"}]}

// data/stats_history.jsonl — nightly 1行/プレイリスト(成長グラフの原本)
{"date":"2026-07-17","playlist_id":"3gW...","name":"Western Musics","count":456}

// data/stats.json — 分布(nightly 再生成)
{"generated_at":"...","artists_top":[{"name":"Justin Bieber","count":49}],
 "decades":[{"decade":2010,"count":180}]}

// data/heatmap.json — JST 変換済み集計
{"generated_at":"...","cells":[{"dow":0,"hour":23,"count":14}]}

// data/top.json — Spotify 公式 Top
{"generated_at":"...","tracks":{"short_term":[{"id":"...","name":"...","artists":["..."],"rank":1}],
 "medium_term":[],"long_term":[]},"artists":{"short_term":[],"medium_term":[],"long_term":[]}}

// data/releases.json / releases_seen.json — 新譜(直近14日) / 既読 album_id 集合
{"generated_at":"...","items":[{"album_id":"...","album_name":"...","album_type":"single",
 "artist":"...","release_date":"2026-07-15","first_seen":"2026-07-17"}]}

// data/archive_weekly.json — added_at の ISO 週集計(「その週に初めて Top50 入りした曲」)
{"generated_at":"...","weeks":[{"iso_week":"2025-W29","tracks":[{"id":"...","name":"...","artists":["..."],"added_at":"..."}]}]}

// data/search_index.json — 横断検索(管理プレイリスト全件、クライアントサイド検索)
{"generated_at":"...","tracks":[{"id":"...","name":"...","artists":["..."],"playlists":["Western Musics","Ed Sheeran"]}]}

// data/wrapped/2026-07.json — 月末 nightly が生成
{"month":"2026-07","plays":412,"top_tracks":[{"id":"...","name":"...","count":18}],
 "top_artists":[{"name":"...","count":40}],"new_tracks":9,"peak":{"dow":5,"hour":22}}

// data/undo/2026-07-17T220301.json — site-ops の削除記録(復元に必要な全情報)
{"id":"2026-07-17T220301","op":"dedupe-apply","created_at":"...",
 "removed":[{"track_id":"...","name":"...","playlists":["3gW...","1PV..."]}]}
```

## 6. 収集パイプライン(GitHub Actions)

### 6.1 `listen-log.yml`(新規)

```yaml
name: listen-log
on:
  schedule:
    - cron: '0 */3 * * *'
  workflow_dispatch:
permissions:
  contents: write
concurrency:
  group: data-branch
  cancel-in-progress: false
env:
  TZ: Asia/Tokyo
  SPOTIPY_CLIENT_ID: ${{ secrets.SPOTIPY_CLIENT_ID }}
  SPOTIPY_CLIENT_SECRET: ${{ secrets.SPOTIPY_CLIENT_SECRET }}
  SPOTIPY_REDIRECT_URI: 'http://127.0.0.1:8000/callback'
jobs:
  poll:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v5
      - uses: actions/checkout@v5
        with: { ref: data, path: _data }
      - uses: actions/setup-python@v6
        with: { python-version: '3.11', cache: pip }
      - run: pip install -r requirements.txt
      - name: Restore Spotify token cache   # nightly.yml と同一の手順を流用
        run: printf '%s' "$SPOTIFY_TOKEN_CACHE" > .cache-spotify
        env: { SPOTIFY_TOKEN_CACHE: '${{ secrets.SPOTIFY_TOKEN_CACHE }}' }
      - run: python listen_log.py --data-dir _data/data
      - name: Push if changed               # rebase リトライ最大3回、変更なしなら何もしない
        working-directory: _data
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data && git diff --cached --quiet && exit 0
          git commit -m "data: 聴取ログ更新"
          for i in 1 2 3; do git push && exit 0 || git pull --rebase; done; exit 1
```

`listen_log.py` の仕様:
- カーソル管理: `data/listening/.cursor`(最後の `played_at` の unix ms)。`sp.current_user_recently_played(after=cursor)`
- `played_at` で重複排除して当月 JSONL に追記。新規0件なら何も書かずに exit 0
- スコープ `user-read-recently-played` が無い場合: `auth_status.json` の `missing_scopes` に記録して exit 0(§6.4)

### 6.2 `nightly.yml` の拡張(既存ステップは変更しない)

pipeline ステップの後に追加:
1. **サマリ集約**: 各ツールに `core.write_step_summary(tool, dict)`(新設・`log/step_summary/<tool>.json` に書く、
   gitignore 済み領域)を仕込み、`sitegen.py` が集約して `runs.jsonl` に1行追記(`GITHUB_RUN_ID` 使用)
2. **`python sitegen.py --data-dir _data/data`**: dupes(dedupe scan)・unknown・stats_history・stats・heatmap・
   top・releases・archive_weekly・search_index・auth_status を生成。月末日なら wrapped も
3. data ブランチへ commit & push(§6.1 と同じリトライ)
4. `curl -fsS -X POST "$VERCEL_DEPLOY_HOOK"`(secret 未設定なら `::notice` を出してスキップ — Phase 1 段階でも動くように)

nightly.yml 冒頭に dual-checkout を追加し、`concurrency.group` を `nightly` から **`spotify-serial`** に変更
(site-ops と共有し、プレイリスト変更の同時実行を直列化する)。

### 6.3 API 消費の見積もり(設計根拠)

新譜ウォッチが最大(管理プレイリスト在籍+フォロー中 ≒ 100〜150 アーティスト × `/artists/{id}/albums` 1req)。
nightly 全体で +200 req 程度・spotipy が 429 リトライ内蔵、実測43秒→2〜3分になる想定。問題ない。

### 6.4 graceful skip(スコープ不足への耐性)

新スコープ(`user-read-recently-played` / `user-top-read` / `user-follow-read`)が必要な処理は、
`SpotifyException` の 403 を捕捉して該当出力だけスキップし、`auth_status.json.missing_scopes` に記録する。
**再認証前でも既存スコープ分(dupes/unknown/stats/search 等)は全部生成される** — Phase 0 の完了を待たずに
Phase 1〜2 を実装・検証できるようにするため。サイトは missing_scopes を見て該当ウィジェットに
「再認証で有効化」プレースホルダを出す。

## 7. 操作系(site-ops.yml — このサイトの核)

### 7.1 ワークフロー定義

```yaml
name: site-ops
on:
  workflow_dispatch:
    inputs:
      op:
        type: choice
        options: [dedupe-apply, classify-apply, keep-apply, undo]
      payload:
        type: string          # JSON 文字列(GitHub の inputs 上限 64KB、実運用は数KB)
        required: true
permissions:
  contents: write
concurrency:
  group: spotify-serial       # nightly と直列化
  cancel-in-progress: false
# steps: dual-checkout → python siteops.py --op "$OP" --payload "$PAYLOAD" --data-dir _data/data
#        → data push(リトライ) → deploy hook
#        失敗時: 既存 nightly-failure と同じ Issue 起票ステップを流用
```

### 7.2 op 仕様(`siteops.py`)

| op | payload | 動作 |
|---|---|---|
| `dedupe-apply` | `{"decisions":[{"group_id":"g-...","keep":["id1"],"remove":["id2"]}]}` | ①現在の dupes.json とグループ ID・トラック ID を照合(**不一致は全体拒否**) ②keep∪remove がグループ全体と一致することを検証 ③remove を**全出現プレイリストから同時削除**(dedupe-requirements §6 の sync 整合) ④undo 記録 ⑤dupes.json 再生成 |
| `classify-apply` | `{"decisions":[{"track_id":"...","class":"japanese"}]}` | ①unknown.json に存在するか検証 ②該当プレイリストへ追加→お気に入りから削除(inbox.py の移動ロジックを関数化して共用) ③`artist_class_cache.json` に `source:"manual"` で記録(main へコミット) ④unknown.json から除去 |
| `keep-apply` | `{"add":[{"group_id":"...","track_ids":[...]}],"remove":["g-..."]}` | dedupe_keep.json を更新。add はグループ実在検証 |
| `undo` | `{"undo_id":"2026-07-17T220301"}` | undo ファイルの全トラックを各プレイリストへ再追加(並びは翌晩 sort が直す)。使用済み undo はリネームで無効化 |

### 7.3 安全要件

1. **サーバ側検証が唯一の防衛線**(payload はブラウザ発 = 信用しない)。検証失敗は何も変更せず exit 1 → Issue
2. 削除は必ず undo 記録とセット。undo 記録の書き込みが失敗したら削除自体を中止
3. PAT はブラウザ localStorage のみ。リポジトリ・データファイル・URL に載せない
4. 変更系 API を呼ぶのは `siteops.py`(と既存 inbox/sync/sort/archive)だけ。sitegen/listen_log は読み取り専用

### 7.4 サイト側の操作 UX

- 初回設定画面で PAT を貼る → `GET /repos/{owner}/{repo}/actions/workflows` で疎通確認 → localStorage 保存
- PAT 未設定なら操作ボタンをすべて disabled(閲覧専用モード)
- dispatch 成功後、対象グループを localStorage で「処理中」表示 → データ更新(dupes.json の generated_at 変化)で解消。
  3分経って未反映なら Actions の run 一覧へのリンクを表示

## 8. サイト実装(site/)

```
site/
├── package.json         # vite + react + react-router-dom + recharts + typescript
├── vite.config.ts       # define: __REPO__ = VERCEL_GIT_REPO_OWNER + "/" + VERCEL_GIT_REPO_SLUG
├── index.html
├── public/data/         # ローカル開発用フィクスチャ(本番はビルド時に data ブランチを注入)
└── src/
    ├── theme.css        # デザイントークン(色/余白/角丸/フォント)— DESIGN-spotify.md をここに反映
    ├── lib/data.ts      # fetch("/data/*.json") + 型定義(§5.3 準拠)
    ├── lib/github.ts    # workflow_dispatch 呼び出し(PAT, __REPO__)
    ├── lib/pat.ts       # localStorage 管理 + 疎通確認
    ├── components/      # EmbedPlayer / SummaryTimeline / AuthBanner / DupeGroupCard / Heatmap / ...
    └── pages/           # Home / Organize / Stats / Discover / Memories / Search
```

| ページ | 内容(機能# は §3) |
|---|---|
| Home | #1 サマリ / #2 エラー統計 / #3 再認証バナー(全ページ共通ヘッダ) / #7 今週Top / #8 累計 / 最後に聴いた曲 |
| Organize | #4 重複整理(tier 別・embed 聴き比べ・残す選択→dispatch) / #5 unknown 振り分け / #6 keep 管理 / undo 一覧 |
| Stats | #9 成長グラフ / #10 分布 / #11 ヒートマップ / #12 streak |
| Discover | #13 新譜・忘れかけ・公式 Top(すべて embed つき) |
| Memories | #14 1年前の今週 / #16 月間 Wrapped |
| Search | #15 横断検索(search_index.json をクライアントで絞り込み・外部 API なし) |

**Vercel 設定**(実装者が `vercel` CLI で作成・本人アカウント・プロジェクト名 `spotify-dashboard`):
- Root Directory: `site/` / Framework: Vite / production branch: `main`
- Build Command: `npm run build:vercel` — data ブランチを codeload の tarball で取得して同梱
  (public repo なのでトークン不要。**リポジトリ名は `VERCEL_GIT_REPO_OWNER`/`VERCEL_GIT_REPO_SLUG` から取得** — Phase 5 のリネームに自動追従):

```jsonc
// package.json scripts
"build:vercel": "bash fetch-data.sh && vite build",
// fetch-data.sh:
//   curl -fsSL https://codeload.github.com/$VERCEL_GIT_REPO_OWNER/$VERCEL_GIT_REPO_SLUG/tar.gz/refs/heads/data \
//     | tar -xz --strip-components=1 -C public "$VERCEL_GIT_REPO_SLUG-data/data"
```

- モバイル(縦画面)ファースト。朝スマホで見る UX が主戦場(受け入れ基準 §14-7)

## 9. おすすめ3種の実装方針

1. **新譜ウォッチ**: フォロー中(`user-follow-read`) + 管理プレイリスト在籍アーティストの
   `/artists/{id}/albums?include_groups=album,single` を nightly 走査 → 直近14日を releases.json へ。既読は releases_seen.json
2. **忘れかけ再発掘**: v1 は「公式 Top long_term に居るのに直近30日のログに出ない曲」(**初日から機能する近似**)。
   ログ90日蓄積後に「自前ログで昔頻出・直近ゼロ」基準へ切替(sitegen 内の閾値変更のみで済む構造にする)
3. **公式 Top**: `user-top-read` の3期間をそのまま top.json へ

## 10. CI・テスト

- `ci.yml` に site ジョブ追加: Node 22 / `npm ci` / `tsc --noEmit` / `vite build`(データはフィクスチャ使用・codeload を叩かない)
- Python: 新規純関数(カーソル管理・週集計・ヒートマップ集計・グループID生成・payload 検証)に pytest 追加。ruff 通過
- siteops の検証ロジックはモックでユニットテスト必須(不正 payload 拒否・グループ不一致拒否)

## 11. Phase 0 — 本人にしかできない作業(実装者は依頼文を提示して待つ)

1. **統合スコープで再認証**: `core.py` の SCOPE を全ツール共通の統合定数にした後
   (`playlist-* + user-library-* + user-read-recently-played + user-top-read + user-follow-read`)、
   ローカルで `python inbox.py` → ブラウザ認証 → `gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify`
2. **fine-grained PAT 発行**: 対象=このリポジトリのみ / Repository permissions = **Actions: Read and write** のみ /
   期限1年 → サイトの初回設定画面に貼る(リポジトリ Secret には入れない。期限切れ時は再発行して貼り直し)
3. **Vercel Deploy Hook 作成**(Phase 2 でプロジェクト作成後): ダッシュボード → Settings → Git → Deploy Hooks
   (branch: main)→ `gh secret set VERCEL_DEPLOY_HOOK` に URL を登録
4. **docs/DESIGN-spotify.md の配置**(未配置でも実装は進む — §0.1)

## 12. フェーズ分割と完了条件

| Phase | 状態 | 内容 | 完了条件(マージ判定) |
|---|---|---|---|
| 1 | ✅ 完了 | データ基盤: core 拡張(統合 SCOPE・write_step_summary)/ listen_log.py / sitegen.py / dedupe.py(scan)/ listen-log.yml / nightly 拡張 / data ブランチ作成 | nightly 実行後、data ブランチに §5.3 の全ファイルが生成される(新スコープ分は missing_scopes 記録で可)。CI green |
| 2 | ✅ 完了（デプロイ除く） | サイト閲覧版: site/ 全ページ + Vercel プロジェクト作成・デプロイ | スマホ実機幅で全ページ表示。デザインは DESIGN-spotify.md。**Vercel デプロイは本人作業（Phase 0-3）** |
| 3 | ✅ コード完了 | 操作系: siteops.py + site-ops.yml + PAT UI + undo | 検証ロジック単体テスト済み。**受け入れ §14-1（実削除）の初回は本人立ち会いで実測** |
| 4 | ⬜ 後続 | 熟成系: wrapped(月末生成)・streak・忘れかけの自前ログ切替構造 | 該当 JSON とページが揃う |
| 5 | ⬜ 後続 | **リネーム**: `gh repo rename spotify-dashboard` → ローカル `mv`(§12.1) | 旧名参照が残っていない(grep で確認)。サイト再デプロイ成功 |

**本人の残タスク（Phase 0 の未完分）:**
1. `python reauth.py` で統合スコープ再認証 → `gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify`
   （聴取ログ・公式Top・新譜が有効化。**操作系 dedupe/classify は再認証なしでも動く**）
2. Vercel でプロジェクト `spotify-dashboard` 作成（Root Directory=`site/`・Build=`npm run build:vercel`・production=main）→ デプロイ
3. Vercel Deploy Hook 作成 → `gh secret set VERCEL_DEPLOY_HOOK`（nightly/site-ops が再ビルドを起動）
4. fine-grained PAT 発行（対象=本リポジトリ / Actions: Read and write / 期限1年）→ サイトの「操作 OFF」から貼る
5. 初回の実削除を1件、サイトで実行して受け入れ §14-1 を確認（本人立ち会い推奨）

### 12.1 Phase 5 リネーム手順(本人決定: 「最後に変更する」)

1. `gh repo rename spotify-dashboard`(GitHub が旧 URL を自動リダイレクト。Issues/Secrets/Actions/PAT の
   リポジトリ選択は repo ID 追従なので無傷。Vercel の Git 連携も repo ID 追従)
2. ローカル: `cd ~/my-projects && mv spotify-playlist-tools spotify-dashboard` →
   `git remote set-url origin git@github.com:YOSHIHIDEShimoji/spotify-dashboard.git`
3. README・docs 内の旧リポジトリ名表記を一括更新(コード側は `VERCEL_GIT_REPO_*` / `GITHUB_REPOSITORY` 参照で
   リネーム非依存に書いてあるはず — grep `spotify-playlist-tools` で残存ゼロを確認)
4. 任意: venv を `pyenv virtualenv 3.11.9 spotify-dashboard-3.11.9` で作り直し `.python-version` 更新
   (旧名 venv のままでも動作に支障はない)

## 13. セキュリティ・プライバシー(決定済み)

- 聴取ログの生データは public — **本人が受容済み**(2026-07-17)。非公開化したくなったら
  Vercel Deployment Protection + private データリポジトリ分離に移行(その時に再設計)
- PAT 権限は単一リポジトリ・Actions: write のみ。漏れても「ワークフロー起動」まで。中身の検証は §7.3
- サイト閲覧は認証なし(データが public な前提のため守るものがない)

## 14. 受け入れ基準(Phase 3 完了 = サイト成立の定義)

1. サイトで重複グループを聴き比べ→残す方を選択→数分後に Spotify 側から消え、undo 記録が残り、
   翌 nightly が no-op で完走する(sync の巻き込み削除・AP 残留が起きない)
2. unknown 曲をサイトで「邦楽」指定→ Japanese Musics に入り、キャッシュに `manual` で記録され、以後 unknown に出ない
3. PAT 未設定ブラウザでは操作ボタンが全て無効・閲覧は全機能動く。不正 payload(改竄 group_id)は拒否され Issue が立つ
4. 週間 Top・累計・ヒートマップが listening JSONL の手計算と一致する
5. 昨晩のサマリの数字が Actions 実行ログと一致する
6. トークン失効時、次ジョブ後にサイトへ再認証バナーが出る(missing_scopes 時は該当ウィジェットのみプレースホルダ)
7. スマホ(縦 390px)で全ページが実用的に閲覧・操作できる
8. `dedupe.py` の検出が dedupe-requirements.md §8-1,2 を満たす(ISRC ペア検出・横断グループ統合)

## 15. 既存ドキュメントへの影響

- [dedupe-requirements.md](dedupe-requirements.md): UI(対話 CLI)と「見張り後で検討」は本書が上書き。
  エンジン・安全要件・sync 整合・undo は全面継承(冒頭に注記済み)
- [feature-ideas.md](feature-ideas.md): A-1・B-1・C-1・E系を本書が実装に昇格。C-2(灰色曲)は不採用が確定
- README.md: Phase 2 完了時にサイト URL とアーキテクチャ概要を追記、Phase 5 でリポジトリ名を更新
