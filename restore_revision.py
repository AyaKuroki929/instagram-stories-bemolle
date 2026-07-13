"""Google Driveのファイルを過去の版（リビジョン）に復元する汎用ツール

指定した名前のファイルをDriveで検索し、最も古い版（または指定index）の
中身をダウンロードして、現行版として書き戻す。
本人制作のBA動画などを誤って上書きした際の復旧用。

環境変数: GOOGLE_REFRESH_TOKEN / GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
入力: RESTORE_FILENAME（対象ファイル名・完全一致）
      RESTORE_REVISION_INDEX（省略時 0 = 最古の版）
"""
from __future__ import annotations

import os
import sys

import requests

FILENAME = os.environ["RESTORE_FILENAME"]
_idx_raw = os.environ.get("RESTORE_REVISION_INDEX", "").strip()
REV_INDEX = int(_idx_raw) if _idx_raw else None  # None=最古のまともな版を自動選択


def token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "refresh_token",
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> None:
    h = {"Authorization": f"Bearer {token()}"}

    # 1. ファイルを検索
    q = FILENAME.replace("'", "\\'")
    r = requests.get("https://www.googleapis.com/drive/v3/files", headers=h, params={
        "q": f"name='{q}' and trashed=false",
        "fields": "files(id,name,size,parents,mimeType)",
    }, timeout=15)
    r.raise_for_status()
    files = r.json().get("files", [])
    if not files:
        raise Exception(f"ファイルが見つかりません: {FILENAME}")
    if len(files) > 1:
        print(f"⚠️ 同名ファイルが{len(files)}件。先頭を使用: {files[0]['id']}")
    fid = files[0]["id"]
    mime = files[0].get("mimeType") or "application/octet-stream"
    print(f"対象: {FILENAME} (id={fid}, 現行サイズ={files[0].get('size','?')}, mime={mime})")

    # 2. 版の一覧
    r = requests.get(f"https://www.googleapis.com/drive/v3/files/{fid}/revisions",
                     headers=h, params={"fields": "revisions(id,modifiedTime,size)"}, timeout=15)
    r.raise_for_status()
    revs = r.json().get("revisions", [])
    print("版一覧:")
    for i, rv in enumerate(revs):
        print(f"  [{i}] {rv['modifiedTime']} size={rv.get('size','?')}")
    if len(revs) < 2:
        raise Exception("復元できる旧版がありません")
    # Drive同期はアップロード開始時に数十バイトのプレースホルダー版を作る。
    # 「最古の版」を機械的に選ぶとこのゴミを掴んで実データを48バイトで上書きする
    # 事故が起きた（2026-07-13 C0342/C0343）。1KB未満の版は選択対象から除外する。
    MIN_REAL_SIZE = 1024
    if REV_INDEX is None:
        # Drive同期は1回のアップロードで「48バイトのプレースホルダー→部分チャンク→完成形」
        # と複数の版を残す（部分チャンクはmoov欠落で再生不能）。よって「最初のアップロード
        # バースト（先頭の版から10分以内）のうち最大サイズの版」を原本として選ぶ。
        from datetime import datetime, timedelta
        def ts(rv):
            return datetime.fromisoformat(rv["modifiedTime"].replace("Z", "+00:00"))
        first_time = ts(revs[0])
        burst = [i for i, rv in enumerate(revs) if ts(rv) - first_time <= timedelta(minutes=10)]
        real = [i for i in burst if int(revs[i].get("size", "0") or 0) >= MIN_REAL_SIZE]
        if not real:
            raise Exception("最初のアップロードバーストにまともな版がありません")
        idx = max(real, key=lambda i: int(revs[i].get("size", "0") or 0))
        print(f"→ index未指定のため、最初のアップロードバースト内で最大の版[{idx}]を自動選択")
    else:
        idx = REV_INDEX
    target = revs[idx]
    print(f"→ 版[{idx}]（{target['modifiedTime']}）を復元します")

    # 3. 旧版をダウンロード
    r = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{fid}/revisions/{target['id']}",
        headers=h, params={"alt": "media"}, timeout=300)
    r.raise_for_status()
    data = r.content
    print(f"旧版取得: {len(data)//1024}KB")
    if len(data) < MIN_REAL_SIZE:
        raise Exception(
            f"取得した版が{len(data)}バイトしかありません（同期プレースホルダーの可能性）。"
            "書き戻しを中止しました。版一覧からsizeの大きい版のindexを指定してください")

    # 4. 現行版として書き戻し
    r = requests.patch(
        f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media",
        headers={**h, "Content-Type": mime}, data=data, timeout=600)  # 実ファイルの形式を使う（mp4固定だと画像等の復元でmimeが壊れる）
    if not r.ok:
        raise Exception(f"書き戻し失敗: {r.status_code} {r.text[:300]}")
    print(f"✅ 復元完了: {FILENAME} を {target['modifiedTime']} の版に戻しました")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
