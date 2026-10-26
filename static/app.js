/*
 * 网页前端（阶段 5b）：发消息 → 每 400 毫秒查一次进度 → 显示执行步骤、确认弹窗、最终回答。
 * 阶段 5c：侧边栏三个页签——原文（点 [E1] 看片段原文）、记忆（查看和删除长期记忆）、资料（PDF、索引、CSV 的状态）。
 * 安全：模型和工具返回的文字一律先转义再显示（escapeHtml），不直接当 HTML 插入页面。
 */

const $ = (sel) => document.querySelector(sel);
const POLL_MS = 400;
const TOOL_NAMES = {
  search_documents: "检索论文",
  calculate_stats: "计算统计量",
  list_experiment_files: "列出实验文件",
  describe_experiment_file: "查看表头",
  group_statistics: "分组统计",
  save_memory: "保存记忆",
  search_memory: "查找记忆",
  forget_memory: "删除记忆",
};

let sessionId = null;
let busy = false;
let modalJob = null;        // 正在等待确认的任务编号；null 表示弹窗没开
const answers = [];         // 每条回答自己的证据 {cites, evidence}；回答里的 [E1] 带着下标，点开时找回自己那一份
const panel = { tab: null, answer: null, id: null };   // 侧边栏当前页签（null = 关着）和正在看的证据

// ---------- 和后端通信 ----------

async function api(path, options = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `请求失败（${r.status}）`);
  return data;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------- 文字显示：先转义，再做少量格式（粗体、代码、表格、列表、证据编号） ----------

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ctx = {cites: 这条回答的有效引用, answer: 这条回答在 answers 里的下标}
function citeChip(id, ctx) {
  const src = ctx.cites[id];
  const title = src ? `${src.file} 第 ${src.page} 页${src.section ? " · " + src.section : ""}（点击看原文）` : "这个编号不在检索结果里";
  return `<button type="button" class="cite${src ? "" : " invalid"}" data-id="${id}" data-answer="${ctx.answer}" title="${escapeHtml(title)}">${id}</button>`;
}

function inline(text, ctx) {
  let h = escapeHtml(text);
  h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  // [E1]、[E1][E3]、[E2，E5] 都变成可点的小标签
  h = h.replace(/\[(E\d+(?:\s*[,，、]\s*E\d+)*)\]/g, (_, ids) => ids.split(/\s*[,，、]\s*/).map((id) => citeChip(id, ctx)).join(""));
  return h;
}

const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
const isListItem = (line) => /^\s*([-*]|\d+\.)\s+/.test(line);

function renderTable(rows, ctx) {
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const data = rows.map(cells).filter((c) => !c.every((x) => /^:?-{2,}:?$/.test(x)));   // 去掉 |---| 分隔行
  const [head, ...body] = data;
  const tr = (cs, tag) => `<tr>${cs.map((c) => `<${tag}>${inline(c, ctx)}</${tag}>`).join("")}</tr>`;
  return `<div class="table-wrap"><table><thead>${tr(head, "th")}</thead><tbody>${body.map((r) => tr(r, "td")).join("")}</tbody></table></div>`;
}

function renderMarkdown(text, ctx) {
  const lines = String(text).replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (isTableRow(line)) {
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(lines[i++]);
      out.push(renderTable(rows, ctx));
    } else if (isListItem(line)) {
      const tag = /^\s*\d+\./.test(line) ? "ol" : "ul";
      const items = [];
      while (i < lines.length && isListItem(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      out.push(`<${tag}>${items.map((t) => `<li>${inline(t, ctx)}</li>`).join("")}</${tag}>`);
    } else if (/^#{1,6}\s/.test(line)) {
      out.push(`<p class="h">${inline(line.replace(/^#+\s*/, ""), ctx)}</p>`);
      i++;
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() && !isTableRow(lines[i]) && !isListItem(lines[i])) para.push(lines[i++]);
      out.push(`<p>${para.map((l) => inline(l, ctx)).join("<br>")}</p>`);
    }
  }
  return out.join("");
}

// ---------- 执行过程 ----------

function describeArgs(call) {
  const a = call.args && typeof call.args === "object" ? call.args : {};
  if (call.name === "search_documents") return `「${a.query ?? ""}」`;
  if (call.name === "save_memory") return `「${a.content ?? ""}」`;
  if (call.name === "forget_memory") return `#${a.memory_id ?? ""}`;
  if (call.name === "search_memory") return a.keyword ? `「${a.keyword}」` : "";
  if (call.name === "calculate_stats") return Array.isArray(a.numbers) ? a.numbers.join("、") : "";
  if (call.name === "group_statistics") return [a.file, a.value_column, a.group_by && `按 ${a.group_by} 分组`].filter(Boolean).join(" · ");
  if (call.name === "describe_experiment_file") return a.file ?? "";
  return "";
}

function describeResult(call) {
  if (!call.ok) return `<span class="fail">失败：${escapeHtml(call.error ?? "")}</span>`;
  if (call.name === "search_documents" && Array.isArray(call.results)) {
    return `找到 ${call.results.length} 段：` + call.results.map((r) => {
      const page = String(r.chunk_id).split("#")[1] ?? "";
      return `${escapeHtml(r.evidence_id)} ${escapeHtml(page.replace("p", "第 ") + " 页")}（${r.score}）`;
    }).join("；");
  }
  return "完成";
}

function stepsHtml(steps) {
  const rounds = [...new Set([...steps.model_calls, ...steps.tool_calls].map((s) => s.round))].sort((a, b) => a - b);
  const lines = [];
  for (const r of rounds) {
    for (const m of steps.model_calls.filter((s) => s.round === r)) {
      const what = m.finish_reason === "tool_calls" ? "模型决定调用工具" : m.finish_reason === "stop" ? "模型作答" : `模型结束（${m.finish_reason}）`;
      lines.push(`<div>第 ${r} 轮 · ${what} <span class="meta">${(m.ms / 1000).toFixed(1)} 秒 · ${m.prompt_tokens + m.completion_tokens} token</span></div>`);
    }
    for (const t of steps.tool_calls.filter((s) => s.round === r)) {
      const wait = t.wait_ms ? `，等待确认 ${(t.wait_ms / 1000).toFixed(1)} 秒` : "";
      lines.push(`<div class="tool">↳ ${escapeHtml(TOOL_NAMES[t.name] ?? t.name)} ${escapeHtml(describeArgs(t))} → ${describeResult(t)} <span class="meta">${Math.round(t.ms)} ms${wait}</span></div>`);
    }
  }
  return `<div class="timeline">${lines.join("") || "<div>准备中…</div>"}</div>`;
}

function stepsSummary(steps) {
  const ms = [...steps.model_calls, ...steps.tool_calls].reduce((s, x) => s + (x.ms || 0), 0);
  const tokens = steps.model_calls.reduce((s, m) => s + m.prompt_tokens + m.completion_tokens, 0);
  return `执行过程 · ${steps.model_calls.length} 次模型调用 · ${steps.tool_calls.length} 次工具调用 · ${(ms / 1000).toFixed(1)} 秒 · ${tokens.toLocaleString()} token`;
}

// ---------- 消息 ----------

function hideEmpty() { $("#empty")?.remove(); }

function scrollToBottom() { const m = $("#messages"); m.scrollTop = m.scrollHeight; }

function addUserMessage(text) {
  hideEmpty();
  const row = document.createElement("div");
  row.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;          // 用户输入只当纯文本显示
  row.appendChild(bubble);
  $("#messages").appendChild(row);
  scrollToBottom();
}

function addAssistantCard() {
  const row = document.createElement("div");
  row.className = "msg assistant";
  row.innerHTML = `<div class="card"><div class="thinking"><span class="dots"><span></span><span></span><span></span></span><span class="label">思考中</span></div><div class="live"></div></div>`;
  $("#messages").appendChild(row);
  scrollToBottom();
  return row.querySelector(".card");
}

function showProgress(card, snap) {
  const label = card.querySelector(".thinking .label");
  if (label) label.textContent = snap.status === "waiting_confirm" ? "等待你确认写入" : "思考中";
  const live = card.querySelector(".live");
  if (live && (snap.steps.model_calls.length || snap.steps.tool_calls.length)) {
    live.innerHTML = `<details class="steps" open><summary>执行中</summary>${stepsHtml(snap.steps)}</details>`;
  }
  scrollToBottom();
}

function showAnswer(card, snap) {
  const res = snap.result;
  const cites = Object.fromEntries(res.citations.map((c) => [c.id, c]));
  const ctx = { cites, answer: answers.push({ cites, evidence: res.evidence || [] }) - 1 };
  let html = `<div class="answer">${renderMarkdown(res.answer_raw || "（没有回答）", ctx)}</div>`;
  if (res.citations.length) {
    html += `<div class="sources">` + res.citations.map((c) =>
      `<div>${citeChip(c.id, ctx)}${escapeHtml(c.file)} 第 ${c.page} 页${c.label ? `（印刷页 ${escapeHtml(c.label)}）` : ""}${c.section ? " · " + escapeHtml(c.section) : ""}</div>`).join("") + `</div>`;
  }
  if (res.invalid_citations.length) html += `<div class="notice">回答引用了检索结果里没有的编号：${res.invalid_citations.map(escapeHtml).join("、")}</div>`;
  if (res.uncited) html += `<div class="notice">这次检索了资料，但回答没有标注证据编号</div>`;
  if (res.status !== "ok") html += `<div class="notice">回答没有正常结束（${escapeHtml(res.status)}）</div>`;
  html += `<details class="steps"><summary>${stepsSummary(snap.steps)}</summary>${stepsHtml(snap.steps)}</details>`;
  card.innerHTML = html;
  scrollToBottom();
}

function showError(card, message) {
  card.innerHTML = `<div class="notice error">${escapeHtml(message)}</div>`;
}

// ---------- 侧边栏：原文 / 记忆 / 资料 ----------

function markOpenTab() {                                  // 页签和顶栏按钮都标出当前打开的是哪一页
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === panel.tab)));
  $("#open-memory").classList.toggle("active", panel.tab === "memory");
  $("#open-sources").classList.toggle("active", panel.tab === "sources");
}

function openPanel(tab) {
  panel.tab = tab;
  $("#panel").hidden = false;
  $(".layout").classList.add("with-panel");
  markOpenTab();
  if (tab === "evidence") renderEvidence();
  if (tab === "memory") loadMemories();
  if (tab === "sources") loadSources();
}

function closePanel() {
  panel.tab = null;
  $("#panel").hidden = true;
  $(".layout").classList.remove("with-panel");
  markOpenTab();
}

function togglePanel(tab) { panel.tab === tab ? closePanel() : openPanel(tab); }

function setPanelBody(html) { $("#panel-body").innerHTML = html; }

function panelError(tab, message) {
  if (panel.tab === tab) setPanelBody(`<div class="notice error">${escapeHtml(message)}</div>`);
}

function fmtTime(iso) {
  const d = new Date(iso);
  if (!iso || isNaN(d)) return escapeHtml(iso ?? "");
  return d.toLocaleString("zh-CN", { hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function fmtSize(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

// 原文：数据就在这条回答的结果里（后端随回答一起返回），不用再请求
function showEvidence(answer, id) {
  panel.answer = answer;
  panel.id = id;
  openPanel("evidence");
}

function renderEvidence() {
  const a = answers[panel.answer];
  if (!a) return setPanelBody(`<p class="hint">点回答里的 <span class="cite static">E1</span> 这样的编号，在这里看对应的原文片段。</p>`);
  const e = a.evidence.find((x) => x.id === panel.id);
  let html = e ? `
    <div class="ev-title"><span class="cite static">${escapeHtml(e.id)}</span>${escapeHtml(e.file)}</div>
    <div class="ev-meta">第 ${escapeHtml(e.page)} 页${e.label ? `（印刷页 ${escapeHtml(e.label)}）` : ""}${e.section ? " · " + escapeHtml(e.section) : ""}</div>
    <div class="ev-meta">检索词「${escapeHtml(e.query ?? "")}」 · 相似度 ${escapeHtml(e.score)}
      <span class="tag${e.cited ? " cited" : ""}">${e.cited ? "回答引用了这段" : "检索到，但回答没有引用"}</span></div>
    <blockquote class="passage">${escapeHtml(e.text ?? "")}</blockquote>
    <p class="hint">这是检索时交给模型的原文片段，不是整页。文件名和页码由程序按片段出处填写，不是模型写的。</p>`
    : `<div class="notice error">${escapeHtml(panel.id)} 不在检索结果里：回答引用了一个不存在的编号，程序已把它标为无效引用。</div>`;
  if (a.evidence.length > 1 || (!e && a.evidence.length)) {
    html += `<div class="panel-subtitle">这条回答用到的片段</div><div class="ev-list">` + a.evidence.map((x) => `
      <button type="button" class="ev-item${x.id === panel.id ? " active" : ""}" data-action="evidence" data-id="${escapeHtml(x.id)}">
        <span class="cite static">${escapeHtml(x.id)}</span>
        <span class="ev-item-text">${escapeHtml(x.file)} 第 ${escapeHtml(x.page)} 页 · ${escapeHtml(x.score)}</span>
        <span class="tag${x.cited ? " cited" : ""}">${x.cited ? "已引用" : "未引用"}</span>
      </button>`).join("") + `</div>`;
  }
  setPanelBody(html);
}

// 记忆：查看、删除（删除前在这一行里再确认一次）
async function loadMemories() {
  setPanelBody(`<p class="hint">加载中…</p>`);
  try {
    const { memories } = await api("/api/memories");
    if (panel.tab === "memory") setPanelBody(memoriesHtml(memories));
  } catch (e) {
    panelError("memory", e.message);
  }
}

function memoriesHtml(list) {
  const items = list.slice().reverse().map((m) => `
    <div class="mem" data-id="${escapeHtml(m.id)}">
      <div class="mem-text">${escapeHtml(m.content)}</div>
      <div class="mem-meta"><span>#${escapeHtml(m.id)} · ${fmtTime(m.created_at)}</span>
        <button type="button" class="link danger" data-action="ask-delete">删除</button></div>
      <div class="mem-confirm" hidden>
        <span>确定删除这条记忆？</span>
        <button type="button" class="btn ghost small" data-action="cancel-delete">取消</button>
        <button type="button" class="btn danger small" data-action="delete">删除</button>
      </div>
    </div>`).join("");
  return `<div class="panel-subtitle">长期记忆 · ${list.length} 条（新的在前）</div>` +
    (items || `<p class="hint">还没有长期记忆。在对话里说"记住：……"，你确认后会出现在这里。</p>`) +
    `<p class="hint">写入只能通过对话，每次都要你确认。删除是软删除：数据库里保留记录并标为已删除，助手之后查不到它。</p>`;
}

async function deleteMemory(row) {
  try {
    await api(`/api/memories/${encodeURIComponent(row.dataset.id)}`, { method: "DELETE" });
    loadMemories();
  } catch (e) {
    row.querySelector(".mem-confirm").innerHTML = `<span class="fail">${escapeHtml(e.message)}</span>`;
  }
}

// 资料：PDF 有没有进索引、索引是否过期、data/ 里有哪些表
async function loadSources() {
  setPanelBody(`<p class="hint">加载中…</p>`);
  try {
    const s = await api("/api/sources");
    if (panel.tab === "sources") setPanelBody(sourcesHtml(s));
  } catch (e) {
    panelError("sources", e.message);
  }
}

function sourcesHtml(s) {
  const idx = s.index;
  const [cls, label] = !idx.exists ? ["error", "没有索引"] : idx.stale.length ? ["warn", "索引已过期"] : ["ok", "索引和资料一致"];
  const model = s.search_error ? `<span class="fail">不可用</span>` : s.search_ready ? "已加载" : "加载中…";
  const files = (list, meta) => `<ul class="files">${list.map((f) =>
    `<li><span class="file-name">${escapeHtml(f.name)}</span><span class="file-meta">${meta(f)}</span></li>`).join("")}</ul>`;

  let html = `<div class="state ${cls}">${label}</div>`;
  if (idx.stale.length) {
    html += `<ul class="stale">${idx.stale.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`;
    if (idx.exists) html += `<p class="hint">在项目目录运行 <code>python build_index.py</code> 重建索引，然后重启网页服务。</p>`;
  }
  html += `<div class="panel-subtitle">论文和协议 · docs/</div>`;
  html += s.docs.length
    ? files(s.docs, (d) => `${d.chunks ? `${d.chunks} 段` : `<span class="fail">未建索引</span>`} · ${fmtSize(d.size_bytes)}`)
    : `<p class="hint">docs/ 里还没有 PDF。</p>`;
  html += `<dl class="kv">`;
  if (idx.exists) {
    html += `<dt>嵌入模型</dt><dd>${escapeHtml(idx.model)}</dd><dt>建立时间</dt><dd>${fmtTime(idx.built_at)}</dd>
      <dt>片段数</dt><dd>${escapeHtml(idx.chunks)} 段${idx.chunk_size ? `，每段最多 ${escapeHtml(idx.chunk_size)} 字符` : ""}</dd>`;
  }
  html += `<dt>检索模型</dt><dd>${model}</dd></dl>`;
  if (s.search_error) html += `<div class="notice error">${escapeHtml(s.search_error)}</div>`;
  html += `<div class="panel-subtitle">实验数据 · data/</div>`;
  html += s.data_files.length ? files(s.data_files, (f) => fmtSize(f.size_bytes)) : `<p class="hint">data/ 里还没有 CSV。</p>`;
  html += `<p class="hint">检索在本机完成。发给模型 API 的是对话内容和工具结果（检索到的片段、统计结果、记忆）；整篇 PDF 和 CSV 的原始行不会发出去。</p>`;
  return html;
}

// ---------- 确认弹窗 ----------

function openModal(jobId, pending) {
  modalJob = jobId;
  $("#modal-title").textContent = pending.tool === "forget_memory" ? "确认删除长期记忆" : "确认写入长期记忆";
  $("#modal-detail").textContent = `助手想要${pending.detail}`;
  $("#modal").hidden = false;
  $("#modal-no").focus();             // 默认焦点在"取消"：误按回车不会写入
}

function closeModal() { modalJob = null; $("#modal").hidden = true; }

async function answerModal(approve) {
  const jobId = modalJob;
  closeModal();
  if (!jobId) return;
  try {
    await api(`/api/jobs/${jobId}/confirm`, { method: "POST", body: JSON.stringify({ approve }) });
  } catch (e) {
    console.warn(e.message);          // 例如已经超时：后端会按拒绝继续，页面不用处理
  }
}

// ---------- 发送与轮询 ----------

async function poll(jobId, card) {
  for (;;) {
    const snap = await api(`/api/jobs/${jobId}`);
    if (snap.status === "waiting_confirm" && snap.pending && modalJob !== jobId) openModal(jobId, snap.pending);
    if (snap.status !== "waiting_confirm" && modalJob === jobId) closeModal();   // 超时后后端已按拒绝处理
    if (snap.status === "done") {
      showAnswer(card, snap);
      if (panel.tab === "memory") loadMemories();         // 这条消息可能刚写入或删除了记忆
      return;
    }
    if (snap.status === "error") return showError(card, snap.error);
    showProgress(card, snap);
    await sleep(POLL_MS);
  }
}

function setBusy(value) {
  busy = value;
  $("#send").disabled = value;
  $("#new-chat").disabled = value;
  $("#input").placeholder = value ? "正在回答…" : "输入问题，Enter 发送";
}

async function send(text) {
  text = text.trim();
  if (busy || !text) return;
  setBusy(true);
  addUserMessage(text);
  const card = addAssistantCard();
  try {
    const { session_id, job_id } = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text, session_id: sessionId }) });
    sessionId = session_id;
    await poll(job_id, card);
  } catch (e) {
    showError(card, e.message);
  } finally {
    setBusy(false);
    $("#input").focus();
  }
}

async function newChat() {
  if (busy) return;
  try {
    const { session_id } = await api("/api/sessions/reset", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
    sessionId = session_id;
    location.reload();
  } catch (e) {
    alert(e.message);
  }
}

async function watchStatus() {
  const el = $("#status");
  for (;;) {
    try {
      const s = await api("/api/status");
      if (s.search_error) { el.className = "status error"; el.textContent = "资料检索不可用"; el.title = s.search_error; return; }
      if (s.search_ready) { el.className = "status ready"; el.textContent = "检索就绪"; el.title = ""; return; }
    } catch (e) { /* 服务还没起来，继续等 */ }
    await sleep(1000);
  }
}

// ---------- 事件 ----------

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#input");
  const text = input.value;
  input.value = "";
  input.style.height = "";
  send(text);
});

$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {      // 输入法选词时的回车不算发送
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});

$("#input").addEventListener("input", (e) => {                   // 输入框随内容长高
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
});

document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
$("#new-chat").addEventListener("click", newChat);
$("#modal-yes").addEventListener("click", () => answerModal(true));
$("#modal-no").addEventListener("click", () => answerModal(false));
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (modalJob) answerModal(false);                       // 弹窗优先：Esc 等于"取消"
  else if (panel.tab) closePanel();
});
$("#messages").addEventListener("click", (e) => {           // 点回答里的 [E1] → 侧边栏看原文
  const chip = e.target.closest(".cite[data-answer]");
  if (chip) showEvidence(Number(chip.dataset.answer), chip.dataset.id);
});
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => openPanel(t.dataset.tab)));
$("#panel-close").addEventListener("click", closePanel);
$("#open-memory").addEventListener("click", () => togglePanel("memory"));
$("#open-sources").addEventListener("click", () => togglePanel("sources"));
$("#status").addEventListener("click", () => openPanel("sources"));
$("#panel-body").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const row = btn.closest(".mem");
  const action = btn.dataset.action;
  if (action === "evidence") { panel.id = btn.dataset.id; renderEvidence(); }
  if (action === "ask-delete") {
    btn.hidden = true;
    row.querySelector(".mem-confirm").hidden = false;
    row.querySelector('[data-action="cancel-delete"]').focus();          // 默认焦点在"取消"
  }
  if (action === "cancel-delete") {
    row.querySelector(".mem-confirm").hidden = true;
    row.querySelector('[data-action="ask-delete"]').hidden = false;
  }
  if (action === "delete") deleteMemory(row);
});
// 展开"执行过程"时，让它完整出现在视野里（最后一条消息展开后常被输入框挡住）
document.addEventListener("toggle", (e) => {
  if (e.target.matches?.("details.steps") && e.target.open) e.target.scrollIntoView({ block: "nearest" });
}, true);

watchStatus();
$("#input").focus();
