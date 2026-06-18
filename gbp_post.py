#!/usr/bin/env python3
"""週1回（日曜）Googleビジネスプロフィール用の AIO 最適化投稿を生成し、
LINE（Claude通知Bot）へ「貼るだけ」の形で配信する（テキストのみ／画像なし）。

GBP API のアクセス申請は3回却下（申請アカウントのリスティングwebsite不一致が真因）。
そのため自動投稿はせず、人がGBPアプリに貼る半自動方式。規約リスクゼロ・追加課金ゼロ。
LINE broadcast の仕組みは instagram-stories-bemolle から流用（本体 post_story.py は不変更）。
"""
import os
import sys
import json
import traceback
from datetime import datetime, timezone, timedelta

import requests
import anthropic

JST = timezone(timedelta(hours=9))

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
LINE_TOKEN    = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

MODEL = "claude-sonnet-4-6"  # 週1回・コスト軽微。品質を上げたければ claude-opus-4-8 に変更可

STATE_FILE = "gbp_state.json"  # {"last_index": int, "recent_texts": [..]}

# ── テーマ輪番（毎週ローテーション）──────────────────────────────
# angle: その回でAIに書かせる切り口（実際に検索/AIに聞かれる悩みに答える形）
THEMES = [
    {
        "key": "tarumi", "label": "たるみ・フェイスライン",
        "angle": "「スキンケアを変えてもフェイスラインのたるみが戻らない」という悩みに、"
                 "肌を支える真皮のコラーゲン減少→土台がゆるむ→表面ケアでは届かない、という機序で答える。"
                 "プラペン（医療レベルの幹細胞原液を真皮へ届ける施術）が土台にアプローチすることに繋げる。",
    },
    {
        "key": "slim", "label": "痩身・ボディの仕組み",
        "angle": "「食事制限してもお腹や下半身が落ちにくい」という悩みに、"
                 "年齢とともに代謝やリンパの巡りが落ちること・部分的に溜まりやすいことを説明し、"
                 "当サロンの痩身が巡りと土台から整えるアプローチであることに繋げる。",
    },
    {
        "key": "konkyo", "label": "施術根拠（プラペン／幹細胞原液）",
        "angle": "「エステの“ハリが出る”は一時的では？」という疑問に答える形で、"
                 "プラペンが医療レベルの幹細胞原液を真皮層へ直接届け、肌が本来持つ再生の働きにアプローチする仕組みを説明する。"
                 "誇張せず、なぜ表面の化粧品と違うのかを丁寧に。",
    },
    {
        "key": "kishitsu", "label": "毛穴・肌質改善・ノーファンデ",
        "angle": "「毛穴の開きやくすみで、ファンデーションが手放せない」という悩みに、"
                 "肌質そのものを底上げする肌質改善プログラムの考え方で答える。"
                 "ノーファンデを目指せる肌へ、という前向きな温度感で。",
    },
    {
        "key": "henka", "label": "続けた変化・通う意味",
        "angle": "「一度のケアで変わるの？」という疑問に、肌も体も土台から変えるには続けることに意味がある、"
                 "という観点で答える。コースで段階的に整えていく考え方を、押し売りにならない範囲で。",
    },
    {
        "key": "kisetsu", "label": "季節の肌・体の悩み",
        "angle": "今の季節に多い肌・体の悩み（梅雨〜初夏なら、むくみ・皮脂崩れ・紫外線によるくすみ等）に触れ、"
                 "その時季にこそ整えておく意味を説明する。季節感は自然に、定型句にならないように。",
    },
]

# ── ベモーレの事実（AIOのエンティティ・正確性の土台。voiceルールは下のSYSTEMに集約）──
FACTS = """【ベモーレ 基本情報（投稿に織り込む正確な事実）】
- 店名：Beauty Salon Bemolle（ベモーレ）／痩身ダイエット&たるみ・肌質改善の専門サロン
- エリア：大阪市天王寺区上汐3-5-18 オブリオポルタ上町台901号室。谷町九丁目駅・大阪上本町駅から徒歩5分
- 営業：平日9:30〜18:00（日曜定休）。※夜営業・週末営業・お仕事帰り枠は無い
- 対象：たるみ・痩身・肌質改善に悩む 40〜50代を中心とした女性
- 主な施術：プラペン（医療レベルの幹細胞原液を真皮へ届ける）、全身痩身、肌質改善プログラム
- 予約・相談：LINE から受付"""

# ── voiceルール（プロンプトキャッシュで固定。AI臭排除・敬語・ベモーレらしさ）──
SYSTEM = """あなたは大阪の美容サロン「ベモーレ」のオーナーとして、Googleビジネスプロフィールの投稿文を書きます。
読み手は、たるみ・痩身・肌質改善に悩む40〜50代を中心とした女性です。

【最重要：AIO（AI検索／AI Overview）最適化】2026年の基準で、AIに引用されやすい文章にする：
1. 冒頭で、実際に検索・AIに聞かれる「悩みの質問」を一文で提示し、直後にその答え（理由・仕組み）を書く
2. 因果・機序を平易な日本語で説明する（例：真皮のコラーゲンが減る→土台がゆるむ→表面ケアでは届かない）。AIが抜き出しやすい因果文にする
3. エンティティを具体語で入れる：店名(ベモーレ)・地域(谷町九丁目／上本町／大阪)・施術名(プラペン等)・対象(40〜50代)。曖昧語で濁さない
4. 専門性が伝わる根拠を一つ入れる（E-E-A-T）
5. 末尾に、場所・営業時間・予約導線（LINE）を自然に添える

【文体ルール（厳守）】
- 敬語ベース。ただしガチガチの広告コピーにしない。人間が書いた温度のある文章に。AI臭・型・テンプレ感を排除する
- サロンの呼称は「ベモーレ」または「当サロン」。「うち」は使わない
- 「絶対」「必ず」「100%」など言い切り・過剰保証は使わない。効果を断定しない（「アプローチする」「目指せる」「選ばれています」等）
- 「あなた専用」「あなたに合わせて組む」等のカスタム/オーダーメイド表現は使わない（当サロンはカスタムメニュー提供なし）
- 営業は平日9:30〜18:00・日曜定休。「夜」「週末」「お仕事帰り」など営業時間に反する表現は禁止。必要なら「平日朝から夕方まで」と書く
- 3点リーダーを使うときは「…」を1つだけ。「……」と重ねない
- 絵文字は使っても1〜2個まで（多用しない）。記号で飾り立てない

【形式】
- 全体で日本語250〜350字程度
- 1行目は【】で囲んだ短い見出し（その回のテーマが一目で分かるもの）
- 段落は2〜3個。読みやすく改行する
- 最後の行は予約・相談の導線（「ご予約・ご相談はLINEから。平日9:30〜18:00に承っております（日曜定休）」のような形。毎回同じ言い回しにしすぎない）

出力は投稿本文のみ。前置き・解説・マークダウン記号・引用符は付けない。"""


# ── 状態ファイル ──────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 本文生成（Claude・AIO最適化）────────────────────────────────
def generate_text(theme: dict, recent_texts: list) -> str:
    today = datetime.now(JST)
    avoid = ""
    if recent_texts:
        joined = "\n---\n".join(recent_texts[-4:])
        avoid = f"\n\n【直近の投稿（書き出し・言い回しが被らないようにする）】\n{joined}"
    user = (
        f"{FACTS}\n\n"
        f"今週のテーマ：{theme['label']}\n"
        f"切り口：{theme['angle']}\n"
        f"今日の日付：{today.strftime('%Y年%-m月%-d日')}（この時季の悩みに触れてよい）"
        f"{avoid}\n\n"
        "上記テーマで、AIに引用されやすく、かつ人間味のあるGoogleビジネスプロフィール投稿を1本書いてください。"
    )
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=900,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


# ── LINE 配信（Claude通知Bot へ broadcast）──────────────────────
def send_line(messages: list) -> None:
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={"Authorization": f"Bearer {LINE_TOKEN}"},
        json={"messages": messages},
        timeout=15,
    )
    if not r.ok:
        raise Exception(f"LINE broadcast {r.status_code}: {r.text[:200]}")


def notify_error(msg: str) -> None:
    try:
        send_line([{"type": "text", "text": msg}])
    except Exception:
        pass


# ── メイン ────────────────────────────────────────────────────
def main() -> None:
    state = load_json(STATE_FILE, {"last_index": -1, "recent_texts": []})

    # テーマ輪番（直前と同じにならないよう次へ）
    idx = (state.get("last_index", -1) + 1) % len(THEMES)
    theme = THEMES[idx]
    print(f"今週のテーマ: {theme['label']}")

    # 本文生成
    body = generate_text(theme, state.get("recent_texts", []))
    print("=== 生成本文 ===\n" + body + "\n================")

    # LINE配信（①案内 ②コピー用の本文だけ）
    header = (
        "📍 今週のGoogleビジネスプロフィール投稿\n"
        f"テーマ：{theme['label']}\n\n"
        "下の本文をコピーして貼り付けて投稿してください。\n"
        "ボタンは「詳細」→ LINE予約URL がおすすめです。"
    )
    send_line([
        {"type": "text", "text": header},
        {"type": "text", "text": body},  # ← これだけをコピーすればOK
    ])
    print("LINE配信 完了")

    # 状態更新（テーマ位置・直近本文）
    recent = (state.get("recent_texts", []) + [body])[-6:]
    save_json(STATE_FILE, {"last_index": idx, "recent_texts": recent})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        notify_error(f"⚠️ GBP週次投稿の生成に失敗しました。\n{type(e).__name__}: {str(e)[:300]}")
        sys.exit(1)
