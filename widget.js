/*!
 * T-ZONE website chat widget.
 *
 * Loaded on a company's own website with a single tag:
 *
 *   <script src="https://<this platform>/widget.js" data-widget-key="wc_..."></script>
 *
 * Deliberately plain JavaScript, no build step, no dependency: it has to run
 * on whatever the embedding page already loads, in whatever order, without
 * colliding with the host site's own styles or scripts. Everything it needs
 * lives inside one IIFE and one namespaced DOM subtree.
 *
 * Talks to backend/api/routes/webchat_widget.py, two routes, no session and
 * no cookie: POST a message, GET the conversation back by polling. The
 * widget key is not a secret -- it is this very file's own src attribute,
 * visible to anyone who views the page source it is embedded in -- so what
 * keeps one visitor from reading another's conversation is the random
 * `visitor_id` this file mints once per browser and never sends anywhere but
 * back to this widget key's own conversation.
 */
(function () {
  "use strict";

  var currentScript =
    document.currentScript ||
    (function () {
      var scripts = document.getElementsByTagName("script");
      return scripts[scripts.length - 1];
    })();

  var widgetKey = currentScript.getAttribute("data-widget-key");

  if (!widgetKey) {
    console.error("[tzone-widget] Missing data-widget-key on the script tag.");
    return;
  }

  var apiBase = new URL(currentScript.src).origin;
  var storageKey = "tzone_webchat_visitor:" + widgetKey;
  var pollMs = 4000;
  var pollTimer = null;
  var renderedIds = {};

  function visitorId() {
    try {
      var existing = window.localStorage.getItem(storageKey);
      if (existing) return existing;

      var minted =
        (window.crypto && window.crypto.randomUUID
          ? window.crypto.randomUUID()
          : "v-" + Date.now() + "-" + Math.random().toString(36).slice(2)) + "";

      window.localStorage.setItem(storageKey, minted);
      return minted;
    } catch (err) {
      // Private browsing or blocked storage: fall back to a per-load id.
      // History will not survive a reload, but the conversation still works.
      return "v-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    }
  }

  var visitor = visitorId();

  // ------------------------------------------------------------------ DOM

  var root = document.createElement("div");
  root.id = "tzone-webchat-root";
  document.addEventListener("DOMContentLoaded", mount);
  if (document.readyState === "interactive" || document.readyState === "complete") {
    mount();
  }

  function mount() {
    if (document.getElementById("tzone-webchat-root")) return;

    injectStyles();
    document.body.appendChild(root);
    root.innerHTML =
      '<button type="button" class="tzone-wc-bubble" aria-label="Open chat">' +
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor"><path d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z"/></svg>' +
      "</button>" +
      '<div class="tzone-wc-panel" hidden>' +
      '<header class="tzone-wc-head">' +
      '<span>Chat with us</span>' +
      '<button type="button" class="tzone-wc-close" aria-label="Close chat">&times;</button>' +
      "</header>" +
      '<div class="tzone-wc-messages" role="log" aria-live="polite"></div>' +
      '<form class="tzone-wc-form">' +
      '<input type="text" maxlength="4000" placeholder="Type a message…" autocomplete="off" required />' +
      '<button type="submit">Send</button>' +
      "</form>" +
      "</div>";

    var bubble = root.querySelector(".tzone-wc-bubble");
    var panel = root.querySelector(".tzone-wc-panel");
    var closeBtn = root.querySelector(".tzone-wc-close");
    var form = root.querySelector(".tzone-wc-form");
    var input = form.querySelector("input");

    bubble.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) {
        fetchMessages();
        startPolling();
        input.focus();
      } else {
        stopPolling();
      }
    });

    closeBtn.addEventListener("click", function () {
      panel.hidden = true;
      stopPolling();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendMessage(text);
    });
  }

  // ------------------------------------------------------------------ API

  function sendMessage(text) {
    renderOptimistic(text);

    fetch(apiBase + "/api/webchat/" + encodeURIComponent(widgetKey) + "/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visitor_id: visitor, text: text }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("send failed");
        return response.json();
      })
      .then(function () {
        fetchMessages();
      })
      .catch(function () {
        renderSystemNote("That message could not be sent. Please try again.");
      });
  }

  function fetchMessages() {
    fetch(
      apiBase +
        "/api/webchat/" +
        encodeURIComponent(widgetKey) +
        "/messages?visitor_id=" +
        encodeURIComponent(visitor) +
        "&limit=50",
    )
      .then(function (response) {
        if (!response.ok) throw new Error("poll failed");
        return response.json();
      })
      .then(function (body) {
        (body.messages || []).forEach(renderStored);
      })
      .catch(function () {
        // A missed poll is not shown to the visitor -- the next one, four
        // seconds later, tries again on its own.
      });
  }

  function startPolling() {
    stopPolling();
    pollTimer = window.setInterval(fetchMessages, pollMs);
  }

  function stopPolling() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // --------------------------------------------------------------- render

  function messagesEl() {
    return root.querySelector(".tzone-wc-messages");
  }

  function renderOptimistic(text) {
    var el = document.createElement("div");
    el.className = "tzone-wc-msg tzone-wc-msg-out tzone-wc-msg-pending";
    el.textContent = text;
    messagesEl().appendChild(el);
    scrollToEnd();
  }

  function renderStored(message) {
    if (renderedIds[message.id]) return;
    renderedIds[message.id] = true;

    // The server's own direction names this from the COMPANY's side, the
    // opposite of what this widget's CSS classes mean: "in" is a message
    // arriving at the company (the visitor's own message), "out" is the
    // company or assistant replying (arriving at the visitor). Treating
    // "out" as the visitor's own message here shows every real reply as a
    // silently-dropped duplicate of the visitor's last line instead of the
    // actual answer -- caught only by sending a real message through a real
    // browser, not by any unit test of the parsing logic alone.
    var isOwnMessage = message.direction === "in";

    // The optimistic bubble from `renderOptimistic` becomes this one's
    // confirmed replacement rather than a duplicate: the poll that follows a
    // send always includes the message just sent.
    var pending = messagesEl().querySelector(".tzone-wc-msg-pending");
    if (pending && isOwnMessage) {
      pending.classList.remove("tzone-wc-msg-pending");
      return;
    }

    var el = document.createElement("div");
    el.className =
      "tzone-wc-msg " + (isOwnMessage ? "tzone-wc-msg-out" : "tzone-wc-msg-in");
    el.textContent = message.text;
    messagesEl().appendChild(el);
    scrollToEnd();
  }

  function renderSystemNote(text) {
    var el = document.createElement("div");
    el.className = "tzone-wc-msg tzone-wc-msg-system";
    el.textContent = text;
    messagesEl().appendChild(el);
    scrollToEnd();
  }

  function scrollToEnd() {
    var el = messagesEl();
    el.scrollTop = el.scrollHeight;
  }

  // ---------------------------------------------------------------- style

  function injectStyles() {
    var style = document.createElement("style");
    style.textContent =
      "#tzone-webchat-root{position:fixed;bottom:20px;right:20px;z-index:2147483000;font-family:system-ui,-apple-system,sans-serif;}" +
      ".tzone-wc-bubble{width:56px;height:56px;border-radius:50%;border:0;background:#0b5fff;color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.2);cursor:pointer;display:flex;align-items:center;justify-content:center;}" +
      ".tzone-wc-panel{position:fixed;bottom:88px;right:20px;width:320px;max-width:calc(100vw - 40px);height:440px;max-height:calc(100vh - 120px);background:#fff;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.25);display:flex;flex-direction:column;overflow:hidden;}" +
      ".tzone-wc-head{background:#0b5fff;color:#fff;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;font-size:14px;font-weight:600;}" +
      ".tzone-wc-close{background:none;border:0;color:#fff;font-size:20px;line-height:1;cursor:pointer;}" +
      ".tzone-wc-messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#f6f7f9;}" +
      ".tzone-wc-msg{max-width:80%;padding:8px 12px;border-radius:14px;font-size:13px;line-height:1.4;word-wrap:break-word;}" +
      ".tzone-wc-msg-out{align-self:flex-end;background:#0b5fff;color:#fff;border-bottom-right-radius:4px;}" +
      ".tzone-wc-msg-in{align-self:flex-start;background:#fff;color:#111;border:1px solid #e2e5ea;border-bottom-left-radius:4px;}" +
      ".tzone-wc-msg-system{align-self:center;color:#8a8f98;font-size:11px;font-style:italic;}" +
      ".tzone-wc-msg-pending{opacity:.6;}" +
      ".tzone-wc-form{display:flex;gap:8px;padding:10px;border-top:1px solid #e2e5ea;background:#fff;}" +
      ".tzone-wc-form input{flex:1;border:1px solid #dcdfe4;border-radius:8px;padding:8px 10px;font-size:13px;outline:none;}" +
      ".tzone-wc-form input:focus{border-color:#0b5fff;}" +
      ".tzone-wc-form button{background:#0b5fff;color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;}" +
      "@media (max-width:480px){.tzone-wc-panel{right:10px;left:10px;width:auto;}}";
    document.head.appendChild(style);
  }
})();
