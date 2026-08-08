(() => {
  "use strict";

  const SESSION_KEY = "travel-weather-session-id";
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#message-input");
  const sendButton = document.querySelector("#send-button");
  const restartButton = document.querySelector("#restart-button");
  const conversation = document.querySelector("#conversation");
  const welcome = document.querySelector("#welcome");
  const messageList = document.querySelector("#message-list");
  const exampleButtons = document.querySelectorAll(".example-button");

  let loading = false;
  let sessionId = sessionStorage.getItem(SESSION_KEY);

  function setLoading(nextLoading) {
    loading = nextLoading;
    input.disabled = nextLoading;
    sendButton.disabled = nextLoading || input.value.trim().length === 0;
    restartButton.disabled = nextLoading;
    exampleButtons.forEach((button) => {
      button.disabled = nextLoading;
    });

    document.querySelector("#loading-message")?.remove();
    if (nextLoading) {
      const loadingMessage = document.createElement("div");
      loadingMessage.id = "loading-message";
      loadingMessage.className = "loading-message";
      loadingMessage.setAttribute("role", "status");

      const dots = document.createElement("span");
      dots.className = "loading-dots";
      dots.setAttribute("aria-hidden", "true");
      for (let index = 0; index < 3; index += 1) {
        dots.append(document.createElement("span"));
      }

      const label = document.createElement("span");
      label.textContent = "正在查询景点和天气……";
      loadingMessage.append(dots, label);
      messageList.append(loadingMessage);
      scrollToLatest();
    }
  }

  function showConversation() {
    welcome.hidden = true;
    messageList.hidden = false;
    restartButton.hidden = false;
  }

  function appendMessage(role, content, options = {}) {
    showConversation();

    const wrapper = document.createElement("div");
    wrapper.className = `message message-${role}`;
    if (options.isError) wrapper.classList.add("message-error");
    if (role === "agent" && typeof options.html === "string") {
      wrapper.innerHTML = options.html;
      wrapper.querySelectorAll("table").forEach((table) => {
        const scroller = document.createElement("div");
        scroller.className = "table-scroll";
        table.before(scroller);
        scroller.append(table);
      });
    } else {
      wrapper.textContent = content;
    }

    if (options.needsFollowUp) {
      const hint = document.createElement("div");
      hint.className = "follow-up-hint";
      hint.textContent = "正在等待你补充地点或时间";
      wrapper.append(hint);
    }

    messageList.append(wrapper);
    scrollToLatest();
  }

  function scrollToLatest() {
    requestAnimationFrame(() => {
      conversation.scrollTo({
        top: conversation.scrollHeight,
        behavior: "smooth",
      });
    });
  }

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch {
      return {};
    }
  }

  async function sendMessage(rawMessage) {
    const message = rawMessage.trim();
    if (!message || loading) return;

    appendMessage("user", message);
    input.value = "";
    resizeInput();
    setLoading(true);

    try {
      const body = { message };
      if (sessionId) body.session_id = sessionId;

      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await readJson(response);

      if (!response.ok || typeof data.reply !== "string") {
        throw new Error(data.error || "请求失败，请稍后重试。");
      }

      sessionId = data.session_id;
      sessionStorage.setItem(SESSION_KEY, sessionId);
      appendMessage("agent", data.reply, {
        html: typeof data.reply_html === "string" ? data.reply_html : null,
        needsFollowUp: data.needs_follow_up === true,
      });
    } catch (error) {
      const friendlyMessage =
        error instanceof Error && error.message
          ? error.message
          : "请求出错了，请稍后重试。";
      appendMessage("agent", friendlyMessage, { isError: true });
      input.value = message;
      resizeInput();
    } finally {
      setLoading(false);
      input.focus();
    }
  }

  async function restartConversation() {
    if (loading) return;

    const previousSessionId = sessionId;
    sessionId = null;
    sessionStorage.removeItem(SESSION_KEY);
    messageList.replaceChildren();
    messageList.hidden = true;
    welcome.hidden = false;
    restartButton.hidden = true;
    input.value = "";
    resizeInput();
    input.focus();

    if (previousSessionId) {
      try {
        await fetch(`/api/sessions/${encodeURIComponent(previousSessionId)}`, {
          method: "DELETE",
        });
      } catch {
        // 本地界面已经清空；服务端会话会在进程重启或后续淘汰时释放。
      }
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage(input.value);
  });

  input.addEventListener("input", () => {
    resizeInput();
    sendButton.disabled = loading || input.value.trim().length === 0;
  });

  input.addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(input.value);
    }
  });

  restartButton.addEventListener("click", restartConversation);
  exampleButtons.forEach((button) => {
    button.addEventListener("click", () => sendMessage(button.textContent));
  });

  restartButton.hidden = !sessionId;
  resizeInput();
})();
