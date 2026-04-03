"""Playback engine: VLC for media streams, Electron for web live pages."""

import logging
import os
import socket
import subprocess
import threading
import time
import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from shutil import which

from config import Config

try:
    import vlc
except ImportError:  # pragma: no cover
    vlc = None

try:  # pragma: no cover
    import win32api
except ImportError:  # pragma: no cover
    win32api = None

try:  # pragma: no cover
    import win32con
    import win32gui
    import win32ui
except ImportError:  # pragma: no cover
    win32con = None
    win32gui = None
    win32ui = None


logger = logging.getLogger(__name__)

STREAM_SUFFIXES = (".m3u8", ".flv", ".mpd", ".mp4", ".ts", ".rtmp", ".rtsp")
WEBPAGE_HINTS = ("tv.cctv.com", "live", "webcast", "bilibili.com", "douyin.com")


class Player:
    def __init__(self):
        self.instance = None
        self.player = None
        self.current_media = None
        self.current_source = None
        self.current_backend = "idle"
        self.screen_index = Config.PRIMARY_SCREEN
        self.current_screen = None

        self.electron_process = None
        self.electron_port = None
        self.electron_log_handle = None
        self.browser_command = []

        self.last_error = ""
        self.last_started_at = None
        self.expected_playing = False
        self._op_lock = threading.RLock()
        self.playlist_items = []
        self.playlist_index = 0
        self.playlist_mode = "single"
        self.playlist_loop_count = 0  # 0=无限
        self.playlist_round = 1
        self.playlist_backend = "vlc"
        self._playlist_stop_event = threading.Event()
        self._playlist_thread = None
        self.monitor_last_capture_at = None
        self.monitor_last_capture_path = ""
        self.screensaver_url = Path(os.path.join(os.path.dirname(__file__), "screensaver.html")).resolve().as_uri()

        self._init_vlc()
        self.set_screen(Config.PRIMARY_SCREEN)

    def _build_electron_env(self):
        """Electron 主进程不能在 ELECTRON_RUN_AS_NODE=1 下启动。"""
        env = os.environ.copy()
        if env.get("ELECTRON_RUN_AS_NODE") == "1":
            env.pop("ELECTRON_RUN_AS_NODE", None)
        return env

    def _init_vlc(self):
        if vlc is None:
            logger.warning("python-vlc not installed, VLC playback unavailable.")
            return
        try:
            options = [
                "--no-video-title-show",
                "--quiet",
                "--fullscreen",
                "--video-on-top",
                "--mouse-hide-timeout=100",
            ]
            if os.path.exists(Config.VLC_PATH):
                logger.info("Using VLC at: %s", Config.VLC_PATH)
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
            except Exception as exc:
                logger.warning("Failed to read monitor info: %s", exc)
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
        with self._op_lock:
            if not os.path.exists(file_path):
                self.last_error = f"File not found: {file_path}"
                logger.error(self.last_error)
                return False
            if Config.ALL_PLAY_VIA_ELECTRON:
                return self._open_via_electron(file_path, source_type="local")
            return self._play_vlc_media(file_path, source_type="local")

    def play_nas(self, nas_path):
        with self._op_lock:
            if Config.ALL_PLAY_VIA_ELECTRON:
                return self._open_via_electron(nas_path, source_type="nas")
            return self._play_vlc_media(nas_path, source_type="nas")

    def play_live(self, live_url):
        with self._op_lock:
            if Config.ALL_PLAY_VIA_ELECTRON:
                return self._open_via_electron(live_url, source_type="live")
            if self._should_use_browser(live_url):
                return self._open_web_live_electron(live_url)
            return self._play_vlc_media(live_url, source_type="stream")

    def show_screensaver(self):
        with self._op_lock:
            if not Config.IDLE_SCREENSAVER_ENABLED:
                return False
            payload = {"title": Config.IDLE_SCREENSAVER_TITLE or "校园电视播放系统"}
            image_path = (Config.IDLE_SCREENSAVER_IMAGE or "").strip()
            if image_path and os.path.exists(image_path):
                try:
                    payload["image"] = Path(image_path).resolve().as_uri()
                except Exception:
                    logger.warning("Invalid screensaver image path: %s", image_path)
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            payload_b64 = base64.urlsafe_b64encode(payload_text.encode("utf-8")).decode("ascii").rstrip("=")
            target_url = f"{self.screensaver_url}?cfg={payload_b64}"
            if (
                self.current_backend == "electron"
                and (self.current_source or "") == target_url
                and self.electron_process
                and self.electron_process.poll() is None
            ):
                return True
            return self._open_web_live_electron(target_url)

    def _to_electron_url(self, source):
        if source.startswith(("http://", "https://", "file://")):
            return source
        # UNC: \\server\share\path -> file://server/share/path
        if source.startswith("\\\\"):
            return "file://" + source.lstrip("\\").replace("\\", "/")
        try:
            return Path(source).resolve().as_uri()
        except Exception:
            return source

    def _open_via_electron(self, source, source_type="media"):
        parsed = urlparse(str(source))
        if parsed.scheme in {"rtsp", "rtmp"}:
            logger.warning("Electron 对 %s 协议支持有限，回退 VLC: %s", parsed.scheme, source)
            return self._play_vlc_media(source, source_type=source_type)
        target_url = self._to_electron_url(source)
        logger.info("Using Electron for %s source: %s -> %s", source_type, source, target_url)
        return self._open_web_live_electron(target_url)

    def _play_vlc_media(self, source, source_type="media"):
        return self._play_vlc_media_internal(source, source_type=source_type, reset_before_play=True)

    def _play_vlc_media_internal(self, source, source_type="media", reset_before_play=True):
        if not self.player or not self.instance:
            self.last_error = "VLC player is not initialized."
            logger.error(self.last_error)
            return False
        try:
            if reset_before_play:
                self.stop()
            else:
                self.player.stop()
            media = self.instance.media_new(source)
            self.player.set_media(media)
            result = self.player.play()
            time.sleep(0.5)
            self.player.set_fullscreen(True)
            try:
                self.player.video_set_mouse_input(False)
                self.player.video_set_key_input(False)
            except Exception:
                pass
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

    def play_playlist(self, items, source_type="playlist", loop_mode="list_loop", loop_count=0):
        with self._op_lock:
            normalized = [str(x).strip() for x in (items or []) if str(x).strip()]
            if not normalized:
                self.last_error = "Playlist is empty."
                return False

            self.stop()
            self.playlist_items = normalized
            self.playlist_index = 0
            self.playlist_mode = loop_mode or "list_loop"
            self.playlist_loop_count = max(0, int(loop_count or 0))
            self.playlist_round = 1
            self.playlist_backend = "vlc"
            self._playlist_stop_event.clear()

            first = self.playlist_items[0]
            use_electron = Config.ALL_PLAY_VIA_ELECTRON and len(self.playlist_items) == 1
            if use_electron:
                ok = self._open_via_electron(first, source_type=source_type)
                self.playlist_backend = "electron"
            else:
                if Config.ALL_PLAY_VIA_ELECTRON and len(self.playlist_items) > 1:
                    logger.info("Playlist rotation uses VLC to guarantee reliable next-track switching.")
                ok = self._play_vlc_media_internal(first, source_type=source_type, reset_before_play=False)
                self.playlist_backend = "vlc"
            if not ok:
                self.playlist_items = []
                return False

            self._playlist_thread = threading.Thread(target=self._playlist_worker, daemon=True)
            self._playlist_thread.start()
            logger.info(
                "Playlist started. size=%s mode=%s loop_count=%s",
                len(self.playlist_items),
                self.playlist_mode,
                self.playlist_loop_count,
            )
            return True

    def _playlist_worker(self):
        while not self._playlist_stop_event.is_set():
            time.sleep(1)
            with self._op_lock:
                if not self.playlist_items:
                    return

                if self.playlist_backend != "vlc":
                    continue

                if not self.player or vlc is None:
                    continue
                state = self.player.get_state()
                if state not in {vlc.State.Ended, vlc.State.Error}:
                    continue
                if not self._advance_playlist_locked():
                    return

    def _advance_playlist_locked(self):
        if not self.playlist_items:
            return False

        size = len(self.playlist_items)
        mode = self.playlist_mode

        if mode == "single":
            self.playlist_items = []
            self.expected_playing = False
            return False

        if mode == "single_loop":
            if self.playlist_loop_count > 0 and self.playlist_round >= self.playlist_loop_count:
                self.playlist_items = []
                self.expected_playing = False
                return False
            self.playlist_round += 1
            next_index = self.playlist_index
        else:
            # list_loop / once
            next_index = self.playlist_index + 1
            if next_index >= size:
                if mode == "once":
                    self.playlist_items = []
                    self.expected_playing = False
                    return False
                # list_loop
                if self.playlist_loop_count > 0 and self.playlist_round >= self.playlist_loop_count:
                    self.playlist_items = []
                    self.expected_playing = False
                    return False
                self.playlist_round += 1
                next_index = 0

        self.playlist_index = next_index
        source = self.playlist_items[self.playlist_index]
        return self._play_vlc_media_internal(source, source_type="playlist", reset_before_play=False)

    def _should_use_browser(self, url):
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        if path.endswith(STREAM_SUFFIXES):
            return False
        return url.startswith(("http://", "https://")) and any(h in url.lower() for h in WEBPAGE_HINTS)

    def _open_web_live_electron(self, url):
        self.stop()
        runtime_dir = os.path.join(Config.BASE_DIR, "runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        self.electron_port = self._reserve_port(Config.ELECTRON_CONTROL_HOST, Config.ELECTRON_CONTROL_PORT_BASE)
        self.electron_log_handle = open(
            os.path.join(runtime_dir, "electron_runner.log"),
            "a",
            encoding="utf-8",
        )

        electron_bin = self._resolve_electron_bin()
        logger.info("Resolved Electron binary: %s", electron_bin)
        if not electron_bin:
            self.last_error = (
                "Electron binary not found. "
                "Please install Electron globally or set YP_ELECTRON_BIN."
            )
            logger.error(self.last_error)
            return False
        if not self._validate_electron_bin(electron_bin):
            return False

        screen = self.current_screen or {"left": 0, "top": 0, "width": 1920, "height": 1080}
        runner_path = os.path.join(os.path.dirname(__file__), "electron_runner.js")
        command = [
            electron_bin,
            runner_path,
            "--",
            "--url",
            url,
            "--host",
            Config.ELECTRON_CONTROL_HOST,
            "--port",
            str(self.electron_port),
            "--left",
            str(screen["left"]),
            "--top",
            str(screen["top"]),
            "--width",
            str(screen["width"]),
            "--height",
            str(screen["height"]),
        ]
        if Config.WINDOW_TOPMOST:
            command.append("--topmost")

        try:
            self.electron_process = subprocess.Popen(
                command,
                stdout=self.electron_log_handle,
                stderr=self.electron_log_handle,
                env=self._build_electron_env(),
            )
            logger.info("Launching Electron command: %s", command)
            self.browser_command = command
            if not self._wait_electron_ready():
                exit_code = self.electron_process.poll() if self.electron_process else None
                logger.error("Electron did not become ready. process_exit=%s", exit_code)
                raise RuntimeError("Electron control endpoint not ready.")
            self.current_source = url
            self.current_backend = "electron"
            self.expected_playing = True
            self.last_started_at = time.time()
            self.last_error = ""
            logger.info("Web live opened via Electron: %s", url)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("Electron open failed: %s", exc)
            self._stop_electron_process()
            return False

    def _validate_electron_bin(self, electron_bin):
        try:
            result = subprocess.run(
                [electron_bin, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
                env=self._build_electron_env(),
            )
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or "").strip()
                self.last_error = f"Electron binary check failed: {msg}"
                logger.error(self.last_error)
                return False
            return True
        except Exception as exc:
            self.last_error = f"Electron binary not runnable: {exc}"
            logger.error(self.last_error)
            return False

    def _resolve_electron_bin(self):
        configured = (Config.ELECTRON_BIN or "").strip()

        # 1) Explicitly configured binary/path wins.
        if configured:
            if os.path.isabs(configured) and os.path.exists(configured):
                return configured
            configured_hit = which(configured)
            if configured_hit:
                return configured_hit

        # 2) Prefer project-local Electron.
        local_candidates = [
            os.path.join(Config.BASE_DIR, "node_modules", "electron", "dist", "electron.exe"),
            os.path.join(Config.BASE_DIR, "node_modules", ".bin", "electron"),
            os.path.join(Config.BASE_DIR, "node_modules", ".bin", "electron.cmd"),
        ]
        for candidate in local_candidates:
            if os.path.exists(candidate):
                return candidate

        # 3) Global PATH fallback.
        return which("electron")

    def _reserve_port(self, host, base):
        for port in range(base, base + 50):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
            finally:
                sock.close()
        raise RuntimeError(f"No available local port for host={host}, base={base}.")

    def _wait_electron_ready(self):
        start = time.time()
        while time.time() - start < Config.ELECTRON_STARTUP_WAIT:
            if not self.electron_process or self.electron_process.poll() is not None:
                return False
            if self._electron_request("GET", "/health", ignore_error=True):
                return True
            time.sleep(0.3)
        return False

    def _electron_request(self, method, path, ignore_error=False):
        if not self.electron_port:
            return False
        url = f"http://{Config.ELECTRON_CONTROL_HOST}:{self.electron_port}{path}"
        req = urllib.request.Request(url=url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=Config.ELECTRON_CONTROL_TIMEOUT) as resp:
                return 200 <= resp.getcode() < 300
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, ConnectionResetError):
            if not ignore_error:
                logger.warning("Electron request failed: %s %s", method, path)
            return False

    def inject_web_play(self):
        if self.current_backend != "electron":
            self.last_error = "Current backend is not Electron."
            return False
        ok = self._electron_request("POST", "/inject/play")
        self.last_error = "" if ok else "Electron play injection failed."
        return ok

    def inject_web_fullscreen(self):
        if self.current_backend != "electron":
            self.last_error = "Current backend is not Electron."
            return False
        ok = self._electron_request("POST", "/inject/fullscreen")
        self.last_error = "" if ok else "Electron fullscreen injection failed."
        return ok

    def ensure_foreground(self):  # pragma: no cover
        if self.current_backend == "electron":
            self._electron_request("POST", "/inject/fullscreen", ignore_error=True)

    def stop(self):
        with self._op_lock:
            try:
                self._playlist_stop_event.set()
                self.playlist_items = []
                self.playlist_index = 0
                self.playlist_round = 1
                self.playlist_backend = "vlc"
                if self.player:
                    self.player.stop()
                self.current_media = None
                if self.electron_process:
                    self._stop_electron_process()
                self.current_source = None
                self.current_backend = "idle"
                self.expected_playing = False
                logger.info("Playback stopped.")
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("Stop playback failed: %s", exc)
                return False

    def _stop_electron_process(self):
        try:
            self._electron_request("POST", "/stop", ignore_error=True)
        except Exception:
            pass
        try:
            if self.electron_process and self.electron_process.poll() is None:
                self.electron_process.terminate()
                self.electron_process.wait(timeout=3)
        except Exception:
            try:
                if self.electron_process and self.electron_process.poll() is None:
                    self.electron_process.kill()
            except Exception:
                pass
        finally:
            self.electron_process = None
            self.electron_port = None
            if self.electron_log_handle:
                try:
                    self.electron_log_handle.close()
                except Exception:
                    pass
                self.electron_log_handle = None

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
        if self.current_backend == "electron":
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

    def is_healthy(self):
        if not self.expected_playing:
            return True
        if self.current_backend == "electron":
            return self.electron_process is not None and self.electron_process.poll() is None
        if self.current_backend == "vlc" and self.player and vlc is not None:
            state = self.player.get_state()
            return state not in {vlc.State.Error, vlc.State.Ended}
        return False

    def get_status(self):
        state_label = "Idle"
        is_playing = False
        if self.current_backend == "electron":
            state_label = "ElectronPlayback"
            is_playing = self.electron_process is not None and self.electron_process.poll() is None
        elif self.player and vlc is not None:
            state = self.player.get_state()
            state_label = str(state)
            is_playing = state == vlc.State.Playing

        return {
            "state": state_label,
            "is_playing": is_playing,
            "backend": self.current_backend,
            "electron": bool(self.electron_process),
            "current_source": self.current_source,
            "screen_index": self.screen_index,
            "screen_name": self.current_screen["name"] if self.current_screen else f"Screen {self.screen_index}",
            "last_error": self.last_error,
            "last_started_at": self.last_started_at,
            "playlist_size": len(self.playlist_items),
            "playlist_index": self.playlist_index,
            "playlist_mode": self.playlist_mode,
            "playlist_loop_count": self.playlist_loop_count,
            "playlist_round": self.playlist_round,
            "playlist_backend": self.playlist_backend,
            "monitor_last_capture_at": self.monitor_last_capture_at,
        }

    def capture_monitor_snapshot(self):
        """捕获当前目标屏幕截图，保存为 runtime/monitor_latest.bmp"""
        if not win32gui or not win32ui or not win32con:
            self.last_error = "pywin32 screenshot modules unavailable."
            return False, self.last_error

        screen = self.current_screen or {"left": 0, "top": 0, "width": 1920, "height": 1080}
        left = int(screen.get("left", 0))
        top = int(screen.get("top", 0))
        width = int(screen.get("width", 0))
        height = int(screen.get("height", 0))
        if width <= 0 or height <= 0:
            return False, "Invalid monitor bounds."

        runtime_dir = os.path.join(Config.BASE_DIR, "runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        frames_dir = os.path.join(runtime_dir, "monitor_frames")
        os.makedirs(frames_dir, exist_ok=True)
        ts = int(time.time() * 1000)
        target_path = os.path.join(frames_dir, f"monitor_{ts}.bmp")

        hdesktop = None
        desktop_dc = None
        img_dc = None
        mem_dc = None
        screenshot = None
        try:
            hdesktop = win32gui.GetDesktopWindow()
            desktop_dc = win32gui.GetWindowDC(hdesktop)
            img_dc = win32ui.CreateDCFromHandle(desktop_dc)
            mem_dc = img_dc.CreateCompatibleDC()
            screenshot = win32ui.CreateBitmap()
            screenshot.CreateCompatibleBitmap(img_dc, width, height)
            mem_dc.SelectObject(screenshot)
            mem_dc.BitBlt((0, 0), (width, height), img_dc, (left, top), win32con.SRCCOPY)
            screenshot.SaveBitmapFile(mem_dc, target_path)
            self.monitor_last_capture_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.monitor_last_capture_path = target_path
            # 清理旧截图，避免磁盘累积；保留最近 20 张
            try:
                files = sorted(
                    [os.path.join(frames_dir, x) for x in os.listdir(frames_dir) if x.lower().endswith(".bmp")],
                    key=lambda p: os.path.getmtime(p),
                )
                for old in files[:-20]:
                    try:
                        os.remove(old)
                    except Exception:
                        pass
            except Exception:
                pass
            return True, target_path
        except Exception as exc:
            logger.warning("Capture monitor snapshot failed: %s", exc)
            return False, str(exc)
        finally:
            try:
                if screenshot:
                    win32gui.DeleteObject(screenshot.GetHandle())
            except Exception:
                pass
            try:
                if mem_dc:
                    mem_dc.DeleteDC()
            except Exception:
                pass
            try:
                if img_dc:
                    img_dc.DeleteDC()
            except Exception:
                pass
            try:
                if hdesktop and desktop_dc:
                    win32gui.ReleaseDC(hdesktop, desktop_dc)
            except Exception:
                pass
