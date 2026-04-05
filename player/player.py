"""Playback engine: VLC for media streams, Electron for web live pages."""

import logging
import os
import socket
import subprocess
import threading
import time
import base64
import json
import hashlib
import shutil
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit, unquote
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
        self.window_mode = "fullscreen"
        self.window_bounds = {"left": 0, "top": 0, "width": 0, "height": 0}

        self.electron_process = None
        self.electron_port = None
        self.electron_log_handle = None
        self.browser_command = []
        self.electron_window_signature = None

        self.last_error = ""
        self.last_started_at = None
        self.expected_playing = False
        self._op_lock = threading.RLock()
        self.playlist_items = []
        self.playlist_index = 0
        self.playlist_mode = "single"
        self.playlist_loop_count = 0  # 0=infinite
        self.playlist_round = 1
        self.playlist_backend = "vlc"
        self.playlist_source_type = "playlist"
        self.playlist_native_repeat = None
        self.playlist_play_counts = []
        self.playlist_current_item = None
        self._playlist_stop_event = threading.Event()
        self._playlist_thread = None
        self.monitor_last_capture_at = None
        self.monitor_last_capture_path = ""
        self.screensaver_url = Path(os.path.join(os.path.dirname(__file__), "screensaver.html")).resolve().as_uri()
        self.media_shell_url = Path(os.path.join(os.path.dirname(__file__), "media_player.html")).resolve().as_uri()

        self._init_vlc()
        self.set_screen(Config.PRIMARY_SCREEN)

    def _build_electron_env(self):
        # Ensure Electron starts as desktop app, not in NODE mode.
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
                "--video-on-top",
                "--mouse-hide-timeout=100",
                "--no-qt-fs-controller",
                "--qt-minimal-view",
                "--no-video-deco",
                "--no-qt-name-in-title",
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

    def set_window_rect(self, mode="fullscreen", left=0, top=0, width=0, height=0):
        mode = (mode or "fullscreen").strip().lower()
        if mode not in {"fullscreen", "custom"}:
            mode = "fullscreen"
        self.window_mode = mode
        self.window_bounds = {
            "left": int(left or 0),
            "top": int(top or 0),
            "width": max(0, int(width or 0)),
            "height": max(0, int(height or 0)),
        }
        return True

    def _resolve_window_rect(self):
        screen = self.current_screen or {"left": 0, "top": 0, "width": 1920, "height": 1080}
        if self.window_mode != "custom":
            return "fullscreen", {
                "left": int(screen.get("left", 0)),
                "top": int(screen.get("top", 0)),
                "width": max(1, int(screen.get("width", 1920))),
                "height": max(1, int(screen.get("height", 1080))),
            }

        left = int(self.window_bounds.get("left", 0))
        top = int(self.window_bounds.get("top", 0))
        width = max(100, int(self.window_bounds.get("width", 0) or 0))
        height = max(100, int(self.window_bounds.get("height", 0) or 0))
        return "custom", {"left": left, "top": top, "width": width, "height": height}

    def play_local(self, file_path):
        with self._op_lock:
            if not os.path.exists(file_path):
                self.last_error = f"File not found: {file_path}"
                logger.error(self.last_error)
                return False
            return self._open_via_electron(file_path, source_type="local")

    def play_nas(self, nas_path):
        with self._op_lock:
            return self._open_via_electron(nas_path, source_type="nas")

    def play_live(self, live_url):
        with self._op_lock:
            return self._open_via_electron(live_url, source_type="live")

    def show_screensaver(self):
        with self._op_lock:
            if not Config.IDLE_SCREENSAVER_ENABLED:
                return False
            screensaver_mode = (Config.IDLE_SCREENSAVER_WINDOW_MODE or "fullscreen").strip().lower()
            if screensaver_mode not in {"fullscreen", "custom"}:
                screensaver_mode = "fullscreen"
            self.set_screen(Config.IDLE_SCREENSAVER_SCREEN_INDEX)
            self.set_window_rect(
                mode=screensaver_mode,
                left=Config.IDLE_SCREENSAVER_WINDOW_LEFT,
                top=Config.IDLE_SCREENSAVER_WINDOW_TOP,
                width=Config.IDLE_SCREENSAVER_WINDOW_WIDTH,
                height=Config.IDLE_SCREENSAVER_WINDOW_HEIGHT,
            )
            mode, bounds = self._resolve_window_rect()
            target_signature = (
                int(self.screen_index),
                str(mode),
                int(bounds["left"]),
                int(bounds["top"]),
                int(bounds["width"]),
                int(bounds["height"]),
                bool(Config.WINDOW_TOPMOST),
            )
            payload = {"title": Config.IDLE_SCREENSAVER_TITLE or "Campus Player"}
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
                and self.electron_window_signature == target_signature
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

    def _is_nextcloud_source(self, source):
        text = str(source or "").strip().lower()
        if not text.startswith(("http://", "https://")):
            return False
        if "/remote.php/dav/" in text:
            return True
        base = str(getattr(Config, "NEXTCLOUD_URL", "") or "").strip().lower()
        if not base:
            return False
        try:
            source_host = (urlsplit(text).hostname or "").lower()
            base_host = (urlsplit(base).hostname or "").lower()
            return bool(source_host and base_host and source_host == base_host)
        except Exception:
            return False

    def _cache_nextcloud_source(self, source):
        text = str(source or "").strip()
        if not self._is_nextcloud_source(text):
            return text

        split = urlsplit(text)
        source_url = text
        username = unquote(split.username or "") if split.username else ""
        password = unquote(split.password or "") if split.password else ""
        if split.username or split.password:
            host = split.hostname or ""
            port = f":{split.port}" if split.port else ""
            netloc = f"{host}{port}"
            source_url = urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))
        else:
            username = str(getattr(Config, "NEXTCLOUD_USERNAME", "") or "").strip()
            password = str(getattr(Config, "NEXTCLOUD_PASSWORD", "") or "").strip()

        if not username or not password:
            self.last_error = "Nextcloud auth missing for cache download."
            logger.error(self.last_error)
            return None

        runtime_dir = os.path.join(Config.BASE_DIR, "runtime", "nextcloud_cache")
        os.makedirs(runtime_dir, exist_ok=True)
        ext = os.path.splitext((urlsplit(source_url).path or ""))[1].lower()
        if not ext:
            ext = ".mp4"
        key = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
        target = os.path.join(runtime_dir, f"{key}{ext}")
        if os.path.exists(target) and os.path.getsize(target) > 0:
            return target

        tmp = target + ".part"
        req = urllib.request.Request(source_url, method="GET")
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("User-Agent", "youthPlayer/nextcloud-cache")
        ctx = ssl._create_unverified_context() if getattr(Config, "NEXTCLOUD_SKIP_SSL_VERIFY", False) else None
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp, open(tmp, "wb") as f:
                content_type = str(resp.headers.get("Content-Type", "") or "").lower()
                if "text/html" in content_type or "application/json" in content_type:
                    raise RuntimeError(f"unexpected content-type: {content_type}")
                shutil.copyfileobj(resp, f)
            if not os.path.exists(tmp) or os.path.getsize(tmp) <= 0:
                raise RuntimeError("empty download")
            os.replace(tmp, target)
            logger.info("Nextcloud cached: %s -> %s", source_url, target)
            return target
        except Exception as exc:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            self.last_error = f"Nextcloud cache download failed: {exc}"
            logger.error(self.last_error)
            return None

    def _open_via_electron(self, source, source_type="media", loop=False, reset_before_open=False):
        source = self._cache_nextcloud_source(source)
        if source is None:
            return False
        target_url = self._to_electron_url(source)
        if self._should_use_media_shell(source_type, target_url):
            target_url = self._build_media_shell_url(target_url, loop=loop)
        logger.info("Using Electron for %s source: %s -> %s", source_type, source, target_url)
        return self._open_web_live_electron(
            target_url,
            loop=loop,
            reset_before_open=reset_before_open,
        )

    def _should_use_media_shell(self, source_type, target_url):
        parsed = urlparse(str(target_url))
        scheme = (parsed.scheme or "").lower()
        path = (parsed.path or "").lower()
        whole = str(target_url or "").lower()
        if source_type in {"local", "nas"}:
            return True
        if "/remote.php/dav/" in whole:
            return True
        if scheme in {"file"}:
            return True
        if path.endswith(STREAM_SUFFIXES):
            return True
        return False

    def _build_media_shell_url(self, media_url, loop=False):
        payload_text = json.dumps(
            {"src": str(media_url), "loop": bool(loop)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload_b64 = base64.urlsafe_b64encode(payload_text.encode("utf-8")).decode("ascii").rstrip("=")
        return f"{self.media_shell_url}?cfg={payload_b64}"

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
            mode, bounds = self._resolve_window_rect()
            if mode == "custom":
                media.add_option(":no-video-deco")
                media.add_option(":qt-minimal-view")
                media.add_option(":no-qt-fs-controller")
                media.add_option(f":video-x={int(bounds['left'])}")
                media.add_option(f":video-y={int(bounds['top'])}")
                media.add_option(f":width={int(bounds['width'])}")
                media.add_option(f":height={int(bounds['height'])}")
            else:
                # Explicitly pin fullscreen to the configured monitor on multi-screen setups.
                media.add_option(f":qt-fullscreen-screennumber={int(self.screen_index)}")
                # Keep geometry hints for VLC versions/drivers that use pre-fullscreen window position.
                media.add_option(f":video-x={int(bounds['left'])}")
                media.add_option(f":video-y={int(bounds['top'])}")
                media.add_option(f":width={int(bounds['width'])}")
                media.add_option(f":height={int(bounds['height'])}")
                media.add_option(":fullscreen")
            if (
                source_type == "playlist"
                and self.playlist_backend == "vlc"
                and self.playlist_native_repeat is not None
            ):
                media.add_option(f"input-repeat={int(self.playlist_native_repeat)}")
            self.player.set_media(media)
            result = self.player.play()
            time.sleep(0.5)
            if mode == "custom":
                self.player.set_fullscreen(False)
            else:
                # 閺屾劒绨?VLC/閺勬儳宕辩紒鍕値缁楃濞喡ょ殶閻劋绗夐悽鐔告櫏閿涘苯浠涢柌宥堢槸绾箽閸掑洤鍩岄崗銊ョ潌
                for _ in range(6):
                    self.player.set_fullscreen(True)
                    time.sleep(0.15)
                    try:
                        if bool(self.player.get_fullscreen()):
                            break
                    except Exception:
                        pass
            if mode == "custom":
                logger.info(
                    "Apply VLC custom window rect: left=%s top=%s width=%s height=%s",
                    bounds["left"],
                    bounds["top"],
                    bounds["width"],
                    bounds["height"],
                )
            else:
                logger.info("Apply VLC fullscreen mode.")
                logger.info(
                    "VLC fullscreen target screen_index=%s screen=%s bounds=%s",
                    self.screen_index,
                    (self.current_screen or {}).get("name"),
                    bounds,
                )
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
            self.playlist_backend = "electron"
            self.playlist_source_type = source_type or "playlist"
            self.playlist_native_repeat = None
            self.playlist_play_counts = [0 for _ in self.playlist_items]
            self.playlist_current_item = self.playlist_items[0] if self.playlist_items else None
            self._playlist_stop_event.clear()

            first = self.playlist_items[0]
            ok = self._open_via_electron(
                first,
                source_type=source_type,
                loop=False,
                reset_before_open=False,
            )
            if not ok:
                self.playlist_items = []
                self.playlist_play_counts = []
                self.playlist_current_item = None
                return False
            self.playlist_play_counts[0] += 1

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

                if self.playlist_backend == "electron":
                    if not self._electron_media_finished():
                        continue
                    if not self._advance_playlist_locked():
                        return
                    continue

                if self.playlist_backend != "vlc":
                    continue

                if not self.player or vlc is None:
                    continue
                state = self.player.get_state()
                if state not in {vlc.State.Ended, vlc.State.Error, vlc.State.Stopped}:
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
        self.playlist_current_item = source
        if self.playlist_backend == "electron":
            ok = self._open_via_electron(
                source,
                source_type=self.playlist_source_type or "playlist",
                loop=False,
                reset_before_open=False,
            )
            if ok and self.playlist_index < len(self.playlist_play_counts):
                self.playlist_play_counts[self.playlist_index] += 1
            return ok
        ok = self._play_vlc_media_internal(source, source_type="playlist", reset_before_play=False)
        if ok and self.playlist_index < len(self.playlist_play_counts):
            self.playlist_play_counts[self.playlist_index] += 1
        return ok

    def _should_use_browser(self, url):
        parsed = urlparse(url)
        path = (parsed.path or "").lower()
        if path.endswith(STREAM_SUFFIXES):
            return False
        return url.startswith(("http://", "https://")) and any(h in url.lower() for h in WEBPAGE_HINTS)

    def _open_web_live_electron(self, url, loop=False, reset_before_open=True):
        mode, bounds = self._resolve_window_rect()
        ignore_cert_errors = bool(
            getattr(Config, "NEXTCLOUD_SKIP_SSL_VERIFY", False) and str(url or "").lower().startswith("https://")
        )
        target_signature = (
            int(self.screen_index),
            str(mode),
            int(bounds["left"]),
            int(bounds["top"]),
            int(bounds["width"]),
            int(bounds["height"]),
            bool(Config.WINDOW_TOPMOST),
            bool(ignore_cert_errors),
        )
        can_reuse_window = (
            not reset_before_open
            and self.electron_process
            and self.electron_process.poll() is None
            and self.electron_port
            and self.electron_window_signature == target_signature
        )
        if can_reuse_window:
            out = self._electron_request_json(
                "POST",
                "/navigate",
                payload={"url": url, "loop": bool(loop)},
                ignore_error=True,
            ) or {}
            if out.get("ok"):
                self.current_source = url
                self.current_backend = "electron"
                self.expected_playing = True
                self.last_started_at = time.time()
                self.last_error = ""
                return True

        if reset_before_open:
            self.stop()
        else:
            # Keep playlist state when switching items; only restart backend process/window.
            self._stop_active_backend_only()
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
            "--screen-index",
            str(self.screen_index),
            "--screen-left",
            str((self.current_screen or {}).get("left", 0)),
            "--screen-top",
            str((self.current_screen or {}).get("top", 0)),
            "--window-mode",
            mode,
            "--left",
            str(bounds["left"]),
            "--top",
            str(bounds["top"]),
            "--width",
            str(bounds["width"]),
            "--height",
            str(bounds["height"]),
        ]
        if Config.WINDOW_TOPMOST:
            command.append("--topmost")
        if loop:
            command.append("--loop")
        if ignore_cert_errors:
            command.append("--ignore-certificate-errors")

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
            self.electron_window_signature = target_signature
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
        is_windows = os.name == "nt"

        def _is_windows_executable(path):
            lower = str(path or "").lower()
            return lower.endswith(".exe") or lower.endswith(".cmd") or lower.endswith(".bat")

        # 1) Prefer project-local Electron first.
        if is_windows:
            local_candidates = [
                os.path.join(Config.BASE_DIR, "node_modules", "electron", "dist", "electron.exe"),
                os.path.join(Config.BASE_DIR, "node_modules", ".bin", "electron.cmd"),
                os.path.join(Config.BASE_DIR, "node_modules", ".bin", "electron.bat"),
            ]
        else:
            local_candidates = [
                os.path.join(Config.BASE_DIR, "node_modules", "electron", "dist", "electron"),
                os.path.join(Config.BASE_DIR, "node_modules", ".bin", "electron"),
        ]
        for candidate in local_candidates:
            if os.path.exists(candidate):
                return candidate

        # 2) Then honor configured binary/path.
        if configured:
            if os.path.isabs(configured) and os.path.exists(configured):
                if not is_windows or _is_windows_executable(configured):
                    return configured
            configured_hit = which(configured)
            if configured_hit and (not is_windows or _is_windows_executable(configured_hit)):
                return configured_hit

        # 3) Global PATH fallback last.
        if is_windows:
            return which("electron.cmd") or which("electron.exe") or which("electron")
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

    def _electron_request_json(self, method, path, payload=None, ignore_error=False):
        if not self.electron_port:
            return None
        url = f"http://{Config.ELECTRON_CONTROL_HOST}:{self.electron_port}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=Config.ELECTRON_CONTROL_TIMEOUT) as resp:
                if resp.getcode() < 200 or resp.getcode() >= 300:
                    return None
                payload = resp.read()
                if not payload:
                    return None
                return json.loads(payload.decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, ConnectionResetError, json.JSONDecodeError):
            if not ignore_error:
                logger.warning("Electron JSON request failed: %s %s", method, path)
            return None

    def _electron_media_finished(self):
        if not self.electron_process:
            return True
        if self.electron_process.poll() is not None:
            return True
        status = self._electron_request_json("GET", "/probe/media_status", ignore_error=True) or {}
        if not status.get("ok"):
            return False
        media = status.get("status") or {}
        videos = int(media.get("videos", 0) or 0)
        any_playing = bool(media.get("any_playing", False))
        all_ended = bool(media.get("all_ended", False))
        if videos <= 0:
            return False
        return all_ended and not any_playing

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
            if self.window_mode == "custom":
                self._electron_request("POST", "/focus", ignore_error=True)
            else:
                self._electron_request("POST", "/inject/fullscreen", ignore_error=True)

    def stop(self):
        with self._op_lock:
            try:
                self._playlist_stop_event.set()
                self.playlist_items = []
                self.playlist_index = 0
                self.playlist_round = 1
                self.playlist_backend = "vlc"
                self.playlist_source_type = "playlist"
                self.playlist_native_repeat = None
                self.playlist_play_counts = []
                self.playlist_current_item = None
                self._stop_active_backend_only()
                logger.info("Playback stopped.")
                return True
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("Stop playback failed: %s", exc)
                return False

    def _stop_active_backend_only(self):
        if self.player:
            self.player.stop()
        self.current_media = None
        if self.electron_process:
            self._stop_electron_process()
        self.current_source = None
        self.current_backend = "idle"
        self.expected_playing = False

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
            self.electron_window_signature = None
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
            "window_mode": self.window_mode,
            "window_bounds": self.window_bounds,
            "last_error": self.last_error,
            "last_started_at": self.last_started_at,
            "playlist_size": len(self.playlist_items),
            "playlist_index": self.playlist_index,
            "playlist_mode": self.playlist_mode,
            "playlist_loop_count": self.playlist_loop_count,
            "playlist_round": self.playlist_round,
            "playlist_backend": self.playlist_backend,
            "playlist_current_item": self.playlist_current_item,
            "playlist_play_counts": list(self.playlist_play_counts),
            "monitor_last_capture_at": self.monitor_last_capture_at,
        }

    def capture_monitor_snapshot(self):
        """Capture current target monitor screenshot to runtime/monitor_latest.bmp."""
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
            # 濞撳懐鎮婇弮褎鍩呴崶鎾呯礉闁灝鍘ょ壕浣烘磸缁毙濋敍娑楃箽閻ｆ瑦娓舵潻?20 瀵?
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


