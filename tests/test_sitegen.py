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
        "inbox": {"processed": 4, "japanese": 1, "western": 3, "unknown_count": 0, "unknown": [],
                  "moved": [{"name": "s1", "artist": "A", "dest": ["Japanese Musics"]}]},
        "sync": {"added": 3, "removed": 0, "new_playlists": 0,
                 "changes": [{"playlist": "AP", "added": ["s1", "s2", "s3"], "removed": 0}]},
        "sort": {"playlists": 8, "skipped": 0,
                 "changes": [{"name": "Western Musics", "status": "sorted", "count": 100}]},
        "archive": {"added": 0, "added_tracks": []},
        "dedupe": {"deleted": 3, "groups": 2, "changes": [
            {"name": "drunk text", "kept": {"album": "mood swings", "album_type": "album"},
             "removed": [{"album": "in all of my lonely nights", "album_type": "single"}],
             "delta_ms": 0, "undo_id": "u1"}]},
    }
    r = sitegen.build_run_record(summaries, 123, "2026-07-15", False)
    assert r["status"] == "success"  # 5ステップ全て揃って成功
    assert r["run_id"] == 123
    assert r["steps"]["inbox"]["western"] == 3
    assert r["steps"]["dedupe"] == {"deleted": 3, "groups": 2}
    # ステップ内訳がそのまま載る（サイトのモーダル用）
    assert r["detail"]["inbox"][0]["dest"] == ["Japanese Musics"]
    assert r["detail"]["sync"][0]["added"] == ["s1", "s2", "s3"]
    assert r["detail"]["sort"][0]["name"] == "Western Musics"
    assert r["detail"]["dedupe"][0]["undo_id"] == "u1"
    # dedupe が欠けたら partial（1ステップでも欠落）
    assert sitegen.build_run_record({k: {} for k in ("inbox", "sync", "sort", "archive")},
                                    1, "d", False)["status"] == "partial"
    assert sitegen.build_run_record({"inbox": {}}, 1, "d", False)["status"] == "partial"
    # 旧サマリ（detail 無し）は各ステップ空リストにフォールバックする
    r2 = sitegen.build_run_record({"inbox": {}}, 1, "d", False)
    assert r2["detail"]["inbox"] == [] and r2["detail"]["dedupe"] == []
    assert r2["steps"]["dedupe"] == {"deleted": 0, "groups": 0}


def test_build_stats_and_search():
    records = [
        {"id": "a", "name": "x", "artists": [{"name": "Ed", "id": "art_ed"}], "album": {"release_date": "2014-06-20"},
         "playlists": [{"id": "p1", "name": "W"}]},
        {"id": "b", "name": "y", "artists": [{"name": "Ed", "id": "art_ed"}],
         "album": {"release_date": "2011-01-01", "images": [{"url": "big"}, {"url": "small"}]},
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
    assert b["image"] == "small"  # 保留タブ等のサムネイル用（最小サイズ）
    a = next(t for t in idx["tracks"] if t["id"] == "a")
    assert a["image"] is None  # images 無しは None

    rows = sitegen.playlist_count_rows(records, [{"id": "p1", "name": "W"}, {"id": "p2", "name": "Ed"}], "2026-07-15")
    counts = {r["playlist_id"]: r["count"] for r in rows}
    assert counts == {"p1": 2, "p2": 1}


def test_build_top_includes_image():
    class Sp:
        def current_user_top_tracks(self, limit, time_range):
            return {"items": [{"id": "t1", "name": "T", "artists": [{"name": "A"}],
                               "album": {"images": [{"url": "big"}, {"url": "sm"}]}}]}

        def current_user_top_artists(self, limit, time_range):
            return {"items": [{"id": "a1", "name": "AA"}]}

    top = sitegen.build_top(Sp())
    assert top["tracks"]["short_term"][0]["image"] == "sm"  # 公式 Top のサムネイル（最小サイズ）


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


# ─────────────────────────── Last.fm scrobble 取り込み ───────────────────────────

def test_cumulative_ranking_keeps_image_when_present():
    recs = [
        {"played_at": "2026-07-01T10:00:00Z", "track_id": "t1", "name": "n", "artists": [{"name": "A"}], "image": "http://i/1.jpg"},
        {"played_at": "2026-07-02T10:00:00Z", "track_id": "t1", "name": "n", "artists": [{"name": "A"}], "image": "http://i/1.jpg"},
    ]
    r = sitegen.cumulative_ranking(recs)
    assert r[0]["image"] == "http://i/1.jpg" and r[0]["count"] == 2
    # 画像の無いレコード（自前ログ由来）は image キーを付けない＝既存出力を変えない
    assert "image" not in sitegen.cumulative_ranking([_rec("2026-07-01T10:00:00Z", "t2")])[0]


def test_scrobbles_resolve_to_spotify_or_synthesize(tmp_path):
    import json
    (tmp_path / "search_index.json").write_text(json.dumps({
        "tracks": [
            {"id": "3xhc0Y528hLu0Rc4iBrDP1", "name": "STAY (with Justin Bieber)",
             "artists": ["The Kid LAROI", "Justin Bieber"], "image": "http://img/stay.jpg"},
        ]
    }), encoding="utf-8")
    resolver = sitegen._scrobble_resolver(tmp_path / "search_index.json")
    scrobbles = [
        {"played_at": "2026-07-19T10:00:00+00:00", "uts": 1, "name": "STAY (with Justin Bieber)",
         "artist": "The Kid LAROI", "image": "http://lfm/stay.jpg"},
        {"played_at": "2026-07-19T11:00:00+00:00", "uts": 2, "name": "Unknown Song",
         "artist": "Nobody", "image": "http://lfm/unk.jpg"},
    ]
    recs = sitegen._scrobbles_to_records(scrobbles, resolver)
    # ライブラリ内は Spotify id とアートに解決（アートは search_index 優先）
    assert recs[0]["track_id"] == "3xhc0Y528hLu0Rc4iBrDP1"
    assert recs[0]["image"] == "http://img/stay.jpg"
    assert recs[0]["artists"] == [{"name": "The Kid LAROI"}]
    # 未解決は lastfm: 合成id ＋ Last.fm 画像
    assert recs[1]["track_id"].startswith("lastfm:")
    assert recs[1]["image"] == "http://lfm/unk.jpg"


def test_listening_records_prefers_scrobbles_and_fills_gaps(tmp_path):
    (tmp_path / "scrobbles").mkdir()
    (tmp_path / "listening").mkdir()
    core.append_jsonl(tmp_path / "scrobbles" / "2026-07.jsonl", [
        {"played_at": "2026-07-18T10:00:00+00:00", "uts": 100, "name": "B", "artist": "X", "image": None},
        {"played_at": "2026-07-19T10:00:00+00:00", "uts": 200, "name": "C", "artist": "Y", "image": None},
    ])
    core.append_jsonl(tmp_path / "listening" / "2026-07.jsonl", [
        _rec("2026-07-17T10:00:00Z", "before"),   # 連携前（先行）→ 補完される
        _rec("2026-07-18T12:00:00Z", "inside"),   # scrobble カバー内 → 重複回避で除外
        _rec("2026-07-20T10:00:00Z", "after"),    # 連携停止後（穴）→ 補完される
    ])
    ids = [r["track_id"] for r in sitegen._listening_records(tmp_path)]
    assert "before" in ids and "after" in ids and "inside" not in ids


def test_listening_records_falls_back_to_selflog(tmp_path):
    (tmp_path / "listening").mkdir()
    core.append_jsonl(tmp_path / "listening" / "2026-07.jsonl", [_rec("2026-07-17T10:00:00Z", "x")])
    # scrobbles ディレクトリ無し → 自前ログにフォールバック
    assert [r["track_id"] for r in sitegen._listening_records(tmp_path)] == ["x"]
