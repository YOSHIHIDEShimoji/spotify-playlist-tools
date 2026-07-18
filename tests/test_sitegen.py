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
    assert b["release_date"] == "2011-01-01"  # 年代モーダル用に載せる

    rows = sitegen.playlist_count_rows(records, [{"id": "p1", "name": "W"}, {"id": "p2", "name": "Ed"}], "2026-07-15")
    counts = {r["playlist_id"]: r["count"] for r in rows}
    assert counts == {"p1": 2, "p2": 1}


def test_merge_records_and_stats_dist():
    # a: Western のみ / b: Western（後で 1900's も）/ c: 1900's のみ
    base = [
        {"id": "a", "artists": [{"name": "X"}], "album": {"release_date": "2015-01-01"},
         "playlists": [{"id": "pW", "name": "W"}]},
        {"id": "b", "artists": [{"name": "Y"}], "album": {"release_date": "2005-01-01"},
         "playlists": [{"id": "pW", "name": "W"}]},
    ]
    extra = [
        {"id": "b", "artists": [{"name": "Y"}], "album": {"release_date": "2005-01-01"},
         "playlists": [{"id": "p19", "name": "1900s"}]},
        {"id": "c", "artists": [{"name": "Z"}], "album": {"release_date": "1998-01-01"},
         "playlists": [{"id": "p19", "name": "1900s"}]},
    ]
    merged = sitegen._merge_records(base, extra)
    assert len(merged) == 3  # a, b, c
    b = next(r for r in merged if r["id"] == "b")
    assert {p["id"] for p in b["playlists"]} == {"pW", "p19"}  # 在籍をマージ

    dist = sitegen.build_stats_dist(merged, [{"id": "pW", "name": "W"}, {"id": "p19", "name": "1900s"}])
    assert [p["id"] for p in dist["playlists"]] == ["pW", "p19"]
    assert dist["by"]["pW"]["total"] == 2   # a, b
    assert dist["by"]["p19"]["total"] == 2  # b, c
    assert dist["all"]["total"] == 3        # a, b, c（重複 b は1回だけ）


def test_select_recent_albums():
    albums = [
        {"id": "al1", "name": "New", "album_type": "single", "artists": [{"name": "X", "id": "artX"}],
         "release_date": "2026-07-15", "images": [{"url": "u640"}, {"url": "u300"}, {"url": "u64"}]},
        {"id": "al2", "name": "Old", "album_type": "album", "artists": [{"name": "X"}], "release_date": "2020-01-01"},
        {"id": "al3", "name": "Seen", "album_type": "single", "artists": [{"name": "X"}], "release_date": "2026-07-14"},
        {"id": "al4", "name": "YearOnly", "album_type": "album", "artists": [{"name": "X"}], "release_date": "2026"},
    ]
    # レビュー H4: seen で抑止せず窓内を全部返す。seen は is_new 判定だけに使う
    window, ids = sitegen.select_recent_albums(albums, "2026-07-10", {"al3"})
    got = {f["album_id"]: f["is_new"] for f in window}
    assert got == {"al1": True, "al3": False}  # al1 新着 / al3 既読だが窓内なので出す / al2 古い / al4 年のみ
    assert ids == {"al1", "al2", "al3", "al4"}
    al1 = next(f for f in window if f["album_id"] == "al1")
    assert al1["artist_id"] == "artX"       # 邦/洋分けに使う primary artist id
    assert al1["image"] == "u64"            # 最小サイズ（末尾）のサムネイル


def test_release_class_map(monkeypatch):
    import classify
    import inbox
    monkeypatch.setattr(inbox, "load_inbox_config", lambda _p: ("pJP", "pW", {}))
    monkeypatch.setattr(classify, "load_cache", lambda: {"artC": {"class": "japanese"}})
    pl_records = [
        {"artists": [{"id": "artA", "name": "A"}], "playlists": [{"id": "pW", "name": "W"}]},
        {"artists": [{"id": "artB", "name": "B"}], "playlists": [{"id": "pJP", "name": "JP"}]},
        {"artists": [{"id": "artC", "name": "C"}], "playlists": [{"id": "pW", "name": "W"}]},
    ]
    m = sitegen._release_class_map(pl_records)
    assert m["artA"] == "western"
    assert m["artB"] == "japanese"
    assert m["artC"] == "japanese"  # classify cache が在籍推定より優先される
