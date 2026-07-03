"""文章生成

Claude（Haiku）による投稿文の生成と、その材料になる季節判定・天気取得。
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime

import requests

from .config import ANTHROPIC_KEY, COURSES_FACIAL, COURSES_SLIM
from .state import load_recent_closings, save_recent_text
from .util import claude_text, extract_json


# ── 季節判定（月＋日）─────────────────────────────────────────
def get_season(today: datetime) -> str:
    """月単位だと5月末でも『春』と出てしまうため、日付まで見て季節感を返す。"""
    md = (today.month, today.day)
    if   md >= (12, 1) or md < (2, 18):  return "冬"
    elif md < (5, 16):                   return "春"
    elif md < (6, 21):                   return "初夏"
    elif md < (9, 8):                    return "夏"
    elif md < (11, 16):                  return "秋"
    else:                                return "冬"


# ── 日曜定休日コンテンツ生成 ──────────────────────────────────
def generate_sunday_content(today: datetime) -> dict:
    month  = today.month

    # 日曜（定休日）は季節に触れない（毎回同じ季節フレーズになりがちで定型的なため）
    season_label = ""
    season_rule = "・季節や天気の言葉（春・初夏・夏・秋・冬など）は入れない。感謝や気遣いで自然に書く"

    # 内容タイプ（②の感謝・振り返りの切り口）をランダム選択。毎週違う切り口になるよう種類を増やしている
    hints = {
        "general":  "先週のご予約・ご来店への感謝",
        "skin":     "先週お肌の変化を実感してくださった方への感謝",
        "body":     "先週体の変化・ダイエット効果を実感してくださった方への感謝",
        "both":     "先週お肌と体の両方で嬉しい変化があったことへの感謝",
        "blessed":  "素敵なお客様に恵まれていることへの感謝（先週も幸せな一週間だった、という温度感）",
        "talk":     "お客様との会話や笑顔が日々の励みになっていること",
        "longtime": "長く通い続けてくださる方への感謝",
        "newguest": "先週は新しいお客様との出会いが多かったことへの感謝",
        "effort":   "結果を出そうと頑張るお客様の姿に、こちらが励まされていること（寄り添い）",
        "recharge": "今日はスタッフ一同ゆっくり充電して、また月曜に良い状態でお迎えしたいこと",
    }
    result_type = random.choices(
        list(hints),
        weights=[12, 12, 12, 8, 12, 10, 10, 10, 10, 8],
    )[0]
    result_hint = hints[result_type]

    prompt = f"""あなたはエステサロン「ベモーレ」（大阪・谷町九丁目）の公式Instagramを運営するライターです。
今日は日曜日・定休日です。以下のルールで投稿文をJSONで出力してください。

今日：{month}月{season_label}・日曜日・定休日

【構成】
① 朝の挨拶（必ず「おはようございます。」で始める・短く。「ベモーレです」は不要）
② 本日が「定休日」であることを必ず「定休日」という言葉で伝える＋{result_hint}
③ 明日月曜日から営業再開することを伝える締め（前向きで温かく）

【文章ルール】
・必ず「おはようございます。」で書き出す
・本日の休みは必ず「定休日」と書く（「お休み」だけは不可。臨時休業と誤解されるため）
・「ベモーレ」はカタカナのみ
・「皆さん」は使わない（必ず「皆様」）
・「来院」は使わない（病院の言葉。サロンなので必ず「来店」）
{season_rule}
・AIっぽい整いすぎた文章は禁止。黒木（オーナー）がそのまま投稿できる温度感
・敬語ベースで柔らかく。短文と中文を混ぜてリズムをつける
・誇張・大げさな表現は禁止
・毎週違う表現になるよう、定型フレーズを避ける
・全体を短く簡潔に（ストーリー1枚に余裕で収まる量）。お礼の重複・説明の盛りすぎ・冗長な言い回しは禁止

以下のJSONのみ出力（他は不要）：
{{
  "greeting": "「おはようございます。」で始まる朝の挨拶（1文・短く）",
  "status": "定休日のお知らせ＋感謝（合わせて2文・簡潔に）",
  "closing": "明日からの営業再開（1文・簡潔に）"
}}"""

    result = extract_json(claude_text(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        temperature=1,
        messages=[{"role": "user", "content": prompt}],
        api_key=ANTHROPIC_KEY,
    ))
    result["greeting"] = "おはようございます。"  # 挨拶は固定（事実でない一文の創作を防ぐ）
    result["courses"] = []  # 定休日はコースなし
    return result


# ── 大阪の天気取得（Open-Meteo・APIキー不要） ────────────────────
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


# ── 平日コンテンツ生成（Claude Haiku） ───────────────────────────
def generate_content(today: datetime) -> dict:
    month   = today.month
    day     = today.day
    weekday = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]

    season  = get_season(today)

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
    facial_pick = random.sample(COURSES_FACIAL, k=random.randint(1, 2))

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

    # 当日だけの手動指定：新規の肌質改善体験のお客様がいる日などに環境変数で固定する
    if os.environ.get("STORY_FORCE_FACIAL_TRIAL") == "1":
        status = "本日もリピーター様、ご新規様にお越しいただきます。"
        course_pool = ["肌質改善体験"] + slim_pick[:1] + facial_pick[:1]
        courses_str = "\n".join(f"・{c}" for c in course_pool)
        print("STORY_FORCE_FACIAL_TRIAL=1 → ご新規＋肌質改善体験で固定")

    # 平日は季節に触れない。天気も基本触れず、大雨など足元が悪い日だけ気遣いを入れる。
    weather = get_weather(hour=7)
    # 足元が悪い天気（雨・雪・雷・霰。ただし小雨・小雪は除く）
    is_bad_footing = bool(weather) and any(k in weather for k in ("雨", "雪", "雷", "霰")) \
        and "小雨" not in weather and "小雪" not in weather

    season_label = ""  # 季節は出さない
    weather_line = f"\n今日の大阪の天気：{weather}（7時時点・足元が悪い）" if is_bad_footing else ""

    if is_bad_footing:
        hook_rule = ("② ご来店を心待ちにしている一言。今日は足元が悪いので"
                     "「足元の悪い中ですが、お気をつけてお越しください」のような気遣いを自然に一言"
                     "（季節の言葉は使わない・毎回表現を変える）")
        closing_hint = "心待ちにしている一言（足元への気遣いを含む、1文）"
    else:
        hook_rule = ("② ご来店を心待ちにしている一言。天気や季節の話には触れず、"
                     "感謝・気遣い・サロンの雰囲気など別の切り口で（毎回変える）")
        closing_hint = "心待ちにしている一言（天気・季節に触れない、1文）"

    recent_closings = load_recent_closings(10)
    avoid_block = ""
    if recent_closings:
        lst = "\n".join(f"・{g}" for g in recent_closings)
        avoid_block = (
            f"\n\n【最近使った締めの一言＝繰り返さない】\n{lst}\n"
            "上記と同じ・似た締めにならないよう、毎回違う切り口にする。"
        )

    prompt = f"""あなたはエステサロン「ベモーレ」（大阪・谷町九丁目）の公式Instagramを運営するライターです。
今日のInstagramストーリー1枚目の「締めの一言」だけをJSONで出力してください。
（挨拶「おはようございます。」と満席のお知らせはこちらで付けるので、生成しないでください）

今日：{month}月{day}日{season_label}{weather_line}

【締めの一言のルール】
{hook_rule}{avoid_block}

【文章ルール（最重要）】
・事実でないことを書かない（「準備しています」「〜しています」など、確認できない具体的な行動・状況を勝手に作らない）
・曜日（月曜日・火曜日など）には一切触れない・書かない
・「ベモーレ」はカタカナ表記のみ（Bemolleは使わない）
・「皆さん」は使わない（必ず「皆様」）
・AIっぽい整いすぎた文章は禁止。黒木（オーナー）がそのまま投稿できる温度感
・敬語ベースで柔らかく、誇張・無駄な修飾語は削る

以下のJSONのみ出力（他は不要）：
{{
  "closing": "{closing_hint}"
}}"""

    result = extract_json(claude_text(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        temperature=1,
        messages=[{"role": "user", "content": prompt}],
        api_key=ANTHROPIC_KEY,
    ))
    result["greeting"] = "おはようございます。"  # 挨拶は固定（事実でない一文の創作を防ぐ）
    result["status"] = status   # Pythonで決定した文言をそのまま使う（Claude変更禁止）
    result["courses"] = course_pool
    save_recent_text(result["greeting"], result.get("closing", ""))  # 締めの連日重複を防ぐ履歴
    return result
