# 主程序文件
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from flask_login import LoginManager

from config import Config
from models import User, db


app = Flask(__name__)
app.config.from_object(Config)


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


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "main.login"
login_manager.login_message = "请先登录后再访问管理界面。"


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


player = Player()
controller = Controller(app, player, scheduler)
watchdog = Watchdog(player, controller)

init_routes(player, controller, watchdog)
app.register_blueprint(main_blueprint)


def init_db():
    with app.app_context():
        db.create_all()
        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(username="admin", is_admin=True, is_active=True)
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
            logger.info("已创建默认管理员账户：admin / admin123")


def bootstrap():
    init_db()
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
