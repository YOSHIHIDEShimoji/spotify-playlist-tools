#!/usr/bin/env python3
"""lastfm_log.py — Last.fm scrobble 取り込み（30分毎）

Last.fm の全 scrobble（user.getRecentTracks）を取得し、月別 JSONL に追記する。
Spotify の recently-played（直近50件・要ポーリング）と違い、Last.fm は全再生を
50件制限なく永続保持するので、これを聴取統計の正とする（sitegen が scrobbles を優先集計）。

カーソル（前回取得済みの最大 uts）を <data-dir>/scrobbles/.cursor に持ち、from=cursor+1 で
差分だけ取る。新規0件なら何も書かない。今再生中（date 無し）のトラックはスキップする。

環境変数:
  LASTFM_API_KEY  必須（読み取り専用キー）。未設定なら静かに exit 0（graceful skip）。
  LASTFM_USER     省略時 "shimoji_"。

Usage:
  python lastfm_log.py --data-dir <dir>   # <dir>/scrobbles/*.jsonl に追記
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import core

API = "https://ws.audioscrobbler.com/2.0/"
DEFAULT_USER = "shimoji_"
PAGE_LIMIT = 200          # getRecentTracks は最大200/ページ
MAX_PAGES = 25            # 差分の安全上限（初回でも直近 25*200 まで）


def _api(method: str, **params) -> dict:
    key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not key:
        raise core.AuthRequired("LASTFM_API_KEY 未設定")
    query = {"method": method, "api_key": key, "format": "json", **params}
    req = Request(API + "?" + urlencode(query), headers={"User-Agent": "spotify-playlist-tools/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if isinstance(data, dict) and "error" in data:
        # 10=Invalid API key / 6=Invalid parameters(ユーザー不在) 等
        raise RuntimeError(f"Last.fm API error {data.get('error')}: {data.get('message')}")
    return data


def _image(images: list | None) -> str | None:
    """Last.fm の image 配列（size 別）から大きめの URL を返す。無ければ None。"""
    by = {img.get("size"): (img.get("#text") or "").strip() for img in (images or [])}
    for size in ("extralarge", "large", "medium", "small"):
        if by.get(size):
            return by[size]
    return None


def _to_record(track: dict) -> dict | None:
    date = track.get("date")
    if not date:  # now playing はまだ確定していないのでスキップ
        return None
    try:
        uts = int(date["uts"])
    except (KeyError, TypeError, ValueError):
        return None
    played_at = datetime.fromtimestamp(uts, tz=timezone.utc).isoformat()
    return {
        "played_at": played_at,
        "uts": uts,
        "name": track.get("name", ""),
        "artist": (track.get("artist") or {}).get("#text", ""),
        "album": (track.get("album") or {}).get("#text", ""),
        "mbid": (track.get("mbid") or None),
        "image": _image(track.get("image")),
    }


def poll(user: str, cursor: int | None, max_pages: int = MAX_PAGES) -> tuple[list[dict], int]:
    """getRecentTracks を新しい側から辿り、cursor より新しい scrobble を全部集める。

    Last.fm は新しい順に返す。from=cursor+1 を渡すと cursor 以降だけに絞れるので、
    ページを進めながら（totalPages まで）取り切る。初回（cursor なし）は from を渡さず
    直近 max_pages ぶんだけ取る（過去は Spotify エクスポートで別途取り込む方針）。
    """
    records: list[dict] = []
    seen: set[int] = set()
    max_uts = cursor or 0
    base: dict = {"user": user, "limit": PAGE_LIMIT}
    if cursor:
        base["from"] = cursor + 1  # cursor 自身は取得済み

    page = 1
    while page <= max_pages:
        data = _api("user.getrecenttracks", page=page, **base)
        rt = data.get("recenttracks", {})
        tracks = rt.get("track", [])
        if isinstance(tracks, dict):  # 1件だけだと dict で返ることがある
            tracks = [tracks]
        for t in tracks:
            rec = _to_record(t)
            if rec is None:
                continue
            uts = rec["uts"]
            if cursor and uts <= cursor:
                continue
            if uts in seen:
                continue
            seen.add(uts)
            records.append(rec)
            max_uts = max(max_uts, uts)
        attr = rt.get("@attr", {})
        try:
            total_pages = int(attr.get("totalPages", "1"))
        except (TypeError, ValueError):
            total_pages = 1
        if page >= total_pages or not tracks:
            break
        page += 1
        time.sleep(0.25)  # レート制限への配慮
    return records, max_uts


def _month_key(played_at: str) -> str:
    return core.to_jst(played_at).strftime("%Y-%m")


def append_records(scrobbles_dir: Path, records: list[dict]) -> int:
    """既存の uts と重複しないレコードを JST 月別ファイルへ追記。追記件数を返す。"""
    by_month: dict[str, list[dict]] = {}
    for rec in records:
        by_month.setdefault(_month_key(rec["played_at"]), []).append(rec)

    written = 0
    for month, recs in by_month.items():
        path = scrobbles_dir / f"{month}.jsonl"
        existing = {r.get("uts") for r in core.read_jsonl(path)}
        fresh = [r for r in recs if r["uts"] not in existing]
        if fresh:
            fresh.sort(key=lambda r: r["uts"])
            core.append_jsonl(path, fresh)
            written += len(fresh)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Last.fm scrobble 取り込み（getRecentTracks → JSONL）")
    parser.add_argument("--data-dir", required=True, help="data ディレクトリ（scrobbles/ を作る）")
    args = parser.parse_args()

    logger = core.setup_logging("lastfm_log")
    user = os.environ.get("LASTFM_USER", DEFAULT_USER).strip() or DEFAULT_USER
    scrobbles_dir = Path(args.data_dir) / "scrobbles"
    scrobbles_dir.mkdir(parents=True, exist_ok=True)
    cursor_path = scrobbles_dir / ".cursor"
    cursor = int(cursor_path.read_text().strip()) if cursor_path.exists() else None

    records, new_cursor = poll(user, cursor)
    written = append_records(scrobbles_dir, records)
    if written:
        cursor_path.write_text(str(new_cursor))
        logger.info(f"Last.fm scrobble: {written}件を追記しました（user={user}）")
    else:
        logger.info("新規の scrobble はありません")
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired:
        # API キー未設定。静かに諦める（設定後に有効化）。
        core.setup_logging("lastfm_log").info("LASTFM_API_KEY 未設定のためスキップします。")
        return core.EXIT_OK
    except (HTTPError, URLError) as e:
        core.setup_logging("lastfm_log").info(f"Last.fm への通信に失敗（次回再試行）: {e}")
        return core.EXIT_OK
    except Exception as e:
        core.setup_logging("lastfm_log").info(f"Last.fm 取り込みでエラー: {e}")
        raise


if __name__ == "__main__":
    sys.exit(_entry())
