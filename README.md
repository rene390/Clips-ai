# ClipIA — aplicación propia para Android, iPhone y ordenador

MVP instalable como PWA (web app) con:
- Subida de vídeos.
- Clips sugeridos por detección de cambios de escena.
- Subtítulos en español con faster-whisper y descarga SRT.
- Recorte por tiempo.
- Conversión a vertical 9:16, cuadrado 1:1 y horizontal 16:9.
- Opción de incrustar los subtítulos en el vídeo.
- Interfaz adaptable a móvil y escritorio.

## Antes de empezar: qué significa “gratis”
El código y las herramientas son de uso gratuito/open source. Si lo ejecutas en tu propio ordenador, no pagas créditos por vídeo. No significa que un servidor en internet, almacenamiento en la nube, dominio, publicación en tiendas o datos móviles sean gratis. Esta versión es una PWA: se puede abrir e instalar desde el navegador en Android y iPhone; no es todavía una app nativa publicada en Play Store/App Store.

## Opción A — la más sencilla (Docker)
1. Instala Docker Desktop desde https://www.docker.com/products/docker-desktop/
2. Descomprime `clipia_full.zip`.
3. Abre una terminal dentro de la carpeta `clipia_full`.
4. Ejecuta:
   ```bash
   docker compose up --build
   ```
5. En el ordenador abre http://localhost:8000
6. Para detener el servidor: `Ctrl+C`; luego `docker compose down`.

La primera instalación puede tardar. Whisper descargará el modelo `tiny` la primera vez que generes subtítulos. Requiere conexión a internet para descargarlo, pero después puede trabajar localmente.

## Opción B — sin Docker (Linux/macOS)
Instala Python 3.11+, FFmpeg y FFprobe. Luego:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
En Windows PowerShell:
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Instala FFmpeg y asegúrate de que `ffmpeg` y `ffprobe` estén disponibles en PATH.

## Instalar en el teléfono
Para probar en tu misma red Wi-Fi:
1. Ejecuta ClipIA en el ordenador.
2. Averigua la IP local del ordenador (por ejemplo `192.168.1.20`).
3. En el móvil, abre `http://IP-DEL-ORDENADOR:8000`.
4. Android: menú del navegador > “Instalar aplicación” o “Añadir a pantalla de inicio”.
5. iPhone: Safari > Compartir > “Añadir a pantalla de inicio”.

Para instalarla desde fuera de tu casa necesitarás alojarla en un servidor accesible por internet y usar HTTPS. No publiques el servidor de desarrollo directamente en internet.

## Uso
1. Pulsa “Subir vídeo”.
2. “Detectar escenas y crear clips” para exportar cortes sugeridos.
3. “Generar subtítulos” para crear el archivo SRT en español.
4. En “Editar y recortar”, indica inicio/final en segundos.
5. Elige 9:16 para TikTok/Reels/Shorts y pulsa editar.
6. Marca “Incrustar subtítulos” si ya generaste un SRT.

## Endpoints
- `GET /` interfaz web
- `GET /docs` documentación interactiva
- `GET /api/health` comprobación
- `POST /api/upload` campo multipart `video`
- `POST /api/clips/{video_id}?max_clips=8&min_seconds=8`
- `POST /api/subtitles/{video_id}`
- `POST /api/edit/{video_id}` campos multipart: `start`, `end`, `aspect`, `burn_subtitles`

## Límites honestos del MVP
- “Clips automáticos” detecta cambios de escena; no entiende aún qué momento es viral ni genera puntuaciones de engagement.
- Recorte vertical centra la imagen. No hace seguimiento inteligente de caras/personas.
- Subtítulos: español, modelo Whisper pequeño (`tiny`) por defecto. Cambia `WHISPER_MODEL` a `base` o `small` si tu ordenador tiene más memoria y quieres mayor precisión (será más lento).
- El procesamiento se ejecuta en el equipo que aloja ClipIA; no se ha añadido una cola de trabajos ni progreso real por porcentaje.
- No hay login multiusuario, pagos, enlaces de descarga desde plataformas, IA de avatares ni sincronización cloud.

## Seguridad antes de ponerla pública
Añade autenticación, límites por usuario, HTTPS, protección CSRF, límites de duración y tamaño, borrado automático de archivos, cuotas de almacenamiento, aislamiento de FFmpeg y monitorización. No almacenes vídeos de otras personas sin autorización.
