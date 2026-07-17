#!/usr/bin/env python3
"""Spotify Inbox Processor

お気に入りの曲を邦楽/洋楽に判定して各プレイリストへ振り分け、
処理済みの曲をお気に入りから削除する。

判定は classify.py のパイプライン（キャッシュ→ISRC→かな→genres→Gemini一括）。
判定不能な曲は log/unknown_tracks.txt に書き出し、お気に入りには残す（次回再判定）。

Usage:
  python inbox.py [--dry-run]
"""

import sys
from pathlib import Path

import classify
import core

BASE_DIR = Path(__file__).resolve().parent
INBOX_CONFIG_PATH = BASE_DIR / "inbox.txt"
UNKNOWN_TRACKS_PATH = core.LOG_DIR / "unknown_tracks.txt"

SCOPE = (
    "playlist-modify-private playlist-modify-public playlist-read-private "
    "user-library-read user-library-modify"
)


def load_inbox_config(path: Path) -> tuple[str, str, dict[str, str]]:
    cfg = core.parse_config(path)
    japanese_id = cfg.pop("JAPANESE_MUSICS_ID", "")
    western_id = cfg.pop("WESTERN_MUSICS_ID", "")
    if not japanese_id:
        raise RuntimeError(f"JAPANESE_MUSICS_ID が {path} に設定されていません")
    if not western_id:
        raise RuntimeError(f"WESTERN_MUSICS_ID が {path} に設定されていません")
    artists = {k.lower(): core.extract_playlist_id(v) for k, v in cfg.items()}
    return core.extract_playlist_id(japanese_id), core.extract_playlist_id(western_id), artists


def get_liked_tracks(sp) -> list[dict]:
    tracks: list[dict] = []
    results = sp.current_user_saved_tracks(limit=50)
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("id"):
                tracks.append(track)
        results = sp.next(results) if results.get("next") else None
    return tracks


def playlist_track_ids(sp, playlist_id: str) -> set[str]:
    return {t["id"] for t in core.iter_playlist_tracks(sp, playlist_id, "items(track(id)),next")}


def main() -> int:
    logger = core.setup_logging("inbox")
    dry = core.is_dry_run()
    japanese_id, western_id, jp_artists = load_inbox_config(INBOX_CONFIG_PATH)

    sp = core.build_client(SCOPE)
    liked = get_liked_tracks(sp)
    if not liked:
        logger.info("お気に入りに新しい曲はありません")
        core.write_step_summary(
            "inbox",
            {"processed": 0, "japanese": 0, "western": 0, "unknown_count": 0, "unknown": []},
        )
        return core.EXIT_OK

    logger.info(f"お気に入り: {len(liked)}曲を処理します" + (" [DRY-RUN]" if dry else ""))

    cache = classify.load_cache()
    name_cache: dict[str, str] = {}
    playlist_cache: dict[str, set[str]] = {}

    def playlist_name(pid: str) -> str:
        if pid not in name_cache:
            try:
                name_cache[pid] = sp.playlist(pid, fields="name")["name"]
            except Exception:
                name_cache[pid] = pid
        return name_cache[pid]

    def existing_ids(pid: str) -> set[str]:
        if pid not in playlist_cache:
            playlist_cache[pid] = playlist_track_ids(sp, pid)
        return playlist_cache[pid]

    # 第1パス: 決定的な手段で判定。unknown は集約して後で Gemini 一括
    labels: dict[str, str] = {}
    unknown_artist_names: dict[str, str] = {}
    for track in liked:
        label = classify.classify_track(sp, track, cache)
        labels[track["id"]] = label
        if label == "unknown":
            artist = (track.get("artists") or [{}])[0]
            if artist.get("id"):
                unknown_artist_names[artist["id"]] = artist.get("name", "")

    # 第2パス: 残った unknown を Gemini で一括判定
    if unknown_artist_names:
        gemini_map = classify.classify_unknowns_with_gemini(unknown_artist_names, cache, logger)
        for track in liked:
            if labels[track["id"]] == "unknown":
                artist = (track.get("artists") or [{}])[0]
                cls = gemini_map.get(artist.get("id"))
                if cls:
                    labels[track["id"]] = cls

    # 振り分け決定
    jp_ids: list[str] = []
    western_ids: list[str] = []
    artist_adds: dict[str, list[str]] = {}
    processed: list[dict] = []
    unknown_final: list[dict] = []

    for track in liked:
        tid = track["id"]
        name = track["name"]
        all_names = [a["name"] for a in track["artists"]]
        primary = all_names[0]
        label = labels[tid]
        logger.info(f"  [{label}] {name} / {primary}")

        if label == "japanese":
            dest: list[str] = []
            if tid not in existing_ids(japanese_id):
                jp_ids.append(tid)
                existing_ids(japanese_id).add(tid)
                dest.append("Japanese Musics")
            for key, pid in jp_artists.items():
                if any(a.lower() == key for a in all_names) and tid not in existing_ids(pid):
                    artist_adds.setdefault(pid, []).append(tid)
                    existing_ids(pid).add(tid)
                    dest.append(playlist_name(pid))
            processed.append({"id": tid, "name": name, "artist": primary, "dest": dest})
        elif label == "western":
            dest = []
            if tid not in existing_ids(western_id):
                western_ids.append(tid)
                existing_ids(western_id).add(tid)
                dest.append("Western Musics")
            processed.append({"id": tid, "name": name, "artist": primary, "dest": dest})
        else:
            unknown_final.append(
                {
                    "id": tid,
                    "name": name,
                    "artists": all_names,
                    "isrc": (track.get("external_ids") or {}).get("isrc", ""),
                }
            )

    core.write_step_summary(
        "inbox",
        {
            "processed": len(processed),
            "japanese": sum(1 for t in liked if labels[t["id"]] == "japanese"),
            "western": sum(1 for t in liked if labels[t["id"]] == "western"),
            "unknown_count": len(unknown_final),
            "unknown": unknown_final,
        },
    )

    if dry:
        artist_total = sum(len(v) for v in artist_adds.values())
        logger.info(
            f"[DRY-RUN] 追加予定 Japanese={len(jp_ids)} Western={len(western_ids)} "
            f"アーティスト別={artist_total} / お気に入りから削除予定={len(processed)}曲"
        )
        classify.save_cache(cache)
        _write_unknowns(unknown_final, logger)
        return core.EXIT_PARTIAL if unknown_final else core.EXIT_OK

    # プレイリストへ追加（削除より先。追加成功後にお気に入りを消す）
    if jp_ids:
        core.add_in_batches(sp, japanese_id, jp_ids)
    if western_ids:
        core.add_in_batches(sp, western_id, western_ids)
    for pid, tids in artist_adds.items():
        core.add_in_batches(sp, pid, tids)

    if processed:
        ids = [t["id"] for t in processed]
        for i in range(0, len(ids), 50):
            sp.current_user_saved_tracks_delete(ids[i : i + 50])
        logger.info(f"お気に入りから{len(processed)}曲を移動しました")
        for t in processed:
            dests = " / ".join(t["dest"]) if t["dest"] else "既に振り分け済み"
            logger.info(f"    {t['name']} → {dests}")

    classify.save_cache(cache)
    _write_unknowns(unknown_final, logger)
    return core.EXIT_PARTIAL if unknown_final else core.EXIT_OK


def _write_unknowns(unknown_final: list[dict], logger) -> None:
    """判定不能曲を log/unknown_tracks.txt に書き出す。workflow がこれを Issue 化する。
    無ければ前回の残骸を消す。"""
    if not unknown_final:
        try:
            UNKNOWN_TRACKS_PATH.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        return
    logger.info(f"スキップされた曲: {len(unknown_final)}曲")
    UNKNOWN_TRACKS_PATH.parent.mkdir(exist_ok=True)
    with UNKNOWN_TRACKS_PATH.open("w", encoding="utf-8") as f:
        for t in unknown_final:
            primary = t["artists"][0] if t.get("artists") else "?"
            line = f"{t['name']} / {primary}"
            f.write(line + "\n")
            logger.info(f"    {line}")


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("inbox").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
