import dedupe


def test_normalize_title_strips_version_markers():
    assert dedupe.normalize_title("Love Story (Taylor's Version)") == "love story"
    assert dedupe.normalize_title("Photograph - Remastered 2011") == "photograph"
    assert dedupe.normalize_title("Shape of You (feat. Someone)") == "shape of you"
    assert dedupe.normalize_title("Yellow - Live") == "yellow"
    # バージョン表記でない括弧は残す
    assert dedupe.normalize_title("Song (Part 2)") == "song (part 2)"
    assert dedupe.normalize_title("Intro") == "intro"


def test_normalize_title_word_boundary_no_false_strip():
    # レビュー C1 の回帰: 部分一致で別曲を潰さないこと（deliver⊃live, left⊃ft, demons⊃demo, alive⊃live）
    assert dedupe.normalize_title("Money - Deliver Us") == "money - deliver us"
    assert dedupe.normalize_title("Money - Left Behind") == "money - left behind"
    assert dedupe.normalize_title("Song (Alive)") == "song (alive)"
    assert dedupe.normalize_title("Song (Demons)") == "song (demons)"
    # 別曲なので正規化後も別物のまま
    assert dedupe.normalize_title("Money - Deliver Us") != dedupe.normalize_title("Money - Left Behind")


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


def test_tier_c_when_isrc_mixed_transitive_merge():
    # レビュー C2 の回帰: ISRC ペア(a,b) にタイトル一致で c(別ISRC) が推移併合された成分は
    # tier B ではなく C（別録音を「同一録音・ほぼ確実」と偽らない）
    records = [
        _rec("a", "Photograph", "art1", "GB1"),
        _rec("b", "Photograph", "art1", "GB1"),          # a と同一 ISRC
        _rec("c", "Photograph", "art1", "ZZ9"),          # 別 ISRC・タイトル一致で引き込まれる
    ]
    groups = dedupe.build_groups(records)
    assert len(groups) == 1
    assert groups[0]["tier"] == "C"
    assert {t["id"] for t in groups[0]["tracks"]} == {"a", "b", "c"}


def test_pure_isrc_group_is_tier_b():
    records = [_rec("a", "Song", "art1", "GB1"), _rec("b", "Song", "art1", "GB1")]
    groups = dedupe.build_groups(records)
    assert groups[0]["tier"] == "B"


def test_dupes_from_records_excludes_kept_groups():
    # レビュー H2 の回帰: keep_sets に入ったグループはスキャン結果から除外
    records = [_rec("a", "Song", "art1", "US1"), _rec("b", "Song", "art1", "US2")]
    plain = dedupe.dupes_from_records(records, {})
    assert len(plain["groups"]) == 1
    kept = dedupe.dupes_from_records(records, {}, {frozenset({"a", "b"})})
    assert kept["groups"] == []


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
