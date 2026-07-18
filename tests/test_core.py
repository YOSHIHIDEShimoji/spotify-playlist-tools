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
