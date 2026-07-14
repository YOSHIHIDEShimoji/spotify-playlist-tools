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
