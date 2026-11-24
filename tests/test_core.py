import json
import socket
import time

import pytest
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth

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


# ───────────── トークンキャッシュの scope（狭い scope の実行で記録が狭まらない） ─────────────

# CACHED はキャッシュの初期値（reauth.py が書く形）。GRANTED は Spotify の実際の応答に近い形で、
# 並びが要求と無関係で、要求していない scope も含む（2026-11-24 実測）。両者を変えてあるので、
# 「応答を記録した」ことを「初期値のまま」と区別できる。
CACHED = core.SCOPE_ALL
GRANTED = " ".join([*reversed(core.SCOPE_ALL.split()), "playlist-read-collaborative"])
NARROW = "playlist-read-private"


class _FakeTokenResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return dict(self._body)


def _cached(cache):
    return json.loads(cache.read_text())


@pytest.fixture
def token_env(tmp_path, monkeypatch):
    """期限切れトークンのキャッシュ（全 scope 許可済み）を置き、Spotify の token 応答を差し替える。

    返り値は (cache, reply, posts)。reply を書き換えると、次に Spotify が返す内容が変わる。
    posts には token エンドポイントへの送信内容が順に入る。ネットワークには一切出さない。
    """
    def no_network(*args, **kwargs):
        pytest.fail("テストからネットワークに出ようとした")  # except Exception に握りつぶされない

    monkeypatch.setattr(socket, "getaddrinfo", no_network)  # 接続の前に DNS 問い合わせが出るのも塞ぐ
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8000/callback")
    monkeypatch.setenv("CI", "true")  # headless（ブラウザ認証を始めない経路）
    monkeypatch.setattr(core, "ENV_PATH", tmp_path / "no.env")
    cache = tmp_path / ".cache-spotify"
    cache.write_text(json.dumps({
        "access_token": "old", "token_type": "Bearer", "expires_in": 3600,
        "expires_at": int(time.time()) - 10, "refresh_token": "rt", "scope": CACHED,
    }))
    monkeypatch.setattr(core, "CACHE_PATH", cache)
    reply = {"access_token": "new", "token_type": "Bearer", "expires_in": 3600, "scope": GRANTED}
    posts: list[dict] = []

    def fake_post(self, url, data=None, **kwargs):
        assert url == SpotifyOAuth.OAUTH_TOKEN_URL
        posts.append(dict(data or {}))
        return _FakeTokenResponse(reply)

    monkeypatch.setattr(requests.Session, "post", fake_post)
    return cache, reply, posts


def test_narrow_scope_run_does_not_lock_out_broader_runs(token_env):
    """狭い scope の実行で refresh が走っても、そのあと広い scope の headless 実行が通ること。

    素の spotipy だと refresh 時にキャッシュの scope が "playlist-read-private" に書き換わり、
    2回目の build_client が AuthRequired で落ちる（2026-11-23 にローカルで実際に起きた）。
    """
    cache, _, posts = token_env
    core.build_client(NARROW)
    assert [p["grant_type"] for p in posts] == ["refresh_token"]
    assert _cached(cache)["access_token"] == "new"
    assert _cached(cache)["refresh_token"] == "rt"  # 応答に無い refresh token は保たれる
    core.build_client(core.SCOPE_ALL)  # AuthRequired にならない
    assert len(posts) == 1  # 2回目は有効なトークンをそのまま使う


def test_refresh_records_scope_spotify_granted(token_env):
    cache, _, _ = token_env
    core.build_client(NARROW)
    assert _cached(cache)["scope"] == GRANTED  # 初期値（CACHED）でも要求（NARROW）でもない


def test_refresh_does_not_record_more_than_spotify_granted(token_env):
    """要求より許可が狭いときは、記録も許可どおりに狭める（要求や和集合で水増ししない）。

    水増しすると次の実行が validate_token を素通りし、AuthRequired ではなく API の 403 で落ちる。
    記録と実態がずれるという、直したはずの問題が逆向きに戻る。
    """
    cache, reply, _ = token_env
    reply["scope"] = NARROW
    core.build_client(core.SCOPE_ALL)  # 初期記録（全 scope）で通り、refresh で狭い許可が返る
    assert _cached(cache)["scope"] == NARROW
    with pytest.raises(core.AuthRequired):
        core.build_client(core.SCOPE_ALL)


@pytest.mark.parametrize("missing", [None, ""])
def test_refresh_without_scope_in_reply_falls_back_to_requested(token_env, missing):
    cache, reply, _ = token_env
    if missing is None:
        del reply["scope"]
    else:
        reply["scope"] = missing
    core.build_client(NARROW)
    assert _cached(cache)["scope"] == NARROW


@pytest.fixture
def interactive(token_env, monkeypatch):
    """対話実行（ローカルの手動実行・reauth.py）の経路。ブラウザ起動と 8000 番の listen は禁止する。"""
    def forbidden(*args, **kwargs):
        pytest.fail("ブラウザ認証の経路に入った")

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(core, "is_headless", lambda: False)
    monkeypatch.setattr(core, "_free_redirect_port", lambda: None)
    monkeypatch.setattr(SpotifyOAuth, "_open_auth_url", forbidden)
    monkeypatch.setattr(SpotifyOAuth, "_get_auth_response_local_server", forbidden)
    monkeypatch.setattr(SpotifyOAuth, "_get_auth_response_interactive", forbidden)
    return token_env


def test_interactive_refresh_records_scope_spotify_granted(interactive):
    # 対話実行では build_client は検証せず、最初の API 呼び出しの直前に refresh が走る
    cache, _, posts = interactive
    sp = core.build_client(NARROW)
    assert posts == []  # build_client の時点ではまだ refresh しない（遅延 refresh の経路を踏んでいる）
    assert sp.auth_manager.get_access_token(as_dict=False) == "new"
    assert [p["grant_type"] for p in posts] == ["refresh_token"]
    assert _cached(cache)["scope"] == GRANTED


@pytest.mark.parametrize(("requested", "reply_scope", "recorded"), [
    (NARROW, GRANTED, GRANTED),
    (NARROW, None, NARROW),
    (core.SCOPE_ALL, NARROW, NARROW),  # 許可が要求より狭い → 要求や和集合で水増ししない
])
def test_reauthorization_records_scope_spotify_granted(interactive, monkeypatch, requested, reply_scope, recorded):
    """キャッシュが要求を満たさず再認証（auth code 交換）になったときも、応答の scope を記録する。

    reauth.py と README の再認証手順が通る経路。ここで書いたキャッシュが Secret として全 CI ジョブに配られる。
    """
    cache, reply, posts = interactive
    saved = _cached(cache)
    saved["scope"] = "user-top-read"  # 要求を満たさない → refresh ではなく再認証になる
    cache.write_text(json.dumps(saved))
    monkeypatch.setattr(SpotifyOAuth, "get_auth_response", lambda self, *a, **k: "the-code")
    reply["refresh_token"] = "rt2"
    if reply_scope is None:
        del reply["scope"]
    else:
        reply["scope"] = reply_scope
    sp = core.build_client(requested)
    assert sp.auth_manager.get_access_token(as_dict=False) == "new"
    assert [(p["grant_type"], p.get("code")) for p in posts] == [("authorization_code", "the-code")]
    assert _cached(cache)["scope"] == recorded
    assert _cached(cache)["refresh_token"] == "rt2"


def test_spotipy_still_has_the_hook_we_override():
    # private API に依存している。名前が変わると override が黙って効かなくなり、修正前の挙動に戻る
    assert "_add_custom_values_to_token_info" in vars(SpotifyOAuth), (
        "spotipy の private API が変わった。core._GrantedScopeOAuth を見直すこと"
    )
