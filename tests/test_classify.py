import classify


class FakeSpotify:
    """sp.artist(id) をモック。呼ばれたら記録する。"""

    def __init__(self, genres=None, forbid_call=False):
        self._genres = genres or []
        self._forbid = forbid_call
        self.artist_calls = 0

    def artist(self, artist_id):
        self.artist_calls += 1
        assert not self._forbid, "sp.artist が呼ばれてはいけない"
        return {"genres": self._genres}


def _track(artist_id="a1", artist_name="Someone", name="Song", album="Album", isrc=""):
    return {
        "id": "t1",
        "name": name,
        "artists": [{"id": artist_id, "name": artist_name}],
        "album": {"name": album},
        "external_ids": {"isrc": isrc},
    }


def test_kana_matches():
    assert classify.HIRAGANA_KATAKANA.search("ひらがな")
    assert classify.HIRAGANA_KATAKANA.search("カタカナ")
    assert classify.HIRAGANA_KATAKANA.search("ｷﾀｶﾅ")  # 半角カナ


def test_kana_does_not_match_kanji_or_english():
    assert not classify.HIRAGANA_KATAKANA.search("漢字")  # 漢字のみ
    assert not classify.HIRAGANA_KATAKANA.search("English")


def test_cache_hit_skips_api():
    sp = FakeSpotify(forbid_call=True)
    cache = {"a1": {"name": "Someone", "class": "western", "source": "gemini", "date": "2026-01-01"}}
    assert classify.classify_track(sp, _track(), cache) == "western"
    assert sp.artist_calls == 0


def test_isrc_jp_is_japanese_without_api():
    sp = FakeSpotify(forbid_call=True)
    cache = {}
    tr = _track(artist_name="Romaji Artist", name="Song", album="Album", isrc="JPXX02412345")
    assert classify.classify_track(sp, tr, cache) == "japanese"
    assert cache["a1"]["source"] == "isrc"


def test_kana_in_text_is_japanese_without_api():
    sp = FakeSpotify(forbid_call=True)
    cache = {}
    tr = _track(artist_name="バンド", isrc="USXX0000000")
    assert classify.classify_track(sp, tr, cache) == "japanese"
    assert cache["a1"]["source"] == "kana"


def test_kanji_only_falls_through_to_unknown_when_no_genres():
    sp = FakeSpotify(genres=[])  # genres 空 → unknown へ
    cache = {}
    tr = _track(artist_name="周杰倫", name="稲香", album="專輯", isrc="TWXX0000000")
    assert classify.classify_track(sp, tr, cache) == "unknown"
    assert sp.artist_calls == 1  # 漢字のみは genres を見に行く


def test_genres_western():
    sp = FakeSpotify(genres=["pop", "dance pop"])
    cache = {}
    tr = _track(artist_name="Whoever", isrc="USXX0000000")
    assert classify.classify_track(sp, tr, cache) == "western"
    assert cache["a1"]["source"] == "genres"


def test_genres_japanese():
    sp = FakeSpotify(genres=["j-pop"])
    cache = {}
    tr = _track(artist_name="Whoever", isrc="USXX0000000")
    assert classify.classify_track(sp, tr, cache) == "japanese"


def test_gemini_skipped_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cache = {}
    assert classify.classify_unknowns_with_gemini({"a1": "Someone"}, cache) == {}
