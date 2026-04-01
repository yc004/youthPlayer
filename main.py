# 主程序文件
import os
import sys
import logging
from flask import Flask
from flask_login import LoginManager
from apscheduler.schedulers.background import BackgroundScheduler

# 导入配置
from config import Config

# 初始化Flask应用
app = Flask(__name__)
app.config.from_object(Config)

# 初始化Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'main.login'

# 初始化定时任务调度器
scheduler = BackgroundScheduler()
scheduler.start()

# 配置日志
logging.basicConfig(
    level=getattr(logging, app.config['LOG_LEVEL']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(app.config['LOG_FILE']),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 导入模型和db实例
from models import db, User, Schedule

# 初始化数据库
db.init_app(app)

# 用户加载函数
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 导入播放器模块
from player.player import Player
player = Player()

# 导入控制模块
from controller.controller import Controller
controller = Controller(player, scheduler)

# 导入安全模块
from security.watchdog import Watchdog
watchdog = Watchdog(player, controller)

# 导入蓝图
from web.routes import main as main_blueprint, init_routes
app.register_blueprint(main_blueprint)

# 初始化路由模块
init_routes(app)

# 初始化数据库
def init_db():
    with app.app_context():
        db.create_all()
        # 创建默认管理员用户
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password='admin123', is_admin=True)
            db.session.add(admin)
            db.session.commit()

if __name__ == '__main__':
    # 初始化数据库
    init_db()
    
    # 启动看门狗
    watchdog.start()
    
    # 启动Web服务
    app.run(host='0.0.0.0', port=app.config['WEB_PORT'], debug=False)
    
    # 关闭调度器
    scheduler.shutdown()
    
    # 停止看门狗
    watchdog.stop()