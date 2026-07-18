import json
import logging

import pytest

import dedupe
import siteops

_LOG = logging.getLogger("test")


class _FakeSp:
    """プレイリスト内容を辞書で持つ最小フェイク（op 実行系の回帰テスト用・L-C）。"""

    def __init__(self, playlists):
        self.playlists = {pid: list(tracks) for pid, tracks in playlists.items()}

    def playlist_items(self, pid, fields=None, additional_types=None, limit=100):
        return {"items": [{"track": t} for t in self.playlists.get(pid, [])], "next": None}

    def next(self, _result):
        return None

    def playlist_remove_all_occurrences_of_items(self, pid, uris):
        ids = {u.split(":")[-1] for u in uris}
        self.playlists[pid] = [t for t in self.playlists.get(pid, []) if t["id"] not in ids]

    def playlist_add_items(self, pid, ids):
        for tid in ids:
            self.playlists.setdefault(pid, []).append(_mk(tid, "ZZ0"))

    def playlist(self, pid, fields=None):
        return {"snapshot_id": f"snap-{pid}"}

    def playlist_remove_specific_occurrences_of_items(self, pid, items, snapshot_id=None):
        remove_idx = {pos for it in items for pos in it.get("positions", [])}
        self.playlists[pid] = [t for i, t in enumerate(self.playlists.get(pid, [])) if i not in remove_idx]


def _mk(tid, isrc):
    return {"id": tid, "name": "Song", "artists": [{"id": "art", "name": "Art"}],
            "external_ids": {"isrc": isrc}, "duration_ms": 1000, "popularity": 50,
            "album": {"name": "Al", "album_type": "album", "release_date": "2020-01-01"}}


def _seed_dupes(tmp_path):
    group = {"id": "g-x", "tier": "B", "reason": "isrc", "tracks": [
        {"id": "a", "name": "Song", "playlists": [{"id": "pW", "name": "W"}]},
        {"id": "b", "name": "Song", "playlists": [{"id": "pW", "name": "W"}]},  # snapshot は pW のみ
    ]}
    (tmp_path / "dupes.json").write_text(json.dumps({"counts": {"A": 0, "B": 1, "C": 0}, "groups": [group]}))


def test_op_dedupe_apply_and_undo_end_to_end(tmp_path, monkeypatch):
    # b は a の重複。live では b が pW と pAP の両方に在籍（snapshot は pW のみ）
    sp = _FakeSp({"pW": [_mk("a", "GB1"), _mk("b", "GB1")], "pAP": [_mk("b", "GB1")]})
    monkeypatch.setattr(dedupe, "managed_playlists",
                        lambda: [{"id": "pW", "name": "W"}, {"id": "pAP", "name": "AP"}])
    _seed_dupes(tmp_path)

    siteops.op_dedupe_apply(sp, tmp_path, {"decisions": [{"group_id": "g-x", "keep": ["a"], "remove": ["b"]}]}, _LOG)

    # H1: b は全管理 PL（pW/pAP）から消え、keep 側 a は残る
    assert all(t["id"] != "b" for t in sp.playlists["pW"])
    assert all(t["id"] != "b" for t in sp.playlists["pAP"])
    assert any(t["id"] == "a" for t in sp.playlists["pW"])

    # M-3: undo レコードは live 在籍（pW+pAP）を記録
    undo_files = list((tmp_path / "undo").glob("*.json"))
    assert len(undo_files) == 1
    rec = json.loads(undo_files[0].read_text())
    assert set(rec["removed"][0]["playlists"]) == {"pW", "pAP"}

    # H-1: undo_index に即時反映
    idx = json.loads((tmp_path / "undo_index.json").read_text())
    assert len(idx["entries"]) == 1 and idx["entries"][0]["id"] == rec["id"]

    # undo: b が両 PL へ復活し、undo ファイルは .done 化
    siteops.op_undo(sp, tmp_path, {"undo_id": rec["id"]}, _LOG)
    assert any(t["id"] == "b" for t in sp.playlists["pW"])
    assert any(t["id"] == "b" for t in sp.playlists["pAP"])
    assert not undo_files[0].exists()
    assert (tmp_path / "undo" / f"{rec['id']}.done").exists()
    # 二重 undo は拒否
    with pytest.raises(siteops.OpError):
        siteops.op_undo(sp, tmp_path, {"undo_id": rec["id"]}, _LOG)


def test_op_dedupe_trim_keeps_one_and_is_undoable(tmp_path, monkeypatch):
    # pW に同じ曲 x が3回・別曲 y が1回。trim すると x は1つだけ残り、y は無傷。
    sp = _FakeSp({"pW": [_mk("x", "GBX"), _mk("y", "GBY"), _mk("x", "GBX"), _mk("x", "GBX")]})
    monkeypatch.setattr(dedupe, "managed_playlists", lambda: [{"id": "pW", "name": "W"}])
    dupes = {"counts": {"A": 1, "B": 0, "C": 0}, "groups": [
        {"id": "g-A", "tier": "A", "reason": "same-id-in-playlist",
         "track": {"id": "x", "name": "Dup"}, "playlist": {"id": "pW", "name": "W"}, "count": 3}]}
    (tmp_path / "dupes.json").write_text(json.dumps(dupes))

    siteops.op_dedupe_trim(sp, tmp_path, {"group_id": "g-A"}, _LOG)

    assert len([t for t in sp.playlists["pW"] if t["id"] == "x"]) == 1  # 1つだけ残す
    assert any(t["id"] == "y" for t in sp.playlists["pW"])              # 別曲には触れない

    # undo は削除した2個ぶんの再追加を記録（playlists を個数ぶん列挙）
    rec = json.loads(next((tmp_path / "undo").glob("*.json")).read_text())
    assert rec["op"] == "dedupe-trim"
    assert rec["removed"][0]["playlists"] == ["pW", "pW"]

    # undo で x が3つに戻る
    siteops.op_undo(sp, tmp_path, {"undo_id": rec["id"]}, _LOG)
    assert len([t for t in sp.playlists["pW"] if t["id"] == "x"]) == 3


def test_op_dedupe_trim_rejects_non_tier_a(tmp_path):
    sp = _FakeSp({})
    (tmp_path / "dupes.json").write_text(
        json.dumps({"groups": [{"id": "g-1", "tier": "B", "tracks": []}]})
    )
    with pytest.raises(siteops.OpError):
        siteops.op_dedupe_trim(sp, tmp_path, {"group_id": "g-1"}, _LOG)


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


def test_plan_dedupe_rejects_duplicate_group_decisions():
    # レビュー C3 の回帰: 同一グループへの矛盾決定を渡すと全曲削除されるのを防ぐ
    with pytest.raises(siteops.OpError):
        siteops.plan_dedupe(
            _dupes(),
            [{"group_id": "g-1", "keep": ["a"], "remove": ["b"]},
             {"group_id": "g-1", "keep": ["b"], "remove": ["a"]}],
        )


def test_plan_classify_valid_and_rejects():
    unknown = {"tracks": [{"id": "t1", "name": "n", "artists": ["A"], "isrc": ""}]}
    ok = siteops.plan_classify(unknown, [{"track_id": "t1", "class": "japanese"}])
    assert ok == [{"track_id": "t1", "class": "japanese"}]
    with pytest.raises(siteops.OpError):
        siteops.plan_classify(unknown, [{"track_id": "ghost", "class": "japanese"}])
    with pytest.raises(siteops.OpError):
        siteops.plan_classify(unknown, [{"track_id": "t1", "class": "bad"}])


def _write_dupes(tmp_path):
    (tmp_path / "dupes.json").write_text(json.dumps({
        "counts": {"A": 0, "B": 1, "C": 0},
        "groups": [{"id": "g-1", "tier": "B", "reason": "isrc",
                    "tracks": [{"id": "a"}, {"id": "b"}]}],
    }))


def test_op_keep_apply_validates_and_updates_dupes(tmp_path, monkeypatch):
    # add は Spotify を触らない（sp 未使用）ので orchestration をそのまま検証できる
    _write_dupes(tmp_path)
    siteops.op_keep_apply(None, tmp_path, {"add": [{"group_id": "g-1", "track_ids": ["a", "b"]}], "remove": []}, _LOG)
    kept = json.loads((tmp_path / "dedupe_keep.json").read_text())["groups"][0]
    assert kept["group_id"] == "g-1"
    assert {t["id"] for t in kept["tracks"]} == {"a", "b"}  # 保留タブ表示用のスナップショット
    # dupes.json から即時に消える（M-4）
    dupes = json.loads((tmp_path / "dupes.json").read_text())
    assert dupes["groups"] == [] and dupes["counts"]["B"] == 0
    # remove（保留を戻す）は再スキャンで dupes を作り直す。scan 自体は別テストなのでここは呼び出しを検証。
    called = {}
    monkeypatch.setattr(siteops, "_regenerate_dupes", lambda sp, data: called.setdefault("ran", True))
    siteops.op_keep_apply(_FakeSp({}), tmp_path, {"add": [], "remove": ["g-1"]}, _LOG)
    assert json.loads((tmp_path / "dedupe_keep.json").read_text())["groups"] == []
    assert called.get("ran") is True  # 除外解除→再スキャンで元の重複グループが復活する


def test_op_keep_apply_rejects_mismatched_track_ids(tmp_path):
    _write_dupes(tmp_path)
    with pytest.raises(siteops.OpError):
        siteops.op_keep_apply(None, tmp_path, {"add": [{"group_id": "g-1", "track_ids": ["a"]}], "remove": []}, _LOG)
    with pytest.raises(siteops.OpError):
        siteops.op_keep_apply(None, tmp_path, {"add": [{"group_id": "nope", "track_ids": ["a", "b"]}], "remove": []}, _LOG)
