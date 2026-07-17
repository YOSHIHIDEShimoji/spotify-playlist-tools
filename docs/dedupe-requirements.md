# dedupe.py — 重複・別バージョン整理ツール 要件定義

作成: 2026-07-17（Claude Code / Fable 5）。[feature-ideas.md](feature-ideas.md) C-1 の具体化。
**本ドキュメントは要件定義であり、実装はまだ行っていない。**

> **ステータス注記（同日追記）:** 本人決定により UI 方針を変更。§2「決定 UI = 対話 CLI」と
> §4.3 の対話フロー、§2「nightly 見張り = 後で検討」は [dashboard-design.md](dashboard-design.md) で
> **上書き**された（UI はダッシュボードサイト、scan は毎晩実行）。検出エンジン（§3, §4.1-4.2, §4.4-4.5）・
> 安全要件（§5）・sync 整合（§6）・受け入れ基準の考え方は設計書に全面継承。

## 1. 一言で

プレイリスト内の「同じ曲」（Single/Album 版・feat 違い・Remaster 等）をグループ化して提示し、
**本人がどれを残すか選び**、最後にまとめて確認してから削除する対話型ツール。
**無確認の削除経路は設計上存在させない。**

## 2. 決定事項（2026-07-17 本人確認済み）

| 論点 | 決定 | 理由 |
|---|---|---|
| 決定 UI | **対話 CLI** | 大掃除は年数回。実装最小で足りる。Issue チェックボックス方式は状態管理が数倍重く見送り |
| 対象範囲 | **Japanese Musics + Western Musics + アーティスト別 AP 全部** | 横断グループ化と連動削除で sync との整合を保つ（§6 参照）。アーカイブは履歴なので除外 |
| nightly 見張り | **後で検討** | まず手動大掃除のみ。inbox 経由で重複が再発するようなら scan-only ステップを追加 |

## 3. 重複の3段階定義

| Tier | 定義 | 例 | 扱い |
|---|---|---|---|
| **A: 完全重複** | 同一トラック ID が同じプレイリストに2回以上 | 手動追加の事故 | 機械的に消せるが、それでも確認は挟む |
| **B: 同一録音** | ISRC が一致する別トラック ID | Single 盤と Album 盤の同じ Photograph | 「ほぼ確実」と表示して提示 |
| **C: 別バージョン** | ISRC 不一致だが正規化タイトル＋主アーティスト ID が一致 | feat 違い / Remaster / Live / Acoustic / Taylor's Version | 「候補」として提示。同名別曲の誤検出がありうるため**機械は絶対に決めない** |

## 4. 機能要件

### 4.1 スキャン

- 対象: `inbox.txt` の JAPANESE_MUSICS_ID / WESTERN_MUSICS_ID、`sync.txt` のアーティスト別プレイリスト全件
  （`archive.txt` の DEST は対象外）
- 取得フィールドは `id,name,artists(id,name),external_ids,duration_ms,popularity,album(name,album_type,release_date)`
  — すべてプレイリスト取得レスポンスに含まれ、**追加 API コストゼロ**（classify.py と同じ思想）
- `core.iter_playlist_tracks` を再利用（fields に `id` 必須）

### 4.2 グループ化

- **正規化ルール**: 小文字化・NFKC → 以下の接尾辞/括弧を除去した「ベースタイトル」で比較
  `(feat. …)` `(with …)` `- Remastered…` `- Live…` `(Acoustic)` `(Radio Edit)` `(Sped Up)` `(Taylor's Version)` 等
- アーティストは表示名でなく **ID** で比較（表記ゆれ対策）
- **全対象プレイリストを横断して1グループ**にまとめる（同じ曲が Western と AP の両方にあれば
  出現箇所として列挙し、判断は1回・削除は全箇所同時 → §6）
- 補助シグナル: 再生時間差 ±3 秒以内なら「同一録音の可能性大」と表示

### 4.3 提示・決定（対話 CLI）

```
$ python dedupe.py

対象 10 プレイリストをスキャン中...
重複グループ: 12件（完全重複 0 / 同一録音 4 / 別バージョン候補 8）

[5/12] 候補（別バージョン）  Love Story / Taylor Swift
  a) Love Story                    アルバム Fearless        2008  3:55  人気70
  b) Love Story (Taylor's Version) アルバム Fearless (TV)   2021  3:55  人気76  ★推奨
  → 出現: Western Musics, Taylor Swift AP
  残す番号（カンマ区切り可） [a/b/k=全部残す/q=中断] > b

確認: 8曲を削除します（Western Musics 6 / Taylor Swift AP 2）
実行しますか？ [yes/no] > yes
完了。ログ: log/dedupe_2026-07-17.txt（undo 用トラックID一覧つき）
```

- 表示項目: アルバム名・種別（album/single/compilation）・リリース日・長さ・人気度
- ★推奨はアルバム版優先→人気度の参考表示のみ。**デフォルト選択はしない**（Enter 空打ちで先に進まない）
- 3バージョン以上のグループはカンマ区切りで複数残せる（例: `a,c`）
- `k`（全部残す）の決定は `dedupe_keep.json` に永続化し、次回スキャンから同一グループを表示しない
  （トラック ID 集合をキーにする。ファイルはコミット対象。壊れたら消して再回答すればよい）
- `q` はそこまでの決定を破棄して終了（部分適用しない）

### 4.4 適用

- **全グループの判断が終わるまで変更系 API を一切呼ばない**。最終確認 yes の後に一括実行
- Tier B/C（別 ID）: `playlist_remove_all_occurrences_of_items`（100件バッチ、core の remove_in_batches 再利用）
- Tier A（同一 ID の複数出現）: 位置指定削除が必要。`playlist_remove_specific_occurrences_of_items`
  ＋ `snapshot_id` を使い、取得時と snapshot が変わっていたらそのプレイリストは見送る（sort.py と同じガード）

### 4.5 ログ・undo

- 削除した `(プレイリスト名, プレイリストID, トラックID, 曲名/アーティスト)` を `log/dedupe_YYYY-MM-DD.txt` に必ず記録
- undo は「トラック ID を再追加」するだけで完全復元できる — **位置は sort.py が毎晩並べ直すので保存不要**

## 5. 非機能・安全要件

1. 無確認削除ゼロ（Tier A 含む）。自動判断・自動削除の経路をコード上作らない
2. ヘッドレス環境（isatty 偽）では対話モードの起動を拒否。`--report`（scan-only・変更なし・グループ一覧を出力）のみ許可
3. exit code は既存規約に従う: 0=成功 / 1=致命的 / 3=要再認証
4. スコープは既存の inbox.py と同じ（playlist-modify 系）。`.cache-spotify` を共用し新規認証を発生させない

## 6. sync / sort との相互作用（重要な落とし穴）

- **Western からだけ消すと AP に残留する**: sync.py の順方向は追加のみ。AP 側の Single 版はゴミとして永久に残る
- **AP からだけ消すと Western 側も消される**: 双方向同期が「AP からの削除」と解釈し、ソースからも削除する
- → よって**同一曲の全出現箇所を1グループにまとめ、削除も全箇所同時に実行**する（§4.2/4.4）
- 削除後の翌 nightly: sync は「state にあるが AP にない」を検出してソース削除を試みるが既に無いので no-op、
  state が書き直されて収束する。**この no-op 収束を受け入れ基準に含める**（§8）

## 7. やらないこと

- 自動判断・自動削除
- Japanese ⇄ Western 間の重複検出(それは誤振り分けの問題で別件。分類検証は実施済み)
- アーカイブプレイリストの整理(履歴なので触らない)
- nightly への組み込み(§2 のとおり後で検討。入れる場合も scan-only + Issue 報告に限定)

## 8. 受け入れ基準

1. `--report` で Western Musics の Single/Album ペア（ISRC 一致）が Tier B の1グループとして出力される
2. Western と AP の両方にある曲が1グループに統合され、出現箇所が列挙される
3. 対話で `k` を選んだグループが `dedupe_keep.json` に記録され、再実行時に表示されない
4. 最終確認で `no` と答えた場合、変更系 API が1回も呼ばれていない
5. 削除実行後の翌 nightly が失敗・意図しない削除なしで完走する（§6 の no-op 収束）
6. `log/dedupe_*.txt` から削除曲を再追加すると元の状態に戻る（sort 後の並びは変わってよい）

## 9. 将来の拡張（本要件のスコープ外）

- nightly scan-only 見張り（unknown-tracks と同じ Issue 運用パターン）
- C-2（灰色曲検知）との統合 — スキャン基盤を共有できる
