#!/usr/bin/env python3
"""GAS デプロイ版ズレ監視。

「コードは保存したが再デプロイしていない → 本番が古いコードのまま動く」型の沈黙障害
（2026-08-01 うらかたStripe通知の二重送信の真因）を毎朝機械で捕まえる。

各監視対象の「本番デプロイ(固定ID)」が配信しているバージョンと、そのGASの最新バージョンを
clasp 経由で突き合わせ、本番が最新より古ければ Claude通知Bot へ LINE。

- 正常時は何も送らない（LINE枠を無駄にしない。異常時だけ鳴る）
- @HEAD 運用のGAS(フォーム類)は常に最新配信=ズレ不能なので監視不要 → WATCH に入れない
- 監視対象を増やすときは WATCH に {name, scriptId, live(本番デプロイID)} を1行足すだけ
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

# 本番が「バージョン固定デプロイ」のGASだけ（@HEADのフォーム類はズレ不能なので対象外）
WATCH = [
    {
        "name": "うらかたStripe通知(お金)",
        "scriptId": "1XPG_YDVUF65FXdaGrNFuVQ9Kw-TbOirN2BzikD77Iztc6HsBiacAtpTx",
        "live": "AKfycbxdv92wlyoTARb2UeyTDIpVAn0BVPAzz66svRKGiGHKcQQkI7vBGIwNHkptF21EuTWt",  # @28
    },
    {
        "name": "タイムカード(給料)",
        "scriptId": "1-APprsyopIdDcLDrd59DvL1WDH-C49wd70THeYB-JbOs0r1YsAoZfkX6",
        "live": "AKfycbw9SmXqYA0i_Vysp4l0ZbRcNjvTs0miOm-CFVcF9kWLGm-VZ43sqo9FWT0rpgf0PJq4kA",  # @20
    },
]


def _clasp(script_id: str, sub: str) -> str:
    """一時 .clasp.json を作って clasp サブコマンドを実行し stdout+stderr を返す。"""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, ".clasp.json"), "w") as f:
        json.dump({"scriptId": script_id, "rootDir": ""}, f)
    r = subprocess.run(["clasp", sub], cwd=d, capture_output=True, text=True, timeout=90)
    return r.stdout + r.stderr


def served_version(script_id: str, live_id: str):
    """本番デプロイが今配信しているバージョン。@HEAD なら 'HEAD'、見つからなければ None。"""
    out = _clasp(script_id, "deployments")
    for line in out.splitlines():
        if live_id in line:
            if "@HEAD" in line:
                return "HEAD"
            m = re.search(r"@(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def latest_version(script_id: str):
    out = _clasp(script_id, "versions")
    vers = [int(m.group(1)) for m in re.finditer(r"^(\d+)\s", out, re.M)]
    return max(vers) if vers else None


def check():
    problems = []
    for w in WATCH:
        served = served_version(w["scriptId"], w["live"])
        latest = latest_version(w["scriptId"])
        if served is None:
            problems.append(
                f"❌ {w['name']}：本番デプロイ({w['live'][:12]}…)が見つからない。"
                f"誰かが消した / IDが変わった疑い。すぐ確認を。"
            )
        elif served == "HEAD":
            continue  # 常に最新
        elif latest is not None and served < latest:
            problems.append(
                f"⚠️ {w['name']}：本番が v{served} のまま動いています（最新は v{latest}）。"
                f"コードを保存したのに再デプロイし忘れ＝古いコードが本番稼働中の可能性。"
            )
    return problems


def notify(problems):
    text = (
        "🚨 GASデプロイ版ズレ検知\n\n"
        + "\n\n".join(problems)
        + "\n\n直し方：該当GASを最新版で再デプロイ"
        "（clasp deploy -i <本番ID> ／ エディタで『デプロイを管理』→鉛筆→バージョン=最新→デプロイ）。"
    )
    print(text)
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN 未設定 → 送信スキップ", file=sys.stderr)
        return
    if os.environ.get("X_DRY_RUN") == "1":
        print("X_DRY_RUN=1 → 送信スキップ（テスト）", file=sys.stderr)
        return
    body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        print("LINE送信:", r.status)


def main():
    problems = check()
    if problems:
        notify(problems)
        # ドリフト通知は送信済み。ここで exit 1 すると failure() 汎用通知と二重送信になるため 0 で抜ける。
        # （clasp認証切れ等の"監視自体の故障"は例外→非0→workflowの failure() が別に鳴らす）
        print("ズレを検知しLINE送信しました。", file=sys.stderr)
        return
    print("✅ 全GASの本番デプロイ＝最新。ズレなし。")


if __name__ == "__main__":
    main()
