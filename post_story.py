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

import anthropic
import requests
from PIL import Image, ImageDraw, ImageFont


def extract_json(text: str) -> dict:
    """括弧の深さを追跡して最初のJSONオブジェクトを正確に抽出する"""
    start = text.find("{")
    if start == -1:
        raise ValueError("JSONが見つかりません")
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSONの終端が見つかりません")

# ── 設定 ──────────────────────────────────────────────────────────
META_TOKEN    = os.environ["META_ACCESS_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
IMGBB_KEY      = os.environ["IMGBB_API_KEY"]
LINE_TOKEN     = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GDRIVE_REFRESH = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_CLIENT  = os.environ.get("GOOGLE_CLIENT_ID", "")
GDRIVE_SECRET  = os.environ.get("GOOGLE_CLIENT_SECRET", "")
USED_PHOTOS_FILE     = "used_photos.json"
COOLDOWN_DAYS        = 14  # 同じ写真を使わない日数
SIMILARITY_DAYS      = 3   # 類似写真を避ける日数
SIMILARITY_THRESHOLD = 8   # ahashのハミング距離（64ビット中・これ以下を「似ている」と判定）
GDRIVE_FOLDER        = "18K4hZUjbBH3V1XJjiSNNfss6GZnaTNqV"  # ベモーレ ストーリー素材（ルート）
GDRIVE_FOLDER_SLIM   = "170R8MxD_ByugDmxctVQbpmY2p3nXVDK8"  # 痩身
GDRIVE_FOLDER_FACIAL = "1DwNv1e5_j4YnDt23DNgYp9RatJQYpGtj"  # 肌質改善
GDRIVE_FOLDER_COMMON = "18eBpPM72QvZrlVwCAmeenfq6pNjoIEQy"   # 共通（部屋・内装など汎用）
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


def photo_ahash(img_bytes: bytes) -> str:
    """平均ハッシュ（ahash）で画像の見た目フィンガープリントを返す。PIL のみで計算。"""
    try:
        img = Image.open(BytesIO(img_bytes)).convert("L").resize((8, 8), Image.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return format(int(bits, 2), "016x")
    except Exception:
        return ""


def hash_distance(h1: str, h2: str) -> int:
    if not h1 or not h2:
        return 64
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def load_used_photos() -> dict[str, dict]:
    """使用済み写真を読み込む（14日以上前は除外）"""
    if not os.path.exists(USED_PHOTOS_FILE):
        return {}
    try:
        with open(USED_PHOTOS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cutoff = datetime.now(JST) - timedelta(days=COOLDOWN_DAYS)
        result = {}
        for fid, info in data.items():
            if isinstance(info, str):  # 旧フォーマット互換
                info = {"ts": info, "hash": ""}
            if datetime.fromisoformat(info.get("ts", "1970-01-01T00:00:00+00:00")) > cutoff:
                result[fid] = info
        return result
    except Exception:
        return {}


def get_recent_hashes(days: int) -> list[str]:
    """直近N日以内に使った写真のハッシュ一覧を返す（類似チェック用）"""
    if not os.path.exists(USED_PHOTOS_FILE):
        return []
    try:
        with open(USED_PHOTOS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cutoff = datetime.now(JST) - timedelta(days=days)
        return [
            info["hash"]
            for info in data.values()
            if isinstance(info, dict)
            and datetime.fromisoformat(info.get("ts", "1970-01-01T00:00:00+00:00")) > cutoff
            and info.get("hash")
        ]
    except Exception:
        return []


def save_used_photo(file_id: str, photo_hash: str = "") -> None:
    """使用した写真IDとハッシュを used_photos.json に記録する"""
    used = load_used_photos()
    used[file_id] = {"ts": datetime.now(JST).isoformat(), "hash": photo_hash}
    try:
        with open(USED_PHOTOS_FILE, "w", encoding="utf-8") as f:
            json.dump(used, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"used_photos.json 保存失敗: {e}", file=sys.stderr)


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

        # 今日のコースに対応するフォルダを決める（共通は常にフォールバック）
        has_slim   = any("痩身" in c for c in course_pool)
        has_facial = any("肌質" in c for c in course_pool)
        if has_slim and has_facial:
            folder_ids = [GDRIVE_FOLDER_SLIM, GDRIVE_FOLDER_FACIAL, GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]
        elif has_slim:
            folder_ids = [GDRIVE_FOLDER_SLIM, GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]
        elif has_facial:
            folder_ids = [GDRIVE_FOLDER_FACIAL, GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]
        else:
            folder_ids = [GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]

        # 優先フォルダから順に写真を探す
        auth_headers = {"Authorization": f"Bearer {token}"}
        used = load_used_photos()
        recent_h = get_recent_hashes(SIMILARITY_DAYS)

        for folder_id in folder_ids:
            r2 = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=auth_headers,
                params={
                    "q": f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
                    "fields": "files(id,name,thumbnailLink)",
                },
                timeout=15,
            )
            r2.raise_for_status()
            files = r2.json().get("files", [])
            if not files:
                continue

            # 14日クールダウン除外。全使用済みならフォルダ全体から選ぶ
            fresh = [f for f in files if f["id"] not in used]
            candidates = fresh if fresh else files
            random.shuffle(candidates)

            fallback = None
            for candidate in candidates:
                # サムネイルをダウンロードして類似チェック
                if recent_h:
                    thumb_url = candidate.get("thumbnailLink")
                    if thumb_url:
                        try:
                            tr = requests.get(thumb_url, timeout=10)
                            if tr.status_code == 200:
                                h = photo_ahash(tr.content)
                                if any(hash_distance(h, rh) <= SIMILARITY_THRESHOLD for rh in recent_h):
                                    if fallback is None:
                                        fallback = candidate
                                    continue  # 似ているのでスキップ
                        except Exception:
                            pass  # サムネ取得失敗は無視して続行

                # 類似でない（またはチェック不可）→ フル画像をダウンロード
                r3 = requests.get(
                    f"https://www.googleapis.com/drive/v3/files/{candidate['id']}",
                    headers=auth_headers,
                    params={"alt": "media"},
                    timeout=30,
                )
                r3.raise_for_status()
                h_full = photo_ahash(r3.content)
                save_used_photo(candidate["id"], h_full)
                label = "" if fresh else "（全使用済みのためリセット）"
                print(f"Drive写真: {candidate['name']}{label}")
                return r3.content

            # 全候補が似ていた場合 → フォールバック（最初の候補を使用）
            if fallback:
                r3 = requests.get(
                    f"https://www.googleapis.com/drive/v3/files/{fallback['id']}",
                    headers=auth_headers,
                    params={"alt": "media"},
                    timeout=30,
                )
                r3.raise_for_status()
                h_full = photo_ahash(r3.content)
                save_used_photo(fallback["id"], h_full)
                print(f"Drive写真: {fallback['name']}（類似のみのためフォールバック）")
                return r3.content

        return None
    except Exception as e:
        print(f"Drive取得失敗（グラデーション背景で代替）: {e}", file=sys.stderr)
        return None


# ── 3a. 日曜定休日コンテンツ生成 ────────────────────────────────
def generate_sunday_content(today: datetime) -> dict:
    month = today.month

    if month in (12, 1, 2):   season = "冬"
    elif month in (3, 4, 5):  season = "春"
    elif month in (6, 7, 8):  season = "夏"
    else:                      season = "秋"

    # 内容タイプをランダム選択
    result_type = random.choices(
        ["general", "skin", "body", "both"],
        weights=[40, 25, 25, 10],
    )[0]

    result_hint = {
        "general":  "先週たくさんのご予約・ご来院への純粋な感謝",
        "skin":     "先週お肌の変化・改善を実感してくださった方がいたことへの感謝（具体的な感情を含める）",
        "body":     "先週体の変化・ダイエット効果を実感してくださった方がいたことへの感謝（具体的な感情を含める）",
        "both":     "先週お肌と体の両方で嬉しい変化の報告があったことへの感謝",
    }[result_type]

    prompt = f"""あなたはエステサロン「ベモーレ」（大阪・谷町九丁目）の公式Instagramを運営するライターです。
今日は日曜日・定休日です。以下のルールで投稿文をJSONで出力してください。

今日：{month}月（{season}）・日曜日・定休日

【構成】
① 朝の挨拶（短く。「ベモーレです」は不要）
② 定休日のお知らせ＋{result_hint}
③ 明日月曜日から営業再開することを伝える締め（前向きで温かく）

【文章ルール】
・「ベモーレ」はカタカナのみ
・{season}らしい言葉や空気感を自然に一言だけ入れてもいい
・AIっぽい整いすぎた文章は禁止。黒木（オーナー）がそのまま投稿できる温度感
・敬語ベースで柔らかく。短文と中文を混ぜてリズムをつける
・誇張・大げさな表現は禁止
・毎週違う表現になるよう、定型フレーズを避ける

以下のJSONのみ出力（他は不要）：
{{
  "greeting": "朝の挨拶（1文）",
  "status": "定休日のお知らせ＋感謝（2〜3文）",
  "closing": "明日からの営業再開（1〜2文）"
}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        temperature=1,
        messages=[{"role": "user", "content": prompt}],
    )
    result = extract_json(message.content[0].text)
    result["courses"] = []  # 定休日はコースなし
    return result


# ── 3b. 平日コンテンツ生成（Claude Haiku） ───────────────────────
# ── 3c. 大阪の天気取得（Open-Meteo・APIキー不要） ────────────────
def get_weather(hour: int = 7) -> str | None:
    """大阪（谷町九丁目）の指定時刻の天気を日本語で返す。失敗時はNone。"""
    WMO = {
        0: "快晴",
        1: "晴れ",
        2: "晴れのち曇り",
        3: "曇り",
        45: "霧",
        48: "霧",
        51: "小雨",
        53: "雨",
        55: "強い雨",
        61: "小雨",
        63: "雨",
        65: "強い雨",
        71: "小雪",
        73: "雪",
        75: "大雪",
        77: "霰",
        80: "にわか雨",
        81: "雨",
        82: "激しい雨",
        85: "にわか雪",
        86: "大雪",
        95: "雷雨",
        96: "雷雨",
        99: "激しい雷雨",
    }
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 34.665,
                "longitude": 135.521,
                "hourly": "temperature_2m,weathercode",
                "timezone": "Asia/Tokyo",
                "forecast_days": 1,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        code = data["hourly"]["weathercode"][hour]
        temp = round(data["hourly"]["temperature_2m"][hour])
        desc = WMO.get(code, "曇り")
        print(f"天気: {desc}・{temp}℃（{hour}時・大阪）")
        return f"{desc}・{temp}℃"
    except Exception as e:
        print(f"天気取得失敗（スキップ）: {e}", file=sys.stderr)
        return None


def generate_content(today: datetime) -> dict:
    month   = today.month
    day     = today.day
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    if month in (12, 1, 2):   season = "冬"
    elif month in (3, 4, 5):  season = "春"
    elif month in (6, 7, 8):  season = "夏"
    else:                      season = "秋"

    # 満席状況をPython側で確定（AI任せにしない）
    status = random.choices(
        [
            "本日もリピーター様、ご新規様で満席となっております。",
            "本日もリピーター様で満席となっております。",
            "本日もリピーター様、ご新規様にお越しいただきます。",
        ],
        weights=[40, 40, 20],
    )[0]

    # 「ご新規様」が含まれる日は必ず体験メニュー、含まれない日は絶対に入れない
    has_new_guest = "ご新規様" in status

    slim_pick   = random.sample(COURSES_SLIM, k=random.randint(1, 2))
    facial_pick = random.sample(COURSES_FACIAL, k=1)

    if has_new_guest:
        # 痩身体験・肌質体験の両方／片方をランダムに
        trial_choice = random.choices(
            ["both", "slim_only", "facial_only"],
            weights=[40, 30, 30],
        )[0]
        if trial_choice == "both":
            extra = random.choice([slim_pick[0], facial_pick[0]])
            course_pool = ["全身痩身体験", "肌質改善体験", extra]
        elif trial_choice == "slim_only":
            course_pool = ["全身痩身体験"] + slim_pick[:1] + facial_pick[:1]
        else:
            course_pool = ["肌質改善体験"] + slim_pick[:1] + facial_pick[:1]
    else:
        course_pool = slim_pick + facial_pick

    courses_str = "\n".join(f"・{c}" for c in course_pool)

    # 大阪の実際の天気を取得
    weather = get_weather(hour=7)
    weather_line = f"\n今日の大阪の天気：{weather}（7時時点）" if weather else ""

    prompt = f"""あなたはエステサロン「ベモーレ」（大阪・谷町九丁目）の公式Instagramを運営するライターです。
以下のルールに従い、今日のInstagramストーリー1枚目の文章をJSONで出力してください。

今日：{month}月{day}日（{weekday}曜日）・{season}{weather_line}

【1枚目の構成ルール】
① 朝の挨拶（1〜2文。「ベモーレです」は不要。自然な挨拶のみ）
② ご来店を心待ちにしていることが伝わる一言（実際の天気が参考になれば自然に触れる。雨なら必ず触れる。晴れや平凡な天気なら季節・気遣いでも可。毎回変える）

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
  "closing": "心待ちにしている一言（季節・気遣い含む、1文）"
}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        temperature=1,
        messages=[{"role": "user", "content": prompt}],
    )
    result = extract_json(message.content[0].text)
    result["status"] = status   # Pythonで決定した文言をそのまま使う（Claude変更禁止）
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
        """
        1. 。・、の後は必ず改行
        2. 行が40%を超えたら助詞（で・に・を・が・は・と）の後を潜在的な改行点として記録
        3. max_w 超過時に、残り4文字以上になる助詞位置で折り返し
           残りが3文字以下になる場合は折り返しを見送り、次の区切りまで待つ（「ね。」「す。」防止）
        """
        HARD = frozenset("。、")
        SOFT = frozenset("にをがはとり")  # 「で」は複合語除外。「り」はゆっくり・しっかり等の語末で自然な折り返し点
        half_w = max_w * 0.40
        MIN_REMAIN = 4

        lines, line, soft_line = [], "", None

        for ch in text:
            line += ch
            w = font.getbbox(line)[2]

            if ch in HARD:
                lines.append(line)
                line = ""
                soft_line = None
                continue

            # オーバーフロー検査を soft_line 更新より先に実行
            if w > max_w and len(line) > 1:
                if soft_line and len(soft_line) < len(line):
                    remaining = line[len(soft_line):]
                    if len(remaining) >= MIN_REMAIN:
                        lines.append(soft_line)
                        line = remaining
                        soft_line = None
                        continue  # soft_line 更新スキップ
                    # 残りが短すぎ → はみ出し許容（soft_line 更新もスキップ）
                else:
                    lines.append(line[:-1])
                    line = ch
                    soft_line = None
                continue  # オーバーフロー後は soft_line を更新しない

            # オーバーフローなしのときだけ soft_line を更新
            if ch in SOFT and w >= half_w:
                soft_line = line

        if line:
            lines.append(line)

        # 短すぎる末尾行（MIN_REMAIN未満）を前行にマージ（「す。」「ね。」防止）
        merged = []
        for ln in lines:
            if merged and len(ln) < MIN_REMAIN:
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
    if not r.ok:
        raise Exception(f"{r.status_code} {r.text[:400]}")
    creation_id = r.json()["id"]

    # Instagramのコンテナ処理完了を待つ（最大60秒）
    for attempt in range(12):
        time.sleep(5)
        status_r = requests.get(f"{META_API}/{creation_id}", params={
            "fields": "status_code",
            "access_token": META_TOKEN,
        }, timeout=15)
        if status_r.ok:
            status = status_r.json().get("status_code", "")
            print(f"  コンテナ状態: {status} (試行{attempt+1})")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise Exception("コンテナ処理エラー（Instagram側）")
        # IN_PROGRESS or unknown → 待機継続

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

    print("完了")


if __name__ == "__main__":
    main()
