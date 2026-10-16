from datetime import date

import pytest

import one_year_ago as oya


def _p(played_at, tid):
    return {"track_id": tid, "played_at": played_at, "name": "n", "artists": []}


# ─────────────────────────── 1年前の日付 ───────────────────────────

def test_one_year_ago_same_month_day():
    assert oya.one_year_ago(date(2026, 10, 16)) == date(2025, 10, 16)


def test_one_year_ago_leap_day_falls_back_to_feb_28():
    # 2/29 は前の年に存在しない。例外にせず 2/28 を使う
    assert oya.one_year_ago(date(2028, 2, 29)) == date(2027, 2, 28)


def test_one_year_ago_day_after_leap_day_is_not_shifted():
    assert oya.one_year_ago(date(2028, 3, 1)) == date(2027, 3, 1)


# ─────────────────────────── 日ごとの再生 ───────────────────────────

def test_plays_by_day_uses_jst_boundary():
    # UTC 10/15 15:30 は JST 10/16 00:30 → 10/16 の再生として数える
    by = oya.plays_by_day([_p("2025-10-15T15:30:00Z", "late"), _p("2025-10-15T14:59:00Z", "early")])
    assert [p["track_id"] for p in by[date(2025, 10, 16)]] == ["late"]
    assert [p["track_id"] for p in by[date(2025, 10, 15)]] == ["early"]


def test_plays_by_day_sorts_by_time_regardless_of_input_order():
    # 読み込み順は保証されない（履歴と scrobble の継ぎ足し）。時刻で並べ直すこと
    recs = [_p("2025-10-16T05:00:00Z", "b"), _p("2025-10-16T01:00:00Z", "a"), _p("2025-10-16T09:00:00Z", "c")]
    by = oya.plays_by_day(recs)
    assert [p["track_id"] for p in by[date(2025, 10, 16)]] == ["a", "b", "c"]


def test_plays_by_day_excludes_unresolved_lastfm_tracks():
    # Spotify に解決できなかった曲はプレイリストに入れられないので最初から除く
    by = oya.plays_by_day([_p("2025-10-16T01:00:00Z", "lastfm:foo|bar"), _p("2025-10-16T02:00:00Z", "real")])
    assert [p["track_id"] for p in by[date(2025, 10, 16)]] == ["real"]


def test_plays_by_day_skips_rows_without_time_or_id():
    by = oya.plays_by_day([{"track_id": "x"}, {"played_at": "2025-10-16T01:00:00Z"}])
    assert by == {}


def test_tracks_for_day_keeps_listening_order_and_dedupes():
    plays = [{"track_id": t} for t in ("a", "b", "a", "c", "b")]
    # 何回聴いても1回だけ、初めて聴いた位置に置く
    assert oya.tracks_for_day(plays) == ["a", "b", "c"]


# ─────────────────────────── 聴いていない日の代用 ───────────────────────────

D = date(2025, 10, 16)


def test_pick_day_uses_target_when_it_has_plays():
    assert oya.pick_day({D: [1], D.replace(day=15): [1]}, D) == D


def test_pick_day_falls_back_to_nearest_day():
    by = {date(2025, 10, 13): [1], date(2025, 10, 18): [1]}  # 3日前と2日後
    assert oya.pick_day(by, D) == date(2025, 10, 18)


def test_pick_day_prefers_earlier_day_on_tie():
    by = {date(2025, 10, 14): [1], date(2025, 10, 18): [1]}  # どちらも2日差
    assert oya.pick_day(by, D) == date(2025, 10, 14)


def test_pick_day_ignores_days_with_empty_lists():
    by = {D: [], date(2025, 10, 17): [1]}
    assert oya.pick_day(by, D) == date(2025, 10, 17)


def test_pick_day_returns_none_beyond_window():
    by = {date(2025, 12, 1): [1]}  # 46日後
    assert oya.pick_day(by, D, window=30) is None


def test_pick_day_window_is_inclusive():
    by = {date(2025, 11, 15): [1]}  # ちょうど30日後
    assert oya.pick_day(by, D, window=30) == date(2025, 11, 15)


# ─────────────────────────── 説明欄 ───────────────────────────

def test_describe_exact_day():
    assert oya.describe(D, D, 52).startswith("2025年10月16日に聴いた52曲")


def test_describe_substituted_day_says_so():
    text = oya.describe(D, date(2025, 10, 18), 7)
    # 名前どおりでない日は、そうと分かるように書く
    assert "2025年10月16日（1年前の今日）は聴いていなかった" in text
    assert "2025年10月18日に聴いた7曲" in text


def test_describe_no_plays_at_all():
    assert "再生記録がありません" in oya.describe(D, None, 0)


# ─────────────────────────── 全体 ───────────────────────────

def test_build_end_to_end():
    recs = [
        _p("2025-10-16T03:00:00Z", "b"),
        _p("2025-10-16T01:00:00Z", "a"),
        _p("2025-10-16T04:00:00Z", "a"),
        _p("2024-10-16T01:00:00Z", "two_years_ago"),  # 2年前は入らない
    ]
    plan = oya.build(recs, date(2026, 10, 16))
    assert plan["target"] == D and plan["used"] == D
    assert plan["track_ids"] == ["a", "b"]
    assert "2曲" in plan["description"]


def test_build_with_no_plays_anywhere_is_empty():
    plan = oya.build([], date(2026, 10, 16))
    assert plan["used"] is None and plan["track_ids"] == []


# ─────────────────────────── プレイリストの入れ替え ───────────────────────────

class _Sp:
    def __init__(self):
        self.replaced = None
        self.added: list[list[str]] = []
        self.description = None

    def playlist_replace_items(self, pid, items):
        self.replaced = list(items)

    def playlist_add_items(self, pid, items):
        self.added.append(list(items))

    def playlist_change_details(self, pid, description=None):
        self.description = description


def test_apply_replaces_first_100_then_adds_rest():
    sp = _Sp()
    ids = [f"t{i}" for i in range(184)]  # 実測の最大（1日184曲）
    oya.apply(sp, "pid", ids, "desc")
    assert sp.replaced == ids[:100]
    assert sp.added == [ids[100:]]  # 100件バッチで残り84件
    assert sp.description == "desc"


def test_apply_clears_playlist_when_no_tracks():
    sp = _Sp()
    oya.apply(sp, "pid", [], "no plays")
    assert sp.replaced == [] and sp.added == []  # 前日の中身を残さない


def test_apply_without_overflow_does_not_add():
    sp = _Sp()
    oya.apply(sp, "pid", ["a", "b"], "d")
    assert sp.replaced == ["a", "b"] and sp.added == []


def test_load_config_requires_playlist_id(tmp_path):
    cfg = tmp_path / "c.txt"
    cfg.write_text("# empty\n")
    with pytest.raises(RuntimeError):
        oya.load_config(cfg)
    cfg.write_text("PLAYLIST_ID=spotify:playlist:3425aPpfi7aEAqpKxfqQBK\n")
    assert oya.load_config(cfg) == "3425aPpfi7aEAqpKxfqQBK"
