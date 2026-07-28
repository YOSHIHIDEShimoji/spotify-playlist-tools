#!/usr/bin/env python3
"""recommend.py — 「似ている」おすすめの生成（Last.fm ベース）

Spotify 公式のおすすめ API は 2024-11 に新規アプリ向けへ閉じられ、このアプリでも
使えないことを実測で確認している（2026-07-29 時点）:

    GET /v1/recommendations               → 404
    GET /v1/artists/{id}/related-artists  → 404
    GET /v1/audio-features                → 403

そこで似ているアーティスト/曲は Last.fm（artist.getSimilar / track.getSimilar）で出す。
Last.fm 側の「似ている度合い(match)」に、こちらの生涯再生回数（affinity）を掛けて重み付け
するので、公式の汎用おすすめより本人寄りの結果になる。

出力（<data>/recs.json）は「なぜこれが出ているか（because）」を必ず持たせる。基準の分からない
おすすめは出さない、というのがこの画面の方針。

環境変数:
  LASTFM_API_KEY  未設定なら空の recs.json を書いて静かに終了（サイトは「未設定」表示になる）。
"""

import json
import math
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import core

API = "https://ws.audioscrobbler.com/2.0/"
UA = "spotify-playlist-tools/1.0 (+https://github.com/YOSHIHIDEShimoji/spotify-playlist-tools)"

ARTIST_SEEDS = 40        # 生涯上位いくつのアーティストを種にするか
TRACK_SEEDS = 30         # 同・曲
SIMILAR_PER_SEED = 20    # 1つの種から取る候補数
ARTIST_RESULTS = 30      # 出力するアーティスト候補
TRACK_RESULTS = 40       # 出力する曲候補
REQUEST_INTERVAL = 0.2   # Last.fm への最短間隔（秒）
RESOLVE_BUDGET = 60      # 1晩に Spotify 検索で解決する件数の上限


def _api(method: str, **params) -> dict:
    """Last.fm を叩く。失敗（HTTP/ネットワーク/APIエラー）は呼び出し側で握れるよう例外にする。"""
    key = os.environ.get("LASTFM_API_KEY", "").strip()
    if not key:
        raise core.AuthRequired("LASTFM_API_KEY 未設定")
    query = {"method": method, "api_key": key, "format": "json", **params}
    req = Request(API + "?" + urlencode(query), headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"Last.fm API error {data.get('error')}: {data.get('message')}")
    return data


def _affinity(count: int) -> float:
    """再生回数 → 種としての重み。回数が桁違いでも1人に支配されないよう対数で潰す。

    The Beatles 15592回 と 2位 3377回 をそのまま掛けると、おすすめが全部ビートルズ由来に
    なってしまう。log で 9.65 対 8.12 に圧縮し、他の種にも出番を残す。
    """
    return math.log1p(max(count, 0))


def norm(name: str) -> str:
    """比較用の正規化。表記揺れ（大小・前後空白・remaster 等の版違い）を畳む。"""
    s = (name or "").strip().lower()
    for sep in (" - ", " – "):
        if sep in s:
            s = s.split(sep, 1)[0]
    return " ".join(s.split())


def _key(artist: str, track: str) -> str:
    return f"{norm(artist)}|{norm(track)}"


def similar_artists(seeds: list[dict], known: set[str], fetch=None,
                    limit: int = SIMILAR_PER_SEED) -> list[dict]:
    """似ているアーティストを集計して返す（純関数・fetch を差し替えればテストできる）。

    seeds は [{"name", "count"}]（生涯再生の多い順）。known に入っている（＝すでに聴いている）
    アーティストは候補から外す。スコアは Σ match × affinity(種の再生回数)。
    """
    fetch = fetch or (lambda name: _api("artist.getSimilar", artist=name, limit=limit))
    scores: dict[str, float] = {}
    names: dict[str, str] = {}
    because: dict[str, list[dict]] = {}
    for seed in seeds:
        try:
            payload = fetch(seed["name"])
        except Exception:  # noqa: BLE001 — 1つの種が落ちても他を使う
            continue
        weight = _affinity(seed["count"])
        for item in (payload.get("similarartists") or {}).get("artist", []) or []:
            name = (item.get("name") or "").strip()
            if not name or norm(name) in known:
                continue
            try:
                match = float(item.get("match") or 0)
            except (TypeError, ValueError):
                continue
            if match <= 0:  # 類似度ゼロ/欠損は「似ている」根拠にならないので候補にしない
                continue
            k = norm(name)
            names.setdefault(k, name)
            scores[k] = scores.get(k, 0.0) + match * weight
            because.setdefault(k, []).append({"name": seed["name"], "count": seed["count"]})
    out = [
        {
            "name": names[k],
            "score": round(score, 4),
            # 「なぜ出ているか」は寄与の大きい種から最大2件。再生回数の多い種を優先。
            "because": sorted(because[k], key=lambda b: -b["count"])[:2],
        }
        for k, score in scores.items()
    ]
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out


def similar_tracks(seeds: list[dict], known: set[str], fetch=None,
                   limit: int = 10) -> list[dict]:
    """似ている曲を集計して返す。known（すでに聴いた曲の key）は候補から外す。"""
    fetch = fetch or (
        lambda artist, track: _api("track.getSimilar", artist=artist, track=track, limit=limit)
    )
    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}
    because: dict[str, dict] = {}
    for seed in seeds:
        artists = seed.get("artists") or []
        if not artists:
            continue
        try:
            payload = fetch(artists[0], seed["name"])
        except Exception:  # noqa: BLE001
            continue
        weight = _affinity(seed["count"])
        for item in (payload.get("similartracks") or {}).get("track", []) or []:
            name = (item.get("name") or "").strip()
            artist = ((item.get("artist") or {}).get("name") or "").strip()
            if not name or not artist:
                continue
            k = _key(artist, name)
            if k in known:
                continue
            try:
                match = float(item.get("match") or 0)
            except (TypeError, ValueError):
                continue
            if match <= 0:  # 類似度ゼロ/欠損は「似ている」根拠にならないので候補にしない
                continue
            meta.setdefault(k, {"name": name, "artist": artist})
            scores[k] = scores.get(k, 0.0) + match * weight
            if k not in because or seed["count"] > because[k]["count"]:
                because[k] = {"name": seed["name"], "count": seed["count"]}
    out = [
        {**meta[k], "score": round(score, 4), "because": because[k]}
        for k, score in scores.items()
    ]
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out


def _load(path: Path, key: str) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key) or []
    except (OSError, json.JSONDecodeError):
        return []


def _known_artist_keys(artists: list[dict]) -> set[str]:
    return {norm(a["name"]) for a in artists if a.get("name")}


def _known_track_keys(tracks: list[dict], search_index: list[dict]) -> set[str]:
    keys = set()
    for t in tracks:
        for a in t.get("artists") or []:
            keys.add(_key(a, t.get("name", "")))
    for t in search_index:
        for a in t.get("artists") or []:
            keys.add(_key(a, t.get("name", "")))
    return keys


def _resolve_tracks(sp, rows: list[dict], cache: dict, budget: int) -> int:
    """おすすめ曲を Spotify の track id に解決する（再生ボタンを出すため）。

    名前で1回引いた結果はキャッシュに残す（見つからなかったことも None で覚える）ので、
    翌晩以降の API 消費は「新しく出てきた曲」のぶんだけになる。
    """
    used = 0
    for row in rows:
        k = _key(row["artist"], row["name"])
        if k not in cache:
            if used >= budget:
                continue
            used += 1
            try:
                q = f'track:{row["name"]} artist:{row["artist"]}'
                items = sp.search(q=q, type="track", limit=1).get("tracks", {}).get("items", [])
            except Exception:  # noqa: BLE001
                continue
            if items:
                album = items[0].get("album") or {}
                imgs = album.get("images") or []
                cache[k] = {"id": items[0]["id"], "image": imgs[-1]["url"] if imgs else None}
            else:
                cache[k] = None
        hit = cache.get(k)
        if hit:
            row["id"] = hit.get("id")
            if hit.get("image"):
                row["image"] = hit["image"]
    return used


def build_recs(sp, data: Path, logger=None) -> dict:
    """recs.json を生成して書き出し、その内容を返す。

    LASTFM_API_KEY が無い / Last.fm が落ちている場合は available=False の空データを書く
    （サイト側が「なぜ出せないか」を出せるようにするため。404 にはしない）。
    """
    out_path = data / "recs.json"
    artists = _load(data / "lifetime_artists.json", "artists")
    tracks = _load(data / "lifetime_tracks.json", "tracks")
    if not artists and not tracks:
        return {}

    empty = {"generated_at": core.now_utc_iso(), "source": "lastfm", "available": False,
             "artists": [], "tracks": []}
    if not os.environ.get("LASTFM_API_KEY", "").strip():
        core.atomic_write_json(out_path, {**empty, "reason": "LASTFM_API_KEY 未設定"})
        return empty

    known_artists = _known_artist_keys(artists)
    known_tracks = _known_track_keys(tracks, _load(data / "search_index.json", "tracks"))

    def throttled_artist(name):
        time.sleep(REQUEST_INTERVAL)
        return _api("artist.getSimilar", artist=name, limit=SIMILAR_PER_SEED)

    def throttled_track(artist, track):
        time.sleep(REQUEST_INTERVAL)
        return _api("track.getSimilar", artist=artist, track=track, limit=10)

    try:
        rec_artists = similar_artists(artists[:ARTIST_SEEDS], known_artists, fetch=throttled_artist)
        rec_tracks = similar_tracks(tracks[:TRACK_SEEDS], known_tracks, fetch=throttled_track)
    except (core.AuthRequired, HTTPError, URLError, RuntimeError) as e:
        core.atomic_write_json(out_path, {**empty, "reason": str(e)})
        if logger:
            logger.info(f"recs スキップ: {e}")
        return empty

    rec_artists = rec_artists[:ARTIST_RESULTS]
    rec_tracks = rec_tracks[:TRACK_RESULTS]

    # Spotify の ID / 画像を載せる（再生ボタンとサムネイルのため）。失敗しても本体は出す。
    cache_path = data / "rec_resolve_cache.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    if sp is not None:
        try:
            used = _resolve_tracks(sp, rec_tracks, cache, RESOLVE_BUDGET)
            core.atomic_write_json(cache_path, cache)
            if logger:
                logger.info(f"recs: 曲の解決 {used} 件（キャッシュ {len(cache)} 件）")
        except Exception as e:  # noqa: BLE001
            if logger:
                logger.info(f"recs の Spotify 解決をスキップ: {e}")

    payload = {
        "generated_at": core.now_utc_iso(),
        "source": "lastfm",
        "available": True,
        "artists": rec_artists,
        "tracks": rec_tracks,
    }
    core.atomic_write_json(out_path, payload)
    if logger:
        logger.info(f"recs: アーティスト {len(rec_artists)} / 曲 {len(rec_tracks)}")
    return payload
