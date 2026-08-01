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
from .content import generate_monthly_content, generate_content, generate_sunday_content
from .images import build_image
from .notify import notify
from .publisher import blob_mark_posted, blob_posted_today, get_ig_user_id, post_to_stories
from .state import mark_posted_local, posted_today_local, save_monthly_text
from .threads import run_threads_story


def post_monthly_if_first_day(today: datetime, ig_id: str) -> None:
    """毎月1日、通常ストーリーの「後」に「先月の感謝＋今月の意気込み」を投稿（2026-08-01彩さん指示）。
    おはようございます（通常）→ 感謝、の順で並ばせる。月マーカー(Blob・月単位)で月1回だけ。
    日曜でも実施。通常が投稿済みスキップの日でも、月初が未投稿ならここで後追い投稿する。"""
    if today.day != 1 or blob_posted_today("monthly"):
        return
    try:
        m_content = generate_monthly_content(today)
        m_image = build_image(m_content, today)
        m_id = post_to_stories(ig_id, m_image)
        print(f"月初の感謝ストーリー投稿完了: media_id={m_id}")
        try:
            blob_mark_posted("monthly")
            print("Blobマーカー更新（monthly）")
        except Exception as e:
            print(f"monthlyマーカー書込み失敗: {e}", file=sys.stderr)
            notify(f"⚠️ @bemolle_diet 月初ストーリー: 投稿成功もマーカー書込み失敗（重複の恐れ）: {e}")
        try:
            save_monthly_text({
                "month": today.strftime("%Y-%m"),
                "pattern": m_content.get("pattern"),
                "status": m_content["status"],
                "closing": m_content["closing"],
            })  # 言い回しの永久被り防止の履歴（Save stepでコミット）
        except Exception as e:
            print(f"月初文履歴の保存失敗: {e}", file=sys.stderr)
    except Exception as e:
        print(f"月初ストーリー投稿失敗: {e}", file=sys.stderr)
        notify(f"⚠️ @bemolle_diet 月初の感謝ストーリー投稿に失敗しました\n{e}")


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
    # 判定は「サロン専用マーカー」＝主砦=Blob(git push非依存)＋副=last_post.json(git)。
    # ※Meta /stories(already_posted_today)は使わない：アカウント上の全ストーリーを数え、
    #   朝のThreads→ストーリー(8時)を「サロン投稿済み」と誤判定してバックアップまでスキップさせる
    #   事故があった(2026-07-25)。Blobマーカーはサロン投稿だけが書き、push失敗でも残るため
    #   「Threadsによるマスキング」も「マーカー喪失による二重投稿」も同時に防げる。
    is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if not is_manual and (blob_posted_today() or posted_today_local()):
        print("本日のサロンストーリーは投稿済みのためスキップ。")
        post_monthly_if_first_day(today, ig_id)  # 通常が済んでいても月初が未投稿なら後追い
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
    except Exception as e:
        print(f"Meta APIエラー: {e}", file=sys.stderr)
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nMeta APIエラー: {e}")
        sys.exit(1)

    # 投稿成功 → 同日マーカーを記録。主砦=Blob（git push非依存で必ず残す）。
    try:
        blob_mark_posted()
        print("Blobマーカー更新（二重投稿防止・恒久）")
    except Exception as e:
        # Blobが書けないと次回の二重投稿防止が弱まる → 明示的にLINE警告（沈黙の失敗を作らない）
        print(f"Blobマーカー書込み失敗: {e}", file=sys.stderr)
        notify(f"⚠️ @bemolle_diet ストーリー: 投稿は成功したがBlobマーカー書込みに失敗。\n"
               f"本日のバックアップ実行が二重投稿する恐れ。Actionsを確認してください: {e}")
    mark_posted_local()  # 副：git用マーカー（Save stepでcommit/push）

    # 毎月1日は、通常ストーリーの後に月初の感謝ストーリーを続けて投稿
    post_monthly_if_first_day(today, ig_id)

    print("完了")
