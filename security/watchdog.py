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
        # Fast-path check for Electron window closure.
        self.fast_check_interval = 1.0
        self.recovery_cooldown = Config.WATCHDOG_RECOVERY_COOLDOWN
        self._last_recovery_at = 0.0
        self._last_full_check_at = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.thread.start()
        logger.info("Watchdog started.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Watchdog stopped.")

    def _watchdog_loop(self):
        while self.running:
            try:
                now = time.time()
                self._check_electron_fast(now)
                if now - self._last_full_check_at >= float(self.check_interval):
                    self._check_status(now=now)
                    self._last_full_check_at = now
            except Exception as exc:
                logger.error("Watchdog check failed: %s", exc)
            time.sleep(self.fast_check_interval)

    def _check_electron_fast(self, now):
        if not self.player.expected_playing:
            return
        if self.player.current_backend != "electron":
            return
        if self.player.is_healthy():
            return
        # Prevent tight retry loops while still allowing near-immediate recovery.
        if now - self._last_recovery_at < 1.0:
            return

        active_schedule = self.controller.get_active_schedule_now()
        if not active_schedule:
            return

        self._last_recovery_at = now
        logger.warning(
            "Electron window not healthy, recovering active schedule immediately: %s",
            active_schedule.name,
        )
        self.controller.sync_active_schedule(force_restart=True)

    def _check_status(self, now=None):
        if now is None:
            now = time.time()

        status = self.player.get_status()
        logger.debug("Player status: %s", status)

        if Config.WINDOW_RECOVERY_ENABLED:
            self.player.ensure_foreground()

        active_schedule = self.controller.sync_active_schedule(force_restart=False)
        if not active_schedule:
            return

        if self.player.is_healthy():
            return

        if now - self._last_recovery_at < self.recovery_cooldown:
            return

        self._last_recovery_at = now
        logger.warning("Player unhealthy, recovering active schedule: %s", active_schedule.name)
        self.controller.sync_active_schedule(force_restart=True)
