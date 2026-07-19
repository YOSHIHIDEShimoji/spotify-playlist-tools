import logging

import core
import dedupe
import dedupe_auto
import sitegen
import siteops

_LOG = logging.getLogger("test")


def _rec(tid, name, isrc, album_type="album", dur=200000, pop=50):
    return {
        "id": tid, "name": name, "artists": [{"id": "art", "name": "Art"}],
        "isrc": isrc, "album": {"name": "Alb", "album_type": album_type, "release_date": "2020-01-01"},
        "duration_ms": dur, "popularity": pop, "playlists": [{"id": "pW", "name": "W"}],
    }


def _setup(monkeypatch, tmp_path, records, *, dry=False, apply_undo="UNDO1"):
    """dedupe_auto.main() を隔離実行する土台。返り値 (summaries, applied) を検査する。"""
    summaries: dict = {}
    applied: list = []
    monkeypatch.setattr(core, "build_client", lambda scope: object())
    monkeypatch.setattr(dedupe, "managed_playlists", lambda: [{"id": "pW", "name": "W"}])
    monkeypatch.setattr(dedupe, "collect_records", lambda sp, pls: (records, {}))
    monkeypatch.setattr(core, "write_step_summary", lambda name, data: summaries.__setitem__(name, data))

    def fake_apply(sp, data, removals, op_name, logger):
        applied.append({"removals": removals, "op": op_name})
        return apply_undo

    monkeypatch.setattr(siteops, "_apply_removals", fake_apply)
    monkeypatch.setattr("sys.argv", ["dedupe_auto.py", "--data-dir", str(tmp_path)])
    if dry:
        monkeypatch.setenv("DRY_RUN", "1")
    else:
        monkeypatch.delenv("DRY_RUN", raising=False)
    return summaries, applied


def test_dedupe_auto_applies_and_injects_undo_id(monkeypatch, tmp_path):
    recs = [_rec("a", "Song", "GB1", "album"), _rec("b", "Song", "GB1", "single")]
    summaries, applied = _setup(monkeypatch, tmp_path, recs)
    assert dedupe_auto.main() == core.EXIT_OK
    assert len(applied) == 1 and [r["track_id"] for r in applied[0]["removals"]] == ["b"]
    assert applied[0]["op"] == "dedupe-auto"
    s = summaries["dedupe"]
    assert s["deleted"] == 1 and s["groups"] == 1
    assert s["changes"][0]["undo_id"] == "UNDO1"        # 削除後に undo_id を注入
    # 書いたサマリがそのまま sitegen のホーム表示に載る（書き手↔読み手の契約）
    rr = sitegen.build_run_record({"dedupe": s}, 1, "2026-07-19", False)
    assert rr["steps"]["dedupe"]["deleted"] == 1
    assert rr["detail"]["dedupe"][0]["undo_id"] == "UNDO1"


def test_dedupe_auto_dry_run_calls_no_write_api(monkeypatch, tmp_path):
    recs = [_rec("a", "Song", "GB1", "album"), _rec("b", "Song", "GB1", "single")]
    summaries, applied = _setup(monkeypatch, tmp_path, recs, dry=True)
    assert dedupe_auto.main() == core.EXIT_OK
    assert applied == []                                 # 変更系 API を1回も呼ばない
    assert summaries["dedupe"]["dry_run"] is True and summaries["dedupe"]["deleted"] == 1


def test_dedupe_auto_no_targets_writes_zero(monkeypatch, tmp_path):
    recs = [_rec("a", "Song", "GB1", "album"), _rec("b", "Other", "ZZ9", "single")]  # 別曲・別ISRC
    summaries, applied = _setup(monkeypatch, tmp_path, recs)
    assert dedupe_auto.main() == core.EXIT_OK
    assert applied == [] and summaries["dedupe"] == {"deleted": 0, "groups": 0, "changes": []}


def test_dedupe_auto_auth_failure_skips_without_crashing(monkeypatch, tmp_path):
    summaries, applied = _setup(monkeypatch, tmp_path, [])

    def raise_auth(scope):
        raise core.AuthRequired("expired")

    monkeypatch.setattr(core, "build_client", raise_auth)
    assert dedupe_auto.main() == core.EXIT_OK             # nightly を落とさない
    assert applied == [] and summaries["dedupe"]["skipped"] == "auth"


def test_dedupe_auto_scan_error_skips_without_crashing(monkeypatch, tmp_path):
    summaries, applied = _setup(monkeypatch, tmp_path, [])

    def boom(sp, pls):
        raise RuntimeError("api down")

    monkeypatch.setattr(dedupe, "collect_records", boom)
    assert dedupe_auto.main() == core.EXIT_OK
    assert applied == [] and summaries["dedupe"]["skipped"] == "error"


def test_dedupe_auto_fails_closed_on_broken_keep_file(monkeypatch, tmp_path):
    # 保留ファイルが壊れていたら「両方残す」を保証できない → 何も消さずスキップ
    (tmp_path / "dedupe_keep.json").write_text("{ broken")
    recs = [_rec("a", "Song", "GB1", "album"), _rec("b", "Song", "GB1", "single")]
    summaries, applied = _setup(monkeypatch, tmp_path, recs)
    assert dedupe_auto.main() == core.EXIT_OK
    assert applied == [] and summaries["dedupe"]["skipped"] == "keep_error"
