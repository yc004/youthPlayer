import os


class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    # 基础配置
    SECRET_KEY = os.environ.get("YP_SECRET_KEY", "campus-tv-secret-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "YP_DATABASE_URI",
        f"sqlite:///{os.path.join(BASE_DIR, 'playback_system.db')}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # 播放配置
    VLC_PATH = os.environ.get("YP_VLC_PATH", r"C:\Program Files\VideoLAN\VLC\vlc.exe")
    WEB_USE_ELECTRON = os.environ.get("YP_WEB_USE_ELECTRON", "1") == "1"
    WEB_ELECTRON_ONLY = os.environ.get("YP_WEB_ELECTRON_ONLY", "1") == "1"
    # 设为 1 时，local/nas/live 全部优先走 Electron，避免 VLC/Electron 来回切换。
    ALL_PLAY_VIA_ELECTRON = os.environ.get("YP_ALL_PLAY_VIA_ELECTRON", "0") == "1"
    # 留空时优先使用项目内 node_modules/.bin/electron(.cmd)
    ELECTRON_BIN = os.environ.get("YP_ELECTRON_BIN", "electron.cmd")
    ELECTRON_CONTROL_HOST = os.environ.get("YP_ELECTRON_HOST", "127.0.0.1")
    ELECTRON_CONTROL_PORT_BASE = int(os.environ.get("YP_ELECTRON_PORT_BASE", 18870))
    ELECTRON_CONTROL_TIMEOUT = float(os.environ.get("YP_ELECTRON_TIMEOUT", 2.0))
    ELECTRON_STARTUP_WAIT = float(os.environ.get("YP_ELECTRON_STARTUP_WAIT", 20.0))

    # 屏幕配置
    PRIMARY_SCREEN = 0
    SECONDARY_SCREEN = 1
    SCREEN_FALLBACKS = [
        {"index": 0, "name": "主屏幕", "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"index": 1, "name": "副屏幕", "left": 1920, "top": 0, "width": 1920, "height": 1080},
    ]

    # 安全配置
    WATCHDOG_INTERVAL = 15
    WATCHDOG_RECOVERY_COOLDOWN = 20
    WINDOW_TOPMOST = True
    WINDOW_RECOVERY_ENABLED = True

    # 监控截图
    MONITOR_CAPTURE_ENABLED = os.environ.get("YP_MONITOR_CAPTURE_ENABLED", "1") == "1"
    MONITOR_CAPTURE_INTERVAL = int(os.environ.get("YP_MONITOR_CAPTURE_INTERVAL", 5))
    MONITOR_CAPTURE_ONLY_WHEN_PLAYING = os.environ.get("YP_MONITOR_CAPTURE_ONLY_PLAYING", "1") == "1"
    IDLE_SCREENSAVER_ENABLED = os.environ.get("YP_IDLE_SCREENSAVER_ENABLED", "1") == "1"
    IDLE_SCREENSAVER_TITLE = os.environ.get("YP_IDLE_SCREENSAVER_TITLE", "校园电视播放系统")
    IDLE_SCREENSAVER_IMAGE = os.environ.get("YP_IDLE_SCREENSAVER_IMAGE", "").strip()
    IDLE_SCREENSAVER_SCREEN_INDEX = int(os.environ.get("YP_IDLE_SCREENSAVER_SCREEN_INDEX", str(PRIMARY_SCREEN)))
    IDLE_SCREENSAVER_WINDOW_MODE = os.environ.get("YP_IDLE_SCREENSAVER_WINDOW_MODE", "fullscreen").strip().lower()
    IDLE_SCREENSAVER_WINDOW_LEFT = int(os.environ.get("YP_IDLE_SCREENSAVER_WINDOW_LEFT", 0))
    IDLE_SCREENSAVER_WINDOW_TOP = int(os.environ.get("YP_IDLE_SCREENSAVER_WINDOW_TOP", 0))
    IDLE_SCREENSAVER_WINDOW_WIDTH = int(os.environ.get("YP_IDLE_SCREENSAVER_WINDOW_WIDTH", 1280))
    IDLE_SCREENSAVER_WINDOW_HEIGHT = int(os.environ.get("YP_IDLE_SCREENSAVER_WINDOW_HEIGHT", 720))

    # Web 配置
    WEB_PORT = int(os.environ.get("YP_WEB_PORT", 5000))

    # 日志配置
    LOG_LEVEL = os.environ.get("YP_LOG_LEVEL", "INFO")
    LOG_FILE = os.environ.get("YP_LOG_FILE", os.path.join(BASE_DIR, "playback_system.log"))
