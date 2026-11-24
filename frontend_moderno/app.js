/* Bufete Andrade — frontend propio (vanilla JS, sin dependencias). */
"use strict";
const API = "";
const $ = (id) => document.getElementById(id);
const estado = {
  token: localStorage.getItem("legalrag_token") || "",
  perfil: null,
  convs: [], page: 1, pages: 1, total: 0,
  q: "", materia: "", archivadas: false,
  activa: null, mensajes: [], fuentes: [], caso: null,
  tema: localStorage.getItem("legalrag_tema") || "claro",
  minimizado: localStorage.getItem("legalrag_mini") === "1",
  noLeidos: 0, enviando: false,
};
document.documentElement.dataset.tema = estado.tema;

/* ---------- utilidades ---------- */
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 3500);
}
function horaCorta(iso) {  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("es-EC", { hour: "2-digit", minute: "2-digit" });
  } catch { return ""; }
}
function grupoFecha(iso) {
  const d = new Date(iso); const hoy = new Date();
  const dias = Math.floor((hoy - d) / 864e5);
  if (dias < 1 && hoy.getDate() === d.getDate()) return "Hoy";
  if (dias < 2) return "Ayer";
  if (dias < 8) return "Esta semana";
  return "Antes";
}
/* Escapa texto para inyectar en HTML (datos de BD: anti-XSS). */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
/* Markdown mínimo y seguro: escapa primero, luego aplica negrita, cursiva, listas y párrafos. */
function md(texto) {
  const lineas = esc(texto).split("\n");
  let html = "", enLista = false;
  for (const l of lineas) {
    const item = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item) { if (!enLista) { html += "<ul>"; enLista = true; } html += "<li>" + item[1] + "</li>"; continue; }
    if (enLista) { html += "</ul>"; enLista = false; }
    html += l.trim() ? "<p>" + l + "</p>" : "";
  }
  if (enLista) html += "</ul>";
  return html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/(^|[\s(])\*(?!\s)(.+?)\*/g, "$1<em>$2</em>");
}

/* ---------- recopilador de logs (cliente) ---------- */
const nuevoIdLlamada = () => Math.random().toString(16).slice(2, 14);
function reportarError(datos) {
  if (!estado.token) return;
  try {
    fetch(API + "/api/logs/cliente", {
      method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + estado.token },
      body: JSON.stringify({
        mensaje: String(datos.mensaje || "").slice(0, 500), origen: String(datos.origen || "").slice(0, 200),
        ruta_api: String(datos.ruta_api || "").slice(0, 200), estado: datos.estado ?? null,
        id_llamada: datos.id_llamada || "", pila: String(datos.pila || "").slice(0, 2000),
      }),
    }).catch(() => {});
  } catch { /* un reporte nunca debe romper la app */ }
}
window.addEventListener("error", (e) => reportarError({ mensaje: e.message, origen: `${e.filename}:${e.lineno}`, pila: e.error && e.error.stack }));
window.addEventListener("unhandledrejection", (e) => reportarError({ mensaje: (e.reason && e.reason.message) || String(e.reason), origen: "promesa", pila: e.reason && e.reason.stack }));

async function api(path, opts = {}) {
  const idLlamada = nuevoIdLlamada();
  let r;
  try {
    r = await fetch(API + path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-Id-Llamada": idLlamada, ...(opts.headers || {}),
        ...(estado.token ? { Authorization: "Bearer " + estado.token } : {}) },
    });
  } catch (err) {
    reportarError({ mensaje: "Sin conexión con el servidor", origen: "red", ruta_api: path, id_llamada: idLlamada });
    throw new Error("No hay conexión con el servidor. Revise su red e intente de nuevo.");
  }
  if (r.status === 401) { salir(); throw new Error("Sesión expirada"); }
  if (!r.ok) {
    let det = ""; try { det = (await r.json()).detail || ""; } catch { det = ""; }
    if (typeof det !== "string") det = JSON.stringify(det);
    const id = r.headers.get("X-Id-Llamada") || idLlamada;
    if (r.status >= 500) reportarError({ mensaje: det || "Error del servidor", origen: "api", ruta_api: path, estado: r.status, id_llamada: id });
    throw new Error((det || ("Error " + r.status)) + (r.status >= 500 ? ` (ref. ${id})` : ""));
  }
  if (r.status === 204) return null;
  return r.json();
}

/* ---------- login ---------- */
async function entrar(usuario, clave) {
  const cuerpo = new URLSearchParams({ username: usuario, password: clave });
  const r = await fetch(API + "/api/auth/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: cuerpo,
  });
  if (r.status === 429) { toast("Demasiados intentos. Espere unos minutos."); return; }
  if (!r.ok) throw new Error("Credenciales inválidas");
  const datos = await r.json();
  estado.token = datos.access_token;
  localStorage.setItem("legalrag_token", estado.token);
  await cargarPerfil();
}
async function cargarPerfil() {
  const datos = await api("/api/chat/salud");
  estado.perfil = { username: datos.email || datos.username || "", rol: datos.rol || "abogado",
    tenant_id: datos.tenant_id, usuario_id: datos.usuario_id };
  $("usuario-nombre").textContent = estado.perfil.username || "Usuario";
  $("usuario-rol").textContent = "Rol: " + estado.perfil.rol;
  $("btn-admin").hidden = estado.perfil.rol !== "admin";
  mostrarApp();
}
function salir() {
  estado.token = ""; estado.perfil = null; estado.activa = null;
  localStorage.removeItem("legalrag_token");
  $("vista-app").hidden = true; $("burbuja").hidden = true;
  $("vista-login").hidden = false; $("login-error").hidden = true;
}
function mostrarApp() {
  $("vista-login").hidden = true; $("vista-app").hidden = false;
  aplicarModoBurbuja();
  cargarMaterias(); listarConversaciones(true); nuevaConversacion();
  if (new URLSearchParams(location.search).get("vista") === "admin" && estado.perfil.rol === "admin") abrirAdmin();
}
$("form-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const u = $("login-usuario").value.trim(), c = $("login-clave").value;
  $("login-error").hidden = true; $("btn-entrar").disabled = true;
  try { await entrar(u, c); }
  catch (err) { const p = $("login-error"); p.textContent = err.message; p.hidden = false; }
  finally { $("btn-entrar").disabled = false; }
});
$("btn-demo").addEventListener("click", async () => {
  $("login-usuario").value = "m.vela"; $("login-clave").value = "demo-2026";
  $("form-login").requestSubmit();
});
$("btn-salir").addEventListener("click", salir);
$("btn-tema").addEventListener("click", () => {
  estado.tema = estado.tema === "claro" ? "oscuro" : "claro";
  document.documentElement.dataset.tema = estado.tema;
  localStorage.setItem("legalrag_tema", estado.tema);
});

/* ---------- barra lateral ---------- */
const MATERIAS = ["civil", "laboral", "penal", "constitucional", "transito", "familia", "movilidad humana", "servicio publico", "general"];
function cargarMaterias() {
  const s = $("filtro-materia"); s.innerHTML = '<option value="">Todas las materias</option>';
  for (const m of MATERIAS) { const o = document.createElement("option"); o.value = m; o.textContent = m[0].toUpperCase() + m.slice(1); s.appendChild(o); }
}
async function listarConversaciones(reset = false) {
  if (reset) { estado.page = 1; estado.convs = []; }
  const q = new URLSearchParams({ page: estado.page, page_size: 20 });
  if (estado.q) q.set("q", estado.q);
  if (estado.materia) q.set("materia", estado.materia);
  if (estado.archivadas) q.set("archivada", "true");
  const datos = await api("/api/conversaciones?" + q);
  estado.convs = reset ? datos.items : estado.convs.concat(datos.items);
  estado.pages = datos.pages; estado.total = datos.total;
  $("btn-mas").hidden = estado.page >= estado.pages;
  pintarConversaciones();
}
function pintarConversaciones() {
  const nav = $("lista-convs"); nav.innerHTML = "";
  if (!estado.convs.length) {
    nav.innerHTML = '<div class="vacio"><p>Sin conversaciones.<br>Pulse «+ Nueva» para empezar.</p></div>';
    return;
  }
  let grupo = "";
  for (const c of estado.convs) {
    const g = grupoFecha(c.actualizada_en || c.creada_en);
    if (g !== grupo) { grupo = g; const h = document.createElement("div"); h.className = "grupo-fecha"; h.textContent = grupo; nav.appendChild(h); }
    const b = document.createElement("button");
    b.className = "conv" + (estado.activa === c.id ? " activa" : "");
    b.innerHTML = `<span class="conv-titulo"></span><br><span class="conv-meta"></span>`;
    b.querySelector(".conv-titulo").textContent = c.titulo;
    b.querySelector(".conv-meta").textContent = `${c.materia || "general"} · ${c.mensajes} msgs`;
    b.setAttribute("aria-label", "Abrir " + c.titulo);
    b.addEventListener("click", () => abrirConversacion(c.id));
    nav.appendChild(b);
  }
}
let _tBusca;
$("buscar").addEventListener("input", (e) => {
  clearTimeout(_tBusca);
  _tBusca = setTimeout(() => { estado.q = e.target.value.trim(); listarConversaciones(true); }, 300);
});
$("filtro-materia").addEventListener("change", (e) => { estado.materia = e.target.value; listarConversaciones(true); });
$("btn-archivadas").addEventListener("click", (e) => {
  estado.archivadas = !estado.archivadas;
  e.currentTarget.setAttribute("aria-pressed", String(estado.archivadas));
  listarConversaciones(true);
});
$("btn-mas").addEventListener("click", () => { estado.page++; listarConversaciones(false); });
$("btn-nueva").addEventListener("click", () => nuevaConversacion());
$("btn-menu").addEventListener("click", () => { $("barra").classList.add("abierta"); $("fondo-barra").hidden = false; });
$("fondo-barra").addEventListener("click", cerrarBarra);
function cerrarBarra() { $("barra").classList.remove("abierta"); $("fondo-barra").hidden = true; }

/* ---------- chat ---------- */
const SUGERENCIAS = [
  "¿Cuál es el plazo para apelar una sentencia?",
  "¿Cómo se calcula una liquidación laboral?",
  "¿Qué requisitos tiene una demanda?",
  "Pídeme el resumen de un caso",
];
function pintarSugerencias() {
  const s = $("sugerencias"); s.innerHTML = "";
  if (estado.mensajes.length) return;
  for (const t of SUGERENCIAS) {
    const b = document.createElement("button"); b.textContent = t;
    b.addEventListener("click", () => { $("entrada-texto").value = t; $("form-chat").requestSubmit(); });
    s.appendChild(b);
  }
}
function nuevaConversacion() {
  estado.activa = null; estado.mensajes = []; estado.fuentes = []; estado.caso = null;
  $("chat-titulo").textContent = "Nueva conversación";
  pintarHistorial(); pintarSugerencias(); pintarFuentes(); pintarCaso();
  pintarConversaciones();
}
async function abrirConversacion(id) {
  const datos = await api("/api/conversaciones/" + id);
  estado.activa = datos.id; estado.mensajes = datos.mensajes_lista;
  estado.caso = datos.caso; estado.fuentes = [];
  $("chat-titulo").textContent = datos.titulo;
  pintarHistorial(); pintarSugerencias(); pintarFuentes(); pintarCaso();
  pintarConversaciones(); cerrarBarra();
}
function burbujaMsg(rol, contenido, hora, denegado = false, fuentes = []) {
  const d = document.createElement("div");
  d.className = "msg " + rol + (denegado ? " denegado" : "");
  const quien = rol === "user" ? "Usted" : "Asistente";
  const av = document.createElement("span"); av.className = "avatar"; av.textContent = quien;
  const tx = document.createElement("span"); tx.className = "msg-texto";
  if (rol === "assistant") tx.innerHTML = md(contenido); else tx.textContent = contenido;
  const h = document.createElement("span"); h.className = "hora"; h.textContent = hora || "";
  d.append(av, tx, h);
  if (rol === "assistant" && contenido) {
    const acc = document.createElement("div"); acc.className = "msg-acciones";
    const cp = document.createElement("button"); cp.textContent = "Copiar";
    cp.setAttribute("aria-label", "Copiar respuesta");
    cp.addEventListener("click", () => { navigator.clipboard.writeText(contenido); toast("Respuesta copiada"); });
    acc.appendChild(cp);
    if (fuentes.length) {
      const vf = document.createElement("button"); vf.textContent = "Ver fuentes";
      vf.addEventListener("click", () => document.querySelector('[data-dtab="fuentes"]').click());
      acc.appendChild(vf);
    }
    d.appendChild(acc);
  }
  return d;
}
function pintarHistorial() {
  const h = $("historial"); h.innerHTML = "";
  if (!estado.mensajes.length) {
    h.innerHTML = '<div class="vacio"><p>Haga su primera pregunta jurídica.<br>Ejemplo: plazos, liquidaciones, requisitos o estado de un caso.</p></div>';
    return;
  }
  for (const m of estado.mensajes) {
    let f = [];
    try { f = JSON.parse(m.fuentes || "[]"); } catch { f = []; }
    h.appendChild(burbujaMsg(m.rol, m.contenido, horaCorta(m.creada_en), false, f));
  }
  h.scrollTop = h.scrollHeight;
}
$("form-chat").addEventListener("submit", async (e) => {
  e.preventDefault();
  const texto = $("entrada-texto").value.trim();
  if (!texto || estado.enviando) return;
  $("entrada-texto").value = "";
  estado.enviando = true;
  try {
    if (!estado.activa) {
      const c = await api("/api/conversaciones", { method: "POST", body: JSON.stringify({}) });
      estado.activa = c.id; $("chat-titulo").textContent = c.titulo;
    }
    await api(`/api/conversaciones/${estado.activa}/mensajes`,
      { method: "POST", body: JSON.stringify({ rol: "user", contenido: texto }) });
    estado.mensajes.push({ rol: "user", contenido: texto, creada_en: new Date().toISOString(), fuentes: "" });
    pintarHistorial(); pintarSugerencias();
    const esp = document.createElement("div"); esp.className = "escribiendo"; esp.textContent = "Escribiendo";
    esp.id = "escribiendo"; $("historial").appendChild(esp);
    $("historial").scrollTop = $("historial").scrollHeight;

    const idLlamada = nuevoIdLlamada();
    const r = await fetch(API + "/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Id-Llamada": idLlamada, Authorization: "Bearer " + estado.token },
      body: JSON.stringify({ pregunta: texto }),
    });
    if (!r.ok) {
      reportarError({ mensaje: "Fallo del chat en vivo", origen: "stream", ruta_api: "/api/chat/stream", estado: r.status, id_llamada: r.headers.get("X-Id-Llamada") || idLlamada });
      throw new Error(`El asistente no pudo responder (código ${r.status}, ref. ${r.headers.get("X-Id-Llamada") || idLlamada}).`);
    }
    const lector = r.body.getReader(); const dec = new TextDecoder();
    let buf = "", respuesta = "", fuentes = [], denegado = false;
    const divIA = burbujaMsg("assistant", "", "");
    const spanTx = divIA.querySelector(".msg-texto");
    let divPuesto = false;
    const poner = () => { if (!divPuesto) { $("escribiendo")?.remove(); $("historial").appendChild(divIA); divPuesto = true; } };
    while (true) {
      const { done, value } = await lector.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const partes = buf.split("\n\n"); buf = partes.pop();
      for (const p of partes) {
        const lin = p.trim(); if (!lin.startsWith("data:")) continue;
        const ev = JSON.parse(lin.slice(5));
        if (ev.tipo === "token") { poner(); respuesta += ev.texto || ""; spanTx.innerHTML = md(respuesta); $("historial").scrollTop = 999999; }
        else if (ev.tipo === "hecho") { fuentes = ev.fuentes || []; }
        else if (ev.tipo === "denegado") { respuesta = ev.respuesta; denegado = true; }
        else if (ev.tipo === "error") { respuesta = ev.respuesta; reportarError({ mensaje: String(ev.respuesta || "Error en el stream").slice(0, 300), origen: "stream", ruta_api: "/api/chat/stream", id_llamada: idLlamada }); }
      }
    }
    $("escribiendo")?.remove();
    estado.mensajes.push({ rol: "assistant", contenido: respuesta, creada_en: new Date().toISOString(), fuentes: JSON.stringify(fuentes.map((f) => ({ hash: (f.metadata || {}).hash_archivo || "", coleccion: f.coleccion_origen || "" }))) });
    await api(`/api/conversaciones/${estado.activa}/mensajes`,
      { method: "POST", body: JSON.stringify({ rol: "assistant", contenido: respuesta, fuentes: JSON.stringify(fuentes.map((f) => ({ hash: (f.metadata || {}).hash_archivo || "", coleccion: f.coleccion_origen || "" }))) }) });
    estado.fuentes = fuentes;
    pintarHistorial(); pintarFuentes();
    listarConversaciones(true);
    if (estado.minimizado) { estado.noLeidos++; pintarContador(); }
  } catch (err) { toast(err.message); $("escribiendo")?.remove(); }
  finally { estado.enviando = false; }
});
$("btn-renombrar").addEventListener("click", async () => {
  if (!estado.activa) return;
  const t = prompt("Nuevo título:", $("chat-titulo").textContent);
  if (!t) return;
  const c = await api("/api/conversaciones/" + estado.activa, { method: "PATCH", body: JSON.stringify({ titulo: t }) });
  $("chat-titulo").textContent = c.titulo; listarConversaciones(true);
});
$("btn-archivar").addEventListener("click", async () => {
  if (!estado.activa) return;
  await api("/api/conversaciones/" + estado.activa, { method: "PATCH", body: JSON.stringify({ archivada: true }) });
  toast("Conversación archivada"); nuevaConversacion(); listarConversaciones(true);
});
$("btn-borrar").addEventListener("click", async () => {
  if (!estado.activa || !confirm("¿Borrar esta conversación?")) return;
  await api("/api/conversaciones/" + estado.activa, { method: "DELETE" });
  nuevaConversacion(); listarConversaciones(true);
});
$("btn-exportar").addEventListener("click", async () => {
  if (!estado.activa) return;
  const r = await fetch(API + `/api/conversaciones/${estado.activa}/exportar.pdf`,
    { headers: { Authorization: "Bearer " + estado.token } });
  if (!r.ok) { toast("No se pudo exportar"); return; }
  const blob = await r.blob(); const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "conversacion.pdf"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
});

/* ---------- panel derecho ---------- */
function pintarFuentes() {
  const p = $("dtab-fuentes"); p.innerHTML = "";
  if (!estado.fuentes.length) { p.innerHTML = '<div class="vacio"><p>Sin fuentes todavía.<br>Las citas de cada respuesta aparecen aquí.</p></div>'; return; }
  for (const f of estado.fuentes) {
    const md = f.metadata || {};
    const det = document.createElement("details"); det.className = "fuente";
    const sum = document.createElement("summary");
    sum.textContent = (md.titulo || md.archivo_original || "Documento") + ` (${Math.round((f.puntuacion || 0) * 100)}%)`;
    const cuerpo = document.createElement("div"); cuerpo.className = "fuente-cuerpo";
    const tx = document.createElement("div"); tx.textContent = (f.texto || "").slice(0, 1200);
    const meta = document.createElement("div"); meta.className = "fuente-meta";
    meta.textContent = `Origen: ${f.coleccion_origen || ""} · ${md.categoria || ""}${md.articulo ? " · art. " + md.articulo : ""}${md.pagina ? " · pág. " + md.pagina : ""}`;
    const acc = document.createElement("div"); acc.className = "msg-acciones";
    const cp = document.createElement("button"); cp.textContent = "Copiar artículo";
    cp.setAttribute("aria-label", "Copiar texto de la fuente");
    cp.addEventListener("click", () => { navigator.clipboard.writeText(f.texto || ""); toast("Fuente copiada"); });
    acc.appendChild(cp);
    cuerpo.append(tx, meta, acc); det.append(sum, cuerpo); p.appendChild(det);
  }
}
function pintarCaso() {
  const p = $("dtab-caso"); p.innerHTML = "";
  const c = estado.caso;
  if (!c) { p.innerHTML = '<div class="vacio"><p>Sin caso vinculado.<br> Vincule la conversación a un caso desde el título o pregúnteme por un número de proceso.</p></div>'; return; }
  p.innerHTML = `<dl class="caso-ficha">
    <dt>Número de proceso</dt><dd></dd><dt>Materia</dt><dd></dd><dt>Juzgado</dt><dd></dd>
    <dt>Estado</dt><dd></dd><dt>Cliente</dt><dd></dd></dl>`;
  const dds = p.querySelectorAll("dd");
  [c.numero_proceso, c.materia, c.juzgado, c.estado, c.cliente_nombre].forEach((v, i) => (dds[i].textContent = v || "—"));
}
document.querySelectorAll(".dtab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".dtab").forEach((x) => x.classList.remove("activo"));
  b.classList.add("activo");
  $("dtab-fuentes").hidden = b.dataset.dtab !== "fuentes";
  $("dtab-caso").hidden = b.dataset.dtab !== "caso";
}));

/* ---------- burbuja flotante ---------- */
function aplicarModoBurbuja() {
  const emb = new URLSearchParams(location.search).get("embed") === "1";
  if (estado.minimizado || emb) {
    $("vista-app").hidden = true; $("burbuja").hidden = false;
  } else {
    $("vista-app").hidden = false; $("burbuja").hidden = true;
  }
  // En modo incrustado avisamos al padre para redimensionar el iframe.
  if (emb && window.parent !== window) {
    const abierto = !$("vista-app").hidden;
    window.parent.postMessage({ fuente: "legalrag", abierto }, "*");
    if (abierto) {
      $("vista-app").style.cssText = "position:fixed;inset:0;height:100vh;";
    } else {
      $("vista-app").style.cssText = "";
    }
  }
}
$("btn-burbuja").addEventListener("click", () => {
  estado.minimizado = true; localStorage.setItem("legalrag_mini", "1");
  estado.noLeidos = 0; pintarContador(); aplicarModoBurbuja();
});
$("burbuja").addEventListener("click", () => {
  estado.minimizado = false; localStorage.setItem("legalrag_mini", "0");
  estado.noLeidos = 0; pintarContador(); aplicarModoBurbuja();
});
function pintarContador() {
  const c = $("burbuja-contador");
  c.hidden = estado.noLeidos === 0; c.textContent = estado.noLeidos > 9 ? "9+" : String(estado.noLeidos);
}
(function arrastrarBurbuja() {
  const b = $("burbuja"); let x0 = 0, y0 = 0, izq = 0, sup = 0, moviendo = false;
  b.addEventListener("pointerdown", (e) => {
    moviendo = true; x0 = e.clientX; y0 = e.clientY;
    const r = b.getBoundingClientRect(); izq = r.left; sup = r.top;
    b.setPointerCapture(e.pointerId);
  });
  b.addEventListener("pointermove", (e) => {
    if (!moviendo) return;
    const dx = e.clientX - x0, dy = e.clientY - y0;
    if (Math.abs(dx) + Math.abs(dy) > 6) {
      b.style.left = izq + dx + "px"; b.style.top = sup + dy + "px";
      b.style.right = "auto"; b.style.bottom = "auto"; b.dataset.mover = "1";
    }
  });
  b.addEventListener("pointerup", (e) => {
    if (b.dataset.mover === "1") { e.stopPropagation(); e.preventDefault(); }
    delete b.dataset.mover; moviendo = false;
  }, true);
  b.addEventListener("click", (e) => { if (b.dataset.mover === "1") { e.stopPropagation(); e.preventDefault(); } }, true);
})();

/* ---------- admin ---------- */
$("btn-admin").addEventListener("click", abrirAdmin);
function abrirAdmin() {
  $("panel-chat").hidden = true; $("panel-admin").hidden = false;
  if (window.innerWidth <= 1100) document.querySelector(".panel-der").style.display = "none";
  cargarAdminTab("usuarios");
}
$("btn-volver-chat").addEventListener("click", () => {
  $("panel-chat").hidden = false; $("panel-admin").hidden = true;
  document.querySelector(".panel-der").style.display = "";
});
document.querySelectorAll("#panel-admin .tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#panel-admin .tab").forEach((x) => x.classList.remove("activo"));
  b.classList.add("activo"); cargarAdminTab(b.dataset.tab);
}));
$("chat-titulo").addEventListener("dblclick", () => {
  $("panel-chat").hidden = false; $("panel-admin").hidden = true;
});
async function cargarAdminTab(tab) {
  for (const t of ["usuarios", "permisos", "docs", "casos", "auditoria", "metricas", "logs"])
    $("tab-" + t).hidden = t !== tab;
  const fn = { usuarios: tabUsuarios, permisos: tabPermisos, docs: tabDocs, casos: tabCasos, auditoria: tabAuditoria, metricas: tabMetricas, logs: tabLogs }[tab];
  if (fn) await fn();
}
async function tabUsuarios() {
  const p = $("tab-usuarios");
  const datos = await api("/api/admin/permisos/matriz");
  let h = `<h3>Usuarios (${datos.usuarios.length})</h3>
  <table><tr><th>Usuario</th><th>Rol</th><th>Grupo</th></tr>`;
  for (const u of datos.usuarios) h += `<tr><td></td><td>${u.rol}</td><td>${u.grupo || "—"}</td></tr>`;
  h += `</table><h3>Crear usuario</h3>
  <div class="form-linea"><input id="nu-nombre" placeholder="usuario" aria-label="Usuario">
  <input id="nu-clave" type="password" placeholder="contraseña (mín. 8)" aria-label="Contraseña">
  <select id="nu-rol" aria-label="Rol"><option value="abogado">abogado</option><option value="cliente">cliente</option><option value="admin">admin</option></select>
  <input id="nu-grupo" placeholder="grupo (opcional)" aria-label="Grupo">
  <button class="btn-primario btn-chico" id="nu-crear">Crear</button></div><div id="nu-msg"></div>`;
  p.innerHTML = h;
  const tds = p.querySelectorAll("td:first-child");
  datos.usuarios.forEach((u, i) => (tds[i].textContent = u.username));
  $("nu-crear").addEventListener("click", async () => {
    try {
      await api("/api/admin/usuarios", { method: "POST", body: JSON.stringify({
        username: $("nu-nombre").value.trim(), password: $("nu-clave").value,
        rol: $("nu-rol").value, tenant_id: estado.perfil.tenant_id, grupo: $("nu-grupo").value.trim() || null }) });
      $("nu-msg").textContent = "Usuario creado."; tabUsuarios();
    } catch (err) { $("nu-msg").textContent = err.message; }
  });
}
async function tabPermisos() {
  const p = $("tab-permisos");
  const [matriz, cols] = await Promise.all([
    api("/api/admin/permisos/matriz"), api("/api/admin/colecciones")]);
  const porUsuario = {};
  for (const r of matriz.permisos) {
    if (r.recurso_tipo !== "coleccion") continue;
    const k = "u" + r.usuario_id;
    (porUsuario[k] = porUsuario[k] || {})[r.recurso_id] = r;
  }
  let h = `<h3>Matriz usuario × colección</h3><p style="color:var(--texto-2);font-size:13px">Solo el administrador cambia permisos. El filtro se aplica dentro de la búsqueda: sin permiso no hay texto.</p>
  <table><tr><th>Usuario</th>${cols.colecciones.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  matriz.usuarios.forEach((u, i) => {
    h += `<tr><td>${esc(u.username)}<br><small>${esc(u.rol)}${u.grupo ? " · " + esc(u.grupo) : ""}</small></td>`;
    for (const c of cols.colecciones) {
      const r = (porUsuario["u" + u.usuario_id] || {})[c];
      const on = r ? !!r.puede_ver : (u.rol === "admin" ? true : (u.rol === "abogado" && (c === "leyes_globales" || c === "casos_user_" + u.usuario_id)));
      h += `<td><label class="interruptor"><input type="checkbox" data-u="${u.usuario_id}" data-c="${esc(c)}" data-id="${r ? r.permiso_id : ""}" ${on ? "checked" : ""} ${u.rol === "admin" ? "disabled" : ""} aria-label="Permiso de ${esc(u.username)} sobre ${esc(c)}"><span class="deslizador"></span></label></td>`;
    }
    h += "</tr>";
  });
  h += `</table><h3>Conceder documento concreto (por hash)</h3>
  <div class="form-linea"><input id="pd-hash" placeholder="hash del documento" aria-label="Hash">
  <input id="pd-user" placeholder="usuario o grupo:valor" aria-label="Destino">
  <button class="btn-primario btn-chico" id="pd-dar">Conceder</button></div><div id="pd-msg"></div>`;
  p.innerHTML = h;
  p.querySelectorAll("input[type=checkbox][data-u]").forEach((chk) => chk.addEventListener("change", async () => {
    const u = +chk.dataset.u, c = chk.dataset.c, id = chk.dataset.id;
    try {
      if (chk.checked && !id) {
        await api("/api/admin/permisos", { method: "POST", body: JSON.stringify({ usuario_id: u, recurso_tipo: "coleccion", recurso_id: c }) });
      } else if (!chk.checked && id) {
        await api("/api/admin/permisos/" + id, { method: "DELETE" });
      } else if (!chk.checked && !id) {
        await api("/api/admin/permisos", { method: "POST", body: JSON.stringify({ usuario_id: u, recurso_tipo: "coleccion", recurso_id: c, puede_ver: false }) });
      } else if (chk.checked && id) {
        await api("/api/admin/permisos/" + id, { method: "DELETE" });
        await api("/api/admin/permisos", { method: "POST", body: JSON.stringify({ usuario_id: u, recurso_tipo: "coleccion", recurso_id: c }) });
      }
      toast("Permiso actualizado"); tabPermisos();
    } catch (err) { toast(err.message); chk.checked = !chk.checked; }
  }));
  $("pd-dar").addEventListener("click", async () => {
    const [tipo, valor] = ($("pd-user").value.trim().split(":").length > 1)
      ? $("pd-user").value.trim().split(/:(.+)/) : ["usuario", $("pd-user").value.trim()];
    try {
      const cuerpo = { recurso_tipo: "documento", recurso_id: $("pd-hash").value.trim() };
      if (tipo === "grupo") cuerpo.grupo = valor; else cuerpo.usuario_id = +valor || null;
      if (!cuerpo.usuario_id && !cuerpo.grupo) throw new Error("Indique usuario o grupo:valor");
      await api("/api/admin/permisos", { method: "POST", body: JSON.stringify(cuerpo) });
      $("pd-msg").textContent = "Documento concedido."; tabPermisos();
    } catch (err) { $("pd-msg").textContent = err.message; }
  });
}
async function tabDocs() {
  const p = $("tab-docs");
  const datos = await api("/api/admin/documentos");
  let h = `<h3>Subir documento (PDF)</h3>
  <div class="form-linea"><input id="doc-titulo" placeholder="Título" aria-label="Título">
  <select id="doc-cat" aria-label="Categoría"><option>Leyes</option><option>Jurisprudencia</option><option>Expediente</option></select>
  <input id="doc-dest" placeholder='Destino: global o ID de abogado' aria-label="Destino">
  <input id="doc-file" type="file" accept="application/pdf" aria-label="Archivo PDF">
  <button class="btn-primario btn-chico" id="doc-subir">Subir</button></div><div id="doc-msg"></div>
  <h3>Ingestados (${datos.items.length})</h3><table><tr><th>Archivo</th><th>Categoría</th><th>Destino</th><th></th></tr>`;
  for (const d of datos.items) h += `<tr><td></td><td>${esc(d.categoria)}</td><td>${esc(d.destino)}</td><td><button class="btn-secundario btn-chico peligro" data-borrar="${d.id}">Borrar</button></td></tr>`;
  h += "</table>";
  p.innerHTML = h;
  datos.items.forEach((d, i) => (p.querySelectorAll("td")[i * 4].textContent = d.nombre));
  $("doc-subir").addEventListener("click", async () => {
    const f = $("doc-file").files[0];
    if (!f) { $("doc-msg").textContent = "Adjunte un PDF."; return; }
    const fd = new FormData();
    fd.append("file", f); fd.append("categoria", $("doc-cat").value);
    fd.append("destino_tenant_id", $("doc-dest").value.trim() || "global");
    fd.append("titulo", $("doc-titulo").value.trim() || f.name);
    const r = await fetch(API + "/api/admin/documentos", { method: "POST", headers: { Authorization: "Bearer " + estado.token }, body: fd });
    $("doc-msg").textContent = r.ok ? "Documento indexado." : "Error: " + (await r.text()).slice(0, 200);
    if (r.ok) tabDocs();
  });
  p.querySelectorAll("[data-borrar]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("¿Borrar documento y sus vectores?")) return;
    await api("/api/admin/documentos/" + b.dataset.borrar, { method: "DELETE" });
    toast("Documento borrado"); tabDocs();
  }));
}
async function tabCasos() {
  const p = $("tab-casos");
  const datos = await api("/api/casos");
  let h = `<h3>Casos (${datos.total})</h3><table><tr><th>Proceso</th><th>Materia</th><th>Estado</th><th>Cliente</th></tr>`;
  for (const c of datos.items) h += `<tr><td>${esc(c.numero_proceso)}</td><td>${esc(c.materia)}</td><td>${esc(c.estado)}</td><td></td></tr>`;
  h += `</table><h3>Crear caso</h3><div class="form-linea">
  <input id="cs-num" placeholder="N.º proceso" aria-label="Número"><input id="cs-mat" placeholder="materia" aria-label="Materia">
  <input id="cs-cli" placeholder="cliente" aria-label="Cliente"><button class="btn-primario btn-chico" id="cs-crear">Crear</button></div><div id="cs-msg"></div>`;
  p.innerHTML = h;
  const tds = p.querySelectorAll("td:nth-child(4)");
  datos.items.forEach((c, i) => (tds[i].textContent = c.cliente_nombre || "—"));
  $("cs-crear").addEventListener("click", async () => {
    try {
      await api("/api/casos", { method: "POST", body: JSON.stringify({ numero_proceso: $("cs-num").value.trim(), materia: $("cs-mat").value.trim() || "civil", cliente_nombre: $("cs-cli").value.trim() }) });
      $("cs-msg").textContent = "Caso creado."; tabCasos();
    } catch (err) { $("cs-msg").textContent = err.message; }
  });
}
async function tabAuditoria() {
  const p = $("tab-auditoria");
  const datos = await api("/api/admin/auditoria?page=1&page_size=20");
  let h = `<h3>Auditoría (últimas ${datos.items.length} de ${datos.total})</h3>
  <table><tr><th>Fecha</th><th>Consulta / acción</th><th>Tokens</th></tr>`;
  for (const a of datos.items) h += `<tr><td>${esc((a.timestamp || "").slice(0, 16).replace("T", " "))}</td><td></td><td>${a.tokens_prompt + a.tokens_completion}</td></tr>`;
  h += "</table>";
  p.innerHTML = h;
  const tds = p.querySelectorAll("td:nth-child(2)");
  datos.items.forEach((a, i) => (tds[i].textContent = (a.pregunta || "").slice(0, 120)));
}
async function tabMetricas() {
  const p = $("tab-metricas");
  const m = await api("/api/admin/metricas");
  const maxD = Math.max(1, ...m.consultas_por_dia.map((d) => d.consultas));
  const maxT = Math.max(1, ...m.temas_mas_preguntados.map((t) => t.conversaciones));
  let h = `<h3>Consultas por día (30 días): ${m.consultas_totales} totales · ${m.tokens_prompt + m.tokens_completion} tokens</h3>`;
  for (const d of m.consultas_por_dia.slice(-14))
    h += `<div class="barra-metrica"><span class="etiqueta">${esc(d.dia.slice(5))}</span><span class="barra-ext"><span class="barra-int" style="display:block;width:${Math.round((d.consultas / maxD) * 100)}%"></span></span><span class="valor">${d.consultas}</span></div>`;
  h += `<h3>Temas más preguntados</h3>`;
  for (const t of m.temas_mas_preguntados)
    h += `<div class="barra-metrica"><span class="etiqueta">${esc(t.materia)}</span><span class="barra-ext"><span class="barra-int" style="display:block;width:${Math.round((t.conversaciones / maxT) * 100)}%"></span></span><span class="valor">${t.conversaciones}</span></div>`;
  h += `<h3>Documentos más citados</h3>`;
  h += m.documentos_mas_citados.length
    ? m.documentos_mas_citados.map((d) => `<div class="barra-metrica"><span class="etiqueta">${esc(d.titulo)}</span><span class="barra-ext"><span class="barra-int" style="display:block;width:${Math.round((d.citas / Math.max(1, m.documentos_mas_citados[0].citas)) * 100)}%"></span></span><span class="valor">${d.citas}</span></div>`).join("")
    : "<p>Sin citas registradas todavía.</p>";
  p.innerHTML = h;
}

async function tabLogs() {
  const p = $("tab-logs");
  p.innerHTML = `<h3>Registro de llamadas y errores</h3>
  <div class="form-linea"><input id="lg-q" placeholder="Buscar (ruta, error, ref.)" aria-label="Buscar en logs">
  <label><input type="checkbox" id="lg-err" checked> Solo errores</label>
  <button class="btn-primario btn-chico" id="lg-ver">Actualizar</button>
  <button class="btn-secundario btn-chico" id="lg-csv">Descargar JSON</button></div>
  <div id="lg-tabla"></div>`;
  let ultimo = [];
  const cargar = async () => {
    const q = encodeURIComponent($("lg-q").value.trim());
    const d = await api(`/api/admin/logs?limite=200&solo_errores=${$("lg-err").checked}&q=${q}`);
    ultimo = d.items;
    const cont = $("lg-tabla");
    if (!d.items.length) { cont.innerHTML = '<div class="vacio"><p>Sin eventos con ese filtro.</p></div>'; return; }
    let h = "<table><tr><th>Hora</th><th>Nivel</th><th>Llamada</th><th>Estado</th><th>ms</th><th>Detalle</th></tr>";
    for (const e of d.items) {
      const detalle = e.error ? `${e.error}: ${e.mensaje || ""}` : (e.mensaje || e.origen || "");
      h += `<tr><td>${esc((e.ts || "").slice(11, 19))}</td><td>${esc(e.nivel)}</td><td>${esc((e.metodo || e.tipo || "") + " " + (e.ruta || e.ruta_api || ""))}<br><small>ref. ${esc(e.id || e.id_llamada || "—")}</small></td><td>${esc(e.estado ?? "")}</td><td>${esc(e.ms ?? "")}</td><td>${esc(String(detalle).slice(0, 200))}</td></tr>`;
    }
    cont.innerHTML = h + "</table>";
  };
  $("lg-ver").addEventListener("click", () => cargar().catch((e) => toast(e.message)));
  $("lg-csv").addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(ultimo, null, 2)], { type: "application/json" }));
    a.download = "logs.json"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  });
  await cargar();
}

/* ---------- init ---------- */
if (estado.token) cargarPerfil().catch(() => salir());
