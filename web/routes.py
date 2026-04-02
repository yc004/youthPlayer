import os
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from config import Config
from models import Schedule, SystemSetting, User, db


main = Blueprint("main", __name__)

player = None
controller = None
watchdog = None

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SETTING_KEY_ALL_ELECTRON = "all_play_via_electron"


def init_routes(player_instance, controller_instance, watchdog_instance):
    global player, controller, watchdog
    player = player_instance
    controller = controller_instance
    watchdog = watchdog_instance


def _parse_datetime(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def _parse_weekdays(form):
    days = []
    for value in form.getlist("weekdays"):
        if value.isdigit():
            day = int(value)
            if 0 <= day <= 6:
                days.append(day)
    return sorted(set(days))


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
    return render_template("index.html", **_build_dashboard_context())


@main.route("/control", methods=["POST"])
@login_required
def control():
    action = request.form.get("action", "")
    schedule_id = request.form.get("schedule_id")
    success = controller.control_playback(action, schedule_id=schedule_id)
    flash(
        "操作成功。" if success else f"操作失败：{player.get_status().get('last_error') or '请检查日志'}",
        "success" if success else "error",
    )
    return redirect(url_for("main.index"))


def _validate_schedule_form(form):
    start_time = _parse_datetime(form["start_time"])
    end_time = _parse_datetime(form["end_time"])
    is_weekly = "is_weekly" in form
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

    return start_time, end_time, is_weekly, ",".join(str(day) for day in weekdays)


@main.route("/schedule/add", methods=["POST"])
@login_required
def add_schedule():
    try:
        start_time, end_time, is_weekly, weekly_days = _validate_schedule_form(request.form)

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
        )
        success = controller.add_schedule(schedule)
        flash("时间表添加成功。" if success else "时间表添加失败。", "success" if success else "error")
    except Exception as exc:
        flash(f"时间表添加失败：{exc}", "error")
    return redirect(url_for("main.index"))


@main.route("/schedule/update/<int:schedule_id>", methods=["POST"])
@login_required
def update_schedule(schedule_id):
    try:
        start_time, end_time, is_weekly, weekly_days = _validate_schedule_form(request.form)

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
        }
        success = controller.update_schedule(schedule_id, **kwargs)
        flash("时间表更新成功。" if success else "时间表更新失败。", "success" if success else "error")
    except Exception as exc:
        flash(f"时间表更新失败：{exc}", "error")
    return redirect(url_for("main.index"))


@main.route("/schedule/delete/<int:schedule_id>", methods=["POST"])
@login_required
def delete_schedule(schedule_id):
    success = controller.delete_schedule(schedule_id)
    flash("时间表已删除。" if success else "删除失败。", "success" if success else "error")
    return redirect(url_for("main.index"))


@main.route("/schedule/toggle/<int:schedule_id>", methods=["POST"])
@login_required
def toggle_schedule(schedule_id):
    schedule = db.session.get(Schedule, schedule_id)
    if not schedule:
        flash("未找到该时间表。", "error")
        return redirect(url_for("main.index"))

    success = controller.update_schedule(schedule_id, is_active=not schedule.is_active)
    flash("时间表状态已更新。" if success else "状态更新失败。", "success" if success else "error")
    return redirect(url_for("main.index"))


@main.route("/schedule/play/<int:schedule_id>", methods=["POST"])
@login_required
def play_schedule_now(schedule_id):
    success = controller.control_playback("start", schedule_id=schedule_id)
    flash("已开始播放指定内容。" if success else "播放失败。", "success" if success else "error")
    return redirect(url_for("main.index"))


@main.route("/api/status")
@login_required
def api_status():
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
                }
                if active_schedule
                else None
            ),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@main.route("/api/browse")
@login_required
def api_browse():
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
    return jsonify(player.get_status())


@main.route("/users")
@login_required
def manage_users():
    if not current_user.is_admin:
        flash("只有管理员可以访问用户管理。", "error")
        return redirect(url_for("main.index"))
    users = User.query.order_by(User.username.asc()).all()
    return render_template("users.html", users=users)


@main.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if not current_user.is_admin:
        flash("只有管理员可以访问系统设置。", "error")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        enable_all_electron = "all_play_via_electron" in request.form
        _set_setting_bool(SETTING_KEY_ALL_ELECTRON, enable_all_electron)
        Config.ALL_PLAY_VIA_ELECTRON = enable_all_electron
        flash("系统设置已保存。", "success")
        return redirect(url_for("main.settings"))

    all_play_via_electron = _get_setting_bool(SETTING_KEY_ALL_ELECTRON, Config.ALL_PLAY_VIA_ELECTRON)
    return render_template("settings.html", all_play_via_electron=all_play_via_electron)


@main.route("/user/add", methods=["POST"])
@login_required
def add_user():
    if not current_user.is_admin:
        flash("只有管理员可以创建用户。", "error")
        return redirect(url_for("main.index"))

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        flash("用户名和密码不能为空。", "error")
        return redirect(url_for("main.manage_users"))

    if User.query.filter_by(username=username).first():
        flash("用户名已存在。", "error")
        return redirect(url_for("main.manage_users"))

    user = User(
        username=username,
        is_admin="is_admin" in request.form,
        is_active="is_active" in request.form,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash("用户创建成功。", "success")
    return redirect(url_for("main.manage_users"))


@main.route("/user/toggle/<int:user_id>", methods=["POST"])
@login_required
def toggle_user(user_id):
    if not current_user.is_admin:
        flash("只有管理员可以变更用户状态。", "error")
        return redirect(url_for("main.index"))

    user = db.session.get(User, user_id)
    if not user:
        flash("用户不存在。", "error")
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
def delete_user(user_id):
    if not current_user.is_admin:
        flash("只有管理员可以删除用户。", "error")
        return redirect(url_for("main.index"))

    user = db.session.get(User, user_id)
    if user and user.id != current_user.id:
        db.session.delete(user)
        db.session.commit()
        flash("用户已删除。", "success")
    else:
        flash("不能删除当前登录账户。", "error")
    return redirect(url_for("main.manage_users"))
