"use strict";

const state = {
  config: null,
  conversationId: null,
  channel: "web",
  messages: [],
  waiting: false,
  tab: "stats",
  focusSection: null,
  stats: null,
  tickets: [],
  gaps: [],
  kb: [],
  conversations: [],
};

const $ = (selector, root = document) => root.querySelector(selector);

/** Создаёт DOM-узел. Строки всегда вставляются как текст, поэтому сообщения клиентов не исполняются. */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = data.detail.map((item) => item.msg).join("; ");
    } catch { /* тело ответа не JSON */ }
    throw new Error(message);
  }
  return response.json();
}

const timeFmt = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
const dateFmt = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" });

function fmtWhen(iso) {
  const date = new Date(iso);
  const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((dayStart(new Date()) - dayStart(date)) / 86400000);
  const time = timeFmt.format(date);
  if (days === 0) return time;
  if (days === 1) return `вчера, ${time}`;
  return `${dateFmt.format(date)}, ${time}`;
}

function fmtDuration(minutes) {
  const total = Math.max(0, Math.round(minutes));
  if (total < 60) return `${total} мин`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = isError ? "toast error" : "toast";
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, isError ? 7000 : 3000);
}

async function run(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message, true);
    return null;
  } finally {
    if (button) button.disabled = false;
  }
}

// --- Чат клиента --------------------------------------------------------------------------------

function renderChat() {
  const body = $("#chat");
  const nodes = [];
  if (!state.conversationId) {
    nodes.push(h("div", { class: "msg assistant" }, h("div", { class: "bubble" }, state.config.greeting)));
  }
  for (const message of state.messages) {
    if (message.role === "user") {
      nodes.push(h("div", { class: "msg user" }, h("div", { class: "bubble" }, message.text)));
      continue;
    }
    const isOperator = message.role === "operator";
    nodes.push(h("div", { class: `msg ${isOperator ? "operator" : "assistant"}` },
      isOperator ? h("span", { class: "msg-label" }, "Оператор") : null,
      h("div", { class: "bubble" }, message.text),
      message.sources.length ? h("div", { class: "sources" },
        h("span", { class: "msg-label" }, "Источник:"),
        message.sources.map((source) => h("button", {
          class: "chip", type: "button", title: "Открыть раздел базы знаний",
          onclick: () => { state.tab = "kb"; state.focusSection = source.id; renderTabs(); renderTab(); },
        }, `${source.doc_title} → ${source.title}`))) : null,
    ));
    if (message.action === "escalate") {
      nodes.push(h("div", { class: "handoff" }, "Диалог передан оператору. Обращение видно на вкладке «Оператору»"));
    }
  }
  if (state.waiting) nodes.push(h("div", { class: "typing" }, "Помощник печатает…"));
  body.replaceChildren(...nodes);
  body.scrollTop = body.scrollHeight;

  const readOnly = state.channel !== "web";
  $("#chat-text").disabled = readOnly;
  $("#chat-send").disabled = readOnly || state.waiting;
  $("#chat-text").placeholder = readOnly ? "Диалог из Telegram: только просмотр" : "Напишите вопрос…";
  $("#chat-caption").textContent = readOnly ? "диалог из Telegram, просмотр" : "чат на сайте";
  $("#quick").hidden = readOnly || state.messages.length > 0;
}

async function send(text) {
  text = text.trim();
  if (!text || state.waiting || state.channel !== "web") return;
  state.messages.push({ role: "user", text, sources: [] });
  state.waiting = true;
  renderChat();
  try {
    const result = await api("/api/chat", { method: "POST", body: { text, conversation_id: state.conversationId } });
    state.conversationId = result.conversation_id;
    state.messages.push(result.message);
    if (result.ticket) {
      state.tab = "tickets";
      renderTabs();
    }
  } catch (error) {
    state.messages.pop();
    toast(error.message, true);
  } finally {
    state.waiting = false;
    renderChat();
    await refreshOwner();
    $("#chat-text").focus();
  }
}

function newDialog() {
  state.conversationId = null;
  state.channel = "web";
  state.messages = [];
  renderChat();
  $("#chat-text").focus();
}

async function openConversation(id) {
  const conversation = await run(null, () => api(`/api/conversations/${id}`));
  if (!conversation) return;
  state.conversationId = conversation.id;
  state.channel = conversation.channel;
  state.messages = conversation.messages;
  renderChat();
}

/** Подтягивает ответы оператора в открытый диалог. */
async function syncChat() {
  if (!state.conversationId || state.waiting) return;
  try {
    const conversation = await api(`/api/conversations/${state.conversationId}`);
    if (conversation.messages.length !== state.messages.length && !state.waiting) {
      state.messages = conversation.messages;
      renderChat();
    }
  } catch { /* диалог мог быть удалён сбросом демо */ }
}

// --- Панель владельца ---------------------------------------------------------------------------

const TABS = [
  ["stats", "Показатели"],
  ["tickets", "Оператору"],
  ["gaps", "Пробелы в базе"],
  ["kb", "База знаний"],
  ["dialogs", "Диалоги"],
];

async function refreshOwner() {
  const [stats, tickets, gaps, conversations] = await Promise.all([
    api("/api/stats"), api("/api/tickets"), api("/api/gaps"), api("/api/conversations"),
  ]);
  Object.assign(state, { stats, tickets, gaps, conversations });
  renderTabs();
  // Не перерисовываем вкладку, пока оператор набирает ответ.
  const typing = document.activeElement && document.activeElement.tagName === "TEXTAREA" && document.activeElement.value;
  if (!typing) renderTab();
}

function renderTabs() {
  const counts = { tickets: state.stats ? state.stats.open_tickets : 0, gaps: state.gaps.length };
  $("#tabs").replaceChildren(...TABS.map(([id, label]) =>
    h("button", {
      class: "tab", type: "button", "aria-pressed": String(state.tab === id),
      onclick: () => { state.tab = id; state.focusSection = null; renderTabs(); renderTab(); },
    }, label, counts[id] ? h("span", { class: `count ${id === "tickets" ? "hot" : ""}` }, counts[id]) : null)));
}

function renderTab() {
  const body = $("#tab-body");
  const views = { stats: viewStats, tickets: viewTickets, gaps: viewGaps, kb: viewKb, dialogs: viewDialogs };
  body.replaceChildren(...views[state.tab]());
  const focused = body.querySelector("details.focus");
  if (focused) focused.scrollIntoView({ block: "center" });
  else body.scrollTop = 0;
}

function viewStats() {
  const s = state.stats;
  const rate = s.deflection_rate === null ? "—" : `${Math.round(s.deflection_rate * 100)}%`;
  const latency = s.avg_latency_ms < 1000 ? "< 1 с" : `${(s.avg_latency_ms / 1000).toFixed(1).replace(".", ",")} с`;
  const tiles = [
    ["Вопросов клиентов", s.questions, `диалогов: ${s.conversations}`],
    ["Решено без оператора", rate, `${s.resolved_by_bot} из ${s.resolved_by_bot + s.escalated} ответов`],
    ["Передано оператору", s.escalated, s.urgent_tickets ? `срочных ждёт: ${s.urgent_tickets}` : `ждут ответа: ${s.open_tickets}`, s.urgent_tickets ? "bad" : ""],
    ["Время ответа", latency, "клиент не ждёт в очереди"],
    ["Экономия времени", fmtDuration(s.time_saved_minutes), `оценка: ${s.manual_minutes_per_question} мин на вопрос`],
  ];
  const max = Math.max(1, ...s.topics.map((topic) => topic.count));
  return [
    h("div", { class: "tiles" }, tiles.map(([label, value, note, tone]) =>
      h("div", { class: "tile" },
        h("div", { class: "tile-label" }, label),
        h("div", { class: "tile-value" }, value),
        h("div", { class: `tile-note ${tone || ""}` }, note)))),
    h("h3", { class: "section" }, "О чём спрашивают"),
    s.topics.length ? h("div", { class: "bars" }, s.topics.map((topic) =>
      h("div", { class: "bar" },
        h("span", null, topic.topic),
        h("span", { class: "bar-track" }, h("i", { style: `width:${Math.round((topic.count / max) * 100)}%` })),
        h("span", { class: "bar-count" }, topic.count)))) : h("p", { class: "empty" }, "Пока нет ответов по базе знаний"),
    h("h3", { class: "section" }, "Как ассистент принимает решение"),
    h("p", { class: "tab-intro" }, "Ответ по существу уходит клиенту, только если он опирается на раздел базы знаний или на данные заказа. Жалобы, просьбы позвать человека и вопросы без ответа в базе передаются оператору вместе с историей диалога."),
  ];
}

function viewTickets() {
  if (!state.tickets.length) return [h("p", { class: "empty" }, "Обращений к оператору нет")];
  return [
    h("p", { class: "tab-intro" }, "Сюда попадает всё, что ассистент не должен решать сам. Ответ оператора приходит клиенту в тот же чат."),
    h("div", { class: "cards" }, state.tickets.map((ticket) => {
      const isOpen = ticket.status === "open";
      const textarea = h("textarea", { placeholder: "Ответ клиенту от имени оператора", "aria-label": "Ответ оператора" });
      return h("article", { class: `card ${isOpen ? "" : "closed"}` },
        h("div", { class: "card-top" },
          h("span", { class: `badge ${isOpen ? "open" : "done"}` }, isOpen ? "ждёт ответа" : "закрыто"),
          ticket.priority === "high" ? h("span", { class: "badge high" }, "срочно") : null,
          h("span", { class: "badge" }, ticket.reason_label),
          h("span", null, ticket.channel_label),
          h("time", { datetime: ticket.created_at }, fmtWhen(ticket.created_at))),
        h("p", { class: "question" }, ticket.question),
        isOpen ? [
          textarea,
          h("div", { class: "card-actions" },
            h("button", { class: "btn primary small", type: "button", onclick: (event) => run(event.target, async () => {
              await api(`/api/tickets/${ticket.id}/reply`, { method: "POST", body: { text: textarea.value } });
              textarea.value = "";
              toast("Ответ отправлен клиенту");
              await refreshOwner();
              await syncChat();
            }) }, "Ответить клиенту"),
            h("button", { class: "link", type: "button", onclick: () => openConversation(ticket.conversation_id) }, "Открыть диалог")),
        ] : [
          h("p", { class: "operator-answer" }, ticket.operator_reply),
          h("div", { class: "card-actions" },
            h("button", { class: "link", type: "button", onclick: () => openConversation(ticket.conversation_id) }, "Открыть диалог")),
        ]);
    })),
  ];
}

function viewGaps() {
  if (!state.gaps.length) return [h("p", { class: "empty" }, "Пробелов нет: на все вопросы нашёлся ответ в базе знаний")];
  return [
    h("p", { class: "tab-intro" }, "Вопросы, на которые в базе знаний не нашлось ответа. Допишите раздел в папке data/kb, и на следующий такой вопрос ассистент ответит сам."),
    h("ul", { class: "rows" }, state.gaps.map((gap) =>
      h("li", { class: "row" },
        h("span", { class: "row-main", title: gap.question }, gap.question),
        h("span", { class: "row-side" }, `последний раз ${fmtWhen(gap.last_at)}`, h("span", { class: "times", title: "Сколько раз спросили" }, `×${gap.count}`))))),
  ];
}

function viewKb() {
  const docs = new Map();
  for (const section of state.kb) {
    if (!docs.has(section.doc_title)) docs.set(section.doc_title, []);
    docs.get(section.doc_title).push(section);
  }
  return [
    h("p", { class: "tab-intro" }, `В базе ${state.kb.length} разделов. Это обычные текстовые файлы: владелец правит их сам, без программиста.`),
    ...[...docs.entries()].map(([title, sections]) =>
      h("div", { class: "kb-doc" },
        h("h3", { class: "section" }, title),
        sections.map((section) => h("details", { class: section.id === state.focusSection ? "focus" : null, open: section.id === state.focusSection },
          h("summary", null, section.title), h("p", null, section.text))))),
  ];
}

function viewDialogs() {
  if (!state.conversations.length) return [h("p", { class: "empty" }, "Диалогов пока нет")];
  return [
    h("p", { class: "tab-intro" }, "Нажмите на диалог, чтобы открыть его в окне чата слева."),
    h("ul", { class: "rows" }, state.conversations.map((conversation) =>
      h("li", {
        class: "row clickable", tabindex: "0",
        onclick: () => openConversation(conversation.id),
        onkeydown: (event) => { if (event.key === "Enter") openConversation(conversation.id); },
      },
        h("span", { class: "row-main" }, conversation.preview || "без сообщений"),
        h("span", { class: "row-side" },
          conversation.escalated ? h("span", { class: "badge open" }, "с оператором") : null,
          conversation.channel_label, " · ", fmtWhen(conversation.updated_at))))),
  ];
}

// --- Запуск -------------------------------------------------------------------------------------

async function start() {
  try {
    const config = await api("/api/config");
    state.config = config;
    $("#company").textContent = config.company;
    $("#chat-name").textContent = config.company;
    $("#kb-size").textContent = `База знаний: ${config.sections} разделов`;
    const engine = $("#engine");
    if (config.engine === "claude") {
      engine.textContent = `Движок: ${config.model}`;
      engine.classList.add("live");
      engine.title = "Отвечает Claude, опираясь только на базу знаний. Источник каждого ответа проверяется кодом.";
    } else {
      engine.textContent = "Движок: правила, без LLM";
      engine.title = "Демо-режим без ключа API: ответы ищутся по словам вопроса. Добавьте ANTHROPIC_API_KEY в .env, чтобы ассистент понимал вопросы в свободной форме.";
    }
    const banner = $("#banner");
    banner.textContent = config.engine_error ? `Claude недоступен (${config.engine_error}). Отвечает резервный движок правил.` : "";
    banner.hidden = !config.engine_error;
    $("#btn-reset").hidden = !config.demo_mode;

    $("#quick").replaceChildren(...config.quick_questions.map((question) =>
      h("button", { class: "chip", type: "button", onclick: () => send(question) }, question)));
    state.kb = await api("/api/kb");
    // Ссылка вида /?open=3&tab=tickets сразу открывает нужный диалог и вкладку.
    const params = new URLSearchParams(location.search);
    if (TABS.some(([id]) => id === params.get("tab"))) state.tab = params.get("tab");
    renderChat();
    await refreshOwner();
    if (Number(params.get("open"))) await openConversation(Number(params.get("open")));
  } catch (error) {
    toast(`Не удалось загрузить данные: ${error.message}`, true);
    return;
  }

  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-text");
    const text = input.value;
    input.value = "";
    send(text);
  });
  $("#btn-new").addEventListener("click", newDialog);
  $("#btn-reset").addEventListener("click", (event) => run(event.target, async () => {
    await api("/api/demo/reset", { method: "POST" });
    newDialog();
    await refreshOwner();
    toast("Демо-данные обновлены");
  }));
  // Ответы оператора и сообщения из других каналов подтягиваются без перезагрузки страницы.
  setInterval(() => { syncChat(); refreshOwner().catch(() => {}); }, 5000);
}

start();
