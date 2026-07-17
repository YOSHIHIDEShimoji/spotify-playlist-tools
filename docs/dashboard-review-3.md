# ダッシュボード コードレビュー 3巡目（2巡目修正の検証）

レビュー実施: 2026-07-17（Fable 5・3巡目）。対象: コミット `e446635`（Opus による2巡目対応）を中心に、
`siteops.py` / `sitegen.py` / `site/src/lib/{data,processing}.ts` / `site/src/pages/Organize.tsx` /
`site-ops.yml` / テスト。`pytest`（51 passed）・`ruff`（clean）・`tsc --noEmit`（clean）をローカルで実測。
さらにフェイク sp ハーネス（7シナリオ）で **`op_dedupe_apply`／`op_undo`／`op_keep_apply`／
`write_undo_index` を実際に実行して挙動を確認**した。コードは一切変更していない（本ファイルの新規作成のみ）。
変更系 Spotify API は呼んでいない。

---

## 総評

**収束した。** 2巡目の修正（H-1・M-1〜M-4・L-2〜L-6）は全て意図どおり動くことを実測で確認し、
修正が新たな Critical/High を生んだ形跡は無い。懸念した siteops→sitegen の新依存も循環 import なし
（import は siteops→sitegen の一方向のみ、sitegen のモジュールレベルに副作用なし）。
「削除 → 直後にサイトから取り消し」の H-1 経路はフェイク sp で end-to-end に成立し、
undo の復元先も実在籍ベースに揃った。keep の即時反映・stale payload 拒否・30日フィルタ境界・
`.done` 除外・二重 undo 拒否も全て実測 OK。

残るのは **Medium 1件（取り消しボタンだけが M-2 の直列化の枠外）と Low 3件**で、いずれも
プレイリスト破壊・データ不整合には至らない運用品質の話。1巡目 Critical 3件・2巡目 High 1件と
比べて指摘の深刻度は明確に下がり続けており、3巡で収束と判断する。

### 深刻度サマリ

| 深刻度 | 件数 | 概要 |
|---|---|---|
| **Critical** | 0 | — |
| **High** | 0 | — |
| **Medium** | 1 | undo（取り消し）ボタンが M-2 直列化の対象外で、連打時に中間 run が無音キャンセルされ得る |
| **Low** | 3 | live 在籍ゼロ時の undo fallback が snapshot / 例外時に undo_index 未更新 / op 実行系の回帰テスト欠如 |

---

## 2巡目修正の検証（H-1・M-1〜M-4・L-2〜L-6）

| 指摘 | 判定 | 根拠 |
|---|---|---|
| **H-1** op 直後の undo_index 再生成 | **OK（実測）** | フェイク sp で dedupe-apply 直後に `undo_index.json` が生成され（entries 1件）、undo 直後は `.done` 化と同時に index から即消滅。siteops→sitegen import は一方向で循環なし・sitegen の import 時副作用なし（`import siteops; import sitegen` 実行確認）。呼び出しは削除・undo 記録の**後**なので index が先走ることもない。残: 例外時のみ末尾に届かず index 未更新（→ L-B） |
| **M-1** processing 30分タイムアウト | **OK（コード追跡）** | `stuckIds` はマウント時の reconciliation effect（Organize.tsx:28）と 60秒 interval（:34-40）の両方から呼ばれ、タブを閉じて戻っても mount 時に即解除される。`clearProcessing` は `changed` フラグで無変更時に write しないため effect の再実行ループは発生しない。「永久に全操作不能」には**ならない**（最悪30分） |
| **M-2** dispatch の直列化 | **不十分（穴が1つ残存）** | dupes カード・classify ボタンは `anyProcessing`＋`blocked` で正しく直列化。しかし **UndoSection の取り消しボタンだけが枠外**（markProcessing も blocked も無い）→ M-A。nightly pending との衝突は2巡目どおり残存許容 |
| **M-3** undo に実在籍を記録 | **OK（実測）** | snapshot が `b:[pW]` でも live が `pW+pAP` なら undo record は `['pW','pAP']` になり、op_undo は両方へ再追加（フェイク sp で確認）。削除は従来どおり全管理 PL へ、keep 側は不触。追加コストは管理 PL 全読取1周（`playlist_track_ids` の軽量 fields 再利用・個人規模で許容）。エッジ: live 在籍ゼロの曲は snapshot へ fallback → undo が「削除が触っていない」曲を復活させ得る（実測・→ L-A） |
| **M-4** keep-apply の照合＋即時反映 | **OK（実測）** | group_id 実在＋track_ids==構成を検証し stale 再送は OpError で無変更（実測）。Tier A の gid も「tracks 無し」経由で拒否（実測）。dupes.json から該当グループ即除去・counts 再計算も A/B/C 混在で正しい（実測）。`_sp` は本当に未使用＝Spotify 不触。`load_keep_sets` に翌スキャン除外も接続済み。keep したグループの undo は無い（backend の remove op はあるが UI なし）— 「間違えたら翌 nightly ではなく dedupe_keep.json を直せば戻る」ため許容 |
| **L-2** .done 済み undo の再クリック | **OK（実測）** | undo 直後に index 再生成 → entry 即消滅。二重 undo は OpError（実測） |
| **L-3** undo 一覧 30日限定 | **OK（実測）** | 31日前は落ち・29日前は載り・`.done` は glob 対象外（`with_suffix(".done")` で拡張子ごと変わるため `*.json` に一致しない）。ISO 文字列比較は同一オフセット（JST）同士なので辞書順で正しい |
| **L-4** getServerSnapshot | **OK** | モジュール定数 `EMPTY`（processing.ts:7）。安定参照 |
| **L-5** 可視化時の再フェッチ | **OK** | `useFetching` に統合。リスナーはクリーンアップで解放・`alive` フラグで unmount 後 setState なし・再フェッチ失敗時は既存 data を保持して error のみ更新（全ページが `data ?? []` で握る設計と整合）。`fetcher` はモジュール関数で参照安定＝effect 再実行なし。同一タブに居続けると refetch されない点は visibilitychange 方式の仕様として許容 |
| **L-6** classify 連打 | **OK** | 成功後 `markProcessing(trackId)`（Organize.tsx:255）。track id は Spotify base62 で `g-` と衝突せず、unknown.json から消えたら reconciliation で解消。処理中は「振り分け中…」表示＋disabled |

**動的 name の `useJson`（`wrapped/${month}`）**: month は `wrapped/index.json`（sitegen 生成の
ファイル stem）由来でユーザー入力ではなく、パス操作の余地なし。name 変更時は effect が再実行され
`alive` で古い fetch を破棄。WrappedMonth は month 未確定時に描画されない（WrappedBlock がガード）。安全。

---

## 新規・残存の指摘（深刻度順）

### M-A. 取り消し（undo）ボタンが直列化の枠外 — 連打で中間 undo が無音キャンセルされ得る
- **対象**: `site/src/pages/Organize.tsx:211-243`（`UndoSection`）
- **症状**: M-2 対応の `anyProcessing` ガードと `markProcessing` は dupes カードと classify にしか
  掛かっていない。取り消しボタンは `disabled={!pat || !!status[e.id]}` のみで、(a) 別 op の処理中でも
  押せる、(b) 押しても processing に登録されない。よって「削除実行中（run 1）に取り消しを2連打
  （run 2 pending → run 3 が run 2 をキャンセル）」や「一覧から3件連続で取り消し」で、GitHub
  concurrency（`spotify-serial`・pending 保持は1件）により**中間の undo run が無音キャンセル**される。
  曲は復元されないのに UI は「取り消し中… 数分後に反映」のまま。キャンセルは failure() を発火しないので
  Issue も立たない — 2巡目 M-2 が塞いだのと同型の穴が undo 経路にだけ残った。
- **緩和要因**: 実行されなかった undo は `.done` 化されないため entry が undo_index に**残り続け**、
  リロード後に再度取り消しボタンが出る（完全に隠れはしない）。1件ずつ操作する通常運用では踏まない。
- **推奨修正**: `run(id)` 成功時に `markProcessing("undo-" + id)` し、ボタンを
  `disabled={!pat || !!status[e.id] || anyProcessing}` に。解消は「undo_index から entry 消滅」
  （reconciliation に undo_index.data を追加）＋既存30分タイムアウト。10行程度。

### Low

- **L-A. live 在籍ゼロの曲の undo fallback が snapshot — 「削除が触っていない」曲を復活させ得る**:
  `siteops.py:131` の `live.get(r["track_id"], r["playlists"])`。スキャン後〜op の間に手動/sync で
  当該曲が全管理 PL から消えていた場合、削除は no-op なのに undo record は snapshot の PL を持ち、
  undo 実行でその曲が復活する（フェイク sp で実測: 削除前 `pW=['a']` → undo 後 `pW=['a','b']`）。
  修正前からの挙動の温存であり新バグではないが、M-3 の趣旨（undo = 削除の正確な逆操作）に照らすと
  fallback は `[]` が正しい。1トークンの修正。曲が1曲増えるだけで破壊性は無いため Low。
- **L-B. 例外（部分削除）時に undo_index が未更新**: `siteops.py:141-146`。remove_in_batches が途中で
  落ちると `_refresh_undo_index` に届かない。undo レコード自体は削除前に書かれ、site-ops.yml の
  data push が `if: always()` なので**データブランチには commit される**（実測: 例外後も undo/*.json
  存在・失敗 Issue も立つ）。ただし当日サイトに取り消しボタンが出ず、翌 nightly まで手動 dispatch 頼み。
  推奨: `_write_undo` 直後にも `_refresh_undo_index(data)` を1行（削除前に index に載っても、undo run は
  同一 concurrency で削除 run の後に直列実行されるため危険はない）。
- **L-C. op 実行系（dedupe-apply / undo）の回帰テストが無い**: `tests/test_siteops.py` は plan 系純関数と
  keep-apply のみで、M-3 の live 在籍記録・H-1 の index 即時更新・undo の復元先はスイートに乗っていない
  （2巡目・3巡目ともレビュー側のアドホックなフェイク sp で実測して担保している状態）。本レビューの
  ハーネス相当（FakeSp＋monkeypatch）を `test_siteops.py` に移植すれば、次に op 実行部を触るときの
  安全網になる。〔付随の cosmetic: keep-apply 後の dupes.json は `generated_at` が旧スキャン時刻のまま
  （siteops.py:216-221）。UI は未使用のため実害なし〕

---

## 見送り項目の再評価（1・2巡目からの据え置き）

- **M5（classify が邦楽 AP へ入れない）— 据え置きのまま許容**。状況変化なし。恒久欠損ではあるが実害は
  「artist AP に1曲欠ける」で、次フェーズ最初の10行修正という2巡目の評価を維持。
- **L-1（CJK 直結キーワードの検出漏れ）— 据え置き妥当**。誤マージではなく検出漏れ方向＝安全側。稀。
- **M4（Tier A の削除経路なし）／L2（main push の rebase なし）／L4（releases_seen 単調増加）／
  L5（データ全滅時バナー）— 据え置き妥当**。いずれも頻度・実害が小さく運用で気付ける。
- **M-2 の nightly 衝突面（nightly pending 中のサイト操作で nightly がキャンセルされ得る）— 2巡目どおり
  残存許容**。nightly は 01:00 JST 固定でサイト操作と時間帯が重なりにくい。

---

## 再実装の優先順位

1. **M-A**: UndoSection を直列化の枠内に入れる（markProcessing＋anyProcessing ガード・約10行）。
2. **L-A + L-B**: fallback を `[]` に・`_write_undo` 直後の index 更新（合わせて2行）。
3. **L-C**: op 実行系のフェイク sp 回帰テストをスイートへ移植。
4. §14-1 の実削除立ち会い（1巡目から未消化）: 初回の実 dedupe-apply → 即 undo → 翌 nightly no-op 収束、
   および listen-log 50件超時の実挙動（H3 の実効性）をこのタイミングで実測する。

---

## 検証方法の明示

- **実測**: pytest 51 / ruff / tsc（全 clean）。フェイク sp ハーネス7シナリオ —
  (A) live 在籍が snapshot より広いときの dedupe-apply（undo record・削除範囲・index 即生成・keep 側不触）、
  (B) live 在籍ゼロ曲の fallback と undo での復活、(C) undo の復元先・`.done` 化・index 即消滅・二重 undo 拒否、
  (D) 30日フィルタ境界（29日=載る/31日=落ちる）と `.done` 除外、(E) 削除途中クラッシュ時の undo record
  残存と index 未更新、(F) keep-apply の即時反映・stale 再送拒否・load_keep_sets 接続、
  (G) Tier A gid の keep 拒否と counts 再計算。加えて `import siteops; import sitegen` の循環なしを確認。
- **読解のみ（未実測）**: M-1 のブラウザ実機での30分タイムアウト（ロジックは追跡済み・時計を進めた実測は
  していない）、M-A の GitHub concurrency pending キャンセル（2巡目同様、公式仕様に基づく。実 run での
  再現はしていない）、Vercel deploy hook 経由の反映レイテンシ。

---

## 総合判断: 実運用に出せるか

**条件付き Go。**

- 削除の安全（1巡目 Critical 群）・取り消しの即時性（2巡目 H-1）・操作系の詰み（M-1/M-2）は
  3巡の修正で実測レベルで塞がっており、**「残したい曲が消える」「消した曲が戻せない」経路は
  確認できる範囲で存在しない**。コードベースとしては収束。
- 条件は2つ:
  1. **§14-1 の初回実削除立ち会い**（1巡目から残る唯一の未消化受け入れ条件）。初回の実 dedupe-apply を
     監視付きで行い、undo と翌 nightly の no-op 収束を実測してから常用に移す。
  2. **undo は当面1件ずつ操作する**（M-A の修正が入るまで、取り消しボタンの連打をしない）。
     修正自体は10行程度なので、次のコミットで入れて条件を外すのが早い。

---

## Opus 対応記録（2026-07-17・3巡目）

3巡目の指摘（Medium 1・Low 3）を全て反映。pytest 52 passed / ruff clean / site typecheck clean。

| 指摘 | 対応 |
|---|---|
| **M-A** undo ボタンが直列化の枠外 | 取り消し成功で `markProcessing("undo-"+id)`、`anyProcessing` 中は無効化、undo_index から消えたら reconciliation で解消（Organize が undo_index を取得して連携） |
| **L-A** live 在籍ゼロ時の undo fallback | fallback を snapshot でなく `[]` に（削除が触っていない曲を復活させない） |
| **L-B** 例外時に undo_index 未更新 | `_write_undo` 直後に `_refresh_undo_index` を移動（削除でクラッシュしても取り消せる） |
| **L-C** op 実行系の回帰テスト欠如 | FakeSp で `op_dedupe_apply`＋`op_undo` の end-to-end 回帰テストを追加（H1 全PL削除・M-3 live在籍・H-1 index即時・undo復元・.done・二重undo拒否を一括検証） |

**総合判断（Fable5）: 条件付き Go。** 残条件は §14-1 の初回実削除立ち会いのみ（M-A は本コミットで解消）。3巡で収束（Critical: 3→0→0、High: 6→1→0）。
