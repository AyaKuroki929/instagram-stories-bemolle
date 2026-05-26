#!/usr/bin/env python3
"""@bemolle_diet Instagram Stories 自動投稿（1枚目）"""
from __future__ import annotations

import base64
import json
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

# ── 設定 ──────────────────────────────────────────────────────────
META_TOKEN  = os.environ["META_ACCESS_TOKEN"]
GEMINI_KEY  = os.environ["GEMINI_API_KEY"]
IMGBB_KEY   = os.environ["IMGBB_API_KEY"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
IG_USER_ID  = os.environ.get("IG_USER_ID", "17841470478859455")  # @bemolle_diet
META_API    = "https://graph.facebook.com/v25.0"
JST         = timezone(timedelta(hours=9))

FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
]

COURSES_SLIM  = ["全身痩身12回コース", "全身痩身18回コース", "全身痩身24回コース"]
COURSES_FACIAL = ["３ヶ月肌質改善プログラム", "６ヶ月肌質改善プログラム"]
COURSES_TRIAL  = ["全身痩身体験", "肌質改善体験"]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── 1. Instagram Business Account ID を返す ────────────────────
def get_ig_user_id() -> str:
    return IG_USER_ID


# ── 2. Gemini Flash でストーリー1枚目を生成 ───────────────────
def generate_content(today: datetime) -> dict:
    month  = today.month
    day    = today.day
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    # 季節
    if month in (12, 1, 2):   season = "冬"
    elif month in (3, 4, 5):  season = "春"
    elif month in (6, 7, 8):  season = "夏"
    else:                      season = "秋"

    # コース候補をランダムに2〜3個（痩身と肌質改善が偏らないように）
    slim_pick   = random.sample(COURSES_SLIM, k=random.randint(1, 2))
    facial_pick = random.sample(COURSES_FACIAL, k=1)
    # 8割で満席→新規含む日は体験コースも混ぜる
    include_new = random.random() < 0.4
    if include_new:
        trial_pick = [random.choice(COURSES_TRIAL)]
        course_pool = trial_pick + slim_pick[:1] + facial_pick[:1]
    else:
        course_pool = slim_pick + facial_pick

    courses_str = "\n".join(f"・{c}" for c in course_pool)

    prompt = f"""あなたはエステサロン「ベモーレ」（大阪・谷町九丁目）の公式Instagramを運営するライターです。
以下のルールに従い、今日のInstagramストーリー1枚目の文章をJSONで出力してください。

今日：{month}月{day}日（{weekday}曜日）・{season}

【1枚目の構成ルール】
① 朝の挨拶（短く自然に）
② 本日の状況（以下3パターンからランダムで、満席表現が8割に来るように）
  - 「本日もリピーター様、ご新規様で満席となっております。」
  - 「本日もリピーター様で満席となっております。」
  - 「本日もリピーター様、ご新規様にお越しいただきます。」
③ ご来店を心待ちにしていることが伝わる一言（季節・天気・気遣いなど、毎回変える）

今日のコース（以下をそのまま使う）：
{courses_str}

【文章ルール（最重要）】
・「ベモーレ」はカタカナ表記のみ（Bemolleは使わない）
・AIっぽい整いすぎた文章は禁止
・実際に黒木（オーナー）がそのまま投稿しても違和感ない温度感
・敬語ベースで柔らかく、現場で話している感じ
・一文に緩急をつける（短文と中文を混ぜる）
・誇張表現禁止・無駄な修飾語を削る
・感情は控えめに乗せる（安心・共感・寄り添い）
・整いすぎていたらあえて崩す

以下のJSONのみ出力（他は不要）：
{{
  "greeting": "朝の挨拶（1〜2文）",
  "status": "本日の状況（上記3パターンのいずれか）",
  "closing": "心待ちにしている一言（季節・気遣い含む、1文）"
}}"""

    for attempt in range(3):
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            params={"key": GEMINI_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.9},
            },
            timeout=30,
        )
        if r.status_code == 429 and attempt < 2:
            time.sleep(30)
            continue
        r.raise_for_status()
        break
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    start, end = raw.find("{"), raw.rfind("}") + 1
    result = json.loads(raw[start:end])
    result["courses"] = course_pool
    return result


# ── 3. Pillow で画像生成（1080×1920） ─────────────────────────
def build_image(content: dict, today: datetime) -> bytes:
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # グラデーション背景（温かいベージュ→ローズ）
    for y in range(H):
        t = y / H
        r = int(251 * (1 - t) + 237 * t)
        g = int(245 * (1 - t) + 218 * t)
        b = int(240 * (1 - t) + 218 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    accent    = (172, 108, 108)
    text_dark = (58, 38, 38)
    text_mid  = (110, 75, 75)
    divider_color = (200, 160, 160)

    pad = 80  # 左右余白

    def draw_divider(y: int) -> None:
        draw.line([(pad, y), (W - pad, y)], fill=divider_color, width=1)

    def draw_text_wrapped(text: str, font: ImageFont.FreeTypeFont,
                           color: tuple, y: int, max_width: int) -> int:
        """テキストを折り返してy座標を返す（終端y）"""
        lines, line = [], ""
        for ch in text:
            test = line + ch
            bbox = font.getbbox(test)
            if bbox[2] > max_width and line:
                lines.append(line)
                line = ch
            else:
                line = test
        if line:
            lines.append(line)

        line_h = font.getbbox("あ")[3] + 12
        for ln in lines:
            draw.text((W // 2, y), ln, font=font, fill=color, anchor="mt")
            y += line_h
        return y

    # ── ロゴ ──
    logo_font = get_font(72)
    draw.text((W // 2, 160), "bemolle", font=logo_font, fill=accent, anchor="mm")
    draw_divider(215)

    # ── 日付 ──
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]
    date_font = get_font(36)
    draw.text(
        (W // 2, 255),
        f"{today.month}月{today.day}日（{weekday}）",
        font=date_font, fill=text_mid, anchor="mm",
    )

    # ── 挨拶 ──
    greet_font = get_font(46)
    y = 360
    y = draw_text_wrapped(content["greeting"], greet_font, text_dark, y, W - pad * 2)

    # ── 状況 ──
    y += 24
    y = draw_text_wrapped(content["status"], greet_font, text_dark, y, W - pad * 2)

    # ── 締め一言 ──
    y += 24
    close_font = get_font(42)
    y = draw_text_wrapped(content["closing"], close_font, text_mid, y, W - pad * 2)

    # ── コース欄 ──
    y += 60
    draw_divider(y)
    y += 40

    course_font = get_font(42)
    for course in content["courses"]:
        draw.text((W // 2, y), course, font=course_font, fill=text_dark, anchor="mt")
        y += course_font.getbbox("あ")[3] + 20

    y += 30
    draw_divider(y)

    # ── ブランドタグ（下部） ──
    tag_font = get_font(34)
    draw.text((W // 2, H - 80), "@bemolle_diet", font=tag_font, fill=text_mid, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ── 4. imgbb にアップロード ────────────────────────────────────
def upload_to_imgbb(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    r = requests.post("https://api.imgbb.com/1/upload", data={
        "key": IMGBB_KEY,
        "image": b64,
        "expiration": 7200,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["url"]


# ── 5. Instagram Stories に投稿 ───────────────────────────────
def post_to_stories(ig_user_id: str, image_url: str) -> str:
    r = requests.post(f"{META_API}/{ig_user_id}/media", data={
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": META_TOKEN,
    }, timeout=30)
    r.raise_for_status()
    creation_id = r.json()["id"]

    r = requests.post(f"{META_API}/{ig_user_id}/media_publish", data={
        "creation_id": creation_id,
        "access_token": META_TOKEN,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


# ── 6. LINE通知 ───────────────────────────────────────────────
def notify(msg: str) -> None:
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            json={"messages": [{"type": "text", "text": msg}]},
            timeout=10,
        )
    except Exception:
        pass


# ── メイン ────────────────────────────────────────────────────
def main() -> None:
    today = datetime.now(JST)
    print(f"[{today.strftime('%Y-%m-%d %H:%M')} JST] ストーリー投稿開始")

    try:
        ig_id = get_ig_user_id()
        print(f"IG User ID: {ig_id}")
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nIG ID取得エラー: {e}")
        sys.exit(1)

    try:
        content = generate_content(today)
        print(f"挨拶: {content['greeting']}")
        print(f"コース: {content['courses']}")
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nコンテンツ生成エラー: {e}")
        sys.exit(1)

    try:
        image_bytes = build_image(content, today)
        image_url   = upload_to_imgbb(image_bytes)
        print(f"画像URL: {image_url}")
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\n画像エラー: {e}")
        sys.exit(1)

    try:
        media_id = post_to_stories(ig_id, image_url)
        print(f"投稿完了: media_id={media_id}")
    except Exception as e:
        notify(f"⚠️ @bemolle_diet ストーリー失敗\nMeta APIエラー: {e}")
        sys.exit(1)

    notify(
        f"✅ @bemolle_diet ストーリー投稿完了\n"
        f"{today.strftime('%m/%d')} {content['greeting'][:15]}…\n"
        f"コース：{' / '.join(content['courses'])}"
    )
    print("完了")


if __name__ == "__main__":
    main()
