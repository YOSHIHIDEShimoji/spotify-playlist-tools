# inbox.sh エラー分析 — 原因特定と修正案

作成: 2026-07-14（Claude Code によるプロジェクトレビュー・実測ベース）

## 結論

`./inbox.sh` のエラーの正体は **Gemini API 無料枠のレート制限（10 リクエスト/分）超過による 429** 。
スクリプト自体はクラッシュしておらず exit code は 0。429 になった曲が `unknown` 扱いでスキップされ、
`[gemini error] 429 RESOURCE_EXHAUSTED` がターミナルに出力され、「不明曲あり」の macOS 通知が飛ぶ。

さらに根本には **「Spotify API のアーティスト genres がほぼ全アーティストで空になった」** という
外部環境の変化がある。設計時の第1判定（ジャンル判定）が実質機能しておらず、
日本語文字を含まない曲がほぼ全部 Gemini フォールバックに落ちる構造になっている。

## 実測エビデンス（2026-07-14）

### 1. エラーの再現ログ

2026-07-14 17:12 のユーザー実行（`log/inbox.log` 末尾、17曲処理の回）:

- 前半の曲は正常に `[western]` 判定
- 途中から `[gemini error] 429 RESOURCE_EXHAUSTED ... limit: 10, model: gemini-2.5-flash-lite` が連発
- エラー内容: `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`、`quotaValue: '10'`
- 結果: 5曲（Henry Moodie ×4、KT Tunstall ×1）が unknown スキップ

17:13 に再実行（このレビューで実施）→ 残り6曲は**全曲成功**（exit 0）。
レート窓（1分）がリセットされた後なら通る。つまり「一度に Gemini 判定が必要な曲が
約10曲を超えると必ず失敗する」再現性のある構造的問題。

### 2. Spotify genres の空化（根本原因）

Spotify API で実測した結果:

| アーティスト | genres |
|---|---|
| Henry Moodie | `[]` |
| KT Tunstall | `[]` |
| Bruno Mars | `[]` |
| Taylor Swift | `[]` |
| OneRepublic | `['soft pop']` |

README（[README.md:162](../README.md)）が謳う判定順「① Spotify ジャンル → ② 日本語文字 → ③ Gemini」のうち、
①が大物アーティストですら空を返す。結果、洋楽はほぼ全曲③に到達する。

### 3. コード上の増幅要因

[inbox.py:168-190](../inbox.py) の `classify()`:

- **アーティスト単位のキャッシュがない**。今日のログでは Henry Moodie の曲が12曲あり、
  同一アーティストに対して12回 Gemini を呼んでいる（本来1回で足りる）
- `sp.artist(artist["id"])` も毎トラック呼んでいる（同上、Spotify API の無駄打ち）
- [inbox.py:143-165](../inbox.py) `classify_with_gemini()` は 429 の `retryDelay`（レスポンスに
  「2.6秒後にリトライせよ」と明記されている）を無視して即 `unknown` を返す
- `genai.Client` を呼び出しごとに再生成している

### 4. 過去のエラー（ログに残っている別種の障害）

`log/inbox.log` には過去2種類の致命的エラーもある:

1. **`OSError: [Errno 48] Address already in use`**（193行目、234行目）
   → コミット 966d481 の `free_redirect_port()` で対処済み
2. **`SpotifyOauthError: Server listening on localhost has not been accessed`**（307, 338, 407, 644行目）
   → OAuth トークン失効時、深夜0時の launchd 実行（ヘッドレス）でブラウザ認証フローが開始され、
   誰もアクセスしないままタイムアウト。**inbox.sh には通知処理がないため、これが起きても
   ユーザーは気づけない**（後述）。実際、失効中は複数日連続で silent fail していた形跡がある。

## 修正案（優先順）

### 修正1: アーティスト単位の判定キャッシュ（最重要・即効）

同一実行内のメモリキャッシュ + 実行をまたぐ永続キャッシュ（JSON）。
一度 japanese/western が確定したアーティストは二度と API を叩かない。

```python
CLASS_CACHE_PATH = BASE_DIR / "artist_class_cache.json"  # {artist_id: "japanese"|"western"}

def classify(sp, track, cache: dict) -> str:
    artist_id = track["artists"][0]["id"]
    if artist_id in cache:
        return cache[artist_id]
    label = _classify_uncached(sp, track)
    if label != "unknown":
        cache[artist_id] = label   # unknown はキャッシュしない（再判定の余地を残す）
    return label
```

今日のケースなら Gemini 呼び出しは 16回 → 2回（Henry Moodie と KT Tunstall 各1回）になり、
無料枠に絶対に収まる。**この1つでエラーはほぼ消える。**

### 修正2: ISRC 国コード判定を第2判定に追加（Gemini をほぼ不要にする）

liked tracks のレスポンスには `track.external_ids.isrc` が含まれる。ISRC の先頭2文字は登録国。
**実測（このレビューで両プレイリストを検証）:**

- Japanese Musics（200曲）: `JP` 194曲 / `US` 5曲 / `TC` 1曲
- Western Musics（300曲）: `JP` **0曲**（US 219 / GB 59 / QM 14 ほか）

つまり「ISRC が JP 始まり → 邦楽」は precision ほぼ100%・recall 97%。判定順を

1. ISRC 先頭2文字が `JP` → japanese
2. 日本語文字チェック（曲名・アーティスト名・アルバム名）→ japanese
3. Spotify genres（空でなければ使う）
4. Gemini（残りの少数だけ）

とすれば、Gemini は「JP 以外の ISRC を持つ日本人アーティストの英語曲」級のレアケースにしか
呼ばれなくなる。API コストと 429 リスクが構造的に消える。

```python
def isrc_country(track: dict) -> str:
    return (track.get("external_ids", {}).get("isrc") or "")[:2].upper()
```

### 修正3: Gemini 429 リトライ + バッチ化

それでも Gemini を呼ぶ場合の堅牢化:

- 429 時はレスポンスの `retryDelay` を尊重して1回リトライ（`google.genai.errors.APIError` の
  `code == 429` で分岐）
- あるいは全 unknown 曲を貯めて**1リクエストで一括判定**（JSON で `{"曲名/アーティスト": "japanese|western"}` を
  返させる）。structured output（`response_schema`）を使えばパースも安全
- `genai.Client` はモジュールレベルで1回だけ生成

### 修正4: inbox.sh に他ラッパーと同じエラー処理を追加

[inbox.sh](../inbox.sh) は4つのラッパーで唯一、エラー検出・macOS 通知がない
（sync.sh / sort.sh / archive.sh には認証エラー通知がある）。
README:204 の「OAuth トークンが失効した場合は macOS 通知で警告される」は inbox に関しては**現状false**。

```bash
output=$("$PYTHON" -u inbox.py 2>&1)
exit_code=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] inbox exit=$exit_code" >> "$LOG"
echo "$output" >> "$LOG"
if [[ $exit_code -ne 0 ]]; then
    if echo "$output" | grep -qi "oauth\|token\|unauthorized\|401"; then
        notify "Spotify Inbox: 認証エラー" "再ログインが必要です"
    else
        notify "Spotify Inbox: エラー" "inbox.py が失敗しました"
    fi
fi
```

（現在の `| tee -a` 方式は pipefail 未設定のため exit code が tee のものになる点にも注意）

### 修正5: ヘッドレス実行時は OAuth ブラウザフローを開始しない

launchd 実行時にトークンが失効していると、ブラウザが深夜に開いて（あるいは開けずに）
タイムアウトまでハングする。環境変数か isatty で判定し、キャッシュが無効なら即通知して exit 1 が正しい:

```python
headless = not sys.stdin.isatty()
auth = SpotifyOAuth(scope=SCOPE, cache_path=str(CACHE_PATH), open_browser=not headless)
if headless and not auth.validate_token(auth.cache_handler.get_cached_token()):
    notify("Spotify Inbox: 認証エラー", "トークン失効。手動で inbox.py を実行して再認証してください")
    return 1
```

### 修正6: 判定不能時の exit code

[inbox.py:288](../inbox.py) は常に `return 0`。Gemini が全滅しても正常終了扱いになる。
unknown が発生した場合は非ゼロ（例: 2）を返し、ラッパー側で区別できるようにする。
