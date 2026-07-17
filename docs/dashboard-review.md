# ダッシュボード Phase 1–3 コードレビュー（敵対的レビュー）

レビュー実施: 2026-07-17（Fable 5）。対象: `core.py`（拡張分）/ `dedupe.py` / `listen_log.py` /
`sitegen.py` / `siteops.py` / `reauth.py` / 既存4ツールのサマリ追加分 / `site/`（Vite+React+TS）/
ワークフロー4本 / テスト4本。正典 `docs/dashboard-design.md`・`docs/dedupe-requirements.md` に照らして検証。
`pytest`（43 passed）・`ruff`（clean）はローカルで実行済み。**コードは一切変更していない**（本ファイルの新規作成のみ）。

---

## 総評

土台の設計思想（git=DB・変更系は Actions 経由・payload は信用しない・undo 先行記録・graceful skip）は
一貫していて、純関数の切り出しとテストも丁寧。**が、「プレイリスト破壊を防ぐ」という中核の安全保証に
実際の穴がいくつもある。** 特に (1) 重複検出の正規化が別曲を大量に誤マージする、(2) union-find の推移併合で
「別録音」を「同一録音（ほぼ確実）」と誤ラベルして削除を促す、(3) siteops の payload 検証が同一グループへの
矛盾決定を素通しし、グループ全曲（残すはずの曲を含む）を削除しうる、の3点は、いずれも**残したい曲の消失**に
直結する。加えて dedupe-requirements §6 が要求する「AP 残留を起こさない」保証は dupes.json の鮮度に依存して
おり、現実的な staleness で破れる。設計未達（楽観的 UI・wrapped 表示・undo 一覧・keep のスキャン抑止）も複数。

聴取ログのカーソルは 3時間で50件超えると**取りこぼす**（ページングなし）。sitegen は全例外を握って exit 0 に
潰すため、失敗が可視化されない。総じて「動くが、安全網が想定より薄い」。実削除の初回立ち会い（§14-1）は必須。

### 深刻度サマリ

| 深刻度 | 件数 | 概要 |
|---|---|---|
| **Critical** | 3 | 正規化の過剰マージ / 推移併合による tier-B 誤ラベル / 矛盾決定の素通しで全曲削除 |
| **High** | 6 | AP 残留（dupes.json staleness）/ keep がスキャンに効かない / listen_log 50件超の取りこぼし / 新譜 seen 抑止で表示ほぼ空 / undo がサイトから使えない / classify-apply が Tier A 重複を生む |
| **Medium** | 8 | sitegen 全例外握り潰し / 楽観的 UI 未実装 / wrapped 表示未実装 / Tier A 削除経路なし / classify が AP へ入れない / nightly の push 失敗が無言 / data ブランチ並行書き込み / fixtures が本番に出うる |
| **Low** | 5 | undo id 秒衝突 / main push に rebase なし / モバイルで表の横溢れ / releases_seen 単調増加 / データ全 404 時の無説明 |

---

## Critical

### C1. `normalize_title` の版表記除去が単語境界を持たず、別曲を大量に同一ベースへ潰す
- **対象**: `dedupe.py:33-55`（`_VERSION_WORD`）
- **症状（実測）**: `_VERSION_WORD` は `ft\.?`・`live`・`demo`・`mono` 等を**部分一致（境界なし）**で拾う。
  結果、括弧内・ダッシュ接尾辞に以下が含まれると丸ごと除去される（実際に走らせて確認）:
  - `"Song - Deliver Us"` → `"song"`（"de**liv**er" が `live`）
  - `"Song - Left Behind"` → `"song"`（"Le**ft**" が `ft`）
  - `"Song (Alive)"` → `"song"`（"A**live**"）
  - `"Song (Demons)"` → `"song"`（"**demo**ns"）
  - `"Monochrome - Mono Mix"` → `"monochrome"` は妥当だが `"...- Mono..."` 由来で誤除去も起きる
  同一アーティストの `"Money - Deliver Us"` と `"Money - Left Behind"` は**両方 `"money"` に潰れ、Tier C の
  1グループに誤って統合**される。ダッシュ接尾辞・括弧を持つ実在タイトルは多く、誤検出が日常的に混入する。
- **根拠**: `re.IGNORECASE` の部分一致。`\bedit\b`/`\bversion\b` は境界付きだが、`ft`・`live`・`demo`・`mono`・
  `with `・`remix` 等は境界なし。`normalize_title` はこの語がヒットした括弧/接尾辞を無条件に落とす。
- **なぜ Critical**: 誤マージされたグループは Organize に「別バージョン候補」として並び、ユーザーが
  「これは重複だ」と誤認して**別曲を削除**しうる。人手確認は挟むが、UI が積極的に誤情報を提示する。
- **推奨修正**: `ft`・`live`・`demo`・`mono`・`remix` 等を `\b…\b`（または `\bft\.?\b`）で境界化。
  `with ` は要注意（"within" 誤爆は無いが "swith" 等）。テストに `"- Deliver Us"`/`"(Demons)"`/`"Left Behind"` の
  非マージ回帰を追加。理想は「版キーワードは括弧/接尾辞の**先頭トークン**に限る」など位置制約も足す。

### C2. union-find の推移併合＋tier 判定で「別録音」を「同一録音（ほぼ確実）」と誤ラベル
- **対象**: `dedupe.py:87-144`（`build_groups`／tier 判定 128-135）
- **症状（実測）**: A–B が ISRC 一致、B–C が正規化タイトル一致だと、union-find は A・B・C を**1成分**に併合する
  （A と C は ISRC もタイトルも無関係でも連結される）。tier は「成分内に ISRC 一致ペアが1組でもあれば B」なので、
  タイトルだけで引き込まれた C も含めてグループ全体が **tier B（reason=isrc）** になる。実測:
  `Photograph(album, ISRC=GB1)` + `Photograph(single, ISRC=GB1)` + `Song(Left Behind)(ISRC=ZZ9, title一致)` を
  投入 → `('B','isrc',['a','c','b'])` の単一グループ。C1 の過剰マージと組むと、**まったくの別曲が
  「同一録音・ほぼ確実」バッジ付きで**削除候補に並ぶ。
- **根拠**: `has_isrc_pair` は成分全体を見て1ペアでも真なら B。C を除外する仕組みがない。
- **なぜ Critical**: UI（`Organize.tsx` `TIER_LABEL.B="同一録音"`／`badge-b`）は tier B を最も強く「消してよい」と
  示唆する。推移的に混入した別録音を B と偽ることは、誤削除の直接誘因。dedupe-requirements §3 は C を
  「機械は絶対に決めない」と明記しており、B への昇格は要件違反。
- **推奨修正**: tier はトラック**ペア単位**で判定する（ISRC 一致ペアだけを B、それ以外の連結は C）。
  あるいは ISRC 併合とタイトル併合を**別グループ**として扱い、跨る場合は最弱 tier（C）へ丸める。
  併合理由をトラックごとに保持し、UI で「a↔b は ISRC 一致 / c は題名一致」と可視化するのが安全。

### C3. `plan_dedupe` が同一グループへの矛盾決定を素通しし、残すはずの曲まで全削除する
- **対象**: `siteops.py:43-70`（`plan_dedupe`）
- **症状（実測）**: 同じ `group_id` に対する2つの decision
  `[{keep:[a],remove:[b]}, {keep:[b],remove:[a]}]` を渡すと、各 decision 単体は「keep∪remove==members」を
  満たすため**両方合格**し、`removals` に a と b の両方が積まれる（実測で `removals=['b','a']`）。結果、
  グループの**全トラックが全出現プレイリストから削除**される（残すはずの曲も消える）。同一 group_id の重複を
  弾く検証が無い。
- **根拠**: `plan_dedupe` は decisions をループで独立に検証し、group_id の一意性も keep 集合の整合（同一
  グループで keep と remove が矛盾しないか）も見ていない。
- **なぜ Critical**: 設計 §7.3-1「payload はブラウザ発 = 信用しない。サーバ側検証が唯一の防衛線」を破る。
  正規の UI（`Organize.tsx` は1グループ1 decision）からは踏まないが、**改竄 payload・二重送信・将来の
  一括操作 UI** で即発火する。undo 記録はされるので復元可能だが、「意図しない削除ゼロ」の看板が外れる。
- **推奨修正**: decisions を group_id で一意化（重複は OpError）。さらに、全 decision を集約した後に
  「同一グループで remove された track_id の和集合 ≠ members」を最終検証し、keep が1件も残らない結果を拒否。

---

## High

### H1. dupes.json の staleness で「AP 残留」が起きうる（§6 の中核保証が鮮度依存）
- **対象**: `siteops.py:97-119`（`op_dedupe_apply`）＋ `sync.py:144-170`
- **症状**: 削除対象の出現プレイリスト集合は **保存済み dupes.json の track.playlists** から取る。ある曲 b が
  スキャン時点で Western のみ在籍 → その後の nightly で sync が b を artist AP へ順方向追加 → しかし
  dupes.json は次の nightly まで再生成されない。この窓でサイトから b を削除すると、**削除は Western のみ**に
  当たり、AP の b が残る。翌 nightly: sync 逆方向は「state に b・AP にも b」で削除対象にならず、順方向も
  ソースに b が無いので再追加せず、**b は AP に幽霊として残留**する（dedupe-requirements §6 が「起こしては
  ならない」と名指しした状態）。
- **根拠**: op は data ブランチ HEAD の dupes.json で検証するが、`track.playlists` は前回スキャン時の写像で、
  AP 在籍の最新性を保証しない。group_id は track_id 集合のみから作られ、**playlists の変化では変わらない**ため、
  membership 差では reject されない。
- **推奨修正**: 削除直前に**ライブで各トラックの在籍を再取得**して全出現へ当てる（`collect_records` を op 内で
  対象トラックに限定実行）。少なくとも「全管理プレイリストから該当 track_id を無条件 remove-all-occurrences」に
  変え、dupes.json の playlists を信頼しない。翌 nightly no-op 収束（§14-1）を CI/立ち会いで実測する。

### H2. keep-apply（両方残す）がスキャンに効かず、同じグループが毎晩復活する
- **対象**: `siteops.py:156-168`（`op_keep_apply`）/ `dedupe.py:221-229`（`dupes_from_records`）/ `sitegen.py:391-393`
- **症状**: `dedupe_keep.json` は書かれるが、**どこからも読まれない**。`dupes_from_records`／`scan`／sitegen の
  再生成は keep を一切参照しないため、「両方残す」と決めたグループが翌 nightly の dupes.json に再出現し、
  Organize に出続ける。dedupe-requirements §4.3「`k` の決定を永続化し次回スキャンから表示しない」に違反。
- **根拠**: `grep dedupe_keep` の参照は siteops の書き込みとテストのみ。読取・除外ロジックが存在しない。
- **推奨修正**: `dupes_from_records` に `keep_ids: set[frozenset]` を渡し、グループの track_id 集合が
  keep 済みなら除外。sitegen が data の `dedupe_keep.json` を読んで渡す。keep のキーは設計どおり
  トラック ID 集合（group_id は membership 変化で変わるので集合キーが安全）。

### H3. listen_log が 3時間で50件超のとき最古分を取りこぼす（ページングなし）
- **対象**: `listen_log.py:31-58`（`poll`）
- **症状**: `current_user_recently_played(after=cursor, limit=50)` を**1ページしか読まない**。3時間の窓に
  50件超の再生があると、API は新しい順に50件返し、`max_ms` を最新へ進めて保存するため、**古い側の超過分は
  次回 `after=最新` で二度と取得されない**。短い曲を連続再生する日は 3.6分/曲で50件に達し、現実的に発生。
  設計 §6.1 の「3時間おきに回して取りこぼしを防ぐ」前提が、単ページ取得では崩れる。
- **根拠**: `poll` はレスポンスの `cursors`/`next` を辿らない。max 50 固定。
- **推奨修正**: `resp["cursors"]["after"]` あるいは `next` で**カーソルページング**し、`after=cursor` 以降を
  全部吸うまでループ（安全上限つき）。`limit=50` の1ページ取得を撤廃。テストにページング分岐を追加。

### H4. 新譜ウォッチが seen 抑止で「初回発見の当日だけ」しか出ず、実質ほぼ空
- **対象**: `sitegen.py:236-301`（`select_recent_albums`／`build_releases`）
- **症状**: `select_recent_albums` は `release_date>=cutoff かつ aid not in seen` のみ fresh に入れ、その run で
  見た album を **seen に恒久登録**する。releases.json は毎回 fresh のみで**上書き再生成**される。よって、ある
  新譜は「初めて観測した run」でしか items に入らず、翌 nightly 以降は seen 済みで抑止 → releases.json から消える。
  設計 §5.3/§9 が期待する「直近14日の新譜一覧」が**累積表示されず**、Discover は通常 0〜数件。
- **根拠**: seen は「通知済み」を意味する設計意図だが、releases.json が累積ではなく毎回リセットのため、
  「14日ウィンドウ表示」と「一度きり通知」が両立していない。
- **推奨修正**: releases.json は「seen かどうかに関わらず release_date が直近14日の album 全部」を出す
  （表示は窓ベース）。`releases_seen.json` は「新着の強調（first_seen バッジ）」のためだけに使い、
  抑止条件から外す。あるいは `first_seen` を保持して14日間は表示継続する。

### H5. undo がサイトから実質使えない（一覧 UI も起動 UI も無い）
- **対象**: `site/src/pages/Organize.tsx`（全体）/ 設計 §8 表「Organize: … undo 一覧」
- **症状**: siteops に `undo` op はあるが、フロントに **undo 一覧の取得も dispatch も無い**。静的サイトは
  `data/undo/` を列挙できず（ディレクトリ列挙不可・インデックス JSON も未定義）、`undo_id` を知る術がない。
  結果、「削除は undo とセットだから安全」という前提が**サイト操作者からは行使できない**（手動で
  workflow_dispatch すれば可能だが「CLI はもう叩かない」§1 に反する）。
- **根拠**: `grep undo site/src` はゼロ。undo ファイル一覧を出す `undo_index.json` 等の生成も sitegen に無い。
- **推奨修正**: sitegen が `data/undo/*.json`（未 .done）を集約した `undo_index.json` を生成し、Organize に
  「最近の削除・取り消し」一覧＋`undo` dispatch ボタンを実装する。

### H6. classify-apply が在籍チェックせず追加し、Tier A（同一プレイリスト重複）を生む
- **対象**: `siteops.py:122-153`（`op_classify_apply`）
- **症状**: `core.add_in_batches(sp, dest, [tid])` を**無条件**で呼ぶ。対象曲が既に Japanese/Western Musics に
  在籍していると**同じ track_id が2つ**入り、まさに Tier A 重複を新規作成する（inbox.py は `existing_ids` で
  防いでいるのに、サイト経路だけ防御が抜けている）。unknown.json 由来で「まだ振り分けてない」前提だが、
  他経路で既に入っているケース（手動追加・以前の失敗途中）を吸収できない。
- **根拠**: `op_classify_apply` に在籍集合の取得・除外が無い。
- **推奨修正**: 追加前に dest の在籍 ID を取得して未在籍のみ add（inbox の `playlist_track_ids` を再利用）。

---

## Medium

### M1. sitegen が全例外を握って exit 0 に潰す（失敗が不可視・部分データを本番に流す）
- **対象**: `sitegen.py:459-467`（`_entry`）/ `nightly.yml:86-88`
- **症状**: `_entry` は `except Exception: … return EXIT_OK`。途中でクラッシュしても**一部だけ書けた data を
  commit → deploy 起動**しうる。nightly の「Generate dashboard data」ステップも常に成功扱いになり、
  sitegen の失敗で Issue が立たない（`Report failure` は job 失敗時のみ）。デバッグ困難。
- **推奨修正**: 個別のデータ生成を try 個別化して「どれが失敗したか」をログ＆`sitegen_errors` に残す。
  致命時は非0を返し、workflow で notice/Issue 化する（データ commit は成功分のみ許容する設計に整理）。

### M2. 楽観的 UI（§7.4）が未実装 — 「処理中」は再読込で消える一時文字列
- **対象**: `site/src/pages/Organize.tsx:82-138` / 設計 §7.3・§7.4
- **症状**: dispatch 後は `status` 文字列を出すだけ。設計が要求する「対象グループを **localStorage で処理中表示**、
  dupes.json の `generated_at` 変化で解消、3分未反映なら Actions リンク」は無い。リロードで状態消失、
  反映有無の自動判定も無し（Actions リンクは常時表示）。二重送信抑止も無い。
- **推奨修正**: 処理中 group_id と dispatch 時刻を localStorage に保持し、`generated_at` を監視して解消、
  タイムアウトで Actions 導線を出す。処理中グループのボタンを disable。

### M3. 月間 Wrapped がサイトに表示されない（生成はされるが viewer 不在・月インデックスも無い）
- **対象**: `site/src/pages/Memories.tsx:70-73` / `sitegen.py:371-375`
- **症状**: Memories の「月間 Wrapped」は固定 `<Empty>` プレースホルダ。`data/wrapped/YYYY-MM.json` は月末に
  生成されるが、**どの月が存在するか静的サイトは知れない**（インデックス JSON 未定義）。設計 §3 #16 未達。
- **推奨修正**: sitegen が `wrapped/index.json`（存在月リスト）を生成し、Memories が最新月を fetch して描画。

### M4. Tier A（完全重複）に削除経路が無い
- **対象**: `siteops.py:53-54`（tier A 拒否）/ `Organize.tsx:70-79`（tier A は表示のみ）
- **症状**: dedupe-requirements §4.4 は Tier A を `playlist_remove_specific_occurrences_of_items`＋snapshot ガードで
  消すことを要件化しているが未実装。サイトは Tier A を検出・表示するだけで**直せない**。
- **推奨修正**: 位置指定削除の op を追加するか、当面は「Tier A は inbox 系で手当て」と設計に明記して
  期待値を揃える。

### M5. classify-apply がアーティスト別 AP へ入れない（邦楽は自己修復もされない）
- **対象**: `siteops.py:122-145`（`op_classify_apply`）
- **症状**: inbox.py は邦楽を Japanese Musics **＋一致する artist AP** に入れるが、classify-apply は
  メイン邦/洋バケットのみ。洋楽は翌 nightly の sync（source=Western）で AP へ順方向補完されるが、**邦楽は
  sync 対象外**（source は Western のみ）なので、artist AP に永久に入らない。inbox 経由との結果差。
- **根拠**: `load_inbox_config` の jp_artists を classify-apply は使っていない。
- **推奨修正**: classify-apply でも inbox と同じ AP マッチ（`jp_artists` の名前一致）を実施。仕様として
  「メインのみ」で確定するなら設計に明記。

### M6. nightly / site-ops の data push 失敗が無言（rebase 3回失敗で exit 0）
- **対象**: `nightly.yml:101-106` / `site-ops.yml:90-94`
- **症状**: `for i in 1 2 3; do git push && break; … done` の後に**最終 `exit 1` が無い**。3回とも失敗すると
  ループを抜けて**ステップは成功扱い**、data は未 push のまま deploy hook だけ走る。listen-log.yml は
  `exit 1` があり非対称。
- **推奨修正**: nightly/site-ops のループ末尾に `exit 1` を追加（listen-log と揃える）。失敗時 Issue 化。

### M7. data ブランチへ別 concurrency グループから並行 push（rebase 依存の競合窓）
- **対象**: `listen-log.yml`（group=`data-branch`）vs `nightly.yml`/`site-ops.yml`（group=`spotify-serial`）
- **症状**: 3ワークフローが**同一 data ブランチ**へ push するが、直列化グループが2系統に分かれているため
  listen-log と nightly は**同時実行しうる**。触るファイルが基本非交差（listen-log は listening/*.jsonl と
  `.cursor`、sitegen は集計 JSON 群）なので rebase は概ね綺麗に通るが、`.cursor` や同月 listening を
  両者が触れると競合し得る。M6 の無言失敗と重なると取りこぼす。無限ループの危険は無い（3回上限）。
- **推奨修正**: 全 data ブランチ書き込みを**単一 concurrency グループ**（例 `data-branch`）に統一。
  プレイリスト変更の直列化（spotify-serial）とは別軸なので、data 書き込み用グループを共有させる。

### M8. codeload 失敗時に**フィクスチャが本番に出る**
- **対象**: `site/fetch-data.sh:13-16` / `site/public/data/*`（dry-run 由来の擬似データがコミット済み）
- **症状**: `curl | tar` が失敗すると warning を出して**既存 public/data（フィクスチャ）で続行**。フィクスチャは
  `dry_run:true`・`run_id` 擬似値・`missing_scopes` 全部入りで、これが本番サイトに実データとして表示される。
- **推奨修正**: 本番ビルド（`build:vercel`）では取得失敗を**ビルド失敗**にする（`set -e` で fallback を消す）。
  フィクスチャはローカル dev 専用と明示し、`public/data` にはコミットしない or 明確に区別する。

---

## Low

- **L1. undo id の秒衝突**: `siteops.py:89-90` `_ts()` は秒精度。同秒に2 op が走ると undo ファイルが上書きされ
  1件目が復元不能に。op は分オーダーで直列なので実害は稀。マイクロ秒 or run_id を混ぜると安全。
- **L2. main への push に rebase 無し**: `site-ops.yml:65-76` の cache commit は `git push` 単発。main が動くと
  失敗。稀だが nightly の `Commit state files` も同様（単発 push）。data 側だけリトライがあり非対称。
- **L3. モバイルで表が横溢れ**: `Home.tsx` の実行履歴テーブル（6列）を包む `.card` に `overflow-x:auto` が無い
  （`app.css` で overflow を持つのは heatmap カードのみ）。390px 幅でページ横スクロールが出うる。
- **L4. `releases_seen.json` 単調増加**: `sitegen.py:299` で全 album_id を蓄積し続ける。数年で数万件・毎晩
  全書き換え。実害は小さいが上限・剪定なし。
- **L5. データ全 404 時に無説明**: `data.ts` のエラーは各ページ `data ?? []` で握られ、`<Empty>` になるだけ。
  fetch-data.sh 失敗などで全滅しても「空」に見え、原因が分からない。グローバルなデータ健全性表示が無い。

---

## 設計未達（未実装機能の一覧）

| 設計箇所 | 要求 | 状態 |
|---|---|---|
| §7.4 / §7.3 | 楽観的 UI（localStorage 処理中・generated_at 解消・3分タイムアウト導線・二重送信抑止） | **未実装**（一時文字列のみ）→ M2 |
| §3 #16・§8 Memories | 月間 Wrapped の表示・月インデックス | **未実装**（生成のみ・viewer 無し）→ M3 |
| §8 Organize | undo 一覧・サイトからの undo 実行 | **未実装**（op はあるが UI/index 無し）→ H5 |
| §3 #6・§8 Organize | keep（両方残す）の一覧・取り消し UI | **部分**（追加ボタンのみ・一覧/解除 UI 無し） |
| dedupe-req §4.3 | keep 済みグループをスキャンから除外 | **未実装** → H2 |
| dedupe-req §4.4 | Tier A の位置指定削除（snapshot ガード） | **未実装** → M4 |
| §9 / §3 #13 | 新譜「直近14日」の累積表示 | **機能不全**（初回のみ）→ H4 |
| §6.1 | 3時間窓の取りこぼし防止（>50件） | **不十分**（単ページ）→ H3 |
| §6 dedupe-req | AP 残留ゼロ保証 | **鮮度依存で破れる** → H1 |
| §14-1 | 削除後翌 nightly の no-op 収束・実削除立ち会い | **未実測**（本人作業として残・要検証） |

---

## 良い点（簡潔に）

- **site-ops の payload 注入対策は正しい**: `site-ops.yml:60-63` は payload を `env: PAYLOAD` 経由で
  `--payload "$PAYLOAD"` に渡し、シェル展開に晒していない。`op` は `choice` 型で4値限定。ここは堅い。
- **undo 先行記録**: `op_dedupe_apply` は削除前に undo を確定（§7.3-2 準拠）。設計思想どおり。
- **純関数の分離とテスト**: 正規化・週集計・ヒートマップ・group_id・payload 検証を純関数化し pytest 43件。
  `make_group_id` の順序不変性、JST 変換、月バケットなど要所を押さえている。
- **graceful skip / atomic_write_json / JSONL 追記** の基盤は堅実。トークン失効時に auth_status だけ更新して
  止まらない設計は良い。
- **group_id が track_id 集合の sha1** で membership 変化に追従し、古い payload を自然に reject できる骨格。

---

## 再実装の優先順位（上から順に直す）

1. **C3**: `plan_dedupe` に group_id 一意化＋keep 残存の最終検証を追加（全曲削除を塞ぐ・最優先）。
2. **C1 / C2**: `_VERSION_WORD` を単語境界化し、tier 判定を**ペア単位**へ（別曲マージと B 誤ラベルを止める）。
   併せて回帰テスト（`- Deliver Us`/`(Demons)`/`Left Behind` 非マージ、ISRC ペアのみ B）。
3. **H1**: dedupe-apply を「対象 track の在籍をライブ再取得して全出現削除」に変更し、dupes.json の
   playlists を信頼しない。翌 nightly no-op 収束を実測（§14-1 の立ち会いで確認）。
4. **H6**: classify-apply に在籍チェックを入れ、Tier A 重複の新規作成を防ぐ。
5. **H3**: listen_log をカーソルページング化（>50件取りこぼし解消）。
6. **H2**: `dedupe_keep.json` をスキャン除外に接続（keep が実際に効くように）。
7. **H4**: releases を「14日窓の累積表示」に変更、seen は強調用途に限定。
8. **H5 / M2 / M3**: undo 一覧・楽観的 UI・wrapped 表示を実装（サイト完結の受け入れ §14 を満たす）。
9. **M6 / M7 / M8**: data push 失敗を非0化・data 書き込みを単一 concurrency へ・本番ビルドで
   フィクスチャ fallback を禁止。
10. **M1**: sitegen の例外握り潰しを個別化し、失敗を可視化（Issue/notice）。
11. 残り Low（L1–L5）を掃除。

（未検証の明示）H3 の「50件超で取りこぼす」は Spotify recently-played の `after` が新しい順に最大 limit 件を
返す仕様に基づく推論で、**実 API での 50件超再現は未実施**。ページング未実装という構造的欠落は確実。
H1 の no-op 収束破れは staleness 窓の論理追跡による指摘で、**実削除での再現は未実施**（§14-1 立ち会いで要確認）。

---

## Opus 対応記録（2026-07-17・再実装）

Fable 5 の指摘を受けて修正した内容。回帰テストを追加し pytest 50 passed / ruff clean / site typecheck clean を実測。

| 指摘 | 対応 |
|---|---|
| **C1** 正規化の過剰マージ | `_VERSION_WORD` を全単語境界 `\b` 化。`"Money - Deliver Us"`/`"(Demons)"` 等の非マージ回帰を追加 |
| **C2** tier-B 誤ラベル | tier 判定を「グループ全員が同一の非空 ISRC のときだけ B、それ以外 C」に変更。実データで Viva La Vida（別 ISRC）が正しく C に |
| **C3** 矛盾決定で全曲削除 | `plan_dedupe` に同一 group_id 重複の拒否を追加。回帰テスト追加 |
| **H1** AP 残留 | dedupe-apply を「全管理プレイリストから remove-all-occurrences」に変更し dupes.json の playlists を信頼しない |
| **H2** keep が効かない | `dedupe_keep.json` を `dupes_from_records`/scan の除外に接続（sitegen・siteops 両方） |
| **H3** 50件超取りこぼし | `poll` を before カーソルページング化。ページング回帰テスト追加 |
| **H4** 新譜がほぼ空 | releases を「14日窓の累積表示」に変更、`is_new` バッジ用途へ。album 重複排除も追加 |
| **H5** undo が使えない | sitegen が `undo_index.json` を生成、Organize に undo 一覧＋取り消しボタンを実装 |
| **H6** classify で Tier A 生成 | classify-apply に在籍チェックを追加（未在籍のみ add） |
| **M1** 失敗不可視 | sitegen の例外を `::error::` アノテーションで可視化 |
| **M2** 楽観的 UI 未実装 | `processing` を localStorage 保持・ボタン無効化・dupes 更新で解消 |
| **M3** Wrapped 未表示 | `wrapped/index.json` 生成、Memories に Wrapped 表示を実装 |
| **M6** push 失敗が無言 | nightly/site-ops の data push ループ末尾に `exit 1` |
| **M7** data 並行 push | listen-log の concurrency を `spotify-serial` に統一 |
| **M8** フィクスチャ本番流出 | `fetch-data.sh` を取得失敗でビルド失敗に（fallback 撤去） |
| **L1** undo id 秒衝突 | `_ts()` にマイクロ秒 |
| **L3** モバイル表溢れ | 実行履歴テーブルを `overflow-x:auto` で包む |

**意図的に見送り（低優先・許容範囲）:** M4（Tier A の位置指定削除。稀・report のまま）/ M5（classify を邦楽 AP へも入れる。洋楽は翌 sync で補完・邦楽は手動で足せる）/ L2（main push の rebase）/ L4（releases_seen 剪定）/ L5（データ全滅時の健全性バナー）。次の熟成フェーズで対応。
