# 控制模块
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from models import Schedule, db

logger = logging.getLogger(__name__)

class Controller:
    def __init__(self, player, scheduler):
        self.player = player
        self.scheduler = scheduler
        self.scheduled_jobs = {}
        self._load_schedules()
    
    def _load_schedules(self):
        """加载所有时间表"""
        try:
            from main import app
            with app.app_context():
                schedules = Schedule.query.filter_by(is_active=True).all()
                for schedule in schedules:
                    self._add_schedule_job(schedule)
                logger.info(f"加载了 {len(schedules)} 个时间表")
        except Exception as e:
            logger.error(f"加载时间表失败: {str(e)}")
    
    def _add_schedule_job(self, schedule):
        """添加时间表任务"""
        try:
            # 解析开始时间
            start_time = schedule.start_time
            trigger = CronTrigger(
                hour=start_time.hour,
                minute=start_time.minute,
                second=start_time.second
            )
            
            # 创建任务
            job_id = f"schedule_{schedule.id}"
            job = self.scheduler.add_job(
                func=self._execute_schedule,
                trigger=trigger,
                args=[schedule],
                id=job_id,
                replace_existing=True
            )
            
            self.scheduled_jobs[schedule.id] = job
            logger.info(f"添加时间表任务: {schedule.name} (ID: {schedule.id})")
        except Exception as e:
            logger.error(f"添加时间表任务失败: {str(e)}")
    
    def _execute_schedule(self, schedule):
        """执行时间表任务"""
        try:
            logger.info(f"执行时间表: {schedule.name}")
            
            # 设置屏幕
            self.player.set_screen(schedule.screen_index)
            
            # 根据内容类型播放
            if schedule.content_type == 'local':
                self.player.play_local(schedule.content_path)
            elif schedule.content_type == 'nas':
                self.player.play_nas(schedule.content_path)
            elif schedule.content_type == 'live':
                self.player.play_live(schedule.content_path)
            
            # 设置结束时间任务
            end_trigger = CronTrigger(
                hour=schedule.end_time.hour,
                minute=schedule.end_time.minute,
                second=schedule.end_time.second
            )
            
            end_job_id = f"end_{schedule.id}"
            self.scheduler.add_job(
                func=self.player.stop,
                trigger=end_trigger,
                id=end_job_id,
                replace_existing=True
            )
            
        except Exception as e:
            logger.error(f"执行时间表失败: {str(e)}")
    
    def add_schedule(self, schedule):
        """添加新的时间表"""
        try:
            db.session.add(schedule)
            db.session.commit()
            self._add_schedule_job(schedule)
            logger.info(f"添加新时间表: {schedule.name}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"添加时间表失败: {str(e)}")
            return False
    
    def update_schedule(self, schedule_id, **kwargs):
        """更新时间表"""
        try:
            schedule = Schedule.query.get(schedule_id)
            if not schedule:
                logger.error(f"时间表不存在: {schedule_id}")
                return False
            
            # 更新字段
            for key, value in kwargs.items():
                if hasattr(schedule, key):
                    setattr(schedule, key, value)
            
            db.session.commit()
            
            # 重新添加任务
            if schedule.id in self.scheduled_jobs:
                self.scheduler.remove_job(self.scheduled_jobs[schedule.id].id)
            
            if schedule.is_active:
                self._add_schedule_job(schedule)
            
            logger.info(f"更新时间表: {schedule.name}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"更新时间表失败: {str(e)}")
            return False
    
    def delete_schedule(self, schedule_id):
        """删除时间表"""
        try:
            schedule = Schedule.query.get(schedule_id)
            if not schedule:
                logger.error(f"时间表不存在: {schedule_id}")
                return False
            
            # 移除任务
            if schedule.id in self.scheduled_jobs:
                self.scheduler.remove_job(self.scheduled_jobs[schedule.id].id)
                del self.scheduled_jobs[schedule.id]
            
            db.session.delete(schedule)
            db.session.commit()
            logger.info(f"删除时间表: {schedule.name}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"删除时间表失败: {str(e)}")
            return False
    
    def get_schedules(self):
        """获取所有时间表"""
        try:
            return Schedule.query.all()
        except Exception as e:
            logger.error(f"获取时间表失败: {str(e)}")
            return []
    
    def control_playback(self, action):
        """控制播放"""
        try:
            if action == 'start':
                # 简单实现：播放第一个激活的时间表内容
                from main import app
                with app.app_context():
                    schedule = Schedule.query.filter_by(is_active=True).first()
                    if schedule:
                        # 设置屏幕
                        self.player.set_screen(schedule.screen_index)
                        
                        # 根据内容类型播放
                        if schedule.content_type == 'local':
                            return self.player.play_local(schedule.content_path)
                        elif schedule.content_type == 'nas':
                            return self.player.play_nas(schedule.content_path)
                        elif schedule.content_type == 'live':
                            return self.player.play_live(schedule.content_path)
                    else:
                        logger.error("没有找到激活的时间表")
                        return False
            elif action == 'stop':
                return self.player.stop()
            elif action == 'pause':
                return self.player.pause()
            elif action == 'resume':
                return self.player.resume()
            else:
                logger.error(f"未知的控制动作: {action}")
                return False
        except Exception as e:
            logger.error(f"控制播放失败: {str(e)}")
            return False