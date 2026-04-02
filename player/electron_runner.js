const { app, BrowserWindow } = require("electron");
const http = require("http");
const fs = require("fs");
const path = require("path");

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    url: "",
    host: "127.0.0.1",
    port: 18870,
    left: 0,
    top: 0,
    width: 1920,
    height: 1080,
    topmost: false,
  };

  for (let i = 0; i < args.length; i += 1) {
    const k = args[i];
    if (k === "--") continue;
    const v = args[i + 1];
    if (k === "--url") out.url = v;
    if (k === "--host") out.host = v;
    if (k === "--port") out.port = Number(v);
    if (k === "--left") out.left = Number(v);
    if (k === "--top") out.top = Number(v);
    if (k === "--width") out.width = Number(v);
    if (k === "--height") out.height = Number(v);
    if (k === "--topmost") out.topmost = true;
  }
  return out;
}

const SCRIPT_PLAY = `
(() => {
  let played = false;
  const videos = Array.from(document.querySelectorAll("video"));
  for (const v of videos) {
    try {
      const p = v.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
      played = true;
    } catch (_) {}
  }
  if (!played) {
    const btn = document.querySelector("[class*='play'], [title*='播放'], [aria-label*='播放']");
    if (btn) {
      try { btn.click(); played = true; } catch (_) {}
    }
  }
  return { played, videos: videos.length };
})();
`;

const SCRIPT_FULLSCREEN = `
(() => {
  let fullscreen = false;
  const v = document.querySelector("video");
  function fs(node) {
    if (!node) return false;
    try {
      if (document.fullscreenElement) return true;
      if (node.requestFullscreen) { node.requestFullscreen(); return true; }
      if (node.webkitRequestFullscreen) { node.webkitRequestFullscreen(); return true; }
    } catch (_) {}
    return false;
  }
  fullscreen = fs(v) || fs(document.documentElement);
  if (!fullscreen) {
    const btn = document.querySelector("[class*='full'], [title*='全屏'], [aria-label*='全屏']");
    if (btn) {
      try { btn.click(); fullscreen = true; } catch (_) {}
    }
  }
  return { fullscreen };
})();
`;

let mainWindow = null;
let controlServer = null;
const params = parseArgs();
const TRACE_LOG = path.join(__dirname, "..", "runtime", "electron_trace.log");

function trace(...args) {
  try {
    const line = `[${new Date().toISOString()}] ${args
      .map((item) => (typeof item === "string" ? item : JSON.stringify(item)))
      .join(" ")}\n`;
    fs.appendFileSync(TRACE_LOG, line, "utf8");
  } catch (_) {}
}

process.on("uncaughtException", (err) => {
  trace("uncaughtException", String(err && err.stack ? err.stack : err));
});

process.on("unhandledRejection", (err) => {
  trace("unhandledRejection", String(err && err.stack ? err.stack : err));
});

function writeJson(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload), "utf-8");
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Content-Length", String(body.length));
  res.end(body);
}

async function runInject(script) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return { ok: false, error: "window_not_ready" };
  }
  try {
    await mainWindow.webContents.executeJavaScript(script, true);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

function createControlServer() {
  trace("createControlServer", { host: params.host, port: params.port });
  controlServer = http.createServer(async (req, res) => {
    const route = (req.url || "").split("?")[0];
    if (req.method === "GET" && route === "/health") {
      writeJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "POST" && route === "/inject/play") {
      const out = await runInject(SCRIPT_PLAY);
      writeJson(res, out.ok ? 200 : 500, out);
      return;
    }
    if (req.method === "POST" && route === "/inject/fullscreen") {
      const out = await runInject(SCRIPT_FULLSCREEN);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.setFullScreen(true);
      }
      writeJson(res, out.ok ? 200 : 500, out);
      return;
    }
    if (req.method === "POST" && route === "/inject/play_fullscreen") {
      const p = await runInject(SCRIPT_PLAY);
      const f = await runInject(SCRIPT_FULLSCREEN);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.setFullScreen(true);
      }
      writeJson(res, p.ok || f.ok ? 200 : 500, { ok: p.ok || f.ok, play: p, fullscreen: f });
      return;
    }
    if (req.method === "POST" && route === "/stop") {
      writeJson(res, 200, { ok: true });
      app.quit();
      return;
    }
    writeJson(res, 404, { ok: false, error: "not_found" });
  });

  controlServer.on("error", (err) => {
    trace("controlServer.error", String(err && err.stack ? err.stack : err));
  });
  controlServer.listen(params.port, params.host);
}

function createWindow() {
  trace("createWindow.start", params);
  mainWindow = new BrowserWindow({
    x: params.left,
    y: params.top,
    width: params.width,
    height: params.height,
    fullscreen: true,
    kiosk: true,
    alwaysOnTop: params.topmost,
    autoHideMenuBar: true,
    webPreferences: {
      javascript: true,
      webSecurity: true,
      contextIsolation: true,
      nodeIntegration: false,
      autoplayPolicy: "no-user-gesture-required",
    },
  });

  mainWindow.webContents.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
  );

  mainWindow.loadURL(params.url);
  mainWindow.once("ready-to-show", () => {
    trace("window.ready-to-show");
    mainWindow.show();
    mainWindow.setFullScreen(true);
  });
  mainWindow.webContents.on("did-fail-load", (_event, code, desc, validatedUrl) => {
    trace("did-fail-load", { code, desc, validatedUrl });
  });
  mainWindow.webContents.on("did-finish-load", async () => {
    trace("did-finish-load");
    await runInject(SCRIPT_PLAY);
    await runInject(SCRIPT_FULLSCREEN);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setFullScreen(true);
    }
  });
}

app.whenReady().then(() => {
  trace("app.whenReady");
  createControlServer();
  createWindow();
});

app.on("window-all-closed", () => {
  trace("window-all-closed");
  if (controlServer) {
    controlServer.close();
  }
  app.quit();
});
