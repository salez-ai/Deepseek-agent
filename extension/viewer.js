// Full-size viewer for images the agent made. Runs as an extension page, so the DeepSeek page's
// security policy does not apply. The image is only ever shown through <img>, never as a document.
const params = new URLSearchParams(location.search);
const path = params.get('path') || '';
const $ = (id) => document.getElementById(id);
let current = null; // { mime, dataUrl, w, h, base }

function fail(text) {
  $('name').textContent = path || 'Image';
  const m = $('msg');
  m.textContent = text;
  m.className = 'err';
}

function download(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

function exportPng() {
  const scale = Math.min(+$('scale').value || 1, Math.floor(8192 / Math.max(current.w, current.h)) || 1);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(current.w * scale);
  canvas.height = Math.round(current.h * scale);
  const img = new Image();
  img.onload = () => {
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) { $('info').textContent = 'PNG export failed'; return; }
      download(blob, `${current.base}${scale > 1 ? '@' + scale + 'x' : ''}.png`);
    }, 'image/png');
  };
  img.src = current.dataUrl;
}

(async () => {
  if (!path || path.startsWith('/') || path.split(/[\\/]/).includes('..')) return fail('No image path was given.');
  const res = await chrome.runtime.sendMessage({ type: 'bridgeFile', path });
  if (!res || !res.ok) return fail((res && res.error) || 'Could not load the image.');
  const dataUrl = `data:${res.mime};base64,${res.data}`;
  const img = new Image();
  img.alt = path;
  img.onerror = () => fail('The browser could not display this image.');
  img.onload = () => {
    const base = path.split('/').pop().replace(/\.[^.]+$/, '');
    current = { mime: res.mime, dataUrl, w: img.naturalWidth || 800, h: img.naturalHeight || 600, base, ext: path.split('.').pop() };
    document.title = path;
    $('name').textContent = path;
    $('info').textContent = `${current.w} x ${current.h} | ${res.mime.replace('image/', '').replace('svg+xml', 'SVG').toUpperCase()} | ${Math.max(1, Math.round(res.size / 1024))} KB`;
    $('stage').replaceChildren(img);
    for (const id of ['png', 'orig', 'fit']) $(id).disabled = false;
  };
  img.src = dataUrl;
})();

$('png').addEventListener('click', () => current && exportPng());
$('orig').addEventListener('click', () => {
  if (!current) return;
  const bytes = Uint8Array.from(atob(current.dataUrl.split(',')[1]), (c) => c.charCodeAt(0));
  download(new Blob([bytes], { type: current.mime }), `${current.base}.${current.ext}`);
});
$('fit').addEventListener('click', () => {
  const actual = $('stage').classList.toggle('actual');
  $('fit').textContent = actual ? 'Fit to window' : 'Actual size';
});
