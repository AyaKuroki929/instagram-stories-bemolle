"""文章生成

Claude（Haiku）による投稿文の生成と、その材料になる季節判定・天気取得。
"""
from __future__ import annotations

import os
import random
import re
import sys
from datetime import datetime

import requests

from .config import ANTHROPIC_KEY, COURSES_FACIAL, COURSES_SLIM, MONTHLY_PHOTOS
from .state import load_recent_closings, load_recent_theme_days
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


# ── 毎月1日の感謝コンテンツ（2026-08-01彩さん承認の4パターンをローテーション）──
# 通常ストーリーの「後」に上げる（おはようございます→感謝の順・2026-08-01彩さん指示）。
# 挨拶なし・引用に句点を入れない・ダッシュ絶対禁止・「させてください」の姿勢。
MONTHLY_PATTERNS = [
    {  # A: 王道（感謝→嬉しい報告→させてください）
        "status": "{prev}月もご来店いただき、ありがとうございました。"
                  "「肌の調子が良くなった」「周りに気づいてもらえた」"
                  "そんな嬉しいご報告をいくつもいただいた1ヶ月でした。",
        "closing": "{cur}月も、皆様の綺麗をサポートさせてください。今月もよろしくお願いいたします。",
    },
    {  # B: お客様の頑張りを立てる
        "status": "{prev}月も、ご来店ありがとうございました。"
                  "結果が出たのは、通い続けてくださった皆様の頑張りがあってこそです。"
                  "そのお手伝いができたことが、嬉しい1ヶ月でした。",
        "closing": "{cur}月も、皆様の綺麗のお手伝いをさせてください。心よりお待ちしております。",
    },
    {  # C: 変化への喜び
        "status": "{prev}月はご来店いただき、ありがとうございました。"
                  "お会いするたびに表情が明るくなっていく。"
                  "その変化を近くで見られて、嬉しい1ヶ月でした。",
        "closing": "{cur}月も、皆様がもっと自分を好きになれるように、サポートさせてください。"
                   "今月もよろしくお願いいたします。",
    },
    {  # D: 月の始まりから入る
        "status": "{cur}月が始まりました。{prev}月もご来店いただき、ありがとうございました。"
                  "嬉しい変化のご報告が多く、私たちも励まされた1ヶ月でした。",
        "closing": "今月も、皆様の綺麗をサポートさせてください。どうぞよろしくお願いいたします。",
    },
]


def generate_monthly_content(today: datetime) -> dict:
    """毎月1日、通常ストーリーの後に上げる「先月の感謝＋今月の意気込み」。
    構成は4パターンを月替わりローテーション。同じパターンの2回目以降は、過去の全文履歴を
    渡して「言い回しを一生被らせない」変化版をHaikuで生成（失敗時は承認済みベース文で投稿を止めない）。"""
    from .state import load_monthly_texts

    prev = (today.month - 2) % 12 + 1
    cur = today.month
    idx = (today.year * 12 + today.month) % 4
    base = MONTHLY_PATTERNS[idx]
    status = base["status"].format(prev=prev, cur=cur)
    closing = base["closing"].format(prev=prev, cur=cur)

    history = load_monthly_texts()
    used_this_pattern = [h for h in history if h.get("pattern") == idx]
    if used_this_pattern:  # 2周目以降だけ変化版を生成（1周目は承認済み原文）
        try:
            past = "\n".join(f"・{h['status']}／{h['closing']}" for h in history)
            prompt = f"""あなたはエステサロン「ベモーレ」のInstagramストーリー文を書くライターです。
毎月1日の「先月の感謝＋今月の意気込み」ストーリーの文章を、下のベース文と同じ構成・同じ姿勢のまま、
言い回しだけ新しくして作ってください。

【ベース文（構成と温度感はこのまま）】
status: {status}
closing: {closing}

【過去に使った文＝言い回し・フレーズを一切繰り返さない】
{past}

【厳守ルール】
・「おはようございます」は入れない（直前の通常ストーリーに入っているため）
・「{prev}月」への感謝→嬉しい報告や変化→「{cur}月も〜させてください」の姿勢（こちらが奉仕する側）
・かぎかっこ内の短いセリフ引用に句点「。」は入れない
・ダッシュ（—や——）は絶対に使わない
・大げさな言葉禁止：報酬・栄養・原点・誇り・何より・一番の・最高の・かけがえのない・たくさん の連発
・「皆さん」でなく「皆様」。「来院」でなく「来店」
・「〜しましょう」の誘い形は使わない（お迎えする立場の言葉で）
・長さはベース文と同程度。飾らず、現場でそのまま言える言葉で

以下のJSONのみ出力：
{{"status": "...", "closing": "..."}}"""
            result = extract_json(claude_text(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
                api_key=ANTHROPIC_KEY,
            ))
            new_s, new_c = (result.get("status") or "").strip(), (result.get("closing") or "").strip()
            ok = (new_s and new_c and "—" not in new_s + new_c
                  and "おはよう" not in new_s + new_c and f"{cur}月" in new_s + new_c)
            if ok:
                status, closing = new_s, new_c
            else:
                print("月初文の生成が検証NG→承認済みベース文を使用", file=sys.stderr)
        except Exception as e:
            print(f"月初文の生成失敗→承認済みベース文を使用: {e}", file=sys.stderr)

    return {
        "greeting": "",  # 挨拶なし（通常ストーリー側にある）
        "status": status,
        "closing": closing,
        "courses": [],
        # 月初の写真は「その月の1枚」に固定（月替わりローテ）。プレビューと本番が必ず同じ写真になり、
        # 14日クールダウンの影響で写真がすり替わる事故を起こさない（2026-08-01彩さん指示）。
        # 上げ直し等でさらに固定したい時は STORY_MONTHLY_PHOTO にファイル名を指定。
        "photo_names": [os.environ.get("STORY_MONTHLY_PHOTO")
                        or MONTHLY_PHOTOS[(today.year * 12 + today.month) % len(MONTHLY_PHOTOS)]],
        "layout": "lower",  # 顔が上部にある写真向け：文字は下半分＋暗幕（2026-08-01彩さん指摘）
        "pattern": idx,
    }


# ── DM案内ストーリー（火金20時・2026-08-06彩さん指示・固定文）──────────
def generate_dm_info_content() -> dict:
    """お問い合わせはDMでなく公式LINEへ、の定期案内。文言は彩さん確定の固定文。
    写真は通常のプール（共通）から重複回避エンジンで選ぶ＝前後のストーリーと被らない。"""
    return {
        "greeting": "",
        "status": "日々たくさんのお問い合わせのDMをいただきますが、"
                  "DMでは質問への回答はお受けしておりません。",
        "closing": "公式LINEよりお問い合わせをお願いいたします。"
                   "公式LINEはプロフィールのURLからご登録いただけます。",
        "courses": [],
    }


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
① 先週への感謝を一言（{result_hint}）
② 明日月曜日から営業再開することを伝える締め（前向きで温かく）
※「おはようございます。」と「本日は定休日をいただいております。」はこちらで自動で付けるので書かないこと（感謝と締めだけを書く）

【文章ルール】
・「ベモーレ」はカタカナのみ
・「皆さん」は使わない（必ず「皆様」）
・「来院」は使わない（病院の言葉。サロンなので必ず「来店」）
・「〜しましょう」の誘い形は使わない（お迎えする立場の言葉で。「お待ちしております」等）
{season_rule}
・AIっぽい整いすぎた文章は禁止。黒木（オーナー）がそのまま投稿できる温度感
・敬語ベースで柔らかく。短文と中文を混ぜてリズムをつける
・誇張・大げさな表現は禁止・事実でないことを書かない
・毎週違う表現になるよう、定型フレーズを避ける
・全体を短く簡潔に（ストーリー1枚に余裕で収まる量）。お礼の重複・説明の盛りすぎ・冗長な言い回しは禁止

以下のJSONのみ出力（他は不要）：
{{
  "gratitude": "先週への感謝の一言（1文・簡潔に）",
  "closing": "明日月曜日からの営業再開（1文・簡潔に）"
}}"""

    result = extract_json(claude_text(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
        api_key=ANTHROPIC_KEY,
    ))
    # 挨拶と「定休日」の明記はPython側で固定（モデルが定休日を落とす事故を防ぐ）
    result["greeting"] = "おはようございます。"
    gratitude = (result.get("gratitude") or result.get("status") or "").strip()
    result["status"] = "本日は定休日をいただいております。" + gratitude
    result["courses"] = []  # 定休日はコースなし
    return result


# ── 大阪の天気取得（Open-Meteo・APIキー不要） ────────────────────
def get_weather(hour: int = 7) -> str | None:
    """大阪（谷町九丁目）の「今の実況」天気を日本語で返す。失敗時はNone。
    予報の時刻枠は見ない（7時の予報が雨でも実際は止んでいるズレの実害・2026-08-13彩さん指示）。"""
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
                "current_weather": True,
                "timezone": "Asia/Tokyo",
                "forecast_days": 1,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        code = data["current_weather"]["weathercode"]
        temp = round(data["current_weather"]["temperature"])
        desc = WMO.get(code, "曇り")
        print(f"天気: {desc}・{temp}℃（実況・大阪）")
        return f"{desc}・{temp}℃"
    except Exception as e:
        print(f"天気取得失敗（スキップ）: {e}", file=sys.stderr)
        return None


# ── 締め候補の選定（3候補から過去と最も遠い1本を選ぶ） ─────────────
_CLOSING_BAD_WORDS = (
    # 曜日・呼称
    "曜日", "月曜", "火曜", "水曜", "木曜", "金曜", "土曜", "日曜", "皆さん", "来院",
    # 盛り語（プロンプト禁止と機械チェックを一致させる）
    "報酬", "栄養", "原点", "誇り", "何より", "一番の", "最高の", "かけがえ",
    # 誘い形（「お会いしましょう」＝対等の誘いでお客様への言葉として不適切・2026-08-13彩さん指摘）
    "ましょう",
    # 身体の観察系（気持ち悪くなる・2026-08-13彩さん指示）
    "表情", "呼吸", "姿勢",
    # 季節・天気（プロンプト禁止と機械チェックを一致させる）
    "春", "夏", "秋", "冬", "天気",
    # 業種違いの語彙（ベモーレは結果を出す痩身・フェイシャル専門。マッサージ/リラクゼーションではない・2026-08-13彩さん指示）
    "ほぐ", "マッサージ", "癒し", "癒や", "リラックス", "リラクゼーション",
)


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"\s", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def _pick_closing(cands: list, recent: list[str]) -> str | None:
    """3候補を検証し、直近の締めとの文字類似（bigram Dice）が最も低い1本を採用。
    全候補が検証NGなら None を返す（未検証のまま投稿しない・2026-08-13彩さん指示。
    呼び出し側で再生成→最終的に安全な固定文へフォールバック）。スコアはログに出す。"""
    valid = []
    for c in cands:
        c = str(c or "").strip()
        if not c or len(c) > 70:
            continue
        if any(w in c for w in _CLOSING_BAD_WORDS):
            continue
        # 文末の「ね」「よ」等のくだけた終助詞を却下（×お待ちしていますね・2026-08-13彩さん指示）
        if re.search(r"[ねよわ][。！!♪]*$", c) or re.search(r"[ねよわ]。", c):
            continue
        # サロン側の願望・決意の表明を却下（×「お体をほぐしていきたいです」＝読み手不在の自分語り・2026-08-13彩さん指示）
        if re.search(r"たいです|たいと思|ていきたい", c):
            continue
        # 裸の「お越しをお待ち」を却下（「皆様のお越しを〜」の形だけ自然・2026-08-13彩さん指示）
        if re.search(r"(?<!の)お越しをお待ち", c):
            continue
        valid.append(c)
    if not valid:  # 全滅→未検証の文は絶対に出さない（禁止語「何より」が世に出た実害・2026-08-13）
        print("  ⚠️ 全候補が検証NG→再生成へ", file=sys.stderr)
        return None
    # 読み手に向かう言葉を含む候補を優先（サロンの状態報告で閉じる文を後回しに・2026-08-08彩さん指摘）
    reader_words = ("お待ち", "お会い", "お越し", "ご来店", "どうぞ", "楽しみ")
    reader = [c for c in valid if any(w in c for w in reader_words)]
    if reader:
        valid = reader
    else:
        print("  ⚠️ 読み手向け表現を含む候補なし（全候補が状態報告型）", file=sys.stderr)
    if not recent:
        return valid[0]

    def max_sim(c: str) -> float:
        bc = _bigrams(c)
        return max((2 * len(bc & _bigrams(r)) / max(1, len(bc) + len(_bigrams(r))) for r in recent),
                   default=0.0)

    scored = sorted((max_sim(c), i, c) for i, c in enumerate(valid))
    for sc, i, c in scored:
        print(f"  締め候補{i + 1}: 類似={sc:.2f} {c}")
    return scored[0][2]


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
    weather = get_weather()  # 実況
    # 足元が悪い天気（雨・雪・雷・霰。ただし小雨・小雪は除く）
    is_bad_footing = bool(weather) and any(k in weather for k in ("雨", "雪", "雷", "霰")) \
        and "小雨" not in weather and "小雪" not in weather

    season_label = ""  # 季節は出さない
    weather_line = ""  # 足元が悪い日は締めを固定文にするためプロンプトには渡さない

    # 締めの切り口（ID・指示文・重み）。指示文は「方向」だけ示す＝完成文をテーマにすると
    # 毎回同じ文に収束する（8/4「昨日と今朝がほぼ同じ」の実害。Sol設計レビューで抽象化）
    THEME_DEFS = [
        ("thanks",  "ご来店への感謝や気遣いをシンプルに", 35),
        ("change",  "★お客様の良い結果・変化（痩身や肌）を喜ぶ気持ち、または"
                    "お客様からいただいた嬉しい言葉。この3つの角度だけ"
                    "（表情・呼吸・姿勢など身体の観察はしない＝気持ち悪くなる・2026-08-13彩さん指示。"
                    "特定の個人の話・数字・誇張にはしない）", 25),
        ("careful", "今日も一人ひとり丁寧に施術する、という当たり前の気持ち", 20),
        ("plain",   "お会いできるのを楽しみにしている、それだけを飾らずに", 20),
    ]
    NORMAL_THEME_IDS = {t[0] for t in THEME_DEFS}

    if is_bad_footing:
        # 足元が悪い日はAI生成せず固定文（生成させると「滑りやすい路面」等の
        # 報道調・AIぽい言い回しになる実害・2026-08-13彩さん指示）
        kind = "雪" if "雪" in weather else "雨"
        fixed_closing = f"{kind}で足元が悪いので、お気をつけてお越しください。"
        print(f"締め: 固定文（足元が悪い日・{weather}）")
        return {
            "greeting": "おはようございます。",
            "status": status,
            "courses": course_pool,
            "closing": fixed_closing,
            "theme": "weather",
        }
    # 直近2日で使った切り口は除外（連続・1日おきの意味被りを防ぐ。Sol設計レビュー2026-08-04）
    recent_themes = load_recent_theme_days(2, allowed=NORMAL_THEME_IDS)
    cands = [t for t in THEME_DEFS if t[0] not in recent_themes] or THEME_DEFS
    theme_id, closing_theme, _w = random.choices(cands, weights=[t[2] for t in cands])[0]
    print(f"締めの切り口: {theme_id}（直近2日の除外: {sorted(recent_themes)}）")
    hook_rule = ("② ご来店を心待ちにしている一言。天気や季節の話には触れず、"
                 f"今日の切り口＝{closing_theme}。\n"
                 "【AIっぽさ禁止・厳守】\n"
                 "・「皆様が〜している姿（様子・こと・時間）が、私たちの〜です」という構文は使わない"
                 "（毎日この骨組みで言葉だけ替わるのが一番AIっぽい）\n"
                 "・大げさな言葉禁止：報酬・栄養・原点・誇り・何より・一番の・最高の・かけがえのない\n"
                 "・短く。飾らず、現場でお客様にそのまま言える一言（長い詩的な文にしない）\n"
                 "・主語は省けるなら省く（「皆様が」「お客様が」を毎回付けない。"
                 "誰のことか伝わるなら無い方が自然で、直接その人に届く言葉になる）\n"
                 "・温かみは残す（事務的な定型だけにはしない）\n"
                 "・締めは必ず読み手に向けた言葉で文を閉じる（お待ちしています・お会いできるのを"
                 "楽しみにしています等）。サロンの状態・行動の報告で文を終えない"
                 "（例：「準備ができています」で終わるのはNG。「準備をしてお待ちしております」ならOK）\n"
                 "・奇をてらわない。日常でお客様にそのまま言う言葉の範囲で書く\n"
                 "・同じ言葉を1つの文の中で繰り返さない（「感じるたび…感じます」等）")
    closing_hint = "心待ちにしている一言（天気・季節に触れない、1文・短く）"

    recent_closings = load_recent_closings(10)
    avoid_block = ""
    if recent_closings:
        yesterday = recent_closings[-1]
        others = recent_closings[:-1]
        avoid_block = (
            f"\n\n【昨日の締め＝意味ごと使用禁止（最重要）】\n・{yesterday}\n"
            "言い換えての再利用も禁止。昨日と同じ出来事・感情・因果関係を、今日は取り上げないこと。"
        )
        if others:
            lst = "\n".join(f"・{g}" for g in others)
            avoid_block += (
                f"\n\n【最近の締め（参考・どれにも似せない）】\n{lst}\n"
                "言葉を入れ替えるだけの類似も禁止。文の骨組み（構文・語順・文末）ごと変えること。"
            )

    prompt = f"""あなたはエステサロン「ベモーレ」（大阪・谷町九丁目）の公式Instagramを運営するライターです。
ベモーレは結果を出す痩身・フェイシャル専門のエステサロンです（マッサージ・リラクゼーションのサロンではありません）。
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
・「来院」は使わない（病院の言葉。サロンなので必ず「来店」）
・「〜しましょう」の誘い形は使わない（対等の誘いになり、お客様をお迎えする立場の言葉として不適切。文末は「お待ちしております」等の迎える言葉で）
・文末に「ね」「よ」などのくだけた終助詞を付けない（×「お待ちしていますね」→○「お待ちしております」「お待ちいたしております」。きちんとした敬語で結ぶ）
・マッサージ・リラクゼーションの言葉（ほぐす・癒し・リラックス等）は使わない（業種が違う）
・自分（サロン側）の希望・決意を語らない（×「お体をほぐしていきたいです」等の「〜したいです」「〜していきたい」は禁止）。2文になる場合も、どの文も読み手に向けた内容で書く
・AIっぽい整いすぎた文章は禁止。黒木（オーナー）がそのまま投稿できる温度感
・敬語ベースで柔らかく、誇張・無駄な修飾語は削る

以下のJSONのみ出力（他は不要）：
{{
  "closings": ["{closing_hint}", "2本目（1本目と違う言い回し・違う文型で）", "3本目（さらに別の言い回し・別の文型で）"]
}}"""

    closing = None
    for attempt in range(3):  # 全候補NGなら生成し直し（最大2回リトライ）
        raw = extract_json(claude_text(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
            api_key=ANTHROPIC_KEY,
        ))
        cands = raw.get("closings") or ([raw["closing"]] if raw.get("closing") else [])
        closing = _pick_closing(cands, recent_closings)
        if closing:
            break
        print(f"  再生成 {attempt + 1}/2", file=sys.stderr)
    if not closing:
        # それでも全滅なら安全な固定文（禁止語が世に出る経路をゼロに・2026-08-13彩さん承認の文面）
        closing = "皆様のお越しを心よりお待ちしております。"
        print("  ⚠️ 3回全滅→固定文で投稿", file=sys.stderr)
    return {
        "greeting": "おはようございます。",  # 挨拶は固定（事実でない一文の創作を防ぐ）
        "status": status,                    # Pythonで決定した文言をそのまま使う（Claude変更禁止）
        "courses": course_pool,
        "closing": closing,
        "theme": theme_id,                   # 履歴保存は投稿成功後にmain.pyで行う（失敗時に履歴が進むバグの修正）
    }
