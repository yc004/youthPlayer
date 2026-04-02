# 安全模块 - 看门狗程序
import logging
import threading
import time

from config import Config


logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, player, controller):
        self.player = player
        self.controller = controller
        self.running = False
        self.thread = None
        self.check_interval = Config.WATCHDOG_INTERVAL
        self.recovery_cooldown = Config.WATCHDOG_RECOVERY_COOLDOWN
        self._last_recovery_at = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.thread.start()
        logger.info("看门狗启动成功")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("看门狗停止成功")

    def _watchdog_loop(self):
        while self.running:
            try:
                self._check_status()
            except Exception as exc:
                logger.error("看门狗检查失败: %s", exc)
            time.sleep(self.check_interval)

    def _check_status(self):
        status = self.player.get_status()
        logger.debug("播放器状态: %s", status)

        if Config.WINDOW_RECOVERY_ENABLED:
            self.player.ensure_foreground()

        active_schedule = self.controller.sync_active_schedule(force_restart=False)
        if not active_schedule:
            return

        if self.player.is_healthy():
            return

        now = time.time()
        if now - self._last_recovery_at < self.recovery_cooldown:
            return

        self._last_recovery_at = now
        logger.warning("检测到播放器异常，尝试恢复当前时间表: %s", active_schedule.name)
        self.controller.sync_active_schedule(force_restart=True)
