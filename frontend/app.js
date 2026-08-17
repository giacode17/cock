// Minimal chat UI wired to the backend (api/app.py locally, or the deployed
// Lambda Function URL below). Swap API_BASE to switch between them.
const API_BASE = "https://gzjlbbqcb7otg5xtebur7766o40jatel.lambda-url.us-west-2.on.aws";
// const API_BASE = "http://localhost:8000";  // local dev (uvicorn api.app:app --reload)
// Must match MOCK_USER_ID in .env / db/seed_data.py.
const USER_ID = "11111111-1111-1111-1111-111111111111";

const chatLog = document.getElementById("chatLog");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const renewalBtn = document.getElementById("renewalBtn");

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  chatLog.appendChild(el);
  chatLog.scrollTop = chatLog.scrollHeight;
  return el;
}

async function postJSON(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json();
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  chatInput.value = "";
  addMessage("user", message);
  const pending = addMessage("agent pending", "Thinking…");

  try {
    const { reply } = await postJSON("/chat", { user_id: USER_ID, message });
    pending.textContent = reply;
    pending.classList.remove("pending");
  } catch (err) {
    pending.textContent = `Error: ${err.message}`;
    pending.classList.remove("pending");
  }
});

renewalBtn.addEventListener("click", async () => {
  addMessage("user", "Should I switch insurance plans at renewal?");
  const pending = addMessage("agent pending", "Thinking…");
  try {
    const res = await fetch(`${API_BASE}/renewal/${USER_ID}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const { reply } = await res.json();
    pending.textContent = reply;
    pending.classList.remove("pending");
  } catch (err) {
    pending.textContent = `Error: ${err.message}`;
    pending.classList.remove("pending");
  }
});
