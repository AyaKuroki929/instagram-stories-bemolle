import os, sys, json, base64, time, urllib.request, urllib.error
CFG=os.path.expanduser("~/.config/canva_ba")
AT=json.load(open(os.path.join(CFG,"token.json")))["access_token"]
img=sys.argv[1]; name=sys.argv[2] if len(sys.argv)>2 else "ba_asset"
def api(method,path,data=None,headers=None,raw=False):
    h={"Authorization":f"Bearer {AT}"}; h.update(headers or {})
    req=urllib.request.Request("https://api.canva.com/rest/v1"+path,data=data,headers=h,method=method)
    try:
        with urllib.request.urlopen(req,timeout=60) as r: return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read())
# 1) create upload job (binary body)
meta=base64.b64encode(name.encode()).decode()
s,d=api("POST","/asset-uploads",data=open(img,"rb").read(),
        headers={"Content-Type":"application/octet-stream",
                 "Asset-Upload-Metadata":json.dumps({"name_base64":meta})})
print("create status",s)
if s not in (200,): print(json.dumps(d)[:500]); sys.exit(1)
job=d.get("job",{}); jid=job.get("id")
print("job id",jid,"status",job.get("status"))
# 2) poll
for _ in range(30):
    s,d=api("GET",f"/asset-uploads/{jid}")
    st=d.get("job",{}).get("status")
    if st=="success":
        asset=d["job"]["asset"]; print("ASSET_ID",asset["id"],"name",asset.get("name")); sys.exit(0)
    if st=="failed": print("FAILED",json.dumps(d)[:400]); sys.exit(1)
    time.sleep(2)
print("poll timeout",json.dumps(d)[:300]); sys.exit(1)
