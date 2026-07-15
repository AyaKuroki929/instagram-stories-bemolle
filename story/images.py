"""画像生成

フォント取得と、サロン用ストーリー画像（1080×1920）の合成。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import FONT_PATHS
from .photos import detect_faces, get_drive_photo, load_fallback_photo


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _photo_background(photo_bytes: bytes, W: int, H: int):
    """写真を背景に合成して (img, text_main, text_sub, line_color) を返す。
    破損画像などで失敗したら例外を投げる（呼び出し側でグラデにフォールバック）。"""
    # EXIF Orientationを適用して写真本来の向きに直す（縦写真が横倒しで出るのを防ぐ）
    src = ImageOps.exif_transpose(Image.open(BytesIO(photo_bytes))).convert("RGB")
    bw, bh = src.size
    if bw > bh:
        # 横写真：中央クロップで端の人が切れるかを顔検出で判断。
        #  ・切れる（端に顔がある）/判定不能 → 全体を見せる「ぼかし余白」
        #  ・切れない（顔が中央域内 or 人がいない部屋・風景）→ 拡大して画面に充填
        keep_w = bh * (W / H)          # 中央クロップで残る元画像の横幅
        x0 = (bw - keep_w) / 2
        x1 = x0 + keep_w
        faces = detect_faces(src)
        if not faces:                  # None(判定不能) も [] も
            crop_safe = faces == []    # 顔ゼロ＝部屋/風景とみなし拡大OK、Noneは安全側で全体表示
        else:
            crop_safe = all(fx >= x0 and fx + fw <= x1 for (fx, fy, fw, fh) in faces)

        if crop_safe:
            # 拡大して中央クロップ充填（縦写真と同じ）
            scale = max(W / bw, H / bh)
            nw, nh = int(bw * scale), int(bh * scale)
            resized = src.resize((nw, nh), Image.LANCZOS)
            left, top = (nw - W) // 2, (nh - H) // 2
            bg = resized.crop((left, top, left + W, top + H))
        else:
            # ぼかし余白で全体表示（端の人を切らない）
            bs = max(W / bw, H / bh)
            bgw, bgh = int(bw * bs), int(bh * bs)
            bl, bt = (bgw - W) // 2, (bgh - H) // 2
            bg = src.resize((bgw, bgh), Image.LANCZOS).crop((bl, bt, bl + W, bt + H))
            bg = bg.filter(ImageFilter.GaussianBlur(45))
            fh = max(1, round(bh * (W / bw)))
            fg = src.resize((W, fh), Image.LANCZOS)
            bg.paste(fg, (0, (H - fh) // 2))
    else:
        # 縦・正方形：画面いっぱいに充填（中央クロップ）
        scale = max(W / bw, H / bh)
        nw, nh = int(bw * scale), int(bh * scale)
        resized = src.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - W) // 2, (nh - H) // 2
        bg = resized.crop((left, top, left + W, top + H))
    # 半透明オーバーレイ（明るい写真・暗い写真どちらでも文字が読める）
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 110))
    img = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    text_main  = (255, 255, 255)
    text_sub   = (235, 220, 220)
    line_color = (255, 255, 255)
    return img, text_main, text_sub, line_color


# ── 画像生成（1080×1920） ─────────────────────────────────────
def build_image(content: dict, today: datetime) -> bytes:
    W, H = 1080, 1920

    # 背景：Drive写真 → 取得不可なら予備写真キャッシュ → どちらも無い時だけグラデ
    photo_bytes = get_drive_photo(content["courses"]) or load_fallback_photo()
    bg_result = None
    if photo_bytes:
        try:
            bg_result = _photo_background(photo_bytes, W, H)
        except Exception as e:
            # 破損写真等で全体を落とさず、グラデ背景で投稿を継続する
            print(f"⚠️ 背景写真の加工に失敗 → グラデ背景で継続: {e}", file=sys.stderr)
    else:
        # ここに来るのは「Drive取得不可かつ予備写真も未生成」の初回限定の最終手段
        print("⚠️ Drive写真も予備写真も無し → グラデ背景（予備写真が出来れば次回以降は回避）", file=sys.stderr)

    if bg_result is not None:
        img, text_main, text_sub, line_color = bg_result
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
        """日本語の折り返し（禁則処理つき）。
        方針：
        - 句点「。」で文を区切り、各文を幅に収まるよう貪欲に折り返す
        - 改行の前に「、」を置かない（行末に読点を残さない）
        - 行頭に「、。」」等の約物を置かない
        - 助詞（を・に・が・は・へ・も・と・で）の直後で改行。ただし助詞の直前が
          漢字/カタカナ（＝内容語末）のときだけ本物の助詞とみなし、語中割れを防ぐ
          （例：皆様が→OK、ありがとう→NG。「できる」「です」等はで＋き/し/す等で除外）
        - 短すぎる先頭/末尾行は避け、末尾の極短行は前行にマージ
        """
        HEAD_NG = "、。」』）)！？!?…・%％"   # 行頭に置かない約物
        PART = "をにがはへもとで"              # 改行してよい助詞
        MIN_BREAK, MIN_TAIL = 5, 4

        def is_content(c: str) -> bool:        # 漢字/カタカナ/々（助詞の直前らしさ）
            o = ord(c)
            return 0x4E00 <= o <= 0x9FFF or 0x30A0 <= o <= 0x30FF or o == 0x3005

        def fits(s: str) -> bool:
            return font.getbbox(s)[2] <= max_w

        def good(sent: str, k: int, n: int) -> bool:
            prev = sent[k - 1]
            nxt = sent[k] if k < n else ""
            if prev == "、" or nxt in HEAD_NG:
                return False
            if prev == "で" and nxt in "きしすさせ":   # できる/です/でした等の語中を除外
                return False
            return prev in PART and k >= 2 and is_content(sent[k - 2])

        def any_break(sent: str, k: int, n: int) -> bool:
            prev = sent[k - 1]
            nxt = sent[k] if k < n else ""
            return prev != "、" and nxt not in HEAD_NG

        lines: list[str] = []
        for para in text.split("\n"):
            for sent in re.findall(r"[^。]*。|[^。]+", para):
                i, n = 0, len(sent)
                while i < n:
                    j = i + 1
                    while j <= n and fits(sent[i:j]):
                        j += 1
                    j -= 1
                    if j >= n:                       # 残り全部が収まる
                        lines.append(sent[i:])
                        break
                    j = max(j, i + 1)
                    # 1) 助詞の自然な区切りで、短すぎない最大位置
                    k = next((x for x in range(j, i, -1)
                              if good(sent, x, n) and x - i >= MIN_BREAK), None)
                    # 2) 無ければ、行末読点/行頭約物だけ避けた最大位置
                    if k is None:
                        k = next((x for x in range(j, i, -1) if any_break(sent, x, n)), None)
                    if not k or k <= i:
                        k = j
                    lines.append(sent[i:k])
                    i = k

        # 末尾の極短行を前行にマージ（句点で完結した行はまたがない）
        merged: list[str] = []
        for ln in lines:
            if merged and len(ln) < MIN_TAIL and not merged[-1].endswith("。"):
                merged[-1] = merged[-1] + ln
            else:
                merged.append(ln)
        return merged

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

    # ── コース一覧（右下）※日曜定休日は非表示 ──
    if content.get("courses"):
        course_font = get_font(40)
        lh_c = course_font.getbbox("あ")[3] + 20
        right_pad = 80
        max_cw = max(course_font.getbbox(f"・{c}")[2] for c in content["courses"])
        x_left  = W - right_pad - max_cw
        x_right = W - right_pad
        bottom_margin = 110
        block_h = lh_c * len(content["courses"])
        y_top = H - bottom_margin - block_h - 50
        draw.line([(x_left, y_top), (x_right, y_top)], fill=line_color, width=1)
        y_c = y_top + 20
        for course in content["courses"]:
            draw.text((x_left, y_c), f"・{course}", font=course_font, fill=text_main, anchor="lt")
            y_c += lh_c
        draw.line([(x_left, y_c + 8), (x_right, y_c + 8)], fill=line_color, width=1)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()
