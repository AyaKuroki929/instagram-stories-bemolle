#!/usr/bin/env python3
"""週1回（日曜）Googleビジネスプロフィール用の AIO 最適化投稿を生成し、
LINE（Claude通知Bot）へ「貼るだけ」の形で配信する（テキストのみ／画像なし）。

GBP API のアクセス申請は3回却下（申請アカウントのリスティングwebsite不一致が真因）。
そのため自動投稿はせず、人がGBPアプリに貼る半自動方式。規約リスクゼロ・追加課金ゼロ。
LINE broadcast の仕組みは instagram-stories-bemolle から流用（本体 post_story.py は不変更）。
"""
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

# 共通基盤（story/util.py）: JSON状態・Claude呼び出し・LINE配信を共用
# ※ story/config.py はimportしない（このスクリプトはMeta等のenvを持たないため）
from story.util import claude_text, line_broadcast, load_json, save_json

JST = timezone(timedelta(hours=9))

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
LINE_TOKEN    = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
# Make.com Webhook（設定されていればGBPへ自動投稿・未設定なら従来のLINE貼るだけ運用）
MAKE_GBP_WEBHOOK = os.environ.get("MAKE_GBP_WEBHOOK", "")

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

# ── FAQ輪番（通常テーマと交互に投稿）─────────────────────────────
# GoogleのQ&A機能は2025-12から段階廃止され、後継のAsk Maps/GeminiはGBP投稿・
# プロフィール・クチコミを読んで回答する。そのためFAQは「投稿」として発信する。
FAQS = [
    {
        "key": "faq_shokuji", "label": "FAQ：食事制限は必要？",
        "angle": "「無理な食事制限は必要ですか？」というよくある質問に答える投稿。"
                 "回答の核：不要。ベモーレは無理な食事制限に頼らず体質から整えることを大切にしていて、"
                 "我慢中心のダイエットが続かなかった方にも通われている。見出しは質問文をそのまま使ってよい。",
    },
    {
        "key": "faq_hajimete", "label": "FAQ：エステが初めてで不安",
        "angle": "「エステは初めてで不安です。大丈夫ですか？」というよくある質問に答える投稿。"
                 "回答の核：初めての方も多い。完全個室で丁寧なカウンセリングから始める。無理な勧誘はしない。",
    },
    {
        "key": "faq_ryoho", "label": "FAQ：痩身とフェイシャル両方できる？",
        "angle": "「痩身とフェイシャル、両方できますか？」というよくある質問に答える投稿。"
                 "回答の核：両方が主力。痩身（全身・下半身・お腹・むくみ）と、たるみ改善・肌質改善のフェイシャル。"
                 "ボディもお顔も一緒にケアしたい方に向いている。",
    },
    {
        "key": "faq_nendai", "label": "FAQ：40代・50代でも通える？",
        "angle": "「40代・50代でも通えますか？」というよくある質問に答える投稿。"
                 "回答の核：40〜50代が中心客層。年齢とともに変化しにくいと感じている方に向き合うメニューがある。",
    },
    {
        "key": "faq_kekka", "label": "FAQ：他で結果が出なかった",
        "angle": "「大手サロンやパーソナルジムで結果が出ませんでした。それでも大丈夫？」というよくある質問に答える投稿。"
                 "回答の核：そうした経験のある方が多く通っている。一人ひとりの体質やライフスタイルに合わせて丁寧に提案する。",
    },
    {
        "key": "faq_dansei", "label": "FAQ：男性も通える？",
        "angle": "「男性も通えますか？」というよくある質問に答える投稿。"
                 "回答の核：女性のお客様が中心のプライベートサロンのため、男性はご紹介のみ承っている。",
    },
    {
        "key": "faq_yoyaku", "label": "FAQ：予約は必要？",
        "angle": "「予約は必要ですか？当日予約はできますか？」というよくある質問に答える投稿。"
                 "回答の核：完全予約制。LINEまたはホットペッパービューティーから。空き状況により当日案内が可能な場合もある。",
    },
    {
        "key": "faq_access", "label": "FAQ：場所・アクセス",
        "angle": "「場所・アクセスを教えてください」というよくある質問に答える投稿。"
                 "回答の核：大阪市天王寺区、谷町九丁目駅・大阪上本町駅から徒歩約5分。"
                 "オブリオポルタ上町台901号室の完全個室プライベートサロン。",
    },
    {
        "key": "faq_shiharai", "label": "FAQ：支払い方法",
        "angle": "「支払い方法は何がありますか？」というよくある質問に答える投稿。"
                 "回答の核：現金・クレジットカード（一括払い・分割払い）・クレジットローン・口座振込に対応。"
                 "※PayPay等のQR決済には触れない。",
    },
    {
        "key": "faq_pace", "label": "FAQ：通うペース",
        "angle": "「どれくらいのペースで通えばいいですか？」というよくある質問に答える投稿。"
                 "回答の核：お悩みや目標に合わせて提案する。カウンセリングで無理のないペースを一緒に決める。",
    },
]

# ── ベモーレの事実（AIOのエンティティ・正確性の土台。voiceルールは下のSYSTEMに集約）──
FACTS = """【ベモーレ 基本情報（投稿に織り込む正確な事実）】
- 店名：Beauty Salon Bemolle（ベモーレ）／痩身ダイエット&たるみ・肌質改善の専門サロン
- エリア：大阪市天王寺区上汐3-5-18 オブリオポルタ上町台901号室。谷町九丁目駅・大阪上本町駅から徒歩5分
- 営業：平日9:30〜18:00（日曜定休）。※夜営業・週末営業・お仕事帰り枠は無い
- 対象：たるみ・痩身・肌質改善に悩む 40〜50代を中心とした女性。男性はご紹介のみ
- 主な施術：プラペン（医療レベルの幹細胞原液を真皮へ届ける）、全身痩身、肌質改善プログラム。ブライダル痩身にも対応
- 環境：完全個室のプライベートサロン・完全予約制。無理な勧誘はしない
- 予約・相談：LINE から受付（ホットペッパービューティーからも予約可）
- 支払い：現金・クレジットカード（一括・分割）・クレジットローン・口座振込"""

# ── voiceルール（プロンプトキャッシュで固定。AI臭排除・敬語・ベモーレらしさ）──
SYSTEM = """あなたは大阪の美容サロン「ベモーレ」のオーナーとして、Googleビジネスプロフィールの投稿文を書きます。
読み手は、たるみ・痩身・肌質改善に悩む40〜50代を中心とした女性です。

【最重要：AIO（AI検索／AI Overview）最適化】2026年の基準で、AIに引用されやすい文章にする。
狙いは「近隣の人が『谷町九丁目 痩身サロン』『上本町 たるみ』『天王寺 フェイシャル』のように
"エリア×悩み/カテゴリ"で検索・AIに質問したとき、AIがベモーレを推薦・引用すること」：
1. 冒頭で、実際に検索・AIに聞かれる「悩みの質問」を一文で提示し、直後にその答え（理由・仕組み）を書く
2. 因果・機序を平易な日本語で説明する（例：真皮のコラーゲンが減る→土台がゆるむ→表面ケアでは届かない）。AIが抜き出しやすい因果文にする
3. エンティティを具体語で入れる：店名(ベモーレ)・地域(谷町九丁目／上本町／天王寺区／大阪)・施術名(プラペン等)・対象(40〜50代)。曖昧語で濁さない
4. カテゴリ語を1つは自然に含める：「痩身サロン」「フェイシャルサロン」「たるみケア」「肌質改善」など、
   人がAIに聞くときのカテゴリ名。ベモーレが"どのカテゴリの店か"をAIが確実に紐付けられるようにする（ただし羅列・キーワード詰め込みは禁止・自然な文中に1つ）
5. 専門性が伝わる根拠を一つ入れる（E-E-A-T）
6. 末尾に、場所・営業時間・予約導線（LINE）を自然に添える

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
    return claude_text(
        model=MODEL,
        max_tokens=900,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        api_key=ANTHROPIC_KEY,
    ).strip()


# ── Make.com経由のGBP自動投稿 ──────────────────────────────────
def post_via_make(body: str) -> bool:
    """MakeのWebhookに本文を送り、Make側のGBP「Create a Post」で自動投稿する。
    Makeが承認済みGBP API権限を持つため、こちらでのAPI申請は不要。
    Webhook未設定・失敗時は False（→従来のLINE貼るだけ運用にフォールバック）。"""
    if not MAKE_GBP_WEBHOOK:
        return False
    import requests
    try:
        r = requests.post(MAKE_GBP_WEBHOOK, json={"summary": body}, timeout=20)
        if r.ok and r.text.strip().lower().startswith("accepted"):
            print("Make Webhook 送信OK → GBP自動投稿へ")
            return True
        print(f"Make Webhook 応答異常: {r.status_code} {r.text[:100]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Make Webhook 送信失敗: {e}", file=sys.stderr)
        return False


# ── LINE 配信（Claude通知Bot へ broadcast・実装は story/util.py）─
def send_line(messages: list) -> None:
    line_broadcast(messages, token=LINE_TOKEN, raise_on_error=True)


def notify_error(msg: str) -> None:
    line_broadcast(msg, token=LINE_TOKEN)  # 失敗してもログのみ（通知の失敗で落とさない）


# ── メイン ────────────────────────────────────────────────────
def main() -> None:
    state = load_json(STATE_FILE, {"last_index": -1, "recent_texts": []})

    # 同日二重実行ガード: schedule と repository_dispatch の両方が同日に発火したり
    # まれな二重発火があると、同じ日に2本投稿されテーマ輪番も2つ進んでしまう。
    # 手動実行(workflow_dispatch等)で意図的に追加したい時は GBP_FORCE=1 で通す。
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    if state.get("last_date") == today_str and not os.environ.get("GBP_FORCE"):
        print(f"本日({today_str})は投稿済み → スキップ（二重実行ガード）")
        return

    # 通常テーマとFAQを交互に投稿（Q&A機能廃止の代替：FAQを投稿としてAIに読ませる）
    is_faq = not state.get("last_was_faq", False)
    idx = state.get("last_index", -1)
    fidx = state.get("last_faq_index", -1)
    if is_faq:
        fidx = (fidx + 1) % len(FAQS)
        theme = FAQS[fidx]
        print(f"今回の投稿: {theme['label']}（FAQ {fidx + 1}/{len(FAQS)}）")
    else:
        idx = (idx + 1) % len(THEMES)
        theme = THEMES[idx]
        print(f"今週のテーマ: {theme['label']}")

    # 本文生成
    body = generate_text(theme, state.get("recent_texts", []))
    print("=== 生成本文 ===\n" + body + "\n================")

    # GBP自動投稿（Make Webhook設定済みなら）→ 失敗時は従来のLINE貼るだけ運用
    auto_posted = post_via_make(body)

    # LINE通知は「失敗時（＝手動対応が必要な時）だけ」送る運用。
    # Make経由で自動投稿できた場合は成功通知を出さない（通知は本当に対応が要る時だけ届く）。
    sanity_month = state.get("sanity_month", "")
    if auto_posted:
        print("Make経由でGBP自動投稿 完了（成功通知は送らない設定）")
        # Makeの「accepted」は受理確認にすぎず、GBPへの実投稿成功の保証ではない
        # （実投稿結果のAPIはMake側にしか無く、こちらから照会できない）。
        # Make側のトークン失効等で沈黙したまま輪番だけ進む事故を防ぐため、
        # 月1回だけ実掲載の目視確認をLINEで依頼する（枠消費は月1通のみ）
        if sanity_month != today_str[:7]:
            try:
                send_line([{
                    "type": "text",
                    "text": (
                        "📋 GBP自動投稿の月1確認\n"
                        "今月も自動投稿が動いています。ただしMakeの「受理」は掲載成功の保証ではないので、"
                        "月に一度だけ実物を確認してください。\n\n"
                        "Googleマップで「ベモーレ」→ プロフィールの「最新情報」に"
                        "直近1週間の投稿があればOKです。\n"
                        f"（今日の投稿テーマ：{theme['label']}）\n"
                        "※この確認依頼は月1回だけ届きます"
                    ),
                }])
                sanity_month = today_str[:7]
            except Exception as e:
                print(f"月1確認通知の送信失敗（投稿処理自体は完了）: {e}", file=sys.stderr)
    else:
        # 自動投稿できなかった＝人が貼り付ける必要がある → この時だけLINEに送る。
        # 注意: タイムアウト/応答喪失ではMake側は受理済み＝投稿される可能性があるため、
        # 貼る前にGBPを確認してもらう（二重投稿防止）。
        header = (
            "⚠️ GBP自動投稿ができませんでした（手動対応が必要）\n"
            f"テーマ：{theme['label']}\n\n"
            "【重要】通信タイムアウトの場合はMake側で投稿済みの可能性があります。\n"
            "先にGBPに今日の投稿が無いか確認し、無ければ下の本文を貼り付けてください。\n"
            "ボタンは「詳細」→ LINE予約URL がおすすめです。"
        )
        send_line([
            {"type": "text", "text": header},
            {"type": "text", "text": body},  # 貼り付け用
        ])
        print("LINE配信 完了（自動投稿失敗の手動フォールバック案内）")

    # 状態更新（テーマ位置・直近本文・投稿日）
    # 投稿は既に完了しているため、ここで失敗しても「生成に失敗」と誤解される通知を出さない
    try:
        recent = (state.get("recent_texts", []) + [body])[-6:]
        save_json(STATE_FILE, {
            "last_index": idx,
            "last_faq_index": fidx,
            "last_was_faq": is_faq,
            "recent_texts": recent,
            "last_date": today_str,
            "sanity_month": sanity_month,
        })
    except Exception as e:
        print(f"状態保存に失敗（投稿自体は完了済み）: {e}", file=sys.stderr)
        notify_error(f"⚠️ GBP投稿は完了しましたが状態保存に失敗しました（次回同テーマが再投稿される可能性）。\n{type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        notify_error(f"⚠️ GBP週次投稿の生成に失敗しました。\n{type(e).__name__}: {str(e)[:300]}")
        sys.exit(1)
