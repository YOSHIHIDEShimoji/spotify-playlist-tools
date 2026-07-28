import json

import recommend


def _similar_artists(pairs):
    """[(name, match)] → Last.fm の artist.getSimilar レスポンス形。"""
    return {"similarartists": {"artist": [{"name": n, "match": str(m)} for n, m in pairs]}}


def _similar_tracks(triples):
    """[(track, artist, match)] → track.getSimilar レスポンス形。"""
    return {"similartracks": {"track": [
        {"name": t, "artist": {"name": a}, "match": str(m)} for t, a, m in triples
    ]}}


# ─────────────────────────── 似ているアーティスト ───────────────────────────

def test_similar_artists_scores_by_match_times_affinity():
    seeds = [{"name": "Big", "count": 10000}, {"name": "Small", "count": 10}]
    responses = {
        "Big": _similar_artists([("X", 0.5)]),
        "Small": _similar_artists([("Y", 0.5)]),
    }
    got = recommend.similar_artists(seeds, set(), fetch=lambda n: responses[n])
    # match が同じなら、よく聴いている種から来た候補が上に来る
    assert [a["name"] for a in got] == ["X", "Y"]
    assert got[0]["score"] > got[1]["score"]


def test_similar_artists_uses_log_affinity_so_one_seed_cannot_dominate():
    # 再生回数が1000倍でも重みは約2倍まで。1人の種におすすめを支配させない。
    seeds = [{"name": "Huge", "count": 15592}, {"name": "Mid", "count": 3377}]
    responses = {"Huge": _similar_artists([("X", 0.30)]), "Mid": _similar_artists([("Y", 0.45)])}
    got = {a["name"]: a["score"] for a in recommend.similar_artists(seeds, set(), fetch=lambda n: responses[n])}
    assert got["Y"] > got["X"]  # 生の回数で重み付けしていたら X が勝ってしまう


def test_similar_artists_accumulates_across_seeds():
    seeds = [{"name": "A", "count": 100}, {"name": "B", "count": 100}]
    responses = {"A": _similar_artists([("Shared", 0.4)]), "B": _similar_artists([("Shared", 0.4), ("Solo", 0.5)])}
    got = {a["name"]: a for a in recommend.similar_artists(seeds, set(), fetch=lambda n: responses[n])}
    # 複数の種から推された候補は加点され、単発の高 match を上回りうる
    assert got["Shared"]["score"] > got["Solo"]["score"]
    assert [b["name"] for b in got["Shared"]["because"]] == ["A", "B"]


def test_similar_artists_excludes_already_listened():
    seeds = [{"name": "A", "count": 100}]
    responses = {"A": _similar_artists([("Known", 0.9), ("New", 0.1)])}
    got = recommend.similar_artists(seeds, {"known"}, fetch=lambda n: responses[n])
    # すでに聴いているアーティストを「おすすめ」に出さない（known は正規化済みキー）
    assert [a["name"] for a in got] == ["New"]


def test_similar_artists_because_is_capped_and_sorted():
    seeds = [{"name": f"S{i}", "count": i * 100} for i in (1, 2, 3)]
    responses = {s["name"]: _similar_artists([("X", 0.5)]) for s in seeds}
    (got,) = recommend.similar_artists(seeds, set(), fetch=lambda n: responses[n])
    assert [b["name"] for b in got["because"]] == ["S3", "S2"]  # 再生の多い種から最大2件


def test_similar_artists_survives_failing_seed():
    seeds = [{"name": "Bad", "count": 100}, {"name": "Good", "count": 100}]

    def fetch(name):
        if name == "Bad":
            raise RuntimeError("Last.fm 500")
        return _similar_artists([("X", 0.5)])

    assert [a["name"] for a in recommend.similar_artists(seeds, set(), fetch=fetch)] == ["X"]


def test_similar_artists_ignores_malformed_match():
    seeds = [{"name": "A", "count": 100}]
    payload = {"similarartists": {"artist": [{"name": "X", "match": None}, {"name": "Y", "match": "0.5"}]}}
    got = recommend.similar_artists(seeds, set(), fetch=lambda n: payload)
    assert [a["name"] for a in got] == ["Y"]


# ─────────────────────────── 似ている曲 ───────────────────────────

def test_similar_tracks_excludes_known_tracks():
    seeds = [{"name": "Seed", "artists": ["A"], "count": 500}]
    payload = _similar_tracks([("Known Song", "B", 0.9), ("New Song", "C", 0.3)])
    known = {recommend._key("B", "Known Song")}
    got = recommend.similar_tracks(seeds, known, fetch=lambda a, t: payload)
    assert [x["name"] for x in got] == ["New Song"]


def test_similar_tracks_normalizes_remaster_suffix_when_excluding():
    # ライブラリに "Song - Remastered 2009" があれば、"Song" のおすすめは重複なので出さない
    seeds = [{"name": "Seed", "artists": ["A"], "count": 500}]
    payload = _similar_tracks([("Song", "B", 0.9)])
    known = {recommend._key("B", "Song - Remastered 2009")}
    assert recommend.similar_tracks(seeds, known, fetch=lambda a, t: payload) == []


def test_similar_tracks_because_keeps_strongest_seed():
    seeds = [{"name": "Weak", "artists": ["A"], "count": 10},
             {"name": "Strong", "artists": ["A"], "count": 900}]
    payload = _similar_tracks([("X", "B", 0.5)])
    (got,) = recommend.similar_tracks(seeds, set(), fetch=lambda a, t: payload)
    assert got["because"]["name"] == "Strong"


def test_similar_tracks_skips_seed_without_artist():
    seeds = [{"name": "NoArtist", "artists": [], "count": 100}]
    calls = []

    def fetch(a, t):
        calls.append((a, t))
        return _similar_tracks([])

    assert recommend.similar_tracks(seeds, set(), fetch=fetch) == []
    assert calls == []  # アーティスト不明の曲では API を呼ばない


# ─────────────────────────── 正規化 ───────────────────────────

def test_norm_folds_case_space_and_version_suffix():
    assert recommend.norm("  Hey Jude  ") == "hey jude"
    assert recommend.norm("Because - Remastered 2009") == "because"
    assert recommend.norm("Song – Live Version") == "song"


# ─────────────────────────── 解決とファイル出力 ───────────────────────────

class _SearchSp:
    def __init__(self, hits=None):
        self.queries = []
        self.hits = hits or {}

    def search(self, q, type, limit):
        self.queries.append(q)
        hit = self.hits.get(q)
        return {"tracks": {"items": [hit] if hit else []}}


def test_resolve_tracks_uses_cache_and_budget():
    rows = [{"name": "N1", "artist": "A1"}, {"name": "N2", "artist": "A2"}]
    sp = _SearchSp({'track:N1 artist:A1': {"id": "ID1", "album": {"images": [{"url": "u"}]}}})
    cache = {}
    used = recommend._resolve_tracks(sp, rows, cache, budget=1)
    assert used == 1 and len(sp.queries) == 1
    assert rows[0]["id"] == "ID1" and rows[0]["image"] == "u"
    assert "id" not in rows[1]  # 予算切れは翌晩へ持ち越し

    # 2回目は解決済みぶんを再検索しない
    sp.queries.clear()
    recommend._resolve_tracks(sp, rows, cache, budget=5)
    assert sp.queries == ['track:N2 artist:A2']


def test_resolve_tracks_remembers_misses():
    rows = [{"name": "Ghost", "artist": "Nobody"}]
    sp = _SearchSp()
    cache = {}
    recommend._resolve_tracks(sp, rows, cache, budget=5)
    recommend._resolve_tracks(sp, rows, cache, budget=5)
    # 見つからなかったことも覚えるので、毎晩同じ空振り検索を繰り返さない
    assert len(sp.queries) == 1
    assert "id" not in rows[0]


def test_build_recs_writes_unavailable_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    (tmp_path / "lifetime_artists.json").write_text(json.dumps({"artists": [{"name": "A", "count": 5}]}))
    (tmp_path / "lifetime_tracks.json").write_text(json.dumps({"tracks": []}))
    recommend.build_recs(None, tmp_path)
    payload = json.loads((tmp_path / "recs.json").read_text())
    # 404 にせず「なぜ出せないか」を書く。サイトが理由を表示できるようにするため。
    assert payload["available"] is False and "LASTFM_API_KEY" in payload["reason"]


def test_build_recs_noop_without_lifetime_data(tmp_path):
    assert recommend.build_recs(None, tmp_path) == {}
    assert not (tmp_path / "recs.json").exists()


def test_known_track_keys_covers_history_and_library():
    tracks = [{"name": "H", "artists": ["A"]}]
    index = [{"name": "L", "artists": ["B"]}]
    keys = recommend._known_track_keys(tracks, index)
    assert recommend._key("A", "H") in keys and recommend._key("B", "L") in keys
