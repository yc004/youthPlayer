import os
from uuid import uuid4
from datetime import datetime
from functools import wraps

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from config import Config
from models import (
    ALL_PERMISSIONS,
    DEFAULT_USER_PERMISSIONS,
    OperationAuditLog,
    Schedule,
    SettingAuditLog,
    SystemSetting,
    User,
    db,
)


main = Blueprint("main", __name__)

player = None
controller = None
watchdog = None

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SETTING_KEY_ALL_ELECTRON = "all_play_via_electron"
SETTING_KEY_MONITOR_INTERVAL = "monitor_capture_interval"
SETTING_KEY_SCREENSAVER_IMAGE = "idle_screensaver_image"
SETTING_KEY_SCREENSAVER_SCREEN_INDEX = "idle_screensaver_screen_index"
SETTING_KEY_SCREENSAVER_WINDOW_MODE = "idle_screensaver_window_mode"
SETTING_KEY_SCREENSAVER_WINDOW_LEFT = "idle_screensaver_window_left"
SETTING_KEY_SCREENSAVER_WINDOW_TOP = "idle_screensaver_window_top"
SETTING_KEY_SCREENSAVER_WINDOW_WIDTH = "idle_screensaver_window_width"
SETTING_KEY_SCREENSAVER_WINDOW_HEIGHT = "idle_screensaver_window_height"
SCREENSAVER_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SCREENSAVER_MAX_SIZE = 20 * 1024 * 1024
PERMISSION_DEFS = [
    ("dashboard.view", "查看控制台"),
    ("playback.control", "控制播放"),
    ("schedule.view", "查看播放计划"),
    ("schedule.manage", "管理播放计划"),
    ("monitor.view", "查看画面监控"),
    ("monitor.capture", "手动截图"),
    ("files.browse", "浏览服务器文件"),
    ("users.manage", "用户管理"),
    ("settings.manage", "系统设置"),
]
PERMISSION_LABEL_MAP = dict(PERMISSION_DEFS)


def init_routes(player_instance, controller_instance, watchdog_instance):
    global player, controller, watchdog
    player = player_instance
    controller = controller_instance
    watchdog = watchdog_instance


def _has_permission(permission_code):
    if not getattr(current_user, "is_authenticated", False):
        return False
    return current_user.has_permission(permission_code)


def _permission_required(permission_code):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not _has_permission(permission_code):
                flash("当前账户无此操作权限。", "error")
                return redirect(url_for("main.index"))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def _first_available_page():
    if _has_permission("dashboard.view"):
        return "main.dashboard"
    if _has_permission("schedule.view"):
        return "main.schedules_page"
    if _has_permission("monitor.view"):
        return "main.monitor_page"
    if _has_permission("users.manage"):
        return "main.manage_users"
    if _has_permission("settings.manage"):
        return "main.settings"
    return "main.logout"


def _parse_user_permissions(form):
    selected = form.getlist("permissions")
    return sorted({item for item in selected if item in ALL_PERMISSIONS})


def _parse_datetime(value, allow_time_only=False):
    raw = (value or "").strip()
    if not raw:
        raise ValueError("时间不能为空。")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        if allow_time_only:
            t = datetime.strptime(raw, "%H:%M").time()
            now = datetime.now()
            return datetime(now.year, now.month, now.day, t.hour, t.minute)
        raise


def _parse_weekdays(form):
    days = []
    for value in form.getlist("weekdays"):
        if value.isdigit():
            day = int(value)
            if 0 <= day <= 6:
                days.append(day)
    return sorted(set(days))


def _parse_playlist_paths(form):
    raw = (form.get("playlist_paths") or "").replace("\r\n", "\n")
    items = [line.strip() for line in raw.split("\n") if line.strip()]
    return items, "\n".join(items)


def _build_timeline_payload(schedules):
    out = []
    for item in schedules:
        out.append(
            {
                "id": item.id,
                "name": item.name,
                "is_active": item.is_active,
                "is_weekly": bool(item.is_weekly),
                "weekly_days": sorted(item.weekly_day_set),
                "start_weekday": item.start_time.weekday(),
                "start_minutes": item.start_time.hour * 60 + item.start_time.minute,
                "end_minutes": item.end_time.hour * 60 + item.end_time.minute,
                "screen_index": item.screen_index,
                "content_type": item.content_type,
            }
        )
    return out


def _build_dashboard_context():
    schedules = controller.get_schedules()
    return {
        "status": player.get_status(),
        "screens": player.get_available_screens(),
        "schedules": schedules,
        "active_schedule": controller.get_current_schedule() or controller.get_active_schedule_now(),
        "summary": controller.get_runtime_summary(),
        "weekday_labels": WEEKDAY_LABELS,
        "timeline_payload": _build_timeline_payload(schedules),
    }


@main.app_context_processor
def inject_permission_helpers():
    return {
        "has_perm": _has_permission,
        "permission_defs": PERMISSION_DEFS,
        "permission_labels": PERMISSION_LABEL_MAP,
    }


def _back(default_endpoint):
    ref = request.referrer
    if ref:
        return redirect(ref)
    return redirect(url_for(default_endpoint))


def _list_windows_roots():
    roots = []
    for code in range(ord("A"), ord("Z") + 1):
        drive = f"{chr(code)}:\\"
        if os.path.exists(drive):
            roots.append(drive)
    return roots


def _get_setting_bool(key, default=False):
    item = db.session.get(SystemSetting, key)
    if not item:
        return default
    return str(item.value).strip().lower() in {"1", "true", "yes", "on"}


def _set_setting_bool(key, value):
    item = db.session.get(SystemSetting, key)
    if not item:
        item = SystemSetting(key=key, value="1" if value else "0")
        db.session.add(item)
    else:
        item.value = "1" if value else "0"
    db.session.commit()


def _get_setting_int(key, default=0):
    item = db.session.get(SystemSetting, key)
    if not item:
        return int(default)
    try:
        return int(str(item.value).strip())
    except Exception:
        return int(default)


def _set_setting_int(key, value):
    v = int(value)
    item = db.session.get(SystemSetting, key)
    if not item:
        item = SystemSetting(key=key, value=str(v))
        db.session.add(item)
    else:
        item.value = str(v)
    db.session.commit()


def _get_setting_str(key, default=""):
    item = db.session.get(SystemSetting, key)
    if not item:
        return str(default or "")
    return str(item.value or "").strip()


def _set_setting_str(key, value):
    item = db.session.get(SystemSetting, key)
    if not item:
        item = SystemSetting(key=key, value=str(value or ""))
        db.session.add(item)
    else:
        item.value = str(value or "")
    db.session.commit()


def _get_screensaver_assets_dir():
    target = os.path.join(Config.BASE_DIR, "runtime", "screensaver_assets")
    os.makedirs(target, exist_ok=True)
    return target


def _is_managed_screensaver_path(path):
    if not path:
        return False
    try:
        assets_dir = os.path.abspath(_get_screensaver_assets_dir())
        target = os.path.abspath(path)
        return os.path.commonpath([assets_dir, target]) == assets_dir
    except Exception:
        return False


def _cleanup_screensaver_file(path):
    if not path:
        return
    try:
        if _is_managed_screensaver_path(path) and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _save_screensaver_upload(file_storage):
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        raise ValueError("请选择要上传的图片文件。")

    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in SCREENSAVER_ALLOWED_EXTENSIONS:
        raise ValueError("仅支持 jpg/jpeg/png/bmp/webp 格式图片。")

    content_length = request.content_length or 0
    if content_length > SCREENSAVER_MAX_SIZE:
        raise ValueError("上传文件过大，最大支持 20MB。")

    content_type = (file_storage.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise ValueError("请上传图片文件。")

    target_name = f"screensaver_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}{suffix}"
    target_path = os.path.join(_get_screensaver_assets_dir(), target_name)
    file_storage.save(target_path)
    if os.path.getsize(target_path) > SCREENSAVER_MAX_SIZE:
        _cleanup_screensaver_file(target_path)
        raise ValueError("上传文件过大，最大支持 20MB。")
    return target_path


def _append_setting_audit_log(setting_key, old_value, new_value):
    try:
        log = SettingAuditLog(
            setting_key=setting_key,
            old_value=old_value,
            new_value=new_value,
            operator_user_id=getattr(current_user, "id", None),
            operator_username=getattr(current_user, "username", "") or "",
            remote_addr=request.remote_addr or "",
            user_agent=(request.headers.get("User-Agent", "") or "")[:500],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _append_operation_audit_log(action, success=True, target_type="system", target_id="", detail=""):
    try:
        log = OperationAuditLog(
            action=action,
            target_type=target_type,
            target_id=str(target_id or ""),
            success=bool(success),
            detail=(detail or "")[:500],
            operator_user_id=getattr(current_user, "id", None),
            operator_username=getattr(current_user, "username", "") or "",
            remote_addr=request.remote_addr or "",
            user_agent=(request.headers.get("User-Agent", "") or "")[:500],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


@main.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if not user or not user.check_password(password):
            flash("用户名或密码错误。", "error")
            return render_template("login.html")

        if not user.is_active:
            flash("当前账户已被停用，请联系管理员。", "error")
            return render_template("login.html")

        if not user.password.startswith(("pbkdf2:", "scrypt:")):
            user.set_password(password)
            db.session.commit()

        login_user(user)
        flash(f"欢迎回来，{user.username}。", "success")
        return redirect(url_for("main.index"))

    return render_template("login.html")


@main.route("/logout")
@login_required
def logout():
    logout_user()
    flash("已安全退出系统。", "success")
    return redirect(url_for("main.login"))


@main.route("/")
@login_required
def index():
    endpoint = _first_available_page()
    if endpoint == "main.logout":
        flash("当前账号未分配任何可访问权限，请联系管理员。", "error")
    return redirect(url_for(endpoint))


@main.route("/dashboard")
@login_required
@_permission_required("dashboard.view")
def dashboard():
    return render_template("dashboard.html", **_build_dashboard_context())


@main.route("/schedules")
@login_required
@_permission_required("schedule.view")
def schedules_page():
    return render_template("schedules.html", **_build_dashboard_context())


@main.route("/monitor")
@login_required
@_permission_required("monitor.view")
def monitor_page():
    return render_template("monitor.html", **_build_dashboard_context())


@main.route("/monitor/capture", methods=["POST"])
@login_required
@_permission_required("monitor.capture")
def monitor_capture_now():
    ok, msg = player.capture_monitor_snapshot()
    flash("已手动截图。" if ok else f"手动截图失败：{msg}", "success" if ok else "error")
    return _back("main.monitor_page")


@main.route("/control", methods=["POST"])
@login_required
@_permission_required("playback.control")
def control():
    action = request.form.get("action", "")
    schedule_id = request.form.get("schedule_id")
    success = controller.control_playback(action, schedule_id=schedule_id)
    _append_operation_audit_log(
        action=f"playback_{action}",
        success=success,
        target_type="schedule" if schedule_id else "player",
        target_id=schedule_id or "",
        detail=player.get_status().get("last_error") or "",
    )
    flash(
        "操作成功。" if success else f"操作失败：{player.get_status().get('last_error') or '请检查日志'}",
        "success" if success else "error",
    )
    return _back("main.dashboard")


def _validate_schedule_form(form):
    is_weekly = "is_weekly" in form
    start_time = _parse_datetime(form["start_time"], allow_time_only=is_weekly)
    end_time = _parse_datetime(form["end_time"], allow_time_only=is_weekly)
    weekdays = _parse_weekdays(form)

    if is_weekly:
        if not weekdays:
            raise ValueError("周循环任务至少需要选择一个星期。")
        start_minutes = start_time.hour * 60 + start_time.minute
        end_minutes = end_time.hour * 60 + end_time.minute
        if end_minutes <= start_minutes:
            raise ValueError("周循环任务要求结束时间晚于开始时间（同一天内）。")
    elif end_time <= start_time:
        raise ValueError("结束时间必须晚于开始时间。")

    loop_mode = (form.get("loop_mode") or "single").strip()
    if loop_mode not in {"single", "list_loop", "single_loop", "once"}:
        loop_mode = "single"
    try:
        loop_count = int(form.get("loop_count") or 0)
    except Exception:
        loop_count = 0
    loop_count = max(0, loop_count)
    _items, playlist_paths = _parse_playlist_paths(form)
    window_mode = (form.get("window_mode") or "fullscreen").strip().lower()
    if window_mode not in {"fullscreen", "custom"}:
        window_mode = "fullscreen"
    try:
        window_left = int(form.get("window_left") or 0)
    except Exception:
        window_left = 0
    try:
        window_top = int(form.get("window_top") or 0)
    except Exception:
        window_top = 0
    try:
        window_width = int(form.get("window_width") or 0)
    except Exception:
        window_width = 0
    try:
        window_height = int(form.get("window_height") or 0)
    except Exception:
        window_height = 0
    if window_mode == "custom":
        if window_width < 100 or window_height < 100:
            raise ValueError("窗口宽高最小为 100 像素。")

    return (
        start_time,
        end_time,
        is_weekly,
        ",".join(str(day) for day in weekdays),
        loop_mode,
        loop_count,
        playlist_paths,
        window_mode,
        window_left,
        window_top,
        window_width,
        window_height,
    )


@main.route("/schedule/add", methods=["POST"])
@login_required
@_permission_required("schedule.manage")
def add_schedule():
    try:
        (
            start_time,
            end_time,
            is_weekly,
            weekly_days,
            loop_mode,
            loop_count,
            playlist_paths,
            window_mode,
            window_left,
            window_top,
            window_width,
            window_height,
        ) = _validate_schedule_form(request.form)

        schedule = Schedule(
            name=request.form["name"].strip(),
            start_time=start_time,
            end_time=end_time,
            content_type=request.form["content_type"],
            content_path=request.form["content_path"].strip(),
            screen_index=int(request.form["screen_index"]),
            is_active=True,
            is_weekly=is_weekly,
            weekly_days=weekly_days,
            playlist_paths=playlist_paths,
            loop_mode=loop_mode,
            loop_count=loop_count,
            window_mode=window_mode,
            window_left=window_left,
            window_top=window_top,
            window_width=window_width,
            window_height=window_height,
        )
        success = controller.add_schedule(schedule)
        flash("时间表添加成功。" if success else "时间表添加失败。", "success" if success else "error")
    except Exception as exc:
        flash(f"时间表添加失败：{exc}", "error")
    return _back("main.schedules_page")


@main.route("/schedule/update/<int:schedule_id>", methods=["POST"])
@login_required
@_permission_required("schedule.manage")
def update_schedule(schedule_id):
    try:
        (
            start_time,
            end_time,
            is_weekly,
            weekly_days,
            loop_mode,
            loop_count,
            playlist_paths,
            window_mode,
            window_left,
            window_top,
            window_width,
            window_height,
        ) = _validate_schedule_form(request.form)

        kwargs = {
            "name": request.form["name"].strip(),
            "start_time": start_time,
            "end_time": end_time,
            "content_type": request.form["content_type"],
            "content_path": request.form["content_path"].strip(),
            "screen_index": int(request.form["screen_index"]),
            "is_active": "is_active" in request.form,
            "is_weekly": is_weekly,
            "weekly_days": weekly_days,
            "playlist_paths": playlist_paths,
            "loop_mode": loop_mode,
            "loop_count": loop_count,
            "window_mode": window_mode,
            "window_left": window_left,
            "window_top": window_top,
            "window_width": window_width,
            "window_height": window_height,
        }
        success = controller.update_schedule(schedule_id, **kwargs)
        flash("时间表更新成功。" if success else "时间表更新失败。", "success" if success else "error")
    except Exception as exc:
        flash(f"时间表更新失败：{exc}", "error")
    return _back("main.schedules_page")


@main.route("/schedule/delete/<int:schedule_id>", methods=["POST"])
@login_required
@_permission_required("schedule.manage")
def delete_schedule(schedule_id):
    success = controller.delete_schedule(schedule_id)
    _append_operation_audit_log(
        action="schedule_delete",
        success=success,
        target_type="schedule",
        target_id=str(schedule_id),
    )
    flash("时间表已删除。" if success else "删除失败。", "success" if success else "error")
    return _back("main.schedules_page")


@main.route("/schedule/toggle/<int:schedule_id>", methods=["POST"])
@login_required
@_permission_required("schedule.manage")
def toggle_schedule(schedule_id):
    schedule = db.session.get(Schedule, schedule_id)
    if not schedule:
        flash("未找到该时间表。", "error")
        return _back("main.schedules_page")

    success = controller.update_schedule(schedule_id, is_active=not schedule.is_active)
    flash("时间表状态已更新。" if success else "状态更新失败。", "success" if success else "error")
    return _back("main.schedules_page")


@main.route("/schedule/play/<int:schedule_id>", methods=["POST"])
@login_required
@_permission_required("playback.control")
def play_schedule_now(schedule_id):
    success = controller.control_playback("start", schedule_id=schedule_id)
    _append_operation_audit_log(
        action="schedule_play_now",
        success=success,
        target_type="schedule",
        target_id=str(schedule_id),
        detail=player.get_status().get("last_error") or "",
    )
    flash("已开始播放指定内容。" if success else "播放失败。", "success" if success else "error")
    return _back("main.schedules_page")


@main.route("/api/status")
@login_required
def api_status():
    if not (_has_permission("dashboard.view") or _has_permission("monitor.view")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    active_schedule = controller.get_current_schedule() or controller.get_active_schedule_now()
    return jsonify(
        {
            "player": player.get_status(),
            "summary": controller.get_runtime_summary(),
            "active_schedule": (
                {
                    "id": active_schedule.id,
                    "name": active_schedule.name,
                    "content_type": active_schedule.content_type,
                    "screen_index": active_schedule.screen_index,
                    "start_time": active_schedule.start_time.isoformat(timespec="minutes"),
                    "end_time": active_schedule.end_time.isoformat(timespec="minutes"),
                    "is_weekly": active_schedule.is_weekly,
                    "weekly_days": sorted(active_schedule.weekly_day_set),
                    "window_mode": active_schedule.window_mode,
                    "window_left": active_schedule.window_left,
                    "window_top": active_schedule.window_top,
                    "window_width": active_schedule.window_width,
                    "window_height": active_schedule.window_height,
                }
                if active_schedule
                else None
            ),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "monitor": {
                "captured_at": player.monitor_last_capture_at,
                "available": bool(player.monitor_last_capture_path and os.path.exists(player.monitor_last_capture_path)),
                "frame_url": url_for("main.monitor_frame"),
            },
        }
    )


@main.route("/monitor/frame")
@login_required
def monitor_frame():
    if not _has_permission("monitor.view"):
        return ("Forbidden", 403)
    path = player.monitor_last_capture_path
    if not path or not os.path.exists(path):
        ok, _ = player.capture_monitor_snapshot()
        if ok:
            path = player.monitor_last_capture_path
    if not path or not os.path.exists(path):
        return ("No monitor frame", 404)
    return send_file(path, mimetype="image/bmp", conditional=True)


@main.route("/api/monitor")
@login_required
def api_monitor():
    if not _has_permission("monitor.view"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    path = player.monitor_last_capture_path
    exists = bool(path and os.path.exists(path))
    if not exists:
        ok, _ = player.capture_monitor_snapshot()
        if ok:
            path = player.monitor_last_capture_path
            exists = bool(path and os.path.exists(path))
    return jsonify(
        {
            "ok": exists,
            "captured_at": player.monitor_last_capture_at,
            "frame_url": (url_for("main.monitor_frame") if exists else ""),
        }
    )


@main.route("/screensaver/preview")
@login_required
def screensaver_preview():
    if not _has_permission("settings.manage"):
        return ("Forbidden", 403)
    path = _get_setting_str(SETTING_KEY_SCREENSAVER_IMAGE, Config.IDLE_SCREENSAVER_IMAGE)
    if not path or not os.path.exists(path):
        return ("No screensaver image", 404)
    return send_file(path, conditional=True)


@main.route("/api/browse")
@login_required
def api_browse():
    if not _has_permission("files.browse"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    raw_path = (request.args.get("path") or "").strip().strip('"')

    # 不传路径时返回系统盘符根列表
    if not raw_path:
        roots = _list_windows_roots()
        return jsonify(
            {
                "ok": True,
                "cwd": "",
                "parent": "",
                "entries": [{"name": root, "path": root, "is_dir": True} for root in roots],
            }
        )

    target = os.path.abspath(os.path.expanduser(raw_path))
    if os.path.isfile(target):
        target = os.path.dirname(target)

    if not os.path.exists(target):
        return jsonify({"ok": False, "error": "路径不存在"}), 404

    if not os.path.isdir(target):
        return jsonify({"ok": False, "error": "不是目录"}), 400

    entries = []
    try:
        with os.scandir(target) as it:
            for item in it:
                try:
                    entries.append(
                        {
                            "name": item.name,
                            "path": item.path,
                            "is_dir": item.is_dir(follow_symlinks=False),
                        }
                    )
                except PermissionError:
                    continue
    except PermissionError:
        return jsonify({"ok": False, "error": "无权限访问该目录"}), 403

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = os.path.dirname(target.rstrip("\\/")) if target else ""
    if parent == target:
        parent = ""

    return jsonify({"ok": True, "cwd": target, "parent": parent, "entries": entries})


@main.route("/status")
@login_required
def legacy_status():
    if not (_has_permission("dashboard.view") or _has_permission("monitor.view")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify(player.get_status())


@main.route("/users")
@login_required
@_permission_required("users.manage")
def manage_users():
    users = User.query.order_by(User.username.asc()).all()
    return render_template(
        "users.html",
        users=users,
        permission_defs=PERMISSION_DEFS,
        permission_labels=PERMISSION_LABEL_MAP,
        default_user_permissions=set(DEFAULT_USER_PERMISSIONS),
    )


@main.route("/settings", methods=["GET", "POST"])
@login_required
@_permission_required("settings.manage")
def settings():
    if request.method == "POST":
        form_action = (request.form.get("form_action") or "system").strip()
        if form_action == "password":
            old_password = request.form.get("old_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not current_user.check_password(old_password):
                flash("当前密码不正确。", "error")
                return redirect(url_for("main.settings"))
            if len(new_password) < 6:
                flash("新密码至少 6 位。", "error")
                return redirect(url_for("main.settings"))
            if new_password != confirm_password:
                flash("两次输入的新密码不一致。", "error")
                return redirect(url_for("main.settings"))
            current_user.set_password(new_password)
            db.session.commit()
            _append_operation_audit_log(
                action="admin_password_change",
                success=True,
                target_type="user",
                target_id=str(current_user.id),
            )
            flash("管理员密码修改成功。", "success")
            return redirect(url_for("main.settings"))

        enable_all_electron = "all_play_via_electron" in request.form
        monitor_interval = request.form.get("monitor_capture_interval", str(Config.MONITOR_CAPTURE_INTERVAL))
        try:
            monitor_interval = max(2, min(3600, int(monitor_interval)))
        except Exception:
            monitor_interval = int(Config.MONITOR_CAPTURE_INTERVAL)
        screensaver_screen_index = request.form.get(
            "screensaver_screen_index",
            str(Config.IDLE_SCREENSAVER_SCREEN_INDEX),
        )
        try:
            screensaver_screen_index = int(screensaver_screen_index)
        except Exception:
            screensaver_screen_index = int(Config.IDLE_SCREENSAVER_SCREEN_INDEX)
        screensaver_window_mode = (request.form.get("screensaver_window_mode") or "fullscreen").strip().lower()
        if screensaver_window_mode not in {"fullscreen", "custom"}:
            screensaver_window_mode = "fullscreen"
        try:
            screensaver_window_left = int(request.form.get("screensaver_window_left") or 0)
        except Exception:
            screensaver_window_left = 0
        try:
            screensaver_window_top = int(request.form.get("screensaver_window_top") or 0)
        except Exception:
            screensaver_window_top = 0
        try:
            screensaver_window_width = int(request.form.get("screensaver_window_width") or 0)
        except Exception:
            screensaver_window_width = 0
        try:
            screensaver_window_height = int(request.form.get("screensaver_window_height") or 0)
        except Exception:
            screensaver_window_height = 0
        if screensaver_window_mode == "custom":
            screensaver_window_width = max(100, screensaver_window_width or 100)
            screensaver_window_height = max(100, screensaver_window_height or 100)

        old_value = "1" if _get_setting_bool(SETTING_KEY_ALL_ELECTRON, Config.ALL_PLAY_VIA_ELECTRON) else "0"
        new_value = "1" if enable_all_electron else "0"
        _set_setting_bool(SETTING_KEY_ALL_ELECTRON, enable_all_electron)

        old_interval = str(_get_setting_int(SETTING_KEY_MONITOR_INTERVAL, Config.MONITOR_CAPTURE_INTERVAL))
        _set_setting_int(SETTING_KEY_MONITOR_INTERVAL, monitor_interval)
        old_ss_screen_index = str(
            _get_setting_int(SETTING_KEY_SCREENSAVER_SCREEN_INDEX, Config.IDLE_SCREENSAVER_SCREEN_INDEX)
        )
        _set_setting_int(SETTING_KEY_SCREENSAVER_SCREEN_INDEX, screensaver_screen_index)
        old_ss_window_mode = _get_setting_str(
            SETTING_KEY_SCREENSAVER_WINDOW_MODE,
            Config.IDLE_SCREENSAVER_WINDOW_MODE,
        )
        _set_setting_str(SETTING_KEY_SCREENSAVER_WINDOW_MODE, screensaver_window_mode)
        old_ss_left = str(_get_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_LEFT, Config.IDLE_SCREENSAVER_WINDOW_LEFT))
        old_ss_top = str(_get_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_TOP, Config.IDLE_SCREENSAVER_WINDOW_TOP))
        old_ss_width = str(_get_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_WIDTH, Config.IDLE_SCREENSAVER_WINDOW_WIDTH))
        old_ss_height = str(_get_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_HEIGHT, Config.IDLE_SCREENSAVER_WINDOW_HEIGHT))
        _set_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_LEFT, screensaver_window_left)
        _set_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_TOP, screensaver_window_top)
        _set_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_WIDTH, screensaver_window_width)
        _set_setting_int(SETTING_KEY_SCREENSAVER_WINDOW_HEIGHT, screensaver_window_height)

        old_screensaver_image = _get_setting_str(SETTING_KEY_SCREENSAVER_IMAGE, Config.IDLE_SCREENSAVER_IMAGE)
        new_screensaver_image = old_screensaver_image
        remove_screensaver_image = "remove_screensaver_image" in request.form
        upload_file = request.files.get("screensaver_image")

        try:
            if remove_screensaver_image:
                new_screensaver_image = ""
            if upload_file and (upload_file.filename or "").strip():
                new_screensaver_image = _save_screensaver_upload(upload_file)
        except Exception as exc:
            flash(f"屏保图片更新失败：{exc}", "error")
            return redirect(url_for("main.settings"))

        _set_setting_str(SETTING_KEY_SCREENSAVER_IMAGE, new_screensaver_image)
        if old_screensaver_image != new_screensaver_image:
            _append_setting_audit_log(
                SETTING_KEY_SCREENSAVER_IMAGE,
                old_screensaver_image or "<empty>",
                new_screensaver_image or "<empty>",
            )
            _cleanup_screensaver_file(old_screensaver_image)
            try:
                player.show_screensaver()
            except Exception:
                pass

        Config.ALL_PLAY_VIA_ELECTRON = enable_all_electron
        Config.MONITOR_CAPTURE_INTERVAL = monitor_interval
        Config.IDLE_SCREENSAVER_IMAGE = new_screensaver_image
        Config.IDLE_SCREENSAVER_SCREEN_INDEX = screensaver_screen_index
        Config.IDLE_SCREENSAVER_WINDOW_MODE = screensaver_window_mode
        Config.IDLE_SCREENSAVER_WINDOW_LEFT = screensaver_window_left
        Config.IDLE_SCREENSAVER_WINDOW_TOP = screensaver_window_top
        Config.IDLE_SCREENSAVER_WINDOW_WIDTH = screensaver_window_width
        Config.IDLE_SCREENSAVER_WINDOW_HEIGHT = screensaver_window_height

        try:
            controller.scheduler.reschedule_job(
                "monitor_capture_job",
                trigger="interval",
                seconds=max(2, int(monitor_interval)),
            )
        except Exception:
            pass
        if old_value != new_value:
            _append_setting_audit_log(SETTING_KEY_ALL_ELECTRON, old_value, new_value)
        if old_interval != str(monitor_interval):
            _append_setting_audit_log(SETTING_KEY_MONITOR_INTERVAL, old_interval, str(monitor_interval))
            try:
                player.capture_monitor_snapshot()
            except Exception:
                pass
        if old_ss_screen_index != str(screensaver_screen_index):
            _append_setting_audit_log(
                SETTING_KEY_SCREENSAVER_SCREEN_INDEX,
                old_ss_screen_index,
                str(screensaver_screen_index),
            )
        if old_ss_window_mode != screensaver_window_mode:
            _append_setting_audit_log(
                SETTING_KEY_SCREENSAVER_WINDOW_MODE,
                old_ss_window_mode,
                screensaver_window_mode,
            )
        if old_ss_left != str(screensaver_window_left):
            _append_setting_audit_log(SETTING_KEY_SCREENSAVER_WINDOW_LEFT, old_ss_left, str(screensaver_window_left))
        if old_ss_top != str(screensaver_window_top):
            _append_setting_audit_log(SETTING_KEY_SCREENSAVER_WINDOW_TOP, old_ss_top, str(screensaver_window_top))
        if old_ss_width != str(screensaver_window_width):
            _append_setting_audit_log(
                SETTING_KEY_SCREENSAVER_WINDOW_WIDTH,
                old_ss_width,
                str(screensaver_window_width),
            )
        if old_ss_height != str(screensaver_window_height):
            _append_setting_audit_log(
                SETTING_KEY_SCREENSAVER_WINDOW_HEIGHT,
                old_ss_height,
                str(screensaver_window_height),
            )
        try:
            player.show_screensaver()
        except Exception:
            pass
        flash("系统设置已保存。", "success")
        return redirect(url_for("main.settings"))

    all_play_via_electron = _get_setting_bool(SETTING_KEY_ALL_ELECTRON, Config.ALL_PLAY_VIA_ELECTRON)
    monitor_capture_interval = _get_setting_int(SETTING_KEY_MONITOR_INTERVAL, Config.MONITOR_CAPTURE_INTERVAL)
    screensaver_screen_index = _get_setting_int(
        SETTING_KEY_SCREENSAVER_SCREEN_INDEX,
        Config.IDLE_SCREENSAVER_SCREEN_INDEX,
    )
    screensaver_window_mode = _get_setting_str(
        SETTING_KEY_SCREENSAVER_WINDOW_MODE,
        Config.IDLE_SCREENSAVER_WINDOW_MODE,
    )
    if screensaver_window_mode not in {"fullscreen", "custom"}:
        screensaver_window_mode = "fullscreen"
    screensaver_window_left = _get_setting_int(
        SETTING_KEY_SCREENSAVER_WINDOW_LEFT,
        Config.IDLE_SCREENSAVER_WINDOW_LEFT,
    )
    screensaver_window_top = _get_setting_int(
        SETTING_KEY_SCREENSAVER_WINDOW_TOP,
        Config.IDLE_SCREENSAVER_WINDOW_TOP,
    )
    screensaver_window_width = _get_setting_int(
        SETTING_KEY_SCREENSAVER_WINDOW_WIDTH,
        Config.IDLE_SCREENSAVER_WINDOW_WIDTH,
    )
    screensaver_window_height = _get_setting_int(
        SETTING_KEY_SCREENSAVER_WINDOW_HEIGHT,
        Config.IDLE_SCREENSAVER_WINDOW_HEIGHT,
    )
    screensaver_image_path = _get_setting_str(SETTING_KEY_SCREENSAVER_IMAGE, Config.IDLE_SCREENSAVER_IMAGE)
    if screensaver_image_path and not os.path.exists(screensaver_image_path):
        screensaver_image_path = ""
    screens = player.get_available_screens() if player else []
    audit_logs = (
        SettingAuditLog.query.order_by(SettingAuditLog.created_at.desc(), SettingAuditLog.id.desc())
        .limit(50)
        .all()
    )
    operation_logs = (
        OperationAuditLog.query.order_by(OperationAuditLog.created_at.desc(), OperationAuditLog.id.desc())
        .limit(100)
        .all()
    )
    return render_template(
        "settings.html",
        all_play_via_electron=all_play_via_electron,
        monitor_capture_interval=monitor_capture_interval,
        screens=screens,
        screensaver_image_path=screensaver_image_path,
        screensaver_screen_index=screensaver_screen_index,
        screensaver_window_mode=screensaver_window_mode,
        screensaver_window_left=screensaver_window_left,
        screensaver_window_top=screensaver_window_top,
        screensaver_window_width=screensaver_window_width,
        screensaver_window_height=screensaver_window_height,
        audit_logs=audit_logs,
        operation_logs=operation_logs,
    )


@main.route("/user/add", methods=["POST"])
@login_required
@_permission_required("users.manage")
def add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        flash("用户名和密码不能为空。", "error")
        return redirect(url_for("main.manage_users"))

    if User.query.filter_by(username=username).first():
        flash("用户名已存在。", "error")
        return redirect(url_for("main.manage_users"))

    make_admin = "is_admin" in request.form
    if make_admin and not current_user.is_admin:
        flash("仅管理员可以创建新的管理员账号。", "error")
        return redirect(url_for("main.manage_users"))

    user = User(
        username=username,
        is_admin=make_admin,
        is_active="is_active" in request.form,
    )
    user.set_permissions(_parse_user_permissions(request.form) or DEFAULT_USER_PERMISSIONS)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash("用户创建成功。", "success")
    return redirect(url_for("main.manage_users"))


@main.route("/user/permissions/<int:user_id>", methods=["POST"])
@login_required
@_permission_required("users.manage")
def update_user_permissions(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("用户不存在。", "error")
        return redirect(url_for("main.manage_users"))

    if user.is_admin:
        flash("管理员默认拥有全部权限，无需单独配置。", "info")
        return redirect(url_for("main.manage_users"))

    user.set_permissions(_parse_user_permissions(request.form))
    db.session.commit()
    flash("用户权限已更新。", "success")
    return redirect(url_for("main.manage_users"))


@main.route("/user/toggle/<int:user_id>", methods=["POST"])
@login_required
@_permission_required("users.manage")
def toggle_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("用户不存在。", "error")
        return redirect(url_for("main.manage_users"))
    if user.is_admin and not current_user.is_admin:
        flash("仅管理员可以操作管理员账号。", "error")
        return redirect(url_for("main.manage_users"))
    if user.id == current_user.id:
        flash("不能停用当前登录账户。", "error")
        return redirect(url_for("main.manage_users"))

    user.is_active = not user.is_active
    db.session.commit()
    flash("用户状态已更新。", "success")
    return redirect(url_for("main.manage_users"))


@main.route("/user/delete/<int:user_id>", methods=["POST"])
@login_required
@_permission_required("users.manage")
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if user and user.id != current_user.id:
        if user.is_admin and not current_user.is_admin:
            flash("仅管理员可以删除管理员账号。", "error")
            return redirect(url_for("main.manage_users"))
        db.session.delete(user)
        db.session.commit()
        flash("用户已删除。", "success")
    else:
        flash("不能删除当前登录账户。", "error")
    return redirect(url_for("main.manage_users"))
