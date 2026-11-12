const $ = id => document.getElementById(id);
const url = $('url'), rights = $('rights'), go = $('go');
const valid = () => /^https?:\/\/((www|m)\.)?(youtube\.com|youtu\.be)\//i.test(url.value.trim());
const sync = () => { go.disabled = !((valid() || $('file').files.length) && rights.checked); $('urlerr').hidden = !url.value.trim() || valid(); };
url.addEventListener('input', sync); $('file').addEventListener('change', sync); rights.addEventListener('change', sync);
const wait = ms => new Promise(r => setTimeout(r, ms));
const mmss = s => Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');

function show(title, msg, pct) {
  $('status').hidden = false;
  $('stitle').textContent = title; $('smsg').textContent = msg;
  $('fill').style.width = pct + '%'; document.querySelector('.bar').setAttribute('aria-valuenow', pct);
}

function render(clips) {
  $('clips').replaceChildren(...clips.map((c, i) => {
    const el = document.createElement('div'); el.className = 'clip';
    const v = Object.assign(document.createElement('video'), { controls: true, preload: 'metadata', src: c.url });
    const d = document.createElement('div');
    const t = document.createElement('b'); t.textContent = `Clip ${i + 1}`;
    const s = document.createElement('small'); s.textContent = `${mmss(c.start)} – ${mmss(c.end)} · ${c.title}`;
    const a = Object.assign(document.createElement('a'), { href: c.url, download: `clipforge-${i + 1}.mp4`, textContent: 'Descargar MP4' });
    d.append(t, s, document.createElement('br'), a); el.append(v, d); return el;
  }));
  $('results').hidden = false;
}

async function poll(id) {
  let fails = 0;
  for (;;) {
    let d;
    try {
      const r = await fetch('/api/jobs/' + id);
      if (r.status === 404) throw new Error('El servidor se reinició o se quedó sin memoria. Vuelve a intentarlo con un video más corto.');
      if (!r.ok) throw new Error((await r.json()).detail || 'Error del servidor');
      d = await r.json(); fails = 0;
    } catch (e) { if (++fails > 4) throw e; await wait(3000); continue; }
    if (d.state === 'error') throw new Error(d.message);
    if (d.state === 'done') { show('Clips listos', d.message, 100); return render(d.clips); }
    show('Creando tus clips…', d.message, d.progress || 0);
    await wait(2500);
  }
}

$('form').addEventListener('submit', async e => {
  e.preventDefault(); if (go.disabled) return;
  go.disabled = true; $('results').hidden = true; show('Enviando…', 'Preparando el trabajo.', 2);
  const o = { rights: rights.checked, count: +$('count').value, max_duration: +$('duration').value,
    ratio: $('ratio').value, quality: +$('quality').value, captions: $('captions').checked };
  const f = $('file').files[0];
  try {
    let r;
    if (f) {
      show('Subiendo el video…', 'No cierres esta página hasta que termine.', 3);
      r = await fetch('/api/upload?' + new URLSearchParams(o), { method: 'POST', body: f });
    } else {
      r = await fetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ youtube_url: url.value.trim(), ...o }) });
    }
    const d = await r.json();
    if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Revisa los datos del formulario.');
    await poll(d.job_id);
  } catch (err) { show('No se pudo completar', err.message, 0); }
  finally { sync(); }
});
