/* BVG Craft — интерфейсът. Говори с Python през window.pywebview.api. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let api = null;
let S = null;               // последното състояние от сървъра
let logSeen = 0;
let logFilter = "all";
let formsFilled = false;

const SPIRIT_TEXT = {
  keeper: "Водещият. Обявява събития, задава гатанки, раздава награди.",
  builder: "Строи каквото поискаш — къщи, кули, мостове.",
  trickster: "Пакостник. Кокошки на главата, левитация, номера.",
  trader: "Разменя предмети. Пазари се жестоко.",
};

const EVENT_TEXT = {
  meteors: "Метеорити падат около играчите. Вътре може да има руда.",
  bounty: "Един играч свети. Който го победи, печели.",
  treasure: "Скрито съкровище. Подсказки топло-студено.",
  undead: "Полунощ, буря и чудовища. Наградени са оцелелите.",
  gravity: "40 секунди скачане до небето.",
  chickens: "Вали пилета. Нищо повече.",
  giant: "Гигант три пъти по-голям. Който го повали, печели.",
  riddle: "Гатанка в чата. Първият верен отговор печели.",
  race: "Координати и старт. Първият стигнал печели.",
  bloodmoon: "Червена нощ, вълни чудовища. Оцелелите получават слава.",
  goldrain: "От небето валят злато, изумруди и диаманти.",
  invasion: "Разбойници нападат най-близкото село. Защитниците — слава.",
  boss: "Бос с име и три фази.",
};

const EVENT_NAMES = {
  meteors: "Метеоритен дъжд", bounty: "Лов на глави", treasure: "Съкровище",
  undead: "Нощ на мъртвите", gravity: "Гравитацията се счупи",
  chickens: "Вали пилета", giant: "Гигантът", riddle: "Гатанка",
  race: "Състезание", bloodmoon: "Кървава луна", goldrain: "Златен дъжд",
  invasion: "Нашествие", boss: "Бос",
};

const CHAOS_TEXT = [
  "Духовете само отговарят. Без история, гости и изненади.",
  "Спокойно: режисьорът мисли по-рядко, духовете идват на ~4 минути.",
  "Живо: история, босове, задачи, гости на ~2 минути. Препоръчително.",
  "Хаос: режисьорът е навсякъде, гости всяка минута, много номера.",
];

/* ---------- помощни ---------- */
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

async function call(name, ...args) {
  try {
    const r = await api[name](...args);
    if (r && r.ok === false && r.error) toast(r.error, true);
    else if (r && r.message) toast(r.message, r.ok === false);
    return r;
  } catch (e) {
    toast("Грешка: " + e, true);
    return null;
  }
}

const HEAD_COLORS = ["#c08552", "#7a9e5c", "#5c7fa8", "#a8695c", "#8f6fa8",
  "#b89a54", "#5c9ea0", "#9c6b84"];
function head(name) {
  let h = 2166136261;                         // FNV-1a — разпръсква по-равно
  for (const ch of String(name)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
  return `<span class="head" style="--h:${HEAD_COLORS[h % HEAD_COLORS.length]}"></span>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* true → зелено, false → червено, null → сиво (не е проверено) */
const cls = ok => ok === true ? "ok" : ok === false ? "bad" : "wait";
const mark = ok => ok === true ? "✓" : ok === false ? "✗" : "–";

function ago(sec) {
  if (sec == null) return "";
  if (sec < 60) return `${sec} сек`;
  if (sec < 3600) return `${Math.round(sec / 60)} мин`;
  return `${Math.round(sec / 3600)} ч`;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function fillSelect(sel, items, empty = "") {
  // items: [[стойност, текст]]; пресъздава само ако списъкът се е сменил
  const sig = JSON.stringify(items);
  if (sel.dataset.sig === sig) return;
  const keep = sel.value;
  sel.dataset.sig = sig;
  sel.innerHTML = items.length
    ? items.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("")
    : `<option value="">${esc(empty)}</option>`;
  if (items.some(([v]) => v === keep)) sel.value = keep;
}

function playerItems(onlyOnline = false) {
  const online = S.online || [];
  const rest = onlyOnline ? [] : (S.config.whitelist || []).filter(n => !online.includes(n));
  return [...online.map(n => [n, `${n} · в света`]), ...rest.map(n => [n, n])];
}

function rosterMap() {
  return Object.fromEntries((S.roster || []).map(r => [r.name, r]));
}

/* ---------- навигация ---------- */
$$(".nav").forEach(b => b.addEventListener("click", () => {
  $$(".nav").forEach(n => n.classList.toggle("is-active", n === b));
  $$(".view").forEach(v => v.classList.toggle("is-active",
    v.id === "view-" + b.dataset.view));
  if (b.dataset.view === "server") { loadVersions(); loadWorld(); }
}));

/* ---------- рисуване ---------- */
function renderPower() {
  const lamp = $("#lamp"), txt = $("#powerText"), btn = $("#powerBtn");
  lamp.className = "lamp" + (S.ready ? " on" : S.running ? " starting" : "");
  txt.textContent = S.ready ? `Върви — ${S.online.length} в света`
    : S.running ? "Пуска се..." : "Спрян";
  btn.textContent = S.running ? "Спри сървъра" : "Пусни сървъра";
  btn.classList.toggle("btn-primary", !S.running);
  btn.classList.toggle("btn-danger", S.running);
}

function renderHome() {
  $("#homeTitle").textContent = S.ready ? "Светът е буден"
    : S.running ? "Светът се събужда" : "Светът спи";
  $("#homeLede").textContent = S.ready
    ? `Minecraft ${S.mc_version || ""} — ${S.online.length} от ${S.config.max_players} играчи.`
    : S.running ? "Генерира се светът. Първия път отнема минута-две."
    : "Пусни сървъра, за да оживеят духовете.";

  const roam = S.config.spirits_roam !== false && S.config.chaos > 0;
  $("#banners").innerHTML = Object.entries(S.characters).map(([k, c]) => `
    <article class="banner c-${k} ${c.enabled || roam ? "" : "dim"}">
      <div class="glyph" aria-hidden="true"></div>
      <h3>${esc(c.name)}</h3>
      <p>${esc(SPIRIT_TEXT[k] || "")}</p>
      <p class="where ${c.enabled || roam ? "placed" : ""}">
        ${visiting(k) ? `Сега е при ${esc(visiting(k))}` : c.enabled ? "На постоянно място" : roam ? "Обикаля света" : "Още не е призован"}</p>
    </article>`).join("");

  const R = rosterMap();
  $("#onlineList").innerHTML = S.online.length
    ? S.online.map(p => `<li>${head(p)}<span>${esc(p)}</span>${
        R[p] && R[p].ip ? `<small class="ip" title="${esc(R[p].ip_kind || "")}">${esc(R[p].ip)}</small>` : ""}</li>`).join("")
    : `<li class="empty">${S.ready ? "Никой не е влязъл." : S.running ? "Сървърът още зарежда." : "Сървърът не върви."}</li>`;

  const ev = S.event;
  const table = ev && ev.standings && ev.standings.length
    ? `<ol>${ev.standings.map(([n, v]) => `<li>${esc(n)} — ${v}${ev.amount ? ` / ${ev.amount}` : ""}</li>`).join("")}</ol>` : "";
  $("#eventNow").innerHTML = ev
    ? `<strong>${esc(EVENT_NAMES[ev.key] || ev.title)}</strong><span>Остават ${ev.left} секунди</span>${table}`
    : `<p class="empty">Нищо не върви в момента.</p>`;
  $("#randomEvent").textContent = ev ? "Спри събитието" : "Изненадай ги";
  $("#randomEvent").disabled = !S.ready || (!ev && !S.online.length);
}

function visiting(key) {
  const a = S.visits && S.visits.active;
  return a && S.characters[key] && a.spirit === S.characters[key].name ? a.player : null;
}

function renderStory() {
  const st = S.story || {};
  $("#sagaTitle").textContent = st.title ? `„${st.title}“${st.chapter ? ` — глава ${st.chapter}` : ""}` : "Още не е започнала";
  $("#sagaGoal").textContent = st.title ? (st.goal ? `Цел: ${st.goal}` : "")
    : "Режисьорът ще я измисли, щом някой влезе.";
  $("#chronList").innerHTML = (st.chronicle || []).map(c =>
    `<li><span class="when">преди ${ago(c.ago)}</span><span>${esc(c.text)}</span></li>`).join("");

  const v = S.visits || {};
  const rows = [];
  if (v.active) rows.push(`<li class="live"><b>${esc(v.active.spirit)}</b> е при ${esc(v.active.player)} <small>още ${v.active.left} сек</small></li>`);
  for (const r of (v.recent || []).slice(v.active ? 1 : 0, 5))
    rows.push(`<li><b>${esc(r.spirit)}</b> → ${esc(r.player)} <small>преди ${ago(r.ago)}</small><span class="say">${esc(r.say)}</span></li>`);
  $("#visitFeed").innerHTML = rows.join("") || `<li class="empty">${S.config.spirits_roam === false ? "Изключено в Събития." : "Още никой не е идвал."}</li>`;

  const q = S.quests || { active: [], done: [] };
  const qs = q.active.map(x => `<li data-q="${x.id}"><b>${esc(x.player)}</b>: ${esc(x.kind.toLowerCase())} ${esc(x.label)}
      <small>${x.progress}/${x.amount} · за ${esc(x.giver)} · ${ago(Math.max(0, x.left))}</small>
      <button class="link-danger" data-qc="${x.id}" type="button">махни</button></li>`)
    .concat(q.done.slice(0, 3).map(x => `<li class="done"><b>${esc(x.player)}</b> ✓ ${esc(x.label)} <small>за ${esc(x.giver)}, преди ${ago(x.ago)}</small></li>`));
  const qf = $("#questFeed");
  if (!qf.contains(document.activeElement)) {
    qf.innerHTML = qs.join("") || `<li class="empty">Няма задачи.</li>`;
    $$("#questFeed [data-qc]").forEach(b => b.addEventListener("click",
      () => call("quest_cancel", parseInt(b.dataset.qc, 10)).then(refresh)));
  }
  $("#fameList").innerHTML = (S.fame || []).map(f =>
    `<li><span class="name">${esc(f.name)}</span><span class="rank r-${esc(f.color)}">${esc(f.rank)}</span><b>${f.fame}</b></li>`).join("")
    || `<li class="empty">Още никой няма слава.</li>`;
}

function renderHealth() {
  $("#health").innerHTML = (S.health || []).map(h => `
    <div class="h-item ${cls(h.ok)}" title="${esc(h.detail)}">
      <span class="dot"></span><b>${esc(h.name)}</b><small>${esc(h.detail)}</small>
    </div>`).join("");
  const err = $("#stateError");
  err.hidden = !S.state_error;
  if (S.state_error) err.textContent = `Таблото не успя да се обнови: ${S.state_error}. Показвам последното известно.`;
}

function renderAi() {
  const h = (S.health || []).find(x => x.name === "Изкуствен разум") || {};
  const a = S.ai || {};
  const title = h.ok === true ? "Работи" : h.ok === false ? "Не работи" : "Още не е питан";
  const stats = a.ok || a.err
    ? `${plural(a.ok, "отговор", "отговора")}, ${plural(a.err, "грешка", "грешки")}${a.today ? `, днес ${a.today}` : ""}${a.last_ok_ago != null ? `, последен преди ${ago(a.last_ok_ago)}` : ""}`
    : "";
  let tip = "";
  const d = String(h.detail || "");
  const now = a.provider && a.model ? `Сега отговаря ${a.provider} / ${a.model}.` : "";
  const line = h.ok === true ? [now, stats].filter(Boolean).join(" · ") : [d, stats].filter(Boolean).join(" · ");
  if (/Няма ключ/.test(d))
    tip = "Без ключ режисьорът разказва готови саги, а духовете ползват готови реплики. За истински разум сложи безплатен ключ — Gemini или Groq.";
  else if (/лимит/i.test(d))
    tip = "Безплатният лимит на една услуга свърши. Добави още една (Groq, Mistral) — програмата сама ще мине на нея.";
  else if (h.ok === false)
    tip = "Натисни „Провери всички ключове“, за да видиш коя услуга не отговаря.";
  else if ((a.configured || 0) === 1)
    tip = "Работи с една услуга. Добави и втора безплатна, за да не спира, когато лимитът свърши.";
  const box = $("#aiStatus");
  box.className = "ai-status " + cls(h.ok);
  box.innerHTML = `<span class="dot"></span><div><b>${title}</b>
    <small>${esc(line)}</small>${tip ? `<small class="tip">${esc(tip)}</small>` : ""}</div>`;

  for (const p of a.providers || []) {
    const st = $(`[data-s="${p.key}"]`), info = $(`[data-i="${p.key}"]`), clr = $(`[data-clear="${p.key}"]`);
    if (!st) continue;
    const state = !p.set ? ["", "няма ключ"]
      : p.bad_key ? ["bad", "ключът не е приет"]
      : p.ok && (!p.err || p.ok >= p.err) ? ["ok", "работи"]
      : p.err && !p.ok ? ["bad", "грешка"]
      : p.models ? ["ok", "готов"] : ["", "сложен"];
    st.className = "pstate " + state[0];
    st.textContent = state[1];
    clr.hidden = !p.set;
    const bits = [];
    if (p.set && p.model) bits.push(`отговаря ${p.model} (${p.ok})`);
    else if (p.set && p.top && p.top.length) bits.push(`ще ползва ${p.top.slice(0, 2).join(", ")}`);
    if (p.set && p.models) bits.push(`${p.models} модела`);
    if (p.set && p.last_error) bits.push(p.last_error);
    info.textContent = bits.join(" · ");
    info.classList.toggle("bad-text", !!(p.set && (p.bad_key || (p.err && !p.ok))));
  }

  const models = (a.models || []);
  const items = [["auto", "Автоматично — препоръчително"], ...models.map(m => [m, m])];
  const chosen = a.chosen || S.config.gemini_model || "auto";
  if (chosen !== "auto" && !models.includes(chosen)) items.push([chosen, chosen + " (ръчно)"]);
  const sel = $("#gemini_model");
  if (document.activeElement !== sel) { fillSelect(sel, items); if (!sel.dataset.touched) sel.value = chosen; }
  $("#modelHint").textContent = models.length ? `Налични ${models.length} модела на Gemini. Ще избере сам.`
    : a.list_error && S.config.gemini_key_set ? `Списъкът с модели не се взе: ${a.list_error}`
    : "Програмата сама избира работещ модел и сменя, ако някой спре.";
}

function renderServer() {
  const ver = S.mc_version;
  const checks = [
    { ok: !!S.paper, title: "Сървър",
      text: S.paper ? `Paper ${ver}` : "Не е свален",
      action: S.paper ? "Свали наново" : "Свали Paper", fn: "get_paper" },
    { ok: S.java_ok, title: "Java",
      text: S.java ? `Java ${S.java}${S.java_ok ? "" : ` — трябва ${S.java_needed}`}`
        : `Няма — трябва ${S.java_needed}`,
      action: S.java_ok ? null : `Свали Java ${S.java_needed}`, fn: "get_java" },
    { ok: !!ver, title: "Лаунчер",
      text: ver ? `Избери версия ${ver}` : "Първо свали сървъра", action: null },
  ];
  $("#checks").innerHTML = checks.map(c => `
    <div class="check ${c.ok ? "ok" : "bad"}">
      <b>${c.title}</b><span>${esc(c.text)}</span>
      ${c.action ? `<button class="btn btn-sm" data-fn="${c.fn}">${c.action}</button>` : ""}
    </div>`).join("");
  $$("#checks [data-fn]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    const ver = $("#mc_version").value || null;
    // Не пращаме празен аргумент — pywebview го превръща в null
    if (b.dataset.fn === "get_paper") await call("get_paper", ver);
    else await call(b.dataset.fn);
  }));

  const p = S.progress;
  $("#progress").hidden = !p;
  if (p) {
    $("#bar").style.width = Math.round((p.value || 0) * 100) + "%";
    $("#progressText").textContent = `${p.verb || "Свалям"} ${p.what}... ${Math.round((p.value || 0) * 100)}%`;
  }
  syncImport();
}

function renderPlayers() {
  const wl = S.config.whitelist || [];
  const admins = S.config.admins || [];
  // Сървърът праща готов списък с IP; ако е по-стара версия — смятаме тук
  const list = S.roster || [...new Set([...wl, ...S.online])].map(n => ({
    name: n, online: S.online.includes(n), whitelisted: wl.includes(n),
    admin: admins.includes(n), ip: null, ip_kind: null, deaths: 0, visits: 0 }));
  list.sort((a, b) => (b.online - a.online) || a.name.localeCompare(b.name));
  $("#roster").innerHTML = list.length ? list.map(r => {
    const n = r.name;
    const meta = [
      r.ip ? `IP <span class="ip">${esc(r.ip)}</span><span class="kind">(${esc(r.ip_kind || "")})</span>`
           : "IP — още не е влизал",
      r.visits ? plural(r.visits, "влизане", "влизания") : "",
      r.deaths ? plural(r.deaths, "смърт", "смърти") : "",
      r.fame ? `${r.fame} слава` : "",
    ].filter(Boolean).join(" · ");
    return `
    <li class="${r.online ? "is-live" : ""}">
      ${head(n)}
      <span><span class="name">${esc(n)}</span><span class="tags">
        ${r.online ? '<span class="tag live">в света</span>' : ""}
        ${r.admin ? '<span class="tag admin">OP</span>' : ""}
        ${r.rank ? `<span class="tag rank">${esc(r.rank)}</span>` : ""}
        ${!r.whitelisted ? '<span class="tag">не е в списъка</span>' : ""}
      </span><span class="meta">${meta}</span></span>
      <span class="row-actions">
        ${r.online ? `<button class="btn btn-sm" data-a="prank" data-n="${esc(n)}">Пусни номер</button>` : ""}
        <button class="btn btn-sm" data-a="fame" data-n="${esc(n)}">+50 слава</button>
        <button class="btn btn-sm" data-a="admin" data-n="${esc(n)}">
          ${r.admin ? "Махни OP" : "Дай OP"}</button>
        ${r.whitelisted ? `<button class="btn btn-sm btn-danger" data-a="remove" data-n="${esc(n)}">Махни</button>`
          : `<button class="btn btn-sm" data-a="add" data-n="${esc(n)}">Добави в списъка</button>`}
      </span>
    </li>`;
  }).join("")
    : `<li><span></span><span class="empty">Списъкът е празен. Добави първо себе си.</span><span></span></li>`;

  $$("#roster [data-a]").forEach(b => b.addEventListener("click", () => {
    const n = b.dataset.n;
    if (b.dataset.a === "remove") call("whitelist_remove", n).then(refresh);
    if (b.dataset.a === "add") call("whitelist_add", n).then(refresh);
    if (b.dataset.a === "admin") call("toggle_admin", n).then(refresh);
    if (b.dataset.a === "prank") call("prank", n);
    if (b.dataset.a === "fame") call("fame_add", n, 50).then(refresh);
  }));

  const net = S.config.open_to_network;
  $("#joinInfo").innerHTML = net
    ? `В същата мрежа приятелите влизат на <code>${esc(S.ip)}</code>. Отвън — през Radmin VPN, ZeroTier или playit.gg.`
    : `Сървърът е само за този компютър — влизаш на <code>127.0.0.1</code>. За приятели включи „Приятели от мрежата“ в раздел Сървър.`;
}

function renderSpirits() {
  const opts = S.online.map(p => `<option>${esc(p)}</option>`).join("");
  $("#spirits").innerHTML = Object.entries(S.characters).map(([k, c]) => `
    <article class="spirit c-${k}">
      <div class="spirit-head">
        <div>
          <h3>${esc(c.name)}</h3>
          <p class="calls">Вика се с: ${c.aliases.map(esc).join(", ")}</p>
        </div>
        ${c.enabled ? `<button class="link-danger" data-a="unplace" data-k="${k}">Прибери</button>` : ""}
      </div>
      <p class="lede">${esc(SPIRIT_TEXT[k] || "")}</p>
      <div class="row">
        <select data-k="${k}" class="who" ${S.ready ? "" : "disabled"}>
          ${opts || "<option value=''>Никой не е в света</option>"}
        </select>
        <button class="btn btn-primary" data-a="visit" data-k="${k}" ${S.ready && S.online.length ? "" : "disabled"}>Прати при него</button>
        <button class="btn" data-a="place" data-k="${k}" ${S.ready && S.online.length ? "" : "disabled"}>
          ${c.enabled ? "Премести тук завинаги" : "Постави тук завинаги"}</button>
      </div>
      ${visiting(k) ? `<p class="hint">Сега е при ${esc(visiting(k))}.</p>` : ""}
      <form class="inline talk" data-k="${k}">
        <input placeholder="Кажи му нещо като собственик" ${S.ready ? "" : "disabled"}>
        <button class="btn" ${S.ready ? "" : "disabled"}>Кажи</button>
      </form>
    </article>`).join("");

  $$("#spirits [data-a=place]").forEach(b => b.addEventListener("click", () => {
    const who = $(`#spirits select[data-k="${b.dataset.k}"]`).value;
    call("place", b.dataset.k, who).then(refresh);
  }));
  $$("#spirits [data-a=visit]").forEach(b => b.addEventListener("click", () => {
    const who = $(`#spirits select[data-k="${b.dataset.k}"]`).value;
    call("spirit_visit", b.dataset.k, who).then(refresh);
  }));
  $$("#spirits [data-a=unplace]").forEach(b => b.addEventListener("click",
    () => call("remove", b.dataset.k).then(refresh)));
  $$("#spirits .talk").forEach(f => f.addEventListener("submit", e => {
    e.preventDefault();
    const inp = $("input", f);
    if (!inp.value.trim()) return;
    call("say_as", f.dataset.k, inp.value.trim());
    toast("Изпратено. Отговорът ще дойде в играта.");
    inp.value = "";
  }));
}

function renderEvents() {
  const n = S.online.length;
  $("#eventGrid").innerHTML = S.events.filter(ev => ev.key !== "boss").map(ev => {
    const blocked = !S.ready || n < ev.min_players || !!S.event;
    const why = !S.ready ? "" : n < ev.min_players
      ? ` Иска поне ${ev.min_players} играчи.` : "";
    return `<button class="ev" data-k="${ev.key}" ${blocked ? "disabled" : ""}>
      <b>${esc(EVENT_NAMES[ev.key] || ev.title)}</b><span>${esc(EVENT_TEXT[ev.key] || "")}${why}</span>
    </button>`;
  }).join("");
  $("#eventNote").textContent = !S.ready ? "Пусни сървъра, за да пускаш събития."
    : S.event ? `Сега върви „${EVENT_NAMES[S.event.key] || S.event.title}“. Изчакай да свърши.`
    : !n ? "Няма никой в света — събитията чакат поне един играч."
    : S.config.chaos ? `Режисьорът води историята и решава на около ${ago(S.config.gm_period || 120)}. В света: ${n}.`
    : "Изненадите са спрени (Тихо). Можеш да пускаш събития ръчно.";
  $$("#eventGrid .ev").forEach(b => b.addEventListener("click",
    () => call("event", b.dataset.k).then(refresh)));
  $("#chaosNote").textContent = CHAOS_TEXT[S.config.chaos] || "";

  const blocked = !S.ready || !n || !!S.event;
  const cg = $("#contestGrid");
  const sig = JSON.stringify([blocked, (S.contests || []).length]);
  if (cg.dataset.sig !== sig) {
    cg.dataset.sig = sig;
    cg.innerHTML = (S.contests || []).map(c => `
      <button class="ev" data-c="${c.key}" ${blocked ? "disabled" : ""}>
        <b>${esc(c.title)}</b><span>${esc(c.desc)}</span></button>`).join("");
    $$("#contestGrid .ev").forEach(b => b.addEventListener("click",
      () => call("contest", b.dataset.c).then(refresh)));
  }
  if (document.activeElement !== $("#gm_power")) $("#gm_power").value = S.config.gm_power || "full";
  if (document.activeElement !== $("#gm_period")) $("#gm_period").value = String(S.config.gm_period || 120);
  $("#gm_enabled").checked = S.config.gm_enabled !== false;
  $("#spirits_roam").checked = S.config.spirits_roam !== false;
  $("#villager_quests").checked = S.config.villager_quests !== false;
  fillSelect($("#bossPlayer"), [["", "случаен играч"], ...playerItems(true)]);
  const bg = $("#bossGrid");
  const bsig = JSON.stringify([blocked, S.bosses || []]);
  if (bg.dataset.sig !== bsig) {
    bg.dataset.sig = bsig;
    bg.innerHTML = (S.bosses || []).map(b => `
      <button class="ev boss" data-b="${esc(b)}" ${blocked ? "disabled" : ""}><b>${esc(b)}</b><span>Бос</span></button>`).join("");
    $$("#bossGrid .ev").forEach(b => b.addEventListener("click",
      () => call("boss", $("#bossPlayer").value || "", b.dataset.b).then(refresh)));
  }
}

function renderGm() {
  const g = S.gm || {};
  const next = g.busy ? "Мисли в момента..."
    : !S.ready ? "Ще започне, когато сървърът е готов."
    : !S.online.length ? "Чака някой да влезе."
    : !S.config.chaos ? "Изненадите са спрени (Тихо) — действа само ако го помолиш."
    : g.next_in != null ? `Следващото решение след ${ago(g.next_in)}.` : "";
  $("#gmNext").textContent = (g.enabled ? "Изкуственият разум гледа света и решава какво да се случи. "
    : "AI режисьорът е изключен — пускат се готови сценарии. ") + next;
  $("#gmNow").disabled = !S.ready || !S.online.length || g.busy;
  $("#gmList").innerHTML = (g.decisions || []).map(d => `
    <li><span class="when">преди ${ago(d.ago)}</span>
      <span class="what"><b>${esc(d.text)}</b><span class="src ${d.source === "AI" ? "" : "plain"}">${esc(d.source)}</span>
      ${d.done && d.done.length ? `<span class="done">${esc(d.done.join(" · "))}</span>` : ""}</span></li>`).join("");
}

function renderConsole() {
  fillSelect($("#qPlayer"), playerItems(), "Няма играчи");
  $$(".quick [data-q]").forEach(b => b.disabled = !S.running);
}

const KIND_ORDER = ["house", "farm", "well", "tower", "garden", "stall", "lamp"];

function renderVillages() {
  fillSelect($("#bPlayer"), playerItems(true), "Никой не е в света");
  fillSelect($("#bKind"), (S.kinds || []).sort((a, b) => KIND_ORDER.indexOf(a.key) - KIND_ORDER.indexOf(b.key)).map(k => [k.key, k.name]));
  fillSelect($("#bStyle"), [["", "стил по избор на селянина"], ...(S.styles || []).map(s => [s.key, s.name])]);
  const can = S.ready && S.online.length;
  $("#bVillage").disabled = !can;
  $("#bBuild").disabled = !can;
  $("#workers_enabled").checked = S.config.workers_enabled !== false;
  $("#village_auto").checked = S.config.village_auto !== false;
  const V = S.villages || { villages: [], workers: [] };
  const KN = Object.fromEntries((S.kinds || []).map(k => [k.key, k.name]));
  KN.plaza = "площад";
  $("#villageList").innerHTML = V.villages.length ? V.villages.map(v => {
    const counts = {};
    v.kinds.forEach(k => counts[k] = (counts[k] || 0) + 1);
    return `<article class="vcard"><h3>${esc(v.name)}</h3>
      <div class="where">X ${v.x} · Z ${v.z} · ${plural(v.buildings, "постройка", "постройки")}</div>
      <div class="chips">${Object.entries(counts).map(([k, c]) => `<span class="tag">${esc(KN[k] || k)}${c > 1 ? ` ×${c}` : ""}</span>`).join("")}</div>
    </article>`;
  }).join("") : `<p class="empty">Още няма села. Застани на поляна в играта и натисни „Ново село тук“ — или режисьорът ще основе сам.</p>`;
  const list = $("#workerList");
  if (list.contains(document.activeElement)) return;
  list.innerHTML = V.workers.length ? V.workers.map(w => {
    const what = w.searching ? `търси място за ${w.searching}`
      : w.job ? (w.waiting ? `${w.job} — чака някой да дойде наблизо` : `строи ${w.job}`)
      : `свободен${w.built ? ` · построил ${w.built}` : ""}`;
    const pct = w.progress != null ? Math.round(w.progress * 100) : null;
    return `<li><span><span class="name">${esc(w.name)}</span>${w.village ? ` <span class="tag">${esc(w.village)}</span>` : ""}
        <span class="what">${esc(what)}${pct != null ? ` · ${pct}%` : ""}${w.x != null ? ` · X ${w.x} Z ${w.z}` : ""}</span>
        ${pct != null ? `<div class="meter"><i style="width:${pct}%"></i></div>` : ""}</span>
      <span class="row-actions">
        ${w.job ? `<button class="btn btn-sm" data-w="stop" data-id="${w.id}">Спри строежа</button>` : ""}
        <button class="btn btn-sm btn-danger" data-w="remove" data-id="${w.id}">Отпрати</button>
      </span></li>`;
  }).join("") : `<li class="empty">Още няма селяни.</li>`;
  $$("#workerList [data-w]").forEach(b => b.addEventListener("click", () =>
    call(b.dataset.w === "stop" ? "worker_stop" : "worker_remove", parseInt(b.dataset.id, 10)).then(refresh)));
}

function fillForms() {
  if (formsFilled) return;
  formsFilled = true;
  const c = S.config;
  for (const f of ["#serverForm", "#aiForm"]) {
    $$(`${f} [name]`).forEach(el => {
      if (el.type === "checkbox") el.checked = !!c[el.name];
      else if (el.type === "password") el.placeholder = c[el.name + "_set"]
        ? "Запазен ✓ (празно = без промяна)" : "Постави ключа тук";
      else el.value = c[el.name] ?? "";
    });
  }
  $("#chaos").value = c.chaos;
  $("#gm_period").value = String(c.gm_period || 120);
  $("#ownerName").value = c.owner_name || "";
  $("#version").textContent = S.version && S.version !== "dev" ? `версия ${S.version}` : "";
}

function collect(form) {
  const out = {};
  $$("[name]", form).forEach(el => {
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.dataset.type === "int") out[el.name] = parseInt(el.value, 10);
    else if (el.type === "password") { if (el.value) out[el.name] = el.value; }
    else out[el.name] = el.value;
  });
  return out;
}

/* ---------- лог ---------- */
const TERM_SRC = new Set(["Конзола", "RCON", "Сървър", "Админ"]);
function renderTerm(items) {
  const box = $("#term");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (const e of items) {
    if (e.level !== "console" && !TERM_SRC.has(e.src)) continue;
    const d = document.createElement("div");
    const mine = e.src === "Конзола" && String(e.msg).startsWith(">");
    d.className = mine ? "me" : e.level === "console" ? "" : e.level;
    d.textContent = e.level === "console" ? e.msg : `[${e.src}] ${e.msg}`;
    frag.appendChild(d);
  }
  box.appendChild(frag);
  while (box.children.length > 2000) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderLog(items) {
  renderTerm(items);
  const box = $("#log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (const e of items) {
    const d = document.createElement("div");
    d.className = `l ${e.level}`;
    d.dataset.level = e.level;
    d.innerHTML = `<span class="t">${e.t}</span><span class="s">${esc(e.src)}</span><span class="m">${esc(e.msg)}</span>`;
    applyFilter(d);
    frag.appendChild(d);
  }
  box.appendChild(frag);
  while (box.children.length > 1500) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function applyFilter(d) {
  const lv = d.dataset.level;
  d.hidden = (logFilter === "game" && lv === "console")
    || (logFilter === "problems" && !["warn", "error"].includes(lv));
}

$$("#logFilters .chip").forEach(c => c.addEventListener("click", () => {
  logFilter = c.dataset.f;
  $$("#logFilters .chip").forEach(x => x.classList.toggle("is-on", x === c));
  $$("#log .l").forEach(applyFilter);
}));

/* ---------- действия ---------- */
$("#powerBtn").addEventListener("click", async () => {
  if (!S) return;
  $("#powerBtn").disabled = true;
  if (S.running) await call("stop");
  else await call("start");
  setTimeout(() => { $("#powerBtn").disabled = false; refresh(); }, 1200);
});

$("#randomEvent").addEventListener("click", () => {
  if (S.event) return call("stop_event").then(refresh);
  const free = S.events.filter(e => S.online.length >= e.min_players);
  if (!free.length) return toast("Няма достатъчно играчи за събитие.", true);
  const pick = free[Math.floor(Math.random() * free.length)];
  call("event", pick.key).then(refresh);
});

$("#serverForm").addEventListener("submit", async e => {
  e.preventDefault();
  const r = await call("save", collect(e.target));
  if (r && r.ok) toast("Запазено. Важи от следващото пускане.");
  refresh();
});

$("#aiForm").addEventListener("submit", async e => {
  e.preventDefault();
  const r = await call("save", collect(e.target));
  if (r && r.ok) toast("Запазено.");
  $$("#aiForm [type=password]").forEach(i => i.value = "");
  formsFilled = false;
  await refresh();
});

$("#testAi").addEventListener("click", async () => {
  const b = $("#testAi");
  b.disabled = true;
  $("#aiResult").textContent = "Питам всяка услуга поотделно...";
  $("#aiTests").innerHTML = "";
  let r = null;
  try { r = await api.test_ai(); } catch (e) { r = { ok: false, error: String(e), results: [] }; }
  b.disabled = false;
  $("#aiResult").textContent = !r ? "" : r.ok ? `Работи: ${r.model}` : `Не работи: ${r.error || ""}`;
  $("#aiTests").innerHTML = ((r && r.results) || []).map(x => `
    <li class="${cls(x.ok)}"><span class="mark">${mark(x.ok)}</span><span>${esc(x.name)}${x.ok ? ` — ${esc(x.model)}, ${x.seconds} сек` : ""}</span>
    <small>${esc(x.ok ? `„${x.say}“` : x.error)}</small></li>`).join("");
  refresh();
});

$("#chaos").addEventListener("input", e => {
  $("#chaosNote").textContent = CHAOS_TEXT[e.target.value];
});
$("#chaos").addEventListener("change", e =>
  call("save", { chaos: parseInt(e.target.value, 10) }).then(refresh));
$("#gm_period").addEventListener("change", e =>
  call("save", { gm_period: parseInt(e.target.value, 10) }).then(refresh));
$("#spirits_roam").addEventListener("change", e => call("save", { spirits_roam: e.target.checked }).then(refresh));
$("#villager_quests").addEventListener("change", e => call("save", { villager_quests: e.target.checked }).then(refresh));
$("#sagaReset").addEventListener("click", () => {
  if (window.confirm("Да започне ли нова история? Летописът и задачите се забравят."))
    call("story_reset").then(refresh);
});
$$("[data-url]").forEach(b => b.addEventListener("click", () => call("open_url", b.dataset.url)));
$$("[data-clear]").forEach(b => b.addEventListener("click", async () => {
  await call("clear_key", b.dataset.clear);
  formsFilled = false;
  refresh();
}));

$("#addPlayer").addEventListener("submit", async e => {
  e.preventDefault();
  const n = $("#newPlayer").value.trim();
  if (!n) return;
  if (!/^[A-Za-z0-9_]{1,16}$/.test(n)) {
    return toast("Името в Minecraft е само с латински букви, цифри и _.", true);
  }
  const r = await call("whitelist_add", n);
  if (r && r.ok) { $("#newPlayer").value = ""; toast(`${n} може да влиза.`); }
  refresh();
});

const history = [];
let histPos = 0;
$("#consoleForm").addEventListener("submit", e => {
  e.preventDefault();
  const v = $("#consoleInput").value.trim();
  if (!v) return;
  call("console", v);
  if (history[history.length - 1] !== v) history.push(v);
  histPos = history.length;
  $("#consoleInput").value = "";
});
$("#consoleInput").addEventListener("keydown", e => {
  if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
  e.preventDefault();
  histPos = Math.max(0, Math.min(history.length, histPos + (e.key === "ArrowUp" ? -1 : 1)));
  $("#consoleInput").value = history[histPos] || "";
});
$$(".quick [data-q]").forEach(b => b.addEventListener("click", () =>
  call("quick", b.dataset.q, $("#qPlayer").value || "").then(refresh)));

/* ---------- режисьорът ---------- */
$("#gmNow").addEventListener("click", () => call("gm_now", "").then(refresh));
$("#gmAsk").addEventListener("submit", e => {
  e.preventDefault();
  const t = $("#gmText").value.trim();
  if (!t) return;
  call("gm_now", t).then(r => { if (r && r.ok) $("#gmText").value = ""; refresh(); });
});
$("#gm_enabled").addEventListener("change", e => call("save", { gm_enabled: e.target.checked }).then(refresh));
$("#gm_power").addEventListener("change", e => call("save", { gm_power: e.target.value }).then(refresh));

/* ---------- села ---------- */
$("#bVillage").addEventListener("click", () => call("village_found", $("#bPlayer").value).then(refresh));
$("#bBuild").addEventListener("click", () => call("village_build", $("#bPlayer").value,
  $("#bKind").value, $("#bStyle").value, $("#bSize").value).then(refresh));
$("#workers_enabled").addEventListener("change", e => call("save", { workers_enabled: e.target.checked }));
$("#village_auto").addEventListener("change", e => call("save", { village_auto: e.target.checked }));
$("#villagesReset").addEventListener("click", () => {
  if (window.confirm("Да забравя ли всички села и селяни? Постройките остават в света."))
    call("villages_reset").then(refresh);
});

/* ---------- играчи: собственик ---------- */
$("#ownerForm").addEventListener("submit", async e => {
  e.preventDefault();
  const n = $("#ownerName").value.trim();
  if (n && !/^[A-Za-z0-9_]{1,16}$/.test(n)) return toast("Името в Minecraft е само с латински букви, цифри и _.", true);
  await call("set_owner", n);
  refresh();
});

/* ---------- AI модели и отчет ---------- */
$("#gemini_model").addEventListener("change", e => { e.target.dataset.touched = "1"; });
$("#refreshModels").addEventListener("click", async () => {
  $("#modelHint").textContent = "Питам кои модели са налични...";
  const r = await call("ai_models", true);
  if (r && r.ok) toast("Моделите са обновени: " + (r.by || []).map(x => `${x.provider} ${x.models.length}`).join(", "));
  refresh();
});
$("#report").addEventListener("click", () => call("export_report"));

async function loadVersions() {
  const sel = $("#mc_version");
  if (sel.options.length > 1) return;
  const list = await api.versions();
  for (const v of list || []) {
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  }
  sel.value = S?.config.mc_version || "";
}

/* ---------- стар свят ---------- */
let worldCheck = null;          // последната успешна проверка на избран свят
let importSeen;                 // id на последния показан резултат

function worldLine(w) {
  const bits = [
    w.version ? `Minecraft ${w.version}` : "версия неизвестна",
    w.size_mb ? `${w.size_mb} MB` : "",
    w.last_played ? `играно на ${w.last_played}` : "",
    w.nether ? "с Нетер и Края" : "",
    w.hardcore ? "хардкор" : "",
  ].filter(Boolean);
  return `<b>${esc(w.name)}</b><span>${bits.map(esc).join(" · ")}</span>`;
}

async function loadWorld() {
  let r = null;
  try { r = await api.world_current(); } catch (e) { /* няма връзка */ }
  $("#worldNow").innerHTML = r && r.world
    ? `<span class="tagline">Сега:</span>${worldLine(r.world)}`
    : `<p class="empty">Още няма свят — ще се създаде при първото пускане, или внеси стария.</p>`;
}

async function inspectWorld(path) {
  path = (path || "").trim();
  if (!path) return;
  worldCheck = null;
  const box = $("#worldFound");
  box.hidden = false;
  box.innerHTML = `<p class="empty">Чета света...</p>`;
  let r = null;
  try { r = await api.world_inspect(path); } catch (e) { r = { ok: false, error: String(e) }; }
  if (!r || !r.ok) {
    box.innerHTML = `<div class="wcard"><p class="warn bad">${esc(r ? r.error : "Грешка")}</p>
      <p class="hint">Избери папката на стария сървър (там, където е server.properties), самата папка на света (с файл level.dat) или .zip с тях.</p></div>`;
    return;
  }
  worldCheck = r;
  const w = r.world, names = r.whitelist_names || [];
  const alt = (w.alternatives || []).length > 1
    ? `<p class="hint">Вътре има няколко свята (${esc(w.alternatives.join(", "))}). Взимам „${esc(w.folder)}“.</p>` : "";
  box.innerHTML = `
    <div class="wcard">
      <div class="wline">${worldLine(w)}</div>
      <p class="wpath">${esc(r.path)}</p>
      ${alt}
      ${r.blocked ? `<p class="warn bad">${esc(r.blocked)}</p>` : ""}
      ${(r.warnings || []).map(x => `<p class="warn">${esc(x)}</p>`).join("")}
      ${names.length ? `<label class="toggle"><input type="checkbox" id="mergeWl" checked><span></span>
        Пусни и ${names.length} играчи от стария списък (${esc(names.slice(0, 6).join(", "))}${names.length > 6 ? "…" : ""})</label>` : ""}
      <div class="actions">
        <button class="btn btn-primary" id="doImport" type="button">Внеси този свят</button>
        <span class="hint" id="importHint"></span>
      </div>
    </div>`;
  $("#doImport").addEventListener("click", doImport);
  syncImport();
}

function syncImport() {
  const b = $("#doImport");
  if (!b || !worldCheck || !S) return;
  const busy = !!(S.progress && S.progress.verb);
  b.disabled = !!worldCheck.blocked || S.running || busy;
  $("#importHint").textContent = worldCheck.blocked ? ""
    : S.running ? "Първо спри сървъра — светът не се сменя, докато върви."
    : busy ? "Внасям..." : "Сегашният свят отива в _backups. Нищо не се трие.";
}

async function doImport() {
  if (!worldCheck) return;
  const merge = $("#mergeWl") ? $("#mergeWl").checked : false;
  $("#doImport").disabled = true;
  const r = await call("world_import", worldCheck.path, merge);
  if (!r || !r.ok) syncImport();
}

function showImportResult(res) {
  const box = $("#worldFound");
  box.hidden = false;
  worldCheck = null;
  if (res.ok) {
    box.innerHTML = `<div class="wcard done">
      <div class="wline"><b>Светът е внесен</b></div>
      <p>Пусни сървъра. Първото зареждане на голям или по-стар свят отнема малко повече.</p>
      ${res.backup ? `<p class="wpath">Предишният свят е запазен в ${esc(res.backup)}</p>` : ""}
      ${(res.added || []).length ? `<p class="hint">Добавени в списъка: ${esc(res.added.join(", "))}</p>` : ""}
      <p class="hint">Ако духовете бяха призовани в стария свят, призови ги наново от раздел Духовете.</p>
    </div>`;
    toast("Светът е внесен.");
  } else {
    box.innerHTML = `<div class="wcard"><p class="warn bad">${esc(res.message || "Не се получи.")}</p>
      <p class="hint">Сегашният свят е оставен както си беше.</p></div>`;
    toast(res.message || "Внасянето не се получи.", true);
  }
  loadWorld();
}

$("#pickFolder").addEventListener("click", async () => {
  const r = await call("world_pick", "folder");
  if (r && r.path) { $("#worldPath").value = r.path; inspectWorld(r.path); }
});
$("#pickZip").addEventListener("click", async () => {
  const r = await call("world_pick", "zip");
  if (r && r.path) { $("#worldPath").value = r.path; inspectWorld(r.path); }
});
$("#worldPathForm").addEventListener("submit", e => {
  e.preventDefault();
  inspectWorld($("#worldPath").value);
});
$("#openServerDir").addEventListener("click", () => call("open_folder", "server"));

/* ---------- пълна проверка ---------- */
$("#diagBtn").addEventListener("click", async () => {
  const b = $("#diagBtn");
  b.disabled = true;
  b.textContent = "Проверявам...";
  $("#diagList").innerHTML = `<li class="empty">Пробвам командите на живия сървър. Отнема до 20 секунди.</li>`;
  const r = await call("diagnose");
  b.disabled = false;
  b.textContent = "Провери всичко";
  if (!r || !r.checks) { $("#diagList").innerHTML = ""; return; }
  $("#diagList").innerHTML = r.checks.map(c => `
    <li class="${cls(c.ok)}"><span class="mark">${mark(c.ok)}</span><span>${esc(c.name)}</span>${
      c.detail ? `<small>${esc(c.detail)}</small>` : ""}</li>`).join("");
  const bad = r.checks.filter(c => c.ok === false).length;
  toast(bad ? `${bad} от проверките не минаха — виж кои.` : "Всичко работи.", bad > 0);
});

/* ---------- обновяване ---------- */
async function refresh() {
  try {
    S = await api.state();
  } catch (e) { return; }
  if (!S) return;
  const steps = [fillForms, renderPower, renderHealth, renderHome, renderStory,
    renderServer, renderPlayers, renderEvents, renderAi, renderGm, renderConsole,
    renderVillages];
  // Една счупена част не бива да спира останалите
  for (const f of steps) {
    try { f(); } catch (e) { console.error(f.name, e); }
  }
  const active = document.activeElement;
  if (!$("#view-spirits").contains(active)) {
    try { renderSpirits(); } catch (e) { console.error(e); }
  }
  const res = S.import_result;
  const id = res ? res.id : null;
  if (importSeen === undefined) importSeen = id;     // старо от преди отварянето
  else if (id && id !== importSeen) { importSeen = id; showImportResult(res); }
}

async function pollLogs() {
  try {
    const items = await api.logs(logSeen);
    if (items && items.length) {
      logSeen = items[items.length - 1].id;
      renderLog(items);
    }
  } catch (e) { /* прозорецът се затваря */ }
}

function boot() {
  api = window.pywebview.api;
  refresh();
  pollLogs();
  setInterval(refresh, 1500);
  setInterval(pollLogs, 700);
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);
