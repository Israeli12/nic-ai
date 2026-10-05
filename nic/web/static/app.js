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

// --- Home-screen install prompt (Android/Chrome) --------------------------
const installBar = document.getElementById("install");
let installPrompt = null;

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  if (localStorage.getItem("nic-install-dismissed") !== "1") {
    installBar.hidden = false;
  }
});

document.getElementById("install-go").addEventListener("click", async () => {
  installBar.hidden = true;
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
});

document.getElementById("install-no").addEventListener("click", () => {
  installBar.hidden = true;
  localStorage.setItem("nic-install-dismissed", "1");
});

// Service workers need a secure context; over plain LAN http this no-ops.
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

// --- Hold-to-talk, using the phone's own speech recognition ---------------
// This is the browser's recognizer, not the offline one on the laptop, so it
// is offered only where the phone supports it and never replaces typing.
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const micButton = document.getElementById("mic");

if (SpeechRecognition) {
  micButton.hidden = false;
  const recognizer = new SpeechRecognition();
  recognizer.lang = navigator.language || "en-US";
  recognizer.interimResults = false;
  recognizer.maxAlternatives = 1;

  let listening = false;
  micButton.addEventListener("click", () => {
    if (listening) {
      recognizer.stop();
      return;
    }
    try {
      recognizer.start();
      listening = true;
      micButton.textContent = "Stop";
    } catch (error) {
      addMessage(`microphone unavailable: ${error.message}`, "error");
    }
  });

  recognizer.addEventListener("result", (event) => {
    const text = event.results[0][0].transcript.trim();
    if (!text) return;
    messageInput.value = text;
    composer.requestSubmit();
  });

  recognizer.addEventListener("end", () => {
    listening = false;
    micButton.textContent = "Talk";
  });
}
