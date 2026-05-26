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
META_TOKEN    = os.environ["META_ACCESS_TOKEN"]
GEMINI_KEY    = os.environ["GEMINI_API_KEY"]
IMGBB_KEY      = os.environ["IMGBB_API_KEY"]
LINE_TOKEN     = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GDRIVE_REFRESH = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_CLIENT  = os.environ.get("GOOGLE_CLIENT_ID", "")
GDRIVE_SECRET  = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GDRIVE_FOLDER        = "18K4hZUjbBH3V1XJjiSNNfss6GZnaTNqV"  # ベモーレ ストーリー素材
GDRIVE_FOLDER_SLIM   = "170R8MxD_ByugDmxctVQbpmY2p3nXVDK8"  # 痩身
GDRIVE_FOLDER_FACIAL = "1DwNv1e5_j4YnDt23DNgYp9RatJQYpGtj"  # 肌質改善
IG_USER_ID     = os.environ.get("IG_USER_ID", "17841470478859455")
META_API       = "https://graph.facebook.com/v25.0"
JST            = timezone(timedelta(hours=9))

FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
]

COURSES_SLIM   = ["全身痩身12回コース", "全身痩身18回コース", "全身痩身24回コース"]
COURSES_FACIAL = ["３ヶ月肌質改善プログラム", "６ヶ月肌質改善プログラム"]
COURSES_TRIAL  = ["全身痩身体験", "肌質改善体験"]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── 1. IG User ID ────────────────────────────────────────────────
def get_ig_user_id() -> str:
    return IG_USER_ID


# ── 2. Google Drive から背景写真を取得（コース内容に連動） ─────
def get_drive_photo(course_pool: list[str]) -> bytes | None:
    if not GDRIVE_REFRESH:
        return None
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token",
            "refresh_token": GDRIVE_REFRESH,
            "client_id": GDRIVE_CLIENT,
            "client_secret": GDRIVE_SECRET,
        }, timeout=15)
        r.raise_for_status()
        token = r.json()["access_token"]

        # 今日のコースに対応するフォルダを決める
        has_slim   = any("痩身" in c for c in course_pool)
        has_facial = any("肌質" in c for c in course_pool)
        if has_slim and has_facial:
            folder_ids = [GDRIVE_FOLDER_SLIM, GDRIVE_FOLDER_FACIAL, GDRIVE_FOLDER]
        elif has_slim:
            folder_ids = [GDRIVE_FOLDER_SLIM, GDRIVE_FOLDER]
        elif has_facial:
            folder_ids = [GDRIVE_FOLDER_FACIAL, GDRIVE_FOLDER]
        else:
            folder_ids = [GDRIVE_FOLDER]

        # 優先フォルダから順に写真を探す
        for folder_id in folder_ids:
            r2 = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                params={
                    "q": f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
                    "fields": "files(id,name)",
                    "access_token": token,
                },
                timeout=15,
            )
            r2.raise_for_status()
            files = r2.json().get("files", [])
            if files:
                chosen = random.choice(files)
                r3 = requests.get(
                    f"https://www.googleapis.com/drive/v3/files/{chosen['id']}",
                    params={"alt": "media", "access_token": token},
                    timeout=30,
                )
                r3.raise_for_status()
                print(f"Drive写真: {chosen['name']}")
                return r3.content

        return None
    except Exception as e:
        print(f"Drive取得失敗（グラデーション背景で代替）: {e}", file=sys.stderr)
        return None


# ── 3. コンテンツ生成（Claude Haiku） ───────────────────────────
def generate_content(today: datetime) -> dict:
    month   = today.month
    day     = today.day
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    if month in (12, 1, 2):   season = "冬"
    elif month in (3, 4, 5):  season = "春"
    elif month in (6, 7, 8):  season = "夏"
    else:                      season = "秋"

    slim_pick   = random.sample(COURSES_SLIM, k=random.randint(1, 2))
    facial_pick = random.sample(COURSES_FACIAL, k=1)
    include_new = random.random() < 0.4
    if include_new:
        trial_pick  = [random.choice(COURSES_TRIAL)]
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


# ── 4. 画像生成（1080×1920） ───────────────────────────────────
def build_image(content: dict, today: datetime) -> bytes:
    W, H = 1080, 1920

    # 背景：Drive写真 or グラデーション（フォールバック）
    photo_bytes = get_drive_photo(content["courses"])
    if photo_bytes:
        bg = Image.open(BytesIO(photo_bytes)).convert("RGB")
        bw, bh = bg.size
        scale = max(W / bw, H / bh)
        nw, nh = int(bw * scale), int(bh * scale)
        bg = bg.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - W) // 2, (nh - H) // 2
        bg = bg.crop((left, top, left + W, top + H))
        # 半透明オーバーレイ（明るい写真・暗い写真どちらでも文字が読める）
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 110))
        img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
        text_main  = (255, 255, 255)
        text_sub   = (235, 220, 220)
        line_color = (255, 255, 255)
    else:
        img = Image.new("RGB", (W, H))
        tmp = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            tmp.line([(0, y), (W, y)], fill=(
                int(251 * (1 - t) + 237 * t),
                int(245 * (1 - t) + 218 * t),
                int(240 * (1 - t) + 218 * t),
            ))
        text_main  = (58, 38, 38)
        text_sub   = (110, 75, 75)
        line_color = (200, 160, 160)

    draw = ImageDraw.Draw(img)

    def wrapped_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
        """。の後は必ず改行。それ以外は max_w で折り返し。"""
        lines, line = [], ""
        for ch in text:
            test = line + ch
            if font.getbbox(test)[2] > max_w and line:
                lines.append(line)
                line = ch
            else:
                line = test
            if ch == "。" and line:
                lines.append(line)
                line = ""
        if line:
            lines.append(line)
        return lines

    def draw_block(text: str, font: ImageFont.FreeTypeFont, color: tuple,
                   y: int, max_w: int) -> int:
        lh = font.getbbox("あ")[3] + 16
        for ln in wrapped_lines(text, font, max_w):
            draw.text((W // 2, y), ln, font=font, fill=color, anchor="mt")
            y += lh
        return y

    # ── メインテキスト（上部・中央） ──
    greet_font = get_font(52)
    close_font = get_font(44)
    pad = 80

    y = 300
    y = draw_block(content["greeting"], greet_font, text_main, y, W - pad * 2)
    y += 40
    y = draw_block(content["status"],   greet_font, text_main, y, W - pad * 2)
    y += 40
    y = draw_block(content["closing"],  close_font, text_sub,  y, W - pad * 2)

    # ── コース一覧（右下） ──
    course_font = get_font(40)
    lh_c = course_font.getbbox("あ")[3] + 20
    n = len(content["courses"])

    right_pad = 80
    max_cw = max(course_font.getbbox(f"・{c}")[2] for c in content["courses"])
    x_left  = W - right_pad - max_cw
    x_right = W - right_pad

    bottom_margin = 110
    block_h = lh_c * n
    y_top = H - bottom_margin - block_h - 50  # 上divider位置

    draw.line([(x_left, y_top), (x_right, y_top)], fill=line_color, width=1)
    y_c = y_top + 20
    for course in content["courses"]:
        draw.text((x_left, y_c), f"・{course}", font=course_font, fill=text_main, anchor="lt")
        y_c += lh_c
    draw.line([(x_left, y_c + 8), (x_right, y_c + 8)], fill=line_color, width=1)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ── 5. imgbb にアップロード ────────────────────────────────────
def upload_to_imgbb(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    r = requests.post("https://api.imgbb.com/1/upload", data={
        "key": IMGBB_KEY,
        "image": b64,
        "expiration": 7200,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["url"]


# ── 6. Instagram Stories に投稿 ───────────────────────────────
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


# ── 7. LINE通知 ───────────────────────────────────────────────
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
        print(f"Meta APIエラー: {e}", file=sys.stderr)
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
