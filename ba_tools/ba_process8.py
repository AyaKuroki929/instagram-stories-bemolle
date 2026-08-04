import sys, os, numpy as np
from PIL import Image, ImageOps
from rembg import remove, new_session
import mediapipe as mp
SESS=new_session("u2net_human_seg")
POSE=mp.solutions.pose.Pose(static_image_mode=True, model_complexity=2)

def load_upright(path):
    im=ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if im.width>im.height: im=im.rotate(-90,expand=True)
    return im
def cutout(im): return remove(im,session=SESS).convert("RGBA")

def process_one(path):
    up=load_upright(path); rgba=cutout(up); arr=np.array(rgba)
    H,W=arr.shape[:2]
    r=POSE.process(np.array(up))
    ref=H*0.7
    if r.pose_landmarks:
        L=r.pose_landmarks.landmark
        def X(i): return L[i].x*W
        def Y(i): return L[i].y*H
        def V(i): return L[i].visibility
        # 頭の中心・大きさを顔ランドマークから推定
        nose=(X(0),Y(0))
        eyes_y=np.mean([Y(i) for i in (2,5)]); mouth_y=np.mean([Y(9),Y(10)])
        ears=[(X(i),Y(i)) for i in (7,8) if V(i)>0.3]
        cx=np.mean([nose[0]]+[e[0] for e in ears])
        if len(ears)==2: span=abs(ears[0][0]-ears[1][0])
        else: span=abs(mouth_y-eyes_y)*1.6           # 横向き等: 顔高から推定
        face_h=max(1.0, mouth_y-eyes_y)
        head_top=eyes_y-face_h*2.4                     # 髪まで覆う
        head_bot=mouth_y+face_h*1.0                    # 顎の下まで
        cy=(head_top+head_bot)/2
        ry=(head_bot-head_top)/2*1.12
        rx=max(span*0.72, ry*0.72)
        yy,xx=np.mgrid[0:H,0:W]
        mask=((xx-cx)/rx)**2 + ((yy-cy)/ry)**2 <= 1.0  # 頭の楕円だけ
        pass  # 頭は消さない（Canvaの枠外に出して隠す運用）
        sh=min(Y(11),Y(12)); heel=max(Y(29),Y(30),Y(31),Y(32)); ref=heel-sh
    a=arr[:,:,3]; ys,xs=np.where(a>12)
    body=Image.fromarray(arr).crop((xs.min(),ys.min(),xs.max()+1,ys.max()+1))
    return body, ref

def body_mean(rgba):
    arr=np.array(rgba).astype(float); m=arr[:,:,3]>40
    return arr[:,:,:3][m].mean(axis=0) if m.sum() else np.array([128.,128,128])
def match_color(after,before):
    bm,am=body_mean(before),body_mean(after)
    gain=np.clip(bm/np.maximum(am,1e-3),0.85,1.15); lg=bm.mean()/max(am.mean(),1e-3)
    if lg>1.05: gain*=1.05/lg
    arr=np.array(after).astype(float)
    for c in range(3): arr[:,:,c]=np.clip(arr[:,:,c]*gain[c],0,255)
    return Image.fromarray(arr.astype("uint8"))

OUT=sys.argv[3]
before,rb=process_one(sys.argv[1]); after,ra=process_one(sys.argv[2])
T=1300.0
before=before.resize((int(before.width*T/rb),int(before.height*T/rb)))
after =after.resize((int(after.width*T/ra),int(after.height*T/ra)))
after=match_color(after,before)
before.save(os.path.join(OUT,"before_clean.png")); after.save(os.path.join(OUT,"after_clean.png"))
Hh=max(before.height,after.height)+40; Wd=before.width+after.width+90
c=Image.new("RGBA",(Wd,Hh),(255,255,255,255))
c.alpha_composite(before,(30,Hh-before.height-20)); c.alpha_composite(after,(before.width+60,Hh-after.height-20))
c.convert("RGB").save(os.path.join(OUT,"_preview8.jpg"),quality=92)
print("done ref",int(rb),int(ra),"sizes",before.size,after.size)
