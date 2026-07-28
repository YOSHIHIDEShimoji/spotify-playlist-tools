import gzip
import json

import import_history


def _ev(name="Song", uri="spotify:track:abc123", ms=60000, ts="2021-03-04T05:06:07Z", artist="A"):
    return {
        "ts": ts,
        "ms_played": ms,
        "master_metadata_track_name": name,
        "master_metadata_album_artist_name": artist,
        "spotify_track_uri": uri,
        "ip_addr": "1.2.3.4",  # PII: 出力に絶対残ってはいけない
        "platform": "windows",
    }


def test_iter_play_records_maps_fields_and_strips_pii():
    out = list(import_history.iter_play_records([_ev()]))
    assert out == [
        {
            "track_id": "abc123",
            "name": "Song",
            "artists": [{"name": "A"}],
            "played_at": "2021-03-04T05:06:07Z",
            "ms": 60000,
        }
    ]
    # PII フィールドが混入していないこと（ms は再生時間なので PII ではない）
    assert "ip_addr" not in out[0] and "platform" not in out[0] and "ms_played" not in out[0]


def test_iter_play_records_keeps_per_play_ms():
    # ms は「その再生の実測値」。定数や min_ms で埋めていたらここで落ちる。
    out = list(import_history.iter_play_records([_ev(ms=31000), _ev(ms=245678)]))
    assert [r["ms"] for r in out] == [31000, 245678]


def test_iter_play_records_drops_short_plays():
    # 30秒未満は再生に数えない（境界: 29999 は落ち、30000 は通る）
    assert list(import_history.iter_play_records([_ev(ms=29999)])) == []
    assert len(list(import_history.iter_play_records([_ev(ms=30000)]))) == 1


def test_iter_play_records_skips_podcasts_and_missing_uri():
    # 曲名なし（＝ポッドキャスト/オーディオブック行）は除外
    podcast = {"ts": "2021-01-01T00:00:00Z", "ms_played": 999999, "master_metadata_track_name": None,
               "spotify_track_uri": None, "episode_name": "Ep 1"}
    # track_uri でない（あり得ないが防御）
    ep_uri = _ev(uri="spotify:episode:zzz")
    # uri 欠落
    no_uri = _ev(uri=None)
    assert list(import_history.iter_play_records([podcast, ep_uri, no_uri])) == []


def test_iter_play_records_artist_optional():
    out = list(import_history.iter_play_records([_ev(artist=None)]))
    assert out[0]["artists"] == []


def test_iter_play_records_skips_missing_timestamp():
    # ts 欠落レコードは素通りさせない（後段 write_history の played_at[:4] が TypeError になる連鎖を断つ）
    assert list(import_history.iter_play_records([_ev(ts=None)])) == []


def test_short_play_counts_counts_only_aborted_plays():
    # 完走（>= min_ms）は数えず、途中でやめた再生だけを曲ごとに数える。
    events = [
        _ev(uri="spotify:track:t1", ms=1000),
        _ev(uri="spotify:track:t1", ms=5000),
        _ev(uri="spotify:track:t1", ms=60000),   # 完走 → 数えない
        _ev(uri="spotify:track:t2", ms=0),       # 0ms（開いて即やめた）も短再生
    ]
    assert import_history.short_play_counts(events) == {"t1": 2, "t2": 1}


def test_short_play_counts_boundary_matches_iter_play_records():
    # 30000 は「再生」、29999 は「短再生」。両者の境界が食い違うと完走率がズレる。
    assert import_history.short_play_counts([_ev(ms=29999)]) == {"abc123": 1}
    assert import_history.short_play_counts([_ev(ms=30000)]) == {}


def test_short_play_counts_skips_podcasts_and_unresolvable():
    podcast = {"ts": "2021-01-01T00:00:00Z", "ms_played": 10, "master_metadata_track_name": None,
               "spotify_track_uri": None, "episode_name": "Ep 1"}
    assert import_history.short_play_counts([podcast, _ev(uri="spotify:episode:z", ms=10)]) == {}


def test_write_extra_is_sorted_and_records_threshold(tmp_path):
    import_history.write_extra({"tb": 2, "ta": 1}, tmp_path, min_ms=30000)
    payload = json.loads((tmp_path / "extra.json").read_text(encoding="utf-8"))
    assert payload["min_ms"] == 30000
    assert list(payload["short_plays"].keys()) == ["ta", "tb"]  # 決定論的な差分のため曲IDでソート


def _r(tid, played_at, name="n", ms=60000):
    return {"track_id": tid, "name": name, "artists": [], "played_at": played_at, "ms": ms}


def test_write_history_buckets_by_year_and_folds_duplicates(tmp_path):
    recs = [
        _r("t2", "2020-05-01T00:00:00Z", "B"),
        _r("t1", "2019-05-01T00:00:00Z", "A"),
        _r("t1", "2019-05-01T00:00:00Z", "A"),  # 完全重複（同 played_at + track_id）は畳む
    ]
    counts = import_history.write_history(recs, tmp_path)
    assert counts == {"2019": 1, "2020": 1}
    assert sorted(p.name for p in tmp_path.glob("*.jsonl.gz")) == ["2019.jsonl.gz", "2020.jsonl.gz"]
    with gzip.open(tmp_path / "2019.jsonl.gz", "rt", encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    assert rows == [_r("t1", "2019-05-01T00:00:00Z", "A")]


def test_write_history_keeps_longest_ms_for_duplicate_play(tmp_path):
    # 同一 (played_at, track_id) で ms だけ違う行は、入力順に関わらず長いほうを残す。
    # -ms をソート鍵から外すと勝者が入力順依存になり、再取り込みで出力が揺れる。
    a = _r("t1", "2020-01-01T00:00:00Z", ms=45000)
    b = _r("t1", "2020-01-01T00:00:00Z", ms=180000)
    for order in ([a, b], [b, a]):
        import_history.write_history(order, tmp_path)
        with gzip.open(tmp_path / "2020.jsonl.gz", "rt", encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        assert [r["ms"] for r in rows] == [180000]


def test_write_history_sorts_by_played_at_then_track_id(tmp_path):
    # 年内で played_at 昇順・同 played_at では track_id 昇順に並ぶこと（sorted を外すと落ちる）
    recs = [
        _r("tb", "2020-03-01T00:00:00Z"),
        _r("ta", "2020-01-01T00:00:00Z"),
        _r("t9", "2020-01-01T00:00:00Z"),  # 同時刻 → track_id 昇順で t9 < ta
    ]
    import_history.write_history(recs, tmp_path)
    with gzip.open(tmp_path / "2020.jsonl.gz", "rt", encoding="utf-8") as f:
        order = [json.loads(x)["track_id"] for x in f if x.strip()]
    assert order == ["t9", "ta", "tb"]


def test_write_history_deterministic_regardless_of_input_order(tmp_path):
    # 再エクスポート取り込みでファイル/glob 順が変わってもバイト一致（ソートが決定論の実体）
    recs = [
        _r("tb", "2020-03-01T00:00:00Z"),
        _r("ta", "2020-01-01T00:00:00Z"),
        _r("t9", "2020-01-01T00:00:00Z"),
    ]
    import_history.write_history(recs, tmp_path)
    first = (tmp_path / "2020.jsonl.gz").read_bytes()
    import_history.write_history(list(reversed(recs)), tmp_path)  # 入力順を変えても
    assert (tmp_path / "2020.jsonl.gz").read_bytes() == first


def test_track_id_only_from_track_uri():
    assert import_history._track_id("spotify:track:XYZ") == "XYZ"
    assert import_history._track_id("spotify:episode:XYZ") is None
    assert import_history._track_id(None) is None
    assert import_history._track_id("spotify:track:") is None
