// Pantalla pública (TV). Solo lectura: muestra el estado que manda el servidor.
import { Countdown, GameSocket, LETTERS, Sfx, clear, el, isLoopbackHost, joinUrl, ordinal, points, qrSrc } from "./common.js";

const app = document.getElementById("app");
const statusDot = document.getElementById("status");
const timer = new Countdown();
const sfx = new Sfx();
let state = null;
let lastKey = "";
let lastSecond = null;

const socket = new GameSocket({
  hello: () => ({ role: "tv" }),
  onStatus: (s) => {
    statusDot.className = `status-dot ${s}`;
    statusDot.setAttribute("aria-label", s === "open" ? "Conectado" : "Reconectando");
    if (s !== "open" && state) renderOverlay("Reconectando…", "Comprueba que la TV sigue en la red de casa.");
  },
  onMessage: (msg) => {
    if (msg.type === "state") {
      const prev = state;
      state = msg;
      timer.set(msg.remaining_ms, msg.duration_ms, msg.paused);
      playSounds(prev, msg);
      render();
    }
  },
});
socket.connect();

function playSounds(prev, next) {
  if (!sfx.enabled || !prev) return;
  if (prev.phase !== next.phase) {
    if (next.phase === "question") sfx.go();
    if (next.phase === "reveal") sfx.reveal();
    if (next.phase === "finished") sfx.fanfare();
  }
}

function joinLink() {
  const base = state.join_base || location.origin;
  return joinUrl(base, state.code);
}

function header() {
  const bar = el("header", { class: "tv-bar" },
    el("div", { class: "brand" }, "Quiz del ", el("span", { text: "Salón" })),
  );
  if (state.question && state.phase !== "lobby" && state.phase !== "finished") {
    const q = state.question;
    bar.append(el("span", { class: "chip", text: `Pregunta ${q.number} de ${q.total}` }));
  }
  if (state.phase !== "lobby") bar.append(el("span", { class: "code", text: `Sala ${state.code}` }));
  if (!sfx.enabled) {
    bar.append(el("button", {
      class: "tv-sound", type: "button",
      onclick: () => { sfx.enable(); render(true); },
    }, "Activar sonido"));
  }
  return bar;
}

function renderLobby() {
  const url = joinLink();
  const players = state.players;
  const seats = el("div", { class: "seats" });
  for (let i = 0; i < state.max_players; i++) {
    const p = players[i];
    seats.append(p
      ? el("div", { class: `seat filled${p.connected ? "" : " off"}` },
          el("span", { class: "ava", text: p.avatar, "aria-hidden": "true" }),
          el("span", { class: "nm", text: p.name }))
      : el("div", { class: "seat", "aria-hidden": "true" }));
  }
  const need = Math.max(0, state.min_players - players.length);
  const side = el("section", { class: "lobby-side" },
    el("h2", { text: players.length ? `${players.length} ${players.length === 1 ? "jugador" : "jugadores"} en la sala` : "La sala está vacía" }),
    el("p", { text: need > 0
      ? `Falta${need === 1 ? "" : "n"} ${need} para empezar. Escanea el código con el móvil.`
      : "Todo listo. El anfitrión empieza la partida desde Home Assistant." }),
    seats,
  );
  if (!state.join_base && isLoopbackHost(location.hostname)) {
    side.append(el("div", { class: "warn", text: "Esta TV ha abierto el juego con «localhost»: los móviles no podrán entrar. Abre la TV con la IP de Home Assistant o define la URL base en las opciones." }));
  }
  const card = el("section", { class: "placard join-card enter" },
    el("img", { src: qrSrc(url), alt: "Código QR para unirse", width: "320", height: "320" }),
    el("h1", { text: "Únete con el móvil" }),
    el("div", { class: "room" }, "Código de sala", el("b", { text: state.code })),
    el("div", { class: "url", text: url }),
  );
  return el("div", { class: "lobby" }, card, side);
}

function renderCountdown() {
  const q = state.question;
  const box = el("section", { class: "countdown enter" },
    el("div", {},
      el("div", { class: "cat-icon", text: q.category.icon, "aria-hidden": "true" }),
      el("h2", { text: q.category.name }),
      el("div", { class: "of", text: `Pregunta ${q.number} de ${q.total}` }),
      q.double ? el("div", { class: "chip sun double-banner", text: "Última pregunta: puntos dobles" }) : null,
      el("div", { class: "n", id: "count-n", text: String(timer.seconds() ?? "") }),
    ),
  );
  return box;
}

function optionTile(i, text, extra = {}) {
  const tile = el("div", { class: `opt opt-${i}${extra.cls ? " " + extra.cls : ""}` },
    el("span", { class: "letter", text: LETTERS[i], "aria-hidden": "true" }),
    el("span", { class: "label", text }),
  );
  if (extra.count !== undefined) tile.append(el("span", { class: "count", text: String(extra.count), "aria-label": `${extra.count} respuestas` }));
  if (extra.mark) tile.append(el("span", { class: "mark", text: extra.mark }));
  return tile;
}

function questionCard(q) {
  return el("section", { class: "placard q-card" },
    el("div", { class: "meta" },
      el("span", { text: `${q.category.icon} ${q.category.name}` }),
      q.double ? el("span", { class: "chip sun", text: "×2 puntos" }) : null,
    ),
    el("h1", { class: `q-text${q.text.length > 110 ? " long" : ""}`, text: q.text }),
  );
}

function timerDisc() {
  return el("div", { class: "timer", id: "timer", role: "timer", "aria-label": "Tiempo restante" },
    el("div", { class: "disc" }),
    el("div", { class: "num", id: "timer-n", text: String(timer.seconds() ?? "") }),
  );
}

function renderQuestion() {
  const q = state.question;
  const opts = el("div", { class: "options" }, q.options.map((t, i) => optionTile(i, t)));
  const row = el("div", { class: "answered-row" },
    el("span", { text: `Han respondido ${state.answered_count} de ${state.players.length}` }),
    state.players.map((p) => el("span", { class: `who${p.answered ? " done" : ""}` },
      el("span", { class: "ava", text: p.avatar, "aria-hidden": "true" }), p.name)),
  );
  return el("div", { class: "round enter" }, questionCard(q), timerDisc(), opts, row);
}

function renderReveal() {
  const q = state.question;
  const byId = Object.fromEntries((q.results || []).map((r) => [r.id, r]));
  const opts = el("div", { class: "options" }, q.options.map((t, i) => optionTile(i, t, {
    cls: i === q.correct ? "right" : "dim",
    count: q.counts[i],
    mark: i === q.correct ? "✓ Correcta" : null,
  })));
  const left = el("div", { class: "round reveal" }, questionCard(q), opts);
  const board = el("aside", { class: "placard board enter" }, el("h3", { text: "Marcador" }));
  for (const p of state.players.slice(0, 10)) {
    const r = byId[p.id];
    board.append(el("div", { class: `row${p.connected ? "" : " off"}` },
      el("span", { class: "rk", text: ordinal(p.rank) }),
      el("span", { class: "ava", text: p.avatar, "aria-hidden": "true" }),
      el("span", { class: "nm", text: p.name }),
      el("span", { class: "sc" }, points(p.score),
        r && r.points ? el("span", { class: "delta", text: `+${points(r.points)}` }) : null),
    ));
  }
  return el("div", { class: "reveal-layout" }, left, board);
}

function renderFinished() {
  const players = state.players;
  const podium = el("div", { class: "podium" });
  const order = [1, 0, 2]; // 2.º, 1.º, 3.º
  for (const idx of order) {
    const p = players[idx];
    if (!p) continue;
    podium.append(el("div", { class: `step p${p.rank <= 3 ? p.rank : 3}` },
      el("span", { class: "ava", text: p.avatar, "aria-hidden": "true" }),
      el("span", { class: "nm", text: p.name }),
      el("div", { class: "block placard" },
        el("span", { class: "pos", text: ordinal(p.rank) }),
        el("span", { class: "sc", text: `${points(p.score)} pts` })),
    ));
  }
  const winners = players.filter((p) => p.rank === 1).map((p) => p.name);
  const title = winners.length > 1 ? `¡Empate! ${winners.join(" y ")}` : winners.length ? `¡Gana ${winners[0]}!` : "Fin de la partida";
  const rest = el("div", { class: "rest" }, players.slice(3).map((p) =>
    el("span", { class: "chip", text: `${ordinal(p.rank)} ${p.avatar} ${p.name} · ${points(p.score)}` })));
  return el("div", { class: "final" }, el("h1", { class: "enter", text: title }), podium, rest);
}

function renderOverlay(big, small) {
  document.querySelector(".overlay")?.remove();
  document.body.append(el("div", { class: "overlay", role: "alert" },
    el("div", { class: "placard" }, el("div", { class: "big", text: big }), small ? el("p", { text: small }) : null)));
}

function render(force = false) {
  if (!state) return;
  const key = `${state.phase}:${state.question ? state.question.index : -1}`;
  const body = {
    lobby: renderLobby,
    countdown: renderCountdown,
    question: renderQuestion,
    reveal: renderReveal,
    finished: renderFinished,
  }[state.phase]();
  if (key === lastKey && !force) {
    // Misma pantalla: sin animación de entrada para evitar parpadeos.
    body.querySelectorAll(".enter").forEach((n) => n.classList.remove("enter"));
    body.classList.remove("enter");
  }
  lastKey = key;
  clear(app).append(header(), body);
  document.querySelector(".overlay")?.remove();
  if (state.paused) renderOverlay("Pausa", "El anfitrión reanudará la partida en un momento.");
  else if (!socket.open) renderOverlay("Reconectando…", "");
}

function frame() {
  const t = document.getElementById("timer");
  if (t && state && state.phase === "question") {
    const frac = timer.fraction();
    t.style.setProperty("--p", frac.toFixed(4));
    const s = timer.seconds();
    t.classList.toggle("hurry", s !== null && s <= 5);
    const n = document.getElementById("timer-n");
    if (n && n.textContent !== String(s)) {
      n.textContent = String(s);
      if (s !== null && s <= 5 && s > 0 && s !== lastSecond && !state.paused) sfx.tick();
      lastSecond = s;
    }
  }
  const c = document.getElementById("count-n");
  if (c && state && state.phase === "countdown") c.textContent = String(Math.max(1, timer.seconds() ?? 1));
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
