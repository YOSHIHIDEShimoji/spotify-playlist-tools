# ダッシュボード コードレビュー 2巡目（1巡目修正の検証）

レビュー実施: 2026-07-17（Fable 5・2巡目）。対象: コミット `61d7e6c`（Opus による1巡目対応）を中心に、
`dedupe.py` / `siteops.py` / `listen_log.py` / `sitegen.py` / `site/`（processing.ts・Organize.tsx・
Memories.tsx・Discover.tsx）/ ワークフロー3本 / テスト。`pytest`（50 passed）・`ruff`（clean）・
`tsc --noEmit`（clean）をローカルで実測。`normalize_title`・`build_groups`・`plan_dedupe`・
`op_dedupe_apply`／`op_undo`（フェイク sp）は**実際に実行して挙動を確認**した。
コードは一切変更していない（本ファイルの新規作成のみ）。変更系 Spotify API は呼んでいない。

---

## 総評

**1巡目の修正は総じて正しい。** Critical 3件（過剰マージ・tier B 誤ラベル・矛盾決定の全曲削除）は
実測で塞がっていることを確認し、修正による「残したい曲が消える」系の新たな経路は見つからなかった。
keep 側トラックが削除される経路・グループ間の keep/remove 衝突（union-find の成分は互いに素なので
構造的に起きない）も検証済み。回帰テストの追加も適切。

残る問題は「削除の安全」ではなく**「取り消しと反映の運用」**に集中している。最大の穴は、
undo_index.json を nightly の sitegen しか再生成しないため、**削除直後の undo がサイトに現れるのが
翌 01:00 JST 以降**になること（H5 の修正が半分しか効いていない）。誤削除に即気付いても当日は
サイトから取り消せず、「削除は undo とセットだから安全」の看板が時間的に破れる。次点は楽観的 UI の
「処理中」が op 失敗時に**永久に残ってそのグループを操作不能にする**こと、および同一 concurrency
グループの pending キャンセル仕様により**連続操作の中間 op が静かに消える**こと。いずれも
データ破壊ではないが、操作系の信頼性を削る。

### 深刻度サマリ

| 深刻度 | 件数 | 概要 |
|---|---|---|
| **Critical** | 0 | — |
| **High** | 1 | 削除直後の undo がサイトから翌 nightly まで実行できない（undo_index 未更新） |
| **Medium** | 4 | processing の永久残留でグループ操作不能 / 連続 dispatch の中間 op が静かに消える / undo の復元先が snapshot のみ（削除範囲と非対称） / keep-apply が payload 未照合＋翌 nightly まで反映されない |
| **Low** | 6 | CJK 直結キーワードの検出漏れ / undo 再クリックで失敗 Issue / undo 一覧に期限なし / getServerSnapshot が SSR 非対応 / useJson が再フェッチしない / classify 連打で失敗 Issue |

---

## 1巡目修正の検証（C1〜M8・L1・L3）

| 指摘 | 判定 | 根拠 |
|---|---|---|
| **C1** 正規化の単語境界化 | **OK（実測）** | 消すべき側 20 ケース（`(feat.)`/`- Remastered 2011`/`(Taylor's Version)` 曲アポストロフィ含む/`(Acoustic)`/`- Live at Wembley`/`(Sped Up)`/`(Re-recorded)` 等）が全て正しくベースタイトル化。消してはいけない側（`- Deliver Us`/`- Left Behind`/`(Alive)`/`(Demons)`/`(The Acoustics)`/`- Withered Flowers`）は全て保存。日本語＋空白区切り（`夜に駆ける (Acoustic)`→`夜に駆ける`）も正常。唯一の退行は CJK 文字に**直結**したキーワード（`曲名（劇場版live）`）が `\b` 不成立で除去されない件だが、これは誤マージではなく検出漏れ方向＝安全側（→ L-1） |
| **C2** 全員同一 ISRC のみ B | **OK（実測）** | 混在 ISRC・推移併合の成分が C になることをテスト＋実行で確認。懸念だった「空 ISRC を含む真の重複が C に格下げ」は実測で `('C','title',['a','b'])` — **非表示にはならず C として表示される**ので見逃しは起きない。B の「ほぼ確実」を偽らないという要件に対し保守的で正しい。なお ISRC リンクで形成されたのに混在で C になった場合 `reason` が "title" になるのは表示上の些細な不正確さのみ |
| **C3** group_id 重複拒否 | **OK（実測）** | 矛盾2決定は OpError。正規 UI の単一 decision は通過。group_id 欠落が2件並ぶケースも（副次的に）拒否される。keep∪remove==members・keep/remove 非空の既存検証と合わせ、単一 payload 内での全曲削除は塞がった |
| **H1** 全管理 PL から remove-all | **OK・ただし undo 非対称が残る** | フェイク sp で実測: remove 対象のみが全管理 PL（3件）から除去され、**keep 側は一切触られない**。グループは union-find の連結成分＝互いに素なので「remove 対象が別グループの keep と衝突」は構造的に不可能。`managed_playlists()` は scan と同一関数で宇宙が一致。一方、**undo は snapshot の playlists（実測: pW のみ）にしか再追加しない**ため、削除（全 PL）との非対称が残る（→ M-3） |
| **H2** keep のスキャン除外 | **OK（実測）** | `load_keep_sets`→`dupes_from_records` の track_id 集合突合を確認。keep{a,b} 済みグループに3件目 c が現れると集合不一致で**グループごと再表示**（実測 [3]）— 構成が変わったら再判断を求める挙動で、隠れっぱなしにならず安全側。sitegen（401行）と siteops `_regenerate_dupes`（206行）の両方が keep を渡している。残る穴は op_keep_apply 側（→ M-4） |
| **H3** listen_log ページング | **ロジックは OK（テスト実測）・実効性は未確認** | 停止条件（cursor 到達・短ページ・初回1ページ・max_pages=20）・seen_ms 重複排除・max_ms 更新は正しく、無限ループ経路なし（before が進まなくても 20 回で打ち切り）。ただし `_PagedSp` は「before で古いページが返る」理想 API を模している。**Spotify の recently-played は履歴保持が直近50件との公知の制限があり、before ページングで 50 件超の過去分が実際に返るかは実測されていない**。修正は無害（返らなければ従来と同じ）だが「取りこぼし解消」の保証は §14-1 相当の実測待ち |
| **H4** releases 14日窓累積 | **OK（テスト＋読解）** | `select_recent_albums` は seen で抑止せず窓内を全件返し、is_new は run 開始時の seen 基準で判定・seen 書き戻しは全走査後（intra-run 汚染なし）。`by_album.setdefault` で合作アルバムの重複排除も正しい。NEW バッジは初観測の nightly から翌 nightly までの約1日で消える（仕様として許容範囲） |
| **H5** undo 一覧・実行 UI | **不十分（新たな穴）** | UI・undo_index 生成・dispatch は実装されたが、**undo_index.json を再生成するのは nightly の sitegen だけ**。op_dedupe_apply は undo レコードを書き dupes は再生成するのに undo_index を更新しない → 削除直後の取り消しボタンが翌 nightly まで出ない（→ H-1） |
| **H6** classify 在籍チェック | **OK（読解）** | 追加前に dest の在籍集合を取得し未在籍のみ add、ループ内で `existing[dest].add(tid)` を更新。payload 内に同一 track_id が2回あっても二重追加されない（副次的に防御） |
| **M1** sitegen 失敗可視化 | **OK** | `::error::` を行頭 print で出力。exit 0 のままなのは「nightly を止めない」設計判断として一貫 |
| **M2** 楽観的 UI | **部分的（新たな穴あり）** | localStorage 保持・ボタン無効化・dupes 消滅時の解消は実装。ただし (a) `useJson` は再フェッチしないためリロードしないと解消しない、(b) **op 失敗時は processing が永久残留**（→ M-1）、(c) 設計 §7.4 の「3分未反映で Actions 導線」タイムアウトは未実装 |
| **M3** Wrapped 表示 | **OK** | `wrapped/index.json` 生成（index 自身を除外・初回は空配列）・最新月 fetch・存在しない月/読込失敗は `<Empty>` で null 安全。型整合も確認 |
| **M6** push 失敗の非0化 | **OK** | nightly:108・site-ops:96 に `exit 1` ＋ `::error::` を確認 |
| **M7** concurrency 統一 | **OK・ただし副作用に注意** | listen-log が `spotify-serial` に統一され並行 push は消えた。ただし同一グループ化により pending キャンセル仕様の影響面が広がった（→ M-2） |
| **M8** フィクスチャ fallback 禁止 | **OK** | `set -euo pipefail`＋fallback 撤去。取得失敗はビルド失敗になり前回デプロイが残る（正しい挙動） |
| **L1** undo id マイクロ秒 | **OK** | `%f` 追加を確認 |
| **L3** モバイル表溢れ | **OK** | Home.tsx:65 `overflowX: "auto"` を確認 |

---

## 新規・残存の指摘（深刻度順）

### H-1. 削除直後の undo がサイトから実行できない — undo_index が siteops で更新されない
- **対象**: `siteops.py:107-132`（`op_dedupe_apply`）/ `sitegen.py:458-478`（`_write_undo_index`）
- **症状**: op_dedupe_apply は undo レコード（`data/undo/*.json`）を書き、dupes.json は再生成して
  data ブランチに commit されるが、**Organize の UndoSection が読む `undo_index.json` は nightly の
  sitegen でしか再生成されない**。つまり「削除 → 直後に間違いに気付く → サイトの取り消しボタン」という
  undo の最重要ユースケースで、ボタンが**翌 01:00 JST まで存在しない**。1巡目 H5 の修正意図
  （「削除は undo とセットだから安全」をサイト操作者が行使できること）が時間軸で半分しか達成されていない。
  同様に op_undo 後も index が古いまま残り、`.done` 化済みエントリの取り消しボタンが押せてしまう
  （押すと OpError → 失敗 Issue が立つ。→ L-2）。
- **推奨修正**: `op_dedupe_apply`・`op_undo`（ついでに `op_classify_apply`）の末尾で
  `sitegen._write_undo_index(data)` を呼ぶ（import の向きが気になるなら関数を `siteops` か共通モジュールへ
  移す）。数行で H5 が完全に閉じる。

### M-1. op 失敗・キャンセル時に「処理中」が永久残留し、そのグループが操作不能になる
- **対象**: `site/src/lib/processing.ts:22-38` / `site/src/pages/Organize.tsx:17-22, 95-122`
- **症状**: `markProcessing(g.id)` の解消条件は「dupes.json からグループが消えること」**のみ**。
  dispatch は 204 で成功しても、その後の run が検証エラー（stale payload 等）で失敗・あるいは
  キャンセル（→ M-2）されるとグループは消えず、processing が localStorage に**無期限に残る**。
  結果、そのグループの「選んだ方を残して削除」「両方残す」ボタンが**恒久的に disabled**
  （復旧手段は DevTools で localStorage を消すことだけ）。1巡目 M2 対応で導入された永続化の副作用で、
  修正前（リロードで復活する一時文字列）には無かった詰み方。設計 §7.4 が要求する
  「3分未反映なら Actions リンク」のタイムアウト解消も未実装のまま。
- **推奨修正**: markProcessing が保存している ISO 時刻を使い、例えば 30 分経過したエントリは
  `useProcessing`／reconciliation effect で自動解除して「反映を確認できませんでした。Actions を確認」
  を表示する。合わせて dupes.json の `generated_at` を定期再フェッチ（→ L-5）すればリロード不要になる。

### M-2. 同一 concurrency グループの pending キャンセルで、連続 dispatch の中間 op が静かに消える
- **対象**: `.github/workflows/site-ops.yml:26-28`（`group: spotify-serial`）/ `Organize.tsx`（各カードが独立に dispatch 可能）
- **症状**: GitHub Actions の concurrency は「実行中1件＋pending 1件」までで、**新しい run が来ると
  既存の pending はキャンセルされる**。サイトは複数グループのカードから連続で dispatch できるため、
  短時間に3件以上操作すると中間の op がキャンセルされる。キャンセルは `failure()` を発火しないので
  **Issue も立たず完全に無音**。さらに当該グループは markProcessing 済みなので M-1 と合流して
  「処理中のまま何も起きない」状態になる。nightly が pending 中にサイト操作を挟むと nightly 側が
  キャンセルされる事故もあり得る（その日のデータ生成が飛ぶ）。
- **推奨修正**: フロントで dispatch を直列化する（processing が1件でもあれば他グループの実行ボタンも
  disable、あるいは「実行キュー」を localStorage に持ち1件ずつ送る）。ワークフロー側だけで解決するなら
  op ごとに payload を単一 run へまとめる UI（複数 decision の一括送信は plan_dedupe が既に対応済み）へ
  寄せるのが筋が良い。

### M-3. undo の復元先が snapshot playlists のみで、削除範囲（全管理プレイリスト）と非対称
- **対象**: `siteops.py:73-78`（plan_dedupe が snapshot の playlists を記録）/ `siteops.py:121-128`（削除は全管理 PL）/ `siteops.py:190-198`（undo は記録された playlists にのみ再追加）
- **症状（フェイク sp で実測）**: 削除は pW/pJ/pAP の全 3 管理 PL に当たるのに、undo レコードの
  playlists は snapshot 由来の `['pW']` のみで、再追加も pW だけ。dupes.json スキャン後〜削除実行の間に
  sync がその曲を AP へ追加していた場合（まさに H1 が守った staleness 窓）、**削除は AP からも消すが
  undo は AP へ戻さない**。洋楽は翌 nightly の順方向 sync（source=Western → AP）で自己修復するが、
  手動で足した在籍や邦楽 AP は戻らない。「undo で完全復元」ではなく「undo でおおむね復元」になっている。
- **推奨修正**: 削除の直前に remove 対象 track の**実在籍**（全管理 PL への in-membership）を取得して
  undo レコードに記録する（削除前なので正確）。コスト増が嫌なら「undo は記録時点の在籍に戻す。
  スキャン後に増えた在籍は sync が補完する（邦楽 AP は対象外）」と設計に明記して期待値を揃える。

### M-4. op_keep_apply が payload を現在の dupes と照合せず、dupes.json も再生成しない
- **対象**: `siteops.py:175-187`（`op_keep_apply`）/ `Organize.tsx:108-117`（keepBoth）
- **症状**: (1) `add[].group_id` / `track_ids` を dupes.json と一切照合しない。モジュール docstring の
  「payload は現在の data と必ず照合」の唯一の例外になっており、stale な payload（構成が変わった後の
  古い track_ids）を黙って記録する（その keep は現行グループに一致せず効かない＝実害は小さいが
  原則違反）。(2) keep を記録しても dupes.json を再生成しないため、「両方残す」を押したグループは
  **翌 nightly まで Organize に表示され続ける**。UI は「処理中… 反映まで数分」と表示するので
  文言と実態が乖離し、processing 状態も最大24時間残る。
- **推奨修正**: keep-apply でも group_id の存在と track_ids==グループ構成を検証（不一致は OpError）。
  反映は full scan 不要 — **既存 dupes.json から該当グループを取り除いて書き戻すだけ**（counts 再計算込み）
  で数行、Spotify API 呼び出しゼロで即時反映できる。

### Low

- **L-1. CJK 直結キーワードの検出漏れ（C1 修正の軽微な退行）**: `dedupe.py:35-41`。`\b` は CJK 文字と
  英字の間で成立しないため、`曲名（劇場版live）`・`曲名（アコースティックversion）` のように版キーワードが
  日本語に直結していると除去されない（実測）。空白や括弧で区切られた通常表記（`夜に駆ける (Acoustic)`）は
  問題なし。誤マージではなく検出漏れ方向なので安全側。気になるなら該当キーワードだけ
  `(?<![A-Za-z])…(?![A-Za-z])`（英字境界）へ緩める。
- **L-2. undo 実行後・削除実行後の undo_index が古く、再クリックが失敗 Issue を量産**: H-1 と同根。
  `.done` 化済み undo_id を dispatch すると OpError → run 失敗 → Issue 起票。siteops での index 再生成で同時に解消。
- **L-3. undo 一覧に期限がない**: `sitegen.py:458-478` は全 undo/*.json を無期限に列挙する。数ヶ月前の
  削除の「取り消し」が並び続け、押せば当時の曲が復活する。表示を直近 N 日に絞るか、undo レコードに
  有効期限を持たせる。
- **L-4. `processing.ts:68` の getServerSnapshot が毎回新オブジェクト**: `() => ({})` は呼び出しごとに
  別参照を返す。現状 `createRoot`（main.tsx:8）のクライアント描画のみなので呼ばれず無害だが、
  SSR/hydrateRoot 化すると無限再描画になる。モジュール定数 `const EMPTY = {}` を返すだけで安全になる。
- **L-5. useJson が再フェッチしない**: `data.ts:25-37` はマウント時1回のみ。楽観的 UI の解消も
  「数分後にサイトへ反映されます」も、実際は**手動リロードが必要**。visibilitychange か 60 秒間隔の
  再フェッチを dupes/undo_index だけにでも入れると M-1/M-2 の体感が大きく改善する。
- **L-6. classify 連打の失敗 Issue**: `Organize.tsx:209-225` は dispatch 成功後にボタンが再有効化され、
  processing マークも無い。同じ曲をもう一度押すと2本目の run が「unknown に無い曲」で失敗し Issue が立つ。
  track_id を markProcessing する（unknown.json から消えたら解消）だけで揃う。

---

## 見送り項目（M4/M5/L2/L4/L5）の再評価

- **M4（Tier A の削除経路なし）— 見送り妥当**。Tier A は「同一 ID が同一 PL に2回」で発生源は手動事故のみ。
  サイトで検出・表示され気付けるし、Spotify アプリから1occurrence 消すのが最短。位置指定削除
  （snapshot_id ガード＋positions）は複雑度に見合わない。ただし keep でも隠せないため、放置すると
  恒久的に表示され続ける点は認識しておく（report のままで実害なし）。
- **M5（classify が邦楽 AP へ入れない）— 見送り許容・ただし恒久欠損**。洋楽は翌 sync で AP 補完されるが、
  邦楽 AP は自己修復経路が**存在しない**ので、サイト経由で振り分けた邦楽曲はその artist AP に永久に
  入らない（inbox 経由と結果が恒久に食い違う）。実害は「AP に1曲欠ける」で小さいが、修正は
  `inbox.load_inbox_config` の jp_artists 名前一致を op_classify_apply に移植する 10 行程度。
  次フェーズの最初に入れる価値はある。
- **L2（main push の rebase なし）/ L4（releases_seen 単調増加。H4 対応で全 album_id を蓄積するように
  なったぶん増加は速くなった）/ L5（データ全滅時の健全性バナー）— いずれも見送り妥当**。実害が出る
  頻度・規模が小さく、運用で気付ける。

---

## 再実装の優先順位

1. **H-1**: siteops の各 op 末尾で undo_index を再生成（数行）。「削除即 undo 可能」を成立させる最優先。
2. **M-1 + L-5**: processing のタイムアウト解消（30分で自動解除＋Actions 導線）と dupes/undo_index の
   定期再フェッチ。操作系の「詰み」を無くす。
3. **M-4**: keep-apply の payload 照合＋dupes.json の軽量即時更新（API 呼び出し不要）。
4. **M-3**: undo レコードに削除直前の実在籍を記録する（か、制約を設計書に明記）。
5. **M-2**: dispatch の直列化（processing 中は全実行ボタンを disable が最小実装）。
6. **L-1〜L-6** を掃除。あわせて §14-1 の実削除立ち会いで「dedupe-apply → 翌 nightly no-op 収束」と
   「listen-log の 50 件超時の実挙動」（H3 の実効性）を実測する。

---

## 検証方法の明示

- **実測**: `normalize_title` 36 ケース／`build_groups`（空 ISRC 混在・ISRC のみリンク・推移併合）／
  `plan_dedupe`（正常系・矛盾2決定・group_id 欠落）／`dupes_from_records`（keep 突合・構成変化で再表示）／
  `op_dedupe_apply`＋`op_undo` のフェイク sp 実行（削除範囲・undo 復元先・.done 化）。
  pytest 50 / ruff / tsc は全て clean。
- **読解のみ（未実測）**: H3 の実 API 挙動（recently-played の 50 件履歴上限下で before ページングが
  過去分を返すか）、M-2 の GitHub concurrency の pending キャンセル（GitHub 公式ドキュメントの仕様に
  基づく。実ワークフローでの再現はしていない）、Vercel ビルドでの fetch-data.sh 失敗時挙動。

---

## Opus 対応記録（2026-07-17・2巡目）

2巡目の指摘を反映。pytest 51 passed / ruff clean / site typecheck clean を実測。

| 指摘 | 対応 |
|---|---|
| **H-1** 削除直後の undo が翌 nightly まで出ない | `siteops` の各 op（dedupe/classify/undo）末尾で `sitegen.write_undo_index` を即実行。`_write_undo_index`→`write_undo_index` に public 化 |
| **M-1** processing の永久残留 | 30分経過で自動解除（`stuckIds`＋60秒間隔チェック）。処理中カードに「Actions で確認」導線 |
| **M-2** 連続 dispatch の中間 op 消失 | フロントで dispatch を直列化（`anyProcessing` の間は他グループ／unknown の実行ボタンも無効化） |
| **M-3** undo の復元先が snapshot のみ | dedupe-apply が削除前に remove 対象の実在籍（全管理 PL）を取得し undo に記録 |
| **M-4** keep-apply が未照合＋翌日反映 | dupes と group_id/track_ids を照合（不一致は OpError）。dupes.json から該当グループを除いて即時反映（API 不要） |
| **L-2** .done 済み undo の再クリック失敗 | H-1 の index 即更新で解消 |
| **L-3** undo 一覧が無期限 | `write_undo_index` を直近30日に限定 |
| **L-4** getServerSnapshot が新オブジェクト | モジュール定数 `EMPTY` を返す |
| **L-5** 手動リロード必須 | `useJson`/`useJsonl` をタブ可視化時に再フェッチ |
| **L-6** classify 連打の失敗 Issue | 成功後に track_id を markProcessing（unknown から消えたら解消） |

**見送り:** L-1（CJK 直結キーワードの検出漏れ＝誤マージではなく安全側の検出漏れ・稀）。M5（邦楽 AP）は次フェーズ候補として据え置き。§14-1 の実削除立ち会い（H1 の no-op 収束・H3 の 50件超実挙動）は本人作業で実測予定。
