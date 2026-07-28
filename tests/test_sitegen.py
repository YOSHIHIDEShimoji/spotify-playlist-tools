import json
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


def test_monthly_wrapped_top_tracks_and_artists_not_capped_at_ten():
    # WRAPPED_TOP=20: 25曲の月で先頭20件だけ（15や100など別の値では緑にならないよう両端を固定）
    recs = [_rec(f"2026-07-{d:02d}T02:00:00Z", f"t{d}", artists=[{"id": f"a{d}", "name": f"A{d}"}]) for d in range(1, 26)]
    w = sitegen.monthly_wrapped(recs, "2026-07")
    assert len(w["top_tracks"]) == sitegen.WRAPPED_TOP == 20
    assert len(w["top_artists"]) == sitegen.WRAPPED_TOP == 20


def test_first_play_months_uses_earliest_play_per_track():
    recs = [
        # t1: 後ろの行のほうが早い月（到着順ではなく本当の最小月を選ぶこと・"or m < first[tid]" の検証）
        _rec("2020-03-01T00:00:00Z", "t1"),
        _rec("2020-01-15T00:00:00Z", "t1"),
        _rec("2021-06-01T00:00:00Z", "t2"),
    ]
    fm = sitegen._first_play_months(recs)
    assert fm == {"t1": "2020-01", "t2": "2021-06"}


def test_backfill_wrapped_fills_past_months_only_and_is_idempotent(tmp_path):
    now = datetime(2026, 7, 20, 12, 0, tzinfo=core.JST)
    recs = [
        _rec("2019-09-05T13:00:00Z", "old"),
        _rec("2019-09-20T13:00:00Z", "old"),   # old の同月内の再々生（distinct曲数/再生数と区別するため）
        _rec("2020-01-01T00:00:00Z", "old"),   # old の既出再生（2020-01 の new_tracks は old2 の1曲のみ）
        _rec("2020-01-01T00:05:00Z", "old2"),
        # JST 境界: UTC では前日=1月31日夜だが JST では2月1日 → 2020-02 側に計上されること
        _rec("2020-01-31T20:00:00Z", "boundary"),
        _rec("2026-07-10T00:00:00Z", "current_month_track"),  # 当月＝進行中 → 対象外
    ]
    sitegen._backfill_wrapped(tmp_path, recs, now)
    assert (tmp_path / "wrapped" / "2019-09.json").exists()
    assert (tmp_path / "wrapped" / "2020-01.json").exists()
    assert not (tmp_path / "wrapped" / "2026-07.json").exists()  # 当月は月末ブロックの担当

    import json
    # 2019-09: old を2回再生（再生数2）だが「初めて聴いた曲」は old の1曲のみ
    w0909 = json.loads((tmp_path / "wrapped" / "2019-09.json").read_text())
    assert w0909["plays"] == 2 and w0909["new_tracks"] == 1
    # 2020-01: old（既出）+ old2（新規）+ boundary は JST では2020-02 側 → 2020-01 の new_tracks は old2 のみ
    w0101 = json.loads((tmp_path / "wrapped" / "2020-01.json").read_text())
    assert w0101["new_tracks"] == 1
    assert "boundary" not in [t["track_id"] for t in w0101["top_tracks"]]
    # boundary の再生は JST 基準で 2020-02 に計上され、2020-02.json が生成される
    assert (tmp_path / "wrapped" / "2020-02.json").exists()
    w0202 = json.loads((tmp_path / "wrapped" / "2020-02.json").read_text())
    assert w0202["plays"] == 1 and w0202["new_tracks"] == 1
    assert [t["track_id"] for t in w0202["top_tracks"]] == ["boundary"]

    # 冪等: 既存ファイルを人為的に変えても上書きしない（同一月を再度書かない）
    (tmp_path / "wrapped" / "2019-09.json").write_text('{"month": "2019-09", "sentinel": true}')
    sitegen._backfill_wrapped(tmp_path, recs, now)
    assert json.loads((tmp_path / "wrapped" / "2019-09.json").read_text()) == {"month": "2019-09", "sentinel": True}


def test_backfill_wrapped_noop_when_no_records(tmp_path):
    now = datetime(2026, 7, 20, 12, 0, tzinfo=core.JST)
    sitegen._backfill_wrapped(tmp_path, [], now)
    assert not (tmp_path / "wrapped").exists()


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


def _write_history_gz(history_dir, records):
    import gzip
    import json

    history_dir.mkdir(parents=True, exist_ok=True)
    by_year = {}
    for r in records:
        by_year.setdefault(r["played_at"][:4], []).append(r)
    for year, recs in by_year.items():
        with gzip.open(history_dir / f"{year}.jsonl.gz", "wt", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_load_history_reads_gz(tmp_path):
    _write_history_gz(tmp_path / "history", [
        _rec("2019-09-05T13:00:00Z", "h1"),
        _rec("2020-01-01T00:00:00Z", "h2"),
    ])
    ids = sorted(r["track_id"] for r in sitegen._load_history(tmp_path / "history"))
    assert ids == ["h1", "h2"]


def test_load_history_absent_is_empty(tmp_path):
    assert sitegen._load_history(tmp_path / "history") == []


def test_load_history_reads_plain_jsonl_too(tmp_path):
    # gz をやめても壊れないフォールバック分岐（素 .jsonl）を保証する
    (tmp_path / "history").mkdir()
    core.append_jsonl(tmp_path / "history" / "2019.jsonl", [_rec("2019-09-05T13:00:00Z", "plain")])
    assert [r["track_id"] for r in sitegen._load_history(tmp_path / "history")] == ["plain"]


def test_listening_records_uses_history_as_base_and_only_appends_tail(tmp_path):
    # 生涯履歴（2019〜2026-07-18）
    _write_history_gz(tmp_path / "history", [
        _rec("2019-09-05T13:00:00Z", "hist_old"),
        _rec("2026-07-18T21:00:00Z", "hist_last"),  # 履歴の末尾（cutoff）
    ])
    # going-forward の自前ログ: cutoff より前は履歴と二重計上しないため無視、後だけ継ぎ足す
    (tmp_path / "listening").mkdir()
    core.append_jsonl(tmp_path / "listening" / "2026-07.jsonl", [
        _rec("2026-07-10T10:00:00Z", "live_before"),   # 履歴が覆う期間 → 無視
        _rec("2026-07-18T21:00:00Z", "live_eq"),        # cutoff と同時刻（== 境界）→ 無視（二重計上の継ぎ目）
        _rec("2026-07-19T10:00:00Z", "live_tail"),      # cutoff より後 → 継ぎ足す
    ])
    ids = [r["track_id"] for r in sitegen._listening_records(tmp_path)]
    # 継ぎ目 == cutoff は必ず除外（`>` を `>=` に緩めるとここで落ちる）。件数も固定して二重計上を面で検出。
    assert sorted(ids) == ["hist_last", "hist_old", "live_tail"]
    assert ids.count("hist_last") == 1  # history が二重連結されない


def test_listening_records_history_prefers_scrobble_tail(tmp_path):
    _write_history_gz(tmp_path / "history", [_rec("2026-07-18T21:00:00Z", "hist_last")])
    # scrobble がある場合も cutoff より後のものだけ継ぎ足す
    (tmp_path / "scrobbles").mkdir()
    core.append_jsonl(tmp_path / "scrobbles" / "2026-07.jsonl", [
        {"played_at": "2026-07-18T10:00:00+00:00", "uts": 1, "name": "old", "artist": "X", "image": None},
        {"played_at": "2026-07-20T10:00:00+00:00", "uts": 2, "name": "new", "artist": "Y", "image": None},
    ])
    names = [r.get("name") for r in sitegen._listening_records(tmp_path)]
    assert "new" in names        # cutoff 後の scrobble は入る
    assert "old" not in names    # cutoff 前の scrobble は履歴側が正なので入らない


# ─────────────────────────── 生涯集計 ───────────────────────────

def _play(played_at, tid, name="n", artist="A", ms=200000):
    return {"played_at": played_at, "track_id": tid, "name": name,
            "artists": [{"name": artist}], "ms": ms}


def test_lifetime_tracks_aggregates_count_ms_span_and_years():
    recs = [
        _play("2019-10-01T00:00:00Z", "t1", ms=100),
        _play("2020-03-05T00:00:00Z", "t1", ms=200),
        _play("2026-01-02T00:00:00Z", "t1", ms=300),
    ]
    (row,) = sitegen.lifetime_tracks(recs)
    assert row["count"] == 3
    assert row["ms"] == 600                       # 総再生時間は各再生の ms の合計
    assert row["first"] == "2019-10-01"           # JST の日付（初回/最終）
    assert row["last"] == "2026-01-02"
    assert row["years"] == {"2019": 1, "2020": 1, "2026": 1}


def test_lifetime_tracks_sorted_by_count_then_name():
    recs = [
        _play("2020-01-01T00:00:00Z", "few", name="zzz"),
        _play("2020-01-01T00:00:00Z", "many", name="mmm"),
        _play("2020-01-02T00:00:00Z", "many", name="mmm"),
        _play("2020-01-03T00:00:00Z", "tie", name="aaa"),
    ]
    order = [t["id"] for t in sitegen.lifetime_tracks(recs)]
    # 再生回数の多い順、同数は曲名昇順（aaa < zzz）。順位＝配列の並びなので順序が仕様。
    assert order == ["many", "tie", "few"]


def test_lifetime_tracks_uses_jst_year_boundary():
    # UTC 2019-12-31 15:30 は JST 2020-01-01 00:30 → 2020 年に数える
    (row,) = sitegen.lifetime_tracks([_play("2019-12-31T15:30:00Z", "t")])
    assert row["years"] == {"2020": 1} and row["first"] == "2020-01-01"


def test_lifetime_tracks_merges_short_plays():
    rows = sitegen.lifetime_tracks([_play("2020-01-01T00:00:00Z", "t1")], {"t1": 7, "other": 3})
    assert rows[0]["short"] == 7
    # 該当なしの曲に short キーを生やさない（完走率の分母を誤らせない）
    rows2 = sitegen.lifetime_tracks([_play("2020-01-01T00:00:00Z", "t2")], {"t1": 7})
    assert "short" not in rows2[0]


def test_lifetime_tracks_treats_missing_ms_as_zero():
    # live ログ / scrobble には ms が無い。欠損を 0 として扱い、例外にも None 混入にもしない。
    rec = {"played_at": "2020-01-01T00:00:00Z", "track_id": "t", "name": "n", "artists": []}
    (row,) = sitegen.lifetime_tracks([rec])
    assert row["ms"] == 0


def test_lifetime_artists_counts_every_credited_artist():
    collab = {"played_at": "2020-01-01T00:00:00Z", "track_id": "t1", "name": "song",
              "artists": [{"name": "Ed"}, {"name": "Charlie"}], "ms": 1000}
    rows = {a["name"]: a for a in sitegen.lifetime_artists([collab])}
    # コラボは両方に1回ずつ付く（どちらか一方に寄せない）
    assert rows["Ed"]["count"] == 1 and rows["Charlie"]["count"] == 1
    assert rows["Ed"]["ms"] == 1000 and rows["Charlie"]["ms"] == 1000


def test_lifetime_artists_counts_unique_tracks_not_plays():
    recs = [
        _play("2020-01-01T00:00:00Z", "t1", artist="A"),
        _play("2020-01-02T00:00:00Z", "t1", artist="A"),  # 同じ曲の再生
        _play("2020-01-03T00:00:00Z", "t2", artist="A"),
    ]
    (row,) = sitegen.lifetime_artists(recs)
    assert row["count"] == 3 and row["tracks"] == 2


def test_lifetime_artists_is_case_insensitive_but_keeps_display_name():
    recs = [
        _play("2020-01-01T00:00:00Z", "t1", artist="Queen"),
        _play("2020-01-02T00:00:00Z", "t2", artist="QUEEN"),
    ]
    (row,) = sitegen.lifetime_artists(recs, {"queen": "Q1"})
    assert row["count"] == 2          # 表記揺れは1人に畳む
    assert row["name"] == "Queen"     # 表示は初出の表記
    assert row["id"] == "Q1"          # ID は小文字キーで引く


def test_lifetime_totals():
    recs = [
        _play("2020-01-01T00:00:00Z", "t1", ms=100),
        _play("2020-01-01T05:00:00Z", "t2", ms=200),  # 同じ JST 日
        _play("2021-06-01T00:00:00Z", "t1", ms=300),
    ]
    tracks = sitegen.lifetime_tracks(recs)
    artists = sitegen.lifetime_artists(recs)
    tot = sitegen.lifetime_totals(recs, tracks, artists)
    assert tot == {"plays": 3, "tracks": 2, "artists": 1, "ms": 600, "since": "2020-01-01", "days": 2}


def test_yearly_wrapped_buckets_by_jst_year():
    recs = [
        _play("2019-12-31T15:30:00Z", "in"),   # JST 2020-01-01
        _play("2020-06-01T03:00:00Z", "in"),
        _play("2021-01-01T00:00:00Z", "out"),
    ]
    w = sitegen.yearly_wrapped(recs, "2020", new_tracks=5)
    assert w["year"] == "2020" and w["plays"] == 2 and w["new_tracks"] == 5
    assert w["ms"] == 400000
    assert [m["month"] for m in w["months"]] == ["2020-01", "2020-06"]
    assert {t["track_id"] for t in w["top_tracks"]} == {"in"}


def test_rediscover_picks_loved_but_long_silent_tracks():
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    tracks = [
        {"id": "gem", "name": "gem", "count": 50, "last": "2023-01-01"},   # 昔よく聴いた → 対象
        {"id": "recent", "name": "recent", "count": 90, "last": "2026-07-01"},  # 最近聴いた → 除外
        {"id": "rare", "name": "rare", "count": 3, "last": "2020-01-01"},  # 回数不足 → 除外
    ]
    got = sitegen.rediscover(tracks, now)
    assert [t["id"] for t in got] == ["gem"]


def test_rediscover_boundary_is_exactly_quiet_days():
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    base = {"id": "x", "name": "x", "count": 99}
    # 365日前ちょうど（2025-07-29）は「まだ聴いている」側＝除外、その1日前は対象
    assert sitegen.rediscover([{**base, "last": "2025-07-29"}], now) == []
    assert len(sitegen.rediscover([{**base, "last": "2025-07-28"}], now)) == 1


def test_rediscover_respects_limit_and_order():
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    tracks = [{"id": f"t{i}", "name": f"t{i}", "count": i, "last": "2020-01-01"} for i in (10, 30, 20)]
    got = sitegen.rediscover(tracks, now, limit=2)
    assert [t["id"] for t in got] == ["t30", "t20"]


def test_on_this_day_matches_month_day_and_excludes_current_year():
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    recs = [
        _play("2020-07-28T15:30:00Z", "a"),   # JST 2020-07-29 → 該当
        _play("2024-07-29T03:00:00Z", "b"),   # JST 2024-07-29 → 該当
        _play("2026-07-29T03:00:00Z", "now"),  # 今年 → 除外（思い出ではない）
        _play("2022-03-03T03:00:00Z", "x"),   # 別の日 → 除外
    ]
    got = sitegen.on_this_day(recs, now)
    assert [y["year"] for y in got] == ["2024", "2020"]   # 新しい年から
    assert got[0]["tracks"][0]["track_id"] == "b"


def test_write_wrapped_index_separates_years_and_months(tmp_path):
    wd = tmp_path / "wrapped"
    wd.mkdir()
    for stem in ("2019-09", "2026-06", "2019", "2026", "index"):
        (wd / f"{stem}.json").write_text("{}")
    sitegen._write_wrapped_index(tmp_path)
    idx = json.loads((wd / "index.json").read_text())
    assert idx["months"] == ["2026-06", "2019-09"]   # 新しい順
    assert idx["years"] == ["2026", "2019"]
    # index.json 自身がどちらにも混ざらないこと（サイトが存在しない月を読みに行かない）
    assert "index" not in idx["months"] and "index" not in idx["years"]


def test_backfill_yearly_wrapped_skips_past_but_refreshes_current(tmp_path):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    recs = [_play("2020-05-01T00:00:00Z", "old"), _play("2026-05-01T00:00:00Z", "new")]
    (tmp_path / "wrapped").mkdir()
    # 過去年は既存ファイルを尊重（履歴は確定済み＝上書きしない）
    sentinel = {"year": "2020", "plays": 999}
    (tmp_path / "wrapped" / "2020.json").write_text(json.dumps(sentinel))
    # 当年は前回の途中経過が残っている状態
    (tmp_path / "wrapped" / "2026.json").write_text(json.dumps({"year": "2026", "plays": 1}))

    sitegen._backfill_yearly_wrapped(tmp_path, recs, now)

    assert json.loads((tmp_path / "wrapped" / "2020.json").read_text()) == sentinel
    cur = json.loads((tmp_path / "wrapped" / "2026.json").read_text())
    assert cur["plays"] == 1 and cur["top_tracks"][0]["track_id"] == "new"  # 毎晩上書き


def test_backfill_yearly_wrapped_creates_missing_past_year(tmp_path):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    sitegen._backfill_yearly_wrapped(tmp_path, [_play("2019-10-01T00:00:00Z", "t")], now)
    assert json.loads((tmp_path / "wrapped" / "2019.json").read_text())["plays"] == 1


def test_load_history_extra_reads_short_plays(tmp_path):
    hd = tmp_path / "history"
    hd.mkdir()
    (hd / "extra.json").write_text(json.dumps({"min_ms": 30000, "short_plays": {"t1": 4}}))
    assert sitegen._load_history_extra(hd) == {"t1": 4}


def test_load_history_extra_tolerates_missing_or_broken(tmp_path):
    assert sitegen._load_history_extra(tmp_path / "nope") == {}
    hd = tmp_path / "history"
    hd.mkdir()
    (hd / "extra.json").write_text("{ broken")
    assert sitegen._load_history_extra(hd) == {}


def test_known_artist_ids_from_playlist_records():
    recs = [{"id": "t", "artists": [{"name": "Queen", "id": "Q"}, {"name": "NoId"}]}]
    assert sitegen.known_artist_ids(recs) == {"queen": "Q"}


class _FakeSp:
    """build_artist_meta 用のスタブ。呼ばれた回数と引数を記録する。"""

    def __init__(self, search_hit=None):
        self.searched: list[str] = []
        self.artist_batches: list[list[str]] = []
        self._search_hit = search_hit or {}

    def search(self, q, type, limit):  # noqa: A002 — spotipy の実シグネチャに合わせる
        self.searched.append(q)
        hit = self._search_hit.get(q)
        return {"artists": {"items": [hit] if hit else []}}

    def artists(self, ids):
        self.artist_batches.append(list(ids))
        return {"artists": [
            {"id": i, "name": f"name-{i}",
             # Spotify は大きい順に 640/320/160 を返す。サムネには中央を使う。
             "images": [{"url": f"big-{i}"}, {"url": f"mid-{i}"}, {"url": f"small-{i}"}],
             "genres": ["g1", "g2", "g3", "g4"], "followers": {"total": 7}}
            for i in ids
        ]}


def test_build_artist_meta_resolves_ids_from_playlists_without_searching():
    sp = _FakeSp()
    pl = [{"artists": [{"name": "Queen", "id": "Q"}]}]
    meta = sitegen.build_artist_meta(sp, ["Queen"], pl, {})
    assert sp.searched == []                 # 在籍曲から引けるものは検索しない（API の無駄打ち防止）
    assert meta["queen"]["id"] == "Q"
    assert meta["queen"]["image"] == "mid-Q"  # images の中央（大きすぎない版）
    assert meta["queen"]["genres"] == ["g1", "g2", "g3"]  # 3件までに切る
    assert meta["queen"]["followers"] == 7


def test_build_artist_meta_skips_already_cached_artists():
    sp = _FakeSp()
    existing = {"queen": {"name": "Queen", "id": "Q", "image": "cached"}}
    meta = sitegen.build_artist_meta(sp, ["Queen"], [{"artists": [{"name": "Queen", "id": "Q"}]}], existing)
    # 画像まで揃っている人は再取得しない＝毎晩の API 消費が増えない
    assert sp.artist_batches == [] and meta["queen"]["image"] == "cached"


def test_build_artist_meta_searches_only_unresolved_and_respects_budget():
    sp = _FakeSp(search_hit={"B": {"id": "IB", "name": "B"}, "C": {"id": "IC", "name": "C"}})
    meta = sitegen.build_artist_meta(sp, ["A", "B", "C"], [{"artists": [{"name": "A", "id": "IA"}]}], {},
                                     search_budget=1)
    assert sp.searched == ["B"]          # A は在籍から解決、予算1なので B だけ検索
    assert meta["b"]["id"] == "IB"
    assert "c" not in meta               # 予算切れは翌晩に回す（積み残しても壊れない）


def test_build_artist_meta_rejects_mismatched_search_result():
    # 検索1位が別人（部分一致の誤爆）なら採用しない。誤った画像を出さないため。
    sp = _FakeSp(search_hit={"The Beat": {"id": "WRONG", "name": "The Beatles"}})
    meta = sitegen.build_artist_meta(sp, ["The Beat"], [], {})
    assert "the beat" not in meta


def test_build_artist_meta_batches_by_fifty():
    sp = _FakeSp()
    names = [f"a{i}" for i in range(120)]
    pl = [{"artists": [{"name": n, "id": f"id-{n}"} for n in names]}]
    sitegen.build_artist_meta(sp, names, pl, {})
    assert [len(b) for b in sp.artist_batches] == [50, 50, 20]  # /v1/artists の上限は50


def test_build_artist_meta_survives_api_failure():
    class _Boom(_FakeSp):
        def artists(self, ids):
            raise RuntimeError("503")

    meta = sitegen.build_artist_meta(_Boom(), ["Queen"], [{"artists": [{"name": "Queen", "id": "Q"}]}], {})
    assert meta["queen"]["id"] == "Q" and "image" not in meta["queen"]  # ID だけ残り翌晩リトライ
