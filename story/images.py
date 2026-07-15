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

try:
    from budoux import load_default_japanese_parser
    _BUDOUX = load_default_japanese_parser()  # 日本語文節境界（Google製・純Python）
except Exception:
    _BUDOUX = None  # 未導入でもヒューリスティックで動く

from .config import FONT_PATHS
from .photos import detect_faces, get_drive_photo, load_fallback_photo


def get_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ── 日本語折り返し（禁則＋行長バランス最適化）──────────────────────
# 行頭に置かない文字（約物＋小書き仮名＋長音＋撥音。ん は語頭に来ない＝直前で切ると語中割れ）
_HEAD_NG = frozenset("、。」』）)！？!?…・%％ーんっゃゅょゎぁぃぅぇぉッャュョヮァィゥェォ")
_PART = frozenset("をにがはへもとで")  # 折り返してよい助詞


def _is_kanji(c: str) -> bool:
    o = ord(c)
    return 0x4E00 <= o <= 0x9FFF or o == 0x3005


def _is_kata(c: str) -> bool:
    return 0x30A0 <= ord(c) <= 0x30FF


def _is_hira(c: str) -> bool:
    return 0x3040 <= ord(c) <= 0x309F


def _is_content(c: str) -> bool:  # 内容語らしい文字（漢字/カタカナ）
    return _is_kanji(c) or _is_kata(c)


def _hard_ok(s: str, k: int) -> bool:
    """位置k（s[k-1]とs[k]の間）で改行してよいか＝絶対禁則。"""
    prev, nxt = s[k - 1], s[k] if k < len(s) else ""
    if prev == "、":                       # 行末に読点を残さない（ユーザールール）
        return False
    if nxt and nxt in _HEAD_NG:            # 行頭禁則
        return False
    if prev == "で" and nxt in "きしすさせ":  # できる/です/でした 等の語中
        return False
    if prev == "と" and s[k:k + 2] in (
        "なる", "なっ", "なり", "いう", "いっ", "いい", "いわ", "いえ",
        "して", "した", "しま", "すれ", "せず",
    ):
        return False                       # となる/という/として 等の複合のみ禁止（皆様と｜いつまでも は許可）
    if prev in "おご" and nxt and _is_content(nxt):
        return False                       # 敬語接頭辞を行末に残さない（お｜帰り・ご｜来店）
    if _is_kata(prev) and nxt and _is_kata(nxt):  # カタカナ語の語中
        return False
    if prev.isascii() and prev.isalnum() and nxt and nxt.isascii() and nxt.isalnum():
        return False                       # 英単語・数値列の語中（LINE/Instagram/2026等）
    return True


def _break_penalty(s: str, k: int) -> float:
    """改行位置の不自然さペナルティ。_hard_ok前提。
    語中割れ級(T2/T3)は行長偏差(最大でも数百)より桁違いに重くし、
    バランスのために語中割れを選ばないことを実質保証する（辞書式優先）。"""
    prev, nxt = s[k - 1], s[k] if k < len(s) else ""
    prev2 = s[k - 2] if k >= 2 else ""
    if prev == "を":
        return 0.0                         # 「を」は現代語で常に助詞（ことを｜お待ち も自然）
    if prev in _PART and prev2 and _is_content(prev2):
        # 内容語＋助詞の直後。が/は/も/と は後続の述部との結び付きが強いので を/に/で/へ よりわずかに劣後
        return 0.5 if prev in "がはもと" else 0.0
    if s[k:k + 3] in ("ござい", "いただ", "くださ"):
        return 4.0                         # 丁寧語の語境界（ありがとう｜ございました 等）
    if _is_hira(prev) and nxt and _is_content(nxt):
        return 4.0                         # ひらがな→漢字/カタカナ＝新しい語の頭（たちの｜誇り）
    if _is_kanji(prev) and nxt and _is_kanji(nxt):
        return 15000.0                     # 漢字熟語の分断（来｜店・空｜間）＝最悪
    return 10000.0                         # ひらがな途中など＝語中割れの恐れ大


def wrapped_lines(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """日本語の折り返し（BudouX文節境界＋禁則＋行長バランスDP最適化）。

    スタイル（2026-07-15 黒木指定）：
      ・読点「、」の位置を最優先の改行点にし、行末に来る「、」は表示しない（詩の句切り風）
        例: 「ベモーレでお会いした時／皆様が…／空間でいられることが／私たちの誇りです。」
      ・短い読点句同士は1行に収まるならまとめる（読点は行中に残る）
      ・1句が1行に収まらない時だけ、句の中をBudouX文節境界＋バランスDPで折る
    保証（通常の日本語短文入力に対して）：
      ・行末に「、」を残さない／行頭に約物・小書き仮名・長音・ん を置かない
      ・語中割れをしない（私たち/との/ともに/向き合う等も保持）
      ・句点「。」で必ず改行し、文をまたいだ行を作らない
      ・結合結果は「行末で非表示にした読点」を除き元テキストと一致（空段落は保持しない）
    縮退入力（1文節が1行に収まらない長大語等）では、禁則より
    「必ず描画できること」を優先して緊急ハードカットする。
    """
    def wrap_sentence(sent: str) -> list[str]:
        n = len(sent)
        # 文字境界ごとの累積幅（CJKはカーニング無しなので差分で部分幅を得る。fontコール O(n)）
        pref = [0.0] * (n + 1)
        for i2 in range(1, n + 1):
            pref[i2] = font.getlength(sent[:i2])

        def width(a: int, b: int) -> float:
            return pref[b] - pref[a]

        if pref[n] <= max_w:
            return [sent]
        em = max(font.getlength("あ"), 1.0)

        # 改行候補と各ペナルティ。主候補=BudouX文節境界（語中割れゼロ）、
        # 予備=ヒューリスティック位置（境界不足時の緊急用・重ペナルティ）
        pen: dict[int, float] = {}
        budoux_bounds: set[int] = set()
        if _BUDOUX is not None:
            try:
                chunks = _BUDOUX.parse(sent)
            except Exception:
                chunks = []
            idx = 0
            for ch in chunks[:-1]:
                idx += len(ch)
                if len(ch) < 2:
                    continue  # 1文字チャンク（心から|く|つろいで 等のモデル癖）は次と結合
                budoux_bounds.add(idx)
        for k in range(1, n):
            if not _hard_ok(sent, k):
                continue
            if k in budoux_bounds:
                # が/は/も/と は後続の述部と結び付きが強いので を/に/で/へ よりわずかに劣後
                pen[k] = 0.5 if sent[k - 1] in "がはもと" else 0.0
            elif _BUDOUX is not None:
                pen[k] = 10000.0  # 文節境界でない位置は緊急時のみ
            else:
                pen[k] = _break_penalty(sent, k)  # BudouX無し環境のフォールバック
        cands = sorted(pen)

        # 貪欲法：最小行数の目安（候補が無い区間は緊急ハードカット。行頭禁則だけは可能な限り避ける）
        def greedy() -> list[int]:
            cuts, i = [], 0
            while i < n:
                j = i + 1
                while j <= n and width(i, j) <= max_w:
                    j += 1
                j -= 1
                if j >= n:
                    cuts.append(n)
                    break
                j = max(j, i + 1)
                k = next((x for x in reversed(cands) if i < x <= j), None)
                if k is None:
                    k = j
                    while k > i + 1 and sent[k] in _HEAD_NG:  # 緊急カットでも行頭禁則を極力回避
                        k -= 1
                cuts.append(k)
                i = k
            return cuts

        g_cuts = greedy()
        L = len(g_cuts)
        if n > 150:
            return _cuts_to_lines(sent, g_cuts)  # 長大入力はDPを省略（性能ガード）

        # 合法候補でL〜L+2行の各最適解を作り、総コスト最小の行数を採用
        # （L行では緊急位置が必要でも、L+1行なら文節境界だけで組める場合があるため）
        pos = sorted(set([0] + cands + [n]))
        INF = float("inf")
        best_sol: tuple[float, list[int]] | None = None
        for trial_L in (L, L + 1, L + 2):
            target = pref[n] / trial_L
            dp: dict[tuple[int, int], tuple[float, int]] = {(0, 0): (0.0, -1)}
            for l in range(1, trial_L + 1):
                for b in pos:
                    if b == 0:
                        continue
                    best = (INF, -1)
                    for a in pos:
                        if a >= b or (a, l - 1) not in dp:
                            continue
                        w = width(a, b)
                        if w > max_w:
                            continue
                        dev = ((w - target) / em) ** 2
                        c = dp[(a, l - 1)][0] + dev + (0.0 if b == n else pen[b])
                        if c < best[0]:
                            best = (c, a)
                    if best[0] < INF:
                        dp[(b, l)] = best
            if (n, trial_L) in dp:
                cost = dp[(n, trial_L)][0] + 30.0 * (trial_L - L)  # 行数増を強く抑制（バランス目的の不要な行増を防ぐ。緊急回避10000級の時だけL+1が勝つ）
                cuts, b, l = [], n, trial_L
                while l > 0:
                    cuts.append(b)
                    b = dp[(b, l)][1]
                    l -= 1
                cuts.reverse()
                if best_sol is None or cost < best_sol[0]:
                    best_sol = (cost, cuts)
        if best_sol is not None:
            return _cuts_to_lines(sent, best_sol[1])
        return _cuts_to_lines(sent, g_cuts)  # 合法候補では組めない（長大語など）→ 緊急カット込みの貪欲

    def disp_w(s: str) -> float:
        return font.getlength(s) if s else 0.0

    out: list[str] = []
    for para in text.split("\n"):
        # (表示文字列, 行末で読点を非表示にしたか) のリスト
        lines: list[tuple[str, bool]] = []
        for sent in re.findall(r"[^。]*。|[^。]+", para):
            # 読点で句に分割（、は句末に保持）→ 収まる限り句をまとめてグループ化
            clauses = re.findall(r"[^、]*、|[^、]+", sent)
            groups: list[str] = []
            cur = ""
            for cl in clauses:
                trial = cur + cl
                disp = trial.strip("、")  # 行頭・行末の読点はすべて非表示扱い
                if cur and disp_w(disp) > max_w:
                    groups.append(cur)
                    cur = cl
                else:
                    cur = trial
            if cur:
                groups.append(cur)
            for g in groups:
                disp = g.strip("、")  # 行頭・行末の読点はすべて非表示
                had_comma = g.endswith("、")
                if not disp:
                    continue  # 読点のみの縮退句は表示しない
                if disp_w(disp) <= max_w:
                    lines.append((disp, had_comma))
                else:
                    segs = wrap_sentence(disp)  # 1句が長い→句内をBudouX＋DPで折る
                    for i3, s3 in enumerate(segs):
                        lines.append((s3, had_comma if i3 == len(segs) - 1 else False))
        # 末尾の極短行を前行にマージ（非表示にした読点は復元して結合・幅と句点をチェック）
        merged: list[tuple[str, bool]] = []
        for disp, had in lines:
            if merged and len(disp) < 4 and not merged[-1][0].endswith("。"):
                pd, ph = merged[-1]
                cand = pd + ("、" if ph else "") + disp
                if disp_w(cand) <= max_w:
                    merged[-1] = (cand, had)
                    continue
            merged.append((disp, had))
        out.extend(d for d, _ in merged)
    return out


def _cuts_to_lines(sent: str, cuts: list[int]) -> list[str]:
    lines, prev = [], 0
    for c in cuts:
        lines.append(sent[prev:c])
        prev = c
    return lines


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
