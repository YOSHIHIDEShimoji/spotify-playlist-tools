#!/usr/bin/env python3
"""import_history.py — Spotify Extended Streaming History を聴取ログへ取り込む（ワンタイム）

Spotify のプライバシーページから請求できる「拡張ストリーミング履歴」(Streaming_History_Audio_*.json)
を、sitegen が扱う聴取レコード形（listen_log.py / scrobble と同じ 1レコード=1再生）へ変換し、
年別 gzip JSONL（<out>/YYYY.jsonl.gz）に書き出す。これが累計/ヒートマップ/wrapped の
「本当の生涯履歴」基盤になる（recently-played は直近50件しか返らず 2019 まで遡れないため）。

方針:
- **1再生の定義 = ms_played >= 30秒**。Spotify の stream 定義・Last.fm scrobble の意味論に合わせ、
  数秒のスキップを再生として数えない（これで cumulative_top が実感と一致する）。
- **PII は落とす**。生の履歴には ip_addr / platform / offline_timestamp 等が含まれる。出力には
  track_id / name / artists / played_at だけを残す（scrobble・自前ログと同じ最小形）。
- **エピソード/ポッドキャスト/オーディオブックは除外**。spotify_track_uri と曲名がある行だけ通す。
- 年別に分割（純粋にファイルサイズ都合。読み込み側は全ファイルを連結する）。
- 出力は sort 済み・決定論的。何度流しても同じ結果（再エクスポートの上書き取り込みが安全）。

生の履歴（"Spotify Extended Streaming History/" や data/streaming_history/）は個人の生ログ＝
public リポに絶対コミットしない（.gitignore 済み）。出力の gz だけを data ブランチへ載せる。

Usage:
  python import_history.py --src "Spotify Extended Streaming History" --out-dir <data>/history
"""

import argparse
import glob
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

MIN_MS_PLAYED = 30_000  # 30秒未満は「再生」に数えない（Spotify の stream 閾値）


def _track_id(uri: str | None) -> str | None:
    """'spotify:track:ID' から ID を取り出す。track 以外（episode 等）の uri は None。"""
    if not uri or not uri.startswith("spotify:track:"):
        return None
    tid = uri.rsplit(":", 1)[-1]
    return tid or None


def iter_play_records(events, min_ms: int = MIN_MS_PLAYED):
    """拡張履歴イベント列を聴取レコード（1再生）へ変換して yield する純関数。

    通す条件（すべて満たす行だけ）:
    - master_metadata_track_name がある（＝楽曲。ポッドキャスト/オーディオブックを除外）
    - spotify_track_uri が spotify:track: で track_id に解決できる
    - ms_played >= min_ms（短いスキップを再生に数えない）

    出力レコード: {track_id, name, artists:[{name}], played_at}（scrobble/自前ログと同形）。
    """
    for ev in events:
        name = ev.get("master_metadata_track_name")
        if not name:
            continue
        tid = _track_id(ev.get("spotify_track_uri"))
        if not tid:
            continue
        if (ev.get("ms_played") or 0) < min_ms:
            continue
        played_at = ev.get("ts")
        if not played_at:
            continue
        artist = ev.get("master_metadata_album_artist_name")
        yield {
            "track_id": tid,
            "name": name,
            "artists": [{"name": artist}] if artist else [],
            "played_at": played_at,
        }


def _load_events(src: Path) -> tuple[list[dict], list[str]]:
    """src 配下（またはファイル）から Streaming_History_Audio_*.json を全部読む。イベント列とファイル一覧を返す。"""
    if src.is_file():
        files = [str(src)]
    else:
        files = sorted(glob.glob(str(src / "Streaming_History_Audio_*.json")))
    events: list[dict] = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            events.extend(json.load(fh))
    return events, files


def write_history(records: list[dict], out_dir: Path) -> dict[str, int]:
    """レコードを played_at の年（UTC）ごとに YYYY.jsonl.gz へ書き出す。年→件数を返す。

    決定論的にするため played_at で安定ソートし、同一 (played_at, track_id) の重複行は畳む
    （同じエクスポートを2回渡しても増えない）。既存の history/*.jsonl.gz は上書きする。
    """
    seen: set[tuple[str, str]] = set()
    by_year: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(records, key=lambda x: (x["played_at"], x["track_id"])):
        key = (r["played_at"], r["track_id"])
        if key in seen:
            continue
        seen.add(key)
        by_year[r["played_at"][:4]].append(r)

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for year, recs in sorted(by_year.items()):
        path = out_dir / f"{year}.jsonl.gz"
        # mtime=0 で gzip ヘッダを固定 → 中身が同じなら毎回バイト一致（無意味な差分を作らない）
        with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as gz:
            for rec in recs:
                gz.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        counts[year] = len(recs)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="拡張ストリーミング履歴 → 聴取ログ(gz JSONL) 取り込み")
    parser.add_argument(
        "--src",
        default="Spotify Extended Streaming History",
        help="Streaming_History_Audio_*.json のあるフォルダ（または単一ファイル）",
    )
    parser.add_argument("--out-dir", required=True, help="出力先（例: _data/data/history）")
    parser.add_argument(
        "--min-ms", type=int, default=MIN_MS_PLAYED, help="再生とみなす最小 ms_played（既定30000）"
    )
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"エラー: 入力が見つかりません: {src}", file=sys.stderr)
        return 1

    events, files = _load_events(src)
    records = list(iter_play_records(events, args.min_ms))
    counts = write_history(records, Path(args.out_dir))

    total = sum(counts.values())
    print(f"入力ファイル: {len(files)}  生イベント: {len(events)}")
    print(f"取り込んだ再生（>= {args.min_ms/1000:.0f}s）: {total}")
    for year, n in sorted(counts.items()):
        print(f"  {year}: {n:>6}")
    print(f"出力: {args.out_dir}/*.jsonl.gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
