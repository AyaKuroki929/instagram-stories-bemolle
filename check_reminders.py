"""リマインダーの自己点検（CIで自動実行・人の目に頼らない安全網）

目的は3つ。どれか1つでも壊れると「設定したのに届かない」が黙って起きるため、
push のたびに機械で確かめる（2026-07-27 追加）。

  ① reminders.json の形式が正しいか（必須キー・type・date・slot・名前の重複）
  ② 登録した once リマインドが、その予定日・時間帯に本当に発火条件を満たすか
     （＝「登録したのに条件が合わず永遠に発火しない」を検出）
  ③ DRY_RUN が状態ファイルを書き換えないか
     （書き換えると、テストしただけで本番当日が「送信済み」になり黙って飛ぶ。実際に踏んだ）

使い方: python3 check_reminders.py   → 異常があれば exit 1
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date

import personal_reminder as pr

VALID_SLOTS = {"early", "morning", "evening19", "evening"}
VALID_TYPES = {"once", "monthly"}


def fail(msg: str, errors: list) -> None:
    print(f"❌ {msg}")
    errors.append(msg)


def main() -> int:
    errors: list = []
    reminders = json.load(open("reminders.json", encoding="utf-8"))

    # ── ① 形式チェック ──────────────────────────────
    seen = set()
    for r in reminders:
        name = r.get("name", "(名前なし)")
        if not r.get("name"):
            fail(f"name がない: {r}", errors)
        if name in seen:
            fail(f"name が重複（送信済み判定が混線する）: {name}", errors)
        seen.add(name)

        if not r.get("message"):
            fail(f"message がない: {name}", errors)

        t = r.get("type")
        if t not in VALID_TYPES:
            fail(f"type が不正（{t}）: {name}", errors)

        slot = r.get("slot", "morning")
        if slot not in VALID_SLOTS:
            fail(f"slot が不正（{slot}）: {name}", errors)

        if t == "once":
            try:
                date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                fail(f"date が不正: {name}", errors)
        elif t == "monthly":
            try:
                day = int(r["day"])
                if not 1 <= day <= 28:
                    fail(f"day は1〜28にする（29〜31は無い月に飛ばない）: {name}", errors)
            except (KeyError, ValueError):
                fail(f"day が不正: {name}", errors)

    # ── ② 予定日に本当に発火するか ────────────────────
    for r in reminders:
        if r.get("type") != "once" or "date" not in r:
            continue
        try:
            d = date.fromisoformat(r["date"])
        except ValueError:
            continue
        slot = r.get("slot", "morning")
        if not pr._is_due(r, d, slot):
            fail(f"予定日に発火しない設定になっている: {r['name']}（{d} / {slot}）", errors)
        # 指定外スロットで誤爆しないことも確認
        for other in VALID_SLOTS - {slot}:
            if pr._is_due(r, d, other):
                fail(f"指定外の時間帯にも発火してしまう: {r['name']}（{other}）", errors)

    # ── ③ DRY_RUN が状態ファイルを汚さないか ──────────
    with tempfile.TemporaryDirectory() as tmp:
        for f in ("reminders.json", "reminder_state.json", "personal_reminder.py"):
            if os.path.exists(f):
                shutil.copy(f, tmp)
        before = open(os.path.join(tmp, "reminder_state.json"), encoding="utf-8").read() \
            if os.path.exists(os.path.join(tmp, "reminder_state.json")) else ""

        targets = [(r["date"], r.get("slot", "morning"))
                   for r in reminders if r.get("type") == "once" and "date" in r]
        for d, slot in targets[:5]:  # 先頭5件で十分（全件回さなくても書き込みの有無は分かる）
            subprocess.run(
                [sys.executable, "personal_reminder.py"],
                cwd=tmp,
                env={**os.environ, "DRY_RUN": "1",
                     "REMINDER_DATE_OVERRIDE": d, "REMINDER_SLOT_OVERRIDE": slot},
                capture_output=True, check=False,
            )
        after = open(os.path.join(tmp, "reminder_state.json"), encoding="utf-8").read() \
            if os.path.exists(os.path.join(tmp, "reminder_state.json")) else ""
        if before != after:
            fail("DRY_RUN が reminder_state.json を書き換えている"
                 "（テストしただけで本番が『送信済み』になり黙って飛ぶ）", errors)

    if errors:
        print(f"\n🚨 {len(errors)}件の問題があります。直すまでリマインドは信用できません。")
        return 1

    once = sum(1 for r in reminders if r.get("type") == "once")
    ev = sum(1 for r in reminders if r.get("slot") in ("evening19", "evening"))
    print(f"✅ リマインダー自己点検OK（全{len(reminders)}件／単発{once}件／夜スロット{ev}件）")
    print("   形式・予定日の発火条件・DRY_RUNの非破壊性 を確認しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
