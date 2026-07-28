import json
from datetime import datetime, timedelta
from urllib.error import HTTPError

import core
import upcoming


def _rg(title, date, typ="Album"):
    return {"title": title, "first-release-date": date, "primary-type": typ}


# ─────────────────────── 未来のリリースだけを抜く ───────────────────────

def test_future_releases_keeps_only_dates_after_today():
    payload = {"release-groups": [
        _rg("Past", "2020-01-01"),
        _rg("Today", "2026-07-29"),      # 今日ちょうどは「予定」ではない
        _rg("Soon", "2026-08-15"),
    ]}
    got = upcoming.future_releases(payload, "2026-07-29", "2027-09-01")
    assert [r["title"] for r in got] == ["Soon"]


def test_future_releases_drops_imprecise_dates():
    # "2027" や "2027-05" は日が確定していない＝予定として使えないので出さない
    payload = {"release-groups": [_rg("Year only", "2027"), _rg("Month only", "2027-05"), _rg("Full", "2027-05-20")]}
    got = upcoming.future_releases(payload, "2026-07-29", "2027-09-01")
    assert [r["title"] for r in got] == ["Full"]


def test_future_releases_respects_horizon():
    # 遠すぎる日付は MusicBrainz の誤登録が多いので切る
    payload = {"release-groups": [_rg("Far", "2030-01-01"), _rg("Near", "2026-09-01")]}
    got = upcoming.future_releases(payload, "2026-07-29", "2027-09-01")
    assert [r["title"] for r in got] == ["Near"]


def test_future_releases_sorted_by_date():
    payload = {"release-groups": [_rg("B", "2026-12-01"), _rg("A", "2026-09-01")]}
    assert [r["title"] for r in upcoming.future_releases(payload, "2026-07-29", "2027-09-01")] == ["A", "B"]


def test_future_releases_handles_empty_payload():
    assert upcoming.future_releases({}, "2026-07-29", "2027-09-01") == []


# ─────────────────────── MBID の解決 ───────────────────────

def test_lookup_mbid_reads_artist_relation():
    payload = {"relations": [{"artist": {"id": "MB1", "name": "Ed Sheeran"}}]}
    assert upcoming.lookup_mbid("SPOT1", fetch=lambda: payload) == "MB1"


def test_lookup_mbid_returns_none_when_url_unknown():
    # MusicBrainz にその Spotify URL が登録されていない＝404。落とさず None を返す。
    def boom():
        raise HTTPError("u", 404, "Not Found", {}, None)

    assert upcoming.lookup_mbid("SPOT1", fetch=boom) is None


def test_lookup_mbid_reraises_other_http_errors():
    def boom():
        raise HTTPError("u", 503, "Service Unavailable", {}, None)

    try:
        upcoming.lookup_mbid("SPOT1", fetch=boom)
    except HTTPError as e:
        assert e.code == 503
    else:
        raise AssertionError("503 は握り潰さない（一過性障害を成功扱いしない）")


def test_lookup_mbid_none_when_no_relation():
    assert upcoming.lookup_mbid("SPOT1", fetch=lambda: {"relations": []}) is None


# ─────────────────────── 巡回の選択 ───────────────────────

def test_select_for_refresh_prefers_least_recently_checked():
    cache = {
        "a": {"mbid": "M1", "refreshed": "2026-07-28T00:00:00+00:00"},
        "b": {"mbid": "M2", "refreshed": None},                       # 未取得が最優先
        "c": {"mbid": "M3", "refreshed": "2026-01-01T00:00:00+00:00"},
    }
    assert upcoming.select_for_refresh(cache, 3) == ["b", "c", "a"]


def test_select_for_refresh_skips_unresolved_artists():
    cache = {"a": {"mbid": None, "refreshed": None}, "b": {"mbid": "M", "refreshed": None}}
    assert upcoming.select_for_refresh(cache, 5) == ["b"]


def test_select_for_refresh_honours_limit():
    cache = {str(i): {"mbid": f"M{i}", "refreshed": None} for i in range(10)}
    assert len(upcoming.select_for_refresh(cache, 4)) == 4


# ─────────────────────── 再解決の判定 ───────────────────────

def test_due_true_for_never_checked():
    assert upcoming._due({"mbid": None, "checked": None}, datetime.now(core.JST))


def test_due_false_when_already_resolved():
    assert not upcoming._due({"mbid": "M", "checked": None}, datetime.now(core.JST))


def test_due_waits_before_retrying_a_miss():
    now = datetime.now(core.JST)
    recent = (now - timedelta(days=3)).isoformat()
    old = (now - timedelta(days=40)).isoformat()
    assert not upcoming._due({"mbid": None, "checked": recent}, now)
    assert upcoming._due({"mbid": None, "checked": old}, now)


# ─────────────────────── 全体 ───────────────────────

class _FollowSp:
    def __init__(self, artists):
        self.artists = artists

    def current_user_followed_artists(self, limit=50, after=None):
        return {"artists": {"items": self.artists, "cursors": {}}}


def test_build_upcoming_uses_cache_and_drops_released_items(tmp_path, monkeypatch):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    monkeypatch.setattr(upcoming.time, "sleep", lambda s: None)
    # 今夜の巡回対象から外れた人でも、キャッシュ済みの予定は出し続ける
    # （毎晩少しずつしか見に行かない設計なので、ここが効かないと大半が消える）
    monkeypatch.setattr(upcoming, "REFRESH_BUDGET", 0)
    (tmp_path / "mb_cache.json").write_text(json.dumps({"artists": {
        "S1": {"name": "A", "mbid": "M1", "checked": "2026-07-01T00:00:00+00:00",
               "refreshed": "2026-07-28T00:00:00+00:00",
               "upcoming": [{"title": "Old", "date": "2026-07-01", "type": "Album"},
                            {"title": "Next", "date": "2026-08-20", "type": "Album"}]},
    }}))
    monkeypatch.setattr(upcoming, "_get", lambda *a, **k: {"release-groups": []})

    payload = upcoming.build_upcoming(_FollowSp([{"id": "S1", "name": "A"}]), tmp_path, now=now)
    titles = [i["title"] for i in payload["items"]]
    # 発売日を過ぎたものは落ち、これからのものだけ残る
    assert titles == ["Next"]
    assert payload["known"] == 1 and payload["followed"] == 1


def test_build_upcoming_ignores_unfollowed_leftovers(tmp_path, monkeypatch):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    monkeypatch.setattr(upcoming.time, "sleep", lambda s: None)
    (tmp_path / "mb_cache.json").write_text(json.dumps({"artists": {
        "GONE": {"name": "Unfollowed", "mbid": "M9", "refreshed": "2026-07-28T00:00:00+00:00",
                 "upcoming": [{"title": "Ghost", "date": "2026-09-01", "type": "Album"}]},
    }}))
    monkeypatch.setattr(upcoming, "_get", lambda *a, **k: {"release-groups": []})
    payload = upcoming.build_upcoming(_FollowSp([]), tmp_path, now=now)
    # フォローを外した人の残骸は出さない
    assert payload["items"] == []


def test_build_upcoming_writes_cache(tmp_path, monkeypatch):
    now = datetime(2026, 7, 29, 12, 0, tzinfo=core.JST)
    monkeypatch.setattr(upcoming.time, "sleep", lambda s: None)
    monkeypatch.setattr(upcoming, "lookup_mbid", lambda sid: "MB-NEW")
    monkeypatch.setattr(upcoming, "_get", lambda *a, **k: {"release-groups": [
        {"title": "Future", "first-release-date": "2026-10-01", "primary-type": "Album"},
    ]})
    payload = upcoming.build_upcoming(_FollowSp([{"id": "S9", "name": "New"}]), tmp_path, now=now)
    cache = json.loads((tmp_path / "mb_cache.json").read_text())["artists"]
    assert cache["S9"]["mbid"] == "MB-NEW"
    assert [i["title"] for i in payload["items"]] == ["Future"]
