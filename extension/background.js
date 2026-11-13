// The content script runs inside chat.deepseek.com, whose CSP and CORS rules
// would block calls to localhost. The service worker is not bound by them, so
// every bridge request is relayed through here.

const DEFAULTS = { bridgeUrl: 'http://127.0.0.1:8765', token: '' };

async function callBridge({ path, body }) {
  const { bridgeUrl, token } = await chrome.storage.local.get(DEFAULTS);
  const base = String(bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/+$/, '');
  if (!token) {
    return { ok: false, error: 'No token set. Click the extension icon and paste the token printed by the bridge.' };
  }
  try {
    const res = await fetch(base + path, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json', 'X-Agent-Token': token },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => null);
    if (res.status === 401) {
      return { ok: false, error: 'The bridge rejected the token. Copy it again from the bridge window.' };
    }
    return data || { ok: false, error: `Unexpected response (HTTP ${res.status})` };
  } catch (e) {
    return { ok: false, error: `Cannot reach the bridge at ${base}. Is bridge.py running?` };
  }
}

// Download an image from the project through the bridge and hand it back as base64.
async function fetchFile(path) {
  const { bridgeUrl, token } = await chrome.storage.local.get(DEFAULTS);
  const base = String(bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/+$/, '');
  if (!token) return { ok: false, error: 'No token set.' };
  try {
    const res = await fetch(base + '/file?path=' + encodeURIComponent(path), { headers: { 'X-Agent-Token': token } });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { msg = (await res.json()).error || msg; } catch (e) { /* not JSON */ }
      return { ok: false, error: msg };
    }
    const mime = (res.headers.get('Content-Type') || '').split(';')[0];
    const bytes = new Uint8Array(await res.arrayBuffer());
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return { ok: true, mime, size: bytes.length, data: btoa(bin) };
  } catch (e) {
    return { ok: false, error: 'Cannot reach the bridge.' };
  }
}

// ---- image generation -----------------------------------------------------------------------------
// The generator is a web page with a postMessage API (see README: "Real image generation"). It needs a
// real browser, so the extension is the browser: first a hidden frame, then (if that fails) a background tab.
const IMG_DEFAULTS = {
  imageGen: true,
  imageGenUrl: 'https://perchance.org/5jsm6cis4r',
  imageGenMode: 'auto',            // auto | frame | tab
  imageGenTimeoutSec: 120,
};
const IMG_SIZES = { square: '512x512', landscape: '768x512', portrait: '512x768', large: '768x768' };

function generatorUrl(text) {
  try {
    const u = new URL(String(text));
    const local = u.hostname === 'localhost' || u.hostname === '127.0.0.1';
    if (u.protocol === 'https:' || (u.protocol === 'http:' && local)) return u;
  } catch (e) { /* not a URL */ }
  return null;
}

async function ensureOffscreen() {
  const existing = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (existing.length) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['IFRAME_SCRIPTING'],
    justification: 'Run the image generator page in a hidden frame and talk to it with postMessage.',
  });
}

async function viaFrame(url, params, timeoutMs) {
  try {
    await ensureOffscreen();
    return await chrome.runtime.sendMessage({ target: 'offscreen', type: 'gen:run', url, params, timeoutMs })
      || { ok: false, code: 'NOT_READY', error: 'the hidden frame did not answer' };
  } catch (e) {
    return { ok: false, code: 'NOT_READY', error: String(e.message || e) };
  }
}

// Runs inside the generator page of the tab we opened (all frames). Self-contained on purpose.
function pollForImage(timeoutMs) {
  return new Promise((resolve) => {
    const start = Date.now();
    const tick = () => {
      const img = document.getElementById('resultImg');
      const elapsed = Date.now() - start;
      if (!img && elapsed > 3000) return resolve(null);               // some other frame: not the generator
      if (img && !img.hidden && /^data:image\//.test(img.src || '')) {
        return resolve({ ok: true, dataUrl: img.src, meta: (document.getElementById('metaEl') || {}).textContent || '' });
      }
      const status = (document.getElementById('statusEl') || {}).textContent || '';
      if (img && /failed|error/i.test(status)) return resolve({ ok: false, code: 'GENERATOR_ERROR', error: status.trim().slice(0, 200) });
      if (elapsed > timeoutMs) return resolve({ ok: false, code: 'TIMEOUT', error: `no picture after ${Math.round(timeoutMs / 1000)} seconds` });
      setTimeout(tick, 400);
    };
    tick();
  });
}

async function viaTab(url, params, timeoutMs) {
  const u = new URL(url);
  const granted = await chrome.permissions.contains({ origins: [u.origin + '/*'] });
  if (!granted) {
    return { ok: false, code: 'NO_PERMISSION', error: `the extension may only open ${u.hostname} in a tab if it is perchance.org or localhost` };
  }
  const link = new URL(url);
  link.searchParams.set('prompt', params.prompt);
  link.searchParams.set('resolution', params.resolution);
  if (params.negativePrompt) link.searchParams.set('negative', params.negativePrompt);
  if (params.seed !== undefined && params.seed !== null && params.seed !== '') link.searchParams.set('seed', String(params.seed));
  // the page appends its selected style (default "Photo") to prompts opened through a link, but not to
  // prompts sent with postMessage. An empty style selects "None", so both methods behave the same.
  link.searchParams.set('style', '');
  link.searchParams.set('style', '');       // the page adds its Style dropdown (default "Photo") to the prompt unless told "None"
  link.searchParams.set('autogen', '1');
  link.searchParams.set('embed', '1');
  const tab = await chrome.tabs.create({ url: link.href, active: false });
  try {
    for (let i = 0; i < 100; i++) {                                    // wait up to 30 s for the page to load
      const t = await chrome.tabs.get(tab.id);
      if (t.status === 'complete') break;
      await new Promise((r) => setTimeout(r, 300));
    }
    const frames = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: pollForImage, args: [timeoutMs] });
    const hit = frames.map((f) => f.result).find((r) => r);
    if (!hit) return { ok: false, code: 'NOT_READY', error: 'the generator page did not show a picture area' };
    if (hit.ok) {
      const m = /seed:\s*(-?\d+)/.exec(hit.meta || '');
      return { ok: true, dataUrl: hit.dataUrl, seed: m ? Number(m[1]) : undefined };
    }
    return hit;
  } catch (e) {
    return { ok: false, code: 'TAB_FAILED', error: String(e.message || e) };
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
}

function explain(res, tried, url) {
  const host = new URL(url).hostname;
  const how = tried.join(' then ');
  const hint = ` Open ${url} in a normal tab to check that the generator works there.`;
  if (res.code === 'TIMEOUT') return `${host} did not return a picture in time (${how}). It may be busy; try again.${hint}`;
  if (res.code === 'GENERATOR_ERROR') return `The generator reported a problem: ${res.error} (${how}). Try a different prompt, or try again.`;
  if (res.code === 'BAD_RESULT') return `The generator answered with something that is not a picture (${how}).`;
  if (res.code === 'NO_PERMISSION') return `${res.error}. Use the hidden-frame mode, or a perchance.org address.`;
  return `Could not reach the image generator (${how}): ${res.error}.${hint} If it only works in a real tab, set the mode to "Background tab" in the extension popup.`;
}

let genQueue = Promise.resolve();       // one picture at a time

async function generateImageNow(msg) {
  const cfg = await chrome.storage.local.get(IMG_DEFAULTS);
  if (!cfg.imageGen) return { ok: false, error: 'Image generation is switched off in the extension popup.' };
  const u = generatorUrl(cfg.imageGenUrl);
  if (!u) return { ok: false, error: 'The generator address in the extension popup is not valid (use https://...).' };
  const p = msg.params || {};
  const prompt = String(p.prompt || '').trim();
  if (prompt.length < 3) return { ok: false, error: 'The prompt is too short.' };
  const sizeKey = p.size || 'square';
  if (!IMG_SIZES[sizeKey]) return { ok: false, error: `size must be one of: ${Object.keys(IMG_SIZES).join(', ')}` };
  const params = { prompt, resolution: IMG_SIZES[sizeKey], negativePrompt: String(p.negative || '').trim().slice(0, 500) };
  if (p.seed !== undefined && p.seed !== '' && p.seed !== null) params.seed = p.seed;

  const timeoutMs = Math.max(15, Math.min(600, Number(cfg.imageGenTimeoutSec) || 120)) * 1000;
  const mode = ['auto', 'frame', 'tab'].includes(cfg.imageGenMode) ? cfg.imageGenMode : 'auto';
  const t0 = Date.now();
  const tried = [];
  let res;
  if (mode === 'tab') {
    tried.push('background tab');
    res = await viaTab(u.href, params, timeoutMs);
  } else {
    tried.push('hidden frame');
    res = await viaFrame(u.href, params, mode === 'auto' ? Math.min(timeoutMs, 60000) : timeoutMs);
    if (!res.ok && mode === 'auto') {
      const first = res;
      tried.push('background tab');
      res = await viaTab(u.href, params, timeoutMs);
      if (!res.ok && !res.code) res.code = first.code;
    }
  }
  if (!res.ok) return { ok: false, error: explain(res, tried, u.href) };
  const seconds = Math.round((Date.now() - t0) / 1000);
  const how = tried[tried.length - 1];
  if (msg.test) return { ok: true, dataUrl: res.dataUrl, seed: res.seed, how, seconds };

  const saved = await callBridge({
    path: '/image',
    body: { data: res.dataUrl.split(',')[1], name: p.name || '', prompt, seed: res.seed, size: params.resolution, negative: params.negativePrompt, source: u.hostname },
  });
  if (!saved.ok) return { ok: false, error: 'The picture was made but could not be saved: ' + (saved.error || 'unknown error') };
  return { ok: true, path: saved.path, bytes: saved.bytes, ext: saved.ext, seed: res.seed, size: params.resolution, how, seconds, host: u.hostname };
}

function generateImage(msg) {
  const job = genQueue.then(() => generateImageNow(msg)).catch((e) => ({ ok: false, error: String(e.message || e) }));
  genQueue = job.then(() => undefined);
  return job;
}

// ---- desktop notifications --------------------------------------------------------------------
// Notification ids look like  dsa|<tabId>|<windowId>|<kind>|<time>  so a click can find its tab.
const NOTIFY_KINDS = ['approval', 'continue', 'paused', 'error', 'done'];

function showNotification(msg, sender) {
  const kind = NOTIFY_KINDS.includes(msg.kind) ? msg.kind : 'approval';
  const tab = (sender && sender.tab) || {};
  const id = `dsa|${tab.id || 0}|${tab.windowId || 0}|${kind}|${Date.now()}`;
  chrome.notifications.create(id, {
    type: 'basic',
    iconUrl: 'icons/icon128.png',
    title: String(msg.title || 'DeepSeek Agent').slice(0, 80),
    message: String(msg.message || '').slice(0, 220),
    priority: 2,
    requireInteraction: !!msg.urgent,   // stays until you click it (Windows/Linux; macOS ignores this)
    silent: true,                       // the extension plays its own ringtone
  }, () => { void chrome.runtime.lastError; });
  return id;
}

function clearNotifications(kind, sender) {
  const tabId = sender && sender.tab ? String(sender.tab.id) : null;
  chrome.notifications.getAll((all) => {
    for (const id of Object.keys(all || {})) {
      const [prefix, t, , k] = id.split('|');
      if (prefix === 'dsa' && (tabId === null || t === tabId) && (!kind || k === kind)) chrome.notifications.clear(id);
    }
  });
}

function handleNotificationClick(id) {
  const [prefix, tabId, windowId] = String(id).split('|');
  if (prefix !== 'dsa') return;
  if (+tabId) chrome.tabs.update(+tabId, { active: true }).catch(() => {});
  if (+windowId) chrome.windows.update(+windowId, { focused: true }).catch(() => {});
  chrome.notifications.clear(id);
}
chrome.notifications.onClicked.addListener(handleNotificationClick);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'generateImage') {
    generateImage(msg).then(sendResponse);
    return true;
  }
  if (msg && msg.type === 'notify') {
    sendResponse({ ok: true, id: showNotification(msg, sender) });
    return false;
  }
  if (msg && msg.type === 'clearNotify') {
    clearNotifications(msg.kind, sender);
    sendResponse({ ok: true });
    return false;
  }
  if (msg && msg.type === 'bridgeFile') {
    fetchFile(String(msg.path || '')).then(sendResponse);
    return true;
  }
  if (msg && msg.type === 'openImage') {
    chrome.tabs.create({ url: chrome.runtime.getURL('viewer.html') + '?path=' + encodeURIComponent(String(msg.path || '')) });
    sendResponse({ ok: true });
    return false;
  }
  if (msg && msg.type === 'bridge') {
    callBridge(msg).then(sendResponse);
    return true; // async response
  }
  if (msg && msg.type === 'openOptions') {
    chrome.runtime.openOptionsPage();
    sendResponse({ ok: true });
  }
  return false;
});
