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
    is_weekly = db.Column(db.Boolean, default=False, nullable=False)
    weekly_days = db.Column(db.String(20), default="", nullable=False)  # "0,1,2" => Mon,Tue,Wed

    def __repr__(self):
        return f"<Schedule {self.name}>"

    @property
    def weekly_day_set(self):
        if not self.weekly_days:
            return set()
        out = set()
        for token in self.weekly_days.split(","):
            token = token.strip()
            if token.isdigit():
                day = int(token)
                if 0 <= day <= 6:
                    out.add(day)
        return out

    def is_running_at(self, now):
        if not self.is_active:
            return False
        if self.is_weekly:
            day_set = self.weekly_day_set
            if not day_set:
                return False
            if now.weekday() not in day_set:
                return False
            now_minutes = now.hour * 60 + now.minute
            start_minutes = self.start_time.hour * 60 + self.start_time.minute
            end_minutes = self.end_time.hour * 60 + self.end_time.minute
            return start_minutes <= now_minutes < end_minutes
        return self.start_time <= now < self.end_time

    @property
    def is_running_now(self):
        return self.is_running_at(datetime.now())


class SystemSetting(db.Model):
    __tablename__ = "system_setting"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500), nullable=False, default="")

    def __repr__(self):
        return f"<SystemSetting {self.key}={self.value}>"
