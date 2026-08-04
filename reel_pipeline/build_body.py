# -*- coding: utf-8 -*-
import os, glob, subprocess, sys
import telv  # テロップ・ヘッダー描画（再利用）

C=os.path.dirname(os.path.abspath(__file__))
D="/Users/ayakuroki/Library/CloudStorage/GoogleDrive-nailsalon.flat@gmail.com/マイドライブ/リール/フェイシャル素材"
SEG=os.path.join(C,"segb"); os.makedirs(SEG,exist_ok=True)
def clip(num):
    fs=glob.glob(f"{D}/DJI_*_{num}_D.MP4");
    if not fs: sys.exit(f"clip {num} not found")
    return fs[0]

onsets=[0.0,3.863,7.163,9.362,12.961,15.55,18.42,22.022,24.343,26.971,29.154,32.014,34.175]
END=37.735
def dur(i): return (onsets[i+1] if i+1<len(onsets) else END)-onsets[i]

# (seg_index0, 素材, in点, ズーム方向, 暗転)  ※BAはNoneで別扱い
# #1,#2=AIスチル(顔なし後ろ姿/夕暮れ) #6=Welcome→機械 #7=IMG_9041(別客) #8=VID(彩さん施術・別室)
K="/Users/ayakuroki/Library/CloudStorage/GoogleDrive-nailsalon.flat@gmail.com/マイドライブ/リール/共通素材"
segs=[

 (3,"0055",1.0,"out", False),
 (4,"0121",3.0,"in",  False),
 (8,"0060",1.0,"in",  False),
 (12,"0035",3.0,"out",False),
]
GAM={"0043":1.00,"0034":1.14,"0046":1.00,"0045":1.15,"0055":1.33,"0121":1.55,
     "0130":1.75,"0143":1.27,"0150":1.28,"0060":1.36,"0035":1.02,
     "0203":1.23,"IMG_9041":1.00,"VID":1.00}

def build_seg(idx,num,tin,zdir,dark):
    dd=dur(idx); g=GAM[num]; Z=0.08
    N=max(1,round(dd*30))
    zexpr = f"1+{Z}*on/{N}" if zdir=="in" else f"{1+Z}-{Z}*on/{N}"
    vf=(f"fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"eq=gamma={g:.3f}:saturation=1.05:contrast=1.03,"
        f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,setsar=1")
    if dark: vf+=",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.22:t=fill"
    vf+=",format=yuv420p"
    out=os.path.join(SEG,f"s{idx:02d}.mp4")
    cmd=["ffmpeg","-y","-v","error","-ss",str(tin),"-t",f"{dd:.3f}","-i",clip(num),
         "-an","-vf",vf,"-r","30","-c:v","libx264","-preset","medium","-crf","18",out]
    subprocess.run(cmd,check=True)
    return out

# BA区間（seg 9,10,11 = onsets[9]→END手前 onsets[12]=34.131）
def build_ba():
    dd=onsets[12]-onsets[9]  # 34.131-26.927=7.204
    ba=os.path.join(D,"BA2.mp4")
    vf=("fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "setsar=1,format=yuv420p")
    out=os.path.join(SEG,"s09_ba.mp4")
    subprocess.run(["ffmpeg","-y","-v","error","-ss","0.2","-t",f"{dd:.3f}","-i",ba,
                    "-an","-vf",vf,"-r","30","-c:v","libx264","-preset","medium","-crf","18",out],check=True)
    return out

def build_still(idx, png, zdir, dark=False):
    """AIスチルを微ズームで動画化"""
    dd=dur(idx); Z=0.07; N=max(1,round(dd*30))
    zexpr=f"1+{Z}*on/{N}" if zdir=="in" else f"{1+Z}-{Z}*on/{N}"
    vf=(f"scale=1536:1920,crop=1080:1920,"
        f"zoompan=z='{zexpr}':d={N}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,setsar=1")
    if dark: vf+=",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.22:t=fill"
    vf+=",format=yuv420p"
    out=os.path.join(SEG,f"s{idx:02d}.mp4")
    subprocess.run(["ffmpeg","-y","-v","error","-loop","1","-t",f"{dd:.3f}","-i",png,
        "-an","-vf",vf,"-t",f"{dd:.3f}","-r","30","-c:v","libx264","-preset","medium","-crf","18",out],check=True)
    return out

def build_path(idx, path, tin, zdir, g, dark=False):
    """任意パスの実写クリップからセグメント生成"""
    dd=dur(idx); Z=0.08; N=max(1,round(dd*30))
    zexpr=f"1+{Z}*on/{N}" if zdir=="in" else f"{1+Z}-{Z}*on/{N}"
    vf=(f"fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"eq=gamma={g:.3f}:saturation=1.05:contrast=1.03,"
        f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,setsar=1")
    if dark: vf+=",drawbox=x=0:y=0:w=iw:h=ih:color=black@0.22:t=fill"
    vf+=",format=yuv420p"
    out=os.path.join(SEG,f"s{idx:02d}.mp4")
    subprocess.run(["ffmpeg","-y","-v","error","-ss",str(tin),"-t",f"{dd:.3f}","-i",path,
        "-an","-vf",vf,"-r","30","-c:v","libx264","-preset","medium","-crf","18",out],check=True)
    return out

def build_seg6():
    """#6 ベモーレの〜: Welcomeサイン(1.0s)→機械(残り) の2カット構成"""
    dd=dur(5); d1=1.0; d2=dd-d1
    welcome=glob.glob(f"{K}/DJI_*_0203_D.MP4")[0]
    a=os.path.join(SEG,"s05a.mp4"); b=os.path.join(SEG,"s05b.mp4")
    vf1=(f"fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
         f"eq=gamma=1.23:saturation=1.05:contrast=1.03,setsar=1,format=yuv420p")
    subprocess.run(["ffmpeg","-y","-v","error","-ss","1.0","-t",f"{d1:.3f}","-i",welcome,
        "-an","-vf",vf1,"-r","30","-c:v","libx264","-preset","medium","-crf","18",a],check=True)
    N=max(1,round(d2*30))
    vf2=(f"fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
         f"eq=gamma=1.75:saturation=1.05:contrast=1.03,"
         f"zoompan=z='1+0.08*on/{N}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,setsar=1,format=yuv420p")
    subprocess.run(["ffmpeg","-y","-v","error","-ss","0.5","-t",f"{d2:.3f}","-i",clip("0130"),
        "-an","-vf",vf2,"-r","30","-c:v","libx264","-preset","medium","-crf","18",b],check=True)
    out=os.path.join(SEG,"s05.mp4")
    lf6=os.path.join(SEG,"l6.txt")
    with open(lf6,"w") as fh: fh.write(f"file '{a}'\nfile '{b}'\n")
    subprocess.run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",lf6,
        "-c:v","libx264","-preset","medium","-crf","18","-r","30",out],check=True)
    return out

print("== セグメント生成 ==")
build_seg(0,"0045",0.2,"in",False); print(" s00 0045実写フック done")
build_still(1, os.path.join(C,"ai_dusk.png"), "out", dark=False); print(" s01 AI夕暮れ done")
build_still(2, os.path.join(C,"ai_hook3.png"), "out", dark=True); print(" s02 AIフラットレイ done")
for idx,num,tin,zdir,dark in segs[:3]:
    build_seg(idx,num,tin,zdir,dark); print(f" s{idx:02d} {num} done")
build_seg6(); print(" s05 Welcome→機械 done")
build_path(6, os.path.join(D,"IMG_9041.MOV"), 0.8, "in", 1.00); print(" s06 IMG_9041 done")
build_path(7, os.path.join(D,"VID_20260106071314514.MP4"), 105.0, "out", 1.00); print(" s07 VID done")
ba=build_ba(); print(" BA done")
build_seg(*segs[3]); print(" s12 0035 done")

# concat 順: s00..s08, BA, s12
concat_list=[os.path.join(SEG,f"s{ i:02d}.mp4") for i in range(9)]+[ba,os.path.join(SEG,"s12.mp4")]
lf=os.path.join(SEG,"list.txt")
with open(lf,"w") as fh:
    for p in concat_list: fh.write(f"file '{p}'\n")
body_concat=os.path.join(C,"body_concat.mp4")
subprocess.run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",lf,
                "-c:v","libx264","-preset","medium","-crf","18","-r","30",body_concat],check=True)
d=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",body_concat],capture_output=True,text=True).stdout.strip()
print(f"body_concat 尺={d} (期待 {END})")

# ===== テロップ・ヘッダー描画 =====
print("== テロップ描画 ==")
telv.header()
# 各phrase: (name, lines[(text,mode,size)], y)
TL=[
 ("f01",[("崩れるたびに塗り直して","min",58),("気づけば1日3回","g",64)],1230),
 ("f02",[("夕方には毛穴落ち","min",58),("シワにファンデが溜まる","min",56)],1230),
 ("f03",[("隠しても","min",60),("隠しきれない","min",66)],1230),
 ("f04",[("でも、たるみも毛穴も","min",54),("年齢のせいじゃありません","g",56)],1230),
 ("f05",[("お肌の土台が","min",60),("ゆるんでいるだけ","min",62)],1230),
 ("f06",[("ベモーレのプラズマ","min",58),("肌質改善は","min",60)],1230),
 ("f07",[("寝転んでいるだけで","min",56),("お肌の奥から育て直す","min",56)],1230),
 ("f08",[("痛みも、ダウンタイムもなし","g",54)],1230),
 ("f09",[("整形みたいなことは","min",56),("しなくていい","min",64)],1230),
 ("f10",[("ファンデが","min",58),("薄くなっていく","min",64)],1545),
 ("f11",[("隠すお肌から","min",56),("見せられるお肌へ","gold",64)],1545),
 ("f12",[("ちゃんと","min",58),("結果を出します","g",64)],1545),
 ("f13",[("まずは体験で","min",54),("今のお肌の状態を","min",54),("一緒に見てみませんか","min",60)],1230),
]
for name,lines,y in TL: telv.render(name,lines,y=y)

# white flash png
from PIL import Image
Image.new("RGBA",(1080,1920),(255,255,255,255)).save(os.path.join(C,"white.png"))

print("== ヘッダー＋テロップ＋フラッシュ 合成 ==")
hdr=os.path.join(C,"telv","header.png")
tp=[os.path.join(C,"telv",f"{n}.png") for n,_,_ in TL]
white=os.path.join(C,"white.png")
# inputs
inputs=["-i",body_concat,"-loop","1","-t",f"{END}","-i",hdr]
for p in tp: inputs+=["-loop","1","-t",f"{END}","-i",p]
inputs+=["-loop","1","-t",f"{END}","-i",white]
# indices: 0 body,1 header,2..14 telops(f01..f13),15 white
fc=[]
# header: 全体からBA区間を除外
fc.append("[1]format=rgba,fade=t=in:st=0:d=0.4:alpha=1[hd]")
fc.append("[0][hd]overlay=0:0:enable='not(between(t,26.971,34.175))'[v1]")
prev="v1"
ba_start=26.971
for i,(name,lines,y) in enumerate(TL):
    st=onsets[i]; en=(onsets[i+1] if i+1<len(onsets) else END)
    fin=0.22; fout=0.16
    lab=f"t{i}"
    fc.append(f"[{2+i}]format=rgba,fade=t=in:st={st:.3f}:d={fin}:alpha=1,fade=t=out:st={en-fout:.3f}:d={fout}:alpha=1[{lab}]")
    outp=f"vv{i}"
    fc.append(f"[{prev}][{lab}]overlay=0:0:enable='between(t,{st:.3f},{en:.3f})'[{outp}]")
    prev=outp
# flash 白: BA直前 26.75-26.95
fc.append(f"[15]format=rgba,fade=t=in:st=26.76:d=0.10:alpha=1,fade=t=out:st=26.89:d=0.12:alpha=1[fl]")
fc.append(f"[{prev}][fl]overlay=0:0:enable='between(t,26.76,27.07)',format=yuv420p[vout]")
filt=";".join(fc)
body_final=os.path.join(C,"body_final.mp4")
cmd=["ffmpeg","-y","-v","error"]+inputs+["-filter_complex",filt,"-map","[vout]",
     "-c:v","libx264","-preset","medium","-crf","18","-r","30",body_final]
subprocess.run(cmd,check=True)
d2=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",body_final],capture_output=True,text=True).stdout.strip()
print(f"body_final 尺={d2}")
print("DONE body_final.mp4")
