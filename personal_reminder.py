"""個人リマインダー（Claude通知Bot → 彩さんのLINE）

reminders.json の予定を毎朝チェックし、当日分だけ Claude通知Bot の broadcast で送る。
（Claude通知Botは管理者専用＝友だちは彩さんのみ。お客様には届かない）

reminders.json のエントリ形式:
  {"name": "...", "type": "monthly", "day": 27,           "message": "..."}  # 毎月day日
  {"name": "...", "type": "once",    "date": "YYYY-MM-DD", "message": "..."}  # 1回だけ

  任意キー "slot"（送る時間帯）:
    "morning"（既定・省略時）… 9:00 JST に送る
    "evening"               … 20:00 JST に送る
  彩さんは朝9時は準備中で携帯を触れないため、夜に受け取りたいものは "slot": "evening" を付ける。

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
    """今どちらの時間帯の実行か。朝cron(9:00/9:40 JST)→morning、夜cron(20:00/20:40 JST)→evening。"""
    override = os.environ.get("REMINDER_SLOT_OVERRIDE", "")
    if override:
        return override
    return "evening" if datetime.now(JST).hour >= 12 else "morning"


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
    """日付を過ぎたのに一度も送られていない once リマインドを拾う。
    「設定したのに届かなかった」を沈黙させないための取りこぼし検知（2026-07-27追加）。
    過去にCronCreate（セッション内保持）で作ったリマインドが誰にも気づかれず消えた事故の再発防止。"""
    out = []
    for r in reminders:
        if r.get("type") != "once":
            continue
        try:
            d = date.fromisoformat(r["date"])
        except (KeyError, ValueError):
            continue
        if d < today and r["name"] not in sent_log and f"MISSED:{r['name']}" not in sent_log:
            out.append((r, d))
    return out


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

    # 取りこぼし検知：期日を過ぎたのに一度も送られていないものがあれば警告を送る
    missed = _missed(reminders, today, sent_log)
    if missed:
        names = "\n".join(f"・{r['name']}（{d} 予定）" for r, d in missed)
        alert = ("🚨 リマインドの取りこぼしを検知しました\n\n"
                 f"下記は予定日を過ぎましたが、一度も送信されていません。\n\n{names}\n\n"
                 "内容を確認して、必要なら今すぐ対応してください。")
        if dry_run:
            print(f"[DRY_RUN] 取りこぼし警告:\n{alert}")
        else:
            from story.util import line_broadcast
            if not line_broadcast(alert):
                sys.exit("LINE送信失敗: 取りこぼし警告")
            for r, _ in missed:
                sent_log[f"MISSED:{r['name']}"] = today.isoformat()
            print(f"取りこぼし警告を送信: {len(missed)}件")

    due = [
        r for r in reminders
        if _is_due(r, today, now_slot) and sent_log.get(r["name"]) != today.isoformat()
    ]
    if not due:
        print(f"{today}({now_slot}): 送信対象なし")
        if missed and not dry_run:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.write("\n")
        return
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

    if dry_run:
        print("[DRY_RUN] 状態ファイルは更新しません")
        return

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
