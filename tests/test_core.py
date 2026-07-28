import pytest
import requests
import spotipy

import core


def test_parse_config(tmp_path):
    p = tmp_path / "cfg.txt"
    p.write_text(
        "# comment\n"
        "\n"
        "SOURCE_PLAYLIST_ID=abc123\n"
        "Charlie Puth=pid1\n"
        "WEIRD=a=b=c\n"          # 値に = を含む
        "no_equals_line\n",       # = が無い行は無視
        encoding="utf-8",
    )
    cfg = core.parse_config(p)
    assert cfg["SOURCE_PLAYLIST_ID"] == "abc123"
    assert cfg["Charlie Puth"] == "pid1"
    assert cfg["WEIRD"] == "a=b=c"
    assert "no_equals_line" not in cfg
    assert "# comment" not in cfg


def test_extract_playlist_id():
    pid = "3sOTawp5o0fz1caiPR47aV"
    assert core.extract_playlist_id(pid) == pid
    assert core.extract_playlist_id(f"https://open.spotify.com/playlist/{pid}") == pid
    assert core.extract_playlist_id(f"https://open.spotify.com/playlist/{pid}?si=xyz") == pid
    assert core.extract_playlist_id(f"spotify:playlist:{pid}") == pid


def test_append_line_adds_missing_newline(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("first=1", encoding="utf-8")  # 末尾改行なし
    core.append_line(p, "second=2")
    assert p.read_text(encoding="utf-8") == "first=1\nsecond=2\n"


def test_append_line_keeps_existing_newline(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("first=1\n", encoding="utf-8")
    core.append_line(p, "second=2")
    assert p.read_text(encoding="utf-8") == "first=1\nsecond=2\n"


def test_append_line_empty_file(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("", encoding="utf-8")
    core.append_line(p, "first=1")
    assert p.read_text(encoding="utf-8") == "first=1\n"


def test_remove_saved_in_batches_uses_me_tracks_and_50_chunks():
    # spotipy 2.26 の me/library(uris) は50件で400になるため、me/tracks(ids) を直接叩く
    calls = []

    class Sp:
        def _delete(self, url, **kwargs):
            calls.append((url, kwargs))

    ids = [f"id{i:02d}" for i in range(120)]
    core.remove_saved_in_batches(Sp(), ids)
    assert [u for u, _ in calls] == ["me/tracks"] * 3
    sizes = [len(k["ids"].split(",")) for _, k in calls]
    assert sizes == [50, 50, 20]
    assert calls[0][1]["ids"].startswith("id00,id01")


# ─────────────────────── API リトライ（nightly の 503 対策） ───────────────────────

@pytest.fixture
def no_sleep(monkeypatch):
    """バックオフの待ち時間を潰す。待った秒数のリストを返す。"""
    waited: list[float] = []
    monkeypatch.setattr(core.time, "sleep", lambda s: waited.append(s))
    return waited


def _spotify_error(status, msg="boom"):
    return spotipy.SpotifyException(status, -1, msg)


def test_retry_api_returns_immediately_on_success(no_sleep):
    assert core.retry_api(lambda: "ok") == "ok"
    assert no_sleep == []


def test_retry_api_recovers_from_transient_503(no_sleep):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _spotify_error(503)
        return "ok"

    assert core.retry_api(flaky) == "ok"
    assert calls["n"] == 3
    assert no_sleep == [3.0, 6.0]  # 指数バックオフ


def test_retry_api_treats_exhausted_urllib3_retries_as_transient(no_sleep):
    # spotipy は urllib3 のリトライ枯渇を 429 + "Max Retries" で投げてくる。
    # 2026-07-26 の nightly 失敗はこれ。ここを一過性と見なせないと再試行されない。
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _spotify_error(429, "Max Retries, reason: too many 503 error responses")
        return "ok"

    assert core.retry_api(flaky) == "ok"


def test_retry_api_retries_connection_errors(no_sleep):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("reset")
        return "ok"

    assert core.retry_api(flaky) == "ok"


def test_retry_api_does_not_retry_permanent_errors(no_sleep):
    for status in (401, 403, 404):
        calls = {"n": 0}

        def boom(s=status, c=calls):
            c["n"] += 1
            raise _spotify_error(s)

        with pytest.raises(spotipy.SpotifyException):
            core.retry_api(boom)
        assert calls["n"] == 1, f"{status} は再試行しない（待っても直らない）"
    assert no_sleep == []


def test_retry_api_raises_after_exhausting_attempts(no_sleep):
    calls = {"n": 0}

    def always_down():
        calls["n"] += 1
        raise _spotify_error(503)

    with pytest.raises(spotipy.SpotifyException):
        core.retry_api(always_down, attempts=3)
    assert calls["n"] == 3  # 最後まで駄目なら素直に落とす（issue で気づけるように）


class _PagingSp:
    """ページングのスタブ。fail_on で指定した回数だけ next を一過性エラーにする。"""

    def __init__(self, pages, fail_times=0):
        self.pages = pages
        self.fail_times = fail_times
        self.next_calls = 0

    def playlist_items(self, pid, fields=None, additional_types=None, limit=100):
        return self.pages[0]

    def next(self, results):
        self.next_calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise _spotify_error(503)
        idx = self.pages.index(results)
        return self.pages[idx + 1]


def _page(track_ids, has_next):
    return {"items": [{"track": {"id": t}} for t in track_ids], "next": "url" if has_next else None}


def test_iter_playlist_tracks_reads_all_pages(no_sleep):
    sp = _PagingSp([_page(["a"], True), _page(["b"], False)])
    assert [t["id"] for t in core.iter_playlist_tracks(sp, "pid", "f,next")] == ["a", "b"]


def test_iter_playlist_tracks_resumes_after_transient_failure(no_sleep):
    # 2ページ目の取得が2回失敗しても、待って再試行して最後まで読み切る
    sp = _PagingSp([_page(["a"], True), _page(["b"], False)], fail_times=2)
    assert [t["id"] for t in core.iter_playlist_tracks(sp, "pid", "f,next")] == ["a", "b"]
    assert sp.next_calls == 3


def test_next_page_returns_none_at_end(no_sleep):
    assert core.next_page(_PagingSp([]), {"next": None}) is None
    assert core.next_page(_PagingSp([]), None) is None


def test_remove_in_batches_retries_transient_errors(no_sleep):
    """全出現削除は冪等なので再試行して良い（追加系と違い二重適用の害が無い）。"""
    class _Sp:
        def __init__(self):
            self.calls = 0

        def playlist_remove_all_occurrences_of_items(self, pid, uris):
            self.calls += 1
            if self.calls == 1:
                raise _spotify_error(503)

    sp = _Sp()
    core.remove_in_batches(sp, "pid", ["t1"])
    assert sp.calls == 2


def test_retry_api_retries_unclassified_errors(no_sleep):
    """http_status=-1（spotipy が分類できなかった接続断など）も一過性として再試行する。

    ここが False に退行すると、この変更の動機だった「一過性障害で夜間バッチ全体が落ちる」が
    そのまま再発するので、明示的に固定する。
    """
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _spotify_error(-1, "connection reset")
        return "ok"

    assert core.retry_api(flaky) == "ok"
    assert calls["n"] == 2


def test_iter_playlist_tracks_retries_the_first_page(no_sleep):
    """初回ページの取得も retry でくるまれていること（next だけ守っても意味がない）。"""
    class _Sp(_PagingSp):
        def __init__(self):
            super().__init__([_page(["a"], False)])
            self.first_calls = 0

        def playlist_items(self, pid, fields=None, additional_types=None, limit=100):
            self.first_calls += 1
            if self.first_calls == 1:
                raise _spotify_error(503)
            return self.pages[0]

    sp = _Sp()
    assert [t["id"] for t in core.iter_playlist_tracks(sp, "pid", "f,next")] == ["a"]
    assert sp.first_calls == 2
