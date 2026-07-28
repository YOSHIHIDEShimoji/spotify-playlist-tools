import sync


def _track(tid, name, *artists):
    return {"id": tid, "name": name, "artists": [{"name": a} for a in artists]}


# ── コラボ曲の振り分け（本人の要望: ソースには1曲・両方のアーティストPLに入る） ──

def test_collab_track_goes_to_every_credited_artist():
    tracks = [_track("t1", "Song", "Ed Sheeran", "Charlie Puth")]
    ed, ed_name = sync.match_tracks_for_artist(tracks, "ed sheeran")
    charlie, charlie_name = sync.match_tracks_for_artist(tracks, "charlie puth")
    # 同じ1曲が両方のアーティストプレイリストの対象になる（曲数の多い方に寄せない）
    assert ed == ["t1"] and charlie == ["t1"]
    assert ed_name == "Ed Sheeran" and charlie_name == "Charlie Puth"


def test_match_is_case_insensitive_and_returns_spotify_spelling():
    tracks = [_track("t1", "Song", "ONE OK ROCK")]
    ids, name = sync.match_tracks_for_artist(tracks, "one ok rock")
    assert ids == ["t1"] and name == "ONE OK ROCK"  # 設定ファイルの表記ではなく Spotify 表記を使う


def test_match_counts_each_track_once_even_if_artist_listed_twice():
    # 同じアーティストが2回クレジットされていても曲は1回だけ（重複追加を防ぐ）
    tracks = [_track("t1", "Song", "A", "A")]
    ids, _ = sync.match_tracks_for_artist(tracks, "a")
    assert ids == ["t1"]


def test_unrelated_artist_matches_nothing():
    tracks = [_track("t1", "Song", "A")]
    assert sync.match_tracks_for_artist(tracks, "b") == ([], "")


# ── クレジット漏れの客演（曲名の feat.） ──

def test_featured_artist_in_title_is_matched():
    # artists 配列に載っていない客演も、曲名に書いてあれば拾う
    tracks = [_track("t1", "Song (feat. Charlie Puth)", "Ed Sheeran")]
    ids, name = sync.match_tracks_for_artist(tracks, "charlie puth")
    assert ids == ["t1"] and name == "Charlie Puth"


def test_extract_featured_artists_handles_common_notations():
    assert sync.extract_featured_artists("Song (feat. A)") == ["A"]
    assert sync.extract_featured_artists("Song ft. A") == ["A"]
    assert sync.extract_featured_artists("Song [featuring A]") == ["A"]
    assert sync.extract_featured_artists("Song (with A)") == ["A"]
    assert sync.extract_featured_artists("Song (feat. A & B)") == ["A", "B"]
    assert sync.extract_featured_artists("Song (feat. A, B and C)") == ["A", "B", "C"]


def test_extract_featured_artists_empty_when_absent():
    assert sync.extract_featured_artists("Plain Song") == []
    assert sync.extract_featured_artists("") == []


def test_title_false_positive_does_not_match_unconfigured_name():
    # "Dance with Me" は "Me" を客演として取り出すが、設定済みアーティスト名と一致しない限り
    # プレイリストには影響しない（誤爆が実害にならない設計）
    tracks = [_track("t1", "Dance with Me", "A")]
    assert sync.extract_featured_artists("Dance with Me") == ["Me"]
    assert sync.match_tracks_for_artist(tracks, "somebody") == ([], "")


def test_credited_artist_wins_over_title_parsing():
    # クレジットにいる場合はそちらの表記を使う（曲名の表記揺れでプレイリスト名を汚さない）
    tracks = [_track("t1", "Song (feat. charlie puth)", "Charlie Puth")]
    _, name = sync.match_tracks_for_artist(tracks, "charlie puth")
    assert name == "Charlie Puth"


# ── 自動検出のカウント（客演の曖昧な名前でプレイリストを作らせない） ──

def test_count_artists_ignores_title_only_features():
    # 自動作成は実際の Spotify プレイリストを生む副作用があるので、曲名由来の名前は数えない
    tracks = [_track("t1", "Song (feat. Ghost)", "A"), _track("t2", "Another", "A")]
    counts = sync.count_artists(tracks)
    assert counts["a"][0] == 2
    assert "ghost" not in counts
