import os, sys, base64, hashlib, secrets, json, urllib.parse, urllib.request, urllib.error, http.server, time
CFG=os.path.expanduser("~/.config/canva_ba")
def load_env():
    e={}
    for line in open(os.path.join(CFG,".env")):
        if "=" in line: k,v=line.strip().split("=",1); e[k]=v
    return e
env=load_env(); CID=env["CANVA_CLIENT_ID"]; CSEC=env["CANVA_CLIENT_SECRET"]; RED=env["CANVA_REDIRECT"]
SCOPE="asset:read asset:write"
if sys.argv[1]=="url":
    ver=base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    chal=base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
    st=secrets.token_urlsafe(16)
    json.dump({"verifier":ver,"state":st}, open(os.path.join(CFG,"pkce.json"),"w"))
    url="https://www.canva.com/api/oauth/authorize?"+urllib.parse.urlencode({
        "code_challenge_method":"s256","response_type":"code","client_id":CID,
        "scope":SCOPE,"code_challenge":chal,"redirect_uri":RED,"state":st})
    print(url)
elif sys.argv[1]=="serve":
    pk=json.load(open(os.path.join(CFG,"pkce.json"))); holder={}
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            p=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in p:
                holder["code"]=p["code"][0]
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers()
                self.wfile.write("<h2>認可完了。このタブを閉じて戻ってOKです。</h2>".encode())
            else:
                self.send_response(400); self.end_headers()
        def log_message(self,*a): pass
    srv=http.server.HTTPServer(("127.0.0.1",8910),H); srv.timeout=1
    dl=time.time()+300
    while "code" not in holder and time.time()<dl: srv.handle_request()
    if "code" not in holder: print("TIMEOUT"); sys.exit(1)
    data=urllib.parse.urlencode({"grant_type":"authorization_code","code":holder["code"],
        "code_verifier":pk["verifier"],"redirect_uri":RED}).encode()
    auth=base64.b64encode(f"{CID}:{CSEC}".encode()).decode()
    req=urllib.request.Request("https://api.canva.com/rest/v1/oauth/token",data=data,
        headers={"Authorization":f"Basic {auth}","Content-Type":"application/x-www-form-urlencoded"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=30) as r: tok=json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("TOKEN_ERROR",e.code,e.read().decode()[:400]); sys.exit(1)
    json.dump(tok, open(os.path.join(CFG,"token.json"),"w")); os.chmod(os.path.join(CFG,"token.json"),0o600)
    print("TOKEN_OK keys:",list(tok.keys()),"scope:",tok.get("scope"),"expires_in:",tok.get("expires_in"))
