#!/usr/bin/env python3
"""
Spotify Artist Playlist Syncer

sync.txt に設定したソースプレイリストを走査し、
各アーティストの曲をそれぞれのプレイリストへ追加する（重複なし）。
AUTO_DETECT_THRESHOLD 曲以上持つ未設定アーティストは自動検出し、
プレイリストを新規作成して設定ファイルに追記する。
新規作成したプレイリストは sort.txt にも追記し、自動ソート対象に加える。

Usage:
  python sync.py
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from spotify_utils import free_redirect_port

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "sync.txt"
SORT_CONFIG_PATH = BASE_DIR / "sort.txt"
CACHE_PATH = BASE_DIR / ".cache-spotify"

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"

ADD_BATCH_SIZE = 100
REMOVE_BATCH_SIZE = 100
AUTO_DETECT_THRESHOLD = 20
STATE_PATH = BASE_DIR / "sync_state.json"


def load_config(path: Path) -> tuple[str, dict[str, str]]:
    source_id = ""
    artists: dict[str, str] = {}  # {artist_name_lower: playlist_id}
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key == "SOURCE_PLAYLIST_ID":
                source_id = value
            else:
                artists[key.lower()] = value
    if not source_id:
        raise RuntimeError(f"SOURCE_PLAYLIST_ID が {path} に設定されていません")
    return source_id, artists


def build_spotify_client() -> spotipy.Spotify:
    for key in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"):
        if not os.getenv(key):
            raise RuntimeError(f"{key} が .env に設定されていません")
    free_redirect_port()
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=SCOPE,
            cache_path=str(CACHE_PATH),
            open_browser=True,
        )
    )


def get_all_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[dict]:
    tracks: list[dict] = []
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(id,name,artists(name))),next",
        additional_types=("track",),
        limit=100,
    )
    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                tracks.append(track)
        results = sp.next(results) if results.get("next") else None
    return tracks


def get_dest_track_ids(sp: spotipy.Spotify, playlist_id: str) -> set[str]:
    track_ids: set[str] = set()
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(id)),next",
        additional_types=("track",),
        limit=100,
    )
    while results:
        for item in results.get("items", []):
            track = item.get("track") or {}
            tid = track.get("id")
            if tid:
                track_ids.add(tid)
        results = sp.next(results) if results.get("next") else None
    return track_ids


def count_artists(tracks: list[dict]) -> dict[str, tuple[int, str]]:
    """Returns {artist_name_lower: (count, spotify_name)}"""
    counts: Counter[str] = Counter()
    spotify_names: dict[str, str] = {}
    for track in tracks:
        for artist in track.get("artists", []):
            name = artist.get("name", "")
            if not name:
                continue
            lower = name.lower()
            counts[lower] += 1
            if lower not in spotify_names:
                spotify_names[lower] = name
    return {lower: (counts[lower], spotify_names[lower]) for lower in counts}


def create_artist_playlist(sp: spotipy.Spotify, artist_name: str) -> str:
    user_id = sp.me()["id"]
    playlist = sp.user_playlist_create(user_id, artist_name, public=True)
    return playlist["id"]


def append_artist_to_config(path: Path, artist_name: str, playlist_id: str) -> None:
    with path.open("a") as f:
        f.write(f"{artist_name}={playlist_id}\n")


def append_to_sort_list(path: Path, playlist_url: str) -> None:
    with path.open("a") as f:
        f.write(f"{playlist_url}\n")


def match_tracks_for_artist(tracks: list[dict], artist_lower: str) -> tuple[list[str], str]:
    matched: list[str] = []
    spotify_name = ""
    for track in tracks:
        for artist in track.get("artists", []):
            name = artist.get("name", "")
            if name.lower() == artist_lower:
                if not spotify_name:
                    spotify_name = name
                matched.append(track["id"])
                break
    return matched, spotify_name


def add_new_tracks(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    for i in range(0, len(track_ids), ADD_BATCH_SIZE):
        sp.playlist_add_items(playlist_id, track_ids[i : i + ADD_BATCH_SIZE])


def remove_tracks_from_playlist(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    uris = [f"spotify:track:{tid}" for tid in track_ids]
    for i in range(0, len(uris), REMOVE_BATCH_SIZE):
        sp.playlist_remove_all_occurrences_of_items(playlist_id, uris[i : i + REMOVE_BATCH_SIZE])


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
            f"{AUTO_DETECT_THRESHOLD}曲以上持つ未設定アーティストを自動検出し、"
            "プレイリストを新規作成して同期する。\n"
            f"設定ファイル: {CONFIG_PATH}\n"
            f"ソート対象: {SORT_CONFIG_PATH}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args()

    load_dotenv(ENV_PATH)
    source_id, artists = load_config(CONFIG_PATH)
    sp = build_spotify_client()

    source_tracks = get_all_tracks(sp, source_id)
    today = date.today().isoformat()
    prev_state = load_sync_state(STATE_PATH)
    is_first_run = not prev_state
    new_state: dict[str, set[str]] = {}

    # 自動検出: threshold以上のアーティストを処理
    artist_counts = count_artists(source_tracks)
    for artist_lower, (count, spotify_name) in sorted(
        artist_counts.items(), key=lambda x: -x[1][0]
    ):
        if count < AUTO_DETECT_THRESHOLD:
            continue
        if artist_lower in artists:
            print(f"[auto] {spotify_name}: {count} tracks (already configured)", flush=True)
        else:
            playlist_id = create_artist_playlist(sp, spotify_name)
            append_artist_to_config(CONFIG_PATH, spotify_name, playlist_id)
            append_to_sort_list(SORT_CONFIG_PATH, f"https://open.spotify.com/playlist/{playlist_id}")
            artists[artist_lower] = playlist_id
            print(f"[auto] {spotify_name}: {count} tracks → created playlist {playlist_id}", flush=True)

    # 同期
    for artist_name_lower, dest_id in artists.items():
        current_ap_ids = get_dest_track_ids(sp, dest_id)

        # 逆方向: AP から削除された曲を Western Musics からも削除
        if not is_first_run and dest_id in prev_state:
            deleted_from_ap = prev_state[dest_id] - current_ap_ids
            if deleted_from_ap:
                remove_tracks_from_playlist(sp, source_id, list(deleted_from_ap))
                source_tracks = [t for t in source_tracks if t["id"] not in deleted_from_ap]
                print(f"[{today}] {artist_name_lower}: removed {len(deleted_from_ap)} from Western Musics", flush=True)

        # 順方向: Western Musics の新規曲を AP へ追加
        candidates, spotify_name = match_tracks_for_artist(source_tracks, artist_name_lower)
        to_add = [tid for tid in candidates if tid not in current_ap_ids]
        if to_add:
            add_new_tracks(sp, dest_id, to_add)
            current_ap_ids.update(to_add)

        skipped = len(candidates) - len(to_add)
        display_name = spotify_name or artist_name_lower
        print(f"[{today}] {display_name}: added {len(to_add)} (skipped {skipped})", flush=True)

        new_state[dest_id] = current_ap_ids

    save_sync_state(STATE_PATH, new_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
