import hashlib, json, os, re, secrets, shutil, sqlite3, subprocess, threading, time, uuid, logging
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
    layout: str = "fill"
    style: str = "classic"
    topic: str = Field("", max_length=80)
    start_at: int = Field(0, ge=0)
    end_at: int = Field(0, ge=0)

def run(args, cwd=None):
    p = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if p.returncode:
        log.error("%s falló: %s", args[0], (p.stderr or p.stdout)[-1500:])
        raise RuntimeError((p.stderr or p.stdout)[-1500:])
    return p.stdout

def ts(t):
    n = round(t * 1000); h, n = divmod(n, 3600000); m, n = divmod(n, 60000); s, n = divmod(n, 1000)
    return f"{h:02}:{m:02}:{s:02},{n:03}"

def pick_moments(segs, total, count, span, topic=""):
    kw = [w for w in re.findall(r"\w+", topic.lower()) if len(w) > 2]
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
        score += 3 * sum(w in text.lower() for w in kw)
        cands.append((score, max(0, start - .2), min(total, end + .3), text))
    chosen = []
    for c in sorted(cands, reverse=True):
        if all(c[2] <= x[1] or c[1] >= x[2] for x in chosen): chosen.append(c)
        if len(chosen) == count: break
    return sorted(chosen, key=lambda c: c[1]) or [(1, 0, min(total, span), segs[0]["text"])]

SUB = {"classic": ("&H00FFFFFF", "&H00FFFFFF", False, False), "yellow": ("&H0000E5FF", "&H0000E5FF", False, False),
       "highlight": ("&H0000E5FF", "&H00FFFFFF", True, False), "caps": ("&H00FFFFFF", "&H00FFFFFF", False, True)}

def ass_time(t):
    cs = round(t * 100); h, cs = divmod(cs, 360000); m, cs = divmod(cs, 6000); s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"

def write_ass(path, W, H, words, a, z, style):
    prim, sec, kar, caps = SUB[style]
    fs = round(min(W, H) / (13 if caps else 16)); n = 3 if caps else 4
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,"
            "PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: D,Liberation Sans,%d,%s,%s,&H00101010,"
            "&H00000000,1,0,0,0,100,100,0,0,1,%d,0,2,40,40,%d,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,"
            "MarginV,Effect,Text\n") % (W, H, fs, prim, sec, max(3, fs // 14), H // 9)
    lines = []
    for i in range(0, len(words), n):
        g = words[i:i + n]
        x, y = max(0, g[0]["start"] - a), min(z - a, g[-1]["end"] - a)
        if y <= x: continue
        parts = []
        for j, w in enumerate(g):
            nxt = g[j + 1]["start"] if j + 1 < len(g) else g[-1]["end"]
            t = w["word"].strip().replace("{", "").replace("}", "")
            parts.append((f"{{\\k{max(1, round((nxt - w['start']) * 100))}}}" if kar else "") + (t.upper() if caps else t))
        lines.append(f"Dialogue: 0,{ass_time(x)},{ass_time(y)},D,,0,0,0,,{' '.join(parts)}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")

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
        if r.start_at or r.end_at:
            segs = [x for x in segs if x["end"] > r.start_at and (not r.end_at or x["start"] < r.end_at)]
        if not segs: raise UserError("No se detectó voz en el video (o en el rango elegido).")
        step("Buscando los mejores momentos…", 50)
        moments = pick_moments(segs, total, r.count, r.max_duration, r.topic)
        W, H = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080), "4:5": (1080, 1350)}[r.ratio]
        if r.quality == 720: W, H = round(W * 2 / 3), round(H * 2 / 3)
        clips = job["clips"]   # se llena clip a clip: el sitio los muestra al instante
        for i, (_, a, z, text) in enumerate(moments, 1):
            step(f"Exportando clip {i} de {len(moments)}…", 50 + int(48 * (i - 1) / len(moments)))
            if r.layout == "fit":
                vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5[bg];"
                      f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
            else:
                vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
            if r.captions:
                words = [w for x in segs for w in x["words"] if w["end"] > a and w["start"] < z]
                if words:
                    write_ass(folder / f"{i}.ass", W, H, words, a, z, r.style)
                    vf += f",ass={i}.ass"
            target = OUT / f"{jid}-{i}.mp4"
            run(["ffmpeg", "-y", "-ss", str(a), "-i", str(video), "-t", str(z - a), "-vf", vf, "-c:v", "libx264",
                 "-preset", "ultrafast", "-crf", "26", "-threads", "2", "-c:a", "aac", "-movflags", "+faststart", str(target)], cwd=folder)
            clips.append({"url": f"/outputs/{target.name}", "start": round(a), "end": round(z),
                          "title": " ".join(text.split()[:9]) + "…"})
        job.update(state="done", message="Tus clips están listos.", progress=100)
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


# ---------- Cuentas de usuario (SQLite + contraseñas PBKDF2 + cookie de sesión) ----------
DB = BASE / "data" / "clipforge.db"; DB.parent.mkdir(exist_ok=True)
SECURE = os.getenv("COOKIE_SECURE") == "1"   # ponlo a 1 cuando sirvas con HTTPS
SESSION_S = 30 * 86400
fails = {}

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

with db() as c:
    c.executescript("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, pw TEXT, created REAL);"
                    "CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, expires REAL);")

def hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300_000).hex()

def check_pw(pw, stored):
    salt, h = stored.split("$")
    return secrets.compare_digest(hash_pw(pw, bytes.fromhex(salt)).split("$")[1], h)

class Cred(BaseModel):
    email: str = Field(max_length=120)
    password: str = Field(min_length=8, max_length=128)

def start_session(resp, uid):
    t = secrets.token_urlsafe(32)
    with db() as c: c.execute("INSERT INTO sessions VALUES(?,?,?)", (t, uid, time.time() + SESSION_S))
    resp.set_cookie("session", t, max_age=SESSION_S, httponly=True, samesite="lax", secure=SECURE)

def current_user(request):
    t = request.cookies.get("session")
    row = None
    if t:
        with db() as c:
            row = c.execute("SELECT u.id, u.email FROM sessions s JOIN users u ON u.id=s.user_id "
                            "WHERE s.token=? AND s.expires>?", (t, time.time())).fetchone()
    if not row: raise HTTPException(401, "Inicia sesión para continuar.")
    return row

def clean_email(e):
    e = e.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e): raise HTTPException(400, "Escribe un correo válido.")
    return e

@app.post("/api/register")
def register(c: Cred, resp: Response):
    email = clean_email(c.email)
    try:
        with db() as d: uid = d.execute("INSERT INTO users(email,pw,created) VALUES(?,?,?)",
                                        (email, hash_pw(c.password), time.time())).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Ese correo ya tiene una cuenta. Inicia sesión.")
    start_session(resp, uid)
    return {"email": email}

@app.post("/api/login")
def login(c: Cred, resp: Response):
    email = clean_email(c.email); now = time.time()
    recent = [t for t in fails.get(email, []) if now - t < 600]
    if len(recent) >= 5: raise HTTPException(429, "Demasiados intentos. Espera 10 minutos.")
    with db() as d: row = d.execute("SELECT id, pw FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_pw(c.password, row["pw"]):
        fails[email] = recent + [now]
        raise HTTPException(401, "Correo o contraseña incorrectos.")
    fails.pop(email, None); start_session(resp, row["id"])
    return {"email": email}

@app.post("/api/logout")
def logout(request: Request, resp: Response):
    t = request.cookies.get("session")
    if t:
        with db() as d: d.execute("DELETE FROM sessions WHERE token=?", (t,))
    resp.delete_cookie("session")
    return {"ok": True}

@app.get("/api/me")
def me(request: Request): return {"email": current_user(request)["email"]}

@app.get("/")
def home(): return FileResponse(BASE / "index.html")

@app.get("/app.js")
def js(): return FileResponse(BASE / "app.js", media_type="text/javascript")

@app.get("/style.css")
def css(): return FileResponse(BASE / "style.css", media_type="text/css")

@app.get("/api/health")
def health(): return {"ok": True}

def admit(r, request, resp):
    vid = current_user(request)["id"]
    if not r.rights:
        raise HTTPException(400, "Debes confirmar que tienes permiso para editar este video.")
    if r.ratio not in ("9:16", "1:1", "16:9", "4:5") or r.quality not in (720, 1080) or r.layout not in ("fill", "fit") or r.style not in SUB:
        raise HTTPException(400, "Ajustes no válidos.")
    if sum(j["state"] in ("queued", "working") for j in jobs.values()) >= MAX_ACTIVE:
        raise HTTPException(429, "El servidor está ocupado. Inténtalo en unos minutos.")
    if any(j["user"] == vid and j["state"] in ("queued", "working") for j in jobs.values()):
        raise HTTPException(429, "Ya tienes un trabajo en curso. Espera a que termine.")
    return vid

def enqueue(jid, vid, r, title):
    jobs[jid] = {"title": title, "state": "queued", "message": "En cola…", "progress": 0, "clips": [], "created": time.time(), "user": vid}
    pool.submit(work, jid, r)
    return {"job_id": jid}

@app.post("/api/jobs")
def create(r: Req, request: Request, resp: Response):
    p = urlparse(r.youtube_url.strip())
    if p.scheme not in ("http", "https") or p.hostname not in HOSTS:
        raise HTTPException(400, "Pega un enlace de YouTube válido.")
    vid = admit(r, request, resp)
    return enqueue(uuid.uuid4().hex[:12], vid, r, r.youtube_url.strip()[:70])

@app.post("/api/upload")
async def upload(request: Request, resp: Response, rights: bool = False, count: int = Query(5, ge=1, le=10),
                 max_duration: int = Query(45, ge=15, le=90), ratio: str = "9:16", quality: int = 1080, captions: bool = True, layout: str = "fill", style: str = "classic",
                 topic: str = Query("", max_length=80), start_at: int = Query(0, ge=0), end_at: int = Query(0, ge=0)):
    r = Req(rights=rights, count=count, max_duration=max_duration, ratio=ratio, quality=quality, captions=captions,
            layout=layout, style=style, topic=topic, start_at=start_at, end_at=end_at)
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
    return enqueue(jid, vid, r, "Video subido")

@app.get("/api/preview")
def preview(request: Request, url: str):
    current_user(request)
    if urlparse(url).hostname not in HOSTS: raise HTTPException(400, "Enlace no válido.")
    try:
        from urllib.request import urlopen
        from urllib.parse import quote
        with urlopen("https://www.youtube.com/oembed?format=json&url=" + quote(url, safe=""), timeout=6) as f: d = json.load(f)
        return {"title": d.get("title", ""), "thumb": d.get("thumbnail_url", "")}
    except Exception:
        raise HTTPException(404, "Sin vista previa.")

@app.get("/api/jobs")
def my_jobs(request: Request):
    uid = current_user(request)["id"]
    mine = sorted((k, v) for k, v in jobs.items() if v["user"] == uid)
    return [{"id": k, "title": v["title"], "state": v["state"], "clips": len(v["clips"])}
            for k, v in sorted(mine, key=lambda x: -x[1]["created"])][:12]

@app.get("/api/jobs/{jid}")
def status(jid: str, request: Request):
    user = current_user(request)
    if jid not in jobs or jobs[jid]["user"] != user["id"]: raise HTTPException(404, "Trabajo no encontrado o caducado.")
    return {k: v for k, v in jobs[jid].items() if k not in ("created", "user")}
