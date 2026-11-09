from pathlib import Path
import os, re, uuid, subprocess
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from scenedetect import detect, AdaptiveDetector

BASE = Path(__file__).resolve().parent.parent
UPLOADS, OUTPUTS = BASE / "uploads", BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
ALLOWED = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

app = FastAPI(title="ClipIA", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE / "app" / "static"), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUTS), name="outputs")

def safe_id(value: str) -> str:
    return Path(value).name

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-2500:])
    return p

def video_path(video_id: str) -> Path:
    p = UPLOADS / safe_id(video_id)
    if not p.exists() or p.suffix.lower() not in ALLOWED:
        raise HTTPException(404, "Vídeo no encontrado")
    return p

def probe_duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(p.stdout.strip())

def stamp(t: float) -> str:
    ms = max(0, int(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

@app.get("/", response_class=HTMLResponse)
def home():
    return (BASE / "app" / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE / "app" / "static" / "manifest.webmanifest",
                        media_type="application/manifest+json")

@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE / "app" / "static" / "sw.js",
                        media_type="application/javascript")

@app.post("/api/upload")
async def upload_video(video: UploadFile = File(...)):
    ext = Path(video.filename or "video.mp4").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Formato no admitido. Usa MP4, MOV, MKV, WebM o AVI.")
    dest = UPLOADS / f"{uuid.uuid4().hex}{ext}"
    total = 0
    try:
        with dest.open("wb") as f:
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, f"El límite de subida es {MAX_UPLOAD // (1024*1024)} MB.")
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return {"video_id": dest.name, "filename": video.filename, "bytes": total}

@app.post("/api/clips/{video_id}")
def create_clips(video_id: str, max_clips: int = 8, min_seconds: float = 8, max_seconds: float = 60):
    src = video_path(video_id)
    try:
        scenes = detect(str(src), AdaptiveDetector())
        segments = []
        for start, end in scenes:
            a, b = start.get_seconds(), end.get_seconds()
            duration = b - a
            if duration >= min_seconds:
                segments.append((a, min(duration, max_seconds)))
            if len(segments) >= min(max(max_clips, 1), 20):
                break
        # If no scene boundaries are found, offer one complete clip up to max_seconds.
        if not segments:
            duration = min(probe_duration(src), max_seconds)
            if duration >= 2:
                segments = [(0.0, duration)]
        clips = []
        for i, (start, duration) in enumerate(segments, 1):
            out = OUTPUTS / f"{src.stem}_clip_{i:02}.mp4"
            run(["ffmpeg", "-y", "-ss", str(start), "-i", str(src), "-t", str(duration),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-c:a", "aac", "-movflags", "+faststart", str(out)])
            clips.append({"name": out.name, "url": f"/outputs/{out.name}",
                          "start": round(start, 2), "duration": round(duration, 2)})
        return {"clips": clips, "message": "Clips sugeridos por cambios de escena. Revísalos antes de publicar."}
    except Exception as e:
        raise HTTPException(500, f"No se pudieron crear los clips: {e}")

@app.post("/api/edit/{video_id}")
def edit_video(video_id: str, start: float = Form(0), end: float = Form(0),
               aspect: str = Form("9:16"), burn_subtitles: bool = Form(False)):
    src = video_path(video_id)
    try:
        full = probe_duration(src)
        a = max(0.0, min(float(start), full))
        b = full if float(end) <= 0 else min(float(end), full)
        if b <= a:
            raise HTTPException(400, "El final debe ser posterior al inicio.")
        filters = []
        if aspect == "9:16":
            filters.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
        elif aspect == "1:1":
            filters.append("scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080")
        elif aspect == "16:9":
            filters.append("scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080")
        else:
            raise HTTPException(400, "Formato no válido.")
        srt = OUTPUTS / f"{src.stem}.srt"
        if burn_subtitles and srt.exists():
            sub = str(srt).replace("\\", "/").replace(":", r"\:")
            filters.append(f"subtitles='{sub}'")
        out = OUTPUTS / f"{src.stem}_edit_{uuid.uuid4().hex[:6]}.mp4"
        run(["ffmpeg", "-y", "-ss", str(a), "-i", str(src), "-t", str(b-a),
             "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(out)])
        return {"name": out.name, "url": f"/outputs/{out.name}", "duration": round(b-a, 2),
                "aspect": aspect}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"No se pudo editar el vídeo: {e}")

@app.post("/api/subtitles/{video_id}")
def create_subtitles(video_id: str):
    src = video_path(video_id)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(os.getenv("WHISPER_MODEL", "tiny"), device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(src), language="es", vad_filter=True)
        srt = OUTPUTS / f"{src.stem}.srt"
        rows = []
        for n, seg in enumerate(segments, 1):
            rows.extend([str(n), f"{stamp(seg.start)} --> {stamp(seg.end)}", seg.text.strip(), ""])
        srt.write_text("\n".join(rows), encoding="utf-8")
        return {"subtitle_file": f"/outputs/{srt.name}", "language": info.language,
                "message": "Subtítulos SRT creados. Puedes incrustarlos al editar el vídeo."}
    except Exception as e:
        raise HTTPException(500, f"Error generando subtítulos: {e}")

@app.get("/api/health")
def health():
    return {"status": "ok", "app": "ClipIA"}
