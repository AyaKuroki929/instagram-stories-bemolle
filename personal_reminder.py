"""個人リマインダー（Claude通知Bot → 彩さんのLINE）

reminders.json の予定を毎朝チェックし、当日分だけ Claude通知Bot の broadcast で送る。
（Claude通知Botは管理者専用＝友だちは彩さんのみ。お客様には届かない）

reminders.json のエントリ形式:
  {"name": "...", "type": "monthly", "day": 27,           "message": "..."}  # 毎月day日
  {"name": "...", "type": "once",    "date": "YYYY-MM-DD", "message": "..."}  # 1回だけ

  任意キー "slot"（送る時間帯）:
    "early"                 … 7:00 JST に送る（プラン変更・解約などその日のうちに人へ依頼する系）
    "morning"（既定・省略時）… 9:00 JST に送る
    "evening"               … 20:00 JST に送る
  当日中に誰かへ依頼する作業は "early"（20時では遅い・2026-07-28本人指示）。
  夜に受け取りたいものは "evening"。

送信済みは reminder_state.json の {"last_sent": {"<name>": "YYYY-MM-DD"}} で管理。
保険cronで同日に2回発火しても重複送信しない。

環境変数:
  LINE_CHANNEL_ACCESS_TOKEN … Claude通知Botのトークン（送信時のみ必須）
  DRY_RUN=1                 … 送信せずログだけ（LINE枠を消費しない）
  REMINDER_DATE_OVERRIDE    … テスト用に「今日」を YYYY-MM-DD で上書き
  REMINDER_SLOT_OVERRIDE    … テスト用に「今の時間帯」を morning/evening で上書き
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

STATE_PATH = "reminder_state.json"


def _today() -> date:
    override = os.environ.get("REMINDER_DATE_OVERRIDE", "")
    if override:
        return date.fromisoformat(override)
    return datetime.now(JST).date()


def _now_slot() -> str:
    """今どの時間帯の実行か。early=7:00/7:40 → morning=9:00/9:40 → evening19=19:00/19:40 → evening=20:00/20:40 (JST)。
    境界はcron遅延に耐えるよう広めに取る（8時台まではearly扱い・16時台まではmorning扱い・19時台まではevening19扱い）。
    それ以上ずれた場合はその回は送られず、翌日の取りこぼし検知🚨が拾う。"""
    override = os.environ.get("REMINDER_SLOT_OVERRIDE", "")
    if override:
        return override
    h = datetime.now(JST).hour
    if h < 9:
        return "early"
    if h < 17:
        return "morning"
    if h < 20:
        return "evening19"
    return "evening"


def _is_due(reminder: dict, today: date, now_slot: str) -> bool:
    # 時間帯が違えば送らない（slot未指定は従来どおり朝）
    if reminder.get("slot", "morning") != now_slot:
        return False
    if reminder.get("type") == "monthly":
        return today.day == int(reminder["day"])
    if reminder.get("type") == "once":
        return today.isoformat() == reminder["date"]
    print(f"[reminder] 不明なtype: {reminder}", file=sys.stderr)
    return False


def _missed(reminders: list, today: date, sent_log: dict) -> list:
    """予定日を過ぎたのに送られていないリマインドを拾う（once・monthly両対応）。
    「設定したのに届かなかった」を沈黙させないための取りこぼし検知（2026-07-27追加）。
    過去にCronCreate（セッション内保持）で作ったリマインドが誰にも気づかれず消えた事故の再発防止。
    戻り値: [(reminder, 予定日, 警告済みマーカーのキー), ...]"""
    out = []
    for r in reminders:
        t = r.get("type")
        if t == "once":
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                continue
            marker = f"MISSED:{r['name']}"
            if d < today and r["name"] not in sent_log and marker not in sent_log:
                out.append((r, d, marker))
        elif t == "monthly":
            # 今月の予定日を過ぎたのに、今月分が送られていない（例：公庫27日が両cron全滅）
            try:
                exp = date(today.year, today.month, int(r["day"]))
            except (KeyError, ValueError):
                continue
            marker = f"MISSED:{r['name']}:{exp.isoformat()}"
            if exp < today and sent_log.get(r["name"]) != exp.isoformat() and marker not in sent_log:
                out.append((r, exp, marker))
    return out


def _quota_warning(today: date, sent_log: dict):
    """LINE配信枠の消費が80%を超えたら警告文を返す（月1回だけ）。
    枠を使い切ると、リマインド本体も失敗通知も全部沈黙するため、その手前で知らせる（2026-07-27追加）。
    チェック自体の失敗でリマインドを止めない（None返しで続行）。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return None
    marker = f"QUOTA_WARNED:{today.strftime('%Y-%m')}"
    if marker in sent_log:
        return None
    try:
        import requests
        h = {"Authorization": f"Bearer {token}"}
        q = requests.get("https://api.line.me/v2/bot/message/quota", headers=h, timeout=10).json()
        c = requests.get("https://api.line.me/v2/bot/message/quota/consumption", headers=h, timeout=10).json()
        if q.get("type") != "limited":
            return None
        limit, used = int(q["value"]), int(c["totalUsage"])
        if limit > 0 and used / limit >= 0.8:
            text = (f"⚠️ Claude通知BotのLINE配信枠が残りわずかです（{used}/{limit}通・{used * 100 // limit}%）\n\n"
                    "枠を使い切ると、リマインドも失敗通知も一切届かなくなります。\n"
                    "枠は月初にリセットされます。\n"
                    "Claudeに「LINE通知の消費を見直して」と伝えてください。")
            return text, marker
    except Exception as e:
        print(f"[reminder] 配信枠チェック失敗（リマインドは続行）: {e}", file=sys.stderr)
    return None


def main() -> None:
    today = _today()
    reminders = json.load(open("reminders.json", encoding="utf-8"))

    try:
        state = json.load(open(STATE_PATH, encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    sent_log: dict = state.setdefault("last_sent", {})

    now_slot = _now_slot()
    dry_run = os.environ.get("DRY_RUN") == "1"

    # 取りこぼし検知：期日を過ぎたのに送られていないものがあれば警告を送る
    missed = _missed(reminders, today, sent_log)
    if missed:
        names = "\n".join(f"・{r['name']}（{d} 予定）" for r, d, _ in missed)
        alert = ("🚨 リマインドの取りこぼしを検知しました\n\n"
                 f"下記は予定日を過ぎましたが、送信されていません。\n\n{names}\n\n"
                 "内容を確認して、必要なら今すぐ対応してください。")
        if dry_run:
            print(f"[DRY_RUN] 取りこぼし警告:\n{alert}")
        else:
            from story.util import line_broadcast
            if not line_broadcast(alert):
                sys.exit("LINE送信失敗: 取りこぼし警告")
            for _, _, marker in missed:
                sent_log[marker] = today.isoformat()
            print(f"取りこぼし警告を送信: {len(missed)}件")

    dirty = bool(missed) and not dry_run  # 取りこぼし警告のマーカーを書いたら保存が必要

    due = [
        r for r in reminders
        if _is_due(r, today, now_slot) and sent_log.get(r["name"]) != today.isoformat()
    ]
    if not due:
        print(f"{today}({now_slot}): 送信対象なし")
    for r in due:
        if dry_run:
            # ⚠️ DRY_RUNでは絶対に送信済みを記録しない。
            # （記録すると本番当日に「送信済み」と判定され、リマインドが黙って飛ぶ。2026-07-27に実際に踏んだ）
            print(f"[DRY_RUN] {r['name']}: {r['message'].splitlines()[0]} …")
            continue
        from story.util import line_broadcast
        if not line_broadcast(r["message"]):
            # 失敗はexit 1でworkflowをfailさせ、failure()の🚨通知に任せる
            sys.exit(f"LINE送信失敗: {r['name']}")
        print(f"送信: {r['name']}")
        sent_log[r["name"]] = today.isoformat()
        dirty = True

    # 配信枠の残量チェック（80%超で月1回警告。枠切れ＝全通知が沈黙、の手前で知らせる）
    quota = _quota_warning(today, sent_log)
    if quota:
        text, marker = quota
        if dry_run:
            print(f"[DRY_RUN] 配信枠警告:\n{text}")
        else:
            from story.util import line_broadcast
            if line_broadcast(text):
                sent_log[marker] = today.isoformat()
                dirty = True
                print("配信枠警告を送信")

    if dry_run:
        print("[DRY_RUN] 状態ファイルは更新しません")
        return

    if dirty:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
