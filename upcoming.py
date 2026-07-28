#!/usr/bin/env python3
"""upcoming.py — これから出るリリース（MusicBrainz ベース）

Spotify には「未発売のリリース」を返す API が無い（新譜は出た後にしか見えない）。
MusicBrainz は発売予定日を持つので、フォロー中アーティストの未来日付のリリースを拾う。

アーティストの対応付けは名前ではなく **Spotify の URL 関連付け** で行う:

    GET /ws/2/url?resource=https://open.spotify.com/artist/<id>&inc=artist-rels

これで「同名の別アーティスト」を掴む事故が起きない。引けた MBID は mb_cache.json に永続化し、
以降は問い合わせない（MBID は変わらない）。

MusicBrainz は 1req/秒を求めるので、間隔を空けつつ「1晩あたりの問い合わせ数」に上限を置く。
毎晩少しずつ進めて、最後にチェックした時刻が古い順に回す（全員が定期的に更新される）。
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import core

WS = "https://musicbrainz.org/ws/2"
UA = "spotify-playlist-tools/1.0 (+https://github.com/YOSHIHIDEShimoji/spotify-playlist-tools)"

REQUEST_INTERVAL = 1.1     # MusicBrainz の作法（1req/秒）を守る
LOOKUP_BUDGET = 25         # 1晩に MBID を新規解決する数
REFRESH_BUDGET = 35        # 1晩にリリース一覧を見に行く数（古い順）
MISS_RETRY_DAYS = 30       # 「MusicBrainz に居なかった」人を再確認するまでの日数
HORIZON_DAYS = 400         # これ以上先の予定は出さない（誤登録が多いため）


def _get(path: str, **params) -> dict:
    url = f"{WS}/{path}?" + urlencode({**params, "fmt": "json"})
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def lookup_mbid(spotify_artist_id: str, fetch=None) -> str | None:
    """Spotify のアーティスト URL から MusicBrainz の MBID を引く。見つからなければ None。"""
    resource = f"https://open.spotify.com/artist/{spotify_artist_id}"
    fetch = fetch or (
        lambda: _get("url", resource=resource, inc="artist-rels")
    )
    try:
        payload = fetch()
    except HTTPError as e:
        if e.code == 404:  # その URL が MusicBrainz に登録されていない（よくある）
            return None
        raise
    for rel in payload.get("relations") or []:
        artist = rel.get("artist") or {}
        if artist.get("id"):
            return artist["id"]
    return None


def future_releases(payload: dict, today: str, horizon: str) -> list[dict]:
    """release-group 一覧から「今日より後・horizon まで」のものを純粋に抽出する。

    MusicBrainz の first-release-date は "YYYY" や "YYYY-MM" のこともある。日まで確定して
    いないものは「いつ出るか分からない」ので出さない（予定として役に立たない）。
    """
    out = []
    for rg in payload.get("release-groups") or []:
        date = (rg.get("first-release-date") or "").strip()
        if len(date) != 10 or not (today < date <= horizon):
            continue
        out.append({
            "title": rg.get("title", ""),
            "date": date,
            "type": rg.get("primary-type") or "",
        })
    out.sort(key=lambda x: (x["date"], x["title"]))
    return out


def _due(entry: dict, now: datetime) -> bool:
    """MBID 未解決の人を再度引きにいくべきか（見つからなかった人は 30日後にもう一度）。"""
    if entry.get("mbid"):
        return False
    checked = entry.get("checked")
    if not checked:
        return True
    try:
        last = core.parse_iso(checked)
    except (ValueError, TypeError):
        return True
    return now - last > timedelta(days=MISS_RETRY_DAYS)


def select_for_refresh(cache: dict, limit: int) -> list[str]:
    """リリース一覧を見に行く対象を「最後に見た時刻が古い順」で選ぶ（全員に順番が回る）。"""
    solved = [(sid, e) for sid, e in cache.items() if e.get("mbid")]
    solved.sort(key=lambda kv: kv[1].get("refreshed") or "")
    return [sid for sid, _ in solved[:limit]]


def build_upcoming(sp, data: Path, logger=None, now: datetime | None = None) -> dict:
    """フォロー中アーティストの発売予定を集めて upcoming.json を書く。

    毎晩少しずつ進む設計（MBID 解決 25件 / リリース確認 35件）。取得できたぶんだけ出す。
    """
    now = now or datetime.now(core.JST)
    out_path = data / "upcoming.json"
    cache_path = data / "mb_cache.json"

    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")).get("artists") or {}
        except (OSError, json.JSONDecodeError):
            cache = {}

    # フォロー中アーティスト（新譜と同じ母集団）
    followed: dict[str, str] = {}
    after = None
    while True:
        arts = core.retry_api(
            lambda a=after: sp.current_user_followed_artists(limit=50, after=a), what="followed_artists"
        ).get("artists", {})
        for a in arts.get("items", []):
            followed[a["id"]] = a.get("name", "")
        after = arts.get("cursors", {}).get("after")
        if not after or not arts.get("items"):
            break

    for sid, name in followed.items():
        cache.setdefault(sid, {"name": name, "mbid": None, "checked": None, "refreshed": None})
        cache[sid]["name"] = name or cache[sid].get("name", "")

    # 1) MBID の解決（未解決＝新しくフォローした人／前回見つからなかった人）
    pending = [sid for sid in followed if _due(cache[sid], now)][:LOOKUP_BUDGET]
    for sid in pending:
        time.sleep(REQUEST_INTERVAL)
        try:
            cache[sid]["mbid"] = lookup_mbid(sid)
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as e:
            if logger:
                logger.info(f"upcoming: MBID 解決に失敗 {cache[sid].get('name')} ({e})")
            continue
        cache[sid]["checked"] = core.now_utc_iso()

    # 2) リリース一覧（古い順に少しずつ）
    today = now.date().isoformat()
    horizon = (now + timedelta(days=HORIZON_DAYS)).date().isoformat()
    items: list[dict] = []
    for sid in select_for_refresh({k: v for k, v in cache.items() if k in followed}, REFRESH_BUDGET):
        time.sleep(REQUEST_INTERVAL)
        try:
            payload = _get("release-group", artist=cache[sid]["mbid"], limit=100)
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as e:
            if logger:
                logger.info(f"upcoming: リリース取得に失敗 {cache[sid].get('name')} ({e})")
            continue
        cache[sid]["refreshed"] = core.now_utc_iso()
        cache[sid]["upcoming"] = future_releases(payload, today, horizon)

    # キャッシュに溜めた予定を全部出す（今夜見に行かなかった人のぶんも残っている）
    for sid, entry in cache.items():
        if sid not in followed:
            continue
        for rel in entry.get("upcoming") or []:
            if rel["date"] > today:  # 発売済みになったものは落とす
                items.append({**rel, "artist": entry.get("name", ""), "artist_id": sid})
    items.sort(key=lambda x: (x["date"], x["artist"]))

    core.atomic_write_json(cache_path, {"generated_at": core.now_utc_iso(), "artists": cache})
    payload = {
        "generated_at": core.now_utc_iso(),
        "source": "musicbrainz",
        "items": items,
        "known": sum(1 for sid in followed if cache[sid].get("mbid")),
        "followed": len(followed),
    }
    core.atomic_write_json(out_path, payload)
    if logger:
        logger.info(f"upcoming: {len(items)}件（MBID 解決済み {payload['known']}/{payload['followed']}）")
    return payload


__all__ = ["build_upcoming", "future_releases", "lookup_mbid", "select_for_refresh"]
