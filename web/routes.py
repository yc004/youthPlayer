# Web 路由
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from models import Schedule, User, db


main = Blueprint("main", __name__)

player = None
controller = None
watchdog = None


def init_routes(player_instance, controller_instance, watchdog_instance):
    global player, controller, watchdog
    player = player_instance
    controller = controller_instance
    watchdog = watchdog_instance


def _parse_datetime(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def _build_dashboard_context():
    schedules = controller.get_schedules()
    return {
        "status": player.get_status(),
        "screens": player.get_available_screens(),
        "schedules": schedules,
        "active_schedule": controller.get_current_schedule() or controller.get_active_schedule_now(),
        "summary": controller.get_runtime_summary(),
    }


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


@main.route("/schedule/add", methods=["POST"])
@login_required
def add_schedule():
    try:
        start_time = _parse_datetime(request.form["start_time"])
        end_time = _parse_datetime(request.form["end_time"])
        if end_time <= start_time:
            raise ValueError("结束时间必须晚于开始时间")

        schedule = Schedule(
            name=request.form["name"].strip(),
            start_time=start_time,
            end_time=end_time,
            content_type=request.form["content_type"],
            content_path=request.form["content_path"].strip(),
            screen_index=int(request.form["screen_index"]),
            is_active=True,
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
        start_time = _parse_datetime(request.form["start_time"])
        end_time = _parse_datetime(request.form["end_time"])
        if end_time <= start_time:
            raise ValueError("结束时间必须晚于开始时间")

        kwargs = {
            "name": request.form["name"].strip(),
            "start_time": start_time,
            "end_time": end_time,
            "content_type": request.form["content_type"],
            "content_path": request.form["content_path"].strip(),
            "screen_index": int(request.form["screen_index"]),
            "is_active": "is_active" in request.form,
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
                }
                if active_schedule
                else None
            ),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


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
