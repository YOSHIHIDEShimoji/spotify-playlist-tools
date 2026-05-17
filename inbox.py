#!/usr/bin/env python3
"""
Spotify Inbox Processor

お気に入りの曲を邦楽/洋楽に判定して各プレイリストへ振り分け、
処理済みの曲をお気に入りから削除する。

Usage:
  python inbox.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from google import genai
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from spotify_utils import free_redirect_port

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
INBOX_CONFIG_PATH = BASE_DIR / "inbox.txt"
CACHE_PATH = BASE_DIR / ".cache-spotify"
NOTIFIER_APP = Path.home() / "Applications/Notifiers/spotify-playlist-tools.app"

WESTERN_MUSICS_ID = "3gWeVkYJPREpkdCpDRjHFw"

SCOPE = (
    "playlist-modify-private playlist-modify-public playlist-read-private "
    "user-library-read user-library-modify"
)

JAPANESE_GENRES = {
    "j-pop", "j-rock", "j-indie", "j-rap", "j-dance", "j-metal",
    "japanese", "anime", "city pop", "visual kei", "shibuya-kei",
    "kayokyoku", "enka", "j-ambient", "j-acoustic",
}

JP_CHAR_RE = re.compile(r"[぀-鿿]")
ADD_BATCH_SIZE = 100
_name_cache: dict[str, str] = {}


def notify(title: str, message: str) -> None:
    subprocess.run(
        ["open", "-W", "-n", str(NOTIFIER_APP), "--args",
         "-title", title, "-message", message],
        check=False,
    )


def playlist_name(sp: spotipy.Spotify, pid: str) -> str:
    if pid not in _name_cache:
        _name_cache[pid] = sp.playlist(pid, fields="name")["name"]
    return _name_cache[pid]


def load_inbox_config(path: Path) -> tuple[str, dict[str, str]]:
    japanese_musics_id = ""
    artists: dict[str, str] = {}
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key == "JAPANESE_MUSICS_ID":
                japanese_musics_id = value
            else:
                artists[key.lower()] = value
    if not japanese_musics_id:
        raise RuntimeError(f"JAPANESE_MUSICS_ID が {path} に設定されていません")
    return japanese_musics_id, artists



def build_client() -> spotipy.Spotify:
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


def get_liked_tracks(sp: spotipy.Spotify) -> list[dict]:
    tracks = []
    results = sp.current_user_saved_tracks(limit=50)
    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("id"):
                tracks.append(track)
        results = sp.next(results) if results.get("next") else None
    return tracks


def get_playlist_track_ids(sp: spotipy.Spotify, playlist_id: str) -> set[str]:
    ids: set[str] = set()
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(id)),next",
        additional_types=("track",),
        limit=100,
    )
    while results:
        for item in results.get("items", []):
            tid = (item.get("track") or {}).get("id")
            if tid:
                ids.add(tid)
        results = sp.next(results) if results.get("next") else None
    return ids


def add_to_playlist(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    for i in range(0, len(track_ids), ADD_BATCH_SIZE):
        sp.playlist_add_items(playlist_id, track_ids[i : i + ADD_BATCH_SIZE])


def is_japanese_genre(genres: list[str]) -> bool:
    for g in genres:
        g_lower = g.lower()
        if any(jg in g_lower for jg in JAPANESE_GENRES):
            return True
    return False


def has_japanese_chars(text: str) -> bool:
    return bool(JP_CHAR_RE.search(text))


def classify_with_gemini(artist_name: str, track_name: str) -> str:
    """Gemini で邦楽/洋楽を判定。キー未設定や失敗時は 'unknown' を返す"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "unknown"
    client = genai.Client(api_key=api_key)
    prompt = (
        f"Is the artist \"{artist_name}\" (song: \"{track_name}\") a Japanese artist or a Western/non-Japanese artist? "
        "Reply with exactly one word: 'japanese' or 'western'."
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
        )
        result = response.text.strip().lower()
        if "japanese" in result:
            return "japanese"
        if "western" in result:
            return "western"
    except Exception as e:
        print(f"  [gemini error] {e}")
    return "unknown"


def classify(sp: spotipy.Spotify, track: dict) -> str:
    """'japanese' / 'western' / 'unknown' を返す"""
    artist = track["artists"][0]
    artist_info = sp.artist(artist["id"])
    genres = artist_info.get("genres", [])

    if genres:
        if is_japanese_genre(genres):
            return "japanese"
        return "western"

    texts = [
        artist["name"],
        track.get("name", ""),
        track.get("album", {}).get("name", ""),
    ]
    if any(has_japanese_chars(t) for t in texts):
        return "japanese"

    if "japanese version" in track.get("name", "").lower():
        return "japanese"

    return classify_with_gemini(artist["name"], track.get("name", ""))



def main() -> int:
    load_dotenv(ENV_PATH)
    japanese_musics_id, jp_artists = load_inbox_config(INBOX_CONFIG_PATH)

    sp = build_client()
    liked = get_liked_tracks(sp)

    if not liked:
        print("お気に入りに新しい曲はありません")
        return 0

    print(f"お気に入り: {len(liked)}曲を処理します")

    playlist_cache: dict[str, set[str]] = {}

    def existing_ids(pid: str) -> set[str]:
        if pid not in playlist_cache:
            playlist_cache[pid] = get_playlist_track_ids(sp, pid)
        return playlist_cache[pid]

    # 振り分け用バッファ
    jp_ids: list[str] = []
    western_ids: list[str] = []
    artist_adds: dict[str, list[str]] = {}  # playlist_id → track_ids

    # 結果記録
    processed: list[dict] = []  # {"id", "name", "artist", "dest_names"}
    unknown_tracks: list[dict] = []  # {"name", "artist"}

    for track in liked:
        tid = track["id"]
        name = track["name"]
        all_artist_names = [a["name"] for a in track["artists"]]
        primary_artist = all_artist_names[0]
        label = classify(sp, track)
        print(f"  [{label}] {name} / {primary_artist}")

        if label == "japanese":
            dest_names: list[str] = []
            if tid not in existing_ids(japanese_musics_id):
                jp_ids.append(tid)
                existing_ids(japanese_musics_id).add(tid)
                dest_names.append("Japanese Musics")
            for jp_key, jp_pid in jp_artists.items():
                if any(a.lower() == jp_key for a in all_artist_names):
                    if tid not in existing_ids(jp_pid):
                        artist_adds.setdefault(jp_pid, []).append(tid)
                        existing_ids(jp_pid).add(tid)
                        dest_names.append(playlist_name(sp, jp_pid))
            processed.append({"id": tid, "name": name, "artist": primary_artist, "dest_names": dest_names})

        elif label == "western":
            dest_names = []
            if tid not in existing_ids(WESTERN_MUSICS_ID):
                western_ids.append(tid)
                existing_ids(WESTERN_MUSICS_ID).add(tid)
                dest_names.append("Western Musics")
            processed.append({"id": tid, "name": name, "artist": primary_artist, "dest_names": dest_names})

        else:
            unknown_tracks.append({"name": name, "artist": primary_artist})

    # プレイリストへ追加
    if jp_ids:
        add_to_playlist(sp, japanese_musics_id, jp_ids)
    if western_ids:
        add_to_playlist(sp, WESTERN_MUSICS_ID, western_ids)
    for pid, tids in artist_adds.items():
        add_to_playlist(sp, pid, tids)

    # お気に入りから削除して一覧表示
    if processed:
        processed_ids = [t["id"] for t in processed]
        for i in range(0, len(processed_ids), 50):
            sp.current_user_saved_tracks_delete(processed_ids[i : i + 50])
        print(f"\nお気に入りから{len(processed)}曲を移動しました")
        for t in processed:
            dests = " / ".join(t["dest_names"]) if t["dest_names"] else "既に振り分け済み"
            print(f"    {t['name']} → {dests}")

    # スキップ一覧
    if unknown_tracks:
        print(f"\nスキップされた曲: {len(unknown_tracks)}曲")
        for t in unknown_tracks:
            print(f"    {t['name']} / {t['artist']}")

    # 通知（不明曲ありの場合のみ）
    if unknown_tracks:
        skipped = "\n".join(f"  {t['name']} / {t['artist']}" for t in unknown_tracks)
        notify(
            "Spotify Inbox: 不明曲あり",
            f"判定できなかった {len(unknown_tracks)}曲:\n{skipped}",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
