// Utilidades compartidas por la TV, el móvil y el panel de anfitrión.
// Todo el texto se inserta con textContent: nunca se interpreta HTML recibido.

export const WS_PATH = "/api/quiz_salon/ws";
export const QR_PATH = "/api/quiz_salon/qr.svg";
export const PLAY_PATH = "/quiz_salon/play";
export const TV_PATH = "/quiz_salon/tv";
export const LETTERS = ["A", "B", "C", "D"];

/** Crea un elemento DOM de forma segura. */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key in node && typeof value !== "string") node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function ordinal(n) {
  return `${n}.º`;
}

export function points(n) {
  return Number(n || 0).toLocaleString("es-ES");
}

export function joinUrl(base, code) {
  return `${base}${PLAY_PATH}?code=${encodeURIComponent(code)}`;
}

export function qrSrc(url) {
  return `${QR_PATH}?url=${encodeURIComponent(url)}`;
}

/** True si el host no sirve para que otros dispositivos se conecten. */
export function isLoopbackHost(hostname) {
  return ["localhost", "127.0.0.1", "[::1]", "::1"].includes(hostname);
}

/** Cuenta atrás local sincronizada con el tiempo restante que manda el servidor. */
export class Countdown {
  constructor() {
    this.set(null, 0, false);
  }
  set(remainingMs, durationMs, paused) {
    this.receivedAt = performance.now();
    this.remainingMs = remainingMs;
    this.durationMs = durationMs || 0;
    this.paused = !!paused;
  }
  remaining() {
    if (this.remainingMs === null || this.remainingMs === undefined) return null;
    if (this.paused) return this.remainingMs;
    return Math.max(0, this.remainingMs - (performance.now() - this.receivedAt));
  }
  fraction() {
    const r = this.remaining();
    if (r === null || !this.durationMs) return 0;
    return Math.max(0, Math.min(1, r / this.durationMs));
  }
  seconds() {
    const r = this.remaining();
    return r === null ? null : Math.ceil(r / 1000);
  }
}

/** WebSocket con reconexión automática, ping y estado visible. */
export class GameSocket {
  constructor({ hello, onMessage, onStatus, url }) {
    this.hello = hello;
    this.onMessage = onMessage;
    this.onStatus = onStatus || (() => {});
    this.url = url || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${WS_PATH}`;
    this.delay = 1000;
    this.ws = null;
    this.stopped = false;
    this.lastVersion = -1;
  }
  connect() {
    this.stopped = false;
    this.onStatus("connecting");
    let ws;
    try {
      ws = new WebSocket(this.url);
    } catch (err) {
      this._retry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.delay = 1000;
      this.lastVersion = -1;
      this.onStatus("open");
      this.identify();
      clearInterval(this.pinger);
      this.pinger = setInterval(() => this.send({ type: "ping" }), 20000);
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (err) {
        return;
      }
      if (msg.type === "state") {
        if (typeof msg.version === "number" && msg.version < this.lastVersion) return;
        this.lastVersion = msg.version;
      }
      if (msg.type === "pong") return;
      this.onMessage(msg);
    };
    ws.onclose = () => {
      clearInterval(this.pinger);
      if (this.ws === ws) this.ws = null;
      this.onStatus("closed");
      if (!this.stopped) this._retry();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch (err) {
        /* ignorado */
      }
    };
  }
  identify() {
    const payload = this.hello();
    if (payload) this.send({ type: "hello", ...payload });
  }
  reconnect() {
    if (this.ws) {
      this.ws.onclose = null;
      try {
        this.ws.close();
      } catch (err) {
        /* ignorado */
      }
      this.ws = null;
    }
    this.connect();
  }
  _retry() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), this.delay);
    this.delay = Math.min(this.delay * 1.7, 10000);
  }
  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }
  get open() {
    return !!this.ws && this.ws.readyState === WebSocket.OPEN;
  }
  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    clearInterval(this.pinger);
    if (this.ws) this.ws.close();
  }
}

/** Efectos de sonido sintetizados (sin archivos ni red). */
export class Sfx {
  constructor() {
    this.ctx = null;
  }
  get enabled() {
    return !!this.ctx;
  }
  enable() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return false;
    this.ctx = this.ctx || new Ctx();
    if (this.ctx.state === "suspended") this.ctx.resume();
    this.tone(660, 0.08, 0);
    return true;
  }
  tone(freq, dur, when = 0, type = "triangle", gain = 0.18) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime + when;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g).connect(this.ctx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.05);
  }
  tick() {
    this.tone(880, 0.06, 0, "square", 0.06);
  }
  go() {
    this.tone(523, 0.12, 0);
    this.tone(784, 0.18, 0.1);
  }
  reveal() {
    [523, 659, 784, 1047].forEach((f, i) => this.tone(f, 0.18, i * 0.08));
  }
  fanfare() {
    [392, 523, 659, 784, 659, 784, 1047].forEach((f, i) => this.tone(f, 0.22, i * 0.12, "sawtooth", 0.08));
  }
}
