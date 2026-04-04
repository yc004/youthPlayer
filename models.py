# 数据库模型
import json
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


ALL_PERMISSIONS = (
    "dashboard.view",
    "playback.control",
    "schedule.view",
    "schedule.manage",
    "monitor.view",
    "monitor.capture",
    "users.manage",
    "settings.manage",
    "files.browse",
)

DEFAULT_USER_PERMISSIONS = (
    "dashboard.view",
    "playback.control",
    "schedule.view",
    "schedule.manage",
    "monitor.view",
    "monitor.capture",
    "files.browse",
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    permissions = db.Column(db.Text, nullable=False, default="")

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

    @property
    def permission_set(self):
        if self.is_admin:
            return set(ALL_PERMISSIONS)
        raw = (self.permissions or "").strip()
        if not raw:
            return set()
        try:
            data = json.loads(raw)
        except Exception:
            data = [item.strip() for item in raw.split(",") if item.strip()]
        if not isinstance(data, list):
            return set()
        return {str(item).strip() for item in data if str(item).strip() in ALL_PERMISSIONS}

    def set_permissions(self, permissions):
        clean = sorted({str(item).strip() for item in (permissions or []) if str(item).strip() in ALL_PERMISSIONS})
        self.permissions = json.dumps(clean, ensure_ascii=False)

    def has_permission(self, permission_code):
        if self.is_admin:
            return True
        return permission_code in self.permission_set


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
    playlist_paths = db.Column(db.Text, default="", nullable=False)  # 多视频，一行一个路径/URL
    loop_mode = db.Column(db.String(20), default="single", nullable=False)  # single/list_loop/single_loop/once
    loop_count = db.Column(db.Integer, default=0, nullable=False)  # 0=无限循环
    window_mode = db.Column(db.String(20), default="fullscreen", nullable=False)  # fullscreen/custom
    window_left = db.Column(db.Integer, default=0, nullable=False)
    window_top = db.Column(db.Integer, default=0, nullable=False)
    window_width = db.Column(db.Integer, default=0, nullable=False)
    window_height = db.Column(db.Integer, default=0, nullable=False)

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

    @property
    def playlist_items(self):
        if not self.playlist_paths:
            return []
        return [line.strip() for line in self.playlist_paths.splitlines() if line.strip()]


class SystemSetting(db.Model):
    __tablename__ = "system_setting"

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.String(500), nullable=False, default="")

    def __repr__(self):
        return f"<SystemSetting {self.key}={self.value}>"


class SettingAuditLog(db.Model):
    __tablename__ = "setting_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), nullable=False, index=True)
    old_value = db.Column(db.String(500), nullable=True)
    new_value = db.Column(db.String(500), nullable=True)
    operator_user_id = db.Column(db.Integer, nullable=True, index=True)
    operator_username = db.Column(db.String(50), nullable=False, default="")
    remote_addr = db.Column(db.String(100), nullable=False, default="")
    user_agent = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)

    def __repr__(self):
        return f"<SettingAuditLog {self.setting_key} {self.old_value}->{self.new_value}>"


class OperationAuditLog(db.Model):
    __tablename__ = "operation_audit_log"

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(100), nullable=False, index=True)  # playback_start / playback_stop / schedule_delete
    target_type = db.Column(db.String(50), nullable=False, default="system")  # schedule/player/system
    target_id = db.Column(db.String(100), nullable=False, default="")
    success = db.Column(db.Boolean, nullable=False, default=True)
    detail = db.Column(db.String(500), nullable=False, default="")
    operator_user_id = db.Column(db.Integer, nullable=True, index=True)
    operator_username = db.Column(db.String(50), nullable=False, default="")
    remote_addr = db.Column(db.String(100), nullable=False, default="")
    user_agent = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)

    def __repr__(self):
        return f"<OperationAuditLog {self.action} success={self.success}>"
