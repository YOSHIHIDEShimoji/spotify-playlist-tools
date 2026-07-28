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


# ─────────────────────── 自動整理（同一録音のみ・安全側） ───────────────────────

def _arec(tid, name, isrc, album_type="album", dur=200000, pop=50, npl=1):
    r = _rec(tid, name, "art1", isrc, album_type)
    r["duration_ms"] = dur
    r["popularity"] = pop
    r["playlists"] = [{"id": f"p{i}", "name": "W"} for i in range(npl)]
    return r


def test_auto_select_keeps_album_over_single():
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "album"),
        _arec("b", "Song", "GB1", "single"),
    ])
    removals, changes = dedupe.auto_select(groups)
    assert [r["track_id"] for r in removals] == ["b"]          # single を消す
    assert changes[0]["kept"]["album_type"] == "album"
    assert changes[0]["removed"][0]["album_type"] == "single"


def test_auto_select_rank_album_single_compilation():
    # album > single > compilation（本人確定）。3版なら album を残し single/compilation を消す
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "compilation"),
        _arec("b", "Song", "GB1", "single"),
        _arec("c", "Song", "GB1", "album"),
    ])
    removals, changes = dedupe.auto_select(groups)
    assert {r["track_id"] for r in removals} == {"a", "b"}  # album(c) だけ残る
    assert changes[0]["kept"]["album_type"] == "album"


def test_auto_select_single_over_compilation_when_no_album():
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "compilation"),
        _arec("b", "Song", "GB1", "single"),
    ])
    removals, _ = dedupe.auto_select(groups)
    assert [r["track_id"] for r in removals] == ["a"]          # compilation を消し single を残す


def test_auto_select_never_touches_tier_c():
    # 別 ISRC（Tier C・別バージョン候補）は自動対象外
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1"),
        _arec("b", "Song", "ZZ9"),   # 別 ISRC → Tier C
    ])
    removals, changes = dedupe.auto_select(groups)
    assert removals == [] and changes == []


def test_auto_select_excludes_large_duration_delta():
    # 同一 ISRC でも秒数差 >3s は別編集の疑いで手動へ落とす
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "album", dur=200000),
        _arec("b", "Song", "GB1", "single", dur=205000),  # Δ5s
    ])
    assert dedupe.auto_select(groups) == ([], [])


def test_auto_select_excludes_version_word_difference():
    # 同一 ISRC でもタイトルの版差語（feat）が食い違えば手動へ（『feat は俺が見たい』）
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "album"),
        _arec("b", "Song (feat. X)", "GB1", "single"),
    ])
    assert dedupe.auto_select(groups) == ([], [])


def test_auto_select_respects_keep_sets():
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "album"),
        _arec("b", "Song", "GB1", "single"),
    ])
    keep = {frozenset({"a", "b"})}
    assert dedupe.auto_select(groups, keep) == ([], [])


def test_auto_select_album_tie_breaks_on_popularity():
    # album 同士（同一 ISRC）は人気度が高い方を残す
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "album", pop=40),
        _arec("b", "Song", "GB1", "album", pop=90),
    ])
    removals, _ = dedupe.auto_select(groups)
    assert [r["track_id"] for r in removals] == ["a"]          # 人気の低い a を消す


def test_auto_select_keep_superset_protects_kept_track():
    # 回帰（監査 blocking）: keep 済みペア {a,b} に同一 ISRC の3枚目 c が加わっても、
    # keep したトラックを含むグループには自動で触れない（完全一致だと b が消えていた）
    groups = dedupe.build_groups([
        _arec("a", "Song", "GB1", "album"),
        _arec("b", "Song", "GB1", "single"),
        _arec("c", "Song", "GB1", "compilation"),
    ])
    removals, changes = dedupe.auto_select(groups, {frozenset({"a", "b"})})
    assert removals == [] and changes == []


def test_auto_select_keep_partial_overlap_protects():
    # keep セットと1トラックでも交差すれば除外（部分交差でも保護）
    groups = dedupe.build_groups([_arec("a", "Song", "GB1", "album"), _arec("b", "Song", "GB1", "single")])
    assert dedupe.auto_select(groups, {frozenset({"b", "zzz"})}) == ([], [])


def test_auto_select_empty_isrc_pair_is_not_auto():
    # ISRC 未付与の別録音同士（タイトル一致）は tier C 扱い ＝ 自動対象外（安全ゲート）
    groups = dedupe.build_groups([_arec("a", "Song", ""), _arec("b", "Song", "")])
    assert all(g["tier"] != "B" for g in groups)
    assert dedupe.auto_select(groups) == ([], [])


def test_auto_select_duration_delta_boundary():
    # 境界: Δ=3000ms ちょうどは自動対象、Δ=3001ms は対象外
    ok = dedupe.build_groups([_arec("a", "Song", "GB1", "album", dur=200000),
                              _arec("b", "Song", "GB1", "single", dur=203000)])
    assert [r["track_id"] for r in dedupe.auto_select(ok)[0]] == ["b"]
    ng = dedupe.build_groups([_arec("a", "Song", "GB1", "album", dur=200000),
                              _arec("b", "Song", "GB1", "single", dur=203001)])
    assert dedupe.auto_select(ng) == ([], [])


def test_auto_select_excludes_when_duration_unknown():
    g = dedupe.build_groups([_arec("a", "Song", "GB1", "album", dur=None),
                             _arec("b", "Song", "GB1", "single")])
    assert dedupe.auto_select(g) == ([], [])


def test_auto_select_tiebreak_prefers_more_playlists_then_id():
    # album・popularity 同点なら在籍数が多い方を残す（全PL一括削除で曲消滅を防ぐ・§3.3）
    g = dedupe.build_groups([_arec("a", "Song", "GB1", "album", pop=50, npl=1),
                             _arec("b", "Song", "GB1", "album", pop=50, npl=3)])
    assert [r["track_id"] for r in dedupe.auto_select(g)[0]] == ["a"]  # 在籍1の a を消す
    # 全キー同点なら id 昇順の先頭を残す（決定論）
    g2 = dedupe.build_groups([_arec("b", "Song", "GB1", "album"), _arec("a", "Song", "GB1", "album")])
    assert [r["track_id"] for r in dedupe.auto_select(g2)[0]] == ["b"]  # a を残し b を消す


def test_version_tokens_same_marker_both_is_auto():
    # 両方に同じ版差語（feat 同士）なら「差」は無い ＝ 自動対象（仕様の明文化）
    g = dedupe.build_groups([_arec("a", "Song (feat. X)", "GB1", "album"),
                             _arec("b", "Song (feat. X)", "GB1", "single")])
    assert [r["track_id"] for r in dedupe.auto_select(g)[0]] == ["b"]


def test_version_tokens_normalizes_fullwidth_and_case():
    assert dedupe._version_tokens("Song (FEAT. X)") == dedupe._version_tokens("song (feat. x)")
    assert "feat" in dedupe._version_tokens("Song（Ｆｅａｔ. Ｘ）")  # NFKC 全角


def test_auto_select_aggregates_multiple_groups():
    # 適格・不適格・適格の3グループ混在で、適格ぶんだけ積み上がる
    groups = dedupe.build_groups([
        _arec("a1", "AAA", "IS1", "album"), _arec("a2", "AAA", "IS1", "single"),   # 適格
        _arec("b1", "BBB", "IS2", "album"), _arec("b2", "BBB", "ZZ9", "single"),   # tier C（不適格）
        _arec("c1", "CCC", "IS3", "album"), _arec("c2", "CCC", "IS3", "single"),   # 適格
    ])
    removals, changes = dedupe.auto_select(groups)
    assert len(changes) == 2
    assert {r["track_id"] for r in removals} == {"a2", "c2"}


def test_keep_file_readable(tmp_path):
    assert dedupe.keep_file_readable(tmp_path) is True           # 無い＝正常
    (tmp_path / "dedupe_keep.json").write_text('{"groups": []}')
    assert dedupe.keep_file_readable(tmp_path) is True           # 妥当な JSON
    (tmp_path / "dedupe_keep.json").write_text("{ broken")
    assert dedupe.keep_file_readable(tmp_path) is False          # 壊れている
