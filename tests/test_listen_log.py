import listen_log


class _FakeSp:
    def __init__(self, items):
        self._items = items
        self.last_after = "unset"

    def current_user_recently_played(self, limit, after=None):
        self.last_after = after
        return {"items": self._items}


def test_poll_maps_items_and_cursor():
    sp = _FakeSp([
        {"played_at": "2026-07-15T02:00:00Z",
         "track": {"id": "t1", "name": "n", "artists": [{"id": "a", "name": "A"}], "duration_ms": 1}},
    ])
    recs, cursor = listen_log.poll(sp, None)
    assert recs[0]["track_id"] == "t1"
    assert recs[0]["artists"] == [{"id": "a", "name": "A"}]
    assert cursor > 0


def test_poll_skips_items_without_id():
    sp = _FakeSp([
        {"played_at": "2026-07-15T02:00:00Z", "track": {"id": None}},
        {"played_at": None, "track": {"id": "t2"}},
    ])
    recs, _ = listen_log.poll(sp, None)
    assert recs == []


def test_append_records_dedup_and_month_bucket(tmp_path):
    d = tmp_path / "listening"
    d.mkdir()
    recs = [{"played_at": "2026-07-15T02:00:00Z", "track_id": "t1",
             "name": "n", "artists": [], "duration_ms": 1}]
    assert listen_log.append_records(d, recs) == 1
    assert listen_log.append_records(d, recs) == 0          # 重複はスキップ
    assert (d / "2026-07.jsonl").exists()                    # JST 月でバケット


def test_append_records_splits_across_months(tmp_path):
    d = tmp_path / "listening"
    d.mkdir()
    recs = [
        {"played_at": "2026-07-31T20:00:00Z", "track_id": "t1", "name": "n", "artists": [], "duration_ms": 1},
        # JST では 2026-08-01 05:00 → 8月ファイルへ
        {"played_at": "2026-08-01T00:00:00Z", "track_id": "t2", "name": "n", "artists": [], "duration_ms": 1},
    ]
    assert listen_log.append_records(d, recs) == 2
    assert (d / "2026-08.jsonl").exists()
