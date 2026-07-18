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

# stats タブで選択できる「完成済み」プレイリスト。管理対象（夜間更新）ではないので
# dedupe（重複検出）には入れず、統計の閲覧だけ対象にする。Western/Japanese は inbox 設定から取る。
STATS_EXTRA_PLAYLISTS = [{"id": "6sqoiZw75RIvnUFC058VJv", "name": "1900's songs"}]


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

def _stats_of(records: list[dict]) -> dict:
    """records（トラック一意）から total / artists_top / decades を出す純関数。"""
    artist_counts: Counter[str] = Counter()
    artist_id: dict[str, str] = {}  # 名前→代表 Spotify アーティスト ID（直リンク用）
    decade_counts: Counter[int] = Counter()
    for r in records:
        for a in r.get("artists") or []:
            name = a.get("name")
            if name:
                artist_counts[name] += 1
                if name not in artist_id and a.get("id"):
                    artist_id[name] = a["id"]
        rd = (r.get("album") or {}).get("release_date", "") or ""
        if len(rd) >= 4 and rd[:4].isdigit():
            decade_counts[(int(rd[:4]) // 10) * 10] += 1
    return {
        "total": len(records),  # ユニーク曲数（延べ合計ではない）
        "artists_top": [
            {"name": n, "count": c, **({"id": artist_id[n]} if n in artist_id else {})}
            for n, c in artist_counts.most_common(STATS_ARTIST_TOP)
        ],
        "decades": [{"decade": d, "count": decade_counts[d]} for d in sorted(decade_counts)],
    }


def build_stats(records: list[dict]) -> dict:
    return {"generated_at": _now_utc_iso(), **_stats_of(records)}


def _merge_records(base: list[dict], extra: list[dict]) -> list[dict]:
    """track_id で base ∪ extra を取り、playlists 在籍をマージする（統計の選択用）。"""
    by_id: dict[str, dict] = {r["id"]: dict(r) for r in base}
    for r in extra:
        tid = r["id"]
        if tid in by_id:
            seen = {p["id"] for p in by_id[tid].get("playlists", [])}
            for p in r.get("playlists", []):
                if p["id"] not in seen:
                    by_id[tid].setdefault("playlists", []).append(p)
        else:
            by_id[tid] = dict(r)
    return list(by_id.values())


def build_stats_dist(records: list[dict], selectable: list[dict]) -> dict:
    """stats タブの選択用。selectable 各プレイリスト単体＋全部合算（all）の統計を返す。
    records は selectable 全プレイリストの在籍を含む前提（_merge_records 済み）。"""
    ids = [p["id"] for p in selectable]

    def in_pl(r: dict, pid: str) -> bool:
        return any(p.get("id") == pid for p in r.get("playlists", []))

    by = {pid: _stats_of([r for r in records if in_pl(r, pid)]) for pid in ids}
    allrecs = [r for r in records if any(in_pl(r, i) for i in ids)]
    return {
        "playlists": [{"id": p["id"], "name": p["name"]} for p in selectable],
        "all": _stats_of(allrecs),
        "by": by,
    }


def _album_image(album: dict) -> str | None:
    """album.images の最小サイズ（末尾）URL を返す。サムネイル用。"""
    imgs = (album or {}).get("images") or []
    return imgs[-1].get("url") if imgs else None


def build_search_index(records: list[dict]) -> dict:
    return {
        "generated_at": _now_utc_iso(),
        "tracks": [
            {
                "id": r["id"],
                "name": r.get("name", ""),
                "artists": [a.get("name", "") for a in (r.get("artists") or [])],
                "playlists": [p["name"] for p in r.get("playlists", [])],
                "release_date": (r.get("album") or {}).get("release_date", ""),
                "image": _album_image(r.get("album") or {}),
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
        # 各ステップの内訳（サイトでステップをタップすると「どの曲がどこへ動いたか」を出す）。
        # 長くなり過ぎないよう各リストは上限を設ける。ステップが何もしていなければ空リスト。
        "detail": {
            "inbox": inbox.get("moved", [])[:200],
            "sync": sync.get("changes", [])[:100],
            "sort": sort.get("changes", [])[:100],
            "archive": archive.get("added_tracks", [])[:200],
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
                 "artists": [a["name"] for a in t.get("artists", [])], "rank": i + 1,
                 "image": _album_image(t.get("album") or {})}
                for i, t in enumerate(tr)
            ]
            ar = sp.current_user_top_artists(limit=30, time_range=term).get("items", [])
            out["artists"][term] = [{"id": a["id"], "name": a["name"], "rank": i + 1} for i, a in enumerate(ar)]
        except Exception:  # noqa: BLE001
            out["tracks"][term] = []
            out["artists"][term] = []
    return out


def select_recent_albums(albums: list[dict], cutoff: str, seen: set) -> tuple[list[dict], set]:
    """album 一覧から「cutoff 以降にリリース」を全部抽出（純関数・レビュー H4）。
    seen で抑止せず窓ベースで累積表示する。seen は is_new（新着バッジ）判定だけに使う。
    返り値: (窓内アイテム, この呼び出しで見た全 album_id)。"""
    out: list[dict] = []
    ids: set = set()
    for al in albums:
        aid = al.get("id")
        if not aid:
            continue
        ids.add(aid)
        rd = al.get("release_date", "") or ""
        if len(rd) == 10 and rd >= cutoff:
            primary = (al.get("artists") or [{}])[0]
            imgs = al.get("images") or []
            out.append(
                {
                    "album_id": aid,
                    "album_name": al.get("name", ""),
                    "album_type": al.get("album_type", ""),
                    "artist": primary.get("name", ""),
                    "artist_id": primary.get("id", ""),
                    "release_date": rd,
                    "is_new": aid not in seen,
                    "image": imgs[-1].get("url") if imgs else None,
                }
            )
    return out, ids


def _release_class_map(pl_records: list[dict]) -> dict[str, str]:
    """artist_id → 'japanese'/'western'。master プレイリスト在籍と classify cache から推定。
    新譜を邦/洋タブに振り分けるための材料（未知はサイト側で western 既定）。"""
    out: dict[str, str] = {}
    try:
        import inbox
        jp_id, western_id, _ = inbox.load_inbox_config(inbox.INBOX_CONFIG_PATH)
    except Exception:  # noqa: BLE001
        jp_id = western_id = None
    for r in pl_records:
        pids = {p.get("id") for p in r.get("playlists", [])}
        cls = "japanese" if jp_id in pids else ("western" if western_id in pids else None)
        if not cls:
            continue
        for a in r.get("artists") or []:
            if a.get("id"):
                out.setdefault(a["id"], cls)
    try:  # classify cache（手動/自動で確定した邦/洋）を上書き（最優先）
        import classify
        for aid, info in classify.load_cache().items():
            if info.get("class") in ("japanese", "western"):
                out[aid] = info["class"]
    except Exception:  # noqa: BLE001
        pass
    return out


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
    by_album: dict[str, dict] = {}
    all_seen = set(seen)
    for aid in artist_ids:
        try:
            albums = sp.artist_albums(aid, album_type="album,single", limit=10).get("items", [])
        except Exception:  # noqa: BLE001
            continue
        window, ids = select_recent_albums(albums, cutoff, seen)
        for it in window:
            by_album.setdefault(it["album_id"], it)  # album 重複排除
        all_seen |= ids

    core.atomic_write_json(seen_path, {"album_ids": sorted(all_seen)})
    class_map = _release_class_map(pl_records)
    items = sorted(by_album.values(), key=lambda x: x["release_date"], reverse=True)
    for it in items:  # 邦/洋の振り分け。未知アーティストは western 既定（サイトのタブ分け用）。
        it["class"] = class_map.get(it.get("artist_id") or "", "western")
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

    # 静的サイトはディレクトリ列挙できないので、undo と wrapped のインデックスを出す（H5・M3）
    write_undo_index(data)
    _write_wrapped_index(data)

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
    keep_sets = dedupe.load_keep_sets(data)  # 「両方残す」をスキャンから除外（H2）
    core.atomic_write_json(data / "dupes.json", dedupe.dupes_from_records(pl_records, intra, keep_sets))
    # 「保留（両方残す）」タブ用。site-ops が書く dedupe_keep.json を無ければ空で用意（サイトが 404 しない）。
    if not (data / "dedupe_keep.json").exists():
        core.atomic_write_json(data / "dedupe_keep.json", {"groups": []})
    # stats: 管理ライブラリの top-level（Growth 用）＋ 選択式の per-playlist（Western/Japanese/1900's）
    stats_json = build_stats(pl_records)
    # 検索インデックスは既定で管理PLのみ。1900's が読めたら merged に差し替え（統計と齟齬を作らない）。
    search_records = pl_records
    try:
        import inbox
        jp, western, _ = inbox.load_inbox_config(inbox.INBOX_CONFIG_PATH)
        extra_records, _intra = dedupe.collect_records(sp, STATS_EXTRA_PLAYLISTS)  # 読み取り専用
        selectable = [
            {"id": western, "name": "Western Musics"},
            {"id": jp, "name": "Japanese Musics"},
            *STATS_EXTRA_PLAYLISTS,
        ]
        merged = _merge_records(pl_records, extra_records)
        stats_json["dist"] = build_stats_dist(merged, selectable)
        search_records = merged  # 統計に出る 1900's の曲を検索でも見つけられるようにする
    except Exception as e:  # noqa: BLE001 — 追加PLが読めなくても top-level 統計は出す
        logger.info(f"stats dist スキップ: {e}")
    core.atomic_write_json(data / "stats.json", stats_json)
    core.atomic_write_json(data / "search_index.json", build_search_index(search_records))
    # プレイリスト別の延べ数に加え、ユニーク曲数の番兵行を残す（サイトの成長チャートはこれを描く。
    # 延べ合計はアーティスト別 PL とマスターの重複で二重計上になるため成長指標に使わない）。
    history_rows = playlist_count_rows(pl_records, playlists, date_str)
    history_rows.append(
        {"date": date_str, "playlist_id": "__library__", "name": "ライブラリ（ユニーク）", "count": len(pl_records)}
    )
    _append_stats_history(data / "stats_history.jsonl", history_rows)

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


def write_undo_index(data: Path, keep_days: int = 30) -> None:
    """data/undo/*.json（未 .done）を集約。サイトの undo 一覧＆取り消しに使う（H5）。
    siteops からも op 直後に呼ぶ（H-1）。直近 keep_days 日ぶんだけ載せる（L-3）。"""
    import json

    cutoff = (datetime.now(core.JST) - timedelta(days=keep_days)).isoformat()
    undo_dir = data / "undo"
    entries: list[dict] = []
    if undo_dir.exists():
        for p in undo_dir.glob("*.json"):  # .done は列挙しない（取り消し済み）
            try:
                rec = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if (rec.get("created_at") or "") < cutoff:
                continue  # 古すぎる undo は隠す（誤って昔の削除を復活させない）
            entries.append({
                "id": rec.get("id"),
                "op": rec.get("op"),
                "created_at": rec.get("created_at"),
                "count": len(rec.get("removed", [])) or len(rec.get("moved", [])),
                "tracks": [r.get("name", "") for r in rec.get("removed", [])][:5],
            })
    entries.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    core.atomic_write_json(data / "undo_index.json", {"generated_at": _now_utc_iso(), "entries": entries})


def _write_wrapped_index(data: Path) -> None:
    """data/wrapped/YYYY-MM.json の存在月一覧。サイトの Wrapped 表示に使う（M3）。"""
    wrapped_dir = data / "wrapped"
    months: list[str] = []
    if wrapped_dir.exists():
        months = sorted((p.stem for p in wrapped_dir.glob("*.json") if p.stem != "index"), reverse=True)
    core.atomic_write_json(data / "wrapped" / "index.json", {"months": months})


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
        # 握り潰して nightly は止めないが、失敗を GitHub アノテーションで可視化する（レビュー M1）。
        # ::error:: は行頭に出す必要があるので logger ではなく print で直接出す。
        print(f"::error::sitegen が失敗しました（部分データの可能性）: {e}", flush=True)
        core.setup_logging("sitegen").info(f"[error] {e}")
        return core.EXIT_OK


if __name__ == "__main__":
    sys.exit(_entry())
