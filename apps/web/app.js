/**
 * Annual Report Analyst UI — home (report projects) → /project/{id}
 * Live: users / projects / annual-report ingest
 * Analyst chat: POST /supervisor/stream on the LangGraph service
 */

const API_BASE = "/api/v1";
// Keys predate the rename; changing them would sign every user out.
const STORAGE_KEY = "report_rag_session_v1";

const EXAMPLE_QUESTIONS = [
  "How much did Shell spend on climate change adaptation in 2025?",
  "What was the total FTE count at year end?",
  "Which sustainability goals does the company report, and by when?",
];

const CHAT_HISTORY_PAGE_SIZE = 20;

const DOC_TYPE_LABELS = {
  annual_report: "Annual report",
  other: "Other document",
};

const state = {
  token: null,
  user: null,
  projects: [],
  projectId: null,
  threadId: null,
  documents: [],
  documentId: null,
  chatOpen: true,
  materialsOpen: true,
  chatMaximized: false,
  chatMessages: [],
  chatBusy: false,
  chatHistoryPage: 0,
  chatHistoryPages: 0,
  chatHistoryHasMore: false,
  chatHistoryLoading: false,
  datapointsRefreshing: false,
  docPreview: {
    datapointsOpen: true,
    summaryOpen: true,
    detailsOpen: false,
    originalOpen: true,
    extractedOpen: false,
    activeDocumentId: null,
    loading: false,
    error: null,
    /** @type {Map<string, { objectUrl: string, frame: HTMLIFrameElement, wrapper: HTMLElement }>} */
    cache: new Map(),
    /** @type {Map<string, Promise<void>>} */
    loadingById: new Map(),
  },
  pollTimer: null,
  listPollTimer: null,
  view: "auth",
  routeGen: 0,
  routing: false,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function sameId(a, b) {
  return String(a || "").toLowerCase() === String(b || "").toLowerCase();
}

function setText(sel, value) {
  const el = typeof sel === "string" ? $(sel) : sel;
  if (el) el.textContent = value ?? "";
}

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

function saveSession() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      token: state.token,
      user: state.user,
      projectId: state.projectId,
      chatOpen: state.chatOpen,
      materialsOpen: state.materialsOpen,
      chatMaximized: state.chatMaximized,
    }),
  );
}

function clearSession() {
  localStorage.removeItem(STORAGE_KEY);
}

function applySessionToken(saved) {
  if (!saved?.token) return false;
  const changed = saved.token !== state.token;
  state.token = saved.token;
  if (saved.user) state.user = saved.user;
  return changed;
}

function bindSessionSync() {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    if (!event.newValue) return;
    let saved = null;
    try {
      saved = JSON.parse(event.newValue);
    } catch {
      return;
    }
    if (!applySessionToken(saved)) return;
    toast("API token updated in another tab — continuing with the new token");
  });
}

function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

function setAuthError(msg) {
  const el = $("#auth-error");
  if (!el) return;
  if (!msg) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = msg;
}

function messageFromErrorBody(text, status) {
  const raw = String(text || "").trim();
  if (!raw) return status ? `Request failed (${status})` : "Request failed";
  try {
    const payload = JSON.parse(raw);
    return (
      payload?.message ||
      payload?.error?.message ||
      (typeof payload?.detail === "string" ? payload.detail : null) ||
      raw
    );
  } catch {
    return raw;
  }
}

function handleAuthFailure(reason) {
  if (!state.token) return;
  toast(reason || "Session expired — sign in again with your API token");
  logout();
  setAuthError("Your API token is no longer valid. Paste the current token, or Regenerate a new one.");
}

async function throwIfNotOk(res, fallback) {
  if (res.ok) return;
  const errText = await res.text();
  const msg = messageFromErrorBody(errText, res.status) || fallback || `Request failed (${res.status})`;
  if (res.status === 401) {
    handleAuthFailure(msg);
  }
  const err = new Error(msg);
  err.status = res.status;
  throw err;
}

async function api(path, { method = "GET", body, formData, headers = {} } = {}) {
  const opts = { method, headers: { ...headers } };
  if (state.token) opts.headers.Authorization = `Bearer ${state.token}`;
  if (formData) {
    opts.body = formData;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, opts);
  if (res.status === 204) return null;
  const text = await res.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { message: text };
  }
  if (!res.ok) {
    const msg = payload?.message || payload?.error?.message || `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.payload = payload;
    if (res.status === 401) {
      handleAuthFailure(msg);
    }
    throw err;
  }
  return payload;
}

function langgraphBase() {
  const configured = window.APP_CONFIG?.langgraphBaseUrl;
  return String(configured || "http://localhost:8080").replace(/\/$/, "");
}

async function readSse(response, onEvent) {
  if (!response.body) throw new Error("No stream from the analyst");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const consume = (chunk) => {
    buffer += chunk.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      let eventName = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      const data = dataLines.join("\n");
      if (data) onEvent(eventName, data);
    }
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      consume(decoder.decode());
      if (buffer.trim()) consume("\n\n");
      break;
    }
    consume(decoder.decode(value, { stream: true }));
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shortId(id) {
  if (!id) return "—";
  const s = String(id);
  return s.length > 12 ? `${s.slice(0, 8)}…` : s;
}

function currentProject() {
  return state.projects.find((p) => sameId(p.id, state.projectId)) || null;
}

const STATUS_LABELS = {
  pending: "Pending",
  processing: "Processing",
  extracted: "Extraction completed",
  ingesting: "Ingesting",
  ready: "Ready · indexing",
  completed: "Completed",
  failed: "Failed",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "—";
}

function isSearchable(status) {
  return status === "ready" || status === "completed";
}

function reportMetaLine(doc) {
  const parts = [doc.companyName, doc.reportYear].filter(Boolean);
  return parts.length ? parts.join(" · ") : "";
}

function renderBrandMarks() {
  const tpl = $("#brand-mark-tpl");
  if (!tpl) return;
  $$("[data-brand-mark]").forEach((slot) => {
    if (!slot.firstChild) slot.appendChild(tpl.content.cloneNode(true));
  });
}

/* ---------- routing ---------- */

function parseRoute() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const m = path.match(/^\/project\/([0-9a-fA-F-]{36})$/);
  if (m) return { name: "project", projectId: m[1] };
  return { name: "home" };
}

function navigate(path, { replace = false } = {}) {
  const next = path || "/";
  if (replace) history.replaceState({ path: next }, "", next);
  else if (window.location.pathname !== next) history.pushState({ path: next }, "", next);
  return route();
}

async function route() {
  if (state.routing) {
    // Let the in-flight pass finish, then apply the latest URL once.
    state.routeQueued = true;
    return;
  }
  state.routing = true;
  try {
    do {
      state.routeQueued = false;
      if (!state.token || !state.user) {
        showAuth();
        return;
      }
      const r = parseRoute();
      if (r.name === "project") {
        await openProjectView(r.projectId);
      } else {
        await openHomeView();
      }
    } while (state.routeQueued);
  } catch (err) {
    console.error(err);
    toast(err.message || "Navigation failed");
  } finally {
    state.routing = false;
  }
}

/* ---------- screens ---------- */

function hideAllScreens() {
  const auth = $("#auth-screen");
  const home = $("#home-screen");
  const workspace = $("#workspace");
  if (auth) auth.hidden = true;
  if (home) home.hidden = true;
  if (workspace) workspace.hidden = true;
}

function showAuth() {
  stopPolling();
  stopListPolling();
  hideAllScreens();
  state.view = "auth";
  state.routeGen += 1;
  const auth = $("#auth-screen");
  if (auth) auth.hidden = false;
  setAuthTab("create");
  const reveal = $("#token-reveal");
  if (reveal) reveal.hidden = true;
  const tabs = $(".tabs");
  if (tabs) tabs.hidden = false;
  setAuthError("");
}

async function openHomeView() {
  stopPolling();
  stopListPolling();
  hideAllScreens();
  state.view = "home";
  state.routeGen += 1;
  state.projectId = null;
  state.threadId = null;
  state.chatMessages = [];
  resetChatHistoryState();
  state.documents = [];
  state.documentId = null;
  resetDocPreviewUi();
  const home = $("#home-screen");
  if (home) home.hidden = false;
  setText("#home-user-chip", state.user?.displayName || state.user?.email || "Signed in");
  saveSession();
  await refreshProjects();
  renderProjectCards();
}

function showWorkspace() {
  hideAllScreens();
  state.view = "project";
  const workspace = $("#workspace");
  if (workspace) workspace.hidden = false;
  setText("#user-chip", state.user?.displayName || state.user?.email || "Signed in");
  applyLayoutChrome();
  updateChatContext();
  renderChat();
}

function showTokenReveal(token) {
  const tabs = $(".tabs");
  if (tabs) tabs.hidden = true;
  const create = $("#form-create-user");
  const tokenForm = $("#form-token");
  const regen = $("#form-regen");
  const reveal = $("#token-reveal");
  if (create) create.hidden = true;
  if (tokenForm) tokenForm.hidden = true;
  if (regen) regen.hidden = true;
  if (reveal) reveal.hidden = false;
  const input = $("#revealed-token");
  if (input) input.value = token;
  setAuthError("");
}

function applyLayoutChrome() {
  const layout = $("#layout");
  if (!layout) return;
  layout.classList.toggle("chat-collapsed", !state.chatOpen && !state.chatMaximized);
  layout.classList.toggle("materials-collapsed", !state.materialsOpen && !state.chatMaximized);
  layout.classList.toggle("chat-maximized", state.chatMaximized);

  const maxBtn = $("#btn-maximize-chat");
  if (maxBtn) {
    maxBtn.textContent = state.chatMaximized ? "❐" : "⛶";
    maxBtn.setAttribute("aria-label", state.chatMaximized ? "Restore split view" : "Maximize chat");
    maxBtn.title = state.chatMaximized ? "Restore" : "Maximize";
  }

  const materialsBtn = $("#btn-toggle-materials");
  if (materialsBtn) {
    const pressed = state.materialsOpen && !state.chatMaximized;
    materialsBtn.setAttribute("aria-pressed", String(pressed));
    materialsBtn.classList.toggle("active", pressed);
  }
  const chatBtn = $("#btn-toggle-chat");
  if (chatBtn) {
    const pressed = state.chatOpen || state.chatMaximized;
    chatBtn.setAttribute("aria-pressed", String(pressed));
    chatBtn.classList.toggle("active", pressed);
  }
}

function updateChatContext() {
  const userEl = $("#chip-user-id");
  const projEl = $("#chip-project-id");
  const threadEl = $("#chip-thread-id");
  if (userEl) {
    userEl.textContent = shortId(state.user?.id);
    userEl.title = state.user?.id || "";
  }
  if (projEl) {
    projEl.textContent = shortId(state.projectId);
    projEl.title = state.projectId || "";
  }
  if (threadEl) {
    threadEl.textContent = shortId(state.threadId);
    threadEl.title = state.threadId || "";
  }
}

function isChatNearBottom(root, threshold = 96) {
  if (!root) return true;
  return root.scrollHeight - root.scrollTop - root.clientHeight <= threshold;
}

function isChatNearTop(root, threshold = 80) {
  if (!root) return false;
  return root.scrollTop <= threshold;
}

function chatMessageKey(msg) {
  return `${msg.role || ""}\0${msg.type || "text"}\0${msg.text || msg.content || ""}`;
}

function mapHistorySearchEvents(events) {
  return (events || [])
    .filter((event) => (event?.type || event?.event) === "ReportSearched")
    .map((event) => ({
      id: event.id || crypto.randomUUID(),
      query: event.query || "",
      status: "done",
      sources: (event.sources || []).map(normalizeSearchSource),
    }));
}

function mapHistoryMessages(items) {
  return (items || []).map((item) => {
    const role = item.role === "user" ? "user" : "assistant";
    const searches = role === "assistant" ? mapHistorySearchEvents(item.events) : [];
    return {
      id: crypto.randomUUID(),
      role,
      type: "text",
      text: item.content || "",
      fromHistory: true,
      ...(searches.length ? { searches } : {}),
    };
  });
}

function resetChatHistoryState() {
  state.chatHistoryPage = 0;
  state.chatHistoryPages = 0;
  state.chatHistoryHasMore = false;
  state.chatHistoryLoading = false;
}

function setChatMessages(messages, { scrollToBottom = false, preserveScrollAnchor = false } = {}) {
  state.chatMessages = messages;
  renderChat({ scrollToBottom, preserveScrollAnchor });
}

function appendChatMessage(msg) {
  setChatMessages(
    [
      ...state.chatMessages,
      { id: msg.id || crypto.randomUUID(), createdAt: Date.now(), ...msg },
    ],
    { scrollToBottom: msg.role === "user" },
  );
}

function patchChatMessage(id, patch) {
  setChatMessages(state.chatMessages.map((item) => (item.id === id ? { ...item, ...patch } : item)));
}

async function fetchChatHistoryPage(page) {
  const params = new URLSearchParams({
    thread_id: state.threadId,
    user_id: state.user.id,
    project_id: state.projectId,
    page: String(page),
    size: String(CHAT_HISTORY_PAGE_SIZE),
  });
  const res = await fetch(`${langgraphBase()}/supervisor/history?${params}`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  await throwIfNotOk(res, `Could not load chat history (${res.status})`);
  return res.json();
}

async function loadChatHistory() {
  if (!state.token || !state.user?.id || !state.projectId || !state.threadId) {
    resetChatHistoryState();
    setChatMessages([]);
    return;
  }
  // Never clobber an in-flight SSE turn — history would drop the live assistant bubble.
  if (state.chatBusy) return;
  state.chatHistoryLoading = true;
  renderChatHistoryStatus();
  try {
    const payload = await fetchChatHistoryPage(1);
    const data = payload?.data || {};
    const messages = mapHistoryMessages(data.messages);
    state.chatHistoryPage = Number(data.page) || 1;
    state.chatHistoryPages = Number(data.pages) || 0;
    state.chatHistoryHasMore = state.chatHistoryPage < state.chatHistoryPages;
    setChatMessages(messages, { scrollToBottom: true });
  } finally {
    state.chatHistoryLoading = false;
    renderChatHistoryStatus();
  }
}

async function loadOlderChatHistory() {
  if (
    state.chatHistoryLoading ||
    !state.chatHistoryHasMore ||
    !state.token ||
    !state.user?.id ||
    !state.projectId ||
    !state.threadId
  ) {
    return;
  }
  const nextPage = state.chatHistoryPage + 1;
  if (nextPage < 2 || (state.chatHistoryPages && nextPage > state.chatHistoryPages)) {
    state.chatHistoryHasMore = false;
    renderChatHistoryStatus();
    return;
  }

  state.chatHistoryLoading = true;
  renderChatHistoryStatus();
  try {
    const payload = await fetchChatHistoryPage(nextPage);
    const data = payload?.data || {};
    const older = mapHistoryMessages(data.messages);
    const existingKeys = new Set(state.chatMessages.map(chatMessageKey));
    const uniqueOlder = older.filter((msg) => !existingKeys.has(chatMessageKey(msg)));
    state.chatHistoryPage = Number(data.page) || nextPage;
    state.chatHistoryPages = Number(data.pages) || state.chatHistoryPages;
    state.chatHistoryHasMore = state.chatHistoryPage < state.chatHistoryPages;
    if (uniqueOlder.length) {
      setChatMessages([...uniqueOlder, ...state.chatMessages], { preserveScrollAnchor: true });
    } else {
      renderChatHistoryStatus();
    }
  } catch (err) {
    toast(err.message || "Could not load older messages");
    renderChatHistoryStatus();
  } finally {
    state.chatHistoryLoading = false;
    renderChatHistoryStatus();
  }
}

function renderChatHistoryStatus() {
  const root = $("#chat-messages");
  if (!root) return;
  let status = root.querySelector(".chat-history-status");
  if (!state.chatHistoryHasMore && !state.chatHistoryLoading) {
    status?.remove();
    return;
  }
  if (!status) {
    status = document.createElement("div");
    status.className = "chat-history-status";
    root.prepend(status);
  }
  if (state.chatHistoryLoading) {
    status.textContent = "Loading older messages…";
    status.classList.add("loading");
  } else if (state.chatHistoryHasMore) {
    status.textContent = "Scroll up for older messages";
    status.classList.remove("loading");
  }
}

function chatGreetingText() {
  const p = currentProject();
  return p
    ? `Hi, I’m your report analyst for “${p.name}”. Ask about any annual report in this project and I’ll answer with verbatim quotes and page citations.`
    : "Hi, I’m your report analyst. Ask about this project’s annual reports.";
}

function inlineMarkdown(text) {
  let s = escapeHtml(text);
  s = s.replace(
    /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
  );
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/\\\$/g, "$");
  return s;
}

function markdownToHtml(raw) {
  const lines = String(raw || "").replaceAll("\r\n", "\n").split("\n");
  const out = [];
  let para = [];
  let list = null;

  const flushPara = () => {
    if (!para.length) return;
    out.push(`<p>${para.map(inlineMarkdown).join("<br />")}</p>`);
    para = [];
  };
  const flushList = () => {
    if (!list) return;
    out.push(`<${list.tag}>${list.items.join("")}</${list.tag}>`);
    list = null;
  };

  for (const line of lines) {
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    if (heading) {
      flushPara();
      flushList();
      const level = heading[1].length;
      out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      const item = ul ? ul[1] : ol[1];
      if (!list || list.tag !== tag) {
        flushList();
        list = { tag, items: [] };
      }
      list.items.push(`<li>${inlineMarkdown(item)}</li>`);
      continue;
    }
    if (quote) {
      flushPara();
      flushList();
      out.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }
    flushList();
    para.push(line);
  }
  flushPara();
  flushList();
  return out.join("");
}

function formatPageRange(pageStart, pageEnd) {
  if (pageStart == null && pageEnd == null) return "";
  if (pageStart != null && pageEnd != null && Number(pageStart) !== Number(pageEnd)) {
    return `pp. ${pageStart}–${pageEnd}`;
  }
  return `p. ${pageStart ?? pageEnd}`;
}

function normalizeSearchSource(source) {
  return {
    document_id: source.document_id || source.documentId || null,
    filename: source.filename || source.originalFilename || "report",
    section: source.section || null,
    page_start: source.page_start ?? source.pageStart ?? null,
    page_end: source.page_end ?? source.pageEnd ?? null,
  };
}

function upsertChatSearch(assistantId, search) {
  const msg = state.chatMessages.find((item) => item.id === assistantId);
  const searches = [...(msg?.searches || [])];
  const idx = searches.findIndex((item) => item.id === search.id);
  if (idx >= 0) searches[idx] = { ...searches[idx], ...search };
  else searches.push(search);
  patchChatMessage(assistantId, { searches });
}

function collectSearchSources(searches) {
  const byKey = new Map();
  for (const search of searches || []) {
    for (const raw of search.sources || []) {
      const source = normalizeSearchSource(raw);
      const key = source.document_id || source.filename;
      if (!byKey.has(key)) {
        byKey.set(key, {
          filename: source.filename,
          pages: [],
          sections: [],
        });
      }
      const entry = byKey.get(key);
      const pages = formatPageRange(source.page_start, source.page_end);
      if (pages && !entry.pages.includes(pages)) entry.pages.push(pages);
      if (source.section && !entry.sections.includes(source.section)) {
        entry.sections.push(source.section);
      }
    }
  }
  return [...byKey.values()];
}

function renderSearchCard(searches) {
  if (!searches?.length) return "";
  const searching = searches.some((item) => item.status === "searching");
  const queries = [...new Set(searches.map((item) => item.query).filter(Boolean))];
  const sources = collectSearchSources(searches);
  const queryHtml = queries.length
    ? `<div class="chat-search-query">${escapeHtml(queries.join(" · "))}</div>`
    : "";
  if (searching && !sources.length) {
    return `<div class="chat-search searching">
      <div class="chat-search-status">Searching reports…</div>
      ${queryHtml}
    </div>`;
  }
  if (!sources.length) {
    return `<div class="chat-search">
      <div class="chat-search-status">No matching passages</div>
      ${queryHtml}
    </div>`;
  }
  const items = sources
    .map((source) => {
      const pages = source.pages.length
        ? `<span class="chat-search-pages">${escapeHtml(source.pages.join(", "))}</span>`
        : "";
      const section = source.sections[0]
        ? `<span class="chat-search-section">${escapeHtml(source.sections[0])}</span>`
        : "";
      return `<li>
        <span class="chat-search-file">${escapeHtml(source.filename)}</span>
        ${pages}${section}
      </li>`;
    })
    .join("");
  const status = searching ? "Searching reports…" : "Sources used";
  return `<div class="chat-search${searching ? " searching" : ""}">
    <div class="chat-search-status">${status}</div>
    ${queryHtml}
    <ul class="chat-search-list">${items}</ul>
  </div>`;
}

function renderChatItem(m) {
  if (m.type === "source") {
    const name = m.filename || "source";
    return `<div class="chat-row user">
      <div class="chat-file" title="${escapeHtml(name)}">
        <span class="chat-file-icon" aria-hidden="true"></span>
        <span class="chat-file-name">${escapeHtml(name)}</span>
      </div>
    </div>`;
  }
  const role = m.role === "user" ? "user" : "assistant";
  const streaming = m.streaming ? " streaming" : "";
  const searchesHtml = role === "assistant" ? renderSearchCard(m.searches) : "";
  const showBubble = Boolean(m.text) || (m.streaming && !searchesHtml);
  const body =
    role === "assistant" && m.text ? markdownToHtml(m.text) : escapeHtml(m.text || "");
  const bubble = showBubble
    ? `<div class="chat-bubble${role === "assistant" ? " markdown" : ""}${streaming}">${body}</div>`
    : "";
  return `<div class="chat-row ${role}">${searchesHtml}${bubble}</div>`;
}

function renderChat({ scrollToBottom = false, preserveScrollAnchor = false } = {}) {
  const root = $("#chat-messages");
  if (!root) return;
  const messages = state.chatMessages;
  const previousScrollTop = root.scrollTop;
  const previousScrollHeight = root.scrollHeight;
  const stickToBottom = scrollToBottom || (!preserveScrollAnchor && isChatNearBottom(root));
  if (!messages.length) {
    root.innerHTML = `
      <div class="chat-placeholder">
        <p id="chat-greeting">${escapeHtml(chatGreetingText())}</p>
        <p class="muted small">Try asking:</p>
        <div class="suggestions">
          ${EXAMPLE_QUESTIONS.map(
            (q) => `<button type="button" class="suggestion" data-prompt="${escapeHtml(q)}">${escapeHtml(q)}</button>`,
          ).join("")}
        </div>
      </div>`;
    return;
  }
  root.innerHTML = messages.map(renderChatItem).join("");
  renderChatHistoryStatus();
  if (stickToBottom) {
    root.scrollTop = root.scrollHeight;
  } else if (preserveScrollAnchor) {
    root.scrollTop = root.scrollHeight - previousScrollHeight + previousScrollTop;
  } else {
    root.scrollTop = previousScrollTop;
  }
}

async function postChatMessage(text) {
  const message = String(text || "").trim();
  if (!message) return;
  if (!state.projectId || !state.threadId || !state.user?.id) {
    toast("Open a project first");
    return;
  }
  if (state.chatBusy) return;
  state.chatBusy = true;
  const sendBtn = $(".composer-send");
  if (sendBtn) sendBtn.disabled = true;

  appendChatMessage({ role: "user", type: "text", text: message });
  const assistantId = crypto.randomUUID();
  appendChatMessage({
    role: "assistant",
    type: "text",
    text: "",
    id: assistantId,
    streaming: true,
  });

  try {
    const res = await fetch(`${langgraphBase()}/supervisor/stream`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${state.token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        message,
        thread_id: state.threadId,
        user_id: state.user.id,
        project_id: state.projectId,
        document_id: state.documentId || null,
        document_name: (() => {
          const open = state.documents.find((d) => sameId(d.id, state.documentId));
          return open ? open.originalFilename || open.title || null : null;
        })(),
        mode: "chat",
        prompt: "main",
      }),
    });
    if (!res.ok) {
      const errText = await res.text();
      const msg = messageFromErrorBody(errText, res.status);
      if (res.status === 401) {
        handleAuthFailure(msg);
      }
      const err = new Error(msg || `Analyst request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    let acc = "";
    await readSse(res, (eventName, data) => {
      if (data === "[DONE]") return;
      let payload = null;
      try {
        payload = JSON.parse(data);
      } catch {
        return;
      }
      const kind = payload.type || eventName;
      if (kind === "error") {
        throw new Error(payload.content || "Analyst stream error");
      }
      if (kind === "ReportSearchStarted") {
        upsertChatSearch(assistantId, {
          id: payload.id,
          query: payload.query || "",
          status: "searching",
          sources: [],
        });
        return;
      }
      if (kind === "ReportSearched") {
        upsertChatSearch(assistantId, {
          id: payload.id,
          query: payload.query || "",
          status: "done",
          sources: (payload.sources || []).map(normalizeSearchSource),
        });
        return;
      }
      if (kind === "ConversationDelta" && payload.content) {
        acc += payload.content;
        patchChatMessage(assistantId, { text: acc, streaming: true });
      }
    });
    patchChatMessage(assistantId, {
      text: acc || "No response from the analyst.",
      streaming: false,
    });
  } catch (err) {
    const msg = err.message || "The analyst could not answer.";
    patchChatMessage(assistantId, {
      text: err.status === 401
        ? "Your API token is no longer valid. Sign in again with the current token (or Regenerate), then retry."
        : msg,
      streaming: false,
    });
  } finally {
    state.chatBusy = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}

function setAuthTab(tab) {
  $$("[data-auth-tab]").forEach((b) =>
    b.classList.toggle("active", b.dataset.authTab === tab),
  );
  const create = $("#form-create-user");
  const tokenForm = $("#form-token");
  const regen = $("#form-regen");
  const reveal = $("#token-reveal");
  const tabs = $(".tabs");
  if (create) create.hidden = tab !== "create";
  if (tokenForm) tokenForm.hidden = tab !== "token";
  if (regen) regen.hidden = tab !== "regen";
  if (reveal) reveal.hidden = true;
  if (tabs) tabs.hidden = false;
  setAuthError("");
}

function logout() {
  stopPolling();
  stopListPolling();
  clearSession();
  state.token = null;
  state.user = null;
  state.projects = [];
  state.projectId = null;
  state.threadId = null;
  state.chatMessages = [];
  resetChatHistoryState();
  state.documents = [];
  state.documentId = null;
  resetDocPreviewUi();
  state.routeGen += 1;
  $("#form-create-user")?.reset();
  $("#form-token")?.reset();
  $("#form-regen")?.reset();
  history.replaceState({ path: "/" }, "", "/");
  showAuth();
  toast("Signed out — use your saved token or Regenerate");
}

/* ---------- bootstrap ---------- */

async function bootstrap() {
  const saved = loadSession();
  if (saved?.token) {
    state.token = saved.token;
    state.chatOpen = saved.chatOpen !== false;
    state.materialsOpen = saved.materialsOpen !== false;
    state.chatMaximized = saved.chatMaximized === true;
    try {
      const me = await api("/me");
      state.user = me.data;
      saveSession();
      await route();
      return;
    } catch {
      clearSession();
      state.token = null;
      state.user = null;
    }
  }
  showAuth();
}

/* ---------- auth ---------- */

function bindAuth() {
  $$("[data-auth-tab]").forEach((btn) => {
    btn.addEventListener("click", () => setAuthTab(btn.dataset.authTab));
  });

  $("#form-create-user")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    setAuthError("");
    const fd = new FormData(e.target);
    try {
      const res = await api("/users", {
        method: "POST",
        body: {
          email: fd.get("email"),
          displayName: fd.get("displayName"),
        },
      });
      state.token = res.data.apiToken;
      state.user = {
        id: res.data.id,
        email: res.data.email,
        displayName: res.data.displayName,
      };
      saveSession();
      showTokenReveal(res.data.apiToken);
      toast("Account created — copy your token before continuing");
    } catch (err) {
      if (err.status === 409) {
        setAuthError("That email already exists. Use “Use token” or “Regenerate”.");
        setAuthTab("regen");
      } else {
        setAuthError(err.message);
      }
    }
  });

  $("#form-token")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    setAuthError("");
    const token = new FormData(e.target).get("token")?.toString().trim();
    state.token = token;
    try {
      const me = await api("/me");
      state.user = me.data;
      saveSession();
      await navigate("/", { replace: true });
    } catch (err) {
      state.token = null;
      setAuthError(err.message);
    }
  });

  $("#form-regen")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    setAuthError("");
    const email = new FormData(e.target).get("email")?.toString().trim();
    try {
      const res = await api("/users/regenerate-token", {
        method: "POST",
        body: { email },
      });
      state.token = res.data.apiToken;
      state.user = {
        id: res.data.id,
        email: res.data.email,
        displayName: res.data.displayName,
      };
      saveSession();
      showTokenReveal(res.data.apiToken);
      toast("New token issued — copy it before continuing");
    } catch (err) {
      setAuthError(err.message);
    }
  });

  $("#btn-copy-token")?.addEventListener("click", async () => {
    const token = $("#revealed-token")?.value || "";
    try {
      await navigator.clipboard.writeText(token);
      toast("Token copied");
    } catch {
      $("#revealed-token")?.select();
      toast("Select the token and copy manually (Ctrl+C)");
    }
  });

  $("#btn-enter-after-token")?.addEventListener("click", () => navigate("/", { replace: true }));
  $("#btn-logout")?.addEventListener("click", logout);
  $("#btn-logout-home")?.addEventListener("click", logout);
}

/* ---------- projects ---------- */

async function refreshProjects() {
  const res = await api("/projects");
  state.projects = (res.data || []).map((p) => ({
    ...p,
    id: String(p.id),
    threadId: p.threadId ? String(p.threadId) : null,
    userId: p.userId ? String(p.userId) : null,
  }));
}

function renderProjectCards() {
  const root = $("#project-cards");
  if (!root) return;
  root.innerHTML = "";
  if (!state.projects.length) {
    root.innerHTML =
      `<div class="empty-card">
        <strong>No report projects yet</strong>
        <p class="muted small">Create a project, upload one or more annual-report PDFs, and start asking questions.</p>
      </div>`;
    return;
  }
  const sorted = [...state.projects].sort((a, b) =>
    String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || "")),
  );
  for (const p of sorted) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "project-card";
    card.dataset.projectId = String(p.id);
    card.innerHTML = `
      <span class="project-card-icon" aria-hidden="true"></span>
      <strong>${escapeHtml(p.name)}</strong>
      <span class="muted small">Updated ${escapeHtml(formatDate(p.updatedAt || p.createdAt))}</span>
      <span class="project-card-cta small">Open analysis →</span>
    `;
    root.appendChild(card);
  }
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

function onProjectSelectChange(e) {
  const id = e.target.value;
  if (!id || sameId(id, state.projectId)) return;
  navigate(`/project/${id}`);
}

function renderProjectSelect() {
  const sel = $("#project-select");
  if (!sel) return;
  sel.removeEventListener("change", onProjectSelectChange);
  const current = state.projectId ? String(state.projectId) : "";
  sel.innerHTML = "";
  for (const p of state.projects) {
    const opt = document.createElement("option");
    opt.value = String(p.id);
    opt.textContent = p.name;
    sel.appendChild(opt);
  }
  if (current && state.projects.some((p) => sameId(p.id, current))) {
    sel.value = current;
  }
  sel.addEventListener("change", onProjectSelectChange);
}

async function openProjectView(projectId) {
  const wanted = String(projectId);
  const gen = ++state.routeGen;
  stopPolling();
  state.documents = [];
  state.documentId = null;
  resetDocPreviewUi();
  state.projectId = wanted;
  const known = state.projects.find((x) => sameId(x.id, wanted));
  state.threadId = known?.threadId ? String(known.threadId) : null;
  showWorkspace();
  renderSources();
  renderMain();
  updateChatContext();

  await refreshProjects();
  if (gen !== state.routeGen) return;

  const p = state.projects.find((x) => sameId(x.id, wanted));
  if (!p) {
    toast("Project not found");
    await navigate("/", { replace: true });
    return;
  }
  state.projectId = String(p.id);
  state.threadId = p.threadId ? String(p.threadId) : null;
  saveSession();
  renderProjectSelect();
  updateChatContext();
  try {
    await loadChatHistory();
  } catch (err) {
    resetChatHistoryState();
    setChatMessages([]);
    toast(err.message || "Could not load chat history");
  }
  await refreshDocuments();
  if (gen !== state.routeGen) return;
  startListPolling();
}

async function createProject(name) {
  const res = await api("/projects", { method: "POST", body: { name } });
  await refreshProjects();
  toast("Project created");
  await navigate(`/project/${res.data.id}`);
}

function bindProjects() {
  $("#btn-home-new-project")?.addEventListener("click", () => {
    const form = $("#form-home-new-project");
    if (!form) return;
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector("input")?.focus();
  });

  $("#form-home-new-project")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = new FormData(e.target).get("name")?.toString().trim();
    try {
      e.target.reset();
      e.target.hidden = true;
      await createProject(name);
    } catch (err) {
      toast(err.message);
    }
  });

  $("#project-cards")?.addEventListener("click", (e) => {
    const card = e.target.closest("[data-project-id]");
    if (!card) return;
    const id = card.dataset.projectId;
    if (id) navigate(`/project/${id}`);
  });

  $$("[data-nav=home]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      navigate("/");
    });
  });
}

/* ---------- documents ---------- */

function isInFlightStatus(status) {
  return ["pending", "processing", "extracted", "ingesting", "ready"].includes(status);
}

function awaitingDatapoints(doc) {
  // After `completed`, the datapoints job may still be writing key_datapoints.
  if (doc.processStatus !== "completed") return false;
  const points = keyDatapointsFromDoc(doc);
  const fteOk = points?.fte && points.fte.value != null;
  const goals = points?.sustainability_goals || points?.sustainabilityGoals;
  const goalsOk = Array.isArray(goals) && goals.length > 0;
  // Filled cards — no need to keep polling.
  if (fteOk || goalsOk) return false;
  const started = Number(doc._datapointsWaitStartedAt || 0);
  if (!started) {
    doc._datapointsWaitStartedAt = Date.now();
    return true;
  }
  return Date.now() - started < 180_000;
}

function showsSpinner(status) {
  return ["pending", "processing", "extracted", "ingesting"].includes(status);
}

async function refreshDocuments() {
  const projectId = state.projectId;
  if (!projectId) return;
  const [docsRes, projRes] = await Promise.all([
    api(`/projects/${projectId}/documents?status=pending&status=processing&status=extracted&status=ingesting&status=ready&status=completed&status=failed`),
    api(`/projects/${projectId}`),
  ]);
  if (!sameId(projectId, state.projectId)) return;

  const previousWait = new Map(
    (state.documents || [])
      .filter((d) => d._datapointsWaitStartedAt)
      .map((d) => [String(d.id), d._datapointsWaitStartedAt]),
  );
  state.documents = (docsRes.data || []).map((doc) => {
    const waitStarted = previousWait.get(String(doc.id));
    return waitStarted ? { ...doc, _datapointsWaitStartedAt: waitStarted } : doc;
  });
  if (projRes.data?.threadId) state.threadId = String(projRes.data.threadId);
  updateChatContext();

  const pending = projRes.data?.pendingSourcesCount ?? 0;
  const badge = $("#pending-badge");
  if (badge) {
    if (pending > 0) {
      badge.hidden = false;
      badge.textContent = `${pending} report${pending === 1 ? "" : "s"} processing…`;
    } else {
      badge.hidden = true;
    }
  }
  renderSources();
  renderMain();
  const busy = state.documents.some(
    (d) => isInFlightStatus(d.processStatus) || awaitingDatapoints(d),
  );
  if (busy) startPolling();
  else stopPolling();
}

function renderSources() {
  const list = $("#source-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.documents.length) {
    list.innerHTML = `<li class="muted small" style="cursor:default">No annual reports yet. Use + or drop a PDF.</li>`;
    return;
  }
  for (const d of state.documents) {
    const li = document.createElement("li");
    const loading = showsSpinner(d.processStatus);
    li.className = "source-item";
    if (loading) li.classList.add("loading");
    if (d._new) li.classList.add("new-source");
    li.classList.toggle("active", sameId(d.id, state.documentId));
    li.title = d.title || d.originalFilename || "";
    const reportMeta = reportMetaLine(d);
    li.innerHTML = `
      <span class="source-row">
        ${loading ? `<span class="spinner" aria-hidden="true"></span>` : `<span class="source-doc-icon" aria-hidden="true"></span>`}
        <span class="source-text">
          <strong>${escapeHtml(d.title)}</strong>
          ${reportMeta ? `<span class="meta">${escapeHtml(reportMeta)}</span>` : ""}
          <span class="meta status-text ${escapeHtml(d.processStatus)}">${escapeHtml(statusLabel(d.processStatus))}</span>
        </span>
      </span>`;
    li.addEventListener("click", () => {
      state.documentId = d.id;
      renderSources();
      renderMain();
    });
    list.appendChild(li);
  }
}

function clearDocPreviewCache() {
  for (const entry of state.docPreview.cache.values()) {
    if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
    entry.wrapper?.remove();
  }
  state.docPreview.cache.clear();
  state.docPreview.loadingById.clear();
  state.docPreview.activeDocumentId = null;
  state.docPreview.loading = false;
  state.docPreview.error = null;
  $("#pdf-preview-stash")?.replaceChildren();
}

function resetDocPreviewUi() {
  state.docPreview.datapointsOpen = true;
  state.docPreview.summaryOpen = true;
  state.docPreview.detailsOpen = false;
  state.docPreview.originalOpen = true;
  state.docPreview.extractedOpen = false;
  clearDocPreviewCache();
}

function detectedMetaValue(doc, value) {
  if (value) return escapeHtml(value);
  const indexing = doc.processStatus === "ready";
  const settled = doc.processStatus === "completed" || doc.processStatus === "failed";
  const label = indexing ? "Detecting…" : settled ? "Not detected" : "Processing…";
  return `<span class="muted">${label}</span>`;
}

function summaryHtmlForDoc(doc) {
  const indexing = doc.processStatus === "ready";
  if (doc.summary) return `<p>${escapeHtml(doc.summary)}</p>`;
  return `<p class="muted">${indexing ? "Writing the description from the opening pages…" : "A report summary appears here when the description step finishes."}</p>`;
}

function datapointsPendingText(doc) {
  const points = keyDatapointsFromDoc(doc);
  const note = points?.status_note || points?.statusNote;
  if (note) return note;
  const settled = doc.processStatus === "completed" || doc.processStatus === "failed";
  if (settled && points && typeof points === "object") {
    return "No FTE or sustainability goals found in the indexed report text.";
  }
  return settled ? "Not extracted yet" : "Available when indexing progresses";
}

function formatFteDatapoint(fte) {
  if (!fte || (fte.value == null && !fte.verbatim)) return null;
  const value =
    fte.value != null
      ? `${Number(fte.value).toLocaleString()}${fte.unit ? ` ${fte.unit}` : " FTE"}`
      : null;
  const asOf = fte.as_of || fte.asOf || null;
  const page = fte.page != null ? `p. ${fte.page}` : null;
  const verbatim = fte.verbatim || null;
  const bits = [value, asOf, page].filter(Boolean);
  const head = bits.length ? escapeHtml(bits.join(" · ")) : "";
  const quote = verbatim ? `<span class="datapoint-quote">“${escapeHtml(verbatim)}”</span>` : "";
  return `<p class="datapoint-value">${head}${head && quote ? "<br>" : ""}${quote}</p>`;
}

function formatSustainabilityDatapoints(goals) {
  const list = Array.isArray(goals) ? goals : [];
  if (!list.length) return null;
  const items = list
    .slice(0, 6)
    .map((goal) => {
      const label = goal.label || "Goal";
      const target = goal.target || "";
      const deadline = goal.deadline || "";
      const page = goal.page != null ? `p. ${goal.page}` : "";
      const meta = [target, deadline, page].filter(Boolean).join(" · ");
      const verbatim = goal.verbatim
        ? `<div class="datapoint-quote">“${escapeHtml(goal.verbatim)}”</div>`
        : "";
      return `<li><strong>${escapeHtml(label)}</strong>${
        meta ? ` — ${escapeHtml(meta)}` : ""
      }${verbatim}</li>`;
    })
    .join("");
  return `<ul class="datapoint-list">${items}</ul>`;
}

function keyDatapointsFromDoc(doc) {
  return doc?.keyDatapoints || doc?.key_datapoints || null;
}

function applyKeyDatapointsToDoc(documentId, keyDatapoints) {
  if (!documentId || !keyDatapoints) return;
  const idx = state.documents.findIndex((d) => sameId(d.id, documentId));
  if (idx >= 0) {
    const prev = keyDatapointsFromDoc(state.documents[idx]) || {};
    const incoming = keyDatapoints;
    const merged = {
      ...prev,
      ...incoming,
      fte:
        incoming.fte && incoming.fte.value != null
          ? incoming.fte
          : prev.fte || incoming.fte || null,
      sustainability_goals: (() => {
        const next = incoming.sustainability_goals || incoming.sustainabilityGoals;
        const prior = prev.sustainability_goals || prev.sustainabilityGoals;
        return Array.isArray(next) && next.length ? next : prior || [];
      })(),
    };
    const fteOk = merged.fte && merged.fte.value != null;
    const goalsOk = Array.isArray(merged.sustainability_goals) && merged.sustainability_goals.length;
    if (fteOk || goalsOk) delete merged.status_note;
    else if (incoming.status_note || incoming.statusNote) {
      merged.status_note = incoming.status_note || incoming.statusNote;
    }
    state.documents[idx] = { ...state.documents[idx], keyDatapoints: merged };
  }
  if (sameId(state.documentId, documentId)) {
    const doc = state.documents.find((d) => sameId(d.id, documentId));
    if (doc) patchDocPanelContent(doc);
  }
}

function renderDocPanelTopHtml(doc) {
  const err = doc.error?.message ? `<p class="error">${escapeHtml(doc.error.message)}</p>` : "";
  return `
    ${err}
    ${pipelineTrail(doc.processStatus)}
    <section class="report-card">
      <div class="report-meta">
        <div class="meta-item">
          <span class="meta-label">Company</span>
          <span class="meta-value">${detectedMetaValue(doc, doc.companyName)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Report year</span>
          <span class="meta-value">${detectedMetaValue(doc, doc.reportYear)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Document type</span>
          <span class="meta-value">${detectedMetaValue(doc, DOC_TYPE_LABELS[doc.docType] || doc.docType)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Pipeline</span>
          <span class="meta-value status-text ${escapeHtml(doc.processStatus)}">${escapeHtml(statusLabel(doc.processStatus))}</span>
        </div>
      </div>
    </section>`;
}

function renderKeyDatapointsHtml(doc) {
  const pendingText = datapointsPendingText(doc);
  const points = keyDatapointsFromDoc(doc);
  const fteHtml = formatFteDatapoint(points?.fte) || `<p class="datapoint-value muted">${pendingText}</p>`;
  const goalsHtml =
    formatSustainabilityDatapoints(points?.sustainability_goals || points?.sustainabilityGoals) ||
    `<p class="datapoint-value muted">${pendingText}</p>`;
  const canRefresh = doc.processStatus === "ready" || doc.processStatus === "completed";
  return `
    <div class="datapoint-grid">
      <article class="datapoint-card">
        <span class="meta-label">FTE count</span>
        ${fteHtml}
        <p class="muted small">Verbatim figure and page citation from the report.</p>
      </article>
      <article class="datapoint-card">
        <span class="meta-label">Sustainability goals</span>
        ${goalsHtml}
        <p class="muted small">Targets and deadlines, quoted from the report.</p>
      </article>
    </div>
    ${
      canRefresh
        ? `<p class="muted small datapoint-refresh-hint">${
            doc.processStatus === "ready"
              ? "Refresh searches whatever is indexed so far (more pages may still be embedding)."
              : "Refresh re-runs extraction over the full indexed report."
          }</p>`
        : `<p class="muted small datapoint-refresh-hint">Refresh is available once the report is searchable (ready).</p>`
    }`;
}

function renderFileDetailsHtml(doc) {
  return `
    <div><strong>File:</strong> ${escapeHtml(doc.originalFilename)}</div>
    <div><strong>MIME type:</strong> ${escapeHtml(doc.mimeType || "—")}</div>
    <div><strong>Status:</strong> ${escapeHtml(doc.processStatus)}</div>
    <div><strong>Updated:</strong> ${escapeHtml(formatDate(doc.updatedAt))}</div>`;
}

function patchDocPanelContent(doc) {
  const top = $("#doc-panel-top");
  if (top) top.innerHTML = renderDocPanelTopHtml(doc);
  const datapointsBody = document.querySelector('[data-fold="datapoints"] .panel-fold-body');
  if (datapointsBody) datapointsBody.innerHTML = renderKeyDatapointsHtml(doc);
  const refreshBtn = document.querySelector("[data-refresh-datapoints]");
  if (refreshBtn) {
    refreshBtn.disabled =
      !(doc.processStatus === "ready" || doc.processStatus === "completed") ||
      Boolean(state.datapointsRefreshing);
  }
  const summaryBody = document.querySelector('[data-fold="summary"] .panel-fold-body');
  if (summaryBody) summaryBody.innerHTML = summaryHtmlForDoc(doc);
  const detailsBody = document.querySelector('[data-fold="details"] .panel-fold-body');
  if (detailsBody) detailsBody.innerHTML = renderFileDetailsHtml(doc);
}

function ensurePreviewStash() {
  let stash = $("#pdf-preview-stash");
  if (stash) return stash;
  stash = document.createElement("div");
  stash.id = "pdf-preview-stash";
  stash.className = "pdf-preview-stash";
  stash.setAttribute("aria-hidden", "true");
  document.body.appendChild(stash);
  return stash;
}

function parkPreviewPanes() {
  // Only used when the preview host is about to be destroyed (empty state /
  // full panel rebuild). Moving iframes kills Chrome PDF annotations — never
  // park on a normal report-tab switch.
  const stash = ensurePreviewStash();
  const host = $("#original-preview-host");
  if (host) {
    for (const pane of [...host.querySelectorAll(".original-preview-pane")]) {
      stash.appendChild(pane);
    }
  }
  for (const entry of state.docPreview.cache.values()) {
    if (entry.wrapper && !stash.contains(entry.wrapper)) {
      stash.appendChild(entry.wrapper);
    }
  }
}

function unparkPreviewPanes() {
  const host = $("#original-preview-host");
  const stash = $("#pdf-preview-stash");
  if (!host || !stash) return;
  for (const pane of [...stash.querySelectorAll(".original-preview-pane")]) {
    // Only move if not already under this host — re-appending remounts the PDF plugin.
    if (pane.parentElement !== host) host.appendChild(pane);
  }
}

function panesForDocument(host, documentId) {
  return [...host.querySelectorAll(".original-preview-pane")].filter((pane) =>
    sameId(pane.dataset.documentId, documentId),
  );
}

function dedupePreviewPanes(host, documentId, keepWrapper = null) {
  const panes = panesForDocument(host, documentId);
  for (const pane of panes) {
    if (keepWrapper && pane === keepWrapper) continue;
    if (keepWrapper) {
      pane.remove();
      continue;
    }
  }
  if (keepWrapper) return keepWrapper;
  // Keep the first pane; drop the rest.
  const [first, ...extras] = panes;
  for (const pane of extras) pane.remove();
  return first || null;
}

function showOnlyPreviewPane(host, documentId) {
  // Keep every iframe mounted at full size. Only toggle visibility/z-index —
  // never [hidden], display:none, or position changes (those remount Chrome's PDF plugin).
  for (const pane of host.querySelectorAll(".original-preview-pane")) {
    const active = sameId(pane.dataset.documentId, documentId);
    pane.classList.toggle("is-active", active);
    pane.classList.toggle("is-inactive", !active);
    pane.setAttribute("aria-hidden", active ? "false" : "true");
  }
}

async function ensureOriginalPreview(doc) {
  const host = $("#original-preview-host");
  const status = $("#original-preview-status");
  if (!host || !doc?.id || !state.projectId || !state.token) return;

  const key = String(doc.id);
  showOnlyPreviewPane(host, key);

  const existingInflight = state.docPreview.loadingById.get(key);
  if (existingInflight) {
    await existingInflight;
    const cachedAfter = state.docPreview.cache.get(key);
    if (cachedAfter?.wrapper) {
      if (cachedAfter.wrapper.parentElement !== host) host.appendChild(cachedAfter.wrapper);
      dedupePreviewPanes(host, key, cachedAfter.wrapper);
      showOnlyPreviewPane(host, key);
      if (status) status.hidden = true;
    }
    return;
  }

  let cached = state.docPreview.cache.get(key);
  if (!cached?.frame) {
    const existingPane = dedupePreviewPanes(host, key);
    const frame = existingPane?.querySelector("iframe");
    if (existingPane && frame?.src) {
      cached = {
        objectUrl: frame.src,
        frame,
        wrapper: existingPane,
      };
      state.docPreview.cache.set(key, cached);
    }
  }

  if (cached?.frame) {
    if (cached.wrapper.parentElement !== host) host.appendChild(cached.wrapper);
    dedupePreviewPanes(host, key, cached.wrapper);
    showOnlyPreviewPane(host, key);
    state.docPreview.activeDocumentId = key;
    state.docPreview.loading = false;
    state.docPreview.error = null;
    if (status) status.hidden = true;
    return;
  }

  const loadPromise = (async () => {
    state.docPreview.loading = true;
    state.docPreview.error = null;
    if (status) {
      status.hidden = false;
      status.textContent = "Loading original PDF…";
      status.classList.remove("error");
    }

    // Drop any half-created empty panes for this doc before making a new one.
    dedupePreviewPanes(host, key, null);
    for (const pane of panesForDocument(host, key)) pane.remove();

    const wrapper = document.createElement("div");
    wrapper.className = "original-preview original-preview-pane";
    wrapper.dataset.documentId = key;
    const frame = document.createElement("iframe");
    frame.title = `Original PDF preview — ${doc.originalFilename || doc.title || "report"}`;
    wrapper.appendChild(frame);
    host.appendChild(wrapper);
    showOnlyPreviewPane(host, key);

    try {
      const res = await fetch(`${API_BASE}/projects/${state.projectId}/documents/${doc.id}/original`, {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      await throwIfNotOk(res, `Could not load original file (${res.status})`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      frame.src = objectUrl;
      state.docPreview.cache.set(key, { objectUrl, frame, wrapper });
      state.docPreview.activeDocumentId = key;
      state.docPreview.loading = false;
      dedupePreviewPanes(host, key, wrapper);
      showOnlyPreviewPane(host, key);
      if (status) status.hidden = true;
    } catch (err) {
      wrapper.remove();
      state.docPreview.loading = false;
      state.docPreview.error = err.message || "Could not load original file";
      if (status) {
        status.hidden = false;
        status.textContent = state.docPreview.error;
        status.classList.add("error");
      }
    }
  })();

  state.docPreview.loadingById.set(key, loadPromise);
  try {
    await loadPromise;
  } finally {
    state.docPreview.loadingById.delete(key);
  }
}

function renderMain() {
  const body = $("#main-body");
  const title = $("#doc-title");
  const pill = $("#doc-status");
  if (!body || !title) return;
  const doc = state.documents.find((d) => sameId(d.id, state.documentId));
  const p = currentProject();

  if (!doc) {
    title.textContent = p?.name || "No report selected";
    if (pill) pill.hidden = true;
    parkPreviewPanes();
    const count = state.documents.length;
    body.innerHTML = `
      <div class="empty-state">
        <p class="eyebrow">Report project</p>
        <h3>${escapeHtml(p?.name || "Project")}</h3>
        <p class="muted">
          ${
            count
              ? `${count} report${count === 1 ? "" : "s"} in this project. Select one on the left to see its key datapoints, or ask the analyst a question across all of them.`
              : "Upload one or more annual-report PDFs. Each report is extracted, chunked and indexed so the analyst can answer with verbatim, cited quotes."
          }
        </p>
        <div class="action-cards">
          <label class="action-card action-card-btn" for="file-input">
            <h4>Upload annual report</h4>
            <p class="muted small">PDF, extracted and indexed on upload</p>
          </label>
          <div class="action-card muted-card">
            <h4>Key datapoints</h4>
            <p class="muted small">FTE count and sustainability goals, extracted at ingest</p>
          </div>
          <div class="action-card muted-card">
            <h4>Report summary</h4>
            <p class="muted small">Coming with the summarizer</p>
          </div>
        </div>
      </div>`;
    return;
  }

  title.textContent = doc.title;
  if (pill) {
    pill.hidden = false;
    pill.textContent = statusLabel(doc.processStatus);
    pill.className = `status-pill ${doc.processStatus}`;
  }

  const preview = state.docPreview;
  const hasOriginal = Boolean(doc.originalFilename || doc.originalFileUrl);
  const existingPanel = body.querySelector(".doc-panel");
  const switchedDoc =
    existingPanel && !sameId(existingPanel.dataset.documentId, doc.id);
  if (switchedDoc) {
    // Opening a report always starts with Key datapoints + Summary expanded.
    preview.datapointsOpen = true;
    preview.summaryOpen = true;
  }

  // Keep the panel (and PDF iframes) mounted across polls AND report switches.
  // Rebuilding via innerHTML / [hidden] remounts Chrome's PDF viewer and wipes annotations.
  if (existingPanel) {
    existingPanel.dataset.documentId = String(doc.id);
    existingPanel.classList.toggle("viewer-open", preview.originalOpen);
    patchDocPanelContent(doc);
    const status = $("#original-preview-status");
    const host = $("#original-preview-host");
    if (hasOriginal && !host) {
      // Panel existed without a preview host (e.g. switched from a doc with no file).
      const foldBody = existingPanel.querySelector('[data-fold="original"] .panel-fold-body');
      if (foldBody) {
        foldBody.innerHTML = `
          <p id="original-preview-status" class="muted small original-preview-status${state.docPreview.cache.has(String(doc.id)) ? " hidden" : ""}">Loading original PDF…</p>
          <div id="original-preview-host" class="original-preview-host"></div>
          <p class="muted small original-preview-hint">
            Annotations stay in this browser session when you switch reports.
            Use the viewer’s download/save control to keep a marked-up PDF on your machine.
          </p>`;
      }
    } else if (!hasOriginal) {
      const foldBody = existingPanel.querySelector('[data-fold="original"] .panel-fold-body');
      // Park any live panes before replacing the host markup.
      parkPreviewPanes();
      if (foldBody) {
        foldBody.innerHTML = `<p class="muted small">Upload a PDF to preview the original file here.</p>`;
      }
    } else if (status && state.docPreview.cache.has(String(doc.id))) {
      status.hidden = true;
    }
    const datapointsFold = existingPanel.querySelector('[data-fold="datapoints"]');
    if (datapointsFold) datapointsFold.open = preview.datapointsOpen;
    const summaryFold = existingPanel.querySelector('[data-fold="summary"]');
    if (summaryFold) summaryFold.open = preview.summaryOpen;
    const detailsFold = existingPanel.querySelector('[data-fold="details"]');
    if (detailsFold) detailsFold.open = preview.detailsOpen;
    const originalFold = existingPanel.querySelector('[data-fold="original"]');
    if (originalFold) originalFold.open = preview.originalOpen;
    if (preview.originalOpen && hasOriginal) {
      unparkPreviewPanes();
      ensureOriginalPreview(doc).catch((err) => toast(err.message));
    }
    return;
  }

  parkPreviewPanes();
  body.innerHTML = `
    <div class="doc-panel${preview.originalOpen ? " viewer-open" : ""}" data-document-id="${escapeHtml(doc.id)}">
      <div class="doc-panel-top" id="doc-panel-top">
        ${renderDocPanelTopHtml(doc)}
      </div>

      <div class="doc-folds">
        <details class="panel-fold" data-fold="datapoints"${preview.datapointsOpen ? " open" : ""}>
          <summary class="panel-fold-summary">
            <span>Key datapoints</span>
            <button
              type="button"
              class="fold-refresh"
              data-refresh-datapoints
              ${doc.processStatus === "ready" || doc.processStatus === "completed" ? "" : "disabled"}
              title="Re-run key datapoints extraction"
            >Refresh</button>
          </summary>
          <div class="panel-fold-body">${renderKeyDatapointsHtml(doc)}</div>
        </details>

        <details class="panel-fold" data-fold="summary"${preview.summaryOpen ? " open" : ""}>
          <summary>Summary</summary>
          <div class="panel-fold-body">${summaryHtmlForDoc(doc)}</div>
        </details>

        <details class="panel-fold" data-fold="details"${preview.detailsOpen ? " open" : ""}>
          <summary>File details</summary>
          <div class="panel-fold-body doc-meta">${renderFileDetailsHtml(doc)}</div>
        </details>

        <details class="panel-fold viewer-fold" data-fold="original"${preview.originalOpen ? " open" : ""}>
          <summary>Original File Preview</summary>
          <div class="panel-fold-body">
            ${
              hasOriginal
                ? `<p id="original-preview-status" class="muted small original-preview-status"${state.docPreview.cache.has(String(doc.id)) ? " hidden" : ""}>Loading original PDF…</p>
                   <div id="original-preview-host" class="original-preview-host"></div>
                   <p class="muted small original-preview-hint">
                     Annotations stay in this browser session when you switch reports.
                     Use the viewer’s download/save control to keep a marked-up PDF on your machine.
                   </p>`
                : `<p class="muted small">Upload a PDF to preview the original file here.</p>`
            }
          </div>
        </details>

        <details class="panel-fold" data-fold="extracted"${preview.extractedOpen ? " open" : ""}>
          <summary>Extracted file editable</summary>
          <div class="panel-fold-body">
            <p class="muted small">Editable extracted text view comes next. For now, use Original File Preview.</p>
          </div>
        </details>
      </div>
    </div>`;

  unparkPreviewPanes();
  if (preview.originalOpen && hasOriginal) {
    ensureOriginalPreview(doc).catch((err) => toast(err.message));
  }
}

const PIPELINE_STEPS = [
  ["processing", "Processing"],
  ["extracted", "Extraction completed"],
  ["ingesting", "Ingesting"],
  ["ready", "Ready"],
  ["completed", "Completed"],
];

function pipelineTrail(status) {
  const rank = { pending: 0, processing: 1, extracted: 2, ingesting: 3, ready: 4, completed: 5 };
  if (status === "failed") return "";
  const current = rank[status] ?? 0;
  const items = PIPELINE_STEPS.map(([key, label], index) => {
    const step = index + 1;
    const cls = step < current ? "done" : step === current ? "current" : "";
    return `<li class="${cls}">${escapeHtml(label)}</li>`;
  }).join("");
  return `<ol class="pipeline">${items}</ol>`;
}

function upsertLocalDocument(doc) {
  const idx = state.documents.findIndex((d) => sameId(d.id, doc.id));
  if (idx >= 0) state.documents[idx] = { ...state.documents[idx], ...doc };
  else state.documents.unshift(doc);
  renderSources();
  renderMain();
}

async function ingestFile(file) {
  const projectId = state.projectId;
  if (!projectId) {
    toast("Open a project first");
    return;
  }
  const title = file.name.replace(/\.[^.]+$/, "");
  let docId = null;
  const sidebar = $("#materials-panel");
  try {
    sidebar?.classList.add("uploading");
    toast(`Uploading ${file.name}…`);
    appendChatMessage({ role: "user", type: "source", filename: file.name });
    appendChatMessage({
      role: "assistant",
      type: "text",
      text: "Report received and processing. You can keep asking about the other reports in the meantime.",
    });
    if (!state.chatOpen && !state.chatMaximized) {
      state.chatOpen = true;
      applyLayoutChrome();
      saveSession();
    }
    const prepared = await api(`/projects/${projectId}/documents/prepare`, {
      method: "POST",
      body: { filename: file.name, title },
    });
    if (!sameId(projectId, state.projectId)) return;
    docId = prepared.data.id;
    upsertLocalDocument({
      id: docId,
      title: prepared.data.title || title,
      originalFilename: file.name,
      mimeType: file.type || "application/pdf",
      processStatus: "pending",
      _new: true,
    });
    state.documentId = docId;
    startPolling();

    const fd = new FormData();
    fd.append("file", file, file.name);
    await api(`/projects/${projectId}/documents/${docId}/content`, {
      method: "PUT",
      formData: fd,
    });
    if (!sameId(projectId, state.projectId)) return;

    await api(`/projects/${projectId}/documents/${docId}/process`, {
      method: "POST",
    });
    if (!sameId(projectId, state.projectId)) return;

    upsertLocalDocument({ id: docId, processStatus: "processing", _new: true });
    toast("Processing started");
    await refreshDocuments();
  } catch (err) {
    if (docId && sameId(projectId, state.projectId)) {
      upsertLocalDocument({ id: docId, processStatus: "failed", _new: true });
    }
    toast(err.message);
  } finally {
    sidebar?.classList.remove("uploading");
  }
}

function bindUpload() {
  const input = $("#file-input");
  const sidebar = $("#materials-panel");
  if (!input) return;

  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    input.value = "";
    if (file) await ingestFile(file);
  });

  if (!sidebar) return;
  ["dragenter", "dragover"].forEach((evt) => {
    sidebar.addEventListener(evt, (e) => {
      e.preventDefault();
      sidebar.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    sidebar.addEventListener(evt, (e) => {
      e.preventDefault();
      sidebar.classList.remove("dragover");
    });
  });
  sidebar.addEventListener("drop", async (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) await ingestFile(file);
  });
}

function bindDocPanel() {
  const body = $("#main-body");
  if (!body || body.dataset.foldsBound === "1") return;
  body.dataset.foldsBound = "1";
  body.addEventListener("toggle", (e) => {
    const details = e.target.closest("details[data-fold]");
    if (!details || !body.contains(details)) return;
    const fold = details.dataset.fold;
    const open = details.open;
    if (fold === "datapoints") state.docPreview.datapointsOpen = open;
    if (fold === "summary") state.docPreview.summaryOpen = open;
    if (fold === "details") state.docPreview.detailsOpen = open;
    if (fold === "original") {
      state.docPreview.originalOpen = open;
      const panel = body.querySelector(".doc-panel");
      panel?.classList.toggle("viewer-open", open);
      if (open) {
        const doc = state.documents.find((d) => sameId(d.id, state.documentId));
        if (doc) ensureOriginalPreview(doc).catch((err) => toast(err.message));
      }
    }
    if (fold === "extracted") state.docPreview.extractedOpen = open;
  }, true);
  body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-refresh-datapoints]");
    if (!btn || !body.contains(btn)) return;
    e.preventDefault();
    e.stopPropagation();
    const doc = state.documents.find((d) => sameId(d.id, state.documentId));
    if (doc) refreshKeyDatapoints(doc).catch((err) => toast(err.message || "Refresh failed"));
  });
}

async function refreshKeyDatapoints(doc) {
  if (!state.projectId || !doc?.id) return;
  if (doc.processStatus !== "ready" && doc.processStatus !== "completed") {
    toast("Refresh needs a searchable index (ready or completed)");
    return;
  }
  if (state.datapointsRefreshing) return;
  state.datapointsRefreshing = true;
  const btn = document.querySelector("[data-refresh-datapoints]");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("busy");
    btn.textContent = "Refreshing…";
  }
  try {
    const res = await api(`/projects/${state.projectId}/documents/${doc.id}/datapoints/refresh`, {
      method: "POST",
    });
    const updated = res.data;
    if (updated) {
      upsertLocalDocument(updated);
      if (sameId(state.documentId, updated.id)) {
        const current = state.documents.find((d) => sameId(d.id, updated.id));
        if (current) patchDocPanelContent(current);
      }
      toast("Key datapoints refreshed");
    }
  } finally {
    state.datapointsRefreshing = false;
    const refreshBtn = document.querySelector("[data-refresh-datapoints]");
    if (refreshBtn) {
      refreshBtn.classList.remove("busy");
      refreshBtn.textContent = "Refresh";
      const current = state.documents.find((d) => sameId(d.id, state.documentId));
      refreshBtn.disabled =
        !current ||
        !(current.processStatus === "ready" || current.processStatus === "completed");
    }
  }
}

function bindChatChrome() {
  $("#btn-toggle-chat")?.addEventListener("click", () => {
    if (state.chatMaximized) {
      state.chatMaximized = false;
      state.chatOpen = true;
    } else {
      state.chatOpen = !state.chatOpen;
      if (state.chatOpen) state.chatMaximized = false;
    }
    applyLayoutChrome();
    saveSession();
  });
  $("#btn-toggle-materials")?.addEventListener("click", () => {
    if (state.chatMaximized) {
      state.chatMaximized = false;
      state.materialsOpen = true;
      state.chatOpen = true;
    } else {
      state.materialsOpen = !state.materialsOpen;
    }
    applyLayoutChrome();
    saveSession();
  });
  $("#btn-close-materials")?.addEventListener("click", () => {
    state.materialsOpen = false;
    if (state.chatMaximized) state.chatMaximized = false;
    applyLayoutChrome();
    saveSession();
  });
  $("#btn-maximize-chat")?.addEventListener("click", () => {
    state.chatMaximized = !state.chatMaximized;
    if (state.chatMaximized) state.chatOpen = true;
    applyLayoutChrome();
    saveSession();
  });
  $("#btn-close-chat")?.addEventListener("click", () => {
    state.chatOpen = false;
    state.chatMaximized = false;
    applyLayoutChrome();
    saveSession();
  });
  $("#chat-panel")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-prompt]");
    if (!btn) return;
    const input = $("#chat-input");
    if (!input) return;
    input.value = btn.dataset.prompt;
    input.focus();
  });
  $("#chat-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#chat-input");
    const text = input?.value || "";
    if (input) input.value = "";
    postChatMessage(text).catch((err) => toast(err.message));
    input?.focus();
  });
  $("#chat-messages")?.addEventListener("scroll", () => {
    const root = $("#chat-messages");
    if (!root || !isChatNearTop(root)) return;
    loadOlderChatHistory().catch((err) => toast(err.message));
  });
}

function startPolling() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(() => {
    if (state.view !== "project" || !state.projectId) return;
    refreshDocuments().catch(() => {});
  }, 2000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startListPolling() {
  stopListPolling();
  state.listPollTimer = setInterval(() => {
    if (state.view !== "project" || !state.projectId) return;
    refreshDocuments().catch(() => {});
  }, 10000);
}

function stopListPolling() {
  if (state.listPollTimer) {
    clearInterval(state.listPollTimer);
    state.listPollTimer = null;
  }
}

window.addEventListener("popstate", () => {
  route().catch((err) => toast(err.message));
});

window.addEventListener("error", (e) => {
  console.error(e.error || e.message);
});

const THEME_KEY = "report_rag_theme";

function applyTheme(dark) {
  document.documentElement.classList.toggle("theme-dark", dark);
  document.querySelectorAll(".theme-toggle").forEach((button) => {
    button.setAttribute("aria-pressed", String(dark));
    button.textContent = dark ? "Light mode" : "Dark mode";
  });
}

function bindTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  const dark =
    stored === "dark" ||
    (stored !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  applyTheme(dark);
  document.querySelectorAll(".theme-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const next = !document.documentElement.classList.contains("theme-dark");
      localStorage.setItem(THEME_KEY, next ? "dark" : "light");
      applyTheme(next);
    });
  });
}

renderBrandMarks();
bindTheme();
bindSessionSync();
bindAuth();
bindProjects();
bindUpload();
bindDocPanel();
bindChatChrome();
bootstrap();
