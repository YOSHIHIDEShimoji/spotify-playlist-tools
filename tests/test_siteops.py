import json
import logging

import pytest

import siteops

_LOG = logging.getLogger("test")


def _dupes():
    return {
        "groups": [
            {
                "id": "g-1", "tier": "B", "reason": "isrc",
                "tracks": [
                    {"id": "a", "name": "Song", "playlists": [{"id": "pW", "name": "W"}, {"id": "pE", "name": "Ed"}]},
                    {"id": "b", "name": "Song", "playlists": [{"id": "pW", "name": "W"}]},
                ],
            },
            {"id": "g-A", "tier": "A", "reason": "same-id-in-playlist",
             "track": {"id": "x", "name": "Dup"}, "playlist": {"id": "pW", "name": "W"}, "count": 2},
        ]
    }


def test_plan_dedupe_valid_returns_removals_with_all_playlists():
    removals = siteops.plan_dedupe(_dupes(), [{"group_id": "g-1", "keep": ["a"], "remove": ["b"]}])
    assert len(removals) == 1
    assert removals[0]["track_id"] == "b"
    assert removals[0]["playlists"] == ["pW"]


def test_plan_dedupe_removes_from_all_occurrences():
    # a を削除すると全出現（pW, pE）が対象になる（sync 整合の核）
    removals = siteops.plan_dedupe(_dupes(), [{"group_id": "g-1", "keep": ["b"], "remove": ["a"]}])
    assert removals[0]["playlists"] == ["pW", "pE"]


def test_plan_dedupe_rejects_unknown_group():
    with pytest.raises(siteops.OpError):
        siteops.plan_dedupe(_dupes(), [{"group_id": "nope", "keep": ["a"], "remove": ["b"]}])


def test_plan_dedupe_rejects_member_mismatch():
    # remove に無関係な id → keep∪remove がグループ構成と一致しない
    with pytest.raises(siteops.OpError):
        siteops.plan_dedupe(_dupes(), [{"group_id": "g-1", "keep": ["a"], "remove": ["zzz"]}])


def test_plan_dedupe_rejects_empty_keep_or_remove():
    with pytest.raises(siteops.OpError):
        siteops.plan_dedupe(_dupes(), [{"group_id": "g-1", "keep": ["a", "b"], "remove": []}])


def test_plan_dedupe_rejects_overlap():
    with pytest.raises(siteops.OpError):
        siteops.plan_dedupe(_dupes(), [{"group_id": "g-1", "keep": ["a", "b"], "remove": ["a"]}])


def test_plan_dedupe_rejects_tier_a():
    with pytest.raises(siteops.OpError):
        siteops.plan_dedupe(_dupes(), [{"group_id": "g-A", "keep": ["x"], "remove": ["x"]}])


def test_plan_classify_valid_and_rejects():
    unknown = {"tracks": [{"id": "t1", "name": "n", "artists": ["A"], "isrc": ""}]}
    ok = siteops.plan_classify(unknown, [{"track_id": "t1", "class": "japanese"}])
    assert ok == [{"track_id": "t1", "class": "japanese"}]
    with pytest.raises(siteops.OpError):
        siteops.plan_classify(unknown, [{"track_id": "ghost", "class": "japanese"}])
    with pytest.raises(siteops.OpError):
        siteops.plan_classify(unknown, [{"track_id": "t1", "class": "bad"}])


def test_op_keep_apply_writes_file(tmp_path):
    # keep-apply は Spotify を触らない（sp 未使用）ので orchestration をそのまま検証できる
    siteops.op_keep_apply(None, tmp_path, {"add": [{"group_id": "g-1", "track_ids": ["a", "b"]}], "remove": []}, _LOG)
    data = json.loads((tmp_path / "dedupe_keep.json").read_text())
    assert data["groups"][0]["group_id"] == "g-1"
    # remove で消える
    siteops.op_keep_apply(None, tmp_path, {"add": [], "remove": ["g-1"]}, _LOG)
    assert json.loads((tmp_path / "dedupe_keep.json").read_text())["groups"] == []
