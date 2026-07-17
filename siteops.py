#!/usr/bin/env python3
"""siteops.py — サイト発の操作を実行する（Phase 3・dashboard-design §7）

ブラウザ（静的サイト）から fine-grained PAT で site-ops.yml を workflow_dispatch し、
このスクリプトが op と payload を受けて Spotify を変更する。サーバレス関数を持たない。

op:
  dedupe-apply   重複グループの remove を全出現プレイリストから同時削除（sync 整合・undo 記録）
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
    return datetime.now(core.JST).strftime("%Y-%m-%dT%H%M%S")


def _write_undo(data: Path, record: dict) -> None:
    core.atomic_write_json(data / "undo" / f"{record['id']}.json", record)


def op_dedupe_apply(sp, data: Path, payload: dict, logger) -> None:
    dupes = json.loads((data / "dupes.json").read_text())
    removals = plan_dedupe(dupes, payload.get("decisions", []))
    if not removals:
        logger.info("削除対象なし")
        return

    # undo を先に確定（記録できないなら削除しない・§7.3-2）
    undo = {"id": _ts(), "op": "dedupe-apply", "created_at": datetime.now(core.JST).isoformat(),
            "removed": removals}
    _write_undo(data, undo)

    # プレイリスト単位でまとめて全出現箇所から削除（sync 整合・§6）
    by_playlist: dict[str, list[str]] = {}
    for r in removals:
        for pid in r["playlists"]:
            by_playlist.setdefault(pid, []).append(r["track_id"])
    for pid, tids in by_playlist.items():
        core.remove_in_batches(sp, pid, tids)
        logger.info(f"削除: {pid} から {len(tids)} 曲")

    _regenerate_dupes(sp, data)
    logger.info(f"完了: {len(removals)} 曲を削除・undo={undo['id']}")


def op_classify_apply(sp, data: Path, payload: dict, logger) -> None:
    import classify
    import inbox

    unknown = json.loads((data / "unknown.json").read_text())
    valid = plan_classify(unknown, payload.get("decisions", []))
    jp_id, western_id, _ = inbox.load_inbox_config(inbox.INBOX_CONFIG_PATH)
    cache = classify.load_cache()

    moved: list[dict] = []
    for d in valid:
        tid, cls = d["track_id"], d["class"]
        track = sp.track(tid)
        dest = jp_id if cls == "japanese" else western_id
        core.add_in_batches(sp, dest, [tid])
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


def op_keep_apply(_sp, data: Path, payload: dict, logger) -> None:
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
    logger.info(f"keep 更新: +{len(payload.get('add', []))} / -{len(payload.get('remove', []))}")


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


def _regenerate_dupes(sp, data: Path) -> None:
    import dedupe
    result = dedupe.scan(sp, dedupe.managed_playlists())
    core.atomic_write_json(data / "dupes.json", result)


OPS = {
    "dedupe-apply": op_dedupe_apply,
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
