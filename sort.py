#!/usr/bin/env python3
"""
Spotify Playlist Sorter / Analyzer

Usage:
  python sort_playlist.py <URL or ID>            # ソート（上書き）
  python sort_playlist.py --analyze <URL or ID>  # 分析グラフを表示（変更なし）
"""

import os
import re
import sys
import argparse
from collections import Counter
from pathlib import Path

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from spotify_utils import free_redirect_port

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CACHE_PATH = BASE_DIR / ".cache-spotify"

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"


def extract_playlist_id(url_or_id: str) -> str:
    m = re.search(r"playlist/([A-Za-z0-9]+)", url_or_id)
    return m.group(1) if m else url_or_id


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
        fields="items(track(id,name,popularity,artists(name),album(release_date))),next",
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


def _normalize_date(date_str: str) -> str:
    parts = date_str.split("-")
    if len(parts) == 1:
        return f"{parts[0]}-01-01"
    if len(parts) == 2:
        return f"{parts[0]}-{parts[1]}-01"
    return date_str


def _artist_names(track: dict) -> list[str]:
    return [a["name"] for a in track.get("artists", []) if a.get("name")]


def sort_tracks(tracks: list[dict]) -> list[dict]:
    artist_count: Counter[str] = Counter()
    for t in tracks:
        for name in _artist_names(t):
            artist_count[name] += 1

    def key(t: dict) -> tuple[int, str, str]:
        names = _artist_names(t)
        # 複数アーティストの場合はカウントが最大のアーティストで代表
        primary = max(names, key=lambda n: artist_count[n]) if names else ""
        date = _normalize_date(t.get("album", {}).get("release_date", "0000"))
        return (-artist_count[primary], primary.lower(), date)

    return sorted(tracks, key=key)


def replace_playlist(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    sp.playlist_replace_items(playlist_id, track_ids[:100])
    for i in range(100, len(track_ids), 100):
        sp.playlist_add_items(playlist_id, track_ids[i : i + 100])


def analyze(tracks: list[dict], playlist_name: str) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    # --- データ集計 ---
    artist_count: Counter[str] = Counter()
    for t in tracks:
        for name in _artist_names(t) or ["Unknown"]:
            artist_count[name] += 1
    years = [
        int(t["album"]["release_date"][:4])
        for t in tracks
        if t.get("album", {}).get("release_date")
    ]
    popularities = [t.get("popularity", 0) for t in tracks]
    top10 = sorted(
        [t for t in tracks if t.get("popularity") is not None],
        key=lambda t: t["popularity"],
        reverse=True,
    )[:10]

    top15_artists = artist_count.most_common(15)
    top15_names = [a for a, _ in reversed(top15_artists)]
    top15_counts = [c for _, c in reversed(top15_artists)]

    # --- レイアウト ---
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(
        f"{playlist_name}  ({len(tracks)} tracks)",
        fontsize=15,
        fontweight="bold",
        y=0.98,
    )

    # [0,0] アーティスト別曲数 Top15
    ax1 = fig.add_subplot(2, 2, 1)
    bars = ax1.barh(top15_names, top15_counts, color="#1DB954")
    ax1.set_title("Top 15 Artists by Track Count", fontweight="bold")
    ax1.set_xlabel("Tracks")
    ax1.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    for bar, cnt in zip(bars, top15_counts):
        ax1.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                 str(cnt), va="center", fontsize=8)

    # [0,1] リリース年分布
    ax2 = fig.add_subplot(2, 2, 2)
    if years:
        min_y, max_y = min(years), max(years)
        bins = range(min_y, max_y + 2)
        ax2.hist(years, bins=bins, color="#1DB954", edgecolor="white", linewidth=0.4)
    ax2.set_title("Release Year Distribution", fontweight="bold")
    ax2.set_xlabel("Year")
    ax2.set_ylabel("Tracks")
    ax2.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

    # [1,0] 人気スコア分布
    ax3 = fig.add_subplot(2, 2, 3)
    ax3.hist(popularities, bins=range(0, 111, 10), color="#1DB954",
             edgecolor="white", linewidth=0.4)
    ax3.set_title("Popularity Score Distribution", fontweight="bold")
    ax3.set_xlabel("Popularity (0–100)")
    ax3.set_ylabel("Tracks")
    ax3.xaxis.set_major_locator(ticker.MultipleLocator(10))

    # [1,1] 人気スコア Top10 テキスト表
    ax4 = fig.add_subplot(2, 2, 4)
    ax4.axis("off")
    ax4.set_title("Top 10 Popular Tracks", fontweight="bold")
    rows = []
    for i, t in enumerate(top10, 1):
        artist = t["artists"][0]["name"] if t.get("artists") else "?"
        name = t["name"]
        if len(name) > 28:
            name = name[:27] + "…"
        rows.append([f"{i}.", f"{t['popularity']}", f"{artist[:18]}", name])
    table = ax4.table(
        cellText=rows,
        colLabels=["#", "Pop", "Artist", "Track"],
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1, 1.35)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#cccccc")
        if r == 0:
            cell.set_facecolor("#1DB954")
            cell.set_text_props(color="white", fontweight="bold")
        elif r % 2 == 0:
            cell.set_facecolor("#f5f5f5")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()


def main() -> int:
    load_dotenv(ENV_PATH)

    parser = argparse.ArgumentParser(
        description="Spotify プレイリストのソート／分析ツール"
    )
    parser.add_argument("playlist", help="プレイリストの URL または ID")
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="分析グラフを表示（プレイリストは変更しない）",
    )
    args = parser.parse_args()

    playlist_id = extract_playlist_id(args.playlist)
    sp = build_spotify_client()

    print(f"取得中: {playlist_id}")
    tracks = get_all_tracks(sp, playlist_id)
    print(f"取得完了: {len(tracks)} 曲")

    if args.analyze:
        playlist_name = sp.playlist(playlist_id, fields="name")["name"]
        analyze(tracks, playlist_name)
        return 0

    sorted_tracks = sort_tracks(tracks)
    sorted_ids = [t["id"] for t in sorted_tracks]
    replace_playlist(sp, playlist_id, sorted_ids)
    print(f"更新完了: {len(sorted_ids)} 曲をソートしました\n")

    for i, t in enumerate(sorted_tracks[:10], 1):
        artist = t["artists"][0]["name"] if t.get("artists") else "Unknown"
        date = t.get("album", {}).get("release_date", "?")
        print(f"  {i:>3}. {artist} — {t['name']} ({date})")
    if len(sorted_tracks) > 10:
        print(f"       ... 他 {len(sorted_tracks) - 10} 曲")

    return 0


if __name__ == "__main__":
    sys.exit(main())
