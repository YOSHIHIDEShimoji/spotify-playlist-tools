#!/usr/bin/env python3
"""sitegen.py — ダッシュボード用データ生成（nightly 後段）

管理プレイリストの横断読取・聴取ログ集計・公式 Top/新譜取得・実行サマリ集約を行い、
<data-dir>/*.json 一式を生成する（dashboard-design §5.3, §6.2）。

- プレイリスト読取は1回（dedupe.collect_records を共有）で dupes / stats / search を作る
- 聴取ログ集計（週間 Top・累計・ヒートマップ・streak・wrapped）は純関数
- 新スコープ依存（recently-played / top / follow）は 403 を graceful skip し
  auth_status.json に missing_scopes を記録する

読み取り専用。プレイリストを一切変更しない。

Usage:
  python sitegen.py --data-dir <dir>
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core
import dedupe

STATS_ARTIST_TOP = 30
CUMULATIVE_TOP = 100
WEEKLY_TOP = 50
MILESTONES = [100, 250, 500, 1000, 2500, 5000, 10000]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── 聴取ログ集計（純関数） ───────────────────────────

def _track_meta(records: list[dict]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for r in records:
        tid = r["track_id"]
        if tid not in meta:
            meta[tid] = {
                "name": r.get("name", ""),
                "artists": [a.get("name", "") for a in (r.get("artists") or [])],
            }
    return meta


def cumulative_ranking(records: list[dict], limit: int = CUMULATIVE_TOP) -> list[dict]:
    counts: Counter[str] = Counter(r["track_id"] for r in records)
    meta = _track_meta(records)
    return [
        {"track_id": tid, "name": meta[tid]["name"], "artists": meta[tid]["artists"], "count": c}
        for tid, c in counts.most_common(limit)
    ]


def weekly_ranking(records: list[dict], now_jst: datetime, limit: int = WEEKLY_TOP) -> list[dict]:
    wk = now_jst.isocalendar()[:2]
    week_recs = [r for r in records if core.to_jst(r["played_at"]).isocalendar()[:2] == wk]
    return cumulative_ranking(week_recs, limit)


def heatmap_cells(records: list[dict]) -> list[dict]:
    cells: Counter[tuple] = Counter()
    for r in records:
        d = core.to_jst(r["played_at"])
        cells[(d.weekday(), d.hour)] += 1  # weekday(): 月=0
    return [{"dow": dow, "hour": h, "count": c} for (dow, h), c in sorted(cells.items())]


def current_streak(records: list[dict], today) -> int:
    """今日（無ければ昨日）から連続で再生のある日数。today は JST の date。"""
    days = {core.to_jst(r["played_at"]).date() for r in records}
    if not days:
        return 0
    d = today if today in days else today - timedelta(days=1)
    streak = 0
    while d in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def milestone_progress(total_plays: int) -> dict:
    reached = [m for m in MILESTONES if total_plays >= m]
    nxt = next((m for m in MILESTONES if total_plays < m), None)
    return {"total": total_plays, "reached": reached, "next": nxt}


def monthly_wrapped(records: list[dict], month: str, new_tracks: int = 0) -> dict:
    """month: 'YYYY-MM'（JST）。その月の再生から Top 曲・アーティスト・ピーク時間帯を出す。"""
    recs = [r for r in records if core.to_jst(r["played_at"]).strftime("%Y-%m") == month]
    artist_counts: Counter[str] = Counter()
    for r in recs:
        for a in r.get("artists") or []:
            if a.get("name"):
                artist_counts[a["name"]] += 1
    cells = Counter((core.to_jst(r["played_at"]).weekday(), core.to_jst(r["played_at"]).hour) for r in recs)
    peak = max(cells, key=cells.get) if cells else None
    return {
        "month": month,
        "plays": len(recs),
        "top_tracks": cumulative_ranking(recs, 10),
        "top_artists": [{"name": n, "count": c} for n, c in artist_counts.most_common(10)],
        "new_tracks": new_tracks,
        "peak": {"dow": peak[0], "hour": peak[1]} if peak else None,
    }


# ─────────────────────── プレイリスト由来（純関数・records から） ───────────────────────

def build_stats(records: list[dict]) -> dict:
    artist_counts: Counter[str] = Counter()
    decade_counts: Counter[int] = Counter()
    for r in records:
        for a in r.get("artists") or []:
            if a.get("name"):
                artist_counts[a["name"]] += 1
        rd = (r.get("album") or {}).get("release_date", "") or ""
        if len(rd) >= 4 and rd[:4].isdigit():
            decade_counts[(int(rd[:4]) // 10) * 10] += 1
    return {
        "generated_at": _now_utc_iso(),
        "artists_top": [{"name": n, "count": c} for n, c in artist_counts.most_common(STATS_ARTIST_TOP)],
        "decades": [{"decade": d, "count": decade_counts[d]} for d in sorted(decade_counts)],
    }


def build_search_index(records: list[dict]) -> dict:
    return {
        "generated_at": _now_utc_iso(),
        "tracks": [
            {
                "id": r["id"],
                "name": r.get("name", ""),
                "artists": [a.get("name", "") for a in (r.get("artists") or [])],
                "playlists": [p["name"] for p in r.get("playlists", [])],
            }
            for r in records
        ],
    }


def playlist_count_rows(records: list[dict], playlists: list[dict], date_str: str) -> list[dict]:
    cnt: Counter[str] = Counter()
    for r in records:
        for p in r.get("playlists", []):
            cnt[p["id"]] += 1
    return [
        {"date": date_str, "playlist_id": pl["id"], "name": pl["name"], "count": cnt.get(pl["id"], 0)}
        for pl in playlists
    ]


# ─────────────────────────── 実行サマリ（純関数） ───────────────────────────

def build_run_record(summaries: dict, run_id, date_str: str, dry_run: bool) -> dict:
    inbox = summaries.get("inbox", {})
    sync = summaries.get("sync", {})
    sort = summaries.get("sort", {})
    archive = summaries.get("archive", {})
    present = [k for k in ("inbox", "sync", "sort", "archive") if k in summaries]
    return {
        "date": date_str,
        "run_id": run_id,
        "status": "success" if len(present) == 4 else "partial",
        "dry_run": dry_run,
        "steps": {
            "inbox": {
                "processed": inbox.get("processed", 0),
                "japanese": inbox.get("japanese", 0),
                "western": inbox.get("western", 0),
                "unknown": inbox.get("unknown_count", 0),
            },
            "sync": {
                "added": sync.get("added", 0),
                "removed": sync.get("removed", 0),
                "new_playlists": sync.get("new_playlists", 0),
            },
            "sort": {"playlists": sort.get("playlists", 0), "skipped": sort.get("skipped", 0)},
            "archive": {"added": archive.get("added", 0)},
        },
        "generated_at": _now_utc_iso(),
    }


# ─────────────────────────── API 依存部（graceful） ───────────────────────────

def probe_scopes(sp) -> list[str]:
    """新スコープの有無を limit=1 の実 API で確認する。未付与は 403 になるため、
    その間だけ spotipy のエラーログを抑制して nightly ログを汚さない。"""
    import logging

    missing: list[str] = []
    probes = {
        "user-read-recently-played": lambda: sp.current_user_recently_played(limit=1),
        "user-top-read": lambda: sp.current_user_top_artists(limit=1),
        "user-follow-read": lambda: sp.current_user_followed_artists(limit=1),
    }
    sp_logger = logging.getLogger("spotipy.client")
    prev = sp_logger.level
    sp_logger.setLevel(logging.CRITICAL)
    try:
        for scope, fn in probes.items():
            try:
                fn()
            except Exception:  # noqa: BLE001 — スコープ有無の実測
                missing.append(scope)
    finally:
        sp_logger.setLevel(prev)
    return missing


def build_top(sp) -> dict:
    out = {"generated_at": _now_utc_iso(), "tracks": {}, "artists": {}}
    for term in ("short_term", "medium_term", "long_term"):
        try:
            tr = sp.current_user_top_tracks(limit=30, time_range=term).get("items", [])
            out["tracks"][term] = [
                {"id": t["id"], "name": t["name"],
                 "artists": [a["name"] for a in t.get("artists", [])], "rank": i + 1}
                for i, t in enumerate(tr)
            ]
            ar = sp.current_user_top_artists(limit=30, time_range=term).get("items", [])
            out["artists"][term] = [{"id": a["id"], "name": a["name"], "rank": i + 1} for i, a in enumerate(ar)]
        except Exception:  # noqa: BLE001
            out["tracks"][term] = []
            out["artists"][term] = []
    return out


def select_recent_albums(albums: list[dict], cutoff: str, seen: set) -> tuple[list[dict], set]:
    """album 一覧から「cutoff 以降にリリース・未 seen」を抽出（純関数）。
    返り値: (新規アイテム, この呼び出しで見た全 album_id)。"""
    fresh: list[dict] = []
    ids: set = set()
    for al in albums:
        aid = al.get("id")
        if not aid:
            continue
        ids.add(aid)
        rd = al.get("release_date", "") or ""
        if len(rd) == 10 and rd >= cutoff and aid not in seen:
            fresh.append(
                {
                    "album_id": aid,
                    "album_name": al.get("name", ""),
                    "album_type": al.get("album_type", ""),
                    "artist": (al.get("artists") or [{}])[0].get("name", ""),
                    "release_date": rd,
                }
            )
    return fresh, ids


def build_releases(sp, pl_records: list[dict], data: Path, now_jst: datetime, within_days: int = 14) -> dict:
    """フォロー中＋在籍アーティストの新譜（直近 within_days 日）を集める。
    既読は releases_seen.json で管理。user-follow-read 前提（呼び出し側でガード）。"""
    import json

    seen_path = data / "releases_seen.json"
    seen: set = set()
    if seen_path.exists():
        try:
            seen = set(json.loads(seen_path.read_text()).get("album_ids", []))
        except (json.JSONDecodeError, OSError):
            seen = set()

    artist_ids: set = set()
    after = None
    while True:
        arts = sp.current_user_followed_artists(limit=50, after=after).get("artists", {})
        for a in arts.get("items", []):
            artist_ids.add(a["id"])
        after = arts.get("cursors", {}).get("after")
        if not after or not arts.get("items"):
            break
    for r in pl_records:
        for a in r.get("artists") or []:
            if a.get("id"):
                artist_ids.add(a["id"])

    cutoff = (now_jst - timedelta(days=within_days)).date().isoformat()
    items: list[dict] = []
    all_seen = set(seen)
    for aid in artist_ids:
        try:
            albums = sp.artist_albums(aid, album_type="album,single", limit=10).get("items", [])
        except Exception:  # noqa: BLE001
            continue
        fresh, ids = select_recent_albums(albums, cutoff, seen)
        items.extend(fresh)
        all_seen |= ids

    core.atomic_write_json(seen_path, {"album_ids": sorted(all_seen)})
    items.sort(key=lambda x: x["release_date"], reverse=True)
    return {"generated_at": _now_utc_iso(), "items": items}


def _empty_top() -> dict:
    empty = {t: [] for t in ("short_term", "medium_term", "long_term")}
    return {"generated_at": _now_utc_iso(), "tracks": dict(empty), "artists": dict(empty)}


def build_archive_weekly(sp, dest_id: str) -> dict:
    weeks: dict[str, list[dict]] = {}
    results = sp.playlist_items(
        dest_id, fields="items(added_at,track(id,name,artists(name))),next",
        additional_types=("track",), limit=100,
    )
    while results:
        for item in results.get("items", []):
            track = item.get("track") or {}
            added_at = item.get("added_at")
            if not track.get("id") or not added_at:
                continue
            iso = core.to_jst(added_at).isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            weeks.setdefault(key, []).append(
                {"id": track["id"], "name": track.get("name", ""),
                 "artists": [a["name"] for a in track.get("artists", [])], "added_at": added_at}
            )
        results = sp.next(results) if results.get("next") else None
    return {
        "generated_at": _now_utc_iso(),
        "weeks": [{"iso_week": k, "tracks": v} for k, v in sorted(weeks.items())],
    }


# ─────────────────────────── オーケストレーション ───────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="ダッシュボード用データ生成")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()

    logger = core.setup_logging("sitegen")
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    now_jst = datetime.now(core.JST)
    date_str = now_jst.date().isoformat()
    dry_run = core.is_dry_run()

    # 1) 実行サマリ集約 → runs.jsonl（トークン不要）
    summaries = core.read_step_summaries()
    run_id = int(os.getenv("GITHUB_RUN_ID", "0")) or None
    _append_run(data / "runs.jsonl", build_run_record(summaries, run_id, date_str, dry_run))

    # 2) unknown.json（inbox サマリ由来・トークン不要）
    core.atomic_write_json(
        data / "unknown.json",
        {"generated_at": _now_utc_iso(), "tracks": summaries.get("inbox", {}).get("unknown", [])},
    )

    # 3) 聴取ログ集計 → weekly/cumulative/heatmap/streak（トークン不要・ファイルのみ）
    records = _load_all_listening(data / "listening")
    core.atomic_write_json(data / "listening_stats.json", {
        "generated_at": _now_utc_iso(),
        "since": min((r["played_at"] for r in records), default=None),
        "weekly_top": weekly_ranking(records, now_jst),
        "cumulative_top": cumulative_ranking(records),
        "streak": current_streak(records, now_jst.date()),
        "milestone": milestone_progress(len(records)),
    })
    core.atomic_write_json(data / "heatmap.json", {"generated_at": _now_utc_iso(), "cells": heatmap_cells(records)})

    # 月末なら wrapped
    if (now_jst + timedelta(days=1)).month != now_jst.month:
        month = now_jst.strftime("%Y-%m")
        new_tracks = _month_new_tracks(data / "runs.jsonl", month)
        core.atomic_write_json(data / "wrapped" / f"{month}.json", monthly_wrapped(records, month, new_tracks))

    # 4) プレイリスト読取が要る部分（トークン必要・失効なら auth_status だけ書いて終了）
    #    使うのは playlist-read のみ。top/follow/recently は probe_scopes で個別に graceful 判定する。
    try:
        sp = core.build_client("playlist-read-private")
    except core.AuthRequired:
        core.atomic_write_json(data / "auth_status.json",
                               {"token_ok": False, "checked_at": _now_utc_iso(), "missing_scopes": []})
        logger.info("トークン失効。auth_status のみ更新して終了。")
        return core.EXIT_OK

    missing = probe_scopes(sp)
    core.atomic_write_json(data / "auth_status.json",
                           {"token_ok": True, "checked_at": _now_utc_iso(), "missing_scopes": missing})

    playlists = dedupe.managed_playlists()
    pl_records, intra = dedupe.collect_records(sp, playlists)
    core.atomic_write_json(data / "dupes.json", dedupe.dupes_from_records(pl_records, intra))
    core.atomic_write_json(data / "stats.json", build_stats(pl_records))
    core.atomic_write_json(data / "search_index.json", build_search_index(pl_records))
    _append_stats_history(data / "stats_history.jsonl", playlist_count_rows(pl_records, playlists, date_str))

    # top / releases は新スコープ依存。probe で欠落が分かっていれば呼ばず空ファイルを置く
    # （403 ログのノイズと無駄な API を避ける・未再認証でもサイトにファイルは揃える）。
    core.atomic_write_json(
        data / "top.json", build_top(sp) if "user-top-read" not in missing else _empty_top()
    )
    core.atomic_write_json(
        data / "releases.json",
        build_releases(sp, pl_records, data, now_jst)
        if "user-follow-read" not in missing
        else {"generated_at": _now_utc_iso(), "items": []},
    )

    # archive_weekly（DEST を added_at で週集計）
    try:
        import archive
        cfg = archive.load_config(archive.CONFIG_PATH)
        dest = core.extract_playlist_id(cfg["DEST_PLAYLIST_ID"])
        core.atomic_write_json(data / "archive_weekly.json", build_archive_weekly(sp, dest))
    except Exception as e:  # noqa: BLE001
        logger.info(f"archive_weekly スキップ: {e}")

    c = summaries.get("inbox", {})
    logger.info(
        f"データ生成完了: dupes={len(pl_records)}曲 unknown={len(c.get('unknown', []))} "
        f"listening={len(records)}再生 missing_scopes={missing}"
    )
    return core.EXIT_OK


def _append_run(path: Path, record: dict) -> None:
    existing = core.read_jsonl(path)
    if record["run_id"] and any(r.get("run_id") == record["run_id"] for r in existing):
        return
    core.append_jsonl(path, [record])


def _append_stats_history(path: Path, rows: list[dict]) -> None:
    existing = core.read_jsonl(path)
    seen = {(r.get("date"), r.get("playlist_id")) for r in existing}
    fresh = [r for r in rows if (r["date"], r["playlist_id"]) not in seen]
    if fresh:
        core.append_jsonl(path, fresh)


def _load_all_listening(listening_dir: Path) -> list[dict]:
    records: list[dict] = []
    if listening_dir.exists():
        for p in sorted(listening_dir.glob("*.jsonl")):
            records.extend(core.read_jsonl(p))
    return records


def _month_new_tracks(runs_path: Path, month: str) -> int:
    total = 0
    for r in core.read_jsonl(runs_path):
        if str(r.get("date", "")).startswith(month):
            steps = r.get("steps", {}).get("inbox", {})
            total += steps.get("japanese", 0) + steps.get("western", 0)
    return total


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("sitegen").info(f"[auth] {e}")
        return core.EXIT_OK  # データ生成は本処理を止めない
    except Exception as e:  # noqa: BLE001
        core.setup_logging("sitegen").info(f"[error] {e}")
        return core.EXIT_OK


if __name__ == "__main__":
    sys.exit(_entry())
