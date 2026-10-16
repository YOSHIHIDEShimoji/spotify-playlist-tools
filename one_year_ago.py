#!/usr/bin/env python3
"""one_year_ago.py — 「1年前の今日」プレイリストの日替わり更新

1年前の同じ日（日本時間）に再生した曲を、聴いた順・重複なしで集め、プレイリストの中身を
丸ごと入れ替える。その日に1曲も聴いていなければ、前後でいちばん近い「聴いた日」で代用し、
どの日の曲かを説明欄に書く（名前どおりでない日があることを隠さない）。

再生記録は sitegen と同じ土台（拡張ストリーミング履歴＋Last.fm scrobble＋自前ログ）を使う。
Last.fm 由来で Spotify に解決できなかった曲（"lastfm:..." の合成 ID）は追加できないので除く。

起動は Vercel Cron → workflow_dispatch（0時台）が主、GitHub の定期実行が保険。同じ日に
何回動いても中身は同じになるので、二重に動いても害はない。

Usage:
  python one_year_ago.py --data-dir <dir> [--dry-run]
"""

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import core

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "one_year_ago.txt"

SCOPE = "playlist-modify-private playlist-read-private"
SEARCH_WINDOW_DAYS = 30  # 聴いていない日の代用を探す範囲（前後それぞれ）


def load_config(path: Path) -> str:
    pid = core.parse_config(path).get("PLAYLIST_ID", "")
    if not pid:
        raise RuntimeError(f"PLAYLIST_ID が {path} に設定されていません")
    return core.extract_playlist_id(pid)


def one_year_ago(today: date) -> date:
    """1年前の同じ日。2/29 は前の年に存在しないので 2/28 にする。"""
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def plays_by_day(records: list[dict]) -> dict[date, list[dict]]:
    """再生を日本時間の日付ごとに、時刻順で並べる。Spotify に無い曲（lastfm: 合成 ID）は除く。

    履歴ファイルや scrobble の読み込み順は保証されないので、ここで必ず時刻で並べ直す。
    """
    out: dict[date, list[tuple]] = defaultdict(list)
    for r in records:
        tid, pa = r.get("track_id"), r.get("played_at")
        if not tid or not pa or tid.startswith("lastfm:"):
            continue
        t = core.parse_iso(pa)
        out[t.astimezone(core.JST).date()].append((t, tid))
    return {d: [{"track_id": tid, "played_at": t} for t, tid in sorted(v)] for d, v in out.items()}


def tracks_for_day(plays: list[dict]) -> list[str]:
    """その日の再生から、聴いた順・重複なしの曲 ID を返す（初めて聴いた時点の位置に置く）。"""
    seen: set[str] = set()
    out: list[str] = []
    for p in plays:
        if p["track_id"] not in seen:
            seen.add(p["track_id"])
            out.append(p["track_id"])
    return out


def pick_day(by_day: dict[date, list], target: date, window: int = SEARCH_WINDOW_DAYS) -> date | None:
    """target に再生があればその日。無ければ前後でいちばん近い再生のある日（同じ距離なら前の日）。

    window 日以内に1日も無ければ None。
    """
    for dist in range(window + 1):
        for cand in ((target,) if dist == 0 else (target - timedelta(days=dist), target + timedelta(days=dist))):
            if by_day.get(cand):
                return cand
    return None


def _ja(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def describe(target: date, used: date | None, count: int) -> str:
    """プレイリストの説明欄。どの日の曲かを必ず書く（代用した日はそうと分かるように）。"""
    tail = "毎日0時ごろ入れ替わります。"
    if used is None:
        return f"{_ja(target)}（1年前の今日）の前後{SEARCH_WINDOW_DAYS}日は再生記録がありません。{tail}"
    if used == target:
        return f"{_ja(target)}に聴いた{count}曲（聴いた順）。{tail}"
    return (
        f"{_ja(target)}（1年前の今日）は聴いていなかったので、いちばん近い{_ja(used)}に"
        f"聴いた{count}曲（聴いた順）。{tail}"
    )


def build(records: list[dict], today: date) -> dict:
    """今日のプレイリストの中身を決める（純関数）。"""
    target = one_year_ago(today)
    by_day = plays_by_day(records)
    used = pick_day(by_day, target)
    track_ids = tracks_for_day(by_day[used]) if used else []
    return {
        "target": target,
        "used": used,
        "track_ids": track_ids,
        "description": describe(target, used, len(track_ids)),
    }


def apply(sp, playlist_id: str, track_ids: list[str], description: str) -> None:
    """プレイリストの中身を丸ごと入れ替え、説明欄を更新する。

    入れ替え（PUT）は100件までなので、先頭100件で置き換えてから残りを追加する。空なら空にする。
    置き換えは何度やっても同じ結果になる（冪等）ので一過性エラーは再試行する。追加は二重登録に
    なりうるので再試行しない（途中で落ちても翌日の置き換えで元に戻る）。
    """
    head, rest = track_ids[:100], track_ids[100:]
    core.retry_api(lambda: sp.playlist_replace_items(playlist_id, head), what="playlist_replace")
    if rest:
        core.add_in_batches(sp, playlist_id, rest)
    core.retry_api(
        lambda: sp.playlist_change_details(playlist_id, description=description), what="playlist_details"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="「1年前の今日」プレイリストを日替わりで更新する")
    parser.add_argument("--data-dir", required=True, help="data ブランチの data/（再生記録の場所）")
    parser.add_argument("--dry-run", action="store_true", help="プレイリストを変更せず予定だけ表示")
    args = parser.parse_args()

    import sitegen  # 再生記録の読み込みは sitegen と同じ処理を使う（合算ロジックを二重に持たない）

    logger = core.setup_logging("one_year_ago")
    playlist_id = load_config(CONFIG_PATH)
    records = sitegen._listening_records(Path(args.data_dir))
    plan = build(records, datetime.now(core.JST).date())
    logger.info(
        f"対象 {plan['target']} → 使う日 {plan['used']} / {len(plan['track_ids'])}曲 / {plan['description']}"
    )
    if core.is_dry_run():
        logger.info("[DRY-RUN] プレイリストは変更しません")
        return core.EXIT_OK

    sp = core.build_client(SCOPE)
    apply(sp, playlist_id, plan["track_ids"], plan["description"])
    logger.info("プレイリストを更新しました")
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("one_year_ago").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
