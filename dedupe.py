#!/usr/bin/env python3
"""dedupe.py — 重複・別バージョン検出エンジン

判定3段階（dedupe-requirements.md §3）:
  A 完全重複   同一トラックIDが同じプレイリストに2回以上（手動追加の事故）
  B 同一録音   ISRC が一致する別トラックID（Single 盤と Album 盤 等）
  C 別バージョン ISRC 不一致だが 正規化タイトル＋主アーティストID が一致
              （feat 違い / Remaster / Live / Acoustic / Taylor's Version 等）

正規化・グループ化は純関数（テスト対象）。scan() が管理プレイリストを横断して
1曲を1グループにまとめ、各出現プレイリストを列挙する（dashboard-design §5.3 dupes.json）。

削除の適用（apply）は siteops.py 側（Phase 3）が本モジュールの検証ロジックを使って行う。
本ファイル自体はプレイリストを変更しない（scan は読み取り専用）。

Usage:
  python dedupe.py --report                 # スキャン結果を stdout に JSON 出力（変更なし）
  python dedupe.py --report --data-dir DIR  # DIR/dupes.json に書き出す
"""

import argparse
import hashlib
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import core

# 別バージョン／コラボ表記を示すキーワード。これらを含む括弧群・ダッシュ接尾辞を
# タイトルから除いて「ベースタイトル」にする。
# すべて単語境界 \b で囲む。境界なしだと "deliver" に "live"、"left" に "ft"、
# "demons" に "demo"、"alive" に "live" が部分一致して別曲を誤マージする（レビュー C1）。
_VERSION_WORD = re.compile(
    r"\b(feat\.?|ft\.?|with|remaster(?:ed)?|re-?master(?:ed)?|live|acoustic|radio edit|"
    r"single version|album version|mono|stereo|deluxe|bonus|instrumental|"
    r"sped ?up|slowed|taylor'?s version|re-?recorded|demo|reprise|"
    r"edit|version|remix)\b",
    re.IGNORECASE,
)
_PAREN = re.compile(r"[（(\[][^）)\]]*[）)\]]")
_DASH_SUFFIX = re.compile(r"\s+[-–—]\s+.+$")


def normalize_title(title: str) -> str:
    """比較用のベースタイトルを返す（NFKC → バージョン表記除去 → 小文字化・空白圧縮）。"""
    t = unicodedata.normalize("NFKC", title or "")

    def _drop(m: re.Match) -> str:
        return "" if _VERSION_WORD.search(m.group(0)) else m.group(0)

    t = _PAREN.sub(_drop, t)
    m = _DASH_SUFFIX.search(t)
    if m and _VERSION_WORD.search(m.group(0)):
        t = t[: m.start()]
    return re.sub(r"\s+", " ", t).strip().lower()


def primary_artist_id(track: dict) -> str:
    artists = track.get("artists") or []
    if not artists:
        return ""
    return artists[0].get("id") or artists[0].get("name", "").lower()


def make_group_id(track_ids) -> str:
    joined = ",".join(sorted(track_ids))
    return "g-" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_groups(records: list[dict]) -> list[dict]:
    """records: 各トラック（track_id 一意）に playlists 出現情報を付けた dict のリスト。
    Tier B（ISRC 一致）と Tier C（正規化タイトル＋主アーティスト一致）で別 ID をまとめ、
    2件以上のグループだけを返す。各要素は dashboard-design §5.3 dupes.json の group 形。"""
    by_id = {r["id"]: r for r in records}
    uf = _UnionFind()
    for r in records:
        uf.find(r["id"])  # 単独ノードも登録

    # Tier B: ISRC が一致する別 ID を連結
    isrc_map: dict[str, list[str]] = {}
    for r in records:
        isrc = (r.get("isrc") or "").upper()
        if isrc:
            isrc_map.setdefault(isrc, []).append(r["id"])
    isrc_pairs: set[frozenset] = set()
    for ids in isrc_map.values():
        for other in ids[1:]:
            uf.union(ids[0], other)
        if len(ids) > 1:
            isrc_pairs.add(frozenset(ids))

    # Tier C: 正規化タイトル＋主アーティストID が一致する別 ID を連結
    key_map: dict[tuple, list[str]] = {}
    for r in records:
        key = (normalize_title(r.get("name", "")), primary_artist_id(r))
        if key[0]:
            key_map.setdefault(key, []).append(r["id"])
    for ids in key_map.values():
        for other in ids[1:]:
            uf.union(ids[0], other)

    # 連結成分を集約
    components: dict[str, list[str]] = {}
    for r in records:
        components.setdefault(uf.find(r["id"]), []).append(r["id"])

    groups: list[dict] = []
    for ids in components.values():
        if len(ids) < 2:
            continue
        # tier 判定（レビュー C2）: グループ全員が同一の非空 ISRC のときだけ B（＝真の同一録音）。
        # ISRC がばらつく／空を含む成分は、タイトルで推移的に引き込まれた別録音が混ざりうるので
        # C（別バージョン候補・機械は決めない）へ丸める。B を「ほぼ確実に消してよい」と誤提示しない。
        isrcs = {(by_id[i].get("isrc") or "").upper() for i in ids}
        all_same_isrc = len(isrcs) == 1 and "" not in isrcs
        tier = "B" if all_same_isrc else "C"
        reason = "isrc" if tier == "B" else "title"
        tracks = [_track_view(by_id[i]) for i in ids]
        # 表示順: アルバム種別（album 優先）→ 人気度降順で参考推奨が上に来るように
        tracks.sort(key=lambda t: (_album_rank(t["album_type"]), -(t.get("popularity") or 0)))
        groups.append(
            {"id": make_group_id(ids), "tier": tier, "reason": reason, "tracks": tracks}
        )
    groups.sort(key=lambda g: (g["tier"], g["tracks"][0]["name"].lower()))
    return groups


def build_intra_dupes(intra: dict[tuple, int], images: dict[str, str] | None = None) -> list[dict]:
    """Tier A（同一プレイリスト内で同じ track_id が2回以上）を報告用に整形。
    intra: {(playlist_id, playlist_name, track_id, name, artists_tuple): count}。
    images: track_id → サムネイル URL（あればカードに表示）。"""
    images = images or {}
    out: list[dict] = []
    for (pid, pname, tid, name, artists), count in intra.items():
        if count > 1:
            out.append(
                {
                    "id": make_group_id([f"{pid}:{tid}"]),
                    "tier": "A",
                    "reason": "same-id-in-playlist",
                    "playlist": {"id": pid, "name": pname},
                    "track": {"id": tid, "name": name, "artists": list(artists),
                              "image": images.get(tid)},
                    "count": count,
                }
            )
    return out


def _album_rank(album_type: str) -> int:
    return {"album": 0, "compilation": 1, "single": 2}.get(album_type, 3)


def _track_view(r: dict) -> dict:
    album = r.get("album") or {}
    return {
        "id": r["id"],
        "name": r.get("name", ""),
        "artists": [a.get("name", "") for a in (r.get("artists") or [])],
        "album": album.get("name", ""),
        "album_type": album.get("album_type", ""),
        "release_date": album.get("release_date", ""),
        "duration_ms": r.get("duration_ms"),
        "popularity": r.get("popularity"),
        "isrc": (r.get("isrc") or ""),
        "image": _album_image(album),
        "playlists": r.get("playlists", []),
    }


_SCAN_FIELDS = (
    "items(track(id,name,artists(id,name),external_ids,duration_ms,popularity,"
    "album(name,album_type,release_date,images))),next"
)


def _album_image(album: dict) -> str | None:
    """アルバム画像のうち最小サイズ（末尾）の URL を返す。サムネイル表示用（帯域節約）。"""
    imgs = album.get("images") or []
    return imgs[-1].get("url") if imgs else None


def collect_records(sp, playlists: list[dict]) -> tuple[list[dict], dict]:
    """playlists: [{"id","name"}]。横断して track 一意レコード（出現プレイリスト付き）と
    Tier A 用の (playlist, track) 出現回数を返す。読み取り専用。sitegen と共有する。"""
    records: dict[str, dict] = {}
    intra: dict[tuple, int] = {}
    for pl in playlists:
        pid, pname = pl["id"], pl.get("name", pl["id"])
        for track in core.iter_playlist_tracks(sp, pid, _SCAN_FIELDS):
            tid = track["id"]
            artists_tuple = tuple(a.get("name", "") for a in (track.get("artists") or []))
            intra_key = (pid, pname, tid, track.get("name", ""), artists_tuple)
            intra[intra_key] = intra.get(intra_key, 0) + 1
            if tid not in records:
                rec = dict(track)
                rec["isrc"] = (track.get("external_ids") or {}).get("isrc", "")
                rec["playlists"] = []
                records[tid] = rec
            occ = {"id": pid, "name": pname}
            if occ not in records[tid]["playlists"]:
                records[tid]["playlists"].append(occ)
    return list(records.values()), intra


def scan(sp, playlists: list[dict], keep_sets: set | None = None) -> dict:
    """横断スキャンして dupes.json 構造を返す（読み取り専用）。"""
    records, intra = collect_records(sp, playlists)
    return dupes_from_records(records, intra, keep_sets)


def dupes_from_records(records: list[dict], intra: dict, keep_sets: set | None = None) -> dict:
    """collect_records の出力から dupes.json 構造を組む（純関数・テスト可能）。
    keep_sets: 「両方残す」と決めたトラック ID 集合（frozenset）の集合。該当 B/C グループは除外する
    （レビュー H2・dedupe-requirements §4.3）。"""
    keep_sets = keep_sets or set()
    groups = build_groups(records)
    if keep_sets:
        groups = [g for g in groups if frozenset(t["id"] for t in g["tracks"]) not in keep_sets]
    images = {r["id"]: _album_image(r.get("album") or {}) for r in records}
    groups = groups + build_intra_dupes(intra, images)
    groups.sort(key=lambda g: {"A": 0, "B": 1, "C": 2}.get(g["tier"], 9))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {t: sum(1 for g in groups if g["tier"] == t) for t in ("A", "B", "C")},
        "groups": groups,
    }


def load_keep_sets(data_dir) -> set:
    """dedupe_keep.json から「両方残す」トラック ID 集合の集合を読む。"""
    from pathlib import Path

    path = Path(data_dir) / "dedupe_keep.json"
    if not path.exists():
        return set()
    try:
        import json

        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    return {frozenset(g.get("track_ids", [])) for g in raw.get("groups", [])}


def managed_playlists() -> list[dict]:
    """inbox.txt / sync.txt / sort.txt から管理プレイリスト（id, name）を集める。
    name は後段の scan 中に上書きしないため、ここでは id と暫定 name を返す。"""
    import inbox
    import sync

    ids: dict[str, str] = {}
    jp, western, jp_artists = inbox.load_inbox_config(inbox.INBOX_CONFIG_PATH)
    ids[jp] = "Japanese Musics"
    ids[western] = "Western Musics"
    for name, pid in jp_artists.items():
        ids.setdefault(pid, name)
    try:
        _src, sync_artists = sync.load_config(sync.CONFIG_PATH)
        for name, pid in sync_artists.items():
            ids.setdefault(pid, name)
    except Exception:
        pass
    return [{"id": pid, "name": name} for pid, name in ids.items()]


def main() -> int:
    parser = argparse.ArgumentParser(description="重複・別バージョン検出（スキャンのみ）")
    parser.add_argument("--report", action="store_true", help="スキャン結果を出力（変更なし）")
    parser.add_argument("--data-dir", help="出力先ディレクトリ（dupes.json を書く）")
    args = parser.parse_args()

    logger = core.setup_logging("dedupe")
    sp = core.build_client("playlist-read-private")
    playlists = managed_playlists()
    result = scan(sp, playlists)
    c = result["counts"]
    logger.info(f"重複グループ: A={c['A']} B={c['B']} C={c['C']}（対象 {len(playlists)} プレイリスト）")

    if args.data_dir:
        core.atomic_write_json(Path(args.data_dir) / "dupes.json", result)
    else:
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2))
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("dedupe").info(f"[auth] {e}")
        return core.EXIT_AUTH


if __name__ == "__main__":
    sys.exit(_entry())
