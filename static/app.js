const chat = document.getElementById("chat");
const chatForm = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const emptyState = document.getElementById("empty-state");

const memoryForm = document.getElementById("memory-form");
const memoryInput = document.getElementById("memory-input");
const memoryList = document.getElementById("memory-list");
const memoryCount = document.getElementById("memory-count");

const contextLabel = document.getElementById("context-label");
const contextBar = document.getElementById("context-bar");
const summaryTrigger = document.getElementById("summary-trigger");
const statusDot = document.getElementById("status-dot");
const serverStatus = document.getElementById("server-status");
const modelName = document.getElementById("model-name");

const summaryNotice = document.getElementById("summary-notice");
const closeSummary = document.getElementById("close-summary");
const newChatButton = document.getElementById("new-chat");
const toggleMemoryButton = document.getElementById("toggle-memory");
const sidebar = document.querySelector(".sidebar");

let generating = false;

marked.setOptions({
  breaks: true,
  gfm: true,
});


function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}


function renderMarkdown(text) {
  const raw = marked.parse(text || "");
  return DOMPurify.sanitize(raw);
}


function enhanceCodeBlocks(container) {
  container.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-code")) return;

    const button = document.createElement("button");
    button.className = "copy-code";
    button.type = "button";
    button.textContent = "복사";

    button.addEventListener("click", async () => {
      const code = pre.querySelector("code");
      const text = code ? code.innerText : pre.innerText;

      await navigator.clipboard.writeText(text);
      button.textContent = "복사됨";

      setTimeout(() => {
        button.textContent = "복사";
      }, 1000);
    });

    pre.appendChild(button);
  });
}


function hideEmptyState() {
  const el = document.getElementById("empty-state");
  if (el) el.remove();
}


function scrollBottom() {
  chat.scrollTop = chat.scrollHeight;
}


function addUserMessage(text) {
  hideEmptyState();

  const row = document.createElement("div");
  row.className = "message-row user";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;

  row.appendChild(bubble);
  chat.appendChild(row);
  scrollBottom();

  return row;
}


function addAssistantMessage(text = "") {
  hideEmptyState();

  const row = document.createElement("div");
  row.className = "message-row assistant";

  const role = document.createElement("div");
  role.className = "message-role";
  role.textContent = "Qwen";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble assistant-content typing-cursor";
  bubble.innerHTML = renderMarkdown(text);

  row.appendChild(role);
  row.appendChild(bubble);

  chat.appendChild(row);
  scrollBottom();

  return { row, bubble };
}


function addError(text) {
  const el = document.createElement("div");
  el.className = "error-box";
  el.textContent = text;
  chat.appendChild(el);
  scrollBottom();
}


function updateStatus(status) {
  if (!status) return;

  const estimated = Math.round(status.estimated_tokens || 0);
  const limit = status.context_limit || 8192;
  const percent = Math.min(
    100,
    Number(status.usage_percent ?? (estimated / limit * 100))
  );

  contextLabel.textContent = `${estimated.toLocaleString()} / ${limit.toLocaleString()}`;
  contextBar.style.width = `${percent}%`;
  summaryTrigger.textContent = (status.summary_trigger || 0).toLocaleString();

  memoryCount.textContent = status.memory_count ?? 0;

  if (status.model) {
    modelName.textContent = status.model;
  }

  if (status.connected === true) {
    statusDot.className = "status-dot online";
    serverStatus.textContent = "LM Studio 연결됨";
  } else if (status.connected === false) {
    statusDot.className = "status-dot offline";
    serverStatus.textContent = "LM Studio 연결 실패";
  }
}


function renderMemory(facts) {
  memoryList.innerHTML = "";
  memoryCount.textContent = facts.length;

  if (!facts.length) {
    const empty = document.createElement("div");
    empty.className = "memory-empty";
    empty.textContent = "저장된 메모가 없습니다.";
    memoryList.appendChild(empty);
    return;
  }

  facts.forEach((fact, index) => {
    const item = document.createElement("div");
    item.className = "memory-item";

    const text = document.createElement("span");
    text.textContent = fact;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "삭제";

    remove.addEventListener("click", async () => {
      const response = await fetch(`/api/memory/${index}`, {
        method: "DELETE",
      });

      const data = await response.json();

      if (!response.ok) {
        alert(data.detail || "메모 삭제 실패");
        return;
      }

      renderMemory(data.facts || []);
      updateStatus(data.status);
    });

    item.appendChild(text);
    item.appendChild(remove);
    memoryList.appendChild(item);
  });
}


async function loadInitialData() {
  try {
    const response = await fetch("/api/history");
    const data = await response.json();

    const messages = data.messages || [];

    if (messages.length) {
      hideEmptyState();

      messages.forEach((message) => {
        if (message.role === "user") {
          addUserMessage(message.content);
        } else if (message.role === "assistant") {
          const view = addAssistantMessage(message.content);
          view.bubble.classList.remove("typing-cursor");
          enhanceCodeBlocks(view.bubble);
        }
      });
    }

    renderMemory(data.memory || []);
    updateStatus(data.status);

    const statusResponse = await fetch("/api/status");
    const server = await statusResponse.json();
    updateStatus(server);

    if (server.error) {
      modelName.textContent = server.error;
    }
  } catch (error) {
    statusDot.className = "status-dot offline";
    serverStatus.textContent = "웹 서버 오류";
    addError(String(error));
  }
}


async function sendMessage(text) {
  if (generating) return;

  generating = true;
  sendButton.disabled = true;
  input.disabled = true;

  addUserMessage(text);
  const assistant = addAssistantMessage("");

  let fullText = "";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message: text }),
    });

    if (!response.ok || !response.body) {
      const body = await response.text();
      throw new Error(body || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;

        let payload;
        try {
          payload = JSON.parse(line);
        } catch {
          continue;
        }

        if (payload.type === "token") {
          fullText += payload.data;
          assistant.bubble.innerHTML = renderMarkdown(fullText);
          scrollBottom();
        }

        if (payload.type === "summary") {
          summaryNotice.classList.remove("hidden");
          updateStatus(payload.data.status);
        }

        if (payload.type === "meta") {
          updateStatus(payload.data.status);
          modelName.textContent = payload.data.model || modelName.textContent;
        }

        if (payload.type === "done") {
          updateStatus(payload.data.status);

          if (payload.data.compacted) {
            summaryNotice.classList.remove("hidden");
          }
        }

        if (payload.type === "error") {
          throw new Error(payload.data);
        }
      }
    }

    assistant.bubble.classList.remove("typing-cursor");
    assistant.bubble.innerHTML = renderMarkdown(fullText);
    enhanceCodeBlocks(assistant.bubble);
    scrollBottom();

  } catch (error) {
    assistant.bubble.classList.remove("typing-cursor");

    if (!fullText) {
      assistant.row.remove();
    }

    addError(
      "응답 오류: " + error.message +
      "\nLM Studio 서버와 모델 로딩 상태를 확인해 주세요."
    );
  } finally {
    generating = false;
    sendButton.disabled = false;
    input.disabled = false;
    input.focus();
  }
}


chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const text = input.value.trim();
  if (!text || generating) return;

  input.value = "";
  input.style.height = "auto";

  await sendMessage(text);
});


input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});


input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
});


memoryForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const text = memoryInput.value.trim();
  if (!text) return;

  const response = await fetch("/api/memory", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });

  const data = await response.json();

  if (!response.ok) {
    alert(data.detail || "메모 추가 실패");
    return;
  }

  memoryInput.value = "";
  renderMemory(data.facts || []);
  updateStatus(data.status);
});


newChatButton.addEventListener("click", async () => {
  if (generating) return;

  const ok = confirm(
    "현재 대화와 누적 요약을 삭제하고 새 채팅을 시작할까요?\n장기 메모는 유지됩니다."
  );

  if (!ok) return;

  const response = await fetch("/api/new", {
    method: "POST",
  });

  const data = await response.json();

  if (!response.ok) {
    alert(data.detail || "새 채팅 시작 실패");
    return;
  }

  chat.innerHTML = `
    <div id="empty-state" class="empty-state">
      <div class="empty-logo">Q</div>
      <h2>새 대화를 시작했어요</h2>
      <p>장기 메모는 그대로 유지됩니다.</p>
    </div>
  `;

  summaryNotice.classList.add("hidden");
  updateStatus(data.status);
});


closeSummary.addEventListener("click", () => {
  summaryNotice.classList.add("hidden");
});


toggleMemoryButton.addEventListener("click", () => {
  sidebar.classList.toggle("open");
});


document.querySelectorAll(".suggestions button").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.textContent;
    input.focus();
  });
});


loadInitialData();
