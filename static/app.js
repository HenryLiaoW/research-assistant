/*
 * 网页前端（阶段 5b）：发消息 → 每 400 毫秒查一次进度 → 显示执行步骤、确认弹窗、最终回答。
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

function citeChip(id, cites) {
  const src = cites[id];
  const title = src ? `${src.file} 第 ${src.page} 页${src.section ? " · " + src.section : ""}` : "这个编号不在检索结果里";
  return `<button type="button" class="cite${src ? "" : " invalid"}" data-id="${id}" title="${escapeHtml(title)}">${id}</button>`;
}

function inline(text, cites) {
  let h = escapeHtml(text);
  h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  // [E1]、[E1][E3]、[E2，E5] 都变成可点的小标签
  h = h.replace(/\[(E\d+(?:\s*[,，、]\s*E\d+)*)\]/g, (_, ids) => ids.split(/\s*[,，、]\s*/).map((id) => citeChip(id, cites)).join(""));
  return h;
}

const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
const isListItem = (line) => /^\s*([-*]|\d+\.)\s+/.test(line);

function renderTable(rows, cites) {
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const data = rows.map(cells).filter((c) => !c.every((x) => /^:?-{2,}:?$/.test(x)));   // 去掉 |---| 分隔行
  const [head, ...body] = data;
  const tr = (cs, tag) => `<tr>${cs.map((c) => `<${tag}>${inline(c, cites)}</${tag}>`).join("")}</tr>`;
  return `<div class="table-wrap"><table><thead>${tr(head, "th")}</thead><tbody>${body.map((r) => tr(r, "td")).join("")}</tbody></table></div>`;
}

function renderMarkdown(text, cites) {
  const lines = String(text).replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (isTableRow(line)) {
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(lines[i++]);
      out.push(renderTable(rows, cites));
    } else if (isListItem(line)) {
      const tag = /^\s*\d+\./.test(line) ? "ol" : "ul";
      const items = [];
      while (i < lines.length && isListItem(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      out.push(`<${tag}>${items.map((t) => `<li>${inline(t, cites)}</li>`).join("")}</${tag}>`);
    } else if (/^#{1,6}\s/.test(line)) {
      out.push(`<p class="h">${inline(line.replace(/^#+\s*/, ""), cites)}</p>`);
      i++;
    } else {
      const para = [];
      while (i < lines.length && lines[i].trim() && !isTableRow(lines[i]) && !isListItem(lines[i])) para.push(lines[i++]);
      out.push(`<p>${para.map((l) => inline(l, cites)).join("<br>")}</p>`);
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
  let html = `<div class="answer">${renderMarkdown(res.answer_raw || "（没有回答）", cites)}</div>`;
  if (res.citations.length) {
    html += `<div class="sources">` + res.citations.map((c) =>
      `<div>${citeChip(c.id, cites)}${escapeHtml(c.file)} 第 ${c.page} 页${c.label ? `（印刷页 ${escapeHtml(c.label)}）` : ""}${c.section ? " · " + escapeHtml(c.section) : ""}</div>`).join("") + `</div>`;
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
    if (snap.status === "done") return showAnswer(card, snap);
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
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && modalJob) answerModal(false); });
// 展开"执行过程"时，让它完整出现在视野里（最后一条消息展开后常被输入框挡住）
document.addEventListener("toggle", (e) => {
  if (e.target.matches?.("details.steps") && e.target.open) e.target.scrollIntoView({ block: "nearest" });
}, true);

watchStatus();
$("#input").focus();
