import sys, os, numpy as np
from PIL import Image, ImageDraw, ImageFont
import mediapipe as mp
MINCHO="/System/Library/Fonts/ヒラギノ明朝 ProN.ttc"; GOTHIC="/System/Library/Fonts/Hiragino Sans GB.ttc"
POSE=mp.solutions.pose.Pose(static_image_mode=True,model_complexity=2)
def F(p,s): return ImageFont.truetype(p,s)
def crop_head(png):
    im=Image.open(png).convert("RGBA"); arr=np.array(im); H,W=arr.shape[:2]
    white=Image.new("RGB",(W,H),(255,255,255)); white.paste(im,(0,0),im)
    r=POSE.process(np.array(white))
    if r.pose_landmarks:
        L=r.pose_landmarks.landmark
        eyes=np.mean([L[2].y,L[5].y])*H; mouth=np.mean([L[9].y,L[10].y])*H
        chin=int(mouth+(mouth-eyes)*1.1)               # 顎の少し下
        arr[:max(1,chin),:,3]=0
    a=arr[:,:,3]; ys,xs=np.where(a>12)
    return Image.fromarray(arr).crop((xs.min(),ys.min(),xs.max()+1,ys.max()+1))

OUT=sys.argv[3]; hook=sys.argv[4] if len(sys.argv)>4 else "久しぶりに大好きな彼氏ができました"
before=crop_head(sys.argv[1]); after=crop_head(sys.argv[2])
W,H=1080,1920
canvas=Image.new("RGBA",(W,H),(255,255,255,255)); d=ImageDraw.Draw(canvas)
# 左68%に2体、右に縦書き余白
zone_w=int(W*0.66); col_w=zone_w//2; feet_y=int(H*0.88); top_y=int(H*0.05); avail_h=feet_y-top_y
def fit(im):
    r=min(avail_h/im.height,(col_w-20)/im.width); return im.resize((int(im.width*r),int(im.height*r)))
b=fit(before); a=fit(after)
bx=int(W*0.02)+(col_w-b.width)//2; ax=int(W*0.02)+col_w+(col_w-a.width)//2
canvas.alpha_composite(b,(bx, feet_y-b.height)); canvas.alpha_composite(a,(ax, feet_y-a.height))
lf=F(GOTHIC,46)
d.text((int(W*0.02)+col_w//2, feet_y+14),"初回施術前",font=lf,fill=(25,25,25),anchor="ma")
d.text((int(W*0.02)+col_w+col_w//2, feet_y+14),"18回施術後",font=lf,fill=(25,25,25),anchor="ma")
# 縦書きフック（右余白・体に被らない）
hf=F(MINCHO,60); ch=68; x=W-64; top=150; rows=(feet_y-top)//ch
i=0
for c in hook:
    if i>=rows: i=0; x-=ch+10
    d.text((x, top+i*ch), c, font=hf, fill=(35,35,35), anchor="ma"); i+=1
canvas.convert("RGB").save(OUT,quality=94); print("saved")
