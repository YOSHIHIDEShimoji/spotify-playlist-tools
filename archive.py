#!/usr/bin/env python3
"""Spotify Top 50 Archiver

SOURCE_PLAYLIST_ID（例: Top 50 - Global）から現在の曲を取得し、
DEST_PLAYLIST_ID にまだ入っていない曲だけを追加する。
毎日実行することで「過去に Top 50 入りしたことがある全曲」が DEST に蓄積されていく。

Usage:
  python archive.py [--dry-run]
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import core

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "archive.txt"

SCOPE = "playlist-modify-private playlist-modify-public playlist-read-private"


def load_config(path: Path) -> dict[str, str]:
    cfg = core.parse_config(path)
    for key in ("SOURCE_PLAYLIST_ID", "DEST_PLAYLIST_ID"):
        if not cfg.get(key):
            raise RuntimeError(f"{key} が {path} に設定されていません")
    return cfg


def get_track_ids(sp, playlist_id: str) -> list[str]:
    """順序を保ったままページング取得（bugs §6: 50曲固定を撤廃）。"""
    return [t["id"] for t in core.iter_playlist_tracks(sp, playlist_id, "items(track(id)),next")]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Spotify Top 50 アーカイバ。\n"
            "SOURCE の現在の曲を取得し、DEST に未追加の曲だけを追加する。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="変更系 API を呼ばず件数のみ表示")
    parser.parse_args()

    logger = core.setup_logging("archive")
    dry = core.is_dry_run()
    cfg = load_config(CONFIG_PATH)
    sp = core.build_client(SCOPE)

    source_id = core.extract_playlist_id(cfg["SOURCE_PLAYLIST_ID"])
    dest_id = core.extract_playlist_id(cfg["DEST_PLAYLIST_ID"])

    existing = set(get_track_ids(sp, dest_id))
    source = get_track_ids(sp, source_id)

    seen: set[str] = set()
    to_add = [t for t in source if t not in existing and not (t in seen or seen.add(t))]
    skipped = len(source) - len(to_add)
    today = date.today().isoformat()
    added_tracks = _resolve_track_names(sp, to_add)  # サイトのステップ内訳用（追加曲名）

    if dry:
        logger.info(f"[{today}][DRY-RUN] would add {len(to_add)} (skipped {skipped})")
        core.write_step_summary(
            "archive", {"added": len(to_add), "skipped": skipped, "added_tracks": added_tracks})
        return core.EXIT_OK

    if to_add:
        core.add_in_batches(sp, dest_id, to_add)
    logger.info(f"[{today}] added {len(to_add)} (skipped {skipped})")
    core.write_step_summary(
        "archive", {"added": len(to_add), "skipped": skipped, "added_tracks": added_tracks})
    return core.EXIT_OK


def _resolve_track_names(sp, track_ids: list[str]) -> list[dict]:
    """track_id を {name, artists} へ解決する（50件ずつ）。失敗しても集計は続ける。"""
    out: list[dict] = []
    for i in range(0, len(track_ids), 50):
        try:
            res = sp.tracks(track_ids[i:i + 50]).get("tracks", [])
        except Exception:  # noqa: BLE001 — 名前解決失敗は無視（件数は別途出る）
            continue
        for t in res:
            if t:
                out.append({"name": t.get("name", ""),
                            "artists": [a["name"] for a in t.get("artists", [])]})
    return out


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("archive").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
