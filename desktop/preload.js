const { contextBridge } = require("electron");

// Lets the frontend know it is running inside the Windows desktop app —
// the Login page shows the "Server settings" panel only in this case.
contextBridge.exposeInMainWorld("tzoneDesktop", {
  platform: "windows",
  version: process.env.npm_package_version || "1.0.0",
});
