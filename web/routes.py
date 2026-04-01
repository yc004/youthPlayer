# Web路由
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required, login_user, logout_user, current_user
from models import User, Schedule, db
from datetime import datetime

main = Blueprint('main', __name__)

# 延迟导入，避免循环导入
player = None
controller = None

def init_routes(app):
    global player, controller
    from main import player as p, controller as c
    player = p
    controller = c

# 登录页面
@main.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:  # 实际应用中应该使用密码哈希
            login_user(user)
            return redirect(url_for('main.index'))
    return render_template('login.html')

# 登出
@main.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

# 主页
@main.route('/')
@login_required
def index():
    status = player.get_status()
    schedules = controller.get_schedules()
    return render_template('index.html', status=status, schedules=schedules)

# 播放控制
@main.route('/control', methods=['POST'])
@login_required
def control():
    action = request.form['action']
    result = controller.control_playback(action)
    # 重定向回主页，避免显示JSON响应
    return redirect(url_for('main.index'))

# 添加时间表
@main.route('/schedule/add', methods=['POST'])
@login_required
def add_schedule():
    data = request.form
    schedule = Schedule(
        name=data['name'],
        start_time=datetime.strptime(data['start_time'], '%Y-%m-%dT%H:%M'),
        end_time=datetime.strptime(data['end_time'], '%Y-%m-%dT%H:%M'),
        content_type=data['content_type'],
        content_path=data['content_path'],
        screen_index=int(data['screen_index']),
        is_active=True
    )
    result = controller.add_schedule(schedule)
    return redirect(url_for('main.index'))

# 更新时间表
@main.route('/schedule/update/<int:schedule_id>', methods=['POST'])
@login_required
def update_schedule(schedule_id):
    data = request.form
    kwargs = {
        'name': data['name'],
        'start_time': datetime.strptime(data['start_time'], '%Y-%m-%dT%H:%M'),
        'end_time': datetime.strptime(data['end_time'], '%Y-%m-%dT%H:%M'),
        'content_type': data['content_type'],
        'content_path': data['content_path'],
        'screen_index': int(data['screen_index']),
        'is_active': 'is_active' in data
    }
    result = controller.update_schedule(schedule_id, **kwargs)
    return redirect(url_for('main.index'))

# 删除时间表
@main.route('/schedule/delete/<int:schedule_id>')
@login_required
def delete_schedule(schedule_id):
    result = controller.delete_schedule(schedule_id)
    return redirect(url_for('main.index'))

# 获取播放状态
@main.route('/status')
def get_status():
    status = player.get_status()
    return jsonify(status)

# 管理用户
@main.route('/users')
@login_required
def manage_users():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    users = User.query.all()
    return render_template('users.html', users=users)

# 添加用户
@main.route('/user/add', methods=['POST'])
@login_required
def add_user():
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    data = request.form
    user = User(
        username=data['username'],
        password=data['password'],
        is_admin='is_admin' in data
    )
    db.session.add(user)
    db.session.commit()
    return redirect(url_for('main.manage_users'))

# 删除用户
@main.route('/user/delete/<int:user_id>')
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        return redirect(url_for('main.index'))
    user = User.query.get(user_id)
    if user and user.id != current_user.id:  # 不能删除自己
        db.session.delete(user)
        db.session.commit()
    return redirect(url_for('main.manage_users'))