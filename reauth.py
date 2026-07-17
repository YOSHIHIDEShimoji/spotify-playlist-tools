#!/usr/bin/env python3
"""reauth.py — 統合スコープでの対話再認証（Phase 0 の本人作業用）

ダッシュボード機能（聴取ログ・公式 Top・新譜ウォッチ）は既存4ツールより広い
スコープを要る。既存ツールのスコープを上げると未再認証時に validate_token で
壊れるため、スコープ拡張はこのスクリプトに分離した（dashboard-design §11-1）。

使い方（ローカルで対話実行）:
  python reauth.py
  → ブラウザが開き Spotify 認証。全スコープを許可する。
  → 完了後、キャッシュを Secrets に反映:
      gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify

このスクリプトは読み取りのみ（プレイリストを一切変更しない）。
"""

import sys

import core


def main() -> int:
    if core.is_headless():
        print(
            "reauth.py は対話実行専用です（ブラウザ認証が必要）。\n"
            "ローカルのターミナルで実行してください。",
            file=sys.stderr,
        )
        return core.EXIT_FATAL

    # SCOPE_ALL でトークンを取得。既存キャッシュがあっても、要求スコープが
    # 現行トークンの上位集合なら SpotifyOAuth が自動で再認証フローを開始する。
    sp = core.build_client(core.SCOPE_ALL)
    me = sp.me()

    # 付与されたスコープを実測で確認する（各機能が使えるか）。
    checks = {
        "user-read-recently-played": lambda: sp.current_user_recently_played(limit=1),
        "user-top-read": lambda: sp.current_user_top_artists(limit=1),
        "user-follow-read": lambda: sp.current_user_followed_artists(limit=1),
    }
    print(f"認証成功: {me.get('display_name') or me.get('id')}")
    print("スコープ実測:")
    all_ok = True
    for scope, probe in checks.items():
        try:
            probe()
            print(f"  ✓ {scope}")
        except Exception as e:  # noqa: BLE001 — 実測なので広く捕捉して結果表示に使う
            all_ok = False
            print(f"  ✗ {scope}  ({type(e).__name__})")

    print()
    if all_ok:
        print("全スコープ OK。次を実行してキャッシュを Secrets に反映してください:")
        print("  gh secret set SPOTIFY_TOKEN_CACHE < .cache-spotify")
        return core.EXIT_OK
    print(
        "一部スコープが付与されていません。ブラウザ認証画面で全項目を許可してから\n"
        "もう一度 reauth.py を実行してください。"
    )
    return core.EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
