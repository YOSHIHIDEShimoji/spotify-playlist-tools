#!/usr/bin/env python3
"""Spotify Artist Playlist Syncer

sync.txt に設定したソースプレイリストを走査し、
各アーティストの曲をそれぞれのプレイリストへ追加する（重複なし）。
AUTO_DETECT_THRESHOLD 曲以上持つ未設定アーティストは自動検出し、
プレイリストを新規作成して設定ファイル（sync.txt / sort.txt）に追記する。

双方向同期: アーティストプレイリストから曲を削除すると、次回実行時に
ソース（Western Musics）からも削除される。前回スナップショットは sync_state.json に保持。

Usage:
  python sync.py [--dry-run]
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import core

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "sync.txt"
SORT_CONFIG_PATH = BASE_DIR / "sort.txt"
STATE_PATH = BASE_DIR / "sync_state.json"

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"
AUTO_DETECT_THRESHOLD = 20


def load_config(path: Path) -> tuple[str, dict[str, str]]:
    cfg = core.parse_config(path)
    source_id = cfg.pop("SOURCE_PLAYLIST_ID", "")
    if not source_id:
        raise RuntimeError(f"SOURCE_PLAYLIST_ID が {path} に設定されていません")
    artists = {k.lower(): core.extract_playlist_id(v) for k, v in cfg.items()}
    return core.extract_playlist_id(source_id), artists


def get_all_tracks(sp, playlist_id: str) -> list[dict]:
    return list(
        core.iter_playlist_tracks(sp, playlist_id, "items(track(id,name,artists(name))),next")
    )


def get_dest_track_ids(sp, playlist_id: str) -> set[str]:
    return {t["id"] for t in core.iter_playlist_tracks(sp, playlist_id, "items(track(id)),next")}


def count_artists(tracks: list[dict]) -> dict[str, tuple[int, str]]:
    counts: Counter[str] = Counter()
    spotify_names: dict[str, str] = {}
    for track in tracks:
        for artist in track.get("artists", []):
            name = artist.get("name", "")
            if not name:
                continue
            lower = name.lower()
            counts[lower] += 1
            spotify_names.setdefault(lower, name)
    return {lower: (counts[lower], spotify_names[lower]) for lower in counts}


def create_artist_playlist(sp, artist_name: str) -> str:
    user_id = sp.me()["id"]
    # 既存の手動作成プレイリストの公開設定に合わせて public=True を維持している。
    # 変更する場合は既存 AP の公開状態を確認してから揃えること（要判断・bugs §10）。
    playlist = sp.user_playlist_create(user_id, artist_name, public=True)
    return playlist["id"]


def match_tracks_for_artist(tracks: list[dict], artist_lower: str) -> tuple[list[str], str]:
    matched: list[str] = []
    spotify_name = ""
    for track in tracks:
        for artist in track.get("artists", []):
            if artist.get("name", "").lower() == artist_lower:
                spotify_name = spotify_name or artist["name"]
                matched.append(track["id"])
                break
    return matched, spotify_name


def load_sync_state(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    with path.open() as f:
        raw: dict[str, list[str]] = json.load(f)
    return {pid: set(ids) for pid, ids in raw.items()}


def save_sync_state(path: Path, state: dict[str, set[str]]) -> None:
    with path.open("w") as f:
        json.dump({pid: sorted(ids) for pid, ids in state.items()}, f, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Spotify アーティスト別プレイリスト同期ツール。\n"
            f"{AUTO_DETECT_THRESHOLD}曲以上持つ未設定アーティストを自動検出し同期する。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="変更系 API を呼ばず予定のみ表示")
    parser.parse_args()

    logger = core.setup_logging("sync")
    dry = core.is_dry_run()
    source_id, artists = load_config(CONFIG_PATH)
    sp = core.build_client(SCOPE)

    source_tracks = get_all_tracks(sp, source_id)
    today = date.today().isoformat()
    prev_state = load_sync_state(STATE_PATH)
    is_first_run = not prev_state
    new_state: dict[str, set[str]] = {}

    total_added = 0
    total_removed = 0
    total_new_playlists = 0
    # サイトのステップ内訳用: どのアーティストPLに何曲追加/削除したか。名前は元曲から引く。
    changes: list[dict] = []
    src_names = {t["id"]: t.get("name", "") for t in source_tracks}

    # 自動検出: threshold 以上の未設定アーティストにプレイリストを作る
    artist_counts = count_artists(source_tracks)
    for artist_lower, (count, spotify_name) in sorted(artist_counts.items(), key=lambda x: -x[1][0]):
        if count < AUTO_DETECT_THRESHOLD or artist_lower in artists:
            if count >= AUTO_DETECT_THRESHOLD:
                logger.info(f"[auto] {spotify_name}: {count} tracks (already configured)")
            continue
        if dry:
            logger.info(f"[auto][DRY-RUN] {spotify_name}: {count} tracks → プレイリスト新規作成予定")
            continue
        playlist_id = create_artist_playlist(sp, spotify_name)
        core.append_line(CONFIG_PATH, f"{spotify_name}={playlist_id}")
        core.append_line(SORT_CONFIG_PATH, f"https://open.spotify.com/playlist/{playlist_id}")
        artists[artist_lower] = playlist_id
        total_new_playlists += 1
        logger.info(f"[auto] {spotify_name}: {count} tracks → created playlist {playlist_id}")

    # 同期
    for artist_lower, dest_id in artists.items():
        current_ap_ids = get_dest_track_ids(sp, dest_id)
        removed_here = 0

        # 逆方向: AP から削除された曲をソースからも削除
        if not is_first_run and dest_id in prev_state:
            deleted = prev_state[dest_id] - current_ap_ids
            if deleted:
                if dry:
                    logger.info(f"[{today}][DRY-RUN] {artist_lower}: ソースから {len(deleted)} 削除予定")
                else:
                    core.remove_in_batches(sp, source_id, list(deleted))
                    source_tracks = [t for t in source_tracks if t["id"] not in deleted]
                    logger.info(f"[{today}] {artist_lower}: removed {len(deleted)} from source")
                total_removed += len(deleted)
                removed_here = len(deleted)

        # 順方向: ソースの新規曲を AP へ追加
        candidates, spotify_name = match_tracks_for_artist(source_tracks, artist_lower)
        to_add = [tid for tid in candidates if tid not in current_ap_ids]
        if to_add and not dry:
            core.add_in_batches(sp, dest_id, to_add)
            current_ap_ids.update(to_add)
        total_added += len(to_add)

        skipped = len(candidates) - len(to_add)
        verb = "would add" if dry else "added"
        logger.info(f"[{today}] {spotify_name or artist_lower}: {verb} {len(to_add)} (skipped {skipped})")
        new_state[dest_id] = current_ap_ids
        if to_add or removed_here:
            changes.append({
                "playlist": spotify_name or artist_lower,
                "added": [src_names.get(tid, "") for tid in to_add],
                "removed": removed_here,
            })

    if dry:
        logger.info("[DRY-RUN] sync_state.json は更新しません")
    else:
        save_sync_state(STATE_PATH, new_state)

    core.write_step_summary(
        "sync",
        {"added": total_added, "removed": total_removed, "new_playlists": total_new_playlists,
         "changes": changes},
    )
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("sync").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
