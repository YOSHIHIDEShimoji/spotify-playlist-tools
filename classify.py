"""classify.py — アーティスト分類パイプライン

判定順（決定的・無料・高速な順）:
  1. 永続キャッシュ（artist_class_cache.json）
  2. ISRC 国コード（track.external_ids.isrc が "JP" 始まり → japanese）
  3. かな判定（ひらがな・カタカナ・半角カナを含む → japanese。漢字のみは保留）
  4. Spotify genres（取得できれば使う。現在はほぼ空）
  5. Gemini 一括（ここまでで残った未知アーティストをまとめて 1 リクエストで判定）

LLM は「キャッシュを埋める最後の手段」であり、実行パスの常連にしない。
"""

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_FILE = BASE_DIR / "artist_class_cache.json"
# 形式: {artist_id: {"name": str, "class": "japanese"|"western", "source": str, "date": "YYYY-MM-DD"}}
# git にコミットする（.gitignore に入れない）。壊れたら消して再生成できる。

# ひらがな(U+3040–309F)・カタカナ(U+30A0–30FF)・半角カナ(U+FF66–FF9F)。漢字は含めない。
HIRAGANA_KATAKANA = re.compile(r"[぀-ヿｦ-ﾟ]")

JAPANESE_GENRES = {
    "j-pop", "j-rock", "j-indie", "j-rap", "j-dance", "j-metal",
    "japanese", "anime", "city pop", "visual kei", "shibuya-kei",
    "kayokyoku", "enka", "j-ambient", "j-acoustic",
}

GEMINI_MODEL = "gemini-2.5-flash-lite"


def _today() -> str:
    return date.today().isoformat()


def _is_japanese_genre(genres) -> bool:
    for g in genres:
        gl = g.lower()
        if any(jg in gl for jg in JAPANESE_GENRES):
            return True
    return False


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache: dict) -> None:
    """一時ファイルに書いてから rename（atomic）。書き込み中断で壊さない。"""
    fd, tmp_path = tempfile.mkstemp(dir=str(BASE_DIR), prefix=".artist_cache_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, CACHE_FILE)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _remember(cache: dict, artist_id: str, name: str, cls: str, source: str) -> str:
    if artist_id:
        cache[artist_id] = {"name": name, "class": cls, "source": source, "date": _today()}
    return cls


def classify_track(sp, track: dict, cache: dict) -> str:
    """'japanese' / 'western' / 'unknown' を返す。unknown は呼び出し側が集約して
    classify_unknowns_with_gemini() へ一括で渡す（曲ごとに LLM を呼ばない）。"""
    artists = track.get("artists") or []
    if not artists:
        return "unknown"
    artist = artists[0]
    aid = artist.get("id")
    name = artist.get("name", "")

    # 1. 永続キャッシュ
    if aid and aid in cache:
        return cache[aid]["class"]

    # 2. ISRC 国コード（liked tracks のレスポンスに含まれる。追加 API コストゼロ）
    isrc = (track.get("external_ids") or {}).get("isrc", "") or ""
    if isrc[:2].upper() == "JP":
        return _remember(cache, aid, name, "japanese", "isrc")

    # 3. かな判定（曲名・アーティスト名・アルバム名のいずれかにかな）
    texts = [name, track.get("name", ""), (track.get("album") or {}).get("name", "")]
    if any(HIRAGANA_KATAKANA.search(t or "") for t in texts):
        return _remember(cache, aid, name, "japanese", "kana")
    if "japanese version" in (track.get("name", "") or "").lower():
        return _remember(cache, aid, name, "japanese", "name")
    # 漢字のみ（中国語の可能性）は japanese 確定にせず、次の手段へフォールスルー

    # 4. Spotify genres
    if aid:
        try:
            genres = sp.artist(aid).get("genres", [])
        except Exception:
            genres = []
        if genres:
            cls = "japanese" if _is_japanese_genre(genres) else "western"
            return _remember(cache, aid, name, cls, "genres")

    # 5. Gemini へ委ねる（呼び出し側が集約）
    return "unknown"


def classify_unknowns_with_gemini(unknown_artists: dict, cache: dict, logger=None) -> dict:
    """unknown_artists: {artist_id: name}。{artist_id: 'japanese'|'western'} を返す。
    GEMINI_API_KEY 未設定・失敗時は空 dict（→ 呼び出し側で unknown のまま扱う）。
    判定結果はキャッシュに書き戻す。"""
    result: dict[str, str] = {}
    if not unknown_artists:
        return result

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        if logger:
            logger.info("GEMINI_API_KEY 未設定のため一括判定をスキップ")
        return result

    try:
        from google import genai
    except ImportError:
        if logger:
            logger.info("google-genai 未インストールのため一括判定をスキップ")
        return result

    # 同名アーティストをまとめ、name→最初の id を保持
    id_by_name: dict[str, str] = {}
    for aid, name in unknown_artists.items():
        if name and name not in id_by_name:
            id_by_name[name] = aid
    names = list(id_by_name.keys())
    if not names:
        return result

    prompt = (
        "Classify each music artist as either \"japanese\" or \"western\" "
        "(western = any non-Japanese artist). Return a JSON object mapping each "
        "exact artist name to its class.\n"
        f"Artists: {json.dumps(names, ensure_ascii=False)}"
    )
    schema = {
        "type": "object",
        "properties": {n: {"type": "string", "enum": ["japanese", "western"]} for n in names},
    }
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={"response_mime_type": "application/json", "response_schema": schema},
        )
        data = json.loads(response.text)
    except Exception as e:
        if logger:
            logger.info(f"[gemini error] {e}")
        return result

    for name, cls in data.items():
        aid = id_by_name.get(name)
        cls = str(cls).strip().lower()
        if aid and cls in ("japanese", "western"):
            _remember(cache, aid, name, cls, "gemini")
            result[aid] = cls
    return result
