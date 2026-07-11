"""Threads → ストーリー化

ベモーレThreadsの今日の投稿を取得し、Threads風の画像にして
Instagramストーリーに投稿する（STORY_MODE=threads で実行）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageOps

from .auth import manage_meta_token
from .config import BLOB_TOKEN, JST, LAST_POST_THREADS_FILE, THREADS_API, THREADS_TOKEN
from .images import get_font
from .notify import notify
from .publisher import get_ig_user_id, post_to_stories, upload_image
from .state import mark_posted_local, posted_today_local

THREADS_MORNING = (4, 11)   # JST 朝（7時投稿）。最優先
THREADS_NIGHT   = (19, 24)  # JST 夜（21時投稿）。朝が無い時のフォールバック
# 昼（11〜19時）のツリー投稿は絶対に選ばない


def fetch_thread_continuation(root_id: str) -> list[str]:
    """連投(ツリー)の続き＝自分の返信の本文を時系列で返す。
    me/threads は自分の返信を返さないため、conversation エンドポイントで取得する。"""
    try:
        r = requests.get(f"{THREADS_API}/{root_id}/conversation", params={
            "fields": "id,text,timestamp,is_reply_owned_by_me",
            "reverse": "false",
            "access_token": THREADS_TOKEN,
        }, timeout=20)
        if not r.ok:
            print(f"conversation取得失敗（続きなしで継続）: {r.status_code} {r.text[:150]}", file=sys.stderr)
            return []
        out = []
        for it in r.json().get("data", []):
            if it.get("is_reply_owned_by_me"):  # 自分の返信＝連投の続きだけ
                t = (it.get("text") or "").strip()
                if t:
                    out.append((it.get("timestamp", ""), t))
        out.sort(key=lambda x: x[0])
        return [t for _, t in out]
    except Exception as e:
        print(f"スレッド続き取得失敗（続きなしで継続）: {e}", file=sys.stderr)
        return []


def get_threads_latest_post() -> dict | None:
    """今日(JST)の『朝(4〜11時)の投稿』を最優先で返す。無ければ『夜(19〜24時)の投稿』。
    昼(11〜19時)のツリー投稿は絶対に選ばない。選んだ投稿がツリー(連投)なら続きを結合して全文にする。
    トークン未設定・API失敗は例外を投げる（「投稿なし」と混同すると失効に何週間も気づけないため）。"""
    if not THREADS_TOKEN:
        raise RuntimeError("THREADS_API_TOKEN_BEMOLLE が未設定です（Secret欠落かトークン失効の可能性）")
    r = requests.get(f"{THREADS_API}/me/threads", params={
        "fields": "id,text,timestamp,permalink,topic_tag,is_reply,root_post",
        "limit": 25,
        "access_token": THREADS_TOKEN,
    }, timeout=20)
    r.raise_for_status()
    items = r.json().get("data", [])
    today = datetime.now(JST).date()
    morning, night = [], []
    for item in items:
        if item.get("is_reply"):
            continue  # 連投の2部目以降は「先頭(root)」の候補にしない
        text = (item.get("text") or "").strip()
        if not text:
            continue
        try:
            dt = datetime.fromisoformat(
                item.get("timestamp", "").replace("Z", "+00:00")).astimezone(JST)
        except Exception:
            continue
        if dt.date() != today:
            continue
        if THREADS_MORNING[0] <= dt.hour < THREADS_MORNING[1]:
            morning.append((dt, item, text))
        elif THREADS_NIGHT[0] <= dt.hour < THREADS_NIGHT[1]:
            night.append((dt, item, text))
        # 昼(11〜19時)はどちらにも入れない＝絶対に選ばない

    bucket = morning or night  # 朝を最優先、無ければ夜
    if not bucket:
        print("今日の朝・夜の投稿が見つかりません（昼は選ばない）", file=sys.stderr)
        return None
    dt, root, _ = min(bucket, key=lambda x: x[0])  # その時間帯の先頭(root)投稿
    root_id = root.get("id")

    # 連投(ツリー)なら続き(自分の返信)を conversation から取得して結合。
    # me/threads は自分の返信を返さないため、別エンドポイントが必要。
    full_text = (root.get("text") or "").strip()
    cont = fetch_thread_continuation(root_id)
    if cont:
        full_text = "\n\n".join([full_text] + cont)
        print(f"朝の投稿はツリー → 本投稿＋続き{len(cont)}件を結合")

    secs = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
    hours = f"{int(secs // 3600)}時間" if secs >= 3600 else f"{int(secs // 60)}分"
    return {"text": full_text, "topic": root.get("topic_tag") or "",
            "hours": hours, "permalink": root.get("permalink", "")}


# ── スキップ通知の同日重複防止 ─────────────────────────────────
# 投稿が無い日は 8:00/8:30/9:00 の各cronが毎回ℹ️を送ってLINE枠を浪費するため、
# 「本日スキップ通知済み」を last_post_threads.json に記録して初回のみ通知する。
def _skip_notified_today() -> bool:
    try:
        if os.path.exists(LAST_POST_THREADS_FILE):
            with open(LAST_POST_THREADS_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("skip_date") == datetime.now(JST).date().isoformat()
    except Exception:
        pass
    return False


def _mark_skip_notified() -> None:
    try:
        d = {}
        if os.path.exists(LAST_POST_THREADS_FILE):
            with open(LAST_POST_THREADS_FILE, encoding="utf-8") as f:
                d = json.load(f)
        d["skip_date"] = datetime.now(JST).date().isoformat()
        tmp = LAST_POST_THREADS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LAST_POST_THREADS_FILE)
    except Exception as e:
        print(f"skipマーカー保存失敗: {e}", file=sys.stderr)


def get_threads_avatar() -> bytes | None:
    """ベモーレThreadsのプロフィール画像を取得（失敗時はNone→プレースホルダ）。"""
    if not THREADS_TOKEN:
        return None
    try:
        r = requests.get(f"{THREADS_API}/me", params={
            "fields": "threads_profile_picture_url",
            "access_token": THREADS_TOKEN,
        }, timeout=15)
        r.raise_for_status()
        url = r.json().get("threads_profile_picture_url")
        if not url:
            return None
        ir = requests.get(url, timeout=15)
        ir.raise_for_status()
        return ir.content
    except Exception as e:
        print(f"Threadsアイコン取得失敗（プレースホルダ使用）: {e}", file=sys.stderr)
        return None


def build_threads_image(post: dict, avatar_bytes: bytes | None) -> bytes:
    """Threads投稿をThreads風の1080×1920ストーリー画像にする（本文量でフォント自動調整）。"""
    W, H = 1080, 1920
    PAD = 80
    ink, gray, link = (20, 20, 22), (120, 120, 128), (40, 90, 200)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # ヘッダー（アイコン＋名前＋トピック＋経過時間）
    ay, av_d = 168, 96
    if avatar_bytes:
        try:
            av = ImageOps.exif_transpose(Image.open(BytesIO(avatar_bytes))).convert("RGB")
            av = ImageOps.fit(av, (av_d, av_d), Image.LANCZOS)
            mask = Image.new("L", (av_d, av_d), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, av_d, av_d], fill=255)
            img.paste(av, (PAD, ay), mask)
        except Exception:
            d.ellipse([PAD, ay, PAD + av_d, ay + av_d], fill=(210, 196, 186))
    else:
        d.ellipse([PAD, ay, PAD + av_d, ay + av_d], fill=(210, 196, 186))
    tx = PAD + av_d + 28
    d.text((tx, ay + 6), "bemolle_diet", font=get_font(46), fill=ink)
    sub = post.get("topic") or ""
    if post.get("hours"):
        sub = f"{sub} ・ {post['hours']}" if sub else post["hours"]
    d.text((tx, ay + 62), sub, font=get_font(32), fill=gray)

    maxw, top, foot_y = W - PAD * 2, 330, H - 150
    avail = foot_y - top - 30

    def wrap(text, font):
        FORBID = "。、」』）)！？!?…"
        out = []
        for raw in text.split("\n"):
            line = ""
            for ch in raw:
                if font.getbbox(line + ch)[2] <= maxw:
                    line += ch
                elif ch in FORBID and line:
                    out.append(line + ch); line = ""
                else:
                    out.append(line); line = ch
            out.append(line)
        return out

    def layout(font, lh, pgap):
        items, h = [], 0
        for seg in post["text"].split("\n"):
            if seg.strip() == "":
                items.append(("gap", "", False)); h += pgap
            else:
                is_link = any(k in seg for k in ("instagram.com", "http", "threads.net"))
                for ln in wrap(seg, font):
                    items.append(("line", ln, is_link)); h += lh
        return items, h

    chosen = None
    for size in (40, 37, 34, 31, 28):
        font = get_font(size); lh = int(size * 1.45); pgap = int(size * 0.55)
        items, h = layout(font, lh, pgap)
        if h <= avail:
            chosen = (font, lh, items); break
    if chosen is None:  # 最小でも収まらない→入る分だけ描いて末尾省略
        font = get_font(28); lh = int(28 * 1.45)
        items, _ = layout(font, lh, int(28 * 0.55))
        keep = max(1, avail // lh - 1)
        items = items[:keep] + [("line", "…続きはThreadsで", False)]
        chosen = (font, lh, items)

    font, lh, items = chosen
    y = top
    for kind, text, is_link in items:
        if kind == "gap":
            y += int(lh * 0.4)
        else:
            d.text((PAD, y), text, font=font, fill=(link if is_link else ink)); y += lh

    d.line([(PAD, foot_y), (W - PAD, foot_y)], fill=(230, 230, 232), width=2)
    d.text((W // 2, H - 100), "Threadsの投稿より　@bemolle_diet",
           font=get_font(30), fill=gray, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def run_threads_story() -> None:
    today = datetime.now(JST)
    print(f"[{today.strftime('%Y-%m-%d %H:%M')} JST] Threads→ストーリー投稿開始")
    ig_id = get_ig_user_id()
    manage_meta_token()

    # サロン投稿(7:00)と同日に複数ストーリーが正常なので、Meta /stories判定は使わず
    # Threads専用のローカルマーカーのみで二重投稿防止。手動dispatchは常に投稿。
    is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if not is_manual and posted_today_local(LAST_POST_THREADS_FILE):
        print("本日のThreadsストーリーは投稿済みのためスキップ。")
        return

    try:
        post = get_threads_latest_post()
    except Exception as e:
        # トークン失効・API障害を「投稿なし」と混同しない（沈黙のまま止まるのを防ぐ）
        print(f"Threads投稿取得失敗: {e}", file=sys.stderr)
        notify(f"⚠️ @bemolle_diet Threadsストーリー：Threads投稿の取得に失敗しました（トークン失効の可能性）\n{str(e)[:200]}")
        sys.exit(1)
    if not post:
        if _skip_notified_today():
            print("朝の投稿なし→スキップ（本日の通知は送信済み）")
            return
        notify("ℹ️ @bemolle_diet Threadsストーリー：今朝の投稿が見つからずスキップしました")
        _mark_skip_notified()
        print("朝の投稿なし→スキップ")
        return
    print(f"対象Threads投稿（{post.get('hours')}）: {post['text'][:40]}…")

    avatar = get_threads_avatar()
    try:
        image_bytes = build_threads_image(post, avatar)
    except Exception as e:
        notify(f"⚠️ @bemolle_diet Threadsストーリー失敗\n画像エラー: {e}")
        sys.exit(1)

    # ドライラン：投稿せず画像をアップしてプレビューURLを表示（初回確認用）。
    # Blobトークンがあれば Blob 経由で上げて、Blobが機能しているかも同時に検証する。
    if os.environ.get("STORY_DRYRUN") == "1":
        host = "blob" if BLOB_TOKEN else "imgbb"
        url = upload_image(image_bytes, host)
        print(f"DRYRUN プレビュー（未投稿・{host}）: {url}")
        return

    try:
        media_id = post_to_stories(ig_id, image_bytes)
        print(f"投稿完了: media_id={media_id}")
        mark_posted_local(LAST_POST_THREADS_FILE)
    except Exception as e:
        print(f"Meta APIエラー: {e}", file=sys.stderr)
        notify(f"⚠️ @bemolle_diet Threadsストーリー失敗\nMeta APIエラー: {e}")
        sys.exit(1)
    print("完了")
