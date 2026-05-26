#!/usr/bin/env python3
"""@bemolle_diet Instagram Stories 自動投稿"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

# ── 設定 ──────────────────────────────────────────────────────────
META_TOKEN  = os.environ["META_ACCESS_TOKEN"]
GEMINI_KEY  = os.environ["GEMINI_API_KEY"]
IMGBB_KEY   = os.environ["IMGBB_API_KEY"]
LINE_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
IG_USERNAME = "bemolle_diet"
META_API    = "https://graph.facebook.com/v21.0"
JST         = timezone(timedelta(hours=9))

FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── 1. Instagram Business Account ID を取得 ────────────────────
def get_ig_user_id() -> str:
    r = requests.get(f"{META_API}/me/accounts", params={
        "fields": "id,name,instagram_business_account{id,username}",
        "access_token": META_TOKEN,
    }, timeout=30)
    r.raise_for_status()
    for page in r.json().get("data", []):
        ig = page.get("instagram_business_account", {})
        if ig.get("username") == IG_USERNAME:
            return ig["id"]
    raise RuntimeError(f"@{IG_USERNAME} がFacebookページに連携されていません")


# ── 2. Gemini Flash でストーリー内容を生成 ─────────────────────
def generate_content(today: datetime) -> dict:
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    prompt = f"""あなたはInstagramストーリーライターです。
大阪のボディケアサロン「ベモーレ」（@bemolle_diet）向けに今日のストーリー文を作成します。

今日：{today.month}月{today.day}日（{weekday}曜日）
ターゲット：40〜50代女性
特徴：痩身・ボディケア専門、完全予約制、大阪、平日9:30〜18:00

以下のJSONのみ出力（他は一切不要）：
{{
  "theme": "今日のテーマ（例：月曜日のリセット）",
  "main_text": "メイン文（30〜50文字、改行は\\nで）",
  "sub_text": "サブ文（20〜30文字）",
  "cta": "行動喚起（20文字以内、例：プロフィールから予約を）"
}}

ルール：
- 「うち」禁止→「ベモーレ」「当サロン」を使う
- 敬語ベース・自然な話し言葉
- 「あなた専用」「カスタム」禁止
- 夜・週末営業の表現禁止
- 曜日・季節にあったテーマで"""

    r = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
        params={"key": GEMINI_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8},
        },
        timeout=30,
    )
    r.raise_for_status()
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    start, end = raw.find("{"), raw.rfind("}") + 1
    return json.loads(raw[start:end])


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

    # ロゴ
    logo_font = get_font(76)
    draw.text((W // 2, 190), "bemolle", font=logo_font, fill=accent, anchor="mm")

    # 装飾ライン
    lw = 260
    draw.line([(W//2 - lw, 252), (W//2 + lw, 252)], fill=accent, width=2)

    # 日付
    date_font = get_font(38)
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]
    draw.text(
        (W // 2, 310),
        f"{today.month}月{today.day}日（{weekday}）",
        font=date_font, fill=text_mid, anchor="mm",
    )

    # テーマ
    theme_font = get_font(46)
    draw.text((W // 2, 430), content["theme"], font=theme_font, fill=text_mid, anchor="mm")

    # 装飾ライン2
    draw.line([(W//2 - lw, 490), (W//2 + lw, 490)], fill=accent, width=1)

    # メインテキスト（改行対応）
    main_font = get_font(62)
    lines = content["main_text"].split("\\n")
    y_base = 750 - (len(lines) * 90) // 2
    for i, line in enumerate(lines):
        draw.text((W // 2, y_base + i * 92), line, font=main_font, fill=text_dark, anchor="mm")

    # サブテキスト
    sub_font = get_font(46)
    draw.text((W // 2, 1000), content["sub_text"], font=sub_font, fill=text_mid, anchor="mm")

    # 区切りドット
    for dx in [-60, 0, 60]:
        draw.ellipse(
            [(W//2 + dx - 6, 1100 - 6), (W//2 + dx + 6, 1100 + 6)],
            fill=accent,
        )

    # CTA
    cta_font = get_font(40)
    draw.text((W // 2, 1720), "▼  " + content["cta"], font=cta_font, fill=accent, anchor="mm")

    # 下部ライン
    draw.line([(W//2 - lw, 1775), (W//2 + lw, 1775)], fill=accent, width=2)

    # ブランドタグ
    tag_font = get_font(34)
    draw.text((W // 2, 1840), "@bemolle_diet", font=tag_font, fill=text_mid, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ── 4. imgbb にアップロード（公開URL取得） ────────────────────
def upload_to_imgbb(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    r = requests.post("https://api.imgbb.com/1/upload", data={
        "key": IMGBB_KEY,
        "image": b64,
        "expiration": 7200,  # 2時間後削除（Meta APIが取得した後は不要）
    }, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["url"]


# ── 5. Instagram Stories に投稿 ───────────────────────────────
def post_to_stories(ig_user_id: str, image_url: str) -> str:
    # メディアコンテナ作成
    r = requests.post(f"{META_API}/{ig_user_id}/media", data={
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": META_TOKEN,
    }, timeout=30)
    r.raise_for_status()
    creation_id = r.json()["id"]

    # 公開
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
        print(f"テーマ: {content['theme']}")
        print(f"メイン: {content['main_text']}")
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
        f"テーマ：{content['theme']}\n"
        f"{today.strftime('%m/%d %H:%M')} JST"
    )
    print("完了")


if __name__ == "__main__":
    main()
