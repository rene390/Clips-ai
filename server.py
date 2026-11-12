import json, os, re, secrets, shutil, subprocess, threading, time, uuid, logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

log = logging.getLogger("clipforge")
BASE = Path(__file__).parent
OUT, TMP = BASE / "outputs", BASE / "tmp"
MAX_MIN = int(os.getenv("MAX_VIDEO_MINUTES", 90))   # límite de duración del video fuente
TTL = int(os.getenv("CLIP_TTL_HOURS", 2)) * 3600    # los clips se borran pasado este tiempo
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE_JOBS", 4))   # trabajos en cola + en curso
WHISPER = os.getenv("WHISPER_MODEL", "tiny")
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", 500)) * 1024 * 1024
HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

shutil.rmtree(TMP, ignore_errors=True)
OUT.mkdir(exist_ok=True); TMP.mkdir(exist_ok=True)
app = FastAPI(title="ClipForge AI")
app.mount("/outputs", StaticFiles(directory=OUT), name="outputs")
pool = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", 1)))
jobs, model, model_lock = {}, None, threading.Lock()

class UserError(Exception): pass

class Req(BaseModel):
    youtube_url: str = Field("", max_length=300)
    rights: bool
    count: int = Field(3, ge=1, le=10)
    max_duration: int = Field(45, ge=15, le=90)
    ratio: str = "9:16"
    quality: int = 1080
    captions: bool = True

def run(args, cwd=None):
    p = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if p.returncode:
        log.error("%s falló: %s", args[0], (p.stderr or p.stdout)[-1500:])
        raise RuntimeError((p.stderr or p.stdout)[-1500:])
    return p.stdout

def ts(t):
    n = round(t * 1000); h, n = divmod(n, 3600000); m, n = divmod(n, 60000); s, n = divmod(n, 1000)
    return f"{h:02}:{m:02}:{s:02},{n:03}"

def pick_moments(segs, total, count, span):
    """Ventanas que empiezan y terminan en límites de frase (sin cortar a mitad)."""
    lo, cands = min(15, span), []
    for i, a in enumerate(segs):
        inside = [s for s in segs[i:] if s["end"] - a["start"] <= span]
        if not inside: continue
        start, end = a["start"], inside[-1]["end"]
        if end - start < lo: continue
        text = " ".join(s["text"] for s in inside)
        words = len(text.split())
        score = words / (end - start) + 0.15 * text.count("?") + 0.1 * text.count("!")
        cands.append((score, max(0, start - .2), min(total, end + .3), text))
    chosen = []
    for c in sorted(cands, reverse=True):
        if all(c[2] <= x[1] or c[1] >= x[2] for x in chosen): chosen.append(c)
        if len(chosen) == count: break
    return sorted(chosen, key=lambda c: c[1]) or [(1, 0, min(total, span), segs[0]["text"])]

def chunks(words, start, end, size=4):
    out = []
    for i in range(0, len(words), size):
        g = words[i:i + size]
        a, z = max(0, g[0]["start"] - start), min(end - start, g[-1]["end"] - start)
        if z > a: out.append((a, z, " ".join(w["word"].strip() for w in g)))
    return out

def work(jid, r):
    global model
    folder = TMP / jid; folder.mkdir(exist_ok=True)
    job = jobs[jid]
    def step(msg, pct): job.update(state="working", message=msg, progress=pct)
    try:
        if r.youtube_url:
            step("Descargando el video…", 5)
            try:
                run(["yt-dlp", "--no-playlist", "--socket-timeout", "30", "--match-filter", f"duration<={MAX_MIN * 60}",
                     "-f", f"bv*[height<={r.quality}]+ba/b[height<={r.quality}]", "--merge-output-format", "mp4",
                     "-o", str(folder / "source.%(ext)s"), "--", r.youtube_url])
            except RuntimeError as e:
                m = str(e).lower()
                if "sign in" in m or "bot" in m or "cookies" in m or "429" in m:
                    raise UserError("YouTube bloqueó la descarga desde este servidor. Sube el archivo de tu video con el botón de arriba.")
                if "private" in m or "unavailable" in m or "age" in m:
                    raise UserError("Ese video es privado, no está disponible o tiene restricción de edad.")
                raise UserError(f"No se pudo descargar el video. Prueba subiendo el archivo (máximo {MAX_MIN} minutos).")
        video = next(folder.glob("source.*"), None)
        if not video: raise UserError(f"El video no se descargó (el máximo es {MAX_MIN} minutos).")
        total = float(json.loads(run(["ffprobe", "-v", "quiet", "-print_format", "json",
                                      "-show_format", str(video)]))["format"]["duration"])
        step("Transcribiendo el audio…", 20)
        with model_lock:
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(WHISPER, device="cpu", compute_type="int8")
            stream, _ = model.transcribe(str(video), vad_filter=True, word_timestamps=True,
                                          beam_size=1, condition_on_previous_text=False)
            segs = [{"start": s.start, "end": s.end, "text": s.text.strip(),
                     "words": [{"start": w.start, "end": w.end, "word": w.word} for w in (s.words or [])]}
                    for s in stream]
        if not segs: raise UserError("No se detectó voz en el video.")
        step("Buscando los mejores momentos…", 50)
        moments = pick_moments(segs, total, r.count, r.max_duration)
        W, H = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}[r.ratio]
        if r.quality == 720: W, H = round(W * 2 / 3), round(H * 2 / 3)
        clips = []
        for i, (_, a, z, text) in enumerate(moments, 1):
            step(f"Exportando clip {i} de {len(moments)}…", 50 + int(48 * (i - 1) / len(moments)))
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
            if r.captions:
                words = [w for s in segs for w in s["words"] if w["end"] > a and w["start"] < z]
                lines = [f"{n}\n{ts(x)} --> {ts(y)}\n{t}\n" for n, (x, y, t) in enumerate(chunks(words, a, z), 1)]
                (folder / f"{i}.srt").write_text("\n".join(lines), encoding="utf-8")
                fs = round(min(W, H) / 17)
                vf += (f",subtitles={i}.srt:original_size={W}x{H}:force_style='FontName=Liberation Sans,Bold=1,"
                       f"FontSize={fs},PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,"
                       f"Outline=3,Alignment=2,MarginV={H // 12}'")
            target = OUT / f"{jid}-{i}.mp4"
            run(["ffmpeg", "-y", "-ss", str(a), "-i", str(video), "-t", str(z - a), "-vf", vf, "-c:v", "libx264",
                 "-preset", "ultrafast", "-crf", "26", "-threads", "2", "-c:a", "aac", "-movflags", "+faststart", str(target)], cwd=folder)
            clips.append({"url": f"/outputs/{target.name}", "start": round(a), "end": round(z),
                          "title": " ".join(text.split()[:9]) + "…"})
        job.update(state="done", message="Tus clips están listos.", progress=100, clips=clips)
    except UserError as e:
        job.update(state="error", message=str(e))
    except Exception:
        log.exception("Error en el trabajo %s", jid)
        job.update(state="error", message="Algo falló al procesar el video. Inténtalo de nuevo con otro enlace.")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

def cleaner():
    while True:
        time.sleep(600)
        now = time.time()
        for f in OUT.glob("*.mp4"):
            if now - f.stat().st_mtime > TTL: f.unlink(missing_ok=True)
        for k in [k for k, v in jobs.items() if now - v["created"] > TTL]: jobs.pop(k, None)

threading.Thread(target=cleaner, daemon=True).start()


# ---------- Acceso directo: cada visitante recibe un id anónimo en una cookie (sin correo ni cuenta) ----------
SECURE = os.getenv("COOKIE_SECURE") == "1"   # ponlo a 1 cuando sirvas con HTTPS

def visitor(request, resp):
    vid = request.cookies.get("vid") or secrets.token_urlsafe(16)
    resp.set_cookie("vid", vid, max_age=30 * 86400, httponly=True, samesite="lax", secure=SECURE)
    return vid

@app.get("/")
def home(): return FileResponse(BASE / "index.html")

@app.get("/app.js")
def js(): return FileResponse(BASE / "app.js", media_type="text/javascript")

@app.get("/style.css")
def css(): return FileResponse(BASE / "style.css", media_type="text/css")

@app.get("/api/health")
def health(): return {"ok": True}

def admit(r, request, resp):
    vid = visitor(request, resp)
    if not r.rights:
        raise HTTPException(400, "Debes confirmar que tienes permiso para editar este video.")
    if r.ratio not in ("9:16", "1:1", "16:9") or r.quality not in (720, 1080):
        raise HTTPException(400, "Ajustes no válidos.")
    if sum(j["state"] in ("queued", "working") for j in jobs.values()) >= MAX_ACTIVE:
        raise HTTPException(429, "El servidor está ocupado. Inténtalo en unos minutos.")
    if any(j["user"] == vid and j["state"] in ("queued", "working") for j in jobs.values()):
        raise HTTPException(429, "Ya tienes un trabajo en curso. Espera a que termine.")
    return vid

def enqueue(jid, vid, r):
    jobs[jid] = {"state": "queued", "message": "En cola…", "progress": 0, "clips": [], "created": time.time(), "user": vid}
    pool.submit(work, jid, r)
    return {"job_id": jid}

@app.post("/api/jobs")
def create(r: Req, request: Request, resp: Response):
    p = urlparse(r.youtube_url.strip())
    if p.scheme not in ("http", "https") or p.hostname not in HOSTS:
        raise HTTPException(400, "Pega un enlace de YouTube válido.")
    vid = admit(r, request, resp)
    return enqueue(uuid.uuid4().hex[:12], vid, r)

@app.post("/api/upload")
async def upload(request: Request, resp: Response, rights: bool = False, count: int = Query(5, ge=1, le=10),
                 max_duration: int = Query(45, ge=15, le=90), ratio: str = "9:16", quality: int = 1080, captions: bool = True):
    r = Req(rights=rights, count=count, max_duration=max_duration, ratio=ratio, quality=quality, captions=captions)
    vid = admit(r, request, resp)
    jid = uuid.uuid4().hex[:12]; folder = TMP / jid; folder.mkdir(); size = 0
    try:
        with open(folder / "source.mp4", "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD: raise HTTPException(413, f"El archivo supera {MAX_UPLOAD // 1048576} MB.")
                f.write(chunk)
        if not size: raise HTTPException(400, "El archivo está vacío.")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True); raise
    return enqueue(jid, vid, r)

@app.get("/api/jobs/{jid}")
def status(jid: str, request: Request):
    if jid not in jobs or jobs[jid]["user"] != request.cookies.get("vid"): raise HTTPException(404, "Trabajo no encontrado o caducado.")
    return {k: v for k, v in jobs[jid].items() if k not in ("created", "user")}
