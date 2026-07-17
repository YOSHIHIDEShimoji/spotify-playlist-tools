from datetime import date, datetime

import core
import sitegen


def _rec(played_at, tid, name="n", artists=None):
    return {
        "played_at": played_at,
        "track_id": tid,
        "name": name,
        "artists": artists or [{"id": "a1", "name": "A"}],
        "duration_ms": 1000,
    }


def test_cumulative_ranking_counts_and_orders():
    recs = [
        _rec("2026-07-01T10:00:00Z", "t1"),
        _rec("2026-07-02T10:00:00Z", "t1"),
        _rec("2026-07-02T11:00:00Z", "t2"),
    ]
    r = sitegen.cumulative_ranking(recs)
    assert r[0] == {"track_id": "t1", "name": "n", "artists": ["A"], "count": 2}
    assert r[1]["track_id"] == "t2" and r[1]["count"] == 1


def test_weekly_ranking_filters_to_current_iso_week():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=core.JST)  # 水曜、週=07-13〜07-19
    recs = [_rec("2026-07-14T10:00:00Z", "in"), _rec("2026-06-01T10:00:00Z", "out")]
    ids = {x["track_id"] for x in sitegen.weekly_ranking(recs, now)}
    assert "in" in ids and "out" not in ids


def test_heatmap_cells_uses_jst():
    # UTC 00:00 → JST 09:00、2026-07-15 は水曜（weekday=2）
    cells = sitegen.heatmap_cells([_rec("2026-07-15T00:00:00Z", "t")])
    assert {"dow": 2, "hour": 9, "count": 1} in cells


def test_current_streak_counts_consecutive_days():
    today = date(2026, 7, 15)
    recs = [
        _rec("2026-07-15T02:00:00Z", "t"),  # JST 07-15
        _rec("2026-07-14T02:00:00Z", "t"),  # JST 07-14
        _rec("2026-07-13T02:00:00Z", "t"),  # JST 07-13
    ]
    assert sitegen.current_streak(recs, today) == 3
    assert sitegen.current_streak([], today) == 0


def test_milestone_progress():
    m = sitegen.milestone_progress(300)
    assert m["total"] == 300 and m["next"] == 500 and 250 in m["reached"] and 500 not in m["reached"]


def test_monthly_wrapped():
    recs = [
        _rec("2026-07-15T02:00:00Z", "t1"),
        _rec("2026-07-16T02:00:00Z", "t1"),
        _rec("2026-06-01T02:00:00Z", "t2"),  # 別月
    ]
    w = sitegen.monthly_wrapped(recs, "2026-07", new_tracks=5)
    assert w["plays"] == 2 and w["new_tracks"] == 5
    assert w["top_tracks"][0]["track_id"] == "t1"


def test_build_run_record_status():
    summaries = {
        "inbox": {"processed": 4, "japanese": 1, "western": 3, "unknown_count": 0, "unknown": []},
        "sync": {"added": 3, "removed": 0, "new_playlists": 0},
        "sort": {"playlists": 8, "skipped": 0},
        "archive": {"added": 0},
    }
    r = sitegen.build_run_record(summaries, 123, "2026-07-15", False)
    assert r["status"] == "success"
    assert r["run_id"] == 123
    assert r["steps"]["inbox"]["western"] == 3
    assert sitegen.build_run_record({"inbox": {}}, 1, "d", False)["status"] == "partial"


def test_build_stats_and_search():
    records = [
        {"id": "a", "name": "x", "artists": [{"name": "Ed", "id": "art_ed"}], "album": {"release_date": "2014-06-20"},
         "playlists": [{"id": "p1", "name": "W"}]},
        {"id": "b", "name": "y", "artists": [{"name": "Ed", "id": "art_ed"}], "album": {"release_date": "2011-01-01"},
         "playlists": [{"id": "p1", "name": "W"}, {"id": "p2", "name": "Ed"}]},
    ]
    s = sitegen.build_stats(records)
    ed = next(a for a in s["artists_top"] if a["name"] == "Ed")
    assert ed["count"] == 2 and ed["id"] == "art_ed"  # 直リンク用の ID を載せる
    assert s["total"] == 2  # ユニーク曲数（延べではない）
    assert 2010 in {d["decade"] for d in s["decades"]}

    idx = sitegen.build_search_index(records)
    b = next(t for t in idx["tracks"] if t["id"] == "b")
    assert b["playlists"] == ["W", "Ed"]

    rows = sitegen.playlist_count_rows(records, [{"id": "p1", "name": "W"}, {"id": "p2", "name": "Ed"}], "2026-07-15")
    counts = {r["playlist_id"]: r["count"] for r in rows}
    assert counts == {"p1": 2, "p2": 1}


def test_select_recent_albums():
    albums = [
        {"id": "al1", "name": "New", "album_type": "single", "artists": [{"name": "X"}], "release_date": "2026-07-15"},
        {"id": "al2", "name": "Old", "album_type": "album", "artists": [{"name": "X"}], "release_date": "2020-01-01"},
        {"id": "al3", "name": "Seen", "album_type": "single", "artists": [{"name": "X"}], "release_date": "2026-07-14"},
        {"id": "al4", "name": "YearOnly", "album_type": "album", "artists": [{"name": "X"}], "release_date": "2026"},
    ]
    # レビュー H4: seen で抑止せず窓内を全部返す。seen は is_new 判定だけに使う
    window, ids = sitegen.select_recent_albums(albums, "2026-07-10", {"al3"})
    got = {f["album_id"]: f["is_new"] for f in window}
    assert got == {"al1": True, "al3": False}  # al1 新着 / al3 既読だが窓内なので出す / al2 古い / al4 年のみ
    assert ids == {"al1", "al2", "al3", "al4"}
