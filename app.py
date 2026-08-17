import os
import uuid
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# ============================================================
# PATHS
# ============================================================

BASE = Path(__file__).resolve().parent

UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
STATIC = BASE / "static"

# Create folders automatically
UPLOADS.mkdir(parents=True, exist_ok=True)
OUTPUTS.mkdir(parents=True, exist_ok=True)
STATIC.mkdir(parents=True, exist_ok=True)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="SliceAI",
    description="AI Video Clip Maker",
    version="1.0.0"
)


# ============================================================
# STATIC WEBSITE
# ============================================================

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC)),
    name="static"
)


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
async def home():
    index_file = STATIC / "index.html"

    if not index_file.exists():
        return {
            "name": "SliceAI",
            "status": "online",
            "message": "SliceAI API is running."
        }

    return FileResponse(str(index_file))


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
async def health():
    ffmpeg_ok = False

    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        ffmpeg_ok = result.returncode == 0

    except Exception:
        ffmpeg_ok = False

    return {
        "ok": True,
        "ffmpeg": ffmpeg_ok
    }


# ============================================================
# FFPROBE
# ============================================================

def get_duration(video_path: Path):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path)
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr)

        return float(result.stdout.strip())

    except Exception as e:
        raise RuntimeError(f"Could not read video duration: {e}")


# ============================================================
# CREATE CLIP
# ============================================================

def create_clip(
    source: Path,
    start: float,
    end: float,
    output: Path
):
    duration = end - start

    command = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-i",
        str(source),
        "-t",
        str(duration),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output)
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-3000:])

    return output


# ============================================================
# GENERATE CLIP TIMES
# ============================================================

def generate_clips(duration: float, number: int = 5):
    clips = []

    # Prefer approximately 30-second clips
    clip_length = 30.0

    if duration < 10:
        return []

    if duration < 30:
        clip_length = duration

    # Create evenly distributed clips
    if duration <= clip_length:
        starts = [0]

    else:
        maximum_start = max(0, duration - clip_length)

        if number == 1:
            starts = [0]

        else:
            step = maximum_start / (number - 1)
            starts = [
                i * step
                for i in range(number)
            ]

    for index, start in enumerate(starts):
        end = min(
            duration,
            start + clip_length
        )

        if end - start >= 5:
            clips.append({
                "id": index,
                "title": f"Best Clip {index + 1}",
                "score": max(
                    50,
                    100 - index * 5
                ),
                "start": round(start, 2),
                "end": round(end, 2)
            })

    return clips


# ============================================================
# VIDEO PROCESSING
# ============================================================

@app.post("/api/process")
async def process_video(
    file: UploadFile = File(...)
):

    allowed_extensions = {
        ".mp4",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".webm"
    }

    extension = Path(
        file.filename or ""
    ).suffix.lower()

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported video format."
        )

    job_id = uuid.uuid4().hex

    source = UPLOADS / f"{job_id}{extension}"

    try:

        # ----------------------------------------------------
        # Save uploaded video
        # ----------------------------------------------------

        with source.open("wb") as output_file:

            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                output_file.write(chunk)

        # ----------------------------------------------------
        # Get video information
        # ----------------------------------------------------

        duration = get_duration(source)

        # ----------------------------------------------------
        # Generate clip locations
        # ----------------------------------------------------

        clips = generate_clips(
            duration,
            number=5
        )

        results = []

        # ----------------------------------------------------
        # Create clips
        # ----------------------------------------------------

        for clip in clips:

            clip_id = clip["id"]

            output = OUTPUTS / (
                f"{job_id}-clip-{clip_id}.mp4"
            )

            create_clip(
                source,
                clip["start"],
                clip["end"],
                output
            )

            results.append({
                "id": clip_id,
                "title": clip["title"],
                "score": clip["score"],
                "start": clip["start"],
                "end": clip["end"],
                "url": (
                    f"/api/jobs/"
                    f"{job_id}/clips/"
                    f"{clip_id}"
                ),
                "download": (
                    f"/api/jobs/"
                    f"{job_id}/clips/"
                    f"{clip_id}/download"
                )
            })

        return {
            "ok": True,
            "job_id": job_id,
            "filename": file.filename,
            "video": {
                "duration": duration
            },
            "clips": results
        }

    except Exception as e:

        source.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail=f"Video processing failed: {str(e)}"
        )


# ============================================================
# VIEW CLIP
# ============================================================

@app.get("/api/jobs/{job_id}/clips/{clip_id}")
async def get_clip(
    job_id: str,
    clip_id: int
):

    clip_path = OUTPUTS / (
        f"{job_id}-clip-{clip_id}.mp4"
    )

    if not clip_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Clip not found."
        )

    return FileResponse(
        str(clip_path),
        media_type="video/mp4"
    )


# ============================================================
# DOWNLOAD CLIP
# ============================================================

@app.get(
    "/api/jobs/{job_id}/clips/{clip_id}/download"
)
async def download_clip(
    job_id: str,
    clip_id: int
):

    clip_path = OUTPUTS / (
        f"{job_id}-clip-{clip_id}.mp4"
    )

    if not clip_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Clip not found."
        )

    return FileResponse(
        str(clip_path),
        media_type="video/mp4",
        filename=f"sliceai-clip-{clip_id}.mp4"
    )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "4000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
