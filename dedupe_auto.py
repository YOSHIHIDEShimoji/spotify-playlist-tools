#!/usr/bin/env python3
"""dedupe_auto.py — nightly の自動整理（同一録音のみ・安全側）

docs/dedupe-auto-requirements.md の適格判定を満たすグループだけを、
album > single > compilation の順で1曲だけ残して削除する。
別バージョン（feat/with/Remaster/Live 等・Tier C）、同一 ISRC でも秒数差 >3s、
版差表記の食い違い、「両方残す」登録済みグループには一切触れない
（＝好みのバージョンが自動で消える経路を持たない）。

削除は siteops._apply_removals（dedupe-apply と共有する唯一の削除経路）を通し、
undo を先に確定する。結果は step_summary 'dedupe' に書き、sitegen が
ホームの実行履歴（inbox/sync/sort/archive と同じ内訳モーダル）へ載せる。

失敗しても nightly 本体を落とさない（何もせず正常終了）。dry-run では変更系 API を呼ばない。

Usage:
  python dedupe_auto.py --data-dir <dir>   # <dir> は undo / dedupe_keep.json のある data ディレクトリ
"""

import argparse
import sys
from pathlib import Path

import core
import dedupe

# siteops と同一。現行トークンに含まれるため再認証前でも動く。
SCOPE = (
    "playlist-modify-private playlist-modify-public playlist-read-private "
    "user-library-read user-library-modify"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="nightly 自動整理（同一録音のみ）")
    parser.add_argument("--data-dir", required=True, help="undo / dedupe_keep.json のある data ディレクトリ")
    args = parser.parse_args()

    logger = core.setup_logging("dedupe-auto")
    data = Path(args.data_dir)
    dry = core.is_dry_run()

    try:
        sp = core.build_client(SCOPE)
        # fail-closed: 保留ファイルが壊れていたら「両方残す」を保証できないので自動整理しない。
        if not dedupe.keep_file_readable(data):
            logger.info("dedupe_keep.json が壊れています。保護を保証できないため自動整理をスキップ。")
            core.write_step_summary("dedupe", {"deleted": 0, "groups": 0, "changes": [], "skipped": "keep_error"})
            return core.EXIT_OK
        playlists = dedupe.managed_playlists()
        records, _intra = dedupe.collect_records(sp, playlists)
        groups = dedupe.build_groups(records)
        keep_sets = dedupe.load_keep_sets(data)
        removals, changes = dedupe.auto_select(groups, keep_sets)
    except core.AuthRequired as e:
        # 要件 §5.4: 認証/スキャン失敗時は何もせず正常終了（nightly を落とさない）。
        logger.info(f"[auth] スキップ: {e}")
        core.write_step_summary("dedupe", {"deleted": 0, "groups": 0, "changes": [], "skipped": "auth"})
        return core.EXIT_OK
    except Exception as e:  # noqa: BLE001 — 自動整理はベストエフォート。落ちても本体を守る
        logger.info(f"スキップ（スキャン失敗）: {e}")
        core.write_step_summary("dedupe", {"deleted": 0, "groups": 0, "changes": [], "skipped": "error"})
        return core.EXIT_OK

    if not removals:
        core.write_step_summary("dedupe", {"deleted": 0, "groups": 0, "changes": []})
        logger.info("自動整理の対象なし")
        return core.EXIT_OK

    if dry:
        # 注意: dry-run テストは「唯一の書き込み経路 _apply_removals が呼ばれない」で検証している。
        # 将来ここに別の変更系 API を足すなら、そのガードと dry-run テストも必ず拡張すること。
        core.write_step_summary(
            "dedupe", {"deleted": len(removals), "groups": len(changes), "changes": changes, "dry_run": True}
        )
        logger.info(f"[DRY-RUN] 自動整理予定: {len(removals)}曲 / {len(changes)}グループ（変更なし）")
        return core.EXIT_OK

    import siteops

    undo_id = siteops._apply_removals(sp, data, removals, "dedupe-auto", logger)
    for c in changes:
        c["undo_id"] = undo_id
    core.write_step_summary("dedupe", {"deleted": len(removals), "groups": len(changes), "changes": changes})
    logger.info(f"自動整理: {len(removals)}曲を削除 / {len(changes)}グループ undo={undo_id}")
    return core.EXIT_OK


def _entry() -> int:
    try:
        return main()
    except core.AuthRequired as e:
        core.setup_logging("dedupe-auto").info(f"[auth] {e}")
        return core.EXIT_OK  # 自動整理の認証失敗で nightly 全体を止めない
    except Exception as e:  # noqa: BLE001
        core.setup_logging("dedupe-auto").info(f"予期せぬエラーでスキップ: {e}")
        return core.EXIT_OK


if __name__ == "__main__":
    sys.exit(_entry())
