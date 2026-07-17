import dedupe


def test_normalize_title_strips_version_markers():
    assert dedupe.normalize_title("Love Story (Taylor's Version)") == "love story"
    assert dedupe.normalize_title("Photograph - Remastered 2011") == "photograph"
    assert dedupe.normalize_title("Shape of You (feat. Someone)") == "shape of you"
    assert dedupe.normalize_title("Yellow - Live") == "yellow"
    # バージョン表記でない括弧は残す
    assert dedupe.normalize_title("Song (Part 2)") == "song (part 2)"
    assert dedupe.normalize_title("Intro") == "intro"


def _rec(tid, name, artist_id, isrc, album_type="album"):
    return {
        "id": tid,
        "name": name,
        "artists": [{"id": artist_id, "name": artist_id.upper()}],
        "isrc": isrc,
        "album": {"name": "Alb", "album_type": album_type, "release_date": "2020-01-01"},
        "duration_ms": 200000,
        "popularity": 50,
        "playlists": [{"id": "p1", "name": "W"}],
    }


def test_build_groups_isrc_and_title():
    records = [
        _rec("a", "Photograph", "art1", "GB1", "album"),
        _rec("b", "Photograph", "art1", "GB1", "single"),   # 同 ISRC → tier B
        _rec("c", "Love Story", "art2", "US1"),
        _rec("d", "Love Story (Taylor's Version)", "art2", "US2"),  # 別 ISRC・同ベース → tier C
        _rec("e", "Unique", "art3", "ZZ9"),                 # 単独
    ]
    groups = dedupe.build_groups(records)
    assert len(groups) == 2
    tiers = {g["tier"] for g in groups}
    assert tiers == {"B", "C"}
    b = next(g for g in groups if g["tier"] == "B")
    # album 種別が先（推奨が上）
    assert b["tracks"][0]["album_type"] == "album"
    assert {t["id"] for t in b["tracks"]} == {"a", "b"}


def test_make_group_id_stable_regardless_of_order():
    assert dedupe.make_group_id(["b", "a"]) == dedupe.make_group_id(["a", "b"])
    assert dedupe.make_group_id(["a"]).startswith("g-")


def test_build_intra_dupes():
    intra = {
        ("p1", "W", "t1", "Song", ("A",)): 2,
        ("p1", "W", "t2", "Other", ("B",)): 1,
    }
    out = dedupe.build_intra_dupes(intra)
    assert len(out) == 1
    assert out[0]["tier"] == "A"
    assert out[0]["count"] == 2
    assert out[0]["track"]["id"] == "t1"
