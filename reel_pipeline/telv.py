# -*- coding: utf-8 -*-
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W,H=1080,1920
MIN="/System/Library/Fonts/ヒラギノ明朝 ProN.ttc"       # W6=index2
G8="/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc"    # index0
WHITE=(255,255,255); YEL=(255,224,70); PINK=(255,90,150)
def f_min(s): return ImageFont.truetype(MIN,s,index=2)
def f_g(s):   return ImageFont.truetype(G8,s,index=0)
def wline(font,s,tr):
    return sum(font.getbbox(c)[2]-font.getbbox(c)[0]+tr for c in s)-tr if s else 0
def draw_line(base, s, font, cy, y, fill, tr, mode):
    # mode: 'min'(明朝白 細フチ+影) 'g'(ゴシック黒フチ7) 'gold'(金グラデ+光彩)
    w=wline(font,s,tr); x=cy-w//2
    if mode=='min':
        sh=Image.new("RGBA",(W,H),(0,0,0,0)); sd=ImageDraw.Draw(sh)
        xx=x
        for c in s:
            sd.text((xx+4,y+6),c,font=font,fill=(0,0,0,255)); xx+=font.getbbox(c)[2]-font.getbbox(c)[0]+tr
        sh=sh.filter(ImageFilter.GaussianBlur(6)); base.alpha_composite(sh)
        d=ImageDraw.Draw(base); xx=x
        for c in s:
            d.text((xx,y),c,font=font,fill=fill,stroke_width=3,stroke_fill=(25,20,22,235)); xx+=font.getbbox(c)[2]-font.getbbox(c)[0]+tr
    elif mode=='g':
        d=ImageDraw.Draw(base); xx=x
        for c in s:
            d.text((xx,y),c,font=font,fill=fill,stroke_width=7,stroke_fill=(0,0,0)); xx+=font.getbbox(c)[2]-font.getbbox(c)[0]+tr
    elif mode=='gold':
        d=ImageDraw.Draw(base); xx=x
        # 光彩
        glow=Image.new("RGBA",(W,H),(0,0,0,0)); gd=ImageDraw.Draw(glow); gx=x
        for c in s:
            gd.text((gx,y),c,font=font,fill=(255,235,150,160),stroke_width=9,stroke_fill=(255,235,150,120)); gx+=font.getbbox(c)[2]-font.getbbox(c)[0]+tr
        glow=glow.filter(ImageFilter.GaussianBlur(9)); base.alpha_composite(glow)
        # 黒フチ
        xx=x
        for c in s: d.text((xx,y),c,font=font,fill=(0,0,0,0),stroke_width=7,stroke_fill=(20,14,8,255)); xx+=font.getbbox(c)[2]-font.getbbox(c)[0]+tr
        # 金グラデ塗り(マスク)
        mask=Image.new("L",(W,H),0); md=ImageDraw.Draw(mask); xx=x
        for c in s: md.text((xx,y),c,font=font,fill=255); xx+=font.getbbox(c)[2]-font.getbbox(c)[0]+tr
        grad=Image.new("RGBA",(W,H),(0,0,0,0)); gl=grad.load()
        for gy in range(y-6,y+font.size+10):
            if 0<=gy<H:
                t=(gy-(y-6))/(font.size+16); r=int(252-55*t); g=int(228-86*t); b=int(155-81*t)
                for gx2 in range(W): gl[gx2,gy]=(r,g,b,255)
        base.paste(grad,(0,0),mask)
def render(name, lines, y=1230, out="telv"):
    # lines: [(text, mode, size)]
    img=Image.new("RGBA",(W,H),(0,0,0,0))
    tr=5
    sized=[]
    for txt,mode,size in lines:
        font = f_min(size) if mode=='min' else f_g(size)
        while wline(font,txt,tr)>1000 and size>34:
            size-=2; font=f_min(size) if mode=='min' else f_g(size)
        sized.append((txt,mode,size,font))
    gap=18
    hs=[int(sz*1.32) for _,_,sz,_ in sized]
    Htot=sum(hs)+gap*(len(sized)-1)
    y0=y-Htot//2
    yy=y0
    for (txt,mode,size,font),h in zip(sized,hs):
        draw_line(img,txt,font,W//2,yy,YEL if mode=='g' else WHITE if mode!='pink' else PINK,tr,'gold' if mode=='gold' else ('g' if mode in('g','pink') else 'min'))
        yy+=h+gap
    p=os.path.join(os.path.dirname(__file__),out,name+".png"); os.makedirs(os.path.dirname(p),exist_ok=True); img.save(p); return p
def header(text="谷町九丁目｜たるみ改善専門店 ベモーレ"):
    img=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(img)
    font=f_min(44); bb=font.getbbox(text); tw=bb[2]-bb[0]; th=bb[3]-bb[1]
    padx,pady=44,22; pw,ph=tw+padx*2,th+pady*2; x0=(W-pw)//2; yv=260
    d.rounded_rectangle([x0,yv,x0+pw,yv+ph],radius=40,fill=(20,15,18,120))
    d.text((x0+padx,yv+pady-bb[1]),text,font=font,fill=(255,255,255))
    p=os.path.join(os.path.dirname(__file__),"telv","header.png"); os.makedirs(os.path.dirname(p),exist_ok=True); img.save(p); return p
