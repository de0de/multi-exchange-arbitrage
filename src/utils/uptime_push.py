"""
Push-пинг в Uptime Kuma — сигнал "главный цикл дошёл до конца итерации".

ПОЧЕМУ ПИНГ ЖИВЁТ ЗДЕСЬ И ВЫЗЫВАЕТСЯ ИЗ ГЛАВНОГО ЦИКЛА, А НЕ ИЗ
`health_monitor.py` (как предполагал исходный чек-лист PLAN.md 5.4):
`ExchangeHealthMonitor.monitoring_loop()` — независимая фоновая корутина
со своим `asyncio.sleep`, и она продолжает работать, когда главный цикл
стоит. Это не предположение: при тесте `to_thread()`-фикса архиватора
health_monitor отработал на расписании прямо во время архивации, пока
`scan()` не выполнялся (PLAN.md 5.1). Пинг оттуда горел бы зелёным во
время 73-минутного паралича главного цикла — ровно в том сценарии, ради
которого монитор и ставится. Пинг должен доказывать, что итерация ДОШЛА
ДО КОНЦА, а не что процесс жив.

Режим отказа выбран сознательно: мониторинг не должен влиять на сбор
данных. Любая ошибка пинга (Kuma лежит, сеть, таймаут) гасится и не
прерывает цикл — худшее, что может случиться, это ложный алерт о том,
что бот не пингует, при живом боте. Обратное (бот падает из-за
недоступности мониторинга) было бы абсурдом.
"""
import asyncio
import logging
import time
from typing import Optional

import aiohttp


class UptimePush:
    """Отправка heartbeat-пингов в push-монитор Uptime Kuma."""

    def __init__(
        self,
        url: str,
        timeout_seconds: float = 10.0,
        error_log_interval: float = 3600.0,
    ):
        self.url = (url or "").strip()
        self.timeout_seconds = timeout_seconds
        # Цикл идёт каждые ~15-20 с. Без этого интервала лежащая Kuma дала бы
        # тысячи одинаковых строк в сутки — тот же приём, что уже применён
        # для смены статуса бирж в health_monitor
        self.error_log_interval = error_log_interval
        self.logger = logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_error_log = 0.0
        self._suppressed = 0

        if not self.url:
            self.logger.info(
                "Uptime Kuma push отключён: UPTIME_KUMA_PUSH_URL не задан"
            )

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def ping(self):
        """
        Один heartbeat. Вызывать в КОНЦЕ итерации главного цикла.

        Ничего не делает, если URL не задан — так бот работает без Kuma
        (локальная разработка, откат мониторинга) без единой правки кода.
        """
        if not self.enabled:
            return

        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds)
                )
            async with self._session.get(self.url) as response:
                if response.status >= 400:
                    self._log_error(f"Uptime Kuma ответил HTTP {response.status}")
                else:
                    self._report_recovery()
        except asyncio.CancelledError:
            # Отмена при остановке сервиса — не ошибка пинга, пробрасываем.
            # (CancelledError наследует BaseException в Python 3.8+, то есть
            # except Exception ниже его и так не поймал бы — но явная ветка
            # защищает от случайной замены на except BaseException.)
            raise
        except Exception as e:
            self._log_error(f"{type(e).__name__}: {e}")

    def _log_error(self, message: str):
        now = time.time()
        self._suppressed += 1
        if now - self._last_error_log < self.error_log_interval:
            return

        suppressed = self._suppressed - 1
        tail = f" (ещё {suppressed} подобных за период)" if suppressed > 0 else ""
        self.logger.warning(
            f"Не удалось отправить heartbeat в Uptime Kuma: {message}{tail}. "
            f"Сбор данных продолжается, это отказ мониторинга, не бота."
        )
        self._last_error_log = now
        self._suppressed = 0

    def _report_recovery(self):
        if self._suppressed > 0:
            self.logger.info(
                f"Heartbeat в Uptime Kuma снова проходит "
                f"(было подавлено сообщений об ошибках: {self._suppressed})"
            )
            self._suppressed = 0

    async def close(self):
        """Закрывает HTTP-сессию — вызывать в finally главного цикла."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
