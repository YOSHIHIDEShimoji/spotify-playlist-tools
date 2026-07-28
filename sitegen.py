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
import gzip
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import core
import dedupe

STATS_ARTIST_TOP = 30
CUMULATIVE_TOP = 100
WEEKLY_TOP = 50
WRAPPED_TOP = 20
MILESTONES = [100, 250, 500, 1000, 2500, 5000, 10000]

# 「忘れられた名曲」の条件: 生涯 REDISCOVER_MIN_PLAYS 回以上聴いたのに、直近
# REDISCOVER_QUIET_DAYS 日は一度も再生していない曲。生涯データがあって初めて成立する。
REDISCOVER_MIN_PLAYS = 10
REDISCOVER_QUIET_DAYS = 365
REDISCOVER_LIMIT = 60

# stats タブで選択できる「完成済み」プレイリスト。管理対象（夜間更新）ではないので
# dedupe（重複検出）には入れず、統計の閲覧だけ対象にする。Western/Japanese は inbox 設定から取る。
STATS_EXTRA_PLAYLISTS = [{"id": "6sqoiZw75RIvnUFC058VJv", "name": "1900's songs"}]


def _now_utc_iso() -> str:
    return core.now_utc_iso()


# ─────────────────────────── 聴取ログ集計（純関数） ───────────────────────────

def _track_meta(records: list[dict]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for r in records:
        tid = r["track_id"]
        if tid not in meta:
            meta[tid] = {
                "name": r.get("name", ""),
                "artists": [a.get("name", "") for a in (r.get("artists") or [])],
                "image": r.get("image"),  # Last.fm 由来の scrobble はアート URL を持つ
            }
    return meta


def cumulative_ranking(records: list[dict], limit: int = CUMULATIVE_TOP) -> list[dict]:
    counts: Counter[str] = Counter(r["track_id"] for r in records)
    meta = _track_meta(records)
    out: list[dict] = []
    for tid, c in counts.most_common(limit):
        row = {"track_id": tid, "name": meta[tid]["name"], "artists": meta[tid]["artists"], "count": c}
        if meta[tid].get("image"):  # 画像は持っているときだけ載せる（自前ログ由来は付かない）
            row["image"] = meta[tid]["image"]
        out.append(row)
    return out


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
        "top_tracks": cumulative_ranking(recs, WRAPPED_TOP),
        "top_artists": [{"name": n, "count": c} for n, c in artist_counts.most_common(WRAPPED_TOP)],
        "new_tracks": new_tracks,
        "peak": {"dow": peak[0], "hour": peak[1]} if peak else None,
    }


# ─────────────────────────── 生涯集計（純関数） ───────────────────────────
#
# 拡張ストリーミング履歴（2019〜）を土台にした「生涯」の集計。ランキングを上位N件で切らず
# 全件出すのが要点で、これがサイト側の逆引き（この曲は生涯何回・何位か）の材料になる。
# rank は配列の並び順そのもの（count 降順）なので JSON には持たせない。

def _play_ms(r: dict) -> int:
    """その再生の再生時間(ms)。拡張履歴だけが持ち、live ログ/scrobble には無いので既定 0。"""
    ms = r.get("ms")
    return ms if isinstance(ms, int) and ms > 0 else 0


def _artist_names(r: dict) -> list[str]:
    return [a.get("name", "") for a in (r.get("artists") or []) if a.get("name")]


def _accumulate(bucket: dict, r: dict, jst: datetime) -> None:
    """1再生ぶんを集計バケットへ足す（曲・アーティスト共通）。first/last は JST の日付。"""
    day = jst.date().isoformat()
    bucket["count"] += 1
    bucket["ms"] += _play_ms(r)
    bucket["years"][jst.strftime("%Y")] = bucket["years"].get(jst.strftime("%Y"), 0) + 1
    if not bucket["first"] or day < bucket["first"]:
        bucket["first"] = day
    if not bucket["last"] or day > bucket["last"]:
        bucket["last"] = day


def _new_bucket() -> dict:
    return {"count": 0, "ms": 0, "years": {}, "first": "", "last": ""}


def lifetime_tracks(records: list[dict], short_plays: dict[str, int] | None = None) -> list[dict]:
    """全曲の生涯集計を count 降順（同数は曲名昇順）で返す。上位N件で切らない。

    short_plays（import_history の extra.json 由来）があれば、その曲を途中でやめた回数を
    `short` として載せる。サイト側は count/(count+short) を完走率として出す。
    """
    short_plays = short_plays or {}
    buckets: dict[str, dict] = {}
    meta = _track_meta(records)
    for r in records:
        tid, pa = r.get("track_id"), r.get("played_at")
        if not tid or not pa:
            continue
        _accumulate(buckets.setdefault(tid, _new_bucket()), r, core.to_jst(pa))
    out = []
    for tid, b in buckets.items():
        row = {
            "id": tid,
            "name": meta[tid]["name"],
            "artists": meta[tid]["artists"],
            "count": b["count"],
            "ms": b["ms"],
            "first": b["first"],
            "last": b["last"],
            "years": b["years"],
        }
        if meta[tid].get("image"):
            row["image"] = meta[tid]["image"]
        if short_plays.get(tid):
            row["short"] = short_plays[tid]
        out.append(row)
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def lifetime_artists(records: list[dict], artist_ids: dict[str, str] | None = None) -> list[dict]:
    """全アーティストの生涯集計を count 降順（同数は名前昇順）で返す。

    1再生は、その曲に credit された全アーティストにそれぞれ1回として数える（monthly_wrapped と
    同じ数え方）。拡張履歴はアルバムアーティスト1名しか持たないため、履歴由来の再生は代表1名に
    付く。artist_ids は 名前(小文字) → Spotify アーティストID の対応（画像・直リンク用）。
    """
    artist_ids = artist_ids or {}
    buckets: dict[str, dict] = {}
    tracks: dict[str, set] = {}
    display: dict[str, str] = {}
    for r in records:
        pa = r.get("played_at")
        if not pa:
            continue
        jst = core.to_jst(pa)
        for name in _artist_names(r):
            key = name.lower()
            display.setdefault(key, name)
            _accumulate(buckets.setdefault(key, _new_bucket()), r, jst)
            if r.get("track_id"):
                tracks.setdefault(key, set()).add(r["track_id"])
    out = []
    for key, b in buckets.items():
        row = {
            "name": display[key],
            "count": b["count"],
            "tracks": len(tracks.get(key, ())),
            "ms": b["ms"],
            "first": b["first"],
            "last": b["last"],
            "years": b["years"],
        }
        if artist_ids.get(key):
            row["id"] = artist_ids[key]
        out.append(row)
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def lifetime_totals(records: list[dict], tracks: list[dict], artists: list[dict]) -> dict:
    """生涯の総量。plays / 曲数 / アーティスト数 / 総再生時間(ms) / 起点 / 聴いた日数。"""
    days = {core.to_jst(r["played_at"]).date().isoformat() for r in records if r.get("played_at")}
    return {
        "plays": len(records),
        "tracks": len(tracks),
        "artists": len(artists),
        "ms": sum(t["ms"] for t in tracks),
        "since": min(days) if days else None,
        "days": len(days),
    }


def yearly_wrapped(records: list[dict], year: str, new_tracks: int = 0) -> dict:
    """year: 'YYYY'（JST）。その年の Top 曲・アーティスト・月別再生・総再生時間を出す。

    月間 wrapped と同じ形（month キーの代わりに year）＋ ms / months を足したもの。
    サイトは同じコンポーネントで月/年どちらも描ける。
    """
    recs = [r for r in records if r.get("played_at") and core.to_jst(r["played_at"]).strftime("%Y") == year]
    artist_counts: Counter[str] = Counter()
    months: Counter[str] = Counter()
    cells: Counter[tuple] = Counter()
    for r in recs:
        jst = core.to_jst(r["played_at"])
        months[jst.strftime("%Y-%m")] += 1
        cells[(jst.weekday(), jst.hour)] += 1
        for name in _artist_names(r):
            artist_counts[name] += 1
    peak = max(cells, key=cells.get) if cells else None
    return {
        "year": year,
        "plays": len(recs),
        "ms": sum(_play_ms(r) for r in recs),
        "top_tracks": cumulative_ranking(recs, WRAPPED_TOP),
        "top_artists": [{"name": n, "count": c} for n, c in artist_counts.most_common(WRAPPED_TOP)],
        "new_tracks": new_tracks,
        "peak": {"dow": peak[0], "hour": peak[1]} if peak else None,
        "months": [{"month": m, "count": months[m]} for m in sorted(months)],
    }


def rediscover(
    tracks: list[dict],
    now_jst: datetime,
    min_plays: int = REDISCOVER_MIN_PLAYS,
    quiet_days: int = REDISCOVER_QUIET_DAYS,
    limit: int = REDISCOVER_LIMIT,
) -> list[dict]:
    """「忘れられた名曲」= よく聴いたのに最近ぱったり聴いていない曲。

    tracks は lifetime_tracks の出力。last（最終再生日・JST）が quiet_days 日より前で、
    生涯 min_plays 回以上のものを再生回数の多い順に返す。
    """
    cutoff = (now_jst - timedelta(days=quiet_days)).date().isoformat()
    hits = [t for t in tracks if t["count"] >= min_plays and t["last"] and t["last"] < cutoff]
    hits.sort(key=lambda t: (-t["count"], t["name"]))
    return hits[:limit]


def on_this_day(records: list[dict], now_jst: datetime) -> list[dict]:
    """今日と同じ月日（JST）に、過去の年で何を聴いていたか。新しい年から順に返す。"""
    md = now_jst.strftime("%m-%d")
    this_year = now_jst.strftime("%Y")
    by_year: dict[str, list[dict]] = {}
    for r in records:
        pa = r.get("played_at")
        if not pa:
            continue
        jst = core.to_jst(pa)
        if jst.strftime("%m-%d") != md or jst.strftime("%Y") == this_year:
            continue
        by_year.setdefault(jst.strftime("%Y"), []).append(r)
    return [
        {"year": y, "plays": len(recs), "tracks": cumulative_ranking(recs, 10)}
        for y, recs in sorted(by_year.items(), reverse=True)
    ]


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

_RUN_STEPS = ("inbox", "sync", "sort", "archive", "dedupe")


def build_run_record(summaries: dict, run_id, date_str: str, dry_run: bool) -> dict:
    inbox = summaries.get("inbox", {})
    sync = summaries.get("sync", {})
    sort = summaries.get("sort", {})
    archive = summaries.get("archive", {})
    dedupe_s = summaries.get("dedupe", {})
    present = [k for k in _RUN_STEPS if k in summaries]
    return {
        "date": date_str,
        "run_id": run_id,
        "status": "success" if len(present) == len(_RUN_STEPS) else "partial",
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
            # 自動整理（同一録音のみ）。消した曲数とグループ数。
            "dedupe": {"deleted": dedupe_s.get("deleted", 0), "groups": dedupe_s.get("groups", 0)},
        },
        # 各ステップの内訳（サイトでステップをタップすると「どの曲がどこへ動いたか」を出す）。
        # 長くなり過ぎないよう各リストは上限を設ける。ステップが何もしていなければ空リスト。
        "detail": {
            "inbox": inbox.get("moved", [])[:200],
            "sync": sync.get("changes", [])[:100],
            "sort": sort.get("changes", [])[:100],
            "archive": archive.get("added_tracks", [])[:200],
            # 自動整理の内訳: 残した版 / 消した版 / 秒数差 / undo ID。
            "dedupe": dedupe_s.get("changes", [])[:100],
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


ARTIST_META_SEARCH_BUDGET = 40  # 1晩に検索で新規解決するアーティスト数の上限（API 負荷の頭打ち）


def _artist_image(artist: dict) -> str | None:
    """artist.images から中サイズ（無ければ最小）の URL を返す。一覧のサムネイル用。"""
    imgs = (artist or {}).get("images") or []
    if not imgs:
        return None
    return imgs[len(imgs) // 2].get("url") or imgs[-1].get("url")


def _load_artist_meta(data: Path) -> dict:
    """artist_meta.json（名前(小文字) → {id, name, image, genres, followers}）を読む。"""
    path = data / "artist_meta.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    meta = payload.get("artists")
    return meta if isinstance(meta, dict) else {}


def known_artist_ids(pl_records: list[dict]) -> dict[str, str]:
    """プレイリスト在籍曲から 名前(小文字) → Spotify アーティストID を作る（検索なしで解決できる分）。"""
    out: dict[str, str] = {}
    for r in pl_records:
        for a in r.get("artists") or []:
            name, aid = a.get("name"), a.get("id")
            if name and aid:
                out.setdefault(name.lower(), aid)
    return out


def build_artist_meta(sp, wanted: list[str], pl_records: list[dict], existing: dict,
                      search_budget: int = ARTIST_META_SEARCH_BUDGET) -> dict:
    """アーティストの画像・ジャンル・フォロワーを取得して名前キーのキャッシュを育てる。

    wanted は表示名のリスト（生涯集計に出てくる全アーティスト）。ID は
      1) プレイリスト在籍曲の artists[].id（無料・確実）
      2) 既存キャッシュ
      3) それでも未解決なら search API（1晩 search_budget 件まで）
    の順に解決し、GET /v1/artists（50件バッチ）で画像等をまとめて引く。

    キャッシュは名前をキーにする（拡張履歴が ID を持たず名前しか持たないため）。既存エントリは
    再取得しない＝毎晩の API 消費は「新しく増えたアーティストぶん」だけで済む。
    """
    meta = {k: dict(v) for k, v in existing.items()}
    from_playlists = known_artist_ids(pl_records)

    need_id: list[str] = []
    for name in wanted:
        key = name.lower()
        if meta.get(key, {}).get("image"):
            continue  # 取得済み（画像まで入っている）ものは触らない
        aid = meta.get(key, {}).get("id") or from_playlists.get(key)
        if aid:
            meta.setdefault(key, {"name": name})["id"] = aid
        else:
            need_id.append(name)

    for name in need_id[:search_budget]:  # 未解決は検索で ID を引く（上限つき）
        try:
            items = sp.search(q=name, type="artist", limit=1).get("artists", {}).get("items", [])
        except Exception:  # noqa: BLE001 — 1件の検索失敗で夜間全体を止めない
            continue
        if items and items[0].get("name", "").lower() == name.lower():
            meta.setdefault(name.lower(), {"name": name})["id"] = items[0]["id"]

    pending = [(k, v["id"]) for k, v in meta.items() if v.get("id") and not v.get("image")]
    for i in range(0, len(pending), 50):
        chunk = pending[i : i + 50]
        try:
            got = sp.artists([aid for _, aid in chunk]).get("artists", [])
        except Exception:  # noqa: BLE001
            continue
        for (key, _), art in zip(chunk, got):
            if not art:
                continue
            meta[key].update({
                "name": art.get("name", meta[key].get("name", "")),
                "image": _artist_image(art),
                "genres": (art.get("genres") or [])[:3],
                "followers": (art.get("followers") or {}).get("total"),
            })
    return meta


def _empty_top() -> dict:
    empty = {t: [] for t in ("short_term", "medium_term", "long_term")}
    return {"generated_at": _now_utc_iso(), "tracks": dict(empty), "artists": dict(empty)}


def build_archive_weekly(sp, dest_id: str) -> dict:
    weeks: dict[str, list[dict]] = {}
    results = core.retry_api(
        lambda: sp.playlist_items(
            dest_id, fields="items(added_at,track(id,name,artists(name),album(images))),next",
            additional_types=("track",), limit=100,
        ),
        what="archive playlist_items",
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
                 "artists": [a["name"] for a in track.get("artists", [])], "added_at": added_at,
                 "image": _album_image(track.get("album") or {})}
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
    #    Last.fm scrobble があればそれを正とし、無い期間だけ自前ログで補完する（_listening_records）。
    records = _listening_records(data)
    core.atomic_write_json(data / "listening_stats.json", {
        "generated_at": _now_utc_iso(),
        "since": min((r["played_at"] for r in records), default=None),
        "weekly_top": weekly_ranking(records, now_jst),
        "cumulative_top": cumulative_ranking(records),
        "streak": current_streak(records, now_jst.date()),
        "milestone": milestone_progress(len(records)),
    })
    core.atomic_write_json(data / "heatmap.json", {"generated_at": _now_utc_iso(), "cells": heatmap_cells(records)})

    # 月末なら wrapped（進行中の当月・new_tracks は nightly の runs.jsonl 由来＝inbox 追加数）
    if (now_jst + timedelta(days=1)).month != now_jst.month:
        month = now_jst.strftime("%Y-%m")
        new_tracks = _month_new_tracks(data / "runs.jsonl", month)
        core.atomic_write_json(data / "wrapped" / f"{month}.json", monthly_wrapped(records, month, new_tracks))

    # 過去の完了済み月で wrapped が無いものを埋める（拡張履歴の取り込みで新たに遡れるようになった分・
    # 冪等＝既存ファイルはスキップするので毎晩ほぼ無コスト。当月（進行中）は上のブロックに任せて対象外）。
    _backfill_wrapped(data, records, now_jst)
    # 年間 wrapped（過去年は確定なので冪等スキップ・当年だけ毎晩更新）
    _backfill_yearly_wrapped(data, records, now_jst)

    # 生涯集計（全曲・全アーティストのランキング／忘れられた名曲／◯年前の今日）。
    # アーティスト画像は前回のキャッシュで載せ、後段（API 節）で取得し直して上書きする。
    lifetime_names = write_lifetime(data, records, now_jst, _load_artist_meta(data))

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

    # アーティストの画像・ジャンルを取得してキャッシュを育て、生涯アーティスト一覧に載せ直す。
    # 既存エントリは再取得しないので、毎晩の API 消費は新しく増えたぶんだけ。
    try:
        meta = build_artist_meta(
            sp, [a["name"] for a in lifetime_names], search_records, _load_artist_meta(data)
        )
        core.atomic_write_json(data / "artist_meta.json", {"generated_at": _now_utc_iso(), "artists": meta})
        write_lifetime_artists(data, records, meta)
    except Exception as e:  # noqa: BLE001 — 画像が無くてもサイトは成立する
        logger.info(f"artist_meta スキップ: {e}")

    # 似ているアーティスト/曲（Last.fm 由来）。Spotify の推薦 API は廃止済みなので唯一の推薦源。
    try:
        import recommend
        recommend.build_recs(sp, data, logger)
    except Exception as e:  # noqa: BLE001 — おすすめが無くてもサイトは成立する
        logger.info(f"recs スキップ: {e}")

    # 発売予定（MusicBrainz 由来）。Spotify は未発売のリリースを返さないため外部ソースを使う。
    # 1req/秒の作法を守って毎晩少しずつ進むので、初回は件数が少ない。
    if "user-follow-read" not in missing:
        try:
            import upcoming
            upcoming.build_upcoming(sp, data, logger)
        except Exception as e:  # noqa: BLE001 — 予定が無くてもサイトは成立する
            logger.info(f"upcoming スキップ: {e}")
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


def _load_all_scrobbles(scrobbles_dir: Path) -> list[dict]:
    """Last.fm scrobble（lastfm_log.py が書く <data>/scrobbles/*.jsonl）を全部読む。"""
    records: list[dict] = []
    if scrobbles_dir.exists():
        for p in sorted(scrobbles_dir.glob("*.jsonl")):
            records.extend(core.read_jsonl(p))
    return records


def _norm_key(name: str, artist: str) -> str:
    """曲名＋アーティストを緩く正規化した突き合わせキー（大小・記号・空白差を吸収）。
    Last.fm の scrobble は Spotify の曲名をそのまま持つので、この程度の正規化で照合できる。"""
    import re

    def n(s: str) -> str:
        return re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", (s or "").lower())

    return f"{n(name)}|{n(artist)}"


def _scrobble_resolver(search_index_path: Path) -> dict:
    """search_index.json（前回ラン生成分）から (曲名, アーティスト) → {id, image} の索引を作る。
    Last.fm scrobble を Spotify track_id・アルバムアートに解決して、再生ボタン/アートを効かせる。"""
    import json

    na: dict[str, dict] = {}  # 曲名＋各アーティスト
    n_only: dict[str, dict] = {}  # 曲名のみ（アーティスト不一致時のフォールバック）
    try:
        data = json.loads(search_index_path.read_text())
    except (OSError, ValueError):
        return {"na": na, "n": n_only}
    for t in data.get("tracks", []):
        tid = t.get("id")
        if not tid:
            continue
        name = t.get("name", "")
        img = t.get("image")
        for a in t.get("artists") or []:
            na.setdefault(_norm_key(name, a), {"id": tid, "image": img})
        n_only.setdefault(_norm_key(name, ""), {"id": tid, "image": img})
    return {"na": na, "n": n_only}


def _scrobbles_to_records(scrobbles: list[dict], resolver: dict) -> list[dict]:
    """scrobble を聴取レコード形（track_id/name/artists/played_at/image）に変換する。
    Spotify に解決できれば spotify id（＝自前ログと同じ id で集計が合流する）、
    解決できなければ 'lastfm:<key>' の合成 id と Last.fm 画像を使う。"""
    na, n_only = resolver.get("na", {}), resolver.get("n", {})
    out: list[dict] = []
    for s in scrobbles:
        name = s.get("name", "")
        artist = s.get("artist", "")
        m = na.get(_norm_key(name, artist)) or n_only.get(_norm_key(name, ""))
        if m:
            tid = m["id"]
            image = m.get("image") or s.get("image")
        else:
            tid = "lastfm:" + _norm_key(name, artist)
            image = s.get("image")
        out.append({
            "track_id": tid,
            "name": name,
            "artists": [{"name": artist}] if artist else [],
            "played_at": s.get("played_at"),
            "image": image,
        })
    return out


def _load_history(history_dir: Path) -> list[dict]:
    """import_history.py が書く拡張ストリーミング履歴（<data>/history/*.jsonl.gz）を全部読む。
    gz でも素の .jsonl でも読めるようにしておく（将来 gz をやめても壊れない）。"""
    records: list[dict] = []
    if not history_dir.exists():
        return records
    for p in sorted(history_dir.glob("*.jsonl.gz")):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            continue
    for p in sorted(history_dir.glob("*.jsonl")):
        records.extend(core.read_jsonl(p))
    return records


def _live_listening_records(data: Path) -> list[dict]:
    """going-forward の聴取レコード（Last.fm scrobble ＋ 自前 recently-played ログ）を返す。
    Last.fm scrobble があればそれを正とし（50件制約なし）、scrobble が覆う期間の外側
    （連携前の先行分・連携停止後の穴）だけ自前ログで補完する。scrobble が無ければ自前ログのみ。"""
    scrobbles = _load_all_scrobbles(data / "scrobbles")
    if not scrobbles:
        return _load_all_listening(data / "listening")
    resolver = _scrobble_resolver(data / "search_index.json")
    records = _scrobbles_to_records(scrobbles, resolver)
    times = [core.parse_iso(r["played_at"]) for r in records if r.get("played_at")]
    if times:
        lo, hi = min(times), max(times)
        for r in _load_all_listening(data / "listening"):
            pa = r.get("played_at")
            if not pa:
                continue
            try:
                t = core.parse_iso(pa)
            except (ValueError, TypeError):
                continue
            if t < lo or t > hi:  # scrobble 未カバー期間（連携前／停止後）だけ補完
                records.append(r)
    return records


def _listening_records(data: Path) -> list[dict]:
    """聴取統計の元レコードを返す。

    拡張ストリーミング履歴（history/*.jsonl.gz・2019〜エクスポート日）があればそれを生涯の土台とし、
    その最終再生より後の分だけ going-forward ソース（scrobble/自前ログ）で継ぎ足す。history が
    連続した1ブロック（2019→エクスポート日）で穴が無いため、末尾以降だけ足せば二重計上は起きない。
    history が無ければ従来どおり going-forward ソースだけを使う。"""
    history = _load_history(data / "history")
    live = _live_listening_records(data)
    if not history:
        return live
    cutoff = max(
        (core.parse_iso(r["played_at"]) for r in history if r.get("played_at")),
        default=None,
    )
    if cutoff is None:
        return live
    tail: list[dict] = []
    for r in live:
        pa = r.get("played_at")
        if not pa:
            continue
        try:
            t = core.parse_iso(pa)
        except (ValueError, TypeError):
            continue
        if t > cutoff:  # 履歴エクスポート後の新しい再生だけ継ぎ足す
            tail.append(r)
    return history + tail


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
    """data/wrapped/ の一覧。月（YYYY-MM）と年（YYYY）を分けて出す（M3）。

    静的サイトはディレクトリを列挙できないのでこのインデックスが唯一の導線になる。年と月は
    ファイル名の形で判別する（4桁＝年 / 7桁＝月）。判別を stem の長さでやると将来 'index' 以外の
    付随ファイルが増えたとき壊れるので、正規表現で厳密に振り分ける。
    """
    wrapped_dir = data / "wrapped"
    months: list[str] = []
    years: list[str] = []
    if wrapped_dir.exists():
        for p in wrapped_dir.glob("*.json"):
            if re.fullmatch(r"\d{4}-\d{2}", p.stem):
                months.append(p.stem)
            elif re.fullmatch(r"\d{4}", p.stem):
                years.append(p.stem)
    core.atomic_write_json(
        data / "wrapped" / "index.json",
        {"months": sorted(months, reverse=True), "years": sorted(years, reverse=True)},
    )


def _backfill_yearly_wrapped(data: Path, records: list[dict], now_jst: datetime) -> None:
    """年間 wrapped（wrapped/YYYY.json）を用意する。

    過去の年は履歴が確定しているので既存ファイルがあればスキップ（冪等）。当年だけは毎晩
    上書きして進行中の内容を反映する。new_tracks は「その年に初めて聴いた曲の数」。
    """
    if not records:
        return
    current_year = now_jst.strftime("%Y")
    years_present = sorted({
        core.to_jst(r["played_at"]).strftime("%Y") for r in records if r.get("played_at")
    })
    new_counts = Counter(_first_play_periods(records, "%Y").values())
    for year in years_present:
        path = data / "wrapped" / f"{year}.json"
        if year != current_year and path.exists():
            continue
        core.atomic_write_json(path, yearly_wrapped(records, year, new_counts.get(year, 0)))


def _load_history_extra(history_dir: Path) -> dict[str, int]:
    """import_history.py が書く extra.json（曲ごとの短再生回数）を読む。無ければ空。"""
    path = history_dir / "extra.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    short = payload.get("short_plays")
    return short if isinstance(short, dict) else {}


def write_lifetime_artists(data: Path, records: list[dict], artist_meta: dict) -> list[dict]:
    """lifetime_artists.json を書き、書いた一覧を返す。

    artist_meta は 名前(小文字) → {id, image, ...} のキャッシュ（build_artist_meta が育てる）。
    初回は空でも成立し、同じ晩の後段でキャッシュを取得し直してから呼び直せば画像が載る。
    """
    ids = {k: v["id"] for k, v in artist_meta.items() if v.get("id")}
    artists = lifetime_artists(records, ids)
    for a in artists:  # 画像はキャッシュから引く（無い間はサイト側がプレースホルダを出す）
        img = (artist_meta.get(a["name"].lower()) or {}).get("image")
        if img:
            a["image"] = img
    core.atomic_write_json(data / "lifetime_artists.json", {
        "generated_at": _now_utc_iso(), "artists": artists,
    })
    return artists


def write_lifetime(data: Path, records: list[dict], now_jst: datetime, artist_meta: dict) -> list[dict]:
    """生涯集計のデータ一式を書く（曲・アーティスト・忘れられた名曲・◯年前の今日）。
    アーティスト一覧を返す（後段の画像取得で「誰を引くか」に使う）。"""
    if not records:
        return []
    short_plays = _load_history_extra(data / "history")
    tracks = lifetime_tracks(records, short_plays)
    artists = write_lifetime_artists(data, records, artist_meta)
    core.atomic_write_json(data / "lifetime_tracks.json", {
        "generated_at": _now_utc_iso(),
        "totals": lifetime_totals(records, tracks, artists),
        "tracks": tracks,
    })
    core.atomic_write_json(data / "rediscover.json", {
        "generated_at": _now_utc_iso(),
        "quiet_days": REDISCOVER_QUIET_DAYS,
        "min_plays": REDISCOVER_MIN_PLAYS,
        "tracks": rediscover(tracks, now_jst),
    })
    core.atomic_write_json(data / "on_this_day.json", {
        "generated_at": _now_utc_iso(),
        "date": now_jst.strftime("%m-%d"),
        "years": on_this_day(records, now_jst),
    })
    return artists


def _first_play_periods(records: list[dict], fmt: str) -> dict[str, str]:
    """track_id → その曲を最初に聴いた期間（JST・strftime の fmt で丸めたもの）。
    wrapped の new_tracks（その期間に初めて聴いた曲の数）を数えるための材料。"""
    first: dict[str, str] = {}
    for r in records:
        tid, pa = r.get("track_id"), r.get("played_at")
        if not tid or not pa:
            continue
        p = core.to_jst(pa).strftime(fmt)
        if tid not in first or p < first[tid]:
            first[tid] = p
    return first


def _first_play_months(records: list[dict]) -> dict[str, str]:
    """track_id → その曲を最初に聴いた月（JST 'YYYY-MM'）。過去分 wrapped の new_tracks 算出に使う。"""
    return _first_play_periods(records, "%Y-%m")


def _backfill_wrapped(data: Path, records: list[dict], now_jst: datetime) -> None:
    """履歴が覆う過去月のうち wrapped/YYYY-MM.json が無いものを埋める（拡張履歴の取り込みで
    新たに遡れるようになった分）。当月（進行中）は対象外＝上の月末ブロックに任せる。冪等（既存は
    スキップ）なので拡張履歴が無い環境でも無害・毎晩ほぼ無コスト。new_tracks は「その曲をライブラリ
    運用で追加した数」（当月ブロックの _month_new_tracks・runs.jsonl 由来）が過去分には無いため、
    代わりに「その月に初めて聴いた曲の数」を使う（全期間データからのみ求まる、より汎用的な代替指標）。"""
    if not records:
        return
    current_month = now_jst.strftime("%Y-%m")
    months_present = sorted({
        core.to_jst(r["played_at"]).strftime("%Y-%m") for r in records if r.get("played_at")
    })
    pending = [m for m in months_present if m < current_month and not (data / "wrapped" / f"{m}.json").exists()]
    if not pending:
        return
    new_counts = Counter(_first_play_months(records).values())
    for month in pending:
        core.atomic_write_json(data / "wrapped" / f"{month}.json", monthly_wrapped(records, month, new_counts.get(month, 0)))


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
