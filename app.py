import os,uuid,subprocess
from pathlib import Path
from fastapi import FastAPI,UploadFile,File,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE=Path(__file__).resolve().parent
UPLOADS=BASE/"uploads"; OUTPUTS=BASE/"outputs"; STATIC=BASE/"static"
for d in (UPLOADS,OUTPUTS,STATIC): d.mkdir(parents=True,exist_ok=True)

app=FastAPI(title="SliceAI",version="2.0")
app.mount("/static",StaticFiles(directory=str(STATIC)),name="static")

@app.get("/")
def home(): return FileResponse(str(STATIC/"index.html"))

@app.get("/api/health")
def health():
    out={}
    for b in ("ffmpeg","ffprobe"):
        try: out[b]=subprocess.run([b,"-version"],capture_output=True,timeout=10).returncode==0
        except Exception: out[b]=False
    return {"ok":True,**out}

def duration(p):
    r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(p)],capture_output=True,text=True,timeout=60)
    if r.returncode: raise RuntimeError(r.stderr[-2000:])
    return float(r.stdout.strip())

def make_clip(src,start,length,out):
    r=subprocess.run(["ffmpeg","-y","-ss",str(start),"-i",str(src),"-t",str(length),"-map","0:v:0","-map","0:a?","-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)],capture_output=True,text=True,timeout=900)
    if r.returncode: raise RuntimeError(r.stderr[-3000:])

@app.post("/api/process")
async def process(file:UploadFile=File(...)):
    ext=Path(file.filename or "").suffix.lower()
    if ext not in {".mp4",".mov",".m4v",".avi",".mkv",".webm"}: raise HTTPException(400,"Unsupported video format.")
    job=uuid.uuid4().hex; src=UPLOADS/f"{job}{ext}"
    try:
        with src.open("wb") as f:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                f.write(chunk)
        dur=duration(src)
        if dur<5: raise RuntimeError("Video is too short.")
        length=min(30.0,dur); count=min(5,max(1,int(dur//10)))
        max_start=max(0,dur-length)
        starts=[0.0] if count==1 else [max_start*i/(count-1) for i in range(count)]
        clips=[]
        for i,s in enumerate(starts):
            out=OUTPUTS/f"{job}-{i}.mp4"; make_clip(src,s,min(length,dur-s),out)
            clips.append({"id":i,"title":f"Slice {i+1}","score":max(50,100-i*7),"start":round(s,2),"end":round(min(dur,s+length),2),"url":f"/api/jobs/{job}/clips/{i}","download":f"/api/jobs/{job}/clips/{i}/download"})
        return {"ok":True,"job_id":job,"duration":round(dur,2),"clips":clips}
    except Exception as e: raise HTTPException(500,f"Processing failed: {e}")

@app.get("/api/jobs/{job}/clips/{clip_id}")
def view_clip(job:str,clip_id:int):
    p=OUTPUTS/f"{job}-{clip_id}.mp4"
    if not p.exists(): raise HTTPException(404,"Clip not found.")
    return FileResponse(str(p),media_type="video/mp4")

@app.get("/api/jobs/{job}/clips/{clip_id}/download")
def download_clip(job:str,clip_id:int):
    p=OUTPUTS/f"{job}-{clip_id}.mp4"
    if not p.exists(): raise HTTPException(404,"Clip not found.")
    return FileResponse(str(p),media_type="video/mp4",filename=f"sliceai-clip-{clip_id+1}.mp4")

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","10000")))
