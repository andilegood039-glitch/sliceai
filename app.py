import os, uuid, json, subprocess, re, math, shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="SliceAI Real Video Processing Backend", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
ALLOWED = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-4000:])
    return p.stdout

def ffprobe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip())

def ffprobe_video(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate",
               "-of", "json", str(path)])
    return json.loads(out)["streams"][0]

def find_scene_scores(path, duration):
    # Real FFmpeg scene-change detection. Returns timestamps where visual content changes sharply.
    cmd = ["ffmpeg", "-hide_banner", "-i", str(path),
           "-vf", "select='gt(scene,0.35)',showinfo",
           "-an", "-f", "null", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    times = []
    for line in p.stderr.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            if 2 < t < duration - 2:
                times.append(t)
    return times

def find_loud_windows(path, duration):
    # Real FFmpeg audio analysis. Loudness is one useful signal for excitement.
    cmd = ["ffmpeg", "-hide_banner", "-i", str(path),
           "-af", "astats=metadata=1:reset=1",
           "-f", "null", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    vals = []
    current_time = 0.0
    for line in p.stderr.splitlines():
        mt = re.search(r"pts_time:([0-9.]+)", line)
        if mt:
            current_time = float(mt.group(1))
        mm = re.search(r"RMS level dB:\s*(-?[0-9.]+)", line)
        if mm:
            db = float(mm.group(1))
            if math.isfinite(db):
                vals.append((current_time, db))
    if not vals:
        return []
    # Keep local peaks above the 70th percentile.
    dbs = sorted(v for _, v in vals)
    threshold = dbs[max(0, int(len(dbs) * 0.70))]
    peaks = [t for t, db in vals if db >= threshold]
    return peaks

def candidate_clips(path, duration, n=5):
    scenes = find_scene_scores(path, duration)
    loud = find_loud_windows(path, duration)
    candidates = []
    # Score candidate centers using both scene changes and loudness.
    for t in scenes + loud:
        start = max(0.0, t - 18.0)
        end = min(duration, start + 36.0)
        if end - start < 8:
            continue
        scene_bonus = 1 if any(abs(s-t) < 1.5 for s in scenes) else 0
        loud_bonus = 1 if any(abs(x-t) < 1.5 for x in loud) else 0
        score = 55 + 18*scene_bonus + 15*loud_bonus
        # Prefer moments away from the first/last few seconds.
        if start > 5 and end < duration - 3:
            score += 5
        candidates.append((min(score, 99), start, end))
    candidates.sort(reverse=True)

    chosen = []
    for score, start, end in candidates:
        if all(abs(start - c["start"]) > 12 for c in chosen):
            chosen.append({"score": int(score), "start": round(start,2), "end": round(end,2)})
        if len(chosen) >= n:
            break

    # If media analysis finds too few moments, use evenly spaced fallback windows.
    if len(chosen) < n:
        for i in range(n * 2):
            center = duration * ((i + 1) / (n * 2 + 1))
            start = max(0, center - 18)
            end = min(duration, start + 36)
            if end - start < 8:
                continue
            if all(abs(start - c["start"]) > 12 for c in chosen):
                chosen.append({"score": 60, "start": round(start,2), "end": round(end,2)})
            if len(chosen) >= n:
                break

    chosen.sort(key=lambda x: x["score"], reverse=True)
    return chosen[:n]

def cut_clip(source, start, end, out):
    # Re-encode for accurate, independent clips and broad browser compatibility.
    run(["ffmpeg", "-y", "-ss", str(start), "-i", str(source),
         "-t", str(end-start), "-map", "0:v:0", "-map", "0:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])

@app.get("/", response_class=HTMLResponse)
def home():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/api/health")
def health():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}

@app.post("/api/process")
async def process_video(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Unsupported video format.")
    job = uuid.uuid4().hex
    src = UPLOADS / f"{job}{ext}"
    size = 0
    with src.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                src.unlink(missing_ok=True)
                raise HTTPException(413, "Video is larger than 1 GB.")
            f.write(chunk)

    try:
        duration = ffprobe_duration(src)
        info = ffprobe_video(src)
        clips = candidate_clips(src, duration, 5)
        result = []
        for i, c in enumerate(clips, 1):
            out = OUTPUTS / f"{job}-clip-{i}.mp4"
            cut_clip(src, c["start"], c["end"], out)
            result.append({
                "id": i,
                "title": ["Best Hook", "Strong Moment", "Big Reaction", "Key Reveal", "Final Highlight"][i-1],
                "score": c["score"],
                "start": c["start"],
                "end": c["end"],
                "url": f"/api/jobs/{job}/clips/{i}"
            })
        return {"job_id": job, "filename": file.filename, "duration": duration,
                "video": {"width": info.get("width"), "height": info.get("height")},
                "clips": result}
    except Exception as e:
        src.unlink(missing_ok=True)
        raise HTTPException(500, f"Processing failed: {e}")

@app.get("/api/jobs/{job}/clips/{clip_id}")
def get_clip(job: str, clip_id: int):
    p = OUTPUTS / f"{job}-clip-{clip_id}.mp4"
    if not p.exists():
        raise HTTPException(404, "Clip not found.")
    return FileResponse(p, media_type="video/mp4", filename=p.name)

@app.get("/api/jobs/{job}/clips/{clip_id}/download")
def download_clip(job: str, clip_id: int):
    p = OUTPUTS / f"{job}-clip-{clip_id}.mp4"
    if not p.exists():
        raise HTTPException(404, "Clip not found.")
    return FileResponse(p, media_type="video/mp4", filename=p.name)
