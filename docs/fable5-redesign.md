# Fable 5 ならこうする — 再設計提案

作成: 2026-07-14（Claude Code / Fable 5 によるプロジェクトレビュー）

> **ステータス注記（同日追記）:** 本人決定により §2（nightly.sh + launchd 1本化）と
> §6 の「クラウド移行はやらない」は [implementation-plan.md](implementation-plan.md) で
> **GitHub Actions 移行に置換**された。§1（分類パイプライン）・§3（観測可能性）・
> §4（ヘッドレス分離）・§5（コード構造）の思想はそのまま実装プランに引き継がれている。

前提: このプロジェクトは「毎晩無人で走り、朝には正しい状態になっている」ことが価値。
だから再設計の軸は機能追加ではなく、**(a) 判定を API に頼らない構造にする、
(b) 競合クラスを設計で消す、(c) 失敗を必ず観測可能にする** の3点。

## 1. 分類パイプラインの再設計 — 「LLM を呼ばないのが最良の LLM 活用」

現在の分類は「genres → 日本語文字 → Gemini」だが、genres は実測でほぼ空
（[inbox-error-analysis.md](inbox-error-analysis.md) 参照）。実質「日本語文字 → Gemini」になっており、
洋楽1曲ごとに LLM を呼ぶ設計は、コスト・レート制限・非決定性の3点で筋が悪い。

**提案する判定順（上から順に、決定的・無料・高速な順）:**

```
1. 永続キャッシュ     — 一度判定したアーティストは即返す（artist_class_cache.json）
2. ISRC 国コード      — track.external_ids.isrc が "JP" 始まり → japanese
3. 日本語かな判定     — ひらがな・カタカナを含む → japanese（漢字のみは保留）
4. Spotify genres     — 空でなければ使う（今や補助扱い）
5. Gemini（バッチ）   — ここまでで残った曲だけを 1 リクエストで一括判定
```

ISRC は実測で Japanese Musics の 97%（194/200）が `JP`、Western Musics は 300 曲中 `JP` ゼロ。
liked tracks のレスポンスに最初から含まれているので**追加 API コストもゼロ**。
このパイプラインなら Gemini 呼び出しは月に数回レベルまで落ち、429 は構造的に消滅する。

ステップ5を呼ぶ場合も、曲ごとではなく**一括 + structured output**:

```python
response = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    contents=f"Classify each artist as japanese or western: {json.dumps(unknown_artists)}",
    config={"response_mime_type": "application/json",
            "response_schema": {"type": "object", "additionalProperties": {"enum": ["japanese", "western"]}}},
)
```

判定結果は必ず永続キャッシュに書き戻す。LLM は「キャッシュを埋めるための最後の手段」であって
実行パスの常連にしない。

## 2. 夜間ジョブの1本化 — 競合を「調停」ではなく「消滅」させる

現在: launchd 3エントリが 0:00 に同時起動し、同じプレイリスト・同じトークンキャッシュ・
同じ OAuth ポートを取り合う（[bugs-and-risks.md](bugs-and-risks.md) 1〜3）。

**提案: `nightly.sh` 1本 + launchd 1エントリ。**

```bash
#!/bin/bash
# nightly.sh — 毎晩 0:00。順序が依存関係を表す
exec 9>/tmp/spotify-playlist-tools.lock
flock -n 9 || exit 0                # 多重起動防止
run inbox    # お気に入り → Japanese/Western へ振り分け
run sync     # Western → アーティスト別 AP（inbox の結果を含めて同期）
run sort     # 全プレイリストをソート（全員の書き込みが終わってから全置換）
run archive  # Top 50 アーカイブ（独立だが直列で害なし）
```

これで「sort の全置換が inbox の追加を消す」「.cache-spotify の同時書き込み」
「OAuth ポートの取り合い」という競合クラス全体が設計から消える。
ロックやリトライで守るより、**並行性そのものをなくす**方が、この規模では正しい。
副次効果として、inbox が振り分けた曲がその晩のうちに sync・sort まで到達する
（現在は最大24時間ずれる）。

## 3. 障害の観測可能性 — 「静かな失敗」をゼロに

無人実行システムの鉄則は「失敗はうるさく、成功は静かに」。現在は逆の箇所がある
（inbox は通知なし、git push 失敗はログに埋まる、exit code は常に 0）。

- Python 側で例外を握って `auth / network / quota / other` に分類し、
  **1箇所の共通ハンドラ**から macOS 通知（シェル側の `grep -qi "auth"` ヒューリスティックを廃止）
- exit code を意味づけ: 0=成功 / 1=致命的エラー / 2=一部スキップ（unknown あり）
- 夜間バッチの最後に1行サマリ（`inbox:6曲 sync:+3/-1 sort:8件 archive:+2`）をログに残す。
  朝に `tail -1` するだけで昨晩の全体像がわかる

## 4. ヘッドレスと対話実行の明確な分離

トークン失効時、launchd 実行では深夜にブラウザ認証フローが走って必ず失敗する。
`isatty()` でヘッドレスを検出し、**ヘッドレスではブラウザフローを開始せず即通知 + exit 1**。
通知メッセージに「ターミナルで `python inbox.py` を実行すれば再認証できる」と復旧手順まで書く。
再認証は対話実行時のみ。これで「失効 → 数日間 silent fail」（ログに実績あり）がなくなる。

## 5. コード構造 — 4スクリプト共通基盤

[improvements.md](improvements.md) 1〜2 の集約を一歩進めて、こういう形に:

```
spotify_playlist_tools/
├── core.py        # クライアント生成・ページング・バッチ・設定パーサ・通知・ロギング
├── classify.py    # 分類パイプライン（ISRC/かな/genres/Gemini + 永続キャッシュ）
├── inbox.py  sync.py  sort.py  archive.py   # 固有ロジックのみ（各 <100行）
├── nightly.sh     # 唯一のエントリポイント（launchd 1エントリ）
└── tests/         # 純関数のユニットテスト
```

public リポジトリなので GitHub Actions で `ruff check` + `pytest`（外部 API はモック）を
push 時に回す。無料枠で足り、auto-commit（sync.txt 更新）が何かを壊したときに検知できる。

## 6. やらないと決めること（過剰設計の回避）

このプロジェクトの規模（個人用・4スクリプト・毎晩1回）を踏まえ、以下は**あえてやらない**:

- DB 導入（sync_state.json と JSON キャッシュで十分。壊れたら消して再生成できる設計を保つ）
- 非同期化・並列化（夜間バッチに速度要件はない。直列が一番デバッグしやすい）
- Docker 化・クラウド移行（launchd + pyenv で完結している。Mac が寝ていると走らない問題が
  実際に出たら初めて検討 — ログの「ネットワーク未接続のためスキップ」の頻度が判断材料）
- spotipy からの乗り換え（2.26.0 で必要十分。Spotify API の 429 リトライも内蔵している）

## 実施順序の提案

| 順 | 作業 | 効果 | 工数目安 |
|---|---|---|---|
| 1 | アーティスト判定キャッシュ + ISRC 判定 | inbox のエラー消滅 | 小 |
| 2 | inbox.sh のエラー通知 + ヘッドレス分離 | silent fail 撲滅 | 小 |
| 3 | nightly.sh 統合（launchd 1本化） | 競合・データ損失リスク消滅 | 小〜中 |
| 4 | sync.sh の `git commit -- パス指定` ほか個別バグ修正 | 事故防止 | 小 |
| 5 | 共通基盤への集約 + テスト + CI | 保守性 | 中 |

1と2だけで「エラーが起きる・気づけない」という現在の問題は解消する。
3まで入れると夜間バッチとして安心して放置できる状態になる。
