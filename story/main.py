"""全体の流れ（エントリーポイント）

STORY_MODE=threads … Threads→ストーリー化（別ワークフロー・8時）
それ以外           … サロンの朝ストーリー投稿（7時）
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

from .auth import manage_meta_token
from .config import JST
from .content import generate_content, generate_sunday_content
from .images import build_image
from .notify import notify
from .publisher import already_posted_today, get_ig_user_id, post_to_stories
from .state import mark_posted_local, posted_today_local
from .threads import run_threads_story


def main() -> None:
    # STORY_MODE=threads ならThreads→ストーリー化を実行（別ワークフロー・8時）
    if os.environ.get("STORY_MODE") == "threads":
        run_threads_story()
        return

    today = datetime.now(JST)
    print(f"[{today.strftime('%Y-%m-%d %H:%M')} JST] ストーリー投稿開始")

    try:
        ig_id = get_ig_user_id()
        print(f"IG User ID: {ig_id}")
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nIG ID取得エラー: {e}")
        sys.exit(1)

    # トークン期限管理（自動延長 → 失敗時はLINE警告）
    manage_meta_token()

    # 同日二重投稿防止：自動実行(schedule/repository_dispatch)のみ判定。
    # 手動 workflow_dispatch は意図的な再投稿なので常に通す。
    # ローカルマーカー（Meta非依存）とMeta /stories の両方で判定し、どちらかが今日ならスキップ。
    is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if not is_manual and (posted_today_local() or already_posted_today(ig_id)):
        print("本日のストーリーは投稿済みのためスキップ。")
        return

    try:
        is_sunday = today.weekday() == 6
        content = generate_sunday_content(today) if is_sunday else generate_content(today)
        print(f"挨拶: {content['greeting']}")
        if not is_sunday:
            print(f"コース: {content['courses']}")
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nコンテンツ生成エラー: {e}")
        sys.exit(1)

    try:
        image_bytes = build_image(content, today)
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\n画像エラー: {e}")
        sys.exit(1)

    try:
        # アップロードはpost_to_stories内で試行ごとに行う（失敗時に新URLで再投稿するため）
        media_id = post_to_stories(ig_id, image_bytes)
        print(f"投稿完了: media_id={media_id}")
        mark_posted_local()  # 最終投稿日マーカー更新（二重投稿防止の永続化）
    except Exception as e:
        print(f"Meta APIエラー: {e}", file=sys.stderr)
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nMeta APIエラー: {e}")
        sys.exit(1)

    print("完了")
