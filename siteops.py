#!/usr/bin/env python3
"""siteops.py — サイト発の操作を実行する（Phase 3・dashboard-design §7）

ブラウザ（静的サイト）から fine-grained PAT で site-ops.yml を workflow_dispatch し、
このスクリプトが op と payload を受けて Spotify を変更する。サーバレス関数を持たない。

op:
  dedupe-apply   重複グループの remove を全出現プレイリストから同時削除（sync 整合・undo 記録）
  dedupe-trim    Tier A（同一プレイリスト内の重複）を1つ残して余分だけ位置指定削除（undo 記録）
  classify-apply unknown 曲を邦楽/洋楽へ移動（cache に manual 記録）
  keep-apply     「両方残す」を dedupe_keep.json に記録
  undo           過去の削除を再追加で復元

安全（§7.3）:
  - payload は現在の data と必ず照合。不一致は「何も変更せず」失敗する（部分適用しない）
  - 削除は必ず undo 記録とセット。undo 記録に失敗したら削除しない
  - 変更系 API を呼ぶのはこのスクリプトと既存4ツールだけ

Usage:
  python siteops.py --op <op> --payload '<json>' --data-dir <dir>
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import core

# 操作に要るのは playlist + library のみ。これは現行トークンに含まれるため再認証前でも動く。
SCOPE = (
    "playlist-modify-private playlist-modify-public playlist-read-private "
    "user-library-read user-library-modify"
)


class OpError(Exception):
    """検証失敗。何も変更せずに失敗させる。"""


# ─────────────────────────── 検証（純関数・テスト対象） ───────────────────────────

def plan_dedupe(dupes: dict, decisions: list) -> list:
    """decisions を現在の dupes.json と照合し、削除対象リストを返す。
    1件でも不整合があれば OpError（部分適用しない）。"""
    groups = {g["id"]: g for g in dupes.get("groups", [])}
    # 同一 group_id への複数 decision を拒否（レビュー C3）。矛盾する2件
    # （keep:[a]/remove:[b] と keep:[b]/remove:[a]）が各々合格して全曲削除されるのを防ぐ。
    seen_gids: set[str] = set()
    for d in decisions:
        gid = d.get("group_id")
        if gid in seen_gids:
            raise OpError(f"同一グループへの決定が重複しています: {gid}")
        seen_gids.add(gid)

    removals: list[dict] = []
    for d in decisions:
        gid = d.get("group_id")
        g = groups.get(gid)
        if not g:
            raise OpError(f"グループが存在しません: {gid}")
        if g.get("tier") == "A" or "tracks" not in g:
            raise OpError(f"このグループは削除操作に未対応です: {gid}")
        members = {t["id"] for t in g["tracks"]}
        keep = set(d.get("keep", []))
        remove = set(d.get("remove", []))
        if keep & remove:
            raise OpError(f"keep と remove が重複しています: {gid}")
        if keep | remove != members:
            raise OpError(f"keep∪remove がグループ構成と一致しません: {gid}")
        if not keep or not remove:
            raise OpError(f"残す・削除は各1件以上必要です: {gid}")
        for t in g["tracks"]:
            if t["id"] in remove:
                removals.append(
                    {"track_id": t["id"], "name": t.get("name", ""),
                     "playlists": [p["id"] for p in t.get("playlists", [])]}
                )
    return removals


def plan_classify(unknown: dict, decisions: list) -> list:
    known = {t["id"] for t in unknown.get("tracks", [])}
    out: list[dict] = []
    for d in decisions:
        tid = d.get("track_id")
        cls = d.get("class")
        if tid not in known:
            raise OpError(f"unknown に無い曲です: {tid}")
        if cls not in ("japanese", "western"):
            raise OpError(f"class が不正です: {cls}")
        out.append({"track_id": tid, "class": cls})
    return out


# ─────────────────────────── 実行 ───────────────────────────

def _ts() -> str:
    # マイクロ秒まで含めて同秒2操作での undo ファイル衝突を防ぐ（レビュー L1）
    return datetime.now(core.JST).strftime("%Y-%m-%dT%H%M%S%f")


def _write_undo(data: Path, record: dict) -> None:
    core.atomic_write_json(data / "undo" / f"{record['id']}.json", record)


def op_dedupe_apply(sp, data: Path, payload: dict, logger) -> None:
    import dedupe
    import inbox

    dupes = json.loads((data / "dupes.json").read_text())
    removals = plan_dedupe(dupes, payload.get("decisions", []))
    if not removals:
        logger.info("削除対象なし")
        return

    remove_ids = [r["track_id"] for r in removals]
    managed = dedupe.managed_playlists()

    # レビュー M3: 削除前に remove 対象の「実在籍」を全管理プレイリストで取得し undo に記録する。
    # snapshot（dupes.json の playlists）は古く、削除範囲（全 PL）と非対称になるため。
    live: dict[str, list[str]] = {}
    for pl in managed:
        ids = inbox.playlist_track_ids(sp, pl["id"])
        for tid in remove_ids:
            if tid in ids:
                live.setdefault(tid, []).append(pl["id"])

    # undo を先に確定（記録できないなら削除しない・§7.3-2）。
    # レビュー L-A: live 在籍が空なら削除は no-op なので undo も何もしない（fallback は snapshot でなく []）。
    undo_removed = [
        {"track_id": r["track_id"], "name": r["name"], "playlists": live.get(r["track_id"], [])}
        for r in removals
    ]
    undo = {"id": _ts(), "op": "dedupe-apply", "created_at": datetime.now(core.JST).isoformat(),
            "removed": undo_removed}
    _write_undo(data, undo)
    _refresh_undo_index(data)  # レビュー L-B: 削除でクラッシュしても取り消せるよう先に index 更新

    # レビュー H1: dupes.json の playlists は前回スキャン時点の写像で古い可能性がある。
    # snapshot だけ消すと sync が AP へ追加した曲が残留する（dedupe-requirements §6 が禁じた状態）。
    # 全管理プレイリストから remove-all-occurrences（idempotent）して残留を構造的に潰す。
    for pl in managed:
        core.remove_in_batches(sp, pl["id"], remove_ids)
    logger.info(f"削除: {len(remove_ids)} 曲を全 {len(managed)} プレイリストから除去")

    _regenerate_dupes(sp, data)
    logger.info(f"完了: undo={undo['id']}")


def _track_positions(sp, playlist_id: str, track_id: str) -> list[int]:
    """playlist 内で track_id が出現する 0 始まりの位置を、並び順どおりに返す。"""
    positions: list[int] = []
    idx = 0
    results = sp.playlist_items(
        playlist_id, fields="items(track(id)),next", additional_types=("track",), limit=100
    )
    while results:
        for item in results.get("items", []):
            tr = item.get("track") or {}
            if tr.get("id") == track_id:
                positions.append(idx)
            idx += 1
        results = sp.next(results) if results.get("next") else None
    return positions


def op_dedupe_trim(sp, data: Path, payload: dict, logger) -> None:
    """Tier A（同一プレイリスト内に同じ track_id が複数）を、1つだけ残して余分を削除する。
    位置指定削除（remove_specific_occurrences）を使うので、他の同名曲や別プレイリストには触れない。"""
    dupes = json.loads((data / "dupes.json").read_text())
    gid = payload.get("group_id")
    g = next((x for x in dupes.get("groups", []) if x.get("id") == gid), None)
    if not g:
        raise OpError(f"グループが存在しません: {gid}")
    if g.get("tier") != "A":
        raise OpError(f"trim は Tier A（同一プレイリスト内の重複）専用です: {gid}")
    pid = (g.get("playlist") or {}).get("id")
    track = g.get("track") or {}
    tid, name = track.get("id"), track.get("name", "")
    if not pid or not tid:
        raise OpError(f"グループ情報が不完全です: {gid}")

    # スナップショットを先に取り、その並びに対する位置で削除する（位置ずれ防止）。
    snapshot = sp.playlist(pid, fields="snapshot_id").get("snapshot_id")
    positions = _track_positions(sp, pid, tid)
    if len(positions) < 2:
        logger.info("重複はすでに解消済み（削除なし）")
        _regenerate_dupes(sp, data)
        return
    remove_positions = positions[1:]  # 先頭の1つは必ず残す

    # undo を先に確定（記録できないなら削除しない・§7.3-2）。削除した個数だけ再追加できるよう
    # playlist_id を個数ぶん列挙する（op_undo は playlists の要素ごとに1回 add する）。
    undo = {
        "id": _ts(), "op": "dedupe-trim", "created_at": datetime.now(core.JST).isoformat(),
        "removed": [{"track_id": tid, "name": name, "playlists": [pid] * len(remove_positions)}],
    }
    _write_undo(data, undo)
    _refresh_undo_index(data)

    sp.playlist_remove_specific_occurrences_of_items(
        pid, [{"uri": f"spotify:track:{tid}", "positions": remove_positions}], snapshot_id=snapshot
    )
    logger.info(f"trim: {name} を {len(remove_positions)} 個削除（1つ残す） pl={pid} undo={undo['id']}")
    _regenerate_dupes(sp, data)


def op_classify_apply(sp, data: Path, payload: dict, logger) -> None:
    import classify
    import inbox

    unknown = json.loads((data / "unknown.json").read_text())
    valid = plan_classify(unknown, payload.get("decisions", []))
    jp_id, western_id, _ = inbox.load_inbox_config(inbox.INBOX_CONFIG_PATH)
    cache = classify.load_cache()
    # レビュー H6: 在籍チェックなしで add すると同一 track_id が2つ入り Tier A 重複を作る。
    # 追加先の現在の在籍 ID を先に取り、未在籍のみ add する（inbox と同じ防御）。
    existing = {jp_id: inbox.playlist_track_ids(sp, jp_id),
                western_id: inbox.playlist_track_ids(sp, western_id)}

    moved: list[dict] = []
    for d in valid:
        tid, cls = d["track_id"], d["class"]
        track = sp.track(tid)
        dest = jp_id if cls == "japanese" else western_id
        if tid not in existing[dest]:
            core.add_in_batches(sp, dest, [tid])
            existing[dest].add(tid)
        sp.current_user_saved_tracks_delete([tid])  # お気に入りから外して処理済みに
        artist = (track.get("artists") or [{}])[0]
        if artist.get("id"):
            cache[artist["id"]] = {
                "name": artist.get("name", ""), "class": cls, "source": "manual",
                "date": datetime.now(core.JST).date().isoformat(),
            }
        moved.append({"track_id": tid, "class": cls, "dest": dest})
        logger.info(f"振り分け: {track.get('name','')} → {cls}")

    classify.save_cache(cache)
    _write_undo(data, {"id": _ts(), "op": "classify-apply",
                       "created_at": datetime.now(core.JST).isoformat(), "moved": moved})

    remaining = [t for t in unknown["tracks"] if t["id"] not in {d["track_id"] for d in valid}]
    core.atomic_write_json(data / "unknown.json",
                           {"generated_at": datetime.now(core.JST).isoformat(), "tracks": remaining})
    _refresh_undo_index(data)


def op_keep_apply(_sp, data: Path, payload: dict, logger) -> None:
    # レビュー M4: add を現在の dupes と照合（group_id 実在＋track_ids==グループ構成）。
    # 不一致は OpError（docstring「payload は必ず照合」の唯一の例外を解消）。
    dupes = json.loads((data / "dupes.json").read_text())
    by_id = {g["id"]: g for g in dupes.get("groups", []) if "tracks" in g}
    for add in payload.get("add", []):
        gid = add.get("group_id")
        g = by_id.get(gid)
        if not g:
            raise OpError(f"グループが存在しません: {gid}")
        if set(add.get("track_ids", [])) != {t["id"] for t in g["tracks"]}:
            raise OpError(f"track_ids がグループ構成と一致しません: {gid}")

    path = data / "dedupe_keep.json"
    keep = json.loads(path.read_text()) if path.exists() else {"groups": []}
    groups = {g["group_id"]: g for g in keep.get("groups", [])}
    for add in payload.get("add", []):
        groups[add["group_id"]] = {
            "group_id": add["group_id"], "track_ids": add.get("track_ids", []),
            "decided_at": datetime.now(core.JST).date().isoformat(),
        }
    for gid in payload.get("remove", []):
        groups.pop(gid, None)
    core.atomic_write_json(path, {"groups": list(groups.values())})

    # レビュー M4: full scan せず dupes.json から該当グループを除いて即時反映（API 呼び出しゼロ）
    keep_ids = {add["group_id"] for add in payload.get("add", [])}
    kept = [g for g in dupes.get("groups", []) if g.get("id") not in keep_ids]
    dupes["groups"] = kept
    dupes["counts"] = {t: sum(1 for g in kept if g.get("tier") == t) for t in ("A", "B", "C")}
    core.atomic_write_json(data / "dupes.json", dupes)
    logger.info(f"keep 更新: +{len(payload.get('add', []))} / -{len(payload.get('remove', []))}（dupes 即時反映）")


def op_undo(sp, data: Path, payload: dict, logger) -> None:
    undo_id = payload.get("undo_id")
    path = data / "undo" / f"{undo_id}.json"
    if not path.exists():
        raise OpError(f"undo が見つかりません: {undo_id}")
    rec = json.loads(path.read_text())
    for r in rec.get("removed", []):
        for pid in r["playlists"]:
            core.add_in_batches(sp, pid, [r["track_id"]])
    path.rename(path.with_suffix(".done"))  # 二重 undo を防ぐ
    logger.info(f"undo 完了: {undo_id}（{len(rec.get('removed', []))} 曲を再追加）")
    _regenerate_dupes(sp, data)
    _refresh_undo_index(data)  # .done 化を即反映（H-1 / L-2）


def _regenerate_dupes(sp, data: Path) -> None:
    import dedupe
    result = dedupe.scan(sp, dedupe.managed_playlists(), dedupe.load_keep_sets(data))
    core.atomic_write_json(data / "dupes.json", result)


def _refresh_undo_index(data: Path) -> None:
    """op 直後に undo_index.json を再生成する（H-1）。nightly を待たずサイトから取り消せるように。"""
    import sitegen
    sitegen.write_undo_index(data)


OPS = {
    "dedupe-apply": op_dedupe_apply,
    "dedupe-trim": op_dedupe_trim,
    "classify-apply": op_classify_apply,
    "keep-apply": op_keep_apply,
    "undo": op_undo,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="サイト発の操作を実行")
    parser.add_argument("--op", required=True, choices=list(OPS))
    parser.add_argument("--payload", required=True, help="JSON 文字列")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()

    logger = core.setup_logging("siteops")
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as e:
        logger.info(f"payload が不正な JSON です: {e}")
        return core.EXIT_FATAL

    sp = core.build_client(SCOPE)
    try:
        OPS[args.op](sp, Path(args.data_dir), payload, logger)
    except OpError as e:
        logger.info(f"検証失敗（変更なし）: {e}")
        return core.EXIT_FATAL
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("siteops").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
