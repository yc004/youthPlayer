# 数据库模型
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<User {self.username}>"

    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        if not self.password:
            return False
        if self.password.startswith(("pbkdf2:", "scrypt:")):
            return check_password_hash(self.password, raw_password)
        return self.password == raw_password

    @property
    def role_name(self):
        return "管理员" if self.is_admin else "普通用户"


class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    content_type = db.Column(db.String(20), nullable=False)  # local, nas, live
    content_path = db.Column(db.String(500), nullable=False)
    screen_index = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<Schedule {self.name}>"

    @property
    def is_running_now(self):
        now = datetime.now()
        return self.is_active and self.start_time <= now < self.end_time
