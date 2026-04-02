# 系统配置文件
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

    # 播放器配置
    VLC_PATH = os.environ.get("YP_VLC_PATH", r"C:\Program Files\VideoLAN\VLC\vlc.exe")
    WEB_LIVE_BROWSER_PATH = os.environ.get("YP_BROWSER_PATH", "")
    WEB_LIVE_BROWSER_PROFILE = os.environ.get(
        "YP_BROWSER_PROFILE",
        os.path.join(BASE_DIR, "runtime", "browser-profile"),
    )
    WEB_LIVE_BROWSER_ARGS = [
        "--new-window",
        "--start-fullscreen",
        "--disable-notifications",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--autoplay-policy=no-user-gesture-required",
    ]
    WEB_LIVE_SCRIPT_INJECTION = os.environ.get("YP_WEB_INJECT", "1") == "1"
    WEB_LIVE_DRIVER_BROWSER = os.environ.get("YP_WEB_DRIVER_BROWSER", "edge")
    WEB_LIVE_DRIVER_PATH = os.environ.get("YP_WEB_DRIVER_PATH", "")
    WEB_LIVE_SCRIPT_RETRY = int(os.environ.get("YP_WEB_SCRIPT_RETRY", 3))
    WEB_LIVE_SCRIPT_RETRY_INTERVAL = float(os.environ.get("YP_WEB_SCRIPT_RETRY_INTERVAL", 1.2))

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

    # Web 配置
    WEB_PORT = int(os.environ.get("YP_WEB_PORT", 5000))

    # 日志配置
    LOG_LEVEL = os.environ.get("YP_LOG_LEVEL", "INFO")
    LOG_FILE = os.environ.get("YP_LOG_FILE", os.path.join(BASE_DIR, "playback_system.log"))
