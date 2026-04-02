"""Playback engine for VLC and web-live browser control."""

import logging
import os
import subprocess
import time
from urllib.parse import urlparse

from config import Config

try:
    import vlc
except ImportError:  # pragma: no cover
    vlc = None

try:  # pragma: no cover
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    win32api = None
    win32con = None
    win32gui = None
    win32process = None

try:  # pragma: no cover
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
except ImportError:  # pragma: no cover
    webdriver = None
    WebDriverException = Exception
    ChromeOptions = None
    ChromeService = None
    EdgeOptions = None
    EdgeService = None


logger = logging.getLogger(__name__)

STREAM_SUFFIXES = (".m3u8", ".flv", ".mpd", ".mp4", ".ts", ".rtmp", ".rtsp")
WEBPAGE_HINTS = ("tv.cctv.com", "live", "webcast", "bilibili.com", "douyin.com")

SCRIPT_PLAY_AND_FULLSCREEN = r"""
const done = { played: false, fullscreen: false, reason: "" };

function safePlay(video) {
  if (!video) return false;
  try {
    video.muted = false;
    video.controls = true;
    const p = video.play();
    if (p && typeof p.catch === "function") {
      p.catch(() => {});
    }
    return true;
  } catch (_) {
    return false;
  }
}

function safeFullscreen(node) {
  if (!node) return false;
  try {
    if (document.fullscreenElement) return true;
    if (node.requestFullscreen) {
      node.requestFullscreen();
      return true;
    }
    if (node.webkitRequestFullscreen) {
      node.webkitRequestFullscreen();
      return true;
    }
    if (node.msRequestFullscreen) {
      node.msRequestFullscreen();
      return true;
    }
  } catch (_) {
    return false;
  }
  return false;
}

const videos = Array.from(document.querySelectorAll("video"));
for (const v of videos) {
  if (safePlay(v)) done.played = true;
}

const activeVideo = videos.find(v => (v.offsetWidth * v.offsetHeight) > 10000) || videos[0];
if (safeFullscreen(activeVideo)) done.fullscreen = true;

if (!done.fullscreen) {
  const root = document.documentElement;
  if (safeFullscreen(root)) done.fullscreen = true;
}

if (!done.played || !done.fullscreen) {
  const selectors = [
    "[class*='play']",
    "[class*='btn-play']",
    "[class*='start']",
    "[aria-label*='播放']",
    "[title*='播放']",
    "[class*='full']",
    "[class*='screen']",
    "[aria-label*='全屏']",
    "[title*='全屏']"
  ];
  for (const sel of selectors) {
    const btn = document.querySelector(sel);
    if (btn) {
      try { btn.click(); } catch (_) {}
    }
  }
}

if (!done.fullscreen) {
  try {
    const evt = new KeyboardEvent("keydown", { key: "f", code: "KeyF", bubbles: true });
    document.dispatchEvent(evt);
  } catch (_) {}
}

done.reason = `videos=${videos.length}`;
done;
"""

SCRIPT_ONLY_PLAY = r"""
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
({ played, videos: videos.length });
"""

SCRIPT_ONLY_FULLSCREEN = r"""
let ok = false;
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
ok = fs(v) || fs(document.documentElement);
if (!ok) {
  const btn = document.querySelector("[class*='full'], [title*='全屏'], [aria-label*='全屏']");
  if (btn) {
    try { btn.click(); ok = true; } catch (_) {}
  }
}
({ fullscreen: ok });
"""


class Player:
    def __init__(self):
        self.instance = None
        self.player = None
        self.current_media = None
        self.current_source = None
        self.current_backend = "idle"
        self.screen_index = Config.PRIMARY_SCREEN
        self.current_screen = None
        self.browser_process = None
        self.browser_driver = None
        self.browser_detached = False
        self.browser_command = []
        self.last_error = ""
        self.last_started_at = None
        self.expected_playing = False

        self._init_vlc()
        self.set_screen(Config.PRIMARY_SCREEN)

    def _init_vlc(self):
        if vlc is None:
            logger.warning("python-vlc not installed, VLC playback unavailable.")
            return

        try:
            options = ["--no-video-title-show", "--quiet"]
            if os.path.exists(Config.VLC_PATH):
                plugin_dir = os.path.join(os.path.dirname(Config.VLC_PATH), "plugins")
                options.append(f"--plugin-path={plugin_dir}")
                logger.info("Using VLC at: %s", Config.VLC_PATH)
            else:
                logger.info("Configured VLC path not found, trying system VLC.")

            self.instance = vlc.Instance(*options)
            self.player = self.instance.media_player_new()
            logger.info("VLC player initialized.")
        except Exception as exc:  # pragma: no cover
            self.instance = None
            self.player = None
            self.last_error = str(exc)
            logger.error("VLC init failed: %s", exc)

    def get_available_screens(self):
        screens = []
        if win32api:
            try:
                for index, monitor in enumerate(win32api.EnumDisplayMonitors()):
                    info = win32api.GetMonitorInfo(monitor[0])
                    left, top, right, bottom = info["Monitor"]
                    screens.append(
                        {
                            "index": index,
                            "name": info.get("Device", f"Screen {index + 1}"),
                            "left": left,
                            "top": top,
                            "width": right - left,
                            "height": bottom - top,
                            "primary": bool(info.get("Flags", 0) & 1),
                        }
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to read monitors from Win32 API: %s", exc)

        if not screens:
            screens = [
                dict(item, primary=(item["index"] == Config.PRIMARY_SCREEN))
                for item in Config.SCREEN_FALLBACKS
            ]
        return screens

    def set_screen(self, screen_index):
        self.screen_index = int(screen_index)
        screens = self.get_available_screens()
        self.current_screen = next(
            (item for item in screens if item["index"] == self.screen_index),
            screens[0] if screens else None,
        )
        logger.info("Target screen set to: %s", self.screen_index)
        return True

    def play_local(self, file_path):
        if not os.path.exists(file_path):
            self.last_error = f"File not found: {file_path}"
            logger.error(self.last_error)
            return False
        return self._play_vlc_media(file_path, source_type="local")

    def play_nas(self, nas_path):
        return self._play_vlc_media(nas_path, source_type="nas")

    def play_live(self, live_url):
        if self._should_use_browser(live_url):
            return self._open_web_browser(live_url)
        return self._play_vlc_media(live_url, source_type="stream")

    def _play_vlc_media(self, source, source_type="media"):
        if not self.player or not self.instance:
            self.last_error = "VLC player is not initialized."
            logger.error(self.last_error)
            return False

        try:
            self.stop()
            media = self.instance.media_new(source)
            self.player.set_media(media)
            result = self.player.play()
            time.sleep(0.5)
            self._set_fullscreen()

            self.current_media = media
            self.current_source = source
            self.current_backend = "vlc"
            self.expected_playing = True
            self.last_started_at = time.time()
            self.last_error = ""
            logger.info("Play %s started: %s (result=%s)", source_type, source, result)
            return result != -1
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("VLC playback failed: %s", exc)
            return False

    def _should_use_browser(self, url):
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        if path.endswith(STREAM_SUFFIXES):
            return False
        return url.startswith(("http://", "https://")) and any(hint in url.lower() for hint in WEBPAGE_HINTS)

    def _open_web_browser(self, url):
        self.stop()
        os.makedirs(Config.WEB_LIVE_BROWSER_PROFILE, exist_ok=True)

        if Config.WEB_LIVE_SCRIPT_INJECTION and webdriver:
            if self._open_web_browser_with_driver(url):
                return True
            logger.warning("Driver mode failed, fallback to native browser process.")

        return self._open_web_browser_native(url)

    def _open_web_browser_with_driver(self, url):
        try:
            browser = (Config.WEB_LIVE_DRIVER_BROWSER or "edge").lower()
            if browser == "chrome":
                options = ChromeOptions()
                options.binary_location = self._find_browser_executable(prefer="chrome") or ""
            else:
                options = EdgeOptions()
                options.binary_location = self._find_browser_executable(prefer="edge") or ""

            for arg in Config.WEB_LIVE_BROWSER_ARGS:
                options.add_argument(arg)

            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-features=msSmartScreenProtection")
            options.add_argument(f"--user-data-dir={Config.WEB_LIVE_BROWSER_PROFILE}")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            if browser == "chrome":
                service = (
                    ChromeService(executable_path=Config.WEB_LIVE_DRIVER_PATH)
                    if Config.WEB_LIVE_DRIVER_PATH
                    else ChromeService()
                )
                self.browser_driver = webdriver.Chrome(service=service, options=options)
            else:
                service = (
                    EdgeService(executable_path=Config.WEB_LIVE_DRIVER_PATH)
                    if Config.WEB_LIVE_DRIVER_PATH
                    else EdgeService()
                )
                self.browser_driver = webdriver.Edge(service=service, options=options)

            self.browser_driver.get(url)
            self.browser_process = None
            self.browser_detached = False
            self.browser_command = ["selenium", browser, url]

            # Try script injection multiple times because many live pages lazy-load video nodes.
            self._inject_play_and_fullscreen(retry=Config.WEB_LIVE_SCRIPT_RETRY)
            self._position_driver_window()

            self.current_source = url
            self.current_backend = "browser"
            self.expected_playing = True
            self.last_started_at = time.time()
            self.last_error = ""
            logger.info("Web live opened via Selenium driver: %s", url)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._close_driver_only()
            logger.error("Driver browser open failed: %s", exc)
            return False

    def _open_web_browser_native(self, url):
        try:
            browser_path = self._find_browser_executable()
            if browser_path:
                command = [browser_path]
                command.extend(Config.WEB_LIVE_BROWSER_ARGS)
                command.append(f"--user-data-dir={Config.WEB_LIVE_BROWSER_PROFILE}")
                command.append(url)
                self.browser_process = subprocess.Popen(command)
                self.browser_detached = False
                self.browser_command = command
            else:
                command = ["cmd", "/c", "start", "", url]
                self.browser_process = subprocess.Popen(command, shell=False)
                self.browser_detached = True
                self.browser_command = command
                logger.warning("No browser executable found, fallback to system default.")

            self.current_source = url
            self.current_backend = "browser"
            self.expected_playing = True
            self.last_started_at = time.time()
            self.last_error = ""
            time.sleep(1)
            self._position_browser_window()
            logger.info("Web live opened via native browser process: %s", url)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Native browser open failed: %s", exc)
            return False

    def _find_browser_executable(self, prefer=None):
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        edge_paths = [
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        custom = [Config.WEB_LIVE_BROWSER_PATH] if Config.WEB_LIVE_BROWSER_PATH else []

        if prefer == "chrome":
            candidates = custom + chrome_paths + edge_paths
        elif prefer == "edge":
            candidates = custom + edge_paths + chrome_paths
        else:
            candidates = custom + chrome_paths + edge_paths

        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate
        return None

    def _inject_play_and_fullscreen(self, retry=2):
        if not self.browser_driver:
            return False
        for _ in range(max(1, retry)):
            try:
                result = self.browser_driver.execute_script(SCRIPT_PLAY_AND_FULLSCREEN)
                logger.info("Injected play+fullscreen script result: %s", result)
                return True
            except WebDriverException:
                time.sleep(Config.WEB_LIVE_SCRIPT_RETRY_INTERVAL)
        return False

    def inject_web_play(self):
        if not self.browser_driver:
            self.last_error = "Web driver unavailable, cannot inject play script."
            return False
        try:
            result = self.browser_driver.execute_script(SCRIPT_ONLY_PLAY)
            logger.info("Injected play script result: %s", result)
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Inject play script failed: %s", exc)
            return False

    def inject_web_fullscreen(self):
        if not self.browser_driver:
            self.last_error = "Web driver unavailable, cannot inject fullscreen script."
            return False
        try:
            result = self.browser_driver.execute_script(SCRIPT_ONLY_FULLSCREEN)
            logger.info("Injected fullscreen script result: %s", result)
            self._position_driver_window()
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Inject fullscreen script failed: %s", exc)
            return False

    def _position_driver_window(self):  # pragma: no cover
        if not self.browser_driver or not self.current_screen:
            return
        screen = self.current_screen
        try:
            self.browser_driver.set_window_rect(
                x=screen["left"],
                y=screen["top"],
                width=screen["width"],
                height=screen["height"],
            )
            self.browser_driver.fullscreen_window()
        except Exception as exc:
            logger.warning("Position driver window failed: %s", exc)

    def _position_browser_window(self):  # pragma: no cover
        if not (self.browser_process and win32gui and win32process and self.current_screen):
            return

        screen = self.current_screen
        for _ in range(20):
            hwnd = self._find_window_by_pid(self.browser_process.pid)
            if hwnd:
                flags = win32con.SWP_SHOWWINDOW
                z_order = win32con.HWND_TOPMOST if Config.WINDOW_TOPMOST else win32con.HWND_NOTOPMOST
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetWindowPos(
                    hwnd,
                    z_order,
                    screen["left"],
                    screen["top"],
                    screen["width"],
                    screen["height"],
                    flags,
                )
                return
            time.sleep(0.5)

    def _find_window_by_pid(self, pid):  # pragma: no cover
        matched = []

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if process_id == pid:
                matched.append(hwnd)

        win32gui.EnumWindows(callback, None)
        return matched[0] if matched else None

    def ensure_foreground(self):  # pragma: no cover
        if self.current_backend != "browser":
            return
        if self.browser_driver:
            self._position_driver_window()
        else:
            self._position_browser_window()

    def stop(self):
        try:
            if self.player:
                self.player.stop()
            self.current_media = None

            if self.browser_driver:
                self._close_driver_only()

            if self.browser_process:
                self._stop_browser_process()

            self.current_source = None
            self.current_backend = "idle"
            self.expected_playing = False
            self.browser_detached = False
            logger.info("Playback stopped.")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Stop playback failed: %s", exc)
            return False

    def _close_driver_only(self):
        try:
            if self.browser_driver:
                self.browser_driver.quit()
        except Exception:
            pass
        finally:
            self.browser_driver = None

    def _stop_browser_process(self):
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.browser_process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                self.browser_process.terminate()
        finally:
            self.browser_process = None

    def pause(self):
        if self.current_backend != "vlc" or not self.player:
            self.last_error = "Pause is only available for VLC playback."
            return False
        try:
            self.player.pause()
            logger.info("Playback paused.")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Pause failed: %s", exc)
            return False

    def resume(self):
        if self.current_backend == "browser":
            self.ensure_foreground()
            return True
        if not self.player:
            self.last_error = "VLC player is not initialized."
            return False
        try:
            self.player.play()
            logger.info("Playback resumed.")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Resume failed: %s", exc)
            return False

    def _set_fullscreen(self):
        if not self.player:
            return
        try:
            self.player.set_fullscreen(True)
        except Exception as exc:
            logger.warning("Set VLC fullscreen failed: %s", exc)

    def is_healthy(self):
        if not self.expected_playing:
            return True

        if self.current_backend == "browser":
            if self.browser_driver:
                return self.browser_driver.session_id is not None
            if self.browser_detached:
                return True
            return self.browser_process is not None and self.browser_process.poll() is None

        if self.current_backend == "vlc" and self.player and vlc is not None:
            state = self.player.get_state()
            return state not in {vlc.State.Error, vlc.State.Ended}

        return False

    def get_status(self):
        state_label = "Idle"
        is_playing = False

        if self.current_backend == "browser":
            state_label = "BrowserPlayback"
            if self.browser_driver:
                is_playing = self.browser_driver.session_id is not None
            else:
                is_playing = self.browser_detached or (
                    self.browser_process is not None and self.browser_process.poll() is None
                )
        elif self.player and vlc is not None:
            state = self.player.get_state()
            state_label = str(state)
            is_playing = state == vlc.State.Playing

        return {
            "state": state_label,
            "is_playing": is_playing,
            "backend": self.current_backend,
            "web_driver": bool(self.browser_driver),
            "current_source": self.current_source,
            "screen_index": self.screen_index,
            "screen_name": self.current_screen["name"] if self.current_screen else f"Screen {self.screen_index}",
            "last_error": self.last_error,
            "last_started_at": self.last_started_at,
        }
