#!/usr/bin/env python3
"""
Spotify Top 50 Archiver

SOURCE_PLAYLIST_ID（例: Top 50 - Global）から現在の50曲を取得し、
DEST_PLAYLIST_ID にまだ入っていない曲だけを追加する。
毎日実行することで「過去に Top 50 入りしたことがある全曲」が DEST に蓄積されていく。
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from spotify_utils import free_redirect_port

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "archive.txt"
CACHE_PATH = BASE_DIR / ".cache-spotify"

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"

ADD_BATCH_SIZE = 100


def load_config(path: Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip()
    for key in ("SOURCE_PLAYLIST_ID", "DEST_PLAYLIST_ID"):
        if not cfg.get(key):
            raise RuntimeError(f"{key} が {path} に設定されていません")
    return cfg


def build_spotify_client() -> spotipy.Spotify:
    for key in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"):
        if not os.getenv(key):
            raise RuntimeError(f"{key} が環境変数（.env）に設定されていません")
    free_redirect_port()
    auth_manager = SpotifyOAuth(
        scope=SCOPE,
        cache_path=str(CACHE_PATH),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


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
        if results.get("next"):
            results = sp.next(results)
        else:
            results = None
    return track_ids


def get_source_track_ids(sp: spotipy.Spotify, playlist_id: str) -> list[str]:
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(id))",
        additional_types=("track",),
        limit=50,
    )
    ids: list[str] = []
    for item in results.get("items", []):
        track = item.get("track") or {}
        tid = track.get("id")
        if tid:
            ids.append(tid)
    return ids


def add_new_tracks(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    for i in range(0, len(track_ids), ADD_BATCH_SIZE):
        batch = track_ids[i : i + ADD_BATCH_SIZE]
        sp.playlist_add_items(playlist_id, batch)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Spotify Top 50 アーカイバ。\n"
            "SOURCE_PLAYLIST_ID の現在の曲を取得し、DEST_PLAYLIST_ID に未追加の曲だけを追加する。\n"
            f"設定ファイル: {CONFIG_PATH}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args()

    load_dotenv(ENV_PATH)
    cfg = load_config(CONFIG_PATH)
    sp = build_spotify_client()

    source_id = cfg["SOURCE_PLAYLIST_ID"]
    dest_id = cfg["DEST_PLAYLIST_ID"]

    existing = get_dest_track_ids(sp, dest_id)
    top50 = get_source_track_ids(sp, source_id)

    to_add = [tid for tid in top50 if tid not in existing]
    skipped = len(top50) - len(to_add)

    if to_add:
        add_new_tracks(sp, dest_id, to_add)

    today = date.today().isoformat()
    print(f"[{today}] added {len(to_add)} (skipped {skipped})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
