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
REV_INDEX = int(os.environ.get("RESTORE_REVISION_INDEX", "0"))


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
        "fields": "files(id,name,size,parents)",
    }, timeout=15)
    r.raise_for_status()
    files = r.json().get("files", [])
    if not files:
        raise Exception(f"ファイルが見つかりません: {FILENAME}")
    if len(files) > 1:
        print(f"⚠️ 同名ファイルが{len(files)}件。先頭を使用: {files[0]['id']}")
    fid = files[0]["id"]
    print(f"対象: {FILENAME} (id={fid}, 現行サイズ={files[0].get('size','?')})")

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
    target = revs[REV_INDEX]
    print(f"→ 版[{REV_INDEX}]（{target['modifiedTime']}）を復元します")

    # 3. 旧版をダウンロード
    r = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{fid}/revisions/{target['id']}",
        headers=h, params={"alt": "media"}, timeout=300)
    r.raise_for_status()
    data = r.content
    print(f"旧版取得: {len(data)//1024}KB")

    # 4. 現行版として書き戻し
    r = requests.patch(
        f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media",
        headers={**h, "Content-Type": "video/mp4"}, data=data, timeout=600)
    if not r.ok:
        raise Exception(f"書き戻し失敗: {r.status_code} {r.text[:300]}")
    print(f"✅ 復元完了: {FILENAME} を {target['modifiedTime']} の版に戻しました")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
