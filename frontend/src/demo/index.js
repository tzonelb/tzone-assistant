/* Demo mode — the real interface, no server behind it.
 *
 * This exists so the redesigned platform can be looked at and clicked through
 * from a static host (GitHub Pages), where there is no API, no database and no
 * session. It is compiled in ONLY when the bundle is built with
 * VITE_DEMO_MODE=1; the production build never imports this file, so none of it
 * can affect a real deployment.
 *
 * The important property is that nothing here is invented. `fixtures.json` was
 * *recorded* from the real application: a throwaway encrypted company was
 * provisioned, seeded through the real endpoints, and then every GET the screens
 * make was captured verbatim (tools/capture_demo_fixtures.py). So the shapes the
 * screens receive here are the shapes the server actually sends -- if a screen
 * reads a field that does not exist, it breaks here exactly as it would in
 * production, which is the whole point of previewing this way.
 *
 * Writes are held in memory for the life of the tab: replying to a customer
 * appends the message and updates the inbox row, adding a task adds a row. They
 * are gone on reload, and they never leave the browser.
 */

import { saveAccessToken } from "../api/client";

import fixtures from "./fixtures.json";

const DEMO_BANNER =
  "%cT-ZONE demo — recorded fixtures, no server. Changes live in this tab only.";

// A mutable copy: the screens are allowed to change it, the import is not.
const store = JSON.parse(JSON.stringify(fixtures));

function bodyOf(path) {
  return store[path]?.body;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body ?? {}), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/* ---------------------------------------------------------------- helpers */

function conversationKey(channel, userId) {
  return `/conversations/${channel}/${userId}`;
}

function inboxRow(channel, userId) {
  const list = bodyOf("/conversations/");
  return list?.items?.find(
    (row) => row.channel === channel && row.external_user_id === userId,
  );
}

function nowIso() {
  return new Date().toISOString();
}

function nextId(rows) {
  return rows.reduce((max, row) => Math.max(max, Number(row.id) || 0), 0) + 1;
}

/* ------------------------------------------------------------------ reads */

// The inbox honours the filters the screen sends, so the channel tabs, the
// folders and the search box do something. Anything it does not understand is
// ignored rather than refused -- a demo that 400s teaches nothing.
function readInbox(params) {
  const list = bodyOf("/conversations/");
  const channel = params.get("channel");
  const folder = params.get("folder");
  const search = (params.get("search") || "").trim().toLowerCase();
  const department = params.get("department");

  let items = list.items.slice();

  if (channel && channel !== "all") {
    items = items.filter((row) => row.channel === channel);
  }

  if (folder && folder !== "all") {
    items = items.filter((row) => (row.folder || "inbox") === folder);
  }

  if (department && department !== "all") {
    items = items.filter((row) => row.department === department);
  }

  if (search) {
    items = items.filter((row) =>
      [row.customer_name, row.customer_alias, row.last_message]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(search)),
    );
  }

  return {
    ...list,
    items,
    pagination: { ...list.pagination, total: items.length, total_pages: 1 },
  };
}

function readPath(pathname, params) {
  if (pathname === "/conversations/" || pathname === "/conversations") {
    return readInbox(params);
  }

  if (Object.prototype.hasOwnProperty.call(store, pathname)) {
    return bodyOf(pathname);
  }

  // A conversation the fixtures do not carry: answer the empty-but-valid shape
  // the screen expects rather than an error it has no branch for.
  const thread = pathname.match(/^\/conversations\/([^/]+)\/([^/]+)$/);

  if (thread) {
    return {
      status: "ok",
      channel: thread[1],
      user_id: thread[2],
      messages: [],
    };
  }

  return null;
}

/* ----------------------------------------------------------------- writes */

function writePath(pathname, method, payload) {
  // Sending a reply: append it and move the inbox row, the way the server does.
  const reply = pathname.match(/^\/conversations\/([^/]+)\/([^/]+)\/reply$/);

  if (reply && method === "POST") {
    const [, channel, userId] = reply;
    const key = conversationKey(channel, userId);
    const thread = store[key]?.body;
    const text = payload?.text || "";

    if (thread) {
      const message = {
        id: nextId(thread.messages),
        conversation_id: thread.messages[0]?.conversation_id ?? 1,
        time: nowIso(),
        channel,
        user_id: userId,
        direction: "out",
        sender_type: "employee",
        sender_user_id: bodyOf("/api/auth/me")?.user?.id ?? 1,
        text,
        provider_message_id: null,
        source: "manual",
        metadata: { employee_name: bodyOf("/api/auth/me")?.user?.full_name },
      };
      thread.messages.push(message);

      const row = inboxRow(channel, userId);

      if (row) {
        row.last_message = text;
        row.last_direction = "out";
        row.updated_at = message.time;
        row.message_count = (row.message_count || 0) + 1;
      }

      return { status: "ok", message };
    }
  }

  // The control panel: rename a customer, move them to a section, star, pin.
  const control = pathname.match(/^\/conversations\/([^/]+)\/([^/]+)\/control$/);

  if (control && method === "PATCH") {
    const row = inboxRow(control[1], control[2]);

    if (row) {
      Object.entries(payload || {}).forEach(([key, value]) => {
        if (value === null || value === undefined) {
          return;
        }

        if (key === "customer_alias") {
          row.customer_alias = value;
          row.customer_name = value;
          return;
        }

        row[key] = value;
      });

      return { status: "ok", conversation: row };
    }
  }

  if (pathname === "/api/tasks" && method === "POST") {
    const list = bodyOf("/api/tasks");
    const task = {
      ...list.items[0],
      ...payload,
      id: nextId(list.items),
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    list.items.unshift(task);
    list.total = list.items.length;
    return { status: "ok", task };
  }

  const taskStatus = pathname.match(/^\/api\/tasks\/(\d+)\/status$/);
  const taskItem = pathname.match(/^\/api\/tasks\/(\d+)$/);

  if (taskStatus || (taskItem && (method === "PUT" || method === "PATCH"))) {
    const list = bodyOf("/api/tasks");
    const id = Number((taskStatus || taskItem)[1]);
    const task = list.items.find((row) => Number(row.id) === id);

    if (task) {
      Object.assign(task, payload || {}, { updated_at: nowIso() });
      return { status: "ok", task };
    }
  }

  if (taskItem && method === "DELETE") {
    const list = bodyOf("/api/tasks");
    const id = Number(taskItem[1]);
    list.items = list.items.filter((row) => Number(row.id) !== id);
    list.total = list.items.length;
    return { status: "ok" };
  }

  if (pathname === "/api/appointments" && method === "POST") {
    const list = bodyOf("/api/appointments");
    const appointment = {
      ...list.items[0],
      ...payload,
      id: nextId(list.items),
    };
    list.items.push(appointment);
    list.total = list.items.length;
    return { status: "ok", appointment };
  }

  if (pathname === "/api/saved-replies" && method === "POST") {
    const list = bodyOf("/api/saved-replies");
    const item = { ...list.items[0], ...payload, id: nextId(list.items) };
    list.items.push(item);
    return { status: "ok", item };
  }

  if (pathname === "/api/auth/login") {
    return bodyOf("/api/auth/login");
  }

  // Every other write: acknowledge it. The screens read `status`/`success`.
  return { status: "ok", success: true };
}

function readBody(body) {
  if (!body) {
    return null;
  }

  try {
    return JSON.parse(body);
  } catch {
    // FormData — an upload. There is no JSON to read here, and the
    // acknowledgement is all the screen needs back.
    return null;
  }
}

/* ------------------------------------------------------------ the wiring */

export function installDemoMode() {
  const realFetch = window.fetch?.bind(window);

  window.fetch = async function demoFetch(input, init = {}) {
    const raw =
      typeof input === "string" ? input : input?.url || String(input || "");
    let url;

    try {
      url = new URL(raw, window.location.origin);
    } catch {
      return realFetch ? realFetch(input, init) : json({}, 200);
    }

    const isApi =
      url.origin === window.location.origin &&
      (url.pathname.startsWith("/api/") ||
        url.pathname.startsWith("/conversations"));

    if (!isApi) {
      return realFetch ? realFetch(input, init) : json({}, 404);
    }

    const method = String(init.method || "GET").toUpperCase();

    if (method === "GET") {
      const body = readPath(url.pathname, url.searchParams);
      return json(body ?? { status: "ok", items: [], total: 0 });
    }

    return json(writePath(url.pathname, method, readBody(init.body)));
  };

  // The inbox and team chat open a live stream. There is nothing to stream, and
  // an unhandled connection error would surface as a red banner on the screen,
  // so stand in a silent one that simply never emits.
  class DemoEventSource {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.onmessage = null;
      this.onerror = null;
      this.onopen = null;
      this._listeners = new Map();

      // Report the stream as open on the next tick. The inbox shows a
      // "Reconnecting" badge until it sees this, so a stub that stayed silent
      // would put a permanent warning on a screen that is working fine.
      setTimeout(() => {
        if (this.readyState === 2) {
          return;
        }

        this.readyState = 1;
        const event = new Event("open");
        this.onopen?.(event);
        this._listeners.get("open")?.forEach((fn) => fn(event));
      }, 0);
    }

    addEventListener(type, fn) {
      if (!this._listeners.has(type)) {
        this._listeners.set(type, new Set());
      }

      this._listeners.get(type).add(fn);
    }

    removeEventListener(type, fn) {
      this._listeners.get(type)?.delete(fn);
    }

    close() {
      this.readyState = 2;
    }
  }

  window.EventSource = DemoEventSource;

  // Sign the visitor in. AuthContext short-circuits on getAccessToken() before
  // it ever calls the session endpoint, so without this the preview would open
  // on the login screen with no server to log in against. This sets the very
  // same flag a real successful login sets -- it is not a bypass of the guard,
  // it is the guard being satisfied the normal way.
  saveAccessToken(bodyOf("/api/auth/login")?.access_token || "demo");

  // eslint-disable-next-line no-console
  console.log(DEMO_BANNER, "font-weight:700");
}
