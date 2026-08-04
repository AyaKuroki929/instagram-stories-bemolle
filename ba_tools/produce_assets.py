"""痩身BAリール 資産準備（生産用の前半＝スクリプトで完結する部分）。
before/after写真 → 方式A処理（顔除去・枠別合成）→ Canva Connect APIへ非公開アップロード
→ 4つのasset_idをJSONで出力。トークンは期限前に自動refresh。

出力後、後半（copy-design→update_fill→replace_text→export）はMCPでオーケストレートする。

usage:
  python3 produce_assets.py --before B.jpg --after A.jpg --out DIR [--neck 0.45]
出力(stdout最終行): ASSETS {"b_p2":id,"b_cov":id,"a_p3":id,"a_cov":id}
"""
import os, sys, json, base64, time, argparse, urllib.request, urllib.error

CFG = os.path.expanduser("~/.config/canva_ba")
TOKEN_PATH = os.path.join(CFG, "token.json")
API = "https://api.canva.com/rest/v1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_env():
    e = {}
    for line in open(os.path.join(CFG, ".env")):
        if "=" in line:
            k, v = line.strip().split("=", 1); e[k] = v
    return e


def _token_call(data):
    env = load_env()
    auth = base64.b64encode(f'{env["CANVA_CLIENT_ID"]}:{env["CANVA_CLIENT_SECRET"]}'.encode()).decode()
    req = urllib.request.Request(API + "/oauth/token", data=urllib.parse.urlencode(data).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


import urllib.parse  # noqa: E402


def get_access_token():
    """期限が近ければrefresh。有効なaccess_tokenを返す。"""
    tok = json.load(open(TOKEN_PATH))
    obtained = tok.get("obtained_at", 0)
    exp = tok.get("expires_in", 14400)
    if time.time() < obtained + exp - 300 and obtained:
        return tok["access_token"]
    # refresh
    new = _token_call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    new["obtained_at"] = int(time.time())
    json.dump(new, open(TOKEN_PATH, "w")); os.chmod(TOKEN_PATH, 0o600)
    print("[token] refreshed, expires_in", new.get("expires_in"), file=sys.stderr)
    return new["access_token"]


def upload_asset(access_token, img_path, name):
    def api(method, path, data=None, headers=None):
        h = {"Authorization": f"Bearer {access_token}"}; h.update(headers or {})
        req = urllib.request.Request(API + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    meta = base64.b64encode(name.encode()).decode()
    s, d = api("POST", "/asset-uploads", data=open(img_path, "rb").read(),
               headers={"Content-Type": "application/octet-stream",
                        "Asset-Upload-Metadata": json.dumps({"name_base64": meta})})
    if s != 200:
        raise SystemExit(f"upload create失敗 {s}: {json.dumps(d)[:300]}")
    jid = d["job"]["id"]
    for _ in range(40):
        s, d = api("GET", f"/asset-uploads/{jid}")
        st = d.get("job", {}).get("status")
        if st == "success":
            return d["job"]["asset"]["id"]
        if st == "failed":
            raise SystemExit(f"upload失敗 {json.dumps(d)[:300]}")
        time.sleep(2)
    raise SystemExit("upload poll timeout")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--neck", type=float, default=0.45)
    a = ap.parse_args()

    import ba_process9
    paths = ba_process9.build(a.before, a.after, a.out, a.neck)
    print("[process] 4枚生成", list(paths), file=sys.stderr)

    at = get_access_token()
    stamp = str(int(time.time()))
    assets = {}
    for key, p in paths.items():
        assets[key] = upload_asset(at, p, f"ba_{key}_{stamp}")
        print(f"[upload] {key} -> {assets[key]}", file=sys.stderr)
    print("ASSETS " + json.dumps(assets))


if __name__ == "__main__":
    main()
