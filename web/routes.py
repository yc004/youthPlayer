import os
import base64
import logging
import gzip
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import ssl
from uuid import uuid4
from datetime import datetime
from functools import wraps
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from flask import Blueprint, flash, jsonify, make_response, redirect, render_template, request, send_file, url_for
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
logger = logging.getLogger(__name__)
SYSTEM_STARTED_AT = datetime.now()

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
SETTING_KEY_NEXTCLOUD_ENABLED = "nextcloud_enabled"
SETTING_KEY_NEXTCLOUD_URL = "nextcloud_url"
SETTING_KEY_NEXTCLOUD_USERNAME = "nextcloud_username"
SETTING_KEY_NEXTCLOUD_PASSWORD = "nextcloud_password"
SETTING_KEY_NEXTCLOUD_ROOT = "nextcloud_root"
SETTING_KEY_NEXTCLOUD_SKIP_SSL_VERIFY = "nextcloud_skip_ssl_verify"
SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED = "nextcloud_cache_auto_clear_enabled"
SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_TIME = "nextcloud_cache_auto_clear_time"
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
    primary = (form.get("content_path") or "").strip()
    items = [line.strip() for line in raw.split("\n") if line.strip()]
    if primary:
        items.insert(0, primary)
    deduped = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    items = deduped
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
    nextcloud = _nextcloud_settings()
    return {
        "status": player.get_status(),
        "screens": player.get_available_screens(),
        "schedules": schedules,
        "active_schedule": controller.get_current_schedule() or controller.get_active_schedule_now(),
        "summary": controller.get_runtime_summary(),
        "weekday_labels": WEEKDAY_LABELS,
        "timeline_payload": _build_timeline_payload(schedules),
        "nextcloud_enabled": bool(
            nextcloud["enabled"] and nextcloud["url"] and nextcloud["username"] and nextcloud["password"]
        ),
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


def _nextcloud_settings():
    enabled = _get_setting_bool(SETTING_KEY_NEXTCLOUD_ENABLED, Config.NEXTCLOUD_ENABLED)
    url = _get_setting_str(SETTING_KEY_NEXTCLOUD_URL, Config.NEXTCLOUD_URL).strip()
    username = _get_setting_str(SETTING_KEY_NEXTCLOUD_USERNAME, Config.NEXTCLOUD_USERNAME).strip()
    password = _get_setting_str(SETTING_KEY_NEXTCLOUD_PASSWORD, Config.NEXTCLOUD_PASSWORD).strip()
    root = _get_setting_str(SETTING_KEY_NEXTCLOUD_ROOT, Config.NEXTCLOUD_ROOT).strip() or "/"
    skip_ssl_verify = _get_setting_bool(SETTING_KEY_NEXTCLOUD_SKIP_SSL_VERIFY, Config.NEXTCLOUD_SKIP_SSL_VERIFY)
    return {
        "enabled": bool(enabled),
        "url": url,
        "username": username,
        "password": password,
        "root": root,
        "skip_ssl_verify": bool(skip_ssl_verify),
    }


def _nextcloud_norm_path(raw_path):
    parts = [p for p in str(raw_path or "/").replace("\\", "/").split("/") if p]
    return "/" + "/".join(parts) if parts else "/"


def _nextcloud_webdav_base(url, username):
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    if "/remote.php/dav/" in base:
        return base
    return f"{base}/remote.php/dav/files/{quote(username, safe='')}"


def _nextcloud_join_webdav_url(base, remote_path):
    base = (base or "").rstrip("/")
    segs = [quote(p, safe="") for p in str(remote_path or "/").split("/") if p]
    if not segs:
        return base + "/"
    return base + "/" + "/".join(segs)


def _url_with_basic_auth(url, username, password):
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    user = quote(username or "", safe="")
    pwd = quote(password or "", safe="")
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    auth = f"{user}:{pwd}@" if (user or pwd) else ""
    netloc = f"{auth}{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _nextcloud_browse(raw_path, cfg=None):
    cfg = cfg or _nextcloud_settings()
    if not cfg["enabled"]:
        raise ValueError("Nextcloud is not enabled.")
    if not cfg["url"] or not cfg["username"] or not cfg["password"]:
        raise ValueError("Nextcloud config is incomplete.")

    webdav_base = _nextcloud_webdav_base(cfg["url"], cfg["username"])
    if not webdav_base:
        raise ValueError("Invalid Nextcloud URL.")

    root_path = _nextcloud_norm_path(cfg["root"])
    rel_path = _nextcloud_norm_path(raw_path)
    merged_path = root_path if rel_path == "/" else (root_path.rstrip("/") + rel_path)
    target_url = _nextcloud_join_webdav_url(webdav_base, merged_path)

    req = urllib.request.Request(target_url, method="PROPFIND")
    auth = f"{cfg['username']}:{cfg['password']}".encode("utf-8")
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode("ascii"))
    req.add_header("Depth", "1")
    req.add_header("Content-Type", "application/xml; charset=utf-8")
    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<d:propfind xmlns:d='DAV:'><d:prop><d:displayname/><d:resourcetype/></d:prop></d:propfind>"
    )
    ctx = ssl._create_unverified_context() if cfg.get("skip_ssl_verify") else None
    with urllib.request.urlopen(req, data=body, timeout=15, context=ctx) as resp:
        xml_text = resp.read().decode("utf-8", errors="ignore")

    ns = {"d": "DAV:"}
    payload = ET.fromstring(xml_text)
    responses = payload.findall("d:response", ns)
    entries = []
    current_path = rel_path
    dav_base_path = urlsplit(webdav_base).path.rstrip("/")

    for idx, node in enumerate(responses):
        href_node = node.find("d:href", ns)
        href = unquote((href_node.text or "").strip()) if href_node is not None else ""
        if not href:
            continue
        if idx == 0:
            continue

        href_path = urlsplit(href).path
        rel = href_path
        if rel.startswith(dav_base_path):
            rel = rel[len(dav_base_path):]
        abs_remote_path = _nextcloud_norm_path(rel)  # absolute path under /remote.php/dav/files/<user>
        # Map DAV absolute path to UI-relative path under configured root.
        if root_path != "/" and abs_remote_path.startswith(root_path + "/"):
            ui_rel = "/" + abs_remote_path[len(root_path) + 1 :]
        elif abs_remote_path == root_path:
            ui_rel = "/"
        else:
            ui_rel = abs_remote_path
        ui_rel = _nextcloud_norm_path(ui_rel)
        if ui_rel == current_path:
            continue

        rt = node.find("d:propstat/d:prop/d:resourcetype", ns)
        is_dir = rt is not None and rt.find("d:collection", ns) is not None
        display = node.find("d:propstat/d:prop/d:displayname", ns)
        name = (display.text or "").strip() if display is not None and display.text else ui_rel.rstrip("/").split("/")[-1]
        if not name:
            continue

        if is_dir:
            path_value = ui_rel
        else:
            file_webdav_url = _nextcloud_join_webdav_url(webdav_base, abs_remote_path)
            path_value = _url_with_basic_auth(file_webdav_url, cfg["username"], cfg["password"])
        entries.append({"name": name, "path": path_value, "is_dir": bool(is_dir)})

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = "/" if current_path in {"", "/"} else (os.path.dirname(current_path.rstrip("/")) or "/")
    return {"ok": True, "source": "nextcloud", "cwd": current_path, "parent": parent, "entries": entries}


def _nextcloud_cfg_from_request_args():
    base = _nextcloud_settings()
    url = (request.args.get("url") or "").strip()
    username = (request.args.get("username") or "").strip()
    password = (request.args.get("password") or "").strip()
    root = (request.args.get("root") or "").strip()
    skip_ssl = (request.args.get("skip_ssl_verify") or "").strip().lower()
    if url:
        base["url"] = url
    if username:
        base["username"] = username
    if password:
        base["password"] = password
    if root:
        base["root"] = root
    if skip_ssl in {"1", "true", "yes", "on"}:
        base["skip_ssl_verify"] = True
    elif skip_ssl in {"0", "false", "no", "off"}:
        base["skip_ssl_verify"] = False
    base["enabled"] = bool(base["url"] and base["username"] and base["password"])
    return base


def _nextcloud_cache_dir():
    target = os.path.join(Config.BASE_DIR, "runtime", "nextcloud_cache")
    os.makedirs(target, exist_ok=True)
    return target


def _format_size(num_bytes):
    size = float(max(0, int(num_bytes or 0)))
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.2f} {units[idx]}"


def _nextcloud_cache_stats():
    folder = _nextcloud_cache_dir()
    total_size = 0
    file_count = 0
    newest_mtime = 0
    oldest_mtime = 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        file_count += 1
        total_size += int(stat.st_size or 0)
        mtime = int(stat.st_mtime or 0)
        newest_mtime = max(newest_mtime, mtime)
        oldest_mtime = mtime if oldest_mtime == 0 else min(oldest_mtime, mtime)
    return {
        "folder": folder,
        "file_count": file_count,
        "total_size": total_size,
        "total_size_text": _format_size(total_size),
        "newest_at": datetime.fromtimestamp(newest_mtime).strftime("%Y-%m-%d %H:%M:%S") if newest_mtime else "-",
        "oldest_at": datetime.fromtimestamp(oldest_mtime).strftime("%Y-%m-%d %H:%M:%S") if oldest_mtime else "-",
    }


def _clear_nextcloud_cache():
    folder = _nextcloud_cache_dir()
    removed = 0
    reclaimed = 0
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            reclaimed += int(os.path.getsize(path) or 0)
            os.remove(path)
            removed += 1
        except OSError:
            continue
    return removed, reclaimed


def _normalize_hhmm(value, default="03:00"):
    text = str(value or "").strip()
    if not text:
        return default
    try:
        parts = text.split(":", 1)
        hour = int(parts[0])
        minute = int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except Exception:
        pass
    return default


def sync_nextcloud_cache_auto_clear_job(scheduler):
    job_id = "nextcloud_cache_auto_clear_job"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    enabled = bool(getattr(Config, "NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED", False))
    run_at = _normalize_hhmm(getattr(Config, "NEXTCLOUD_CACHE_AUTO_CLEAR_TIME", "03:00"), default="03:00")
    if not enabled:
        logger.info("Nextcloud cache auto clear disabled.")
        return

    hour, minute = [int(x) for x in run_at.split(":")]

    def _job():
        removed, reclaimed = _clear_nextcloud_cache()
        logger.info(
            "Nextcloud cache auto clear executed at %s: removed=%s reclaimed=%s",
            run_at,
            removed,
            reclaimed,
        )

    scheduler.add_job(
        _job,
        trigger="cron",
        hour=hour,
        minute=minute,
        id=job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("Nextcloud cache auto clear job scheduled at %s daily.", run_at)


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

    loop_mode = (form.get("loop_mode") or "once").strip()
    if loop_mode not in {"single", "list_loop", "single_loop", "once"}:
        loop_mode = "once"
    if loop_mode == "single":
        loop_mode = "once"
    try:
        loop_count = int(form.get("loop_count") or 0)
    except Exception:
        loop_count = 0
    loop_count = max(0, loop_count)
    items, playlist_paths = _parse_playlist_paths(form)
    if not items:
        raise ValueError("请至少配置 1 条播放内容。")
    content_path = items[0]
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
        content_path,
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
            content_path,
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
            content_path=content_path,
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
            content_path,
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
            "content_path": content_path,
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
    uptime_seconds = int((datetime.now() - SYSTEM_STARTED_AT).total_seconds())
    if uptime_seconds < 0:
        uptime_seconds = 0
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
                    "playlist_items": active_schedule.playlist_items,
                }
                if active_schedule
                else None
            ),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "system_uptime_seconds": uptime_seconds,
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
    # Bandwidth optimization: if browser supports gzip, send compressed screenshot bytes.
    accept_encoding = (request.headers.get("Accept-Encoding") or "").lower()
    if "gzip" in accept_encoding:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            compressed = gzip.compress(raw, compresslevel=6)
            resp = make_response(compressed)
            resp.headers["Content-Type"] = "image/bmp"
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Vary"] = "Accept-Encoding"
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            resp.headers["Content-Length"] = str(len(compressed))
            return resp
        except Exception:
            pass
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

    source = (request.args.get("source") or "local").strip().lower()
    raw_path = (request.args.get("path") or "").strip().strip('"')

    if source == "nextcloud":
        try:
            return jsonify(_nextcloud_browse(raw_path))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except urllib.error.HTTPError as exc:
            message = f"Nextcloud request failed: HTTP {exc.code}"
            if exc.code in {401, 403}:
                message = "Nextcloud authentication failed."
            return jsonify({"ok": False, "error": message}), 502
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Nextcloud request failed: {exc}"}), 502

    if not raw_path:
        roots = _list_windows_roots()
        return jsonify(
            {
                "ok": True,
                "source": "local",
                "cwd": "",
                "parent": "",
                "entries": [{"name": root, "path": root, "is_dir": True} for root in roots],
            }
        )

    target = os.path.abspath(os.path.expanduser(raw_path))
    if os.path.isfile(target):
        target = os.path.dirname(target)

    if not os.path.exists(target):
        return jsonify({"ok": False, "error": "Path not found."}), 404
    if not os.path.isdir(target):
        return jsonify({"ok": False, "error": "Not a directory."}), 400

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
        return jsonify({"ok": False, "error": "Permission denied."}), 403

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = os.path.dirname(target.rstrip("\\/")) if target else ""
    if parent == target:
        parent = ""
    return jsonify({"ok": True, "source": "local", "cwd": target, "parent": parent, "entries": entries})


@main.route("/status")
@login_required
def legacy_status():
    if not (_has_permission("dashboard.view") or _has_permission("monitor.view")):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return jsonify(player.get_status())


@main.route("/api/nextcloud/test")
@login_required
@_permission_required("settings.manage")
def api_nextcloud_test():
    cfg = _nextcloud_cfg_from_request_args()
    try:
        result = _nextcloud_browse("/", cfg=cfg)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except urllib.error.HTTPError as exc:
        message = f"Nextcloud request failed: HTTP {exc.code}"
        if exc.code in {401, 403}:
            message = "Nextcloud authentication failed."
        return jsonify({"ok": False, "error": message}), 502
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Nextcloud request failed: {exc}"}), 502

    entries = result.get("entries") or []
    folders = [item for item in entries if item.get("is_dir")]
    return jsonify(
        {
            "ok": True,
            "cwd": result.get("cwd", "/"),
            "folder_count": len(folders),
            "entry_count": len(entries),
            "message": "Nextcloud connection is healthy.",
        }
    )


@main.route("/api/nextcloud/preview")
@login_required
@_permission_required("settings.manage")
def api_nextcloud_preview():
    cfg = _nextcloud_cfg_from_request_args()
    path = (request.args.get("path") or "/").strip() or "/"
    try:
        result = _nextcloud_browse(path, cfg=cfg)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except urllib.error.HTTPError as exc:
        message = f"Nextcloud request failed: HTTP {exc.code}"
        if exc.code in {401, 403}:
            message = "Nextcloud authentication failed."
        return jsonify({"ok": False, "error": message}), 502
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Nextcloud request failed: {exc}"}), 502

    entries = result.get("entries") or []
    return jsonify(
        {
            "ok": True,
            "cwd": result.get("cwd", "/"),
            "parent": result.get("parent", "/"),
            "entries": entries[:300],
        }
    )


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
        if form_action == "nextcloud_cache":
            cache_action = (request.form.get("cache_action") or "").strip()
            if cache_action == "clear":
                removed, reclaimed = _clear_nextcloud_cache()
                _append_operation_audit_log(
                    action="nextcloud_cache_clear",
                    success=True,
                    target_type="cache",
                    target_id="nextcloud",
                    detail=f"removed={removed}, reclaimed={reclaimed}",
                )
                flash(
                    f"Nextcloud 缓存已清理：删除 {removed} 个文件，释放 {_format_size(reclaimed)}。",
                    "success",
                )
            else:
                flash("未知缓存操作。", "error")
            return redirect(url_for("main.settings"))

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
        nextcloud_enabled = "nextcloud_enabled" in request.form
        nextcloud_url = (request.form.get("nextcloud_url") or "").strip()
        nextcloud_username = (request.form.get("nextcloud_username") or "").strip()
        nextcloud_password = (request.form.get("nextcloud_password") or "").strip()
        nextcloud_root = (request.form.get("nextcloud_root") or "/").strip() or "/"
        nextcloud_skip_ssl_verify = "nextcloud_skip_ssl_verify" in request.form
        nextcloud_cache_auto_clear_enabled = "nextcloud_cache_auto_clear_enabled" in request.form
        nextcloud_cache_auto_clear_time = _normalize_hhmm(
            request.form.get("nextcloud_cache_auto_clear_time"),
            default=_normalize_hhmm(getattr(Config, "NEXTCLOUD_CACHE_AUTO_CLEAR_TIME", "03:00"), default="03:00"),
        )
        if not nextcloud_root.startswith("/"):
            nextcloud_root = "/" + nextcloud_root
        if nextcloud_enabled and (not nextcloud_url or not nextcloud_username or not nextcloud_password):
            flash("启用 Nextcloud 前请完整填写地址、用户名和应用密码。", "error")
            return redirect(url_for("main.settings"))

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
        old_nc_enabled = "1" if _get_setting_bool(SETTING_KEY_NEXTCLOUD_ENABLED, Config.NEXTCLOUD_ENABLED) else "0"
        old_nc_url = _get_setting_str(SETTING_KEY_NEXTCLOUD_URL, Config.NEXTCLOUD_URL)
        old_nc_username = _get_setting_str(SETTING_KEY_NEXTCLOUD_USERNAME, Config.NEXTCLOUD_USERNAME)
        old_nc_password = _get_setting_str(SETTING_KEY_NEXTCLOUD_PASSWORD, Config.NEXTCLOUD_PASSWORD)
        old_nc_root = _get_setting_str(SETTING_KEY_NEXTCLOUD_ROOT, Config.NEXTCLOUD_ROOT)
        old_nc_skip_ssl = (
            "1" if _get_setting_bool(SETTING_KEY_NEXTCLOUD_SKIP_SSL_VERIFY, Config.NEXTCLOUD_SKIP_SSL_VERIFY) else "0"
        )
        old_nc_cache_auto_clear_enabled = (
            "1"
            if _get_setting_bool(
                SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED,
                Config.NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED,
            )
            else "0"
        )
        old_nc_cache_auto_clear_time = _normalize_hhmm(
            _get_setting_str(
                SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_TIME,
                Config.NEXTCLOUD_CACHE_AUTO_CLEAR_TIME,
            ),
            default="03:00",
        )
        _set_setting_bool(SETTING_KEY_NEXTCLOUD_ENABLED, nextcloud_enabled)
        _set_setting_str(SETTING_KEY_NEXTCLOUD_URL, nextcloud_url)
        _set_setting_str(SETTING_KEY_NEXTCLOUD_USERNAME, nextcloud_username)
        _set_setting_str(SETTING_KEY_NEXTCLOUD_PASSWORD, nextcloud_password)
        _set_setting_str(SETTING_KEY_NEXTCLOUD_ROOT, nextcloud_root)
        _set_setting_bool(SETTING_KEY_NEXTCLOUD_SKIP_SSL_VERIFY, nextcloud_skip_ssl_verify)
        _set_setting_bool(SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED, nextcloud_cache_auto_clear_enabled)
        _set_setting_str(SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_TIME, nextcloud_cache_auto_clear_time)

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
        Config.NEXTCLOUD_ENABLED = nextcloud_enabled
        Config.NEXTCLOUD_URL = nextcloud_url
        Config.NEXTCLOUD_USERNAME = nextcloud_username
        Config.NEXTCLOUD_PASSWORD = nextcloud_password
        Config.NEXTCLOUD_ROOT = nextcloud_root
        Config.NEXTCLOUD_SKIP_SSL_VERIFY = nextcloud_skip_ssl_verify
        Config.NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED = nextcloud_cache_auto_clear_enabled
        Config.NEXTCLOUD_CACHE_AUTO_CLEAR_TIME = nextcloud_cache_auto_clear_time

        try:
            controller.scheduler.reschedule_job(
                "monitor_capture_job",
                trigger="interval",
                seconds=max(2, int(monitor_interval)),
            )
        except Exception:
            pass
        try:
            sync_nextcloud_cache_auto_clear_job(controller.scheduler)
        except Exception as exc:
            logger.warning("Failed to sync nextcloud cache auto clear job: %s", exc)
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
        if old_nc_enabled != ("1" if nextcloud_enabled else "0"):
            _append_setting_audit_log(SETTING_KEY_NEXTCLOUD_ENABLED, old_nc_enabled, "1" if nextcloud_enabled else "0")
        if old_nc_url != nextcloud_url:
            _append_setting_audit_log(SETTING_KEY_NEXTCLOUD_URL, old_nc_url or "<empty>", nextcloud_url or "<empty>")
        if old_nc_username != nextcloud_username:
            _append_setting_audit_log(
                SETTING_KEY_NEXTCLOUD_USERNAME,
                old_nc_username or "<empty>",
                nextcloud_username or "<empty>",
            )
        if old_nc_password != nextcloud_password:
            _append_setting_audit_log(
                SETTING_KEY_NEXTCLOUD_PASSWORD,
                "<hidden>" if old_nc_password else "<empty>",
                "<hidden>" if nextcloud_password else "<empty>",
            )
        if old_nc_root != nextcloud_root:
            _append_setting_audit_log(SETTING_KEY_NEXTCLOUD_ROOT, old_nc_root or "<empty>", nextcloud_root or "<empty>")
        if old_nc_skip_ssl != ("1" if nextcloud_skip_ssl_verify else "0"):
            _append_setting_audit_log(
                SETTING_KEY_NEXTCLOUD_SKIP_SSL_VERIFY,
                old_nc_skip_ssl,
                "1" if nextcloud_skip_ssl_verify else "0",
            )
        if old_nc_cache_auto_clear_enabled != ("1" if nextcloud_cache_auto_clear_enabled else "0"):
            _append_setting_audit_log(
                SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED,
                old_nc_cache_auto_clear_enabled,
                "1" if nextcloud_cache_auto_clear_enabled else "0",
            )
        if old_nc_cache_auto_clear_time != nextcloud_cache_auto_clear_time:
            _append_setting_audit_log(
                SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_TIME,
                old_nc_cache_auto_clear_time,
                nextcloud_cache_auto_clear_time,
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
    nextcloud_enabled = _get_setting_bool(SETTING_KEY_NEXTCLOUD_ENABLED, Config.NEXTCLOUD_ENABLED)
    nextcloud_url = _get_setting_str(SETTING_KEY_NEXTCLOUD_URL, Config.NEXTCLOUD_URL)
    nextcloud_username = _get_setting_str(SETTING_KEY_NEXTCLOUD_USERNAME, Config.NEXTCLOUD_USERNAME)
    nextcloud_password = _get_setting_str(SETTING_KEY_NEXTCLOUD_PASSWORD, Config.NEXTCLOUD_PASSWORD)
    nextcloud_root = _get_setting_str(SETTING_KEY_NEXTCLOUD_ROOT, Config.NEXTCLOUD_ROOT) or "/"
    nextcloud_skip_ssl_verify = _get_setting_bool(
        SETTING_KEY_NEXTCLOUD_SKIP_SSL_VERIFY,
        Config.NEXTCLOUD_SKIP_SSL_VERIFY,
    )
    nextcloud_cache_auto_clear_enabled = _get_setting_bool(
        SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED,
        Config.NEXTCLOUD_CACHE_AUTO_CLEAR_ENABLED,
    )
    nextcloud_cache_auto_clear_time = _normalize_hhmm(
        _get_setting_str(
            SETTING_KEY_NEXTCLOUD_CACHE_AUTO_CLEAR_TIME,
            Config.NEXTCLOUD_CACHE_AUTO_CLEAR_TIME,
        ),
        default="03:00",
    )
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
    nextcloud_cache_stats = _nextcloud_cache_stats()
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
        nextcloud_enabled=nextcloud_enabled,
        nextcloud_url=nextcloud_url,
        nextcloud_username=nextcloud_username,
        nextcloud_password=nextcloud_password,
        nextcloud_root=nextcloud_root,
        nextcloud_skip_ssl_verify=nextcloud_skip_ssl_verify,
        nextcloud_cache_auto_clear_enabled=nextcloud_cache_auto_clear_enabled,
        nextcloud_cache_auto_clear_time=nextcloud_cache_auto_clear_time,
        nextcloud_cache_stats=nextcloud_cache_stats,
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
