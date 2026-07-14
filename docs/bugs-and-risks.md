# 修正点 — バグ・データ損失リスク（プロジェクト全体）

作成: 2026-07-14（Claude Code によるプロジェクトレビュー）
inbox.sh のエラー自体は [inbox-error-analysis.md](inbox-error-analysis.md) 参照。ここではそれ以外。

## 重大（データ損失・破壊の可能性）

### 1. sort.py の全置換はスナップショット競合で曲を消しうる

[sort.py:95-98](../sort.py) の `replace_playlist()` は「取得時点の曲リスト」でプレイリストを
**全置換**する。取得（`get_all_tracks`）から置換完了までの間に他プロセスが追加した曲は
**黙って消える**。

これは理論上の話ではない。launchd の3ジョブ（inbox / sync→sort / archive）は**全部 0:00 ちょうどに
同時起動**しており（`~/dotfiles/LaunchAgents/*.plist` で実確認）、inbox.py が Western Musics に
追加している最中に sort.py が Western Musics（sort.txt の4行目に入っている）を全置換するシナリオが
毎晩起こりうる。

さらに 100 曲超のプレイリストは「最初の100曲で replace → 残りを add」と複数 API 呼び出しに
分かれるため、途中で失敗するとプレイリストが**部分状態（先頭100曲だけ）**で残る。

**修正案:**
- 実行時刻をずらす or 1本のスクリプトで直列実行する（→ 3. 参照）が最も簡単で確実
- より堅牢にするなら、取得時に `snapshot_id` を保存し、置換直前に再取得して snapshot_id が
  変わっていたらリトライ（Spotify API はプレイリストごとに snapshot_id を返す）

### 2. free_redirect_port() が無関係なプロセスを殺す

[spotify_utils.py:16](../spotify_utils.py) の `lsof -ti :8000` は
**`-sTCP:LISTEN` を指定していない**ため、そのポートに接続中のクライアント側プロセスも列挙される。
さらにポート 8000 は開発サーバの定番ポート（`python -m http.server`、Django、各種 dev server）。
深夜の launchd 実行が、たまたま 8000 番で動いていた無関係の開発サーバを SIGTERM で殺す。

**修正案:**
- `lsof -ti tcp:8000 -sTCP:LISTEN` に限定し、さらにプロセスのコマンド名が自分たちの
  python（spotipy の OAuth サーバの残骸）であることを確認してから kill
- そもそも redirect URI のポートを 8000 から衝突しにくい番号（例: 48752 など動的ポート帯）に
  変更する方が根本的（Spotify Dashboard 側の登録変更も必要）
- SIGTERM 送信後にポート解放を待たずに進むため、遅いプロセスだと結局
  `Address already in use` になる。解放をポーリングで待つ（タイムアウト付き）

### 3. launchd 3ジョブの 0:00 同時起動（競合の温床）

inbox / sync / archive が同時に走ると:

- inbox と sync が **同じ Western Musics** を同時に読み書き（inbox は追加、sync は削除しうる）
- sync 内の sort.sh が Western Musics を全置換（→ 1. のデータ損失）
- 3プロセスが**同じ `.cache-spotify`** を同時に読み書き。トークンリフレッシュが重なると
  キャッシュファイルの書き込み競合が起きうる
- OAuth 再認証が必要な場合、複数プロセスが同時にポート 8000 を取り合い、
  `free_redirect_port()` が**お互いの正当な OAuth サーバを殺し合う**

**修正案:** launchd は1エントリにして `nightly.sh`（inbox → sync → sort → archive を直列実行、
`flock` などで多重起動防止）に統合する。時刻をずらす（0:00 / 0:20 / 0:40）だけでも大幅に安全になる。

### 4. sync.sh の自動 commit が index 全体を巻き込む

[sync.sh:39-44](../sync.sh)（現在未コミットの変更）の `git commit -m "..."` は
**ステージ済みのすべての変更をコミットする**。深夜0時の実行時点でユーザーが別の変更を
`git add` していた場合、それが「auto: sync.txt / sort.txt を更新」コミットに混入して
**public リポジトリに push される**。

**修正案:**

```bash
git commit -m "auto: sync.txt / sort.txt を更新" -- sync.txt sort.txt
```

パス指定コミットなら index の他の内容を無視して該当ファイルだけコミットする。加えて:

- `git push` 失敗（リモートが先行している等）が通知されずログに埋まるだけ。失敗が続くと
  ローカルコミットが溜まり続ける。push 失敗時は notify するべき
- ブランチ確認がない。detached HEAD や別ブランチにいると意図しない場所にコミットされる
- **この sync.sh の変更自体がまだ未コミット**。動作中のコードとリポジトリの内容が
  ずれている状態なので、意図どおりなら早めにコミットを

## 中程度

### 5. OAuth 失効時のヘッドレス・ハング（全スクリプト共通）

トークン失効時、launchd 実行では `open_browser=True` のままブラウザ認証フローが始まり、
「Server listening on localhost has not been accessed」で失敗する（log/inbox.log 307行目ほか、
sync/sort/archive のログにも同型あり）。sync/sort/archive は通知が飛ぶが、
**inbox.sh だけは通知処理がなく silent fail**（README:204 の記述と不一致）。
修正案は [inbox-error-analysis.md](inbox-error-analysis.md) の修正4・修正5。

### 6. archive.py はページングなし・50曲固定

[archive.py:83-96](../archive.py) `get_source_track_ids()` は `limit=50` で1ページだけ取得し、
`fields` に `next` も含めていない。Top 50 前提なら動くが、ソースを差し替えた瞬間に
**51曲目以降を黙って取りこぼす**。せめてコメントで前提を明示するか、他の関数と同じ
ページングループにしておくべき。

### 7. 日本語文字の正規表現が中国語を「邦楽」と誤判定する

[inbox.py:44](../inbox.py) の `JP_CHAR_RE = re.compile(r"[぀-鿿]")`（U+3040–U+9FFF）は
ひらがな・カタカナに加えて **CJK 統合漢字全体**を含む。漢字のみの中国語曲名・アーティスト名
（C-POP、台湾・香港の楽曲）が japanese 判定になる。また半角カナ（U+FF66–FF9D）は範囲外。

**修正案:** 「ひらがな・カタカナ（U+3040–U+30FF、U+FF66–FF9D）を含めば日本語確定、
漢字のみの場合は判定を保留して次の手段へ」の2段階にする。

### 8. inbox.py の exit code が常に 0

[inbox.py:288](../inbox.py)。判定不能曲があっても、Gemini が全滅していても正常終了。
ラッパーや launchd からエラー状態が観測できない。

## 軽微

### 9. README が存在しない `.env.example` を案内している

README:55 に `cp .env.example .env` とあるが、リポジトリに `.env.example` は存在しない。
新規セットアップ時に必ず躓く。`.env.example` を追加する（public リポジトリなのでプレースホルダのみ）。

### 10. sync.py の細かい点

- [sync.py:129](../sync.py) 自動作成プレイリストが `public=True`。意図的か要確認
  （手動作成分と公開設定が揃っているか）
- [sync.py:135](../sync.py) `append_artist_to_config()` は既存ファイルが改行で
  終わっていない場合、最終行に連結されて設定が壊れる。追記前に末尾改行を保証すると安全
- 複数アーティスト曲（コラボ曲）を一方のアーティストプレイリストから消すと
  Western Musics からは消えるが、もう一方のアーティストプレイリストには残留する（仕様なら README に明記）
- Western Musics から直接曲を消しても各アーティストプレイリストには伝播しない
  （「双方向」の意味が AP→source の削除のみである点、README の記述はあるが誤解しやすい）

### 11. launchd 実行環境の確認結果

plist 全文（`~/dotfiles/LaunchAgents/com.yoshihide.run_{inbox,sync,archive}.plist`）と
`launchctl print` を確認した結果（2026-07-14 時点）:

- 3ジョブとも正常にロード済み・last exit code 0。PATH（homebrew 含む）、
  `RunAtLoad=false`、StandardOut/ErrorPath の設定自体に問題なし
- **ネットワーク未接続スキップ = その日の実行が丸ごと消える（中程度・要修正）**。
  `StartCalendarInterval` はスリープ中に 0:00 を跨ぐと復帰時に1回だけ発火するが、
  復帰直後は Wi-Fi 再接続前のことが多く、各 .sh 冒頭の `nc -zw3` チェックが失敗して
  **exit 0 → リトライなし → その晩の処理は全部スキップ**になる。ログに実績が複数ある
  （log/inbox.log の「ネットワーク未接続のためスキップ」が 00:01〜00:04 のタイムスタンプ =
  復帰直後の発火と推測される。2026-06-13 / 06-27 / 07-05 / 07-13 ほか）。
  修正案: スキップではなく「30秒間隔で最大10回リトライしてから諦める」に変える。
  Mac の電源が切れている場合はそもそも発火しない点も認識しておく（launchd の仕様）
- **ログの二重化が不揃い**: plist の StandardOutPath（`~/Library/Logs/*.out`）と
  スクリプト自身の `log/*.log` の2系統がある。inbox.sh は tee 方式なので両方に書かれるが、
  sync/archive/sort は変数キャプチャ方式なので .out 側はほぼ空。障害調査時に
  「どちらを見るべきか」が統一されていない。log/ に一本化して plist 側は
  最終防衛線（スクリプト自体が起動失敗した場合のみ）と割り切るのが良い

### 12. ドキュメント・残骸の不整合

- docstring の旧ファイル名: [sort.py:6](../sort.py) `sort_playlist.py`、
  [sync.txt:1](../sync.txt) `sync_artist_playlists.py`、[archive.txt:1](../archive.txt) `archive_top50.py`
- `__pycache__/archive_top50.cpython-311.pyc` — 改名前の残骸（gitignore 済みなので実害なし、消すだけ）
- リポジトリは **public**。`.env` と `.cache-spotify` は gitignore 済みで git 履歴にも
  含まれていないことを確認済み（安全）。ただし個人のプレイリスト ID と音楽の好みが
  公開されている点は認識しておく（実害はほぼない）
