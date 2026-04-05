import logging
import os
import ctypes

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from flask_login import LoginManager

from config import Config
from models import DEFAULT_USER_PERMISSIONS, SystemSetting, User, db


app = Flask(__name__)
app.config.from_object(Config)


def enable_high_dpi_awareness():
    """避免截图/屏幕坐标在高缩放下只取到左上角区域。"""
    try:
        # Windows 8.1+ per-monitor aware
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        # Windows 7 fallback
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def configure_logging():
    os.makedirs(os.path.dirname(app.config["LOG_FILE"]) or ".", exist_ok=True)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    logging.basicConfig(
        level=getattr(logging, app.config["LOG_LEVEL"], logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(app.config["LOG_FILE"], encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


configure_logging()
logger = logging.getLogger(__name__)
enable_high_dpi_awareness()


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "main.login"
login_manager.login_message = "请先登录后再访问管理页面。"


scheduler = BackgroundScheduler()
scheduler.start()
db.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


from player.player import Player  # noqa: E402
from controller.controller import Controller  # noqa: E402
from security.watchdog import Watchdog  # noqa: E402
from web.routes import init_routes, main as main_blueprint  # noqa: E402
from web.routes import sync_nextcloud_cache_auto_clear_job  # noqa: E402


player = Player()
controller = Controller(app, player, scheduler)
watchdog = Watchdog(player, controller)

init_routes(player, controller, watchdog)
app.register_blueprint(main_blueprint)


def init_db():
    with app.app_context():
        db.create_all()
        # 轻量迁移：为旧版 SQLite 的 schedule 表补充周循环字段
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
            with db.engine.begin() as conn:
                columns = {
                    row[1]
                    for row in conn.exec_driver_sql("PRAGMA table_info(schedule)").fetchall()
                }
                if "is_weekly" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN is_weekly BOOLEAN NOT NULL DEFAULT 0"
                    )
                if "weekly_days" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN weekly_days VARCHAR(20) NOT NULL DEFAULT ''"
                    )
                if "playlist_paths" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN playlist_paths TEXT NOT NULL DEFAULT ''"
                    )
                if "loop_mode" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN loop_mode VARCHAR(20) NOT NULL DEFAULT 'single'"
                    )
                if "loop_count" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN loop_count INTEGER NOT NULL DEFAULT 0"
                    )
                if "window_mode" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN window_mode VARCHAR(20) NOT NULL DEFAULT 'fullscreen'"
                    )
                if "window_left" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN window_left INTEGER NOT NULL DEFAULT 0"
                    )
                if "window_top" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN window_top INTEGER NOT NULL DEFAULT 0"
                    )
                if "window_width" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN window_width INTEGER NOT NULL DEFAULT 0"
                    )
                if "window_height" not in columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE schedule ADD COLUMN window_height INTEGER NOT NULL DEFAULT 0"
                    )
                user_columns = {
                    row[1]
                    for row in conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()
                }
                if "permissions" not in user_columns:
                    conn.exec_driver_sql(
                        "ALTER TABLE user ADD COLUMN permissions TEXT NOT NULL DEFAULT ''"
                    )

        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", is_admin=True, is_active=True)
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
            logger.info("已创建默认管理员账户：admin / admin123")

        updated = False
        for user in User.query.filter_by(is_admin=False).all():
            if not user.permissions:
                user.set_permissions(DEFAULT_USER_PERMISSIONS)
                updated = True
        if updated:
            db.session.commit()


def load_runtime_settings():
    with app.app_context():
        item = db.session.get(SystemSetting, "all_play_via_electron")
        if item:
            Config.ALL_PLAY_VIA_ELECTRON = str(item.value).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            logger.info("Loaded setting all_play_via_electron=%s", Config.ALL_PLAY_VIA_ELECTRON)
        interval_item = db.session.get(SystemSetting, "monitor_capture_interval")
        if interval_item:
            try:
                Config.MONITOR_CAPTURE_INTERVAL = max(2, min(3600, int(str(interval_item.value).strip())))
            except Exception:
                pass
            logger.info("Loaded setting monitor_capture_interval=%ss", Config.MONITOR_CAPTURE_INTERVAL)
        screensaver_image_item = db.session.get(SystemSetting, "idle_screensaver_image")
        if screensaver_image_item:
            image_path = str(screensaver_image_item.value or "").strip()
            Config.IDLE_SCREENSAVER_IMAGE = image_path if image_path and os.path.exists(image_path) else ""
            logger.info("Loaded setting idle_screensaver_image=%s", Config.IDLE_SCREENSAVER_IMAGE or "<empty>")
        ss_screen_item = db.session.get(SystemSetting, "idle_screensaver_screen_index")
        if ss_screen_item:
            try:
                Config.IDLE_SCREENSAVER_SCREEN_INDEX = int(str(ss_screen_item.value).strip())
            except Exception:
                pass
            logger.info("Loaded setting idle_screensaver_screen_index=%s", Config.IDLE_SCREENSAVER_SCREEN_INDEX)
        ss_mode_item = db.session.get(SystemSetting, "idle_screensaver_window_mode")
        if ss_mode_item:
            mode = str(ss_mode_item.value or "").strip().lower()
            if mode in {"fullscreen", "custom"}:
                Config.IDLE_SCREENSAVER_WINDOW_MODE = mode
            logger.info("Loaded setting idle_screensaver_window_mode=%s", Config.IDLE_SCREENSAVER_WINDOW_MODE)
        for key, attr in [
            ("idle_screensaver_window_left", "IDLE_SCREENSAVER_WINDOW_LEFT"),
            ("idle_screensaver_window_top", "IDLE_SCREENSAVER_WINDOW_TOP"),
            ("idle_screensaver_window_width", "IDLE_SCREENSAVER_WINDOW_WIDTH"),
            ("idle_screensaver_window_height", "IDLE_SCREENSAVER_WINDOW_HEIGHT"),
        ]:
            item = db.session.get(SystemSetting, key)
            if not item:
                continue
            try:
                setattr(Config, attr, int(str(item.value).strip()))
            except Exception:
                continue
            logger.info("Loaded setting %s=%s", key, getattr(Config, attr))
        nc_enabled_item = db.session.get(SystemSetting, "nextcloud_enabled")
        nc_url_item = db.session.get(SystemSetting, "nextcloud_url")
        nc_user_item = db.session.get(SystemSetting, "nextcloud_username")
        nc_pass_item = db.session.get(SystemSetting, "nextcloud_password")
        nc_root_item = db.session.get(SystemSetting, "nextcloud_root")
        nc_skip_ssl_item = db.session.get(SystemSetting, "nextcloud_skip_ssl_verify")
        Config.NEXTCLOUD_ENABLED = str(
            nc_enabled_item.value if nc_enabled_item else Config.NEXTCLOUD_ENABLED
        ).strip().lower() in {"1", "true", "yes", "on"}
        Config.NEXTCLOUD_URL = str(nc_url_item.value if nc_url_item else Config.NEXTCLOUD_URL).strip()
        Config.NEXTCLOUD_USERNAME = str(nc_user_item.value if nc_user_item else Config.NEXTCLOUD_USERNAME).strip()
        Config.NEXTCLOUD_PASSWORD = str(nc_pass_item.value if nc_pass_item else Config.NEXTCLOUD_PASSWORD).strip()
        Config.NEXTCLOUD_ROOT = str(nc_root_item.value if nc_root_item else Config.NEXTCLOUD_ROOT).strip() or "/"
        Config.NEXTCLOUD_SKIP_SSL_VERIFY = str(
            nc_skip_ssl_item.value if nc_skip_ssl_item else Config.NEXTCLOUD_SKIP_SSL_VERIFY
        ).strip().lower() in {"1", "true", "yes", "on"}
        nc_cache_auto_clear_enabled_item = db.session.get(SystemSetting, "nextcloud_cache_auto_clear_enabled")
        nc_cache_auto_clear_time_item = db.session.get(SystemSetting, "nextcloud_cache_auto_clear_time")
        Config.NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED = str(
            (
                nc_cache_auto_clear_enabled_item.value
                if nc_cache_auto_clear_enabled_item
                else Config.NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED
            )
        ).strip().lower() in {"1", "true", "yes", "on"}
        Config.NEXTCLOUD_CACHE_AUTO_CLEAR_TIME = str(
            nc_cache_auto_clear_time_item.value
            if nc_cache_auto_clear_time_item
            else Config.NEXTCLOUD_CACHE_AUTO_CLEAR_TIME
        ).strip() or "03:00"


def setup_monitor_capture_job():
    if not Config.MONITOR_CAPTURE_ENABLED:
        return

    interval = max(2, int(Config.MONITOR_CAPTURE_INTERVAL or 5))

    def _capture_job():
        player.capture_monitor_snapshot()

    scheduler.add_job(
        _capture_job,
        trigger="interval",
        seconds=interval,
        id="monitor_capture_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Monitor capture job enabled, interval=%ss", interval)
    try:
        player.capture_monitor_snapshot()
    except Exception:
        pass


def bootstrap():
    init_db()
    load_runtime_settings()
    setup_monitor_capture_job()
    sync_nextcloud_cache_auto_clear_job(scheduler)
    controller.refresh_schedules()
    controller.sync_active_schedule(force_restart=False)


if __name__ == "__main__":
    bootstrap()
    watchdog.start()
    try:
        app.run(host="0.0.0.0", port=app.config["WEB_PORT"], debug=False)
    finally:
        scheduler.shutdown(wait=False)
        watchdog.stop()
