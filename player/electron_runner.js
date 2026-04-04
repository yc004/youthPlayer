const { app, BrowserWindow, screen } = require("electron");
const http = require("http");
const fs = require("fs");
const path = require("path");

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {
    url: "",
    host: "127.0.0.1",
    port: 18870,
    screenIndex: -1,
    screenLeft: 0,
    screenTop: 0,
    left: 0,
    top: 0,
    width: 1920,
    height: 1080,
    topmost: false,
    windowMode: "fullscreen",
    loop: false,
    ignoreCertificateErrors: false,
  };

  for (let i = 0; i < args.length; i += 1) {
    const k = args[i];
    if (k === "--") continue;
    const v = args[i + 1];
    if (k === "--url") out.url = v;
    if (k === "--host") out.host = v;
    if (k === "--port") out.port = Number(v);
    if (k === "--screen-index") out.screenIndex = Number(v);
    if (k === "--screen-left") out.screenLeft = Number(v);
    if (k === "--screen-top") out.screenTop = Number(v);
    if (k === "--window-mode") out.windowMode = String(v || "fullscreen");
    if (k === "--left") out.left = Number(v);
    if (k === "--top") out.top = Number(v);
    if (k === "--width") out.width = Number(v);
    if (k === "--height") out.height = Number(v);
    if (k === "--topmost") out.topmost = true;
    if (k === "--loop") out.loop = true;
    if (k === "--ignore-certificate-errors") out.ignoreCertificateErrors = true;
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

const SCRIPT_HIDE_CURSOR = `
(() => {
  try {
    const styleId = "__yp_hide_cursor_style__";
    if (!document.getElementById(styleId)) {
      const style = document.createElement("style");
      style.id = styleId;
      style.textContent = "* { cursor: none !important; }";
      document.documentElement.appendChild(style);
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
})();
`;

const SCRIPT_LOOP = `
(() => {
  try {
    if (window.__ypLoopTimer) {
      clearInterval(window.__ypLoopTimer);
      window.__ypLoopTimer = null;
    }
  } catch (_) {}

  function ensureLoopAndPlay() {
    const videos = Array.from(document.querySelectorAll("video"));
    let updated = 0;
    for (const v of videos) {
      try {
        v.loop = true;
        if (!v.__ypEndedHooked) {
          v.addEventListener("ended", () => {
            try {
              v.currentTime = 0;
              const p = v.play();
              if (p && typeof p.catch === "function") p.catch(() => {});
            } catch (_) {}
          }, { passive: true });
          v.__ypEndedHooked = true;
        }
        if (v.paused && !v.ended) {
          const p = v.play();
          if (p && typeof p.catch === "function") p.catch(() => {});
        }
        if (v.ended) {
          try {
            v.currentTime = 0;
            const p2 = v.play();
            if (p2 && typeof p2.catch === "function") p2.catch(() => {});
          } catch (_) {}
        }
        updated += 1;
      } catch (_) {}
    }
    return { videos: videos.length, updated };
  }

  const first = ensureLoopAndPlay();
  try {
    window.__ypLoopTimer = setInterval(ensureLoopAndPlay, 1000);
  } catch (_) {}
  return { ok: true, videos: first.videos, updated: first.updated };
})();
`;

const SCRIPT_MEDIA_STATUS = `
(() => {
  const videos = Array.from(document.querySelectorAll("video"));
  let anyPlaying = false;
  let allEnded = videos.length > 0;
  for (const v of videos) {
    try {
      const playing = !v.paused && !v.ended && (v.currentTime > 0 || v.readyState > 2);
      if (playing) anyPlaying = true;
      if (!v.ended) allEnded = false;
    } catch (_) {
      allEnded = false;
    }
  }
  return {
    videos: videos.length,
    any_playing: anyPlaying,
    all_ended: allEnded,
  };
})();
`;

let mainWindow = null;
let controlServer = null;
const params = parseArgs();
let currentLoop = Boolean(params.loop);
const fullscreenWindow = String(params.windowMode || "fullscreen").toLowerCase() !== "custom";
const TRACE_LOG = path.join(__dirname, "..", "runtime", "electron_trace.log");
const RUNTIME_PROFILE_ROOT = path.join(__dirname, "..", "runtime", "electron_profile");

function ensureDir(dirPath) {
  try {
    fs.mkdirSync(dirPath, { recursive: true });
  } catch (_) {}
}

ensureDir(RUNTIME_PROFILE_ROOT);
ensureDir(path.join(RUNTIME_PROFILE_ROOT, "userData"));
ensureDir(path.join(RUNTIME_PROFILE_ROOT, "cache"));
ensureDir(path.join(RUNTIME_PROFILE_ROOT, "gpuCache"));

function safeSetPath(name, targetPath) {
  try {
    ensureDir(targetPath);
    app.setPath(name, targetPath);
    return true;
  } catch (_) {
    return false;
  }
}

safeSetPath("userData", path.join(RUNTIME_PROFILE_ROOT, "userData"));
safeSetPath("cache", path.join(RUNTIME_PROFILE_ROOT, "cache"));
safeSetPath("logs", path.join(RUNTIME_PROFILE_ROOT, "logs"));
safeSetPath("sessionData", path.join(RUNTIME_PROFILE_ROOT, "sessionData"));
try {
  app.commandLine.appendSwitch("disk-cache-dir", path.join(RUNTIME_PROFILE_ROOT, "cache"));
  app.commandLine.appendSwitch("gpu-program-cache-dir", path.join(RUNTIME_PROFILE_ROOT, "gpuCache"));
} catch (_) {}

if (params.ignoreCertificateErrors) {
  app.commandLine.appendSwitch("ignore-certificate-errors");
}

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

app.on("certificate-error", (event, webContents, url, error, certificate, callback) => {
  if (!params.ignoreCertificateErrors) return;
  event.preventDefault();
  trace("certificate-error ignored", { url, error });
  callback(true);
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
    if (req.method === "GET" && route === "/probe/media_status") {
      if (!mainWindow || mainWindow.isDestroyed()) {
        writeJson(res, 500, { ok: false, error: "window_not_ready" });
        return;
      }
      try {
        const out = await mainWindow.webContents.executeJavaScript(SCRIPT_MEDIA_STATUS, true);
        writeJson(res, 200, { ok: true, status: out || {} });
      } catch (err) {
        writeJson(res, 500, { ok: false, error: String(err) });
      }
      return;
    }
    if (req.method === "POST" && route === "/inject/play") {
      const out = await runInject(SCRIPT_PLAY);
      writeJson(res, out.ok ? 200 : 500, out);
      return;
    }
    if (req.method === "POST" && route === "/navigate") {
      const body = await readJsonBody(req);
      const nextUrl = body && typeof body.url === "string" ? body.url.trim() : "";
      if (!nextUrl) {
        writeJson(res, 400, { ok: false, error: "invalid_url" });
        return;
      }
      currentLoop = Boolean(body && body.loop);
      try {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.loadURL(nextUrl);
          mainWindow.show();
          mainWindow.focus();
          if (fullscreenWindow) {
            mainWindow.setFullScreen(true);
          }
        }
        writeJson(res, 200, { ok: true });
      } catch (err) {
        writeJson(res, 500, { ok: false, error: String(err) });
      }
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
    if (req.method === "POST" && route === "/inject/loop") {
      const out = await runInject(SCRIPT_LOOP);
      writeJson(res, out.ok ? 200 : 500, out);
      return;
    }
    if (req.method === "POST" && route === "/focus") {
      if (mainWindow && !mainWindow.isDestroyed()) {
        try {
          mainWindow.show();
          mainWindow.focus();
          if (params.topmost) {
            mainWindow.setAlwaysOnTop(true, "screen-saver");
          }
        } catch (_) {}
      }
      writeJson(res, 200, { ok: true });
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

function readJsonBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (!chunks.length) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf-8")));
      } catch (_) {
        resolve(null);
      }
    });
    req.on("error", () => resolve(null));
  });
}

function getTargetBounds() {
  return {
    x: Number(params.left) || 0,
    y: Number(params.top) || 0,
    width: Math.max(1, Number(params.width) || 1920),
    height: Math.max(1, Number(params.height) || 1080),
  };
}

function boundsEqual(a, b) {
  return (
    Number(a && a.x) === Number(b && b.x) &&
    Number(a && a.y) === Number(b && b.y) &&
    Number(a && a.width) === Number(b && b.width) &&
    Number(a && a.height) === Number(b && b.height)
  );
}

function resolveTargetDisplay(targetBounds) {
  const displays = screen.getAllDisplays();
  if (Number.isInteger(params.screenIndex) && params.screenIndex >= 0 && params.screenIndex < displays.length) {
    return displays[params.screenIndex];
  }
  const exact = displays.find((item) => boundsEqual(item && item.bounds, targetBounds));
  if (exact) return exact;
  const centerPoint = {
    x: Math.round(targetBounds.x + targetBounds.width / 2),
    y: Math.round(targetBounds.y + targetBounds.height / 2),
  };
  const nearest = screen.getDisplayNearestPoint(centerPoint);
  if (nearest) return nearest;
  return screen.getPrimaryDisplay();
}

function createWindow() {
  const requestedBounds = getTargetBounds();
  const targetDisplay = resolveTargetDisplay(requestedBounds);
  const isFullscreen = fullscreenWindow;
  let finalBounds = isFullscreen
    ? ((targetDisplay && targetDisplay.bounds) || requestedBounds)
    : requestedBounds;
  if (!isFullscreen && targetDisplay) {
    const scale = Number(targetDisplay.scaleFactor || 1) || 1;
    const relLeft = Number(params.left || 0) - Number(params.screenLeft || 0);
    const relTop = Number(params.top || 0) - Number(params.screenTop || 0);
    const dipLeft = Number(targetDisplay.bounds.x || 0) + Math.round(relLeft / scale);
    const dipTop = Number(targetDisplay.bounds.y || 0) + Math.round(relTop / scale);
    const dipWidth = Math.max(50, Math.round(Number(params.width || 0) / scale));
    const dipHeight = Math.max(50, Math.round(Number(params.height || 0) / scale));
    finalBounds = {
      x: dipLeft,
      y: dipTop,
      width: dipWidth,
      height: dipHeight,
    };
  }
  trace("createWindow.start", {
    params,
    requestedBounds,
    targetDisplay: targetDisplay
      ? { id: targetDisplay.id, bounds: targetDisplay.bounds }
      : null,
    finalBounds,
  });
  mainWindow = new BrowserWindow({
    x: finalBounds.x,
    y: finalBounds.y,
    width: finalBounds.width,
    height: finalBounds.height,
    show: false,
    frame: false,
    thickFrame: false,
    resizable: false,
    minimizable: false,
    maximizable: false,
    titleBarStyle: "hidden",
    fullscreen: isFullscreen,
    kiosk: isFullscreen,
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
    mainWindow.setBounds(finalBounds, false);
    mainWindow.show();
    mainWindow.focus();
    if (params.topmost) {
      mainWindow.setAlwaysOnTop(true, "screen-saver");
    }
    if (isFullscreen) {
      mainWindow.setKiosk(true);
      mainWindow.setFullScreen(true);
    }
  });
  mainWindow.on("leave-full-screen", () => {
    if (!isFullscreen) return;
    trace("window.leave-full-screen -> force back");
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setFullScreen(true);
    }
  });
  mainWindow.on("blur", () => {
    if (mainWindow && !mainWindow.isDestroyed() && params.topmost) {
      mainWindow.setAlwaysOnTop(true, "screen-saver");
      mainWindow.focus();
    }
  });
  mainWindow.webContents.on("dom-ready", async () => {
    await runInject(SCRIPT_HIDE_CURSOR);
  });
  mainWindow.webContents.on("did-fail-load", (_event, code, desc, validatedUrl) => {
    trace("did-fail-load", { code, desc, validatedUrl });
  });
  mainWindow.webContents.on("did-finish-load", async () => {
    trace("did-finish-load");
    await runInject(SCRIPT_HIDE_CURSOR);
    await runInject(SCRIPT_PLAY);
    if (currentLoop) {
      await runInject(SCRIPT_LOOP);
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setBounds(finalBounds, false);
      if (isFullscreen) {
        await runInject(SCRIPT_FULLSCREEN);
        mainWindow.setKiosk(true);
        mainWindow.setFullScreen(true);
      }
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
