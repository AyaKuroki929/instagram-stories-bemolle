# -*- coding: utf-8 -*-
import os, subprocess
C=os.path.dirname(os.path.abspath(__file__))
RL="/Users/ayakuroki/Library/CloudStorage/GoogleDrive-nailsalon.flat@gmail.com/マイドライブ/リール"
body=os.path.join(C,"body_final.mp4")
cta1=os.path.join(C,"cta1.mp4"); cta2=os.path.join(C,"cta2.mp4")
bgm=os.path.join(RL,"BGM_ElegantSanctuary.mp3")
se_flash=os.path.join(RL,"SE","SE_インパクト(フラッシュ).mp3")
se_kira=os.path.join(RL,"SE","SE_キラーン(金文字).mp3")
voice=os.path.join(C,"vo4_paced.wav")

def d(f): return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",f],capture_output=True,text=True).stdout.strip())
CD=0.0; Tb=d(body); T=Tb+d(cta1)+d(cta2)
print(f"body={Tb:.3f} total={T:.3f}")

# 1) 映像連結: コールドオープン→本編→CTA（concatフィルタで再エンコード＝パラメータ差を吸収）
full=os.path.join(C,"full_video.mp4")
subprocess.run(["ffmpeg","-y","-v","error","-i",body,"-i",cta1,"-i",cta2,
  "-filter_complex","[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
  "-map","[v]","-r","30","-c:v","libx264","-preset","medium","-crf","18",full],check=True)
print("full_video 尺=",d(full))

# 2) 音声ミックス（声はコールドオープン分だけ遅延・BGMは0から全尺）
vd=int(CD*1000)  # 声の遅延ms
duck_a=29.154+CD; duck_b=32.014+CD; cta_start=37.74+CD
flash_ms=int((26.76+CD)*1000); kira_ms=int((29.154+CD)*1000)
bgm_vol=f"volume='if(between(t,{duck_a:.2f},{duck_b:.2f}),0.10,if(gt(t,{cta_start:.2f}),0.24,0.18))':eval=frame"
fade_st=T-1.5
fc=[
 f"[0:a]adelay={vd}|{vd},volume=1.0[a0]",
 f"[1:a]dynaudnorm,{bgm_vol},afade=t=out:st={fade_st:.2f}:d=1.5[a1]",
 f"[2:a]volume=0.45,adelay={flash_ms}|{flash_ms}[a2]",
 f"[3:a]volume=0.40,adelay={kira_ms}|{kira_ms}[a3]",
 f"[a0][a1][a2][a3]amix=inputs=4:normalize=0:duration=longest[am]",
 f"[am]atrim=0:{T:.3f},aresample=44100[aout]",
]
audio=os.path.join(C,"final_audio.m4a")
subprocess.run(["ffmpeg","-y","-v","error",
  "-i",voice,"-stream_loop","3","-i",bgm,"-i",se_flash,"-i",se_kira,
  "-filter_complex",";".join(fc),"-map","[aout]","-c:a","aac","-b:a","192k",audio],check=True)
print("final_audio 尺=",d(audio))

# 3) mux
final=os.path.join(C,"facial_reel_FINAL.mp4")
subprocess.run(["ffmpeg","-y","-v","error","-i",full,"-i",audio,
  "-map","0:v","-map","1:a","-c:v","copy","-c:a","aac","-b:a","192k","-shortest",
  "-movflags","+faststart",final],check=True)
print("FINAL 尺=",d(final))
print("DONE:",final)
