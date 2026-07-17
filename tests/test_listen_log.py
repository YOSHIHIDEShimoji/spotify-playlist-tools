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


class _PagedSp:
    """cursor 指定時に before でページングするフェイク。50件×2ページ、その後は空。"""

    def __init__(self):
        # 新しい順（page1 が新しい・page2 が古い）。有効な ISO を生成
        self.page1 = [self._item(f"a{i}", 2, 49 - i) for i in range(50)]  # 02:49→02:00
        self.page2 = [self._item(f"b{i}", 1, 49 - i) for i in range(50)]  # 01:49→01:00
        self.calls = []
        self._page2_served = False

    @staticmethod
    def _item(tid, hour, minute):
        return {"played_at": f"2026-07-15T{hour:02d}:{minute:02d}:00Z",
                "track": {"id": tid, "name": "n", "artists": [], "duration_ms": 1}}

    def current_user_recently_played(self, limit, after=None, before=None):
        self.calls.append({"after": after, "before": before})
        if before is None:
            return {"items": self.page1}
        if not self._page2_served:
            self._page2_served = True
            return {"items": self.page2}
        return {"items": []}


def test_poll_paginates_when_full_page_and_cursor():
    # レビュー H3 の回帰: cursor ありで満杯ページなら before で次ページも読む
    sp = _PagedSp()
    recs, _ = listen_log.poll(sp, cursor=1)  # 小さい cursor → 全件対象
    ids = {r["track_id"] for r in recs}
    assert any(i.startswith("b") for i in ids)  # 2ページ目（古い側）も取得できている
    assert len(recs) == 100  # 取りこぼしなし
    assert sp.calls[0]["after"] == 1 and sp.calls[1]["before"] is not None


def test_poll_first_run_single_page():
    # cursor なし（初回）は1ページで止める（過去掘り起こししない）
    sp = _PagedSp()
    listen_log.poll(sp, cursor=None)
    assert len(sp.calls) == 1


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
