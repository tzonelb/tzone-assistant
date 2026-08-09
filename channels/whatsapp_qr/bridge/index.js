// T-ZONE WhatsApp Web bridge.
//
// Runs next to the FastAPI backend and speaks the WhatsApp Web protocol
// (Baileys) so a company can connect WhatsApp by scanning a QR code from
// the phone's "Linked devices" screen — no Meta developer app, no Cloud
// API, no paid number.
//
//   node index.js            (or: npm start)
//
// Environment:
//   WA_BRIDGE_PORT     HTTP port for the control API        (default 3901)
//   WA_BRIDGE_SECRET   shared secret with the Python side   (default tzone-local-bridge-secret)
//   TZONE_WEBHOOK_URL  where inbound messages are forwarded (default http://127.0.0.1:8000/webhook/whatsapp-qr)
//   WA_BRIDGE_DATA     session credential storage directory (default ./sessions)
//
// Control API (every request must carry X-Bridge-Secret):
//   POST   /sessions/:key/start      begin or resume a session (QR appears on GET)
//   GET    /sessions/:key            { status, qr, phone, name }
//   POST   /sessions/:key/send       { to, text }  -> { sent, message_id }
//   DELETE /sessions/:key            log out and forget the session
//   GET    /health                   liveness + session summary

import { timingSafeEqual } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import pino from "pino";
import QRCode from "qrcode";
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "baileys";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.WA_BRIDGE_PORT || 3901);
const SECRET = process.env.WA_BRIDGE_SECRET || "tzone-local-bridge-secret";
const WEBHOOK_URL = process.env.TZONE_WEBHOOK_URL || "http://127.0.0.1:8000/webhook/whatsapp-qr";
const DATA_DIR = process.env.WA_BRIDGE_DATA || path.join(__dirname, "sessions");

const logger = pino({ level: process.env.WA_BRIDGE_LOG_LEVEL || "warn" });
const sessions = new Map(); // key -> { sock, status, qrDataUrl, phone, name, stopping }

fs.mkdirSync(DATA_DIR, { recursive: true });

function safeEqual(a, b) {
  const bufA = Buffer.from(String(a || ""));
  const bufB = Buffer.from(String(b || ""));
  if (bufA.length !== bufB.length) return false;
  return timingSafeEqual(bufA, bufB);
}

// Session keys come from the Python side as "waqr-<companyId>-<hex>"; keep
// them filesystem-safe regardless.
function isValidKey(key) {
  return typeof key === "string" && /^[A-Za-z0-9_-]{4,80}$/.test(key);
}

function sessionDir(key) {
  return path.join(DATA_DIR, key);
}

function extractText(message) {
  if (!message) return null;
  if (message.conversation) return message.conversation;
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
  if (message.imageMessage) return message.imageMessage.caption || "[image]";
  if (message.videoMessage) return message.videoMessage.caption || "[video]";
  if (message.audioMessage) return "[voice note]";
  if (message.documentMessage) return message.documentMessage.fileName ? `[document] ${message.documentMessage.fileName}` : "[document]";
  if (message.stickerMessage) return "[sticker]";
  if (message.locationMessage) return "[location]";
  if (message.contactMessage) return "[contact card]";
  if (message.ephemeralMessage) return extractText(message.ephemeralMessage.message);
  if (message.viewOnceMessage) return extractText(message.viewOnceMessage.message);
  if (message.viewOnceMessageV2) return extractText(message.viewOnceMessageV2.message);
  if (message.viewOnceMessageV2Extension) return extractText(message.viewOnceMessageV2Extension.message);
  return null;
}

async function forwardInbound(key, msg) {
  const remoteJid = msg.key?.remoteJid || "";
  if (msg.key?.fromMe) return;

  // Person-to-person chats only: skip groups, broadcast lists and status.
  // Newer WhatsApp delivers some 1:1 chats as "<lid>@lid" with the real
  // phone number in senderPn — use that so those customers aren't dropped.
  let from;
  if (remoteJid.endsWith("@s.whatsapp.net")) {
    from = remoteJid.replace("@s.whatsapp.net", "");
  } else if (remoteJid.endsWith("@lid")) {
    const senderPn = msg.key?.senderPn || "";
    if (!senderPn.endsWith("@s.whatsapp.net")) return;
    from = senderPn.replace("@s.whatsapp.net", "");
  } else {
    return;
  }

  const text = extractText(msg.message);
  if (!text) return;

  const payload = {
    session: key,
    from,
    name: msg.pushName || null,
    text,
    message_id: msg.key?.id || null,
    timestamp: Number(msg.messageTimestamp || 0),
  };
  try {
    await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Bridge-Secret": SECRET },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    logger.error({ err: error?.message, key }, "inbound forward failed");
  }
}

async function startSession(key, { force = false } = {}) {
  const existing = sessions.get(key);
  // Baileys closes the socket with `restartRequired` (515) right after a
  // successful QR scan, and with `timedOut` (408) when a QR expires — in
  // both cases the caller MUST build a fresh socket. So the reconnect path
  // passes force:true; only an already-live/connecting session (called
  // again from the HTTP /start route) short-circuits.
  if (existing && existing.status !== "disconnected" && !force) return existing;

  const entry = existing || { sock: null, status: "starting", qrDataUrl: null, phone: null, name: null, stopping: false, retryTimer: null };
  entry.status = "starting";
  entry.stopping = false;
  sessions.set(key, entry);

  const { state, saveCreds } = await useMultiFileAuthState(sessionDir(key));
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));

  // A DELETE may have landed (and rm'd the creds dir) while we awaited
  // above; if so, don't resurrect the session with a fresh socket. Only
  // clear the slot if it's still ours — never clobber a newer entry.
  if (entry.stopping || sessions.get(key) !== entry) {
    if (sessions.get(key) === entry) sessions.delete(key);
    return entry;
  }

  const sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: ["T-ZONE", "Chrome", "1.0.0"],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });
  entry.sock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      entry.status = "waiting_qr";
      try {
        entry.qrDataUrl = await QRCode.toDataURL(qr, { margin: 1, width: 320 });
      } catch {
        entry.qrDataUrl = null;
      }
    }
    if (connection === "open") {
      entry.status = "connected";
      entry.paired = true;
      entry.qrDataUrl = null;
      entry.phone = (sock.user?.id || "").split(":")[0].split("@")[0] || null;
      entry.name = sock.user?.name || null;
    }
    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      const restart = statusCode === DisconnectReason.restartRequired;
      // Give up on a QR that was never scanned instead of regenerating it
      // forever: only reconnect the expected post-scan restart, or a drop
      // of a session that had actually paired at least once.
      const shouldReconnect = restart || entry.paired;
      if (entry.stopping || loggedOut || !shouldReconnect) {
        entry.status = "disconnected";
        entry.qrDataUrl = null;
        if (loggedOut) fs.rmSync(sessionDir(key), { recursive: true, force: true });
      } else {
        // Transient drop OR the expected post-scan/expiry restart: mark
        // disconnected first, then rebuild a NEW socket (force:true).
        // Marking disconnected is what lets startSession proceed; without
        // it the guard above would return the dead entry and pairing would
        // hang forever.
        entry.status = "reconnecting";
        entry.qrDataUrl = null;
        if (entry.retryTimer) clearTimeout(entry.retryTimer);
        entry.retryTimer = setTimeout(() => {
          entry.retryTimer = null;
          if (entry.stopping) return; // DELETE happened while we waited
          entry.status = "disconnected";
          startSession(key, { force: true }).catch((error) => logger.error({ err: error?.message, key }, "reconnect failed"));
        }, statusCode === DisconnectReason.restartRequired ? 500 : 3000);
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) await forwardInbound(key, msg);
  });

  return entry;
}

// Resume every previously-paired session on boot so a bridge restart does
// not silently disconnect companies.
for (const key of fs.existsSync(DATA_DIR) ? fs.readdirSync(DATA_DIR) : []) {
  if (isValidKey(key) && fs.existsSync(path.join(sessionDir(key), "creds.json"))) {
    startSession(key).catch((error) => logger.error({ err: error?.message, key }, "boot resume failed"));
  }
}

const app = express();
app.use(express.json({ limit: "1mb" }));

app.use((req, res, next) => {
  if (!safeEqual(req.get("x-bridge-secret"), SECRET)) {
    return res.status(403).json({ detail: "Invalid bridge secret" });
  }
  next();
});

app.get("/health", (req, res) => {
  const summary = {};
  for (const [key, entry] of sessions) summary[key] = entry.status;
  res.json({ ok: true, sessions: summary });
});

app.post("/sessions/:key/start", async (req, res) => {
  const { key } = req.params;
  if (!isValidKey(key)) return res.status(400).json({ detail: "Invalid session key" });
  try {
    const entry = await startSession(key);
    res.json({ status: entry.status });
  } catch (error) {
    res.status(500).json({ detail: error?.message || "Failed to start session" });
  }
});

app.get("/sessions/:key", (req, res) => {
  const { key } = req.params;
  if (!isValidKey(key)) return res.status(400).json({ detail: "Invalid session key" });
  const entry = sessions.get(key);
  if (!entry) return res.status(404).json({ detail: "Session not found" });
  res.json({ status: entry.status, qr: entry.qrDataUrl, phone: entry.phone, name: entry.name });
});

app.post("/sessions/:key/send", async (req, res) => {
  const { key } = req.params;
  if (!isValidKey(key)) return res.status(400).json({ detail: "Invalid session key" });
  const entry = sessions.get(key);
  if (!entry || entry.status !== "connected") {
    return res.status(409).json({ detail: "Session is not connected" });
  }
  const to = String(req.body?.to || "").replace(/[^0-9]/g, "");
  const text = String(req.body?.text || "");
  if (!to || !text) return res.status(400).json({ detail: "to and text are required" });
  try {
    const result = await entry.sock.sendMessage(`${to}@s.whatsapp.net`, { text });
    res.json({ sent: true, message_id: result?.key?.id || null });
  } catch (error) {
    res.status(502).json({ sent: false, detail: error?.message || "Send failed" });
  }
});

app.delete("/sessions/:key", async (req, res) => {
  const { key } = req.params;
  if (!isValidKey(key)) return res.status(400).json({ detail: "Invalid session key" });
  const entry = sessions.get(key);
  if (entry) {
    entry.stopping = true;
    if (entry.retryTimer) { clearTimeout(entry.retryTimer); entry.retryTimer = null; }
    try {
      await entry.sock?.logout();
    } catch {
      /* already disconnected */
    }
    sessions.delete(key);
  }
  fs.rmSync(sessionDir(key), { recursive: true, force: true });
  res.json({ deleted: true });
});

app.listen(PORT, "127.0.0.1", () => {
  logger.info({ port: PORT }, "wa-bridge listening");
  console.log(`T-ZONE WhatsApp bridge listening on http://127.0.0.1:${PORT}`);
});
