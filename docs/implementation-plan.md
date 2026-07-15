# 実装プラン — GitHub Actions 移行 + 全面改修（統合版）

作成: 2026-07-14（Claude Code / Fable 5）
ステータス: **プランのみ・未実装**。このプランは別セッション（Opus 等）が実装することを前提に書かれている。
背景となるレビューは [inbox-error-analysis.md](inbox-error-analysis.md) / [bugs-and-risks.md](bugs-and-risks.md) /
[improvements.md](improvements.md) / [fable5-redesign.md](fable5-redesign.md) 参照。
本プランはそれらの指摘の**全件**を解決策に落とし込んだもの（§12 の対応表で全件をトレースできる）。

---

## 0. 決定事項（本人承認済み・2026-07-14）

1. **実行基盤を launchd（Mac）→ GitHub Actions に移行する**。Mac が寝ていると走らない問題の根本解決
2. **エラー通知は macOS 通知を廃止し、GitHub Issue 起票にする**（GitHub モバイルアプリの push 通知が事実上の通知になる。Issue は消えない・履歴が残る・将来エージェントが拾える）
3. **local LLM（Windows マシン）は使わない**。判定チェーンの最後の砦は Gemini のまま
4. Gemini 依存自体を減らす: **ISRC 国コード + かな判定 + 永続キャッシュ**を優先し、Gemini は「キャッシュを埋める最後の手段」に格下げ

## 1. ゴールと非ゴール

**ゴール:**
- 毎晩 0:00 (JST) すぎに GitHub Actions 上で inbox → sync → sort → archive が直列実行される
- Mac の電源・スリープ・ネットワーク状態に依存しない
- 失敗・要対応事項はすべて GitHub Issue として観測できる（silent fail ゼロ）
- Gemini 429 が構造的に起きない（呼び出しが月数回レベルに減る）
- 競合クラス（同時実行・全置換の衝突・トークンキャッシュの取り合い）が設計から消滅

**非ゴール（やらない。[fable5-redesign.md](fable5-redesign.md) §6 と同じ）:**
- DB 導入・非同期化・Docker 化・spotipy からの乗り換え・local LLM 連携

## 2. アーキテクチャ Before / After

```
Before:                                    After:
Mac launchd 3ジョブ (0:00 同時起動)         GitHub Actions 1 workflow (01:00 JST)
├ inbox.sh → inbox.py ─┐                   nightly.yml:
├ sync.sh → sync.py    ├ 同じプレイリスト     checkout → pip → token復元
│     └ sort.sh(loop)  ├ 同じ .cache を      → inbox → sync → sort --all → archive（直列）
└ archive.sh           ┘ 同時に読み書き      → 状態ファイルを path 指定で auto-commit
通知: macOS Notifier.app                    → 失敗時: Issue 起票（dedupe あり）
状態: ローカルの sync_state.json            → unknown曲: 専用 Issue に追記
                                           通知: GitHub Issue（= モバイル push）
                                           状態: リポジトリにコミット（Git が履歴管理）
```

---

## 3. Phase 0 — 準備（実装前に1回だけ・人手を伴う）

実装セッションは最初にこの節を実施し、完了を確認してから Phase 1 へ進む。

### 3.1 GitHub 側のセットアップ

```bash
# Secrets（.env の値を移す。SPOTIFY_TOKEN_CACHE はファイルごと）
gh secret set SPOTIPY_CLIENT_ID
gh secret set SPOTIPY_CLIENT_SECRET
gh secret set GEMINI_API_KEY
gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify

# Issue ラベル（起票の dedupe キーに使う）
gh label create nightly-failure --color B60205 --description "夜間バッチの失敗"
gh label create unknown-tracks  --color FBCA04 --description "振り分け判定できなかった曲"
```

確認: `gh secret list` に4件、`gh label list` に2件が出ること。

### 3.2 認識しておくこと（実装judgementに影響）

- リポジトリは **public**。Secrets は Actions からしか読めず安全だが、コード・設定・コミットに秘密を書かない
- `GITHUB_TOKEN` による push は他の workflow をトリガーしない（自動コミット→無限ループの心配なし）
- schedule は default branch (main) の workflow だけが対象。cron は UTC 指定
- public リポジトリの schedule は「60日間リポジトリに活動がない」と自動停止されるが、毎晩の auto-commit が活動になるため実質問題ない
- **Spotify のトークン**: `.cache-spotify` の refresh_token は長期有効。ランナー上で access_token が更新されてもコミットしない（毎回 Secret から復元→refresh で足りる）。refresh_token 自体が失効した場合だけ人手の再認証が必要（§9.3 の Runbook）

---

## 4. Phase 1 — 共通基盤の集約（core.py / classify.py 新設）

[improvements.md](improvements.md) 1 と [fable5-redesign.md](fable5-redesign.md) 5 の実装。
4スクリプトの重複を `core.py` に、分類パイプラインを `classify.py` に集約する。
既存の `spotify_utils.py` は `core.py` に吸収して廃止（`free_redirect_port` は core 内に移す）。

### 4.1 core.py（骨格 — このシグネチャを維持すること）

```python
"""core.py — クライアント生成・ページング・バッチ・設定・ロギングの共通基盤"""
import logging, os, re, sys
from pathlib import Path

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / ".cache-spotify"

# exit code の意味づけ（fable5-redesign §3）
EXIT_OK = 0        # 成功
EXIT_FATAL = 1     # 致命的エラー（例外）
EXIT_AUTH = 3      # 再認証が必要（headless でトークン失効）

class AuthRequired(Exception):
    """headless 実行でトークンが無効。人手の再認証が必要"""

def is_headless() -> bool:
    return os.getenv("CI") == "true" or not sys.stdin.isatty()

def is_dry_run() -> bool:
    return os.getenv("DRY_RUN") == "1" or "--dry-run" in sys.argv

def build_client(scope: str) -> spotipy.Spotify:
    load_dotenv(BASE_DIR / ".env")  # CI では .env が無く no-op（env は Secrets 由来）
    for key in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"):
        if not os.getenv(key):
            raise RuntimeError(f"{key} が設定されていません")
    headless = is_headless()
    auth = SpotifyOAuth(scope=scope, cache_path=str(CACHE_PATH),
                        open_browser=not headless)
    if headless:
        # ブラウザフローを絶対に開始しない。キャッシュ→refresh で取れなければ即 AuthRequired
        token = auth.cache_handler.get_cached_token()
        token = auth.validate_token(token)   # 期限切れなら refresh_token で更新される
        if not token:
            raise AuthRequired("トークン失効。ローカルで再認証して Secret を更新すること（Runbook 参照）")
    else:
        _free_redirect_port()  # 対話実行時のみ（旧 spotify_utils.free_redirect_port を移設・§5.5 の修正込み）
    return spotipy.Spotify(auth_manager=auth)

def iter_playlist_tracks(sp, playlist_id: str, fields: str):
    """ページングを一元化。fields には必ず ',next' を含めて渡す"""
    results = sp.playlist_items(playlist_id, fields=fields,
                                additional_types=("track",), limit=100)
    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                yield track
        results = sp.next(results) if results.get("next") else None

def add_in_batches(sp, playlist_id: str, track_ids: list[str], batch: int = 100) -> None: ...
def remove_in_batches(sp, playlist_id: str, track_ids: list[str], batch: int = 100) -> None: ...

def parse_config(path: Path) -> dict[str, str]:
    """KEY=VALUE 形式（#コメント・空行スキップ）。既存3スクリプトのパーサを統合"""

def extract_playlist_id(url_or_id: str) -> str:
    """sort.py の実装を移設。全設定ファイルで URL/ID どちらも受け付ける（improvements §7）"""
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", url_or_id)
    return m.group(1) if m else url_or_id

def setup_logging(name: str) -> logging.Logger:
    """print を置き換える。フォーマット: [%(asctime)s] %(message)s（improvements §4）
    CI では stdout のみで良い（ログは Actions が保存する）。ローカルでは log/{name}.log にも書く"""
```

**注意:** `notify()`（macOS 通知）は**作らない**。通知は workflow レイヤの Issue 起票に一本化する（決定事項2）。

### 4.2 classify.py（判定チェーン — fable5-redesign §1 の実装）

```python
"""classify.py — アーティスト分類: キャッシュ → ISRC → かな → genres → Gemini(一括)"""
import json, re
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent / "artist_class_cache.json"
# 形式: {artist_id: {"name": str, "class": "japanese"|"western", "source": str, "date": "YYYY-MM-DD"}}
# git にコミットする（.gitignore に入れない）。壊れたら消して再生成できる

HIRAGANA_KATAKANA = re.compile(r"[぀-ヿｦ-ﾝ]")  # かな＋半角カナ。漢字は含めない（bugs §7 の修正）
KANJI_ONLY = re.compile(r"[一-鿿]")

JAPANESE_GENRES = { ... }  # inbox.py から移設

def classify_track(sp, track: dict, cache: dict) -> str:
    """'japanese' / 'western' / 'unknown' を返す。判定順は決定的・無料・高速な順"""
    artist = track["artists"][0]
    aid = artist["id"]

    # 1. 永続キャッシュ
    if aid in cache:
        return cache[aid]["class"]

    # 2. ISRC 国コード（liked tracks のレスポンスに含まれる。追加APIコストゼロ）
    #    実測: Japanese Musics の97%が JP / Western Musics は JP ゼロ
    isrc = (track.get("external_ids") or {}).get("isrc", "")
    if isrc[:2] == "JP":
        return _remember(cache, aid, artist["name"], "japanese", "isrc")

    # 3. かな判定（曲名・アーティスト名・アルバム名のいずれかにかなが含まれる）
    texts = [artist["name"], track.get("name", ""),
             (track.get("album") or {}).get("name", "")]
    if any(HIRAGANA_KATAKANA.search(t) for t in texts):
        return _remember(cache, aid, artist["name"], "japanese", "kana")
    # 漢字のみ（中国語の可能性）は japanese 確定にしない → 次の手段へ

    # 4. Spotify genres（現在ほぼ空だが、取れた場合は使う）
    genres = sp.artist(aid).get("genres", [])
    if genres:
        cls = "japanese" if _is_japanese_genre(genres) else "western"
        return _remember(cache, aid, artist["name"], cls, "genres")

    # 5. ここでは unknown を返し、呼び出し側が「unknown 全件」を集めて
    #    classify_unknowns_with_gemini() を1回だけ呼ぶ（曲ごとに呼ばない）
    return "unknown"

def classify_unknowns_with_gemini(unknown_artists: dict[str, str], cache: dict) -> dict[str, str]:
    """{artist_id: name} を一括判定して {artist_id: class} を返す。結果はキャッシュに書き戻す。
    GEMINI_API_KEY 未設定・呼び出し失敗時は空 dict（→ 呼び出し側で unknown のまま扱う）"""
    from google import genai
    client = genai.Client()  # GEMINI_API_KEY は env から
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=("Classify each music artist as japanese or western (non-japanese). "
                  f"Artists: {json.dumps(list(unknown_artists.values()), ensure_ascii=False)}"),
        config={"response_mime_type": "application/json",
                "response_schema": {"type": "object",
                                    "additionalProperties": {"enum": ["japanese", "western"]}}},
    )
    ...  # name→class を id に引き直してキャッシュ保存

def load_cache() -> dict: ...
def save_cache(cache: dict) -> None: ...   # 書き込みは atomic に（tmp に書いて rename）
```

**Phase 1 の DoD:** `python -c "import core, classify"` が通る。既存4スクリプトはまだ触らない（Phase 2 で移行）。

---

## 5. Phase 2 — 各スクリプトの改修

全スクリプト共通: `core.build_client()` / `iter_playlist_tracks()` / `parse_config()` / `setup_logging()` を使う形に書き換え、`--dry-run`（変更系 API を呼ばず「何をするはずだったか」をログに出す）を実装する。dry-run は Phase 3 の Actions 上での安全な初回検証に必須。

### 5.1 inbox.py

- 分類を `classify.py` に差し替え。ループ内では unknown を集めるだけにし、ループ後に `classify_unknowns_with_gemini()` を**1回だけ**呼んで再分類（[inbox-error-analysis.md](inbox-error-analysis.md) 修正1〜3）
- `notify()` と `NOTIFIER_APP` を削除（Issue 起票に置き換え。Linux ランナーに `open` は無い）
- **順序保証: プレイリストへの追加が成功してから liked を削除する**（現在も追加→削除の順だが、追加の例外で削除だけ走る経路がないか移行時に確認する）
- unknown 曲があれば `log/unknown_tracks.txt` に「曲名 / アーティスト」を書き出す（workflow がこれを Issue 化する）。exit code は 0 のまま（unknown は「失敗」ではなく「要人間判断」）
- `WESTERN_MUSICS_ID` のハードコードをやめ、inbox.txt に `WESTERN_MUSICS_ID=3gWeVkYJPREpkdCpDRjHFw` を追加して読む（improvements §7）

### 5.2 sync.py

- `append_artist_to_config()`: 追記前にファイル末尾の改行を保証（bugs §10）
- `user_playlist_create(..., public=True)` → 既存の手動プレイリストの公開設定に合わせる。**実装時に既存 AP の public 状態を API で確認し、多数派に合わせること**（bugs §10 の要確認事項をここで解消する）
- git commit/push ブロックは sync.sh ごと廃止 → workflow の auto-commit ステップに移行（§6）。**未コミットの sync.sh の変更（git push 追加）はこの移行で役目を終えるので、コミットせず破棄してよい**
- `sync_state.json` を `.gitignore` から外し、リポジトリにコミットする（Actions のランナーは毎回まっさらなので、状態はリポジトリで持ち回す）

### 5.3 sort.py

- `--all` オプションを追加: sort.txt を読んで全プレイリストを直列ソート（sort.sh のループを Python 側に移す。シェルの `grep -qi "auth"` ヒューリスティックは廃止）
- 全置換の安全化（bugs §1）: 直列実行になった時点で同時書き込み競合は消えるが、安全弁として
  取得直後に `sp.playlist(pid, fields="snapshot_id")` を保存し、置換直前に再取得して
  snapshot_id が変わっていたら**そのプレイリストはスキップして警告ログ**（次の晩に再ソートされる）
- `--analyze`（matplotlib）はローカル専用機能として残す。CI では import されない構造にする（関数内 import は現状のままで OK）

### 5.4 archive.py

- `get_source_track_ids()` にページングを追加（`fields` に `next` を含め、他と同じループに。bugs §6）。`limit=50` の Top50 前提を撤廃

### 5.5 旧 spotify_utils.py の `free_redirect_port()`（core.py に移設時に修正）

- `lsof -ti tcp:{port} -sTCP:LISTEN` に限定（接続中クライアントを殺さない。bugs §2）
- kill 後にポート解放をポーリング（0.2秒 × 最大25回）してから return
- そもそも headless では呼ばれない（Phase 1 の `build_client` 参照）ので、これはローカル再認証時だけの安全策

### 5.6 設定・環境ファイル

- `.env.example` を新規作成（bugs §9。プレースホルダのみ、実値禁止）
- `requirements.txt` をバージョン固定に（improvements §3）:

```
spotipy>=2.26,<3
python-dotenv>=1.2,<2
google-genai>=2.0,<3
```

- `requirements-dev.txt` 新規: `matplotlib`（--analyze 用）、`pytest`、`ruff`
- `.gitignore` から `sync_state.json` を削除（コミット対象化）。`.env` / `.cache-spotify` / `log/` は引き続き ignore
- docstring の旧ファイル名（sort.py / sync.txt / archive.txt）を現名に修正（bugs §12）

**Phase 2 の DoD:** ローカルで `python inbox.py --dry-run` / `python sync.py --dry-run` / `python sort.py --all --dry-run` / `python archive.py --dry-run` が全部 exit 0 で完走し、「実行するはずだった変更」がログに出る。実プレイリストは変化しない。

---

## 6. Phase 3 — GitHub Actions workflow

### 6.1 `.github/workflows/nightly.yml`（全文）

```yaml
name: nightly

on:
  schedule:
    - cron: '0 16 * * *'      # 01:00 JST（16:00 UTC）。scheduler 遅延は実測で不定なので分は0固定。混雑する 15:00 UTC 帯は外した
  workflow_dispatch:
    inputs:
      dry_run:
        description: '変更系APIを呼ばない検証モード'
        type: boolean
        default: false

permissions:
  contents: write   # 状態ファイルの auto-commit
  issues: write     # 失敗・unknown の Issue 起票

concurrency:
  group: nightly
  cancel-in-progress: false

env:
  TZ: Asia/Tokyo    # date.today() を JST 基準にする
  SPOTIPY_CLIENT_ID: ${{ secrets.SPOTIPY_CLIENT_ID }}
  SPOTIPY_CLIENT_SECRET: ${{ secrets.SPOTIPY_CLIENT_SECRET }}
  SPOTIPY_REDIRECT_URI: 'http://127.0.0.1:8000/callback'   # refresh のみで未使用だが spotipy の初期化に必要
  GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
  DRY_RUN: ${{ inputs.dry_run == true && '1' || '0' }}

jobs:
  nightly:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Restore Spotify token cache
        run: printf '%s' "$SPOTIFY_TOKEN_CACHE" > .cache-spotify
        env:
          SPOTIFY_TOKEN_CACHE: ${{ secrets.SPOTIFY_TOKEN_CACHE }}

      - name: Run nightly pipeline (inbox → sync → sort → archive)
        run: |
          set -o pipefail
          mkdir -p log
          python inbox.py   2>&1 | tee -a log/nightly.log
          python sync.py    2>&1 | tee -a log/nightly.log
          python sort.py --all 2>&1 | tee -a log/nightly.log
          python archive.py 2>&1 | tee -a log/nightly.log

      - name: Commit state files
        if: always() && env.DRY_RUN == '0'
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add sync.txt sort.txt sync_state.json artist_class_cache.json
          git diff --cached --quiet || {
            git commit -m "auto: 夜間実行による状態更新"
            git push
          }

      - name: Report unknown tracks as issue
        if: always()
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          [ -s log/unknown_tracks.txt ] || exit 0
          existing=$(gh issue list --label unknown-tracks --state open --json number --jq '.[0].number')
          body="$(printf '## %s の実行で判定できなかった曲\n\n```\n%s\n```\n' "$(date +%F)" "$(cat log/unknown_tracks.txt)")"
          if [ -n "$existing" ]; then
            gh issue comment "$existing" --body "$body"
          else
            gh issue create --title "振り分け判定できなかった曲がある" \
              --label unknown-tracks --body "$body"
          fi

      - name: Report failure as issue
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          run_url="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          tail_log=$(tail -40 log/nightly.log 2>/dev/null || echo "(ログなし)")
          body="$(printf '実行: %s\n\n### ログ末尾\n```\n%s\n```\n\n### 認証エラーの場合の復旧手順\nローカルで `python inbox.py` を対話実行（ブラウザ認証）→ `gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify`\n' "$run_url" "$tail_log")"
          existing=$(gh issue list --label nightly-failure --state open --json number --jq '.[0].number')
          if [ -n "$existing" ]; then
            gh issue comment "$existing" --body "$body"
          else
            gh issue create --title "nightly バッチが失敗している" \
              --label nightly-failure --body "$body"
          fi
```

### 6.2 設計上のポイント（実装時に変えないこと）

- **直列1ジョブ**: inbox → sync → sort → archive の順序が依存関係を表す（fable5-redesign §2）。並列化しない
- **Issue の dedupe**: 同種の open Issue があればコメント追記。失敗が続いても Issue は1本に集約され、直った日に人間（または将来のエージェント）が close する運用
- **auto-commit は path 指定の `git add`** のみ（bugs §4 のパス限定コミットと同じ思想。ランナー上では他の変更が混入する余地自体がないが、明示しておく）
- 失敗ステップがあっても `if: always()` で状態コミットと unknown 報告は実行する（途中まで進んだ状態を失わない）

**Phase 3 の DoD:**
1. `gh workflow run nightly --field dry_run=true` → `gh run watch` で exit 0
2. dry_run=false で手動実行 → 実プレイリストに反映・状態ファイルが auto-commit される
3. 故意に失敗させて（例: 一時的に `SPOTIPY_CLIENT_ID` を壊した dispatch）Issue が起票されること・2回目はコメント追記になることを確認

---

## 7. Phase 4 — 旧基盤の退役

**Phase 3 の DoD 達成後、実際の schedule 実行が2晩連続成功してから**着手する（並走期間中は launchd 側を止めておくため、先に unload だけは行う → 下記手順1）。

1. launchd の unload（Phase 3 の初回実行前にやる。二重実行防止）:

```bash
launchctl bootout "gui/$(id -u)/com.yoshihide.run_inbox"
launchctl bootout "gui/$(id -u)/com.yoshihide.run_sync"
launchctl bootout "gui/$(id -u)/com.yoshihide.run_archive"
```

2. 2晩成功後、plist を撤去（`~/dotfiles` リポジトリでの作業。symlink は trash で消す — **rm は使わない**）:

```bash
trash -v ~/Library/LaunchAgents/com.yoshihide.run_{inbox,sync,archive}.plist
cd ~/dotfiles && git rm LaunchAgents/com.yoshihide.run_{inbox,sync,archive}.plist
git commit -m "spotify-playlist-tools を GitHub Actions に移行したため launchd ジョブを撤去"
```

3. 本リポジトリの整理:
   - `inbox.sh` / `sync.sh` / `sort.sh` / `archive.sh` を削除（`git rm`）。ローカル実行は `python inbox.py` 等で直接行う
   - `spotify_utils.py` を削除（core.py に吸収済み）
   - `spotify_icon.icns` を削除（Notifier.app の素材。通知廃止で不要）。Notifier.app 本体（`~/Applications/Notifiers/`）は本人に確認してから trash
   - `__pycache__/archive_top50.cpython-311.pyc` 等の残骸は `trash` で掃除
4. README.md を全面同期（improvements §6）:
   - launchd の表 → GitHub Actions の説明（cron・Secrets 一覧・Issue 運用・再認証 Runbook）に差し替え
   - 判定順の記述を新チェーン（キャッシュ→ISRC→かな→genres→Gemini一括）に更新
   - `cp .env.example .env` が実在するようになったことを確認
   - 「macOS 通知」への言及を全削除

**Phase 4 の DoD:** `launchctl list | grep yoshihide.run_` が空。README の記述と実挙動が一致。

---

## 8. Phase 5 — テストと CI（improvements §5）

`.github/workflows/ci.yml`（push / PR 時）: `ruff check .` + `pytest`。

`tests/` に外部 API 非依存の純関数テストを置く:

| 対象 | ケース |
|---|---|
| `classify.HIRAGANA_KATAKANA` | ひらがな / カタカナ / 半角カナ → マッチ、漢字のみ・英語 → 非マッチ |
| `classify.classify_track` の順序 | キャッシュヒット時は API を呼ばない（sp をモック）/ ISRC "JP" 優先 / 漢字のみは unknown へフォールスルー |
| `core.parse_config` | コメント行・空行・値に `=` を含む行・末尾改行なし |
| `core.extract_playlist_id` | URL / URI (`spotify:playlist:...`) / 素の ID / `?si=` 付き |
| `sort.sort_tracks` | 曲数降順→名前→リリース日、コラボ曲の代表アーティスト選択 |
| `sort._normalize_date` | `"2023"` → `"2023-01-01"`、`"2023-05"` → `"2023-05-01"` |

Gemini・Spotify API のモック以上の統合テストは書かない（非ゴール）。

**Phase 5 の DoD:** CI が green。以後、auto-commit が何かを壊した場合も push 時の CI で検知できる。

---

## 9. 検証・運用

### 9.1 実装順序と依存

```
Phase 0（準備） → Phase 1（core/classify） → Phase 2（各スクリプト+dry-run）
→ Phase 3（workflow。初回実行前に launchd unload だけ先行実施）
→ 2晩の並走観察 → Phase 4（退役・README） → Phase 5（テスト/CI）
```

Phase 1+2 はローカルだけで完結し、いつでも巻き戻せる。Phase 3 が本番切替点。

### 9.2 ロールバック

- Phase 3 で問題が出たら: workflow ファイルを revert し、`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yoshihide.run_*.plist` で launchd を復帰（Phase 4 完了前なら plist はまだ残っている）
- `sync_state.json` / `artist_class_cache.json` は「消して再生成できる」設計を維持する（sync は初回扱いで順方向のみ、キャッシュは再判定で埋まる）

### 9.3 Runbook: Spotify 再認証（refresh_token 失効時）

```bash
cd ~/my-projects/spotify-playlist-tools
python inbox.py            # 対話実行 → ブラウザで認証（このときだけ redirect port を使う）
gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify
gh workflow run nightly --field dry_run=true   # 動作確認
```

この手順は失敗 Issue の本文に自動で入る（§6.1）ので、朝スマホで見ればやることがわかる。

---

## 10. 実装セッションへの指示（必読）

- **ユーザーのグローバルルール**: `rm` は使わない（`trash -v`）。コミットに Claude の署名・Co-Authored-By を入れない。グローバル Python 環境を汚さない（pyenv virtualenv `spotify-playlist-tools-3.11.9` を使う）
- public リポジトリである。ID・トークン・APIキーの実値をコード・ドキュメント・コミットメッセージに書かない
- 実プレイリストを壊さないこと。Phase 2 完了までは変更系 API を呼ぶ実行をしない（dry-run で検証）。Phase 3 の初回本番実行前に launchd を unload して二重実行を防ぐ
- 各 Phase 完了時にコミットする（Phase 単位でレビュー・巻き戻しできる粒度）
- 本プランと実装が乖離する判断をした場合は、このファイルに「変更記録」節を追記して理由を残す
- わからないこと（例: sync.py の `public=True` の意図、Notifier.app を消してよいか）は推測で埋めず本人に確認する

## 11. 本プランが更新・置換する既存ドキュメント

- [fable5-redesign.md](fable5-redesign.md) §2 の `nightly.sh` + launchd 1本化案 → **Actions workflow に置換**（直列化の思想は同じ、実行基盤が変わった）
- [fable5-redesign.md](fable5-redesign.md) §6 の「クラウド移行はやらない」→ **本人決定により撤回**（「Mac が寝ていると走らない問題が実際に出たら検討」の条件が成立した）
- [bugs-and-risks.md](bugs-and-risks.md) §11 の「ネットワークリトライ」修正案 → Actions 移行で**不要**（ランナーは常時ネット接続）
- [bugs-and-risks.md](bugs-and-risks.md) §3・§4 の launchd 時刻ずらし・git commit 修正案 → Actions 移行で**構造ごと解消**
- macOS 通知の改善案全般（improvements §8 の `-W` 問題含む）→ 通知廃止により**不要**

## 12. 指摘事項 → 解決箇所の対応表（全件トレース）

| 出典 | 指摘 | 解決 |
|---|---|---|
| bugs §1 | sort 全置換の競合・部分失敗 | §6 直列化 + §5.3 snapshot_id ガード |
| bugs §2 | free_redirect_port が無関係プロセスを殺す | §5.5（LISTEN 限定 + headless では不使用） |
| bugs §3 | launchd 0:00 同時起動 | §6 直列1ジョブ化で消滅 |
| bugs §4 | 自動 commit の index 巻き込み | §6 ランナー上の path 指定 add に移行 |
| bugs §5 | OAuth ヘッドレス・ハング / inbox silent fail | §4.1 AuthRequired + §6 失敗 Issue |
| bugs §6 | archive ページングなし | §5.4 |
| bugs §7 | 漢字のみ中国語の誤判定 | §4.2 かな限定 regex + フォールスルー |
| bugs §8 | inbox の exit code 常時 0 | §4.1 exit code 意味づけ + unknown はファイル経由で Issue 化 |
| bugs §9 | .env.example 不在 | §5.6 |
| bugs §10 | sync 細部（末尾改行・public=True 等） | §5.2 |
| bugs §11 | ネットワークスキップ・ログ二重化 | §11 のとおり移行で不要化 / ログは Actions に一本化 |
| bugs §12 | docstring 旧名・残骸 | §5.6 + §7 |
| improvements §1 | 重複コード集約 | §4 core.py / classify.py |
| improvements §2 | シェルラッパー統合 | §7 で .sh 自体を廃止（workflow が代替） |
| improvements §3 | requirements 固定 | §5.6 |
| improvements §4 | print → logging | §4.1 setup_logging |
| improvements §5 | テストゼロ | §8 |
| improvements §6 | README 乖離 | §7 |
| improvements §7 | 設定の一貫性（WESTERN_MUSICS_ID 等） | §5.1 + §4.1 extract_playlist_id 共通化 |
| improvements §8 | 通知ブロッキング・grep 誤判定ほか | 通知廃止 + §5.3 で grep 廃止 |
| redesign §1 | 分類パイプライン | §4.2 |
| redesign §2 | 夜間ジョブ1本化 | §6（Actions 版として実現） |
| redesign §3 | 観測可能性 | §6 Issue 起票 + exit code + ログ集約 |
| redesign §4 | ヘッドレス分離 | §4.1 |
| redesign §5 | コード構造 | §4 |
| inbox-error-analysis 修正1〜6 | Gemini 429 対策一式 | §4.2（キャッシュ/ISRC/一括化）+ §5.1 |
