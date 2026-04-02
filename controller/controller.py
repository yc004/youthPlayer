import logging
from datetime import datetime

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from models import Schedule, db


logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, app, player, scheduler):
        self.app = app
        self.player = player
        self.scheduler = scheduler
        self.scheduled_jobs = {}
        self.current_schedule_id = None

    def refresh_schedules(self):
        self._clear_all_jobs()
        with self.app.app_context():
            schedules = (
                Schedule.query.filter_by(is_active=True)
                .order_by(Schedule.start_time.asc())
                .all()
            )
            for schedule in schedules:
                self._register_schedule_jobs(schedule)
            logger.info("已加载 %s 个有效时间表", len(schedules))

    def _clear_all_jobs(self):
        for schedule_id in list(self.scheduled_jobs.keys()):
            self._remove_schedule_jobs(schedule_id)

    def _register_schedule_jobs(self, schedule):
        if not schedule.is_active:
            return

        now = datetime.now()
        job_ids = []

        if schedule.is_weekly:
            day_set = sorted(schedule.weekly_day_set)
            if not day_set:
                logger.info("跳过周循环时间表(未选择星期): %s (ID=%s)", schedule.name, schedule.id)
                return

            cron_days = ",".join(str(day) for day in day_set)
            start_job_id = f"schedule_weekly_start_{schedule.id}"
            end_job_id = f"schedule_weekly_end_{schedule.id}"

            self.scheduler.add_job(
                func=self._execute_schedule,
                trigger=CronTrigger(
                    day_of_week=cron_days,
                    hour=schedule.start_time.hour,
                    minute=schedule.start_time.minute,
                ),
                args=[schedule.id],
                id=start_job_id,
                replace_existing=True,
                misfire_grace_time=120,
            )
            self.scheduler.add_job(
                func=self._finish_schedule,
                trigger=CronTrigger(
                    day_of_week=cron_days,
                    hour=schedule.end_time.hour,
                    minute=schedule.end_time.minute,
                ),
                args=[schedule.id],
                id=end_job_id,
                replace_existing=True,
                misfire_grace_time=120,
            )
            job_ids.extend([start_job_id, end_job_id])
        else:
            if schedule.start_time > now:
                start_job_id = f"schedule_start_{schedule.id}"
                self.scheduler.add_job(
                    func=self._execute_schedule,
                    trigger=DateTrigger(run_date=schedule.start_time),
                    args=[schedule.id],
                    id=start_job_id,
                    replace_existing=True,
                    misfire_grace_time=120,
                )
                job_ids.append(start_job_id)

            if schedule.end_time > now:
                end_job_id = f"schedule_end_{schedule.id}"
                self.scheduler.add_job(
                    func=self._finish_schedule,
                    trigger=DateTrigger(run_date=schedule.end_time),
                    args=[schedule.id],
                    id=end_job_id,
                    replace_existing=True,
                    misfire_grace_time=120,
                )
                job_ids.append(end_job_id)

        if job_ids:
            self.scheduled_jobs[schedule.id] = job_ids
            logger.info("已注册时间表任务: %s (ID=%s)", schedule.name, schedule.id)
        else:
            logger.info("跳过过期时间表: %s (ID=%s)", schedule.name, schedule.id)

    def _remove_schedule_jobs(self, schedule_id):
        job_ids = self.scheduled_jobs.pop(schedule_id, [])
        for job_id in job_ids:
            try:
                self.scheduler.remove_job(job_id)
            except JobLookupError:
                continue

    def _execute_schedule(self, schedule_id):
        with self.app.app_context():
            schedule = db.session.get(Schedule, schedule_id)
            if not schedule or not schedule.is_active:
                logger.warning("执行时间表时未找到有效记录: %s", schedule_id)
                return False
            return self._play_schedule(schedule, source="schedule")

    def _finish_schedule(self, schedule_id):
        if self.current_schedule_id == schedule_id:
            logger.info("时间表结束，停止播放: %s", schedule_id)
            self.player.stop()
            self.current_schedule_id = None
            self.sync_active_schedule(force_restart=True)
            return True
        return False

    def _play_schedule(self, schedule, source="manual"):
        logger.info("开始执行时间表 [%s]: %s", source, schedule.name)
        self.player.set_screen(schedule.screen_index)

        if schedule.playlist_items:
            success = self.player.play_playlist(
                schedule.playlist_items,
                source_type=schedule.content_type,
                loop_mode=schedule.loop_mode or "list_loop",
                loop_count=schedule.loop_count or 0,
            )
            if success:
                self.current_schedule_id = schedule.id
            return success

        if schedule.content_type == "local":
            success = self.player.play_local(schedule.content_path)
        elif schedule.content_type == "nas":
            success = self.player.play_nas(schedule.content_path)
        elif schedule.content_type == "live":
            success = self.player.play_live(schedule.content_path)
        else:
            logger.error("未知内容类型: %s", schedule.content_type)
            return False

        if success:
            self.current_schedule_id = schedule.id
        return success

    def _find_active_schedule(self, now):
        schedules = (
            Schedule.query.filter(Schedule.is_active.is_(True))
            .order_by(Schedule.start_time.desc())
            .all()
        )
        return next((item for item in schedules if item.is_running_at(now)), None)

    def sync_active_schedule(self, force_restart=False):
        with self.app.app_context():
            active_schedule = self._find_active_schedule(datetime.now())
            if not active_schedule:
                self.current_schedule_id = None
                self.player.show_screensaver()
                return None

            if (
                force_restart
                or self.current_schedule_id != active_schedule.id
                or not self.player.is_healthy()
            ):
                self._play_schedule(active_schedule, source="sync")
            return active_schedule

    def add_schedule(self, schedule):
        try:
            db.session.add(schedule)
            db.session.commit()
            self._register_schedule_jobs(schedule)
            self.sync_active_schedule(force_restart=False)
            logger.info("添加新时间表: %s", schedule.name)
            return True
        except Exception as exc:
            db.session.rollback()
            logger.error("添加时间表失败: %s", exc)
            return False

    def update_schedule(self, schedule_id, **kwargs):
        try:
            schedule = db.session.get(Schedule, schedule_id)
            if not schedule:
                logger.error("时间表不存在: %s", schedule_id)
                return False

            for key, value in kwargs.items():
                if hasattr(schedule, key):
                    setattr(schedule, key, value)

            db.session.commit()
            self._remove_schedule_jobs(schedule_id)
            self._register_schedule_jobs(schedule)
            self.sync_active_schedule(force_restart=False)
            logger.info("更新时间表: %s", schedule.name)
            return True
        except Exception as exc:
            db.session.rollback()
            logger.error("更新时间表失败: %s", exc)
            return False

    def delete_schedule(self, schedule_id):
        try:
            schedule = db.session.get(Schedule, schedule_id)
            if not schedule:
                logger.error("时间表不存在: %s", schedule_id)
                return False

            self._remove_schedule_jobs(schedule_id)
            db.session.delete(schedule)
            db.session.commit()

            if self.current_schedule_id == schedule_id:
                self.player.stop()
                self.current_schedule_id = None
                self.sync_active_schedule(force_restart=True)

            logger.info("删除时间表: %s", schedule.name)
            return True
        except Exception as exc:
            db.session.rollback()
            logger.error("删除时间表失败: %s", exc)
            return False

    def get_schedules(self):
        return Schedule.query.order_by(Schedule.start_time.asc()).all()

    def get_current_schedule(self):
        if not self.current_schedule_id:
            return None
        return db.session.get(Schedule, self.current_schedule_id)

    def get_active_schedule_now(self):
        with self.app.app_context():
            return self._find_active_schedule(datetime.now())

    def get_runtime_summary(self):
        now = datetime.now()
        schedules = self.get_schedules()
        return {
            "schedule_count": len(schedules),
            "active_count": sum(1 for item in schedules if item.is_active),
            "running_count": sum(1 for item in schedules if item.is_running_now),
            "next_schedule": next(
                (
                    {
                        "id": item.id,
                        "name": item.name,
                        "start_time": item.start_time.strftime("%Y-%m-%d %H:%M"),
                        "screen_index": item.screen_index,
                    }
                    for item in schedules
                    if (item.is_weekly and item.is_active) or (item.start_time >= now and item.is_active)
                ),
                None,
            ),
        }

    def control_playback(self, action, schedule_id=None):
        try:
            if action == "start":
                if schedule_id:
                    schedule = db.session.get(Schedule, int(schedule_id))
                else:
                    schedule = self.sync_active_schedule(force_restart=False)
                    if not schedule:
                        schedule = (
                            Schedule.query.filter_by(is_active=True)
                            .order_by(Schedule.start_time.asc())
                            .first()
                        )
                if not schedule:
                    logger.warning("没有可播放的时间表")
                    return False
                return self._play_schedule(schedule, source="manual")

            if action == "stop":
                self.current_schedule_id = None
                return self.player.stop()

            if action == "pause":
                return self.player.pause()

            if action == "resume":
                return self.player.resume()

            if action == "web_play":
                return self.player.inject_web_play()

            if action == "web_fullscreen":
                return self.player.inject_web_fullscreen()

            if action == "web_play_fullscreen":
                play_ok = self.player.inject_web_play()
                fullscreen_ok = self.player.inject_web_fullscreen()
                return play_ok or fullscreen_ok

            logger.error("未知控制动作: %s", action)
            return False
        except Exception as exc:
            logger.error("控制播放失败: %s", exc)
            return False
