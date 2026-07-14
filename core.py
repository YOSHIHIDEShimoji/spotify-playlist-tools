"""core.py — Spotify ツール共通基盤

クライアント生成・ページング取得・バッチ処理・設定パーサ・ロギングを集約する。
旧 spotify_utils.py はこのモジュールに吸収した（free_redirect_port の LISTEN 限定修正込み）。

ヘッドレス（GitHub Actions / launchd）実行ではブラウザ認証フローを開始せず、
トークンが失効していれば AuthRequired を送出する。対話実行時のみ再認証できる。
"""

import logging
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CACHE_PATH = BASE_DIR / ".cache-spotify"
LOG_DIR = BASE_DIR / "log"

# exit code の意味づけ（fable5-redesign §3）
EXIT_OK = 0       # 成功
EXIT_FATAL = 1    # 致命的エラー（例外）
EXIT_PARTIAL = 2  # 一部スキップ（unknown あり。失敗ではなく「要人間判断」）
EXIT_AUTH = 3     # 再認証が必要（ヘッドレスでトークン失効）


class AuthRequired(Exception):
    """ヘッドレス実行でトークンが無効。人手の再認証が必要。"""


def is_headless() -> bool:
    """GitHub Actions か、tty を持たない実行（launchd など）なら True。"""
    if os.getenv("CI") == "true":
        return True
    try:
        return not sys.stdin.isatty()
    except (ValueError, OSError):
        return True


def is_dry_run() -> bool:
    """--dry-run 指定、または DRY_RUN=1 環境変数で有効。"""
    return os.getenv("DRY_RUN") == "1" or "--dry-run" in sys.argv


def build_client(scope: str) -> spotipy.Spotify:
    load_dotenv(ENV_PATH)  # CI では .env が無く no-op（env は Secrets 由来）
    for key in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"):
        if not os.getenv(key):
            raise RuntimeError(f"{key} が設定されていません（.env または環境変数）")

    headless = is_headless()
    auth = SpotifyOAuth(scope=scope, cache_path=str(CACHE_PATH), open_browser=not headless)

    if headless:
        # ブラウザフローを絶対に開始しない。キャッシュ→refresh で取れなければ即 AuthRequired。
        token = auth.validate_token(auth.cache_handler.get_cached_token())
        if not token:
            raise AuthRequired(
                "Spotify トークンが失効しています。ローカルで対話再認証してキャッシュを "
                "更新してください（README の再認証手順を参照）。"
            )
    else:
        _free_redirect_port()  # 対話実行時のみ、残留した OAuth サーバを掃除する

    return spotipy.Spotify(auth_manager=auth)


def iter_playlist_tracks(sp: spotipy.Spotify, playlist_id: str, fields: str):
    """プレイリストの全 track を順に yield する。fields には ',next' を必ず含めて渡す。"""
    results = sp.playlist_items(
        playlist_id, fields=fields, additional_types=("track",), limit=100
    )
    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                yield track
        results = sp.next(results) if results.get("next") else None


def add_in_batches(sp: spotipy.Spotify, playlist_id: str, track_ids, batch: int = 100) -> None:
    for i in range(0, len(track_ids), batch):
        sp.playlist_add_items(playlist_id, track_ids[i : i + batch])


def remove_in_batches(sp: spotipy.Spotify, playlist_id: str, track_ids, batch: int = 100) -> None:
    uris = [f"spotify:track:{tid}" for tid in track_ids]
    for i in range(0, len(uris), batch):
        sp.playlist_remove_all_occurrences_of_items(playlist_id, uris[i : i + batch])


def parse_config(path: Path) -> dict[str, str]:
    """KEY=VALUE 形式（#コメント・空行スキップ、値に = を含んでも可）。"""
    cfg: dict[str, str] = {}
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip()
    return cfg


_ID_RE = re.compile(r"playlist[/:]([A-Za-z0-9]+)")


def extract_playlist_id(url_or_id: str) -> str:
    """URL / spotify:playlist:URI / 素の ID / ?si= 付き のいずれからも ID を取り出す。"""
    m = _ID_RE.search(url_or_id)
    return m.group(1) if m else url_or_id.strip()


def append_line(path: Path, line: str) -> None:
    """設定ファイルへ1行追記する。末尾が改行でない場合は先に改行を足す（設定破損防止）。"""
    needs_nl = False
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_nl = f.read(1) != b"\n"
    with path.open("a") as f:
        if needs_nl:
            f.write("\n")
        f.write(line if line.endswith("\n") else line + "\n")


def setup_logging(name: str) -> logging.Logger:
    """print の代替。フォーマット: [YYYY-MM-DD HH:MM:SS] message。
    stdout へは常に出す。CI 以外ではローカルの log/<name>.log にも書く。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if os.getenv("CI") != "true":
        try:
            LOG_DIR.mkdir(exist_ok=True)
            fileh = logging.FileHandler(LOG_DIR / f"{name}.log")
            fileh.setFormatter(fmt)
            logger.addHandler(fileh)
        except OSError:
            pass
    return logger


def _free_redirect_port() -> None:
    """OAuth リダイレクトポートを掴んでいる残留プロセスを掃除する（対話実行時のみ）。

    LISTEN 状態のプロセスだけを対象にし、接続中のクライアントは殺さない（bugs §2）。
    SIGTERM 後はポート解放をポーリングで待ってから戻る。
    """
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "")
    try:
        port = int(redirect_uri.rsplit(":", 1)[-1].split("/")[0])
    except (ValueError, IndexError):
        return

    if not _port_in_use(port):
        return

    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True, text=True,
    )
    pids = [p for p in result.stdout.strip().splitlines() if p]
    if not pids:
        return
    for pid_str in pids:
        try:
            os.kill(int(pid_str), 15)  # SIGTERM
        except (ValueError, ProcessLookupError):
            pass
    for _ in range(25):  # 0.2秒 × 25 = 最大5秒、ポート解放を待つ
        if not _port_in_use(port):
            return
        time.sleep(0.2)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0
