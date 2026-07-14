#!/usr/bin/env python3
"""Spotify Playlist Sorter / Analyzer

プレイリストを アーティスト曲数降順 → アーティスト名順 → リリース日昇順 で並べ替える。

Usage:
  python sort.py <URL or ID>            # 単体ソート（上書き）
  python sort.py --all                  # sort.txt の全プレイリストを直列ソート
  python sort.py --analyze <URL or ID>  # 分析グラフを表示（変更なし・ローカル専用）
  python sort.py --all --dry-run        # 変更せず対象と件数のみ表示
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import core

BASE_DIR = Path(__file__).resolve().parent
SORT_CONFIG_PATH = BASE_DIR / "sort.txt"

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"


def get_all_tracks(sp, playlist_id: str) -> list[dict]:
    return list(
        core.iter_playlist_tracks(
            sp, playlist_id,
            "items(track(id,name,popularity,artists(name),album(release_date))),next",
        )
    )


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
        primary = max(names, key=lambda n: artist_count[n]) if names else ""
        release = _normalize_date(t.get("album", {}).get("release_date", "0000"))
        return (-artist_count[primary], primary.lower(), release)

    return sorted(tracks, key=key)


def replace_playlist(sp, playlist_id: str, track_ids: list[str]) -> None:
    sp.playlist_replace_items(playlist_id, track_ids[:100])
    for i in range(100, len(track_ids), 100):
        sp.playlist_add_items(playlist_id, track_ids[i : i + 100])


def sort_one(sp, url_or_id: str, logger, dry: bool) -> None:
    playlist_id = core.extract_playlist_id(url_or_id)
    # 全置換の競合ガード（bugs §1）: 取得時と置換直前で snapshot_id が変われば見送る
    snapshot_before = sp.playlist(playlist_id, fields="snapshot_id")["snapshot_id"]
    tracks = get_all_tracks(sp, playlist_id)
    sorted_tracks = sort_tracks(tracks)
    sorted_ids = [t["id"] for t in sorted_tracks]

    if dry:
        logger.info(f"[DRY-RUN] {playlist_id}: {len(sorted_ids)} 曲をソート予定（置換なし）")
        return

    snapshot_now = sp.playlist(playlist_id, fields="snapshot_id")["snapshot_id"]
    if snapshot_now != snapshot_before:
        logger.info(f"[skip] {playlist_id}: 取得中に変更を検出（次回再ソート）")
        return

    replace_playlist(sp, playlist_id, sorted_ids)
    logger.info(f"更新完了: {len(sorted_ids)} 曲をソートしました ({playlist_id})")


def load_sort_targets(path: Path) -> list[str]:
    targets: list[str] = []
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    return targets


def analyze(tracks: list[dict], playlist_name: str) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

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

    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(f"{playlist_name}  ({len(tracks)} tracks)", fontsize=15, fontweight="bold", y=0.98)

    ax1 = fig.add_subplot(2, 2, 1)
    bars = ax1.barh(top15_names, top15_counts, color="#1DB954")
    ax1.set_title("Top 15 Artists by Track Count", fontweight="bold")
    ax1.set_xlabel("Tracks")
    ax1.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    for bar, cnt in zip(bars, top15_counts):
        ax1.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2, str(cnt),
                 va="center", fontsize=8)

    ax2 = fig.add_subplot(2, 2, 2)
    if years:
        bins = range(min(years), max(years) + 2)
        ax2.hist(years, bins=bins, color="#1DB954", edgecolor="white", linewidth=0.4)
    ax2.set_title("Release Year Distribution", fontweight="bold")
    ax2.set_xlabel("Year")
    ax2.set_ylabel("Tracks")
    ax2.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

    ax3 = fig.add_subplot(2, 2, 3)
    ax3.hist(popularities, bins=range(0, 111, 10), color="#1DB954", edgecolor="white", linewidth=0.4)
    ax3.set_title("Popularity Score Distribution", fontweight="bold")
    ax3.set_xlabel("Popularity (0–100)")
    ax3.set_ylabel("Tracks")
    ax3.xaxis.set_major_locator(ticker.MultipleLocator(10))

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
    table = ax4.table(cellText=rows, colLabels=["#", "Pop", "Artist", "Track"],
                      loc="center", cellLoc="left")
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
    parser = argparse.ArgumentParser(description="Spotify プレイリストのソート／分析ツール")
    parser.add_argument("playlist", nargs="?", help="プレイリストの URL または ID")
    parser.add_argument("--all", action="store_true", help="sort.txt の全プレイリストをソート")
    parser.add_argument("--analyze", action="store_true", help="分析グラフを表示（変更しない）")
    parser.add_argument("--dry-run", action="store_true", help="変更せず件数のみ表示")
    args = parser.parse_args()

    logger = core.setup_logging("sort")
    dry = core.is_dry_run()
    sp = core.build_client(SCOPE)

    if args.analyze:
        if not args.playlist:
            parser.error("--analyze はプレイリスト URL/ID が必要です")
        playlist_id = core.extract_playlist_id(args.playlist)
        tracks = get_all_tracks(sp, playlist_id)
        analyze(tracks, sp.playlist(playlist_id, fields="name")["name"])
        return core.EXIT_OK

    if args.all:
        targets = load_sort_targets(SORT_CONFIG_PATH)
        logger.info(f"ソート対象: {len(targets)} プレイリスト" + (" [DRY-RUN]" if dry else ""))
        for url in targets:
            try:
                sort_one(sp, url, logger, dry)
            except Exception as e:
                logger.info(f"[error] {url}: {e}")
        return core.EXIT_OK

    if not args.playlist:
        parser.error("プレイリスト URL/ID か --all を指定してください")
    sort_one(sp, args.playlist, logger, dry)
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("sort").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
