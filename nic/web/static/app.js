const log = document.getElementById("log");
const tokenInput = document.getElementById("token");
const messageInput = document.getElementById("message");
const sendButton = document.getElementById("send");
const composer = document.getElementById("composer");
const pendingBox = document.getElementById("pending");
const pendingSummary = document.getElementById("pending-summary");

let pendingToken = null;

// The token lives only in this browser; it is never sent anywhere but the laptop.
const saved = localStorage.getItem("nic-token");
if (saved) {
  tokenInput.value = saved;
}

function addMessage(text, kind) {
  const node = document.createElement("div");
  node.className = `msg ${kind}`;
  node.textContent = text;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

function addSteps(node, steps) {
  if (!steps || steps.length === 0) return;
  const wrap = document.createElement("div");
  wrap.className = "steps";
  for (const step of steps) {
    const line = document.createElement("div");
    if (!step.ok) line.className = "failed";
    const result = step.result.length > 160 ? `${step.result.slice(0, 160)}...` : step.result;
    line.textContent = `${step.tool} -> ${result}`;
    wrap.appendChild(line);
  }
  node.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

function showPending(pending) {
  if (!pending) {
    pendingToken = null;
    pendingBox.hidden = true;
    return;
  }
  pendingToken = pending.token;
  pendingSummary.textContent = `Needs your approval: ${pending.summary}`;
  pendingBox.hidden = false;
}

async function post(path, body) {
  const token = tokenInput.value.trim();
  localStorage.setItem("nic-token", token);
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nic-Token": token },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: "bad response from laptop" }));
  if (!response.ok) {
    throw new Error(data.error || `request failed (${response.status})`);
  }
  return data;
}

function renderTurn(data) {
  const node = addMessage(data.reply || "(no reply)", "nic");
  addSteps(node, data.steps);
  showPending(data.pending);
}

async function withBusy(action) {
  sendButton.disabled = true;
  try {
    await action();
  } catch (error) {
    addMessage(error.message, "error");
  } finally {
    sendButton.disabled = false;
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;
  addMessage(text, "user");
  messageInput.value = "";
  withBusy(async () => renderTurn(await post("/api/chat", { message: text })));
});

document.getElementById("approve").addEventListener("click", () => {
  const token = pendingToken;
  showPending(null);
  withBusy(async () => renderTurn(await post("/api/approve", { token })));
});

document.getElementById("reject").addEventListener("click", () => {
  const token = pendingToken;
  showPending(null);
  withBusy(async () => renderTurn(await post("/api/reject", { token })));
});

document.getElementById("reset").addEventListener("click", () => {
  withBusy(async () => {
    const data = await post("/api/reset", {});
    log.innerHTML = "";
    addMessage(data.reply, "system");
    showPending(null);
  });
});
