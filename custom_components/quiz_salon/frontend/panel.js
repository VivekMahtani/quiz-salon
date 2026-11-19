// Panel de anfitrión dentro de Home Assistant (custom element, solo administradores).
// Obtiene el token de anfitrión con el WebSocket autenticado de HA y controla la partida.
import { GameSocket, LETTERS, PLAY_PATH, TV_PATH, clear, el, isLoopbackHost, joinUrl, ordinal, points, qrSrc } from "./common.js";

const CSS_URL = new URL("./panel.css", import.meta.url).href;
const PHASES = {
  lobby: "Sala de espera",
  countdown: "Cuenta atrás",
  question: "Pregunta en curso",
  reveal: "Respuesta",
  finished: "Partida terminada",
};

class QuizSalonPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._state = null;
    this._info = null;
    this._socket = null;
    this._status = "connecting";
    this._message = "";
    this._configKey = "";
    this._root = el("div", { class: "wrap" });
    this.shadowRoot.append(el("link", { rel: "stylesheet", href: CSS_URL }), this._root);
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (!this._started) {
      this._started = true;
      this._start();
    }
  }
  set narrow(v) {
    this._narrow = v;
    if (this._menu) this._menu.narrow = v;
  }
  set panel(p) {
    this._panel = p;
  }

  disconnectedCallback() {
    this._socket?.stop();
    this._started = false;
  }

  connectedCallback() {
    if (this._hass && !this._started) {
      this._started = true;
      this._start();
    }
  }

  async _fetchToken() {
    this._info = await this._hass.callWS({ type: "quiz_salon/host_token" });
  }

  async _start() {
    this._render();
    try {
      await this._fetchToken();
    } catch (err) {
      this._message = "No se pudo obtener el acceso de anfitrión. ¿Está cargada la integración y eres administrador?";
      this._render();
      return;
    }
    this._socket = new GameSocket({
      hello: () => ({ role: "host", token: this._info.token }),
      onStatus: (s) => { this._status = s; this._renderStatus(); },
      onMessage: (m) => this._onMessage(m),
    });
    this._socket.connect();
  }

  async _onMessage(msg) {
    if (msg.type === "state") {
      this._state = msg;
      const active = this.shadowRoot.activeElement;
      if (active && active.tagName === "SELECT") {
        // No cerrar un desplegable abierto: se repinta al salir de él.
        if (!this._deferred) {
          this._deferred = true;
          active.addEventListener("blur", () => { this._deferred = false; this._render(); }, { once: true });
        }
        return;
      }
      this._render();
    } else if (msg.type === "error") {
      if (msg.code === "bad_token") {
        try {
          await this._fetchToken();
          this._socket.reconnect();
        } catch (err) { /* se informa abajo */ }
      }
      this._message = msg.message;
      this._renderStatus();
    } else if (msg.type === "ack") {
      this._message = "";
      this._renderStatus();
    }
  }

  _send(type, extra = {}) {
    if (!this._socket?.send({ type, ...extra })) {
      this._message = "Sin conexión con el juego. Reintentando…";
      this._renderStatus();
    }
  }

  _base() {
    return this._state?.join_base || this._info?.join_base || location.origin;
  }

  // ------------------------------------------------------------------ render
  _render() {
    const root = clear(this._root);
    const head = el("header", { class: "head" });
    const Menu = customElements.get("ha-menu-button");
    if (Menu && this._narrow) {
      this._menu = document.createElement("ha-menu-button");
      this._menu.hass = this._hass;
      this._menu.narrow = this._narrow;
      head.append(this._menu);
    }
    head.append(el("h1", { text: "Quiz del Salón" }), el("span", { id: "status", class: "status" }));
    root.append(head);
    this._statusLine = el("div", { class: "msg", role: "status" });
    root.append(this._statusLine);

    const s = this._state;
    if (!s) {
      root.append(el("div", { class: "card", text: this._message || "Conectando con el juego…" }));
      this._renderStatus();
      return;
    }
    const grid = el("div", { class: "grid" });
    grid.append(this._controlsCard(s), this._screensCard(s), this._playersCard(s));
    if (s.phase === "lobby") grid.append(this._configCard(s));
    else grid.append(this._liveCard(s));
    root.append(grid);
    this._renderStatus();
  }

  _renderStatus() {
    const badge = this.shadowRoot.getElementById("status");
    if (badge) {
      badge.className = `status ${this._status}`;
      badge.textContent = this._status === "open" ? "Conectado" : "Reconectando…";
    }
    if (this._statusLine) {
      this._statusLine.textContent = this._message || "";
      this._statusLine.hidden = !this._message;
    }
  }

  _controlsCard(s) {
    const connected = s.players.filter((p) => p.connected).length;
    const card = el("section", { class: "card controls" },
      el("h2", { text: PHASES[s.phase] + (s.paused ? " (en pausa)" : "") }));
    if (s.question && s.phase !== "lobby" && s.phase !== "finished") {
      card.append(el("p", { class: "sub", text: `Pregunta ${s.question.number} de ${s.question.total} · ${s.question.category.name}` }));
    }
    const row = el("div", { class: "buttons" });
    if (s.phase === "lobby") {
      const can = connected >= s.min_players;
      row.append(el("button", { class: "primary", disabled: !can, onclick: () => this._send("start"), text: "Empezar partida" }));
      if (!can) card.append(el("p", { class: "sub", text: `Se necesitan al menos ${s.min_players} jugadores conectados.` }));
    } else if (s.phase === "finished") {
      row.append(el("button", { class: "primary", onclick: () => this._send("new_game", { keep_players: true }), text: "Nueva partida con los mismos jugadores" }));
      row.append(el("button", { onclick: () => this._send("new_game", { keep_players: false }), text: "Nueva partida vacía" }));
    } else {
      row.append(s.paused
        ? el("button", { class: "primary", onclick: () => this._send("resume"), text: "Reanudar" })
        : el("button", { onclick: () => this._send("pause"), text: "Pausar" }));
      const nextLabel = { countdown: "Mostrar pregunta ya", question: "Cerrar pregunta", reveal: "Siguiente pregunta" }[s.phase];
      row.append(el("button", { class: s.paused ? "" : "primary", onclick: () => this._send("next"), text: nextLabel }));
      row.append(el("button", { class: "danger", onclick: () => {
        if (confirm("¿Terminar la partida ahora? Se mostrará la clasificación actual.")) this._send("end");
      }, text: "Terminar partida" }));
    }
    card.append(row);
    if (s.warnings?.length) card.append(...s.warnings.map((w) => el("p", { class: "warn", text: w })));
    return card;
  }

  _screensCard(s) {
    const base = this._base();
    const tvUrl = `${base}${TV_PATH}`;
    const playUrl = joinUrl(base, s.code);
    const card = el("section", { class: "card screens" }, el("h2", { text: "Pantallas" }));
    const line = (label, url, open) => el("div", { class: "link-row" },
      el("div", {}, el("strong", { text: label }), el("code", { text: url })),
      el("div", { class: "mini" },
        el("button", { onclick: () => navigator.clipboard?.writeText(url).then(() => { this._message = "Copiado."; this._renderStatus(); }), text: "Copiar" }),
        el("a", { href: open, target: "_blank", rel: "noopener", class: "btnlink", text: "Abrir" })));
    card.append(
      line("TV", tvUrl, tvUrl),
      line("Jugadores", playUrl, playUrl),
      el("div", { class: "qr-row" },
        el("img", { src: qrSrc(playUrl), alt: "QR para unirse", width: "132", height: "132" }),
        el("p", { class: "sub" }, "Código de sala ", el("b", { class: "code", text: s.code }),
          ". Abre la URL de la TV en el navegador del televisor; mostrará este QR en grande.")));
    const host = new URL(base).hostname;
    if (isLoopbackHost(host)) {
      card.append(el("p", { class: "warn", text: "Estás usando «localhost»: los móviles no podrán entrar. Abre Home Assistant con su IP o define la URL base en las opciones de la integración." }));
    } else if (/nabu\.casa$|ui\.nabu\.casa/.test(host) || (this._info?.local_only && !/^(\d+\.){3}\d+$|\.local$|^\[/.test(host))) {
      card.append(el("p", { class: "warn", text: `Comprueba que «${host}» es accesible desde la red de casa. Con «Solo red local» activo, las conexiones desde fuera se rechazan. Puedes fijar la URL base en las opciones.` }));
    }
    return card;
  }

  _playersCard(s) {
    const card = el("section", { class: "card" }, el("h2", { text: `Jugadores (${s.players.length}/${s.max_players})` }));
    if (!s.players.length) {
      card.append(el("p", { class: "sub", text: "Aún no hay nadie. Pide a los jugadores que escaneen el QR de la TV." }));
      return card;
    }
    const list = el("ul", { class: "players" });
    for (const p of s.players) {
      list.append(el("li", { class: p.connected ? "" : "off" },
        el("span", { class: "rk", text: s.phase === "lobby" ? "" : ordinal(p.rank) }),
        el("span", { class: "ava", text: p.avatar }),
        el("span", { class: "nm", text: p.name }),
        el("span", { class: "state", text: !p.connected ? "desconectado" : p.answered ? "respondió" : "" }),
        el("span", { class: "sc", text: points(p.score) }),
        el("button", { class: "icon", title: `Expulsar a ${p.name}`, "aria-label": `Expulsar a ${p.name}`, onclick: () => {
          if (confirm(`¿Expulsar a ${p.name} de la partida?`)) this._send("kick", { player_id: p.id });
        }, text: "✕" })));
    }
    card.append(list);
    return card;
  }

  _configCard(s) {
    const st = s.settings;
    const card = el("section", { class: "card" }, el("h2", { text: "Configuración" }));
    const select = (label, key, values, fmt) => el("label", { class: "field" }, label,
      el("select", { onchange: (ev) => this._send("configure", { settings: { [key]: Number(ev.target.value) } }) },
        values.map((v) => el("option", { value: String(v), selected: v === st[key], text: fmt(v) }))));
    card.append(
      el("div", { class: "fields" },
        select("Preguntas", "num_questions", [5, 8, 10, 12, 15, 20, 25, 30], (v) => `${v} preguntas`),
        select("Tiempo por pregunta", "time_limit", [10, 15, 20, 30, 45, 60], (v) => `${v} segundos`),
        select("Tiempo viendo la respuesta", "reveal_seconds", [5, 8, 10, 15, 20], (v) => `${v} segundos`)),
      el("label", { class: "check" },
        el("input", { type: "checkbox", checked: st.auto_advance, onchange: (ev) => this._send("configure", { settings: { auto_advance: ev.target.checked } }) }),
        "Pasar a la siguiente pregunta automáticamente"),
    );
    const selected = new Set(st.categories.length ? st.categories : s.categories_available.map((c) => c.id));
    const chips = el("div", { class: "chips", role: "group", "aria-label": "Categorías" });
    for (const c of s.categories_available) {
      const on = selected.has(c.id);
      chips.append(el("button", { class: `chip${on ? " on" : ""}`, "aria-pressed": String(on), onclick: () => {
        const next = new Set(selected);
        on ? next.delete(c.id) : next.add(c.id);
        if (!next.size) { this._message = "Elige al menos una categoría."; this._renderStatus(); return; }
        const all = next.size === s.categories_available.length;
        this._send("configure", { settings: { categories: all ? [] : [...next] } });
      } }, `${c.icon} ${c.name}`, el("small", { text: String(c.count) })));
    }
    card.append(el("h3", { text: "Categorías" }), chips,
      el("p", { class: "sub", text: `${s.question_bank_size} preguntas en total. Puedes añadir las tuyas en /config/quiz_salon/ (ver README).` }));
    return card;
  }

  _liveCard(s) {
    const card = el("section", { class: "card" }, el("h2", { text: "En pantalla" }));
    const q = s.question;
    if (s.phase === "finished") {
      const top = s.players.filter((p) => p.rank === 1).map((p) => p.name).join(" y ");
      card.append(el("p", { text: top ? `Ganador: ${top}` : "Sin jugadores." }));
      return card;
    }
    if (!q || !q.text) {
      card.append(el("p", { class: "sub", text: "Preparando la siguiente pregunta…" }));
      return card;
    }
    card.append(el("p", { class: "q", text: q.text }));
    const ol = el("ol", { class: "opts" });
    q.options.forEach((o, i) => {
      const right = s.phase === "reveal" && q.correct === i;
      ol.append(el("li", { class: right ? "right" : "" }, el("b", { text: LETTERS[i] }), ` ${o}`,
        s.phase === "reveal" ? el("small", { text: ` · ${q.counts[i]}` }) : null, right ? " ✓" : null));
    });
    card.append(ol);
    if (s.phase === "question") card.append(el("p", { class: "sub", text: `Han respondido ${s.answered_count} de ${s.players.length}.` }));
    return card;
  }
}

if (!customElements.get("quiz-salon-panel")) customElements.define("quiz-salon-panel", QuizSalonPanel);
