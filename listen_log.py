#!/usr/bin/env python3
"""listen_log.py — 聴取ログ収集（3時間ごと）

Spotify の「最近再生した曲」（直近50件）を取得し、月別 JSONL に追記する。
API は履歴を50件しか返さないため、3時間おきに回して取りこぼしを防ぐ
（dashboard-design §6.1）。累計・週間 Top・ヒートマップの原本になる。

カーソル（前回取得済みの最大 played_at）を <data-dir>/listening/.cursor に持ち、
after=cursor で差分だけ取る。新規0件なら何も書かない。

スコープ user-read-recently-played が未付与（再認証前）なら、静かに exit 0 する
（graceful skip・dashboard-design §6.4）。夜間の inbox が本物の失効は検知するので二重報告しない。

Usage:
  python listen_log.py --data-dir <dir>   # <dir>/listening/*.jsonl に追記
"""

import argparse
import sys
from pathlib import Path

import core

SCOPE = "user-read-recently-played"


def _played_ms(iso: str) -> int:
    return int(core.parse_iso(iso).timestamp() * 1000)


def poll(sp, cursor: int | None) -> tuple[list[dict], int]:
    """recently-played を取得してレコード列と新カーソル（最大 played_at ms）を返す。"""
    kwargs: dict = {"limit": 50}
    if cursor:
        kwargs["after"] = cursor
    resp = sp.current_user_recently_played(**kwargs)
    records: list[dict] = []
    max_ms = cursor or 0
    for item in resp.get("items", []):
        played_at = item.get("played_at")
        track = item.get("track") or {}
        tid = track.get("id")
        if not played_at or not tid:
            continue
        records.append(
            {
                "played_at": played_at,
                "track_id": tid,
                "name": track.get("name", ""),
                "artists": [
                    {"id": a.get("id"), "name": a.get("name", "")}
                    for a in (track.get("artists") or [])
                ],
                "duration_ms": track.get("duration_ms"),
            }
        )
        max_ms = max(max_ms, _played_ms(played_at))
    return records, max_ms


def _month_key(played_at: str) -> str:
    return core.to_jst(played_at).strftime("%Y-%m")


def append_records(listening_dir: Path, records: list[dict]) -> int:
    """既存の played_at と重複しないレコードを JST 月別ファイルへ追記。追記件数を返す。"""
    by_month: dict[str, list[dict]] = {}
    for rec in records:
        by_month.setdefault(_month_key(rec["played_at"]), []).append(rec)

    written = 0
    for month, recs in by_month.items():
        path = listening_dir / f"{month}.jsonl"
        existing = {r.get("played_at") for r in core.read_jsonl(path)}
        fresh = [r for r in recs if r["played_at"] not in existing]
        if fresh:
            fresh.sort(key=lambda r: r["played_at"])
            core.append_jsonl(path, fresh)
            written += len(fresh)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="聴取ログ収集（recently-played → JSONL）")
    parser.add_argument("--data-dir", required=True, help="data ディレクトリ（listening/ を作る）")
    args = parser.parse_args()

    logger = core.setup_logging("listen_log")
    listening_dir = Path(args.data_dir) / "listening"
    listening_dir.mkdir(parents=True, exist_ok=True)
    cursor_path = listening_dir / ".cursor"
    cursor = int(cursor_path.read_text().strip()) if cursor_path.exists() else None

    sp = core.build_client(SCOPE)
    records, new_cursor = poll(sp, cursor)
    written = append_records(listening_dir, records)
    if written:
        cursor_path.write_text(str(new_cursor))
        logger.info(f"聴取ログ: {written}件を追記しました")
    else:
        logger.info("新規の再生はありません")
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired:
        # スコープ未付与 or トークン失効。データ収集は静かに諦める（nightly が失効は別途検知）。
        core.setup_logging("listen_log").info(
            "recently-played を取得できません（再認証で有効化）。スキップします。"
        )
        return core.EXIT_OK
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "403" in msg or "scope" in msg or "forbidden" in msg:
            core.setup_logging("listen_log").info(f"スコープ不足のためスキップ: {e}")
            return core.EXIT_OK
        raise


if __name__ == "__main__":
    sys.exit(_entry())
