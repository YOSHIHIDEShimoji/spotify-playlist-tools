import core
import lastfm_log


def _track(name, artist, uts, *, mbid="", images=None, album="Al"):
    """getRecentTracks の track 要素（date あり＝確定 scrobble）を組む。"""
    return {
        "name": name,
        "artist": {"#text": artist},
        "album": {"#text": album},
        "mbid": mbid,
        "image": images or [{"size": "large", "#text": f"http://img/{name}.jpg"}],
        "date": {"uts": str(uts), "#text": "x"},
    }


def _nowplaying(name, artist):
    t = _track(name, artist, 0)
    t.pop("date")
    t["@attr"] = {"nowplaying": "true"}
    return t


def test_image_prefers_larger():
    imgs = [{"size": "small", "#text": "s"}, {"size": "large", "#text": "l"}, {"size": "extralarge", "#text": "xl"}]
    assert lastfm_log._image(imgs) == "xl"
    assert lastfm_log._image([{"size": "small", "#text": "s"}]) == "s"
    assert lastfm_log._image([]) is None


def test_to_record_skips_nowplaying_and_maps_fields():
    assert lastfm_log._to_record(_nowplaying("N", "A")) is None
    rec = lastfm_log._to_record(_track("STAY", "The Kid LAROI", 1_700_000_000))
    assert rec["uts"] == 1_700_000_000
    assert rec["name"] == "STAY" and rec["artist"] == "The Kid LAROI"
    assert rec["image"] == "http://img/STAY.jpg"
    assert rec["played_at"].startswith("2023-11-14T")  # uts→UTC ISO


def test_append_records_dedupes_by_uts(tmp_path):
    scr = tmp_path / "scrobbles"
    scr.mkdir()
    recs = [lastfm_log._to_record(_track("A", "X", 1_752_900_000)),
            lastfm_log._to_record(_track("B", "Y", 1_752_900_100))]
    assert lastfm_log.append_records(scr, recs) == 2
    # 同じ uts を含めて再追記 → 新規1件だけ
    again = recs + [lastfm_log._to_record(_track("C", "Z", 1_752_900_200))]
    assert lastfm_log.append_records(scr, again) == 1
    total = sum(len(core.read_jsonl(p)) for p in scr.glob("*.jsonl"))
    assert total == 3


def test_poll_filters_by_cursor(monkeypatch):
    page = {"recenttracks": {
        "@attr": {"page": "1", "totalPages": "1"},
        "track": [
            _nowplaying("Now", "A"),                 # スキップ
            _track("New", "A", 300),                 # cursor 超 → 採用
            _track("Old", "A", 100),                 # cursor 以下 → 除外
        ],
    }}
    monkeypatch.setattr(lastfm_log, "_api", lambda method, **p: page)
    records, cursor = lastfm_log.poll("shimoji_", cursor=200)
    names = [r["name"] for r in records]
    assert names == ["New"] and cursor == 300
