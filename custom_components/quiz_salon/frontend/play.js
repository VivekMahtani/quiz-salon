// Mando del jugador en el móvil.
import { Countdown, GameSocket, LETTERS, clear, el, ordinal, points } from "./common.js";

const AVATARS = ["🦊", "🐼", "🐙", "🦄", "🐸", "🦁", "🐧", "🐨", "🐯", "🐵", "🦉", "🐳", "🦖", "🐝", "🦩", "🐢"];
const STORE = "quiz_salon_session";
const app = document.getElementById("app");
const statusDot = document.getElementById("status");
const timer = new Countdown();
const params = new URLSearchParams(location.search);

let state = null;
let mode = "boot"; // boot | join | in
let pendingJoin = null;
let joinError = "";
let pending = null; // {q, choice} respuesta enviada sin confirmar
let notice = "";
let form = { code: (params.get("code") || "").toUpperCase().slice(0, 4), name: "", avatar: null };
try {
  form.name = localStorage.getItem("quiz_salon_name") || "";
} catch (err) { /* modo privado */ }

function storedSession() {
  try { return localStorage.getItem(STORE); } catch (err) { return null; }
}
function storeSession(v) {
  try { v ? localStorage.setItem(STORE, v) : localStorage.removeItem(STORE); } catch (err) { /* ignorado */ }
}

const socket = new GameSocket({
  hello: () => {
    const session = storedSession();
    if (session) return { role: "player", session };
    if (pendingJoin) return { role: "player", ...pendingJoin };
    return null;
  },
  onStatus: (s) => {
    statusDot.className = `status-dot ${s}`;
    statusDot.setAttribute("aria-label", s === "open" ? "Conectado" : "Reconectando");
    if (s === "open" && !storedSession() && !pendingJoin && mode === "boot") {
      mode = "join";
      render();
    }
    if (s !== "open" && mode === "in") render();
  },
  onMessage: handle,
});
socket.connect();

function handle(msg) {
  switch (msg.type) {
    case "welcome":
      if (msg.session) storeSession(msg.session);
      pendingJoin = null;
      joinError = "";
      mode = "in";
      break;
    case "state":
      state = msg;
      timer.set(msg.remaining_ms, msg.duration_ms, msg.paused);
      if (mode === "in" && !msg.you) return; // aún sin vincular
      if (pending && (msg.phase !== "question" || !msg.question || msg.question.index !== pending.q)) pending = null;
      break;
    case "ack":
      if (msg.ref === "answer") {
        navigator.vibrate?.(30);
      }
      break;
    case "error":
      return handleError(msg);
    case "kicked":
    case "removed":
    case "left":
      storeSession(null);
      mode = "join";
      notice = msg.message || "";
      state = null;
      break;
    case "replaced":
      storeSession(null);
      mode = "join";
      notice = msg.message;
      socket.stop();
      break;
    default:
      return;
  }
  render();
}

function handleError(msg) {
  if (msg.ref === "hello" || ["session_expired", "bad_code", "name_taken", "room_full", "too_many_attempts"].includes(msg.code)) {
    if (msg.code === "session_expired") storeSession(null);
    pendingJoin = null;
    mode = "join";
    joinError = msg.message;
  } else if (msg.ref === "answer") {
    pending = null;
    notice = msg.message;
  } else {
    notice = msg.message;
  }
  render();
}

function topBar() {
  const you = state?.you;
  return el("header", { class: "play-top" },
    el("span", { class: "brand" }, "Quiz del ", el("span", { text: "Salón" })),
    you ? el("span", { class: "me" }, el("span", { class: "ava", text: you.avatar }), `${points(you.score)} pts`) : null,
  );
}

function renderJoin() {
  const err = joinError || notice;
  notice = "";
  const avatars = el("div", { class: "avatars", role: "group", "aria-label": "Elige avatar" },
    AVATARS.map((a) => el("button", {
      type: "button", text: a, "aria-pressed": String(form.avatar === a), "aria-label": `Avatar ${a}`,
      onclick: (ev) => {
        form.avatar = a;
        ev.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.textContent === a)));
      },
    })));
  const code = el("input", { class: "code", id: "code", value: form.code, maxlength: "4", autocomplete: "off", autocapitalize: "characters", inputmode: "text", required: true, "aria-label": "Código de sala" });
  const name = el("input", { id: "name", value: form.name, maxlength: "16", autocomplete: "nickname", enterkeyhint: "go", required: true, "aria-label": "Tu nombre" });
  code.addEventListener("input", () => { code.value = code.value.toUpperCase().replace(/[^A-Z]/g, ""); form.code = code.value; });
  name.addEventListener("input", () => { form.name = name.value; });
  const submit = el("button", { class: "btn", type: "submit", text: "Entrar a jugar" });
  const f = el("form", { class: "placard panel enter", novalidate: true, onsubmit: (ev) => {
    ev.preventDefault();
    const n = name.value.trim();
    if (code.value.length !== 4) { joinError = "El código de sala tiene 4 letras. Lo ves en la tele."; return render(); }
    if (!n) { joinError = "Escribe tu nombre."; return render(); }
    try { localStorage.setItem("quiz_salon_name", n); } catch (e) { /* ignorado */ }
    pendingJoin = { code: code.value, name: n, avatar: form.avatar || undefined };
    joinError = "";
    submit.disabled = true;
    submit.textContent = "Entrando…";
    if (socket.open) socket.identify();
    else socket.reconnect();
  } },
    el("h1", { text: "¡A jugar!" }),
    el("p", { text: "Escribe tu nombre y elige un avatar. Las preguntas salen en la tele." }),
    err ? el("div", { class: "err", role: "alert", text: err }) : null,
    el("label", { class: "field", for: "code" }, "Código de sala", code),
    el("label", { class: "field", for: "name" }, "Tu nombre", name),
    el("div", { class: "field" }, "Avatar", avatars),
    submit,
  );
  return f;
}

function waiting(ava, title, text, extra) {
  return el("section", { class: "wait enter" },
    ava ? el("div", { class: "ava", text: ava, "aria-hidden": "true" }) : null,
    el("h1", { text: title }),
    text ? el("p", { text }) : null,
    extra || null,
  );
}

function renderQuestion() {
  const q = state.question;
  const you = state.you;
  const chosen = pending?.q === q.index ? pending.choice : you.answer;
  const locked = chosen !== null && chosen !== undefined;
  const opts = el("div", { class: `p-opts${locked ? " locked" : ""}` },
    q.options.map((t, i) => el("button", {
      type: "button", class: `p-opt opt-${i}${chosen === i ? " chosen" : ""}`,
      disabled: locked || state.paused, "aria-pressed": String(chosen === i),
      onclick: () => answer(q.index, i),
    }, el("span", { class: "letter", text: LETTERS[i], "aria-hidden": "true" }), el("span", { text: t }))));
  const info = locked
    ? (you.answer === chosen ? `Respuesta ${LETTERS[chosen]} enviada. Mira la tele.` : "Enviando…")
    : (notice || "Toca tu respuesta. Solo vale la primera.");
  notice = "";
  return el("div", { style: { display: "contents" } },
    el("section", { class: "placard p-q" },
      el("div", { class: "meta" }, el("span", { text: `${q.category.icon} ${q.category.name}` }), el("span", { text: `${q.number}/${q.total}${q.double ? " · ×2" : ""}` })),
      el("h2", { text: q.text })),
    el("div", { class: "bar", "aria-hidden": "true" }, el("i", { id: "bar" })),
    opts,
    el("div", { class: "sent", role: "status", text: info }),
  );
}

function answer(qIndex, choice) {
  if (pending || state.you.answer !== null) return;
  pending = { q: qIndex, choice };
  if (!socket.send({ type: "answer", q: qIndex, choice })) {
    pending = null;
    notice = "Sin conexión. Reintentando…";
  }
  render();
}

function renderReveal() {
  const q = state.question;
  const you = state.you;
  let stamp;
  let detail;
  if (you.answer === null || you.answer === undefined) {
    stamp = el("div", { class: "placard stamp none", text: "Sin respuesta" });
    detail = `Era la ${LETTERS[q.correct]}: ${q.options[q.correct]}`;
  } else if (you.was_correct) {
    stamp = el("div", { class: "placard stamp ok", text: `¡Correcto! +${points(you.points)}` });
    detail = you.streak > 1 ? `Racha de ${you.streak} aciertos.` : "";
  } else {
    stamp = el("div", { class: "placard stamp ko", text: "Fallaste" });
    detail = `Era la ${LETTERS[q.correct]}: ${q.options[q.correct]}`;
  }
  return el("section", { class: "result enter" }, stamp,
    detail ? el("div", { class: "detail", text: detail }) : null,
    el("div", { class: "score", text: `Vas ${ordinal(you.rank)} de ${state.players.length} · ${points(you.score)} puntos` }));
}

function renderIn() {
  const you = state.you;
  switch (state.phase) {
    case "lobby":
      return waiting(you.avatar, `¡Estás dentro, ${you.name}!`,
        `Hay ${state.players.length} de ${state.max_players} jugadores. La partida empieza cuando el anfitrión lo decida.`,
        el("button", { class: "link", type: "button", text: "Salir de la sala", onclick: () => {
          if (confirm("¿Seguro que quieres salir de la sala?")) socket.send({ type: "leave" });
        } }));
    case "countdown": {
      const q = state.question;
      return waiting(q.category.icon, q.category.name, `Pregunta ${q.number} de ${q.total}${q.double ? " · puntos dobles" : ""}`,
        el("div", { class: "big", id: "cd", text: String(timer.seconds() ?? "") }));
    }
    case "question":
      if (you.answer === null && !state.question.options) return waiting(you.avatar, "Mira la tele", "");
      return renderQuestion();
    case "reveal":
      return renderReveal();
    case "finished":
      return waiting(you.rank === 1 ? "🏆" : you.avatar,
        you.rank === 1 ? "¡Has ganado!" : `Has quedado ${ordinal(you.rank)}`,
        `${points(you.score)} puntos · ${you.correct} ${you.correct === 1 ? "acierto" : "aciertos"}. Espera a que el anfitrión prepare otra partida.`);
    default:
      return waiting("", "…", "");
  }
}

function render() {
  clear(app).append(topBar());
  if (mode === "join") {
    app.append(renderJoin());
  } else if (mode === "in" && state && state.you) {
    app.append(renderIn());
    if (state.paused) app.append(el("div", { class: "overlay", role: "alert" }, el("div", { class: "placard" }, el("div", { class: "big", text: "Pausa" }))));
  } else {
    app.append(waiting("", "Conectando…", "Si tarda, comprueba que estás en la wifi de casa."));
  }
  if (mode === "in" && !socket.open) {
    app.append(el("div", { class: "err", role: "alert", text: "Conexión perdida. Reconectando… No cierres esta página." }));
  }
}

function frame() {
  const bar = document.getElementById("bar");
  if (bar && state?.phase === "question") bar.style.transform = `scaleX(${timer.fraction().toFixed(4)})`;
  const cd = document.getElementById("cd");
  if (cd && state?.phase === "countdown") cd.textContent = String(Math.max(1, timer.seconds() ?? 1));
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// Mantener la pantalla encendida cuando el navegador lo permite (requiere HTTPS).
async function keepAwake() {
  try {
    if ("wakeLock" in navigator && document.visibilityState === "visible") await navigator.wakeLock.request("screen");
  } catch (err) { /* no disponible */ }
}
document.addEventListener("visibilitychange", keepAwake);
keepAwake();
render();
