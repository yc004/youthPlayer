# 系统配置文件

class Config:
    # 基础配置
    SECRET_KEY = 'your-secret-key-here'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///playback_system.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # 播放器配置
    VLC_PATH = 'C:\\Program Files\\VideoLAN\\VLC\\vlc.exe'  # VLC播放器路径
    
    # 屏幕配置
    PRIMARY_SCREEN = 0  # 主屏幕索引
    SECONDARY_SCREEN = 1  # 副屏幕索引
    
    # 安全配置
    WATCHDOG_INTERVAL = 30  # 看门狗检查间隔（秒）
    WINDOW_PRIORITY = 'high'  # 播放窗口优先级
    
    # Web配置
    WEB_PORT = 5000  # Web服务端口
    
    # 日志配置
    LOG_LEVEL = 'INFO'
    LOG_FILE = 'playback_system.log'