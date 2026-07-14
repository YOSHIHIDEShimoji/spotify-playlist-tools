import sort


def _t(tid, artists, release):
    return {
        "id": tid,
        "name": tid,
        "artists": [{"name": a} for a in artists],
        "album": {"release_date": release},
    }


def test_normalize_date():
    assert sort._normalize_date("2023") == "2023-01-01"
    assert sort._normalize_date("2023-05") == "2023-05-01"
    assert sort._normalize_date("2023-05-09") == "2023-05-09"


def test_sort_by_artist_count_desc_then_name_then_date():
    tracks = [
        _t("solo", ["Zed"], "2020-01-01"),
        _t("big1", ["Alpha"], "2021-06-01"),
        _t("big2", ["Alpha"], "2019-01-01"),
    ]
    order = [t["id"] for t in sort.sort_tracks(tracks)]
    # Alpha は2曲なので先頭。Alpha 内はリリース日昇順（2019 → 2021）
    assert order == ["big2", "big1", "solo"]


def test_collab_track_represented_by_most_frequent_artist():
    tracks = [
        _t("a1", ["Alpha"], "2020-01-01"),
        _t("a2", ["Alpha"], "2020-02-01"),
        _t("collab", ["Beta", "Alpha"], "2018-01-01"),
    ]
    order = [t["id"] for t in sort.sort_tracks(tracks)]
    # collab は Alpha(3回) を代表に選ぶので Alpha 群に入り、日付最古で先頭
    assert order[0] == "collab"
    assert order[1:] == ["a1", "a2"]
