const { app, BrowserWindow, shell, Menu } = require("electron");
const http = require("http");
const fs = require("fs");
const path = require("path");

// The built frontend (React SPA) is bundled next to this file. It is served
// over a local loopback HTTP server instead of file:// because the app uses
// BrowserRouter — deep links like /dashboard need an SPA fallback to
// index.html, which file:// cannot provide.
const DIST_DIR = path.join(__dirname, "dist");

// Fixed preferred port so the origin stays stable across launches; the
// backend accepts any loopback origin, so the fallback ports work too.
const PORTS = [41100, 41101, 41102, 41103, 41104];

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".map": "application/json",
};

function serveDist(request, response) {
  const requestPath = decodeURIComponent(
    new URL(request.url, "http://localhost").pathname,
  );

  let filePath = path.normalize(path.join(DIST_DIR, requestPath));

  // Never serve anything outside the bundled dist folder.
  if (!filePath.startsWith(DIST_DIR)) {
    response.writeHead(403);
    response.end();
    return;
  }

  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    // SPA fallback: every unknown route renders index.html and the
    // React router takes it from there.
    filePath = path.join(DIST_DIR, "index.html");
  }

  const extension = path.extname(filePath).toLowerCase();

  fs.readFile(filePath, (error, content) => {
    if (error) {
      response.writeHead(404);
      response.end();
      return;
    }
    response.writeHead(200, {
      "Content-Type": MIME_TYPES[extension] || "application/octet-stream",
    });
    response.end(content);
  });
}

function startLocalServer() {
  return new Promise((resolve, reject) => {
    const tryPort = (index) => {
      if (index >= PORTS.length) {
        reject(new Error("No free local port for the T-ZONE app."));
        return;
      }

      const server = http.createServer(serveDist);

      server.once("error", () => tryPort(index + 1));
      server.listen(PORTS[index], "127.0.0.1", () =>
        resolve(PORTS[index]),
      );
    };

    tryPort(0);
  });
}

function createWindow(port) {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 640,
    show: false,
    backgroundColor: "#f4f7fb",
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  window.once("ready-to-show", () => window.show());

  // External links (channel connect flows, docs, etc.) open in the real
  // browser, never inside the app shell.
  window.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  window.loadURL(`http://127.0.0.1:${port}/`);
}

Menu.setApplicationMenu(null);

// Single instance — clicking the icon twice focuses the existing window.
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const [existing] = BrowserWindow.getAllWindows();
    if (existing) {
      if (existing.isMinimized()) existing.restore();
      existing.focus();
    }
  });

  app.whenReady().then(async () => {
    const port = await startLocalServer();
    createWindow(port);

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow(port);
      }
    });
  });

  app.on("window-all-closed", () => {
    app.quit();
  });
}
