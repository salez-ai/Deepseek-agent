// Talks to the image generator page (a web page with a postMessage API) from a hidden frame.
//
// The generator page is third-party content, so:
//   - messages are only accepted from the generator's own frame (or a frame nested inside it)
//     AND only from the configured origin (or a sub-domain of the same site),
//   - messages are only sent to the configured origin, never to "*",
//   - the picture must be a base64 data URL of a real image type and at most 14 MB.

(function (root) {
  'use strict';

  const READY_TIMEOUT_MS = 20000;
  const READY_AFTER_LOAD_MS = 8000;      // a page that has loaded but still has not announced itself is not going to
  const MAX_DATA_URL = 14 * 1024 * 1024;
  const DATA_URL = /^data:image\/(?:png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/;

  let frame = null;
  let frameUrl = '';
  let origin = '';
  let site = '';
  const listeners = new Set();

  function siteOf(hostname) {
    if (/^[\d.]+$/.test(hostname) || hostname === 'localhost' || !hostname.includes('.')) return hostname;
    return hostname.split('.').slice(-2).join('.');
  }

  function fromGenerator(source) {
    // cross-origin windows still let us read .parent, so a frame nested inside the generator counts too
    let w = source;
    for (let i = 0; i < 5 && w; i++) {
      if (frame && w === frame.contentWindow) return true;
      if (w === w.parent) return false;
      w = w.parent;
    }
    return false;
  }

  function originAllowed(o) {
    if (o === origin) return true;
    try {
      const h = new URL(o).hostname;
      return h === site || h.endsWith('.' + site);
    } catch (e) { return false; }
  }

  root.addEventListener('message', (e) => {
    if (!frame || !fromGenerator(e.source) || !originAllowed(e.origin)) return;
    const d = e.data;
    if (!d || typeof d !== 'object') return;
    listeners.forEach((fn) => fn(d));
  });

  function waitFor(test, ms) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { listeners.delete(on); reject(new Error('timeout')); }, ms);
      const on = (d) => { if (test(d)) { clearTimeout(timer); listeners.delete(on); resolve(d); } };
      listeners.add(on);
    });
  }

  function dropFrame() {
    if (frame) frame.remove();
    frame = null;
    frameUrl = '';
  }

  // Load the generator once and reuse it. Resolves when the page says it is ready.
  async function ensureFrame(url) {
    const u = new URL(url);
    origin = u.origin;
    site = siteOf(u.hostname);
    if (frame && frameUrl === url) {
      try {                                    // is the page still alive?
        const pong = waitFor((d) => d.action === 'ready', 3000);
        frame.contentWindow.postMessage({ action: 'ping' }, origin);
        await pong;
        return frame;
      } catch (e) { dropFrame(); }
    }
    dropFrame();
    frame = document.createElement('iframe');
    frameUrl = url;
    const ready = waitFor((d) => d.action === 'ready', READY_TIMEOUT_MS);
    // a page that refuses to be framed still fires "load" (showing an error page): give up soon after
    const loaded = new Promise((resolve) => frame.addEventListener('load', () => resolve(), { once: true }));
    const quickFail = loaded.then(() => new Promise((_, reject) => setTimeout(() => reject(new Error('no ready signal after load')), READY_AFTER_LOAD_MS)));
    frame.src = url;
    document.body.append(frame);
    // the page announces itself on load, but we may have missed that: keep asking
    const poke = setInterval(() => { try { frame.contentWindow.postMessage({ action: 'ping' }, origin); } catch (e) { /* not loaded yet */ } }, 1000);
    try {
      await Promise.race([ready, quickFail]);
    } catch (e) {
      dropFrame();
      throw Object.assign(new Error('The generator page did not announce itself'), { code: 'NOT_READY' });
    } finally {
      clearInterval(poke);
    }
    return frame;
  }

  // params: { prompt, resolution, negativePrompt, seed }
  async function run(url, params, timeoutMs) {
    let f;
    try {
      f = await ensureFrame(url);
    } catch (e) {
      return { ok: false, code: e.code || 'NOT_READY', error: e.message };
    }
    const msg = { action: 'generate', prompt: params.prompt, resolution: params.resolution };
    if (params.negativePrompt) msg.negativePrompt = params.negativePrompt;
    if (params.seed !== undefined && params.seed !== null && params.seed !== '') msg.seed = params.seed;
    return new Promise((resolve) => {
      let done = false;
      const finish = (r) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        listeners.delete(on);
        if (!r.ok) dropFrame();                // start clean next time
        resolve(r);
      };
      const timer = setTimeout(() => finish({ ok: false, code: 'TIMEOUT', error: `no picture after ${Math.round(timeoutMs / 1000)} seconds` }), timeoutMs);
      const on = (d) => {
        if (d.action === 'result') {
          const url2 = String(d.dataUrl || '');
          if (url2.length > MAX_DATA_URL || !DATA_URL.test(url2)) {
            finish({ ok: false, code: 'BAD_RESULT', error: 'the generator returned something that is not a picture' });
          } else {
            finish({ ok: true, dataUrl: url2, seed: d.seed, resolution: d.resolution });
          }
        } else if (d.action === 'error') {
          finish({ ok: false, code: 'GENERATOR_ERROR', error: String(d.message || 'unknown error').slice(0, 200) });
        }
      };
      listeners.add(on);
      f.contentWindow.postMessage(msg, origin);
    });
  }

  root.DSAgentGen = { run, dropFrame };

  try {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || msg.target !== 'offscreen' || msg.type !== 'gen:run') return false;
      run(msg.url, msg.params || {}, msg.timeoutMs || 120000).then(sendResponse);
      return true;
    });
  } catch (e) { /* loaded outside the extension (tests) */ }
})(window);
