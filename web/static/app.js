"use strict";
/* Кабинет менеджера Creatica: КП, доставка, документы, ткани, ценники, продажи. */

const S = { user: null, meta: null, route: "" };

// append/replaceChildren пропускают пустые элементы (null/false) — удобно для условных блоков
for (const m of ["append", "replaceChildren"]) {
  const orig = Element.prototype[m];
  Element.prototype[m] = function (...kids) { return orig.apply(this, kids.flat(Infinity).filter(x => x !== null && x !== undefined && x !== false)); };
}
const view = document.getElementById("view");

// ---------- утилиты ---------------------------------------------------------------------------

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const money = n => (Math.round(Number(n) || 0)).toLocaleString("ru-RU").replace(/,/g, " ") + " ₽";
const num = v => { const x = parseFloat(String(v ?? "").replace(/[\s ₽%]/g, "").replace(",", ".")); return isNaN(x) ? 0 : x; };
const todayStr = () => { const d = new Date(); return [d.getDate(), d.getMonth() + 1].map(x => String(x).padStart(2, "0")).join(".") + "." + d.getFullYear(); };
const fmtTs = ts => new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg, ms = 2600) {
  const t = h("div", { class: "toast", text: msg });
  document.body.append(t);
  setTimeout(() => t.remove(), ms);
}

class ApiError extends Error { constructor(data, status) { super(data.error || "Ошибка"); this.data = data; this.status = status; } }

async function api(method, url, body) {
  const opt = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(url, opt); } catch (e) { throw new ApiError({ error: "Нет связи с сервером. Проверьте интернет." }, 0); }
  if (r.status === 401) { location.href = "/#login"; throw new ApiError({ error: "Войдите заново." }, 401); }
  const ct = r.headers.get("Content-Type") || "";
  if (!ct.includes("json")) { if (!r.ok) throw new ApiError({ error: "Сервер не ответил. Попробуйте ещё раз." }, r.status); return r; }
  const data = await r.json();
  if (!r.ok) throw new ApiError(data, r.status);
  return data;
}

async function getBlob(method, url, body) {
  const r = await api(method, url, body);
  if (!(r instanceof Response)) throw new ApiError({ error: "Не получилось" }, 500);
  const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
  return { blob: await r.blob(), name: name ? name[1] : "file" };
}

async function download(url, body, method = "POST") {
  const { blob, name } = await getBlob(method, url, body);
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 30000);
  return name;
}

async function busy(btn, fn) {
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  try { return await fn(); }
  catch (e) { if (!(e instanceof ApiError && e.data.errors)) toast(e.message || "Ошибка"); throw e; }
  finally { btn.disabled = false; btn.innerHTML = old; }
}

function field(label, input, opts = {}) {
  const f = h("label", { class: "field" + (opts.class ? " " + opts.class : "") }, h("span", { text: label }), input,
    opts.extra || null, h("small", { class: "err" }));
  if (opts.key) f.dataset.key = opts.key;
  return f;
}

function input(value, on, attrs = {}) {
  const el = h("input", { value: value ?? "", ...attrs });
  if (on) el.addEventListener("input", () => on(el.value));
  return el;
}

function chips(options, current, on) {
  const box = h("div", { class: "chips" });
  for (const [label, value] of options) {
    const b = h("button", { type: "button", class: "chip" + (value === current ? " on" : ""), text: label,
      onclick: () => { box.querySelectorAll(".chip").forEach(x => x.classList.remove("on")); b.classList.add("on"); on(value); } });
    box.append(b);
  }
  return box;
}

function showErrors(root, errors) {
  root.querySelectorAll(".field").forEach(f => { f.classList.remove("bad"); const e = f.querySelector(".err"); if (e) e.textContent = ""; });
  let first = null;
  for (const [k, v] of Object.entries(errors || {})) {
    const f = root.querySelector(`.field[data-key="${k}"]`);
    if (f) { f.classList.add("bad"); f.querySelector(".err").textContent = v; first = first || f; }
  }
  if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
}

function modal(title, body, opts = {}) {
  const close = () => { ov.remove(); if (opts.onclose) opts.onclose(); };
  const ov = h("div", { class: "overlay", onclick: e => { if (e.target === ov && !opts.sticky) close(); } },
    h("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true" },
      h("div", { class: "row", style: "justify-content:space-between;margin-bottom:12px" },
        h("h3", { text: title, style: "margin:0" }), h("button", { class: "icon", text: "✕", onclick: close, "aria-label": "Закрыть" })),
      body));
  document.body.append(ov);
  return close;
}

function page(title, sub, ...kids) {
  view.replaceChildren(h("h2", { text: title }), sub ? h("div", { class: "sub", text: sub }) : null, ...kids);
  window.scrollTo(0, 0);
}

// локальная копия черновиков форм (переживает перезагрузку страницы)
const local = {
  get(k) { try { return JSON.parse(localStorage.getItem("cr:" + k) || "null"); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem("cr:" + k, JSON.stringify(v)); } catch (e) {} },
  del(k) { try { localStorage.removeItem("cr:" + k); } catch (e) {} },
};

// ---------- навигация -------------------------------------------------------------------------

const ROUTES = [
  ["kp", "📋", "Коммерческое предложение", viewKP],
  ["history", "📄", "Мои КП", viewHistory],
  ["delivery", "🚚", "Стоимость доставки", viewDelivery],
  ["docs", "📝", "Возврат и пени", viewDocs],
  ["payout", "💵", "Выплата дизайнеру", viewPayout],
  ["fabrics", "🔎", "Найти ткань", viewFabrics],
  ["tag", "🏷", "Ценник", viewTag],
  ["deals", "📈", "Планируемые продажи", viewDeals],
  ["cards", "🏢", "Карточка организации", viewCards],
  ["profile", "👤", "Мой профиль", viewProfile],
];

function renderNav() {
  const nav = document.getElementById("nav");
  nav.replaceChildren(...ROUTES.flatMap(([key, icon, label], i) => [
    i === 2 || i === 8 ? h("div", { class: "sep" }) : null,
    h("a", { href: "#" + key, class: S.route === key ? "on" : "", onclick: () => document.getElementById("side").classList.remove("open") },
      h("span", { text: icon }), label),
  ]));
  document.getElementById("me").replaceChildren(
    h("div", { style: "font-weight:700", text: S.user.name }), h("div", { class: "muted", text: S.user.showroom }),
    h("button", { class: "link", style: "padding:4px 0", text: "Выйти", onclick: logout }));
}

async function logout() { await api("POST", "/api/logout"); location.href = "/"; }

function route() {
  const key = (location.hash || "#kp").slice(1).split("/")[0];
  const r = ROUTES.find(x => x[0] === key) || ROUTES[0];
  S.route = r[0];
  renderNav();
  r[3]().catch(e => { if (e instanceof ApiError && e.status === 401) return; page("Ошибка", e.message); });
}

document.getElementById("menuBtn").onclick = () => { document.getElementById("side").classList.add("open"); document.getElementById("menuClose").classList.remove("hidden"); };
document.getElementById("menuClose").onclick = () => document.getElementById("side").classList.remove("open");

async function boot() {
  const me = await api("GET", "/api/me");
  if (!me.user) { location.href = "/#login"; return; }
  S.user = me.user;
  if (S.user.status !== "approved") { pending(); return; }
  S.meta = await api("GET", "/api/meta");
  window.addEventListener("hashchange", route);
  route();
}

function pending() {
  document.getElementById("side").classList.add("hidden");
  view.replaceChildren(h("div", { class: "card", style: "max-width:520px;margin:40px auto;text-align:center" },
    h("h2", { text: "Заявка отправлена" }),
    h("p", { class: "muted", text: `${S.user.name}, руководитель получил вашу заявку в Telegram. Как только он подтвердит доступ — здесь откроются все функции.` }),
    h("div", { class: "row", style: "justify-content:center" },
      h("button", { text: "Проверить снова", onclick: () => location.reload() }),
      h("button", { class: "ghost", text: "Выйти", onclick: logout }))));
}

// =================================================================================================
// КП
// =================================================================================================

const KP = { k: null, timer: null, savedAt: null, source: null };

function kpEmpty() {
  return { kind: "std", customer: "", customer_phone: "", items: [], production: "55 рабочих дней",
    services: "По согласованию", valid_days: 7, payments: S.meta.payments.map(() => true) };
}
const itemTotal = it => Math.round((Number(it.unit_price) || 0) * (1 - (Number(it.discount) || 0) / 100) * (Number(it.qty) || 1));
const kpTotal = k => k.items.reduce((s, it) => s + itemTotal(it), 0);
const kpHasContent = k => k && (k.items.length || k.customer);

function kpSave() {
  clearTimeout(KP.timer);
  local.set("kp", { ts: Date.now() / 1000, kp: KP.k });
  const st = document.getElementById("kpSaved");
  if (st) st.textContent = "Сохраняю…";
  KP.timer = setTimeout(async () => {
    try {
      await api("PUT", "/api/kp/draft", { kp: KP.k, source: KP.source });
      KP.savedAt = new Date();
      const s = document.getElementById("kpSaved");
      if (s) s.textContent = "✓ Черновик сохранён " + KP.savedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      const s = document.getElementById("kpSaved");
      if (s) s.textContent = "Черновик сохранён на этом устройстве";
    }
  }, 1200);
}

async function viewKP() {
  if (!KP.k) {
    const d = await api("GET", "/api/kp/draft");
    const loc = local.get("kp");
    let best = d && d.kp ? d : null;
    if (loc && loc.kp && (!best || loc.ts > best.ts + 5)) best = loc;
    KP.k = best && kpHasContent(best.kp) ? best.kp : kpEmpty();
    KP.restored = best && kpHasContent(best.kp) ? best.ts : null;
  }
  renderKP();
}

function renderKP() {
  const k = KP.k;
  const banner = KP.restored ? h("div", { class: "panel row", style: "justify-content:space-between;margin-bottom:14px" },
    h("span", { text: `💾 Восстановлен черновик от ${fmtTs(KP.restored)} — продолжайте с того же места.` }),
    h("button", { class: "ghost small", text: "Начать новое КП", onclick: kpNew })) : null;
  KP.restored = null;

  const items = h("div", { class: "list" });
  if (!k.items.length) items.append(h("div", { class: "empty", text: "Позиций пока нет — добавьте первую." }));
  k.items.forEach((it, i) => items.append(kpItemRow(it, i)));

  const customer = h("div", { class: "card" }, h("span", { class: "label", text: "Заказчик" }),
    field("Вариант КП", chips([["📋 Стандарт", "std"], ["🎨 С визуализацией", "vis"]], k.kind, v => { k.kind = v; kpSave(); })),
    h("div", { class: "grid2" },
      field("ФИО или организация *", input(k.customer, v => { k.customer = v; kpSave(); }, { placeholder: "Иванов Иван" })),
      field("Телефон заказчика *", input(k.customer_phone, v => { k.customer_phone = v; kpSave(); }, { type: "tel", placeholder: "+7 999 123-45-67" }))));

  const terms = h("div", { class: "card" }, h("span", { class: "label", text: "Условия" }),
    h("div", { class: "grid3" },
      field("Сроки изготовления", input(k.production, v => { k.production = v; kpSave(); }, { list: "prodList" })),
      field("Доп. услуги", input(k.services, v => { k.services = v; kpSave(); }, { list: "servList" })),
      field("Действует, дней", input(k.valid_days, v => { k.valid_days = parseInt(v) || 7; kpSave(); }, { type: "number", min: 1, max: 90 }))),
    h("datalist", { id: "prodList" }, h("option", { value: "55 рабочих дней" }), h("option", { value: "30 рабочих дней" })),
    h("datalist", { id: "servList" }, h("option", { value: "По согласованию" }), h("option", { value: "Не требуются" })),
    h("div", { class: "field" }, h("span", { text: "Способы оплаты" }),
      ...S.meta.payments.map((p, i) => h("label", { class: "check" },
        h("input", { type: "checkbox", checked: k.payments[i], onchange: e => { k.payments[i] = e.target.checked; kpSave(); } }), p))));

  page("Коммерческое предложение", "Всё сохраняется автоматически — можно закрыть страницу и вернуться.",
    banner, customer,
    h("div", { class: "card" },
      h("div", { class: "row", style: "justify-content:space-between;margin-bottom:10px" },
        h("span", { class: "label", text: `Позиции (${k.items.length})` }),
        h("button", { class: "small", text: "➕ Добавить позицию", onclick: () => kpItemEditor(null) })),
      items),
    terms,
    h("div", { class: "dock" },
      h("div", {}, h("div", { class: "total", id: "kpTotal", text: "Итого: " + money(kpTotal(k)) }), h("div", { class: "saved", id: "kpSaved", text: KP.savedAt ? "✓ Черновик сохранён" : "" })),
      h("div", { class: "row" },
        h("button", { class: "ghost", text: "👁 Предпросмотр", onclick: e => kpPreview(e.currentTarget) }),
        h("button", { text: "📄 Скачать PDF", onclick: e => kpPdf(e.currentTarget) }))));
}

function kpItemRow(it, i) {
  const k = KP.k;
  const priceIn = h("input", { class: "price-in", value: it.unit_price || "", inputmode: "numeric", title: "Цена за 1 шт — можно поменять прямо здесь",
    onchange: e => { it.unit_price = Math.round(num(e.target.value)); kpSave(); renderKP(); },
    onkeydown: e => { if (e.key === "Enter") e.target.blur(); } });
  const disc = it.discount ? ` −${it.discount}%` : "";
  return h("div", { class: "it" },
    it.photo_id ? h("img", { class: "thumb", src: "/api/photo?id=" + encodeURIComponent(it.photo_id), alt: "" }) : h("div", { class: "thumb" }),
    h("div", { class: "grow" },
      h("div", { class: "t", text: `${i + 1}. ${it.title || "Без названия"}` }),
      h("div", { class: "s", text: [it.size, it.material].filter(Boolean).join(" · ") }),
      h("div", { class: "s", text: `${it.qty} шт × ${money(it.unit_price)}${disc} = ${money(itemTotal(it))}` +
        (it.alt ? ` · альтернатива: ${it.alt.title} — ${money((it.alt.unit_price || 0) * it.qty)}` : "") })),
    h("div", { style: "display:flex;flex-direction:column;gap:6px;align-items:flex-end" },
      h("label", { class: "row", style: "gap:6px;font-size:12px;color:var(--grey)" }, "💰", priceIn),
      h("div", { class: "row", style: "gap:4px" },
        h("button", { class: "icon", text: "✏", title: "Изменить позицию", onclick: () => kpItemEditor(i) }),
        i > 0 ? h("button", { class: "icon", text: "↑", title: "Выше", onclick: () => { [k.items[i - 1], k.items[i]] = [k.items[i], k.items[i - 1]]; kpSave(); renderKP(); } }) : null,
        h("button", { class: "icon danger", text: "🗑", title: "Удалить", onclick: () => { if (confirm(`Удалить позицию «${it.title}»?`)) { k.items.splice(i, 1); kpSave(); renderKP(); } } }))));
}

function priceSearch(onPick, placeholder) {
  const q = h("input", { placeholder: placeholder || "Например: кресло Джун", onkeydown: e => { if (e.key === "Enter") { e.preventDefault(); go.click(); } } });
  const res = h("div", { class: "results hidden" });
  const go = h("button", { type: "button", class: "small", text: "Найти", onclick: () => busy(go, async () => {
    const d = await api("GET", "/api/price/search?q=" + encodeURIComponent(q.value.trim()));
    res.replaceChildren(...(d.items.length ? d.items.map(it => h("button", { type: "button", onclick: () => { res.classList.add("hidden"); onPick(it); } },
      h("div", { text: it.title }), h("small", { text: [it.size || it.size_raw, it.price_from ? "от " + money(it.price_from) : "", it.source === "акция" ? "акция" : ""].filter(Boolean).join(" · ") })))
      : [h("div", { class: "empty", text: "Ничего не нашлось — попробуйте иначе или заполните вручную." })]));
    res.classList.remove("hidden");
  }).catch(() => {}) });
  return h("div", {}, h("div", { class: "field" }, h("span", { text: "🔎 Найти в прайсе" }), h("div", { class: "row", style: "flex-wrap:nowrap" }, q, go)), res);
}

function photoField(obj, label) {
  const img = h("img", { class: "thumb", style: "width:90px;height:90px", src: obj.photo_id ? "/api/photo?id=" + encodeURIComponent(obj.photo_id) : "", alt: "" });
  if (!obj.photo_id) img.classList.add("hidden");
  const file = h("input", { type: "file", accept: "image/*", style: "padding:8px" });
  const del = h("button", { type: "button", class: "ghost small" + (obj.photo_id ? "" : " hidden"), text: "Убрать фото",
    onclick: () => { obj.photo_id = null; img.classList.add("hidden"); del.classList.add("hidden"); file.value = ""; } });
  file.onchange = async () => {
    if (!file.files[0]) return;
    const fd = new FormData(); fd.append("file", file.files[0]);
    const st = h("span", { class: "spin" }); file.after(st);
    try {
      const r = await fetch("/api/photo", { method: "POST", body: fd, credentials: "same-origin" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Фото не загрузилось");
      obj.photo_id = d.id; img.src = "/api/photo?id=" + encodeURIComponent(d.id); img.classList.remove("hidden"); del.classList.remove("hidden");
    } catch (e) { toast(e.message); file.value = ""; } finally { st.remove(); }
  };
  return field(label, h("div", { class: "row" }, img, h("div", { style: "flex:1;min-width:200px" }, file, del)));
}

function kpItemEditor(index) {
  const k = KP.k;
  const src = index === null ? { title: "", qty: 1, size: "", material: "", link: "", unit_price: "", discount: 0, photo_id: null, alt: null }
    : JSON.parse(JSON.stringify(k.items[index]));
  const it = src;
  const box = h("div", {});
  const vis = k.kind === "vis";

  function fill(target, p) {
    target.title = p.title; target.size = p.size || p.size_raw || target.size;
    target.default_price = p.price_from;
    if (!num(target.unit_price) && p.price_from) target.unit_price = p.price_from;
    draw();
  }
  function fieldsFor(t, isAlt) {
    const pre = isAlt ? "" : "";
    return [
      priceSearch(p => fill(t, p), isAlt ? "Стандартная позиция для сравнения" : undefined),
      field(pre + "Название *", input(t.title, v => t.title = v)),
      isAlt ? null : h("div", { class: "grid2" },
        field("Количество, шт", input(t.qty, v => t.qty = Math.max(1, parseInt(v) || 1), { type: "number", min: 1 })),
        field("Скидка, %", h("div", {}, input(t.discount, v => t.discount = num(v), { type: "number", min: 0, max: 99, step: "any", id: "discIn" }),
          h("div", { class: "chips", style: "margin-top:6px" }, ...[0, 3, 5, 10].map(v => h("button", { type: "button", class: "chip", text: v + "%",
            onclick: () => { t.discount = v; box.querySelector("#discIn").value = v; } })))))),
      h("div", { class: "grid2" },
        field("Цена за 1 шт, ₽ *", input(t.unit_price, v => t.unit_price = Math.round(num(v)), { inputmode: "numeric" }),
          { extra: t.default_price ? h("small", { class: "muted", text: "По прайсу «от»: " + money(t.default_price) }) : null }),
        field("Размеры, мм", input(t.size, v => t.size = v, { placeholder: "960×950×900" }))),
      field("Материал: обивка, цвет дерева", input(t.material, v => t.material = v, { placeholder: "4 кат. ткани" })),
      field("Ссылка на сайт", input(t.link, v => t.link = v, { placeholder: "https://creatica.shop/…" })),
      photoField(t, isAlt ? "Фото альтернативы" : (vis ? "Визуализация" : "Фото изделия")),
    ];
  }
  function draw() {
    box.replaceChildren(...fieldsFor(it, false));
    if (vis) {
      const has = !!it.alt;
      box.append(h("div", { class: "hr" }), h("div", { class: "row", style: "justify-content:space-between" },
        h("b", { text: "🅱 Альтернатива из коллекции" }),
        h("button", { type: "button", class: "ghost small", text: has ? "Убрать альтернативу" : "Добавить альтернативу",
          onclick: () => { it.alt = has ? null : { title: "", size: "", material: "", link: "", unit_price: "", photo_id: null }; draw(); } })));
      if (has) box.append(h("div", { class: "panel", style: "margin-top:10px" }, ...fieldsFor(it.alt, true)));
    }
    box.append(h("div", { class: "row", style: "justify-content:flex-end;margin-top:8px" },
      h("button", { class: "ghost", type: "button", text: "Отмена", onclick: () => close() }),
      h("button", { type: "button", text: index === null ? "Добавить в КП" : "Сохранить", onclick: save })));
  }
  function save() {
    if (!String(it.title).trim()) { toast("Укажите название позиции"); return; }
    if (!num(it.unit_price)) { toast("Укажите цену"); return; }
    if (it.alt && !String(it.alt.title).trim()) it.alt = null;
    if (index === null) k.items.push(it); else k.items[index] = it;
    kpSave(); close(); renderKP();
    toast(index === null ? "Позиция добавлена" : "Позиция обновлена");
  }
  draw();
  const close = modal(index === null ? "Новая позиция" : `Позиция ${index + 1}`, box, { wide: true, sticky: true });
}

function kpNew() {
  if (kpHasContent(KP.k) && !confirm("Начать новое КП? Текущий черновик будет удалён.")) return;
  KP.k = kpEmpty(); KP.source = null; local.del("kp");
  api("DELETE", "/api/kp/draft").catch(() => {});
  renderKP();
}

function kpCheck() {
  const k = KP.k;
  if (!k.customer.trim()) return "Укажите заказчика";
  if (!k.customer_phone.trim()) return "Укажите телефон заказчика";
  if (!k.items.length) return "Добавьте хотя бы одну позицию";
  return null;
}

async function kpPreview(btn) {
  if (!KP.k.items.length) { toast("Добавьте хотя бы одну позицию"); return; }
  await busy(btn, async () => {
    const { blob } = await getBlob("POST", "/api/kp/preview", { kp: KP.k });
    modal("Предпросмотр — первая страница", h("img", { class: "preview-img", src: URL.createObjectURL(blob), alt: "Предпросмотр КП" }), { wide: true });
  }).catch(() => {});
}

async function kpPdf(btn) {
  const p = kpCheck(); if (p) { toast(p); return; }
  await busy(btn, async () => {
    await download("/api/kp/pdf", { kp: KP.k });
    const done = JSON.parse(JSON.stringify(KP.k));
    local.del("kp");
    const close = modal("КП готово ✓", h("div", {},
      h("p", { text: "PDF скачан, копия ушла руководителю. КП сохранено в «Мои КП»." }),
      h("div", { class: "row" },
        h("button", { text: "📈 Добавить в планируемые продажи", onclick: () => { close(); dealEditor(null, {
          client: done.customer + (done.customer_phone ? ", " + done.customer_phone : ""),
          product: done.items.map(it => it.title + (it.material ? ` (${it.material})` : "")).join("; "),
          amount: kpTotal(done), stage: "КП отправлено", status: "green", arrived: todayStr() }); } }),
        h("button", { class: "ghost", text: "✏ Продолжить правку", onclick: () => { close(); kpSave(); } }),
        h("button", { class: "ghost", text: "🆕 Новое КП", onclick: () => { close(); KP.k = kpEmpty(); renderKP(); } }))));
  }).catch(() => {});
}

// ---------- Мои КП ----------------------------------------------------------------------------

async function viewHistory() {
  const d = await api("GET", "/api/kp/history");
  const list = h("div", { class: "list" });
  if (!d.items.length) list.append(h("div", { class: "empty", text: "Здесь появятся КП, которые вы выпустите." }));
  for (const x of d.items) {
    const badge = x.closed ? h("span", { class: "badge " + (x.closed.outcome === "won" ? "ok" : "bad"), text: x.closed.outcome === "won" ? "купил" : "отказ" }) : null;
    list.append(h("div", { class: "it" },
      h("div", { class: "grow" }, h("div", { class: "t" }, `№${x.number} · ${x.customer || "без заказчика"} `, badge),
        h("div", { class: "s", text: `${fmtTs(x.ts)} · ${money(x.sum)}` })),
      h("div", { class: "row", style: "gap:6px" },
        h("button", { class: "small ghost", text: "Открыть", onclick: () => openHistory(x.i) }),
        x.closed ? null : h("button", { class: "icon", text: "✅", title: "Клиент купил", onclick: () => closeKp(x.i, "won") }),
        x.closed ? null : h("button", { class: "icon", text: "❌", title: "Клиент отказался", onclick: () => closeKp(x.i, "lost") }))));
  }
  page("Мои КП", "Откройте КП, чтобы поправить и выпустить заново. ✅ — клиент купил, ❌ — отказался.", list);
}

async function openHistory(i) {
  if (kpHasContent(KP.k) && !confirm("Открыть это КП? Текущий черновик будет заменён.")) return;
  const d = await api("GET", "/api/kp/history/" + i);
  KP.k = d.kp; KP.source = "history"; kpSave();
  location.hash = "#kp";
}

async function closeKp(i, outcome) {
  let reason = "";
  if (outcome === "lost") { reason = prompt("Почему клиент отказался?") || ""; if (!reason.trim()) return; }
  try { await api("POST", `/api/kp/history/${i}/close`, { outcome, reason }); toast("Готово"); viewHistory(); } catch (e) { toast(e.message); }
}

// =================================================================================================
// Доставка
// =================================================================================================

const DL = local.get("delivery") || { tab: "city", city: "msk", items: [], distance_km: 0, lift_mode: "none", floors: "", lift_difficulty: "normal", time_slot: false, carry_extra_m: 0, assembly: true, region: "", regionItems: "" };
const dlSave = () => local.set("delivery", DL);

async function viewDelivery() {
  const tabs = h("div", { class: "tabs" },
    h("button", { class: DL.tab === "city" ? "on" : "", text: "Москва / СПб", onclick: () => { DL.tab = "city"; dlSave(); viewDelivery(); } }),
    h("button", { class: DL.tab === "region" ? "on" : "", text: "Другой регион", onclick: () => { DL.tab = "region"; dlSave(); viewDelivery(); } }));
  page("Стоимость доставки", "Москва и СПб — по нашим тарифам; регионы — по таблице транспортных компаний.", tabs,
    DL.tab === "city" ? dlCity() : dlRegion());
}

function dlCity() {
  const m = S.meta.delivery;
  const wrap = h("div", {});
  const result = h("div", {});
  const items = h("div", { class: "list" });
  function drawItems() {
    items.replaceChildren();
    if (!DL.items.length) items.append(h("div", { class: "empty", text: "Добавьте, что везём: найдите модель в прайсе или выберите тип." }));
    DL.items.forEach((it, i) => {
      const cat = S.meta.categories.find(c => c.key === it.category);
      items.append(h("div", { class: "it" },
        h("div", { class: "grow" }, h("div", { class: "t", text: it.name || cat.label }),
          h("div", { class: "s", text: [cat.label, it.size, it.asm ? "сборка " + money(it.asm) : "", it.parts > 1 ? `частей: ${it.parts}` : ""].filter(Boolean).join(" · ") }),
          it.category === "case" && !it.dims_known ? h("label", { class: "check", style: "font-size:13px" },
            h("input", { type: "checkbox", checked: it.long_case, onchange: e => { it.long_case = e.target.checked; dlSave(); } }), "длиннее 100 см") : null),
        h("label", { class: "row", style: "gap:6px;font-size:13px" }, "шт",
          h("input", { type: "number", min: 1, value: it.qty, style: "width:70px", onchange: e => { it.qty = Math.max(1, parseInt(e.target.value) || 1); dlSave(); } })),
        h("button", { class: "icon danger", text: "🗑", onclick: () => { DL.items.splice(i, 1); dlSave(); drawItems(); drawOptions(); } })));
    });
  }
  const q = h("input", { placeholder: "Модель: диван Джун, кровать Либерти…", onkeydown: e => { if (e.key === "Enter") { e.preventDefault(); find.click(); } } });
  const res = h("div", { class: "results hidden" });
  const find = h("button", { type: "button", class: "small", text: "Найти", onclick: () => busy(find, async () => {
    const d = await api("GET", "/api/delivery/search?q=" + encodeURIComponent(q.value.trim()));
    res.replaceChildren(...(d.items.length ? d.items.map(f => h("button", { type: "button", onclick: () => {
      DL.items.push({ ...f, qty: 1 }); dlSave(); res.classList.add("hidden"); q.value = ""; drawItems(); drawOptions(); } },
      h("div", { text: `${f.name} ${f.size || ""}` }), h("small", { text: f.asm ? "сборка " + money(f.asm) : "без сборки" })))
      : [h("div", { class: "empty", text: "Не нашёл — выберите тип изделия ниже." })]));
    res.classList.remove("hidden");
  }).catch(() => {}) });
  const manual = h("div", { class: "chips" }, ...S.meta.categories.map(c => h("button", { type: "button", class: "chip", text: "+ " + c.label,
    onclick: () => { DL.items.push({ name: "", category: c.key, qty: 1, asm: 0, parts: 1, long_case: false, dims_known: false }); dlSave(); drawItems(); drawOptions(); } })));

  const opts = h("div", {});
  function drawOptions() {
    const anyBig = DL.items.some(it => it.category === "big");
    const asmTotal = DL.items.reduce((s, it) => s + (it.asm || 0) * it.qty, 0);
    opts.replaceChildren(
      field(`Расстояние от МКАД/КАД, км (первые ${m.free_km} км бесплатно, дальше +${m.km_price} ₽/км)`, h("div", {},
        chips([["В пределах", 0], ["20 км", 20], ["30 км", 30], ["50 км", 50]], DL.distance_km, v => { DL.distance_km = v; kmIn.value = v; dlSave(); }),
        (kmIn = input(DL.distance_km, v => { DL.distance_km = parseInt(v) || 0; dlSave(); }, { type: "number", min: 0, style: "margin-top:6px;max-width:160px" })))),
      field("Подъём в квартиру", chips([["Не нужен", "none"], ["На лифте", "elevator"], ["Вручную, без лифта", "manual"]], DL.lift_mode,
        v => { DL.lift_mode = v; dlSave(); drawOptions(); })),
      DL.lift_mode === "manual" ? h("div", { class: "grid2" },
        field("Этаж", input(DL.floors, v => { DL.floors = v; dlSave(); }, { type: "number", min: 1 })),
        anyBig ? field("Подъём дивана/кровати по лестнице", h("select", { onchange: e => { DL.lift_difficulty = e.target.value; dlSave(); } },
          ...S.meta.difficulty.map(d => h("option", { value: d.key, selected: d.key === DL.lift_difficulty, text: `${d.label} — ${money(d.rate)} за часть/этаж` })))) : null) : null,
      field(`Доставка ко времени (+${money(m.time_fee)})`, chips([["Нет", false], ["Да", true]], DL.time_slot, v => { DL.time_slot = v; dlSave(); })),
      field(`Ручной пронос дальше ${m.carry_free} м от машины`, chips([["Нет", 0], ["30 м", 30], ["50 м", 50], ["100 м", 100]], DL.carry_extra_m, v => { DL.carry_extra_m = v; dlSave(); })),
      asmTotal ? field(`Сборка по прайсу: ${money(asmTotal)}`, chips([["Со сборкой", true], ["Без сборки", false]], DL.assembly, v => { DL.assembly = v; dlSave(); })) : null);
  }
  let kmIn;
  drawItems(); drawOptions();
  const go = h("button", { text: "Рассчитать", onclick: () => busy(go, async () => {
    const d = await api("POST", "/api/delivery/quote", { ...DL });
    result.replaceChildren(h("div", { class: "card calc-lines" }, h("span", { class: "label", text: "Расчёт доставки — " + d.city }),
      ...d.lines.map(l => h("div", { class: "line" }, h("span", { text: l.label }), h("b", { text: l.price ? money(l.price) : "" }))),
      h("div", { class: "line total" }, h("span", { text: "Итого" }), h("span", { text: money(d.total) })),
      h("p", { class: "muted", style: "margin:8px 0 0;font-size:13px", text: "Расчёт ушёл руководителю. Точную стоимость подтверждает логист." })));
    result.scrollIntoView({ behavior: "smooth" });
  }).catch(() => {}) });
  wrap.append(
    h("div", { class: "card" }, h("span", { class: "label", text: "Город" }),
      chips([["Москва", "msk"], ["Санкт-Петербург", "spb"]], DL.city, v => { DL.city = v; dlSave(); })),
    h("div", { class: "card" }, h("span", { class: "label", text: "Что везём" }),
      h("div", { class: "field" }, h("div", { class: "row", style: "flex-wrap:nowrap" }, q, find)), res,
      h("div", { style: "margin-bottom:12px" }, h("div", { class: "muted", style: "font-size:13px;margin-bottom:6px", text: "Или тип изделия:" }), manual),
      items),
    h("div", { class: "card" }, h("span", { class: "label", text: "Условия" }), opts),
    h("div", { class: "row" }, go, h("button", { class: "ghost", text: "Очистить", onclick: () => { DL.items = []; dlSave(); viewDelivery(); } })),
    h("div", { style: "height:14px" }), result);
  return wrap;
}

function dlRegion() {
  const wrap = h("div", {});
  const out = h("div", {});
  const city = input(DL.region, v => { DL.region = v; dlSave(); }, { placeholder: "Например: Северодвинск", onkeydown: e => { if (e.key === "Enter") { e.preventDefault(); find.click(); } } });
  const find = h("button", { type: "button", text: "Найти город", onclick: () => busy(find, async () => {
    const d = await api("GET", "/api/delivery/region?city=" + encodeURIComponent(city.value.trim()));
    if (d.type === "home") {
      out.replaceChildren(h("div", { class: "card" }, h("p", { text: `«${city.value}» — ~${d.km} км от центра: это зона нашей доставки (${d.city} + до 50 км). Посчитайте по городскому прайсу.` }),
        h("button", { text: "Считать как " + d.city, onclick: () => { DL.tab = "city"; DL.city = d.code; dlSave(); viewDelivery(); } })));
    } else if (d.type === "none") {
      out.replaceChildren(h("div", { class: "card", text: `«${city.value}» не нашёл ни в таблице, ни на карте. Проверьте написание.` }));
    } else if (d.entries.length === 1) {
      regionItems(d.entries[0]);
    } else {
      out.replaceChildren(h("div", { class: "card" }, h("span", { class: "label", text: "Нашёл несколько — уточните" }),
        h("div", { class: "chips" }, ...d.entries.map(e => h("button", { class: "chip", text: e.city, onclick: () => regionItems(e) })))));
    }
  }).catch(() => {}) });
  function regionItems(e) {
    const note = e.asked ? h("div", { class: "panel", style: "margin-bottom:12px" }, `📍 Города «${e.asked}» нет в таблице. Ближайший — `, h("b", { text: e.city }), ` (~${e.km} км по прямой), считаю по нему.`) : null;
    const items = h("textarea", { value: DL.regionItems || "", placeholder: "Диван 1 шт\nКресло 2 шт", oninput: ev => { DL.regionItems = ev.target.value; dlSave(); } });
    const res = h("div", {});
    const go = h("button", { text: "Показать стоимость", onclick: () => busy(go, async () => {
      const d = await api("POST", "/api/delivery/region", { entry: e, items: items.value });
      res.replaceChildren(h("div", { class: "card result", html: d.html.replace(/\n/g, "<br>") }));
    }).catch(() => {}) });
    out.replaceChildren(h("div", { class: "card" }, note,
      h("div", { class: "result", style: "margin-bottom:12px" }, h("b", { text: "🌍 " + e.city }), `\nОткуда выгоднее отгружать: ${e.from || "—"}`),
      field("Позиции и количество", items), go), res);
  }
  wrap.append(h("div", { class: "card" }, h("div", { class: "field" }, h("span", { text: "Город доставки" }), h("div", { class: "row", style: "flex-wrap:nowrap" }, city, find))), out);
  return wrap;
}

// =================================================================================================
// Заявления: возврат и пени
// =================================================================================================

const DOC = local.get("docs") || { doc: "refund", company: "", kind: "fiz", answers: {} };
const docSave = () => local.set("docs", DOC);

async function viewDocs() {
  if (!DOC.company) DOC.company = S.meta.companies[0].key;
  const tabs = h("div", { class: "tabs" },
    h("button", { class: DOC.doc === "refund" ? "on" : "", text: "📝 Возврат денег", onclick: () => { DOC.doc = "refund"; docSave(); viewDocs(); } }),
    h("button", { class: DOC.doc === "penalty" ? "on" : "", text: "⚖ Выплата пени", onclick: () => { DOC.doc = "penalty"; docSave(); viewDocs(); } }));
  const schema = await api("GET", `/api/docs/schema/${DOC.doc}?kind=${DOC.kind}`);
  const a = DOC.answers;
  const form = h("div", {});
  const calcBox = h("div", { class: "panel hidden", style: "margin-bottom:12px" });
  let calcTimer;

  function visible(f) { return !f.when || a[f.when.key] === f.when.eq; }
  function draw() {
    form.replaceChildren();
    for (const f of schema.fields) {
      if (!visible(f)) continue;
      const label = f.label.replace(/^\d+\.\s*/, "").replace(/\s*—?\s*или нажмите «Нет»:?$/, "").replace(/:$/, "") + (f.required ? " *" : "");
      if (f.kind === "choice") {
        form.append(field(label, chips(f.buttons.map(b => [b.label, b.value]), a[f.key], v => { a[f.key] = v; docSave(); draw(); }), { key: f.key }));
        continue;
      }
      const el = input(a[f.key], v => { a[f.key] = v; if (f.key === "fact_date") delete a.not_delivered; docSave(); if (DOC.doc === "penalty") penaltyCalc(); },
        { placeholder: f.kind === "date" ? "ДД.ММ.ГГГГ" : "", inputmode: ["amount", "digits", "passport", "card"].includes(f.kind) ? "numeric" : null });
      const btns = f.buttons.length ? h("div", { class: "chips", style: "margin-top:6px" }, ...f.buttons.map(b => h("button", { type: "button", class: "chip", text: b.label,
        onclick: () => {
          let v = b.value;
          if (v === "=today") { v = todayStr(); if (f.key === "fact_date") a.not_delivered = true; }
          else if (v.startsWith("=")) v = a[v.slice(1)] || "";
          else if (v === "-") v = "Нет";
          a[f.key] = v; el.value = v; docSave(); if (DOC.doc === "penalty") penaltyCalc();
        } }))) : null;
      form.append(field(label, el, { key: f.key, extra: btns }));
      if (f.key === "amount" && DOC.doc === "penalty") form.append(calcBox);
    }
  }
  async function penaltyCalc() {
    clearTimeout(calcTimer);
    calcTimer = setTimeout(async () => {
      if (!a.contract_sum || !a.ready_date || !a.fact_date) { calcBox.classList.add("hidden"); return; }
      const d = await api("POST", "/api/docs/penalty/calc", { contract_sum: a.contract_sum, ready_date: a.ready_date, fact_date: a.fact_date }).catch(() => null);
      if (!d || !d.deadline) { calcBox.classList.add("hidden"); return; }
      calcBox.replaceChildren(h("div", { text: `🧮 Готовность ${a.ready_date} + 7 раб. дней = срок доставки ${d.deadline}` }),
        h("div", { text: `${a.not_delivered ? "Не доставлен, считаем по" : "Доставлено"} ${a.fact_date} → просрочка ${d.days} дн.` }),
        h("div", {}, `${d.days} дн. × 0,1% × ${money(num(a.contract_sum))} = `, h("b", { text: money(d.amount) })),
        h("button", { type: "button", class: "small", style: "margin-top:8px", text: "Подставить в сумму пени", onclick: () => {
          a.amount = String(d.amount); docSave(); draw(); penaltyCalc(); } }));
      calcBox.classList.remove("hidden");
    }, 400);
  }
  draw();
  if (DOC.doc === "penalty") penaltyCalc();

  const make = h("button", { text: "📄 Скачать заявление (PDF)", onclick: () => busy(make, async () => {
    try {
      await download("/api/docs/" + DOC.doc, { company: DOC.company, kind: DOC.kind, answers: a });
      toast("Готово! Копия ушла руководителю.");
    } catch (e) { if (e.data && e.data.errors) { showErrors(form, e.data.errors); toast("Проверьте поля, отмеченные красным"); } throw e; }
  }).catch(() => {}) });

  page("Заявления", "Поля можно оставлять пустыми, кроме отмеченных *. Введённое сохраняется на этом устройстве.", tabs,
    h("div", { class: "card" },
      field("Организация", chips(S.meta.companies.map(c => [c.label, c.key]), DOC.company, v => { DOC.company = v; docSave(); })),
      field("Покупатель", chips([["👤 Физическое лицо", "fiz"], ["🏢 Юридическое лицо", "yur"]], DOC.kind, v => { DOC.kind = v; docSave(); viewDocs(); }))),
    h("div", { class: "card" }, form),
    h("div", { class: "row" }, make, h("button", { class: "ghost", text: "Очистить форму", onclick: () => { if (confirm("Очистить все поля?")) { DOC.answers = {}; docSave(); viewDocs(); } } })));
}

// =================================================================================================
// Выплата дизайнеру
// =================================================================================================

const PAY = local.get("payout") || { tab: "legal", legal: {}, person: {} };
const paySave = () => local.set("payout", PAY);

async function viewPayout() {
  const tabs = h("div", { class: "tabs" },
    h("button", { class: PAY.tab === "legal" ? "on" : "", text: "🏢 Юрлицо — по счёту", onclick: () => { PAY.tab = "legal"; paySave(); viewPayout(); } }),
    h("button", { class: PAY.tab === "person" ? "on" : "", text: "👤 Физлицо — на карту", onclick: () => { PAY.tab = "person"; paySave(); viewPayout(); } }));
  if (PAY.tab === "legal") {
    const schema = await api("GET", "/api/payout/schema");
    const a = PAY.legal;
    const form = h("div", { class: "card" }, ...schema.fields.map(f => field(f.label + (f.required ? " *" : ""),
      input(a[f.key], v => { a[f.key] = v; paySave(); }, { placeholder: f.kind === "date" ? todayStr() : "" }), { key: f.key })));
    const make = h("button", { text: "📄 Скачать пакет документов", onclick: () => busy(make, async () => {
      try { await download("/api/payout/legal", { answers: a }); toast("Готово! Копия ушла руководителю."); }
      catch (e) { if (e.data && e.data.errors) { showErrors(form, e.data.errors); toast("Проверьте поля"); } throw e; }
    }).catch(() => {}) });
    page("Выплата дизайнеру", "Договор, отчёт, счёт и акт — одним PDF. Пустые необязательные поля будут прочерками.", tabs, form,
      h("div", { class: "row" }, make, h("button", { class: "ghost", text: "Очистить", onclick: () => { PAY.legal = {}; paySave(); viewPayout(); } })));
    return;
  }
  const a = PAY.person;
  const out = h("div", {});
  const form = h("div", { class: "card" },
    field("ФИО клиента или название заказа", input(a.order_name, v => { a.order_name = v; paySave(); })),
    field("ФИО дизайнера", input(a.designer, v => { a.designer = v; paySave(); })),
    field("Банк получателя", input(a.bank, v => { a.bank = v; paySave(); })),
    h("div", { class: "grid2" },
      field("Сумма заказа, ₽ *", input(a.amount, v => { a.amount = v; paySave(); }, { inputmode: "numeric" }), { key: "amount" }),
      field("Процент дизайнера, % *", input(a.discount, v => { a.discount = v; paySave(); }, { inputmode: "decimal" }), { key: "discount" })));
  const go = h("button", { text: "Рассчитать", onclick: () => busy(go, async () => {
    try {
      const d = await api("POST", "/api/payout/person", a);
      out.replaceChildren(h("div", { class: "card calc-lines" },
        h("div", { class: "line" }, h("span", { text: "Вознаграждение" }), h("b", { text: money(d.reward) })),
        h("div", { class: "line" }, h("span", { text: "Налог за вывод (13%)" }), h("b", { text: "−" + money(d.tax) })),
        h("div", { class: "line total" }, h("span", { text: "К переводу на карту" }), h("span", { text: money(d.payout) })),
        h("p", { class: "muted", style: "font-size:13px;margin:8px 0 0", text: "Расчёт ушёл руководителю." })));
    } catch (e) { if (e.data && e.data.errors) showErrors(form, e.data.errors); throw e; }
  }).catch(() => {}) });
  page("Выплата дизайнеру", "Перевод на карту физлицу: вознаграждение = сумма заказа × процент, минус 13%.", tabs, form, go, h("div", { style: "height:14px" }), out);
}

// =================================================================================================
// Ткани
// =================================================================================================

async function viewFabrics() {
  const d = await api("GET", "/api/fabrics/suppliers");
  let supplier = local.get("fabSupplier") || d.suppliers[0];
  const out = h("div", {});
  const q = h("input", { placeholder: "Название ткани, например velutto", onkeydown: e => { if (e.key === "Enter") { e.preventDefault(); go.click(); } } });
  const go = h("button", { text: "Найти", onclick: () => busy(go, async () => {
    const r = await api("GET", `/api/fabrics/search?supplier=${encodeURIComponent(supplier)}&q=${encodeURIComponent(q.value.trim())}`);
    if (!r.items.length) {
      out.replaceChildren(h("div", { class: "card" }, `«${q.value}» у фабрики ${supplier} не нашёл.`,
        r.similar && r.similar.length ? h("div", { style: "margin-top:8px" }, "Похожие: ", ...r.similar.map(s => h("button", { class: "chip", style: "margin:2px", text: s, onclick: () => { q.value = s; go.click(); } }))) : null));
      return;
    }
    out.replaceChildren(...r.items.map(html => h("div", { class: "card result", html: html.replace(/\n/g, "<br>") })),
      r.total > r.items.length ? h("p", { class: "muted", text: `Найдено ${r.total}, показаны первые ${r.items.length} — уточните название.` }) : null);
  }).catch(() => {}) });
  const sel = h("select", { onchange: e => { supplier = e.target.value; local.set("fabSupplier", supplier); } },
    ...d.suppliers.map(s => h("option", { value: s, selected: s === supplier, text: s })));
  page("Найти ткань", "Цена за отрез, категория и цвета в наличии.",
    h("div", { class: "card" }, h("div", { class: "grid2" }, field("Фабрика", sel), field("Ткань", h("div", { class: "row", style: "flex-wrap:nowrap" }, q, go)))), out);
}

// =================================================================================================
// Ценник
// =================================================================================================

const TAG = local.get("tag") || { title: "", length: "", depth: "", height: "", article: "", frame: "", filler: "", price: "", price_from: "" };
const tagSave = () => local.set("tag", TAG);

async function viewTag() {
  const img = h("img", { class: "preview-img hidden", alt: "Предпросмотр ценника" });
  const form = h("div", {});
  function draw() {
    form.replaceChildren(
      priceSearch(p => {
        const d = p.dims || [];
        Object.assign(TAG, { title: p.title, article: p.article, price_from: p.price_from || "", length: d[0] || "", depth: d[1] || "", height: d[2] || "" });
        tagSave(); draw();
      }),
      field("Название *", input(TAG.title, v => { TAG.title = v; tagSave(); })),
      h("div", { class: "grid3" },
        field("Длина, мм", input(TAG.length, v => { TAG.length = v; tagSave(); })),
        field("Глубина, мм", input(TAG.depth, v => { TAG.depth = v; tagSave(); })),
        field("Высота, мм", input(TAG.height, v => { TAG.height = v; tagSave(); }))),
      h("div", { class: "grid3" },
        field("Артикул", input(TAG.article, v => { TAG.article = v; tagSave(); })),
        field("Каркас", input(TAG.frame, v => { TAG.frame = v; tagSave(); })),
        field("Наполнитель", input(TAG.filler, v => { TAG.filler = v; tagSave(); }))),
      h("div", { class: "grid2" },
        field("Цена образца, ₽", input(TAG.price, v => { TAG.price = v; tagSave(); }, { inputmode: "numeric" })),
        field("Крупная цена «от», ₽", input(TAG.price_from, v => { TAG.price_from = v; tagSave(); }, { inputmode: "numeric" }))));
  }
  draw();
  const pv = h("button", { class: "ghost", text: "👁 Предпросмотр", onclick: () => busy(pv, async () => {
    const { blob } = await getBlob("POST", "/api/tag/preview", TAG);
    img.src = URL.createObjectURL(blob); img.classList.remove("hidden"); img.scrollIntoView({ behavior: "smooth" });
  }).catch(() => {}) });
  const pdf = h("button", { text: "📄 PDF", onclick: () => busy(pdf, () => download("/api/tag/pdf", TAG)).catch(() => {}) });
  const docx = h("button", { text: "📝 Word", onclick: () => busy(docx, () => download("/api/tag/docx", TAG)).catch(() => {}) });
  page("Ценник на мебель", "Найдите позицию в прайсе — размеры, артикул и цена подставятся сами.",
    h("div", { class: "card" }, form), h("div", { class: "row" }, pv, pdf, docx), h("div", { style: "height:14px" }), img);
}

// =================================================================================================
// Планируемые продажи
// =================================================================================================

async function viewDeals() {
  const d = await api("GET", "/api/deals");
  const active = d.items.filter(x => !["done", "lost"].includes(x.status));
  const list = h("div", { class: "list" });
  if (!d.items.length) list.append(h("div", { class: "empty", text: "Сделок пока нет." }));
  for (const x of [...d.items].reverse()) {
    const st = S.meta.statuses.find(s => s.key === x.status);
    list.append(h("div", { class: "it" },
      h("div", { class: "grow" }, h("div", { class: "t", text: x.client }),
        h("div", { class: "s", text: [x.product, x.stage, x.planned_date ? "план: " + x.planned_date : ""].filter(Boolean).join(" · ") }),
        x.blocker ? h("div", { class: "s", text: "Что мешает: " + x.blocker }) : null),
      h("div", { style: "text-align:right" }, h("div", { style: "font-weight:700", text: money(x.amount) }), h("div", { class: "s", text: st ? st.label : "" })),
      h("button", { class: "icon", text: "✏", onclick: () => dealEditor(x) })));
  }
  page("Планируемые продажи", `В работе: ${active.length} на ${money(active.reduce((s, x) => s + (x.amount || 0), 0))}. Руководитель получает каждое изменение.`,
    h("div", { class: "row", style: "margin-bottom:12px" }, h("button", { text: "➕ Новая сделка", onclick: () => dealEditor(null) })), list);
}

function dealEditor(deal, prefill) {
  const x = deal ? { ...deal } : { client: "", product: "", amount: "", arrived: todayStr(), stage: "", status: "green", planned_date: "", blocker: "", lost_reason: "", ...(prefill || {}) };
  const box = h("div", {});
  function draw() {
    box.replaceChildren(
      field("Клиент / сделка *", input(x.client, v => x.client = v), { key: "client" }),
      field("Что покупает: модель, ткань, конфигурация *", h("textarea", { value: x.product, oninput: e => x.product = e.target.value }), { key: "product" }),
      h("div", { class: "grid2" },
        field("Потенциальная сумма, ₽", input(x.amount, v => x.amount = v, { inputmode: "numeric" })),
        field("Когда пришёл", input(x.arrived, v => x.arrived = v, { placeholder: "ДД.ММ.ГГГГ" }))),
      h("div", { class: "grid2" },
        field("Стадия", h("select", { onchange: e => x.stage = e.target.value }, h("option", { value: "", text: "—" }),
          ...S.meta.stages.map(s => h("option", { value: s, selected: s === x.stage, text: s })))),
        field("Статус", h("select", { onchange: e => { x.status = e.target.value; draw(); } },
          ...S.meta.statuses.map(s => h("option", { value: s.key, selected: s.key === x.status, text: s.label }))))),
      field("Планируемая дата продажи", input(x.planned_date, v => x.planned_date = v, { placeholder: "ДД.ММ.ГГГГ" })),
      field("Что мешает / следующий шаг", input(x.blocker, v => x.blocker = v)),
      x.status === "lost" ? field("Почему сорвалась", input(x.lost_reason, v => x.lost_reason = v)) : null,
      h("div", { class: "row", style: "justify-content:space-between;margin-top:6px" },
        deal ? h("button", { class: "ghost danger", text: "Удалить", onclick: async () => {
          if (!confirm("Удалить сделку?")) return;
          await api("DELETE", "/api/deals/" + deal.id); close(); if (S.route === "deals") viewDeals(); } }) : h("span"),
        h("button", { text: deal ? "Сохранить" : "Добавить", onclick: e => busy(e.currentTarget, async () => {
          try {
            await api(deal ? "PUT" : "POST", deal ? "/api/deals/" + deal.id : "/api/deals", { ...x, amount: Math.round(num(x.amount)) || null });
            close(); toast("Сохранено"); if (S.route === "deals") viewDeals();
          } catch (err) { if (err.data && err.data.error) toast(err.data.error); throw err; }
        }).catch(() => {}) })));
  }
  draw();
  const close = modal(deal ? "Сделка" : "Новая сделка", box, { wide: true, sticky: true });
}

// =================================================================================================
// Карточки организаций
// =================================================================================================

async function viewCards() {
  const wrap = h("div", {});
  page("Карточка организации", "Реквизиты для клиентов: скачайте Word или скопируйте строку.", wrap);
  for (const c of S.meta.cards) {
    const card = h("div", { class: "card" }, h("span", { class: "label", text: c.short }));
    wrap.append(card);
    try {
      const d = await api("GET", "/api/cards/" + c.key);
      const dl = h("button", { class: "small", text: "📝 Скачать Word", onclick: () => busy(dl, () => download(`/api/cards/${c.key}?format=docx`, undefined, "GET")).catch(() => {}) });
      card.append(h("h3", { text: d.full }),
        h("div", { class: "calc-lines" }, ...d.rows.map(([k, v]) => h("div", { class: "line", style: "cursor:pointer", title: "Нажмите, чтобы скопировать",
          onclick: () => { navigator.clipboard.writeText(v).then(() => toast("Скопировано: " + k)); } }, h("span", { class: "muted", text: k }), h("span", { text: v, style: "text-align:right" })))),
        h("div", { class: "row", style: "margin-top:12px" }, dl,
          h("button", { class: "ghost small", text: "Скопировать всё", onclick: () => navigator.clipboard.writeText(d.full + "\n" + d.rows.map(r => `${r[0]}: ${r[1]}`).join("\n")).then(() => toast("Реквизиты скопированы")) })));
    } catch (e) { card.append(h("p", { class: "muted", text: e.message })); }
  }
}

// =================================================================================================
// Профиль
// =================================================================================================

async function viewProfile() {
  const u = { ...S.user, password: "" };
  const form = h("div", { class: "card" },
    field("ФИО", input(u.name, v => u.name = v)),
    field("Телефон (логин)", input(u.phone, null, { disabled: true })),
    field("Шоу-рум", h("select", { onchange: e => u.showroom = e.target.value },
      ...(await api("GET", "/api/showrooms")).showrooms.map(s => h("option", { value: s, selected: s === u.showroom, text: s })))),
    field("Telegram", input(u.telegram, v => u.telegram = v, { placeholder: "@username" })),
    field("Новый пароль (если хотите сменить)", input("", v => u.password = v, { type: "password", autocomplete: "new-password" }), { key: "password" }));
  const save = h("button", { text: "Сохранить", onclick: () => busy(save, async () => {
    try { const d = await api("PUT", "/api/profile", u); S.user = d.user; renderNav(); toast("Профиль сохранён"); }
    catch (e) { if (e.data && e.data.errors) showErrors(form, e.data.errors); throw e; }
  }).catch(() => {}) });
  page("Мой профиль", "Эти данные подставляются в КП (менеджер, телефон, шоу-рум).", form, h("div", { class: "row" }, save, h("button", { class: "ghost", text: "Выйти", onclick: logout })));
}

boot().catch(e => page("Не удалось открыть кабинет", e.message));
