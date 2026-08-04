# -*- coding: utf-8 -*-
"""納品前セルフチェック：明るさ・同期・テロップ存在・セーフゾーン・尺"""
import subprocess, numpy as np, io, sys, os
from PIL import Image
C=os.path.dirname(os.path.abspath(__file__))
F=os.path.join(C,"facial_reel_FINAL.mp4")
onsets=[0.0,3.863,7.163,9.362,12.961,15.55,18.42,22.022,24.343,26.971,29.154,32.014,34.175]; END=37.735
ok=True
def fail(msg):
    global ok; ok=False; print("  ✗", msg)

# 1) 尺
d=float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",F],capture_output=True,text=True).stdout.strip())
print(f"[1] 尺: {d:.2f}s", "OK" if 45.8<d<46.9 else "")
if not (45.8<d<46.9): fail(f"尺異常 {d}")

# 2) カットごとの明るさ（各文の中間フレーム）
print("[2] 明るさ (110-175 目標・夕暮れ#2とBAは除外):")
for i in range(13):
    t=(onsets[i]+ (onsets[i+1] if i+1<13 else END))/2
    raw=subprocess.run(["ffmpeg","-v","error","-ss",str(t),"-i",F,"-frames:v","1","-f","image2pipe","-vcodec","png","-"],capture_output=True).stdout
    im=np.asarray(Image.open(io.BytesIO(raw)).convert("L")).astype(float)
    m=im.mean()
    skip = i==1 or 9<=i<=11  # 夕暮れ情景・BA
    mark="(除外)" if skip else ("OK" if 108<=m<=178 else "★")
    print(f"  #{i+1}: mean={m:.0f} {mark}")
    if not skip and not (108<=m<=178): fail(f"#{i+1} 明るさ {m:.0f}")

# 3) 音声同期（間=無音・文頭=有音）
raw=subprocess.run(["ffmpeg","-v","error","-i",F,"-ac","1","-ar","16000","-f","s16le","-"],capture_output=True).stdout
a=np.frombuffer(raw,dtype=np.int16).astype(float)/32768
sr=16000
def rms(t0,t1):
    s=a[int(t0*sr):int(t1*sr)]; return np.sqrt((s**2).mean()) if len(s) else 0
print("[3] 声同期:")
bad=0
for i in range(1,13):
    on=onsets[i]  # 冒頭削除済→声は0起点
    gap=rms(on-0.55,on-0.1); head=rms(on+0.05,on+0.45)
    # BGM込みなので比で判定: 声頭が間の2倍以上あれば同期OK
    if head<0.08 or head<gap*2:
        bad+=1; fail(f"onset{i} gap={gap:.3f} head={head:.3f}")
print(f"  12オンセット中 異常{bad}件")

# 4) テロップ存在（各文中間で下部にコントラスト＝文字がある）
print("[4] テロップ描画:")
missing=0
for i in range(13):
    t=(onsets[i]+(onsets[i+1] if i+1<13 else END))/2
    raw=subprocess.run(["ffmpeg","-v","error","-ss",str(t),"-i",F,"-frames:v","1","-f","image2pipe","-vcodec","png","-"],capture_output=True).stdout
    im=np.asarray(Image.open(io.BytesIO(raw)).convert("L")).astype(float)
    y0,y1=(1400,1700) if 9<=i<=11 else (1100,1400)
    band=im[y0:y1,100:980]
    # 文字あり＝白ピクセル(>230)と黒フチ(<50)が両方一定量
    white=(band>195).mean(); dark=(band<60).mean()  # 黄テロップは輝度215なので195
    has = white>0.005 and dark>0.003
    if not has: missing+=1; fail(f"#{i+1} テロップ検出できず (w={white:.3f} d={dark:.3f})")
print(f"  13文中 未検出{missing}件")

# 5) セーフゾーン: フィード4:5クロップ(285-1635)で文字が切れないか
print("[5] セーフゾーン: テロップPNGの文字境界")
import glob
worst=0
for p in sorted(glob.glob(os.path.join(C,"telv","f*.png"))):
    im=np.asarray(Image.open(p))
    ys=np.where(im[:,:,3]>10)[0]
    if len(ys):
        lo,hi=ys.min(),ys.max()
        if hi>1635 or lo<285: worst=max(worst,hi); fail(f"{os.path.basename(p)} y={lo}-{hi}")
print("  全テロップ y285-1635内" if worst==0 else f"  ★はみ出し max={worst}")

print()
print("== 総合:", "ALL PASS ✅" if ok else "FAILあり ✗ ==")
sys.exit(0 if ok else 1)
