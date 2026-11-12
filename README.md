# ClipForge

Pega un enlace de YouTube de un video tuyo (o con permiso) → transcripción con Faster-Whisper → selección de momentos → clips MP4 con subtítulos.

## Ejecutar
Requiere Python 3.11+ y FFmpeg.
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn server:app --reload
```
Abre http://localhost:8000. La primera vez se descarga el modelo Whisper (en CPU puede tardar).

## Configuración (variables de entorno)
`COOKIE_SECURE` (pon 1 si usas HTTPS) · `MAX_VIDEO_MINUTES` (90) · `WHISPER_MODEL` (small) · `WORKERS` (1) · `MAX_ACTIVE_JOBS` (4) · `CLIP_TTL_HOURS` (2)

## Publicar
`docker build -t clipforge . && docker run -p 8000:8000 clipforge`. Sirve para Render, Railway, Fly.io o un VPS. Para producción añade autenticación, rate limit por IP y almacenamiento persistente; actualiza yt-dlp con frecuencia.

## Uso responsable
Solo videos propios o con permiso explícito. Respeta los derechos de autor y los términos de YouTube.

## Acceso
Sin registro: se entra directo. Cada visitante recibe un id anónimo en una cookie, solo ve sus propios trabajos y tiene uno activo a la vez.
