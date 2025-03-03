import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, List, Any, Optional, Callable

logger = logging.getLogger(__name__)

class ExchangeHealthMonitor:
    """
    Класс для мониторинга здоровья соединений с биржами.
    Отслеживает задержки, ошибки и доступность API.
    """
    
    def __init__(self):
        self.exchange_stats: Dict[str, Dict[str, Any]] = {}
        self.error_thresholds = {
            'consecutive_errors': 3,  # Порог последовательных ошибок
            'error_rate': 0.3,        # Допустимый процент ошибок (30%)
            'max_latency': 2000       # Максимальная задержка в мс
        }
        self._running = False
        self._monitor_task = None
        
    def register_exchange(self, exchange_name: str):
        """Регистрирует новую биржу для мониторинга."""
        if exchange_name not in self.exchange_stats:
            self.exchange_stats[exchange_name] = {
                'status': 'unknown',          # unknown, healthy, degraded, down
                'last_successful_request': None,
                'last_error': None,
                'error_count': 0,
                'consecutive_errors': 0,
                'request_count': 0,
                'avg_latency': 0,
                'latencies': [],
                'errors': []
            }
            logger.info(f"Биржа {exchange_name} добавлена в мониторинг здоровья")
    
    def record_request(self, exchange_name: str, success: bool, latency_ms: float, 
                      error_message: Optional[str] = None):
        """Записывает результат запроса к бирже."""
        if exchange_name not in self.exchange_stats:
            self.register_exchange(exchange_name)
            
        stats = self.exchange_stats[exchange_name]
        stats['request_count'] += 1
        
        # Сохраняем только последние 100 замеров латентности для экономии памяти
        stats['latencies'].append(latency_ms)
        if len(stats['latencies']) > 100:
            stats['latencies'].pop(0)
        
        # Обновляем среднюю задержку
        stats['avg_latency'] = sum(stats['latencies']) / len(stats['latencies']) if stats['latencies'] else 0
        
        if success:
            stats['last_successful_request'] = datetime.now()
            stats['consecutive_errors'] = 0
        else:
            stats['error_count'] += 1
            stats['consecutive_errors'] += 1
            stats['last_error'] = datetime.now()
            stats['errors'].append({
                'timestamp': datetime.now(),
                'message': error_message
            })
            # Ограничиваем хранение ошибок
            if len(stats['errors']) > 20:
                stats['errors'].pop(0)
        
        # Обновляем статус на основе метрик
        self._update_status(exchange_name)
        
    def _update_status(self, exchange_name: str):
        """Обновляет статус биржи на основе собранных метрик."""
        stats = self.exchange_stats[exchange_name]
        
        # Определяем статус на основе наших порогов
        if stats['consecutive_errors'] >= self.error_thresholds['consecutive_errors']:
            stats['status'] = 'down'
            logger.warning(f"Биржа {exchange_name} недоступна. {stats['consecutive_errors']} последовательных ошибок")
        elif stats['request_count'] > 0 and stats['error_count'] / stats['request_count'] > self.error_thresholds['error_rate']:
            stats['status'] = 'degraded'
            error_rate = stats['error_count'] / stats['request_count'] * 100
            logger.warning(f"Производительность биржи {exchange_name} снижена. Частота ошибок: {error_rate:.1f}%")
        elif stats['avg_latency'] > self.error_thresholds['max_latency']:
            stats['status'] = 'degraded'
            logger.warning(f"Высокая задержка для {exchange_name}: {stats['avg_latency']:.1f} мс")
        else:
            stats['status'] = 'healthy'
            
    def get_exchange_status(self, exchange_name: str) -> Dict[str, Any]:
        """Возвращает текущее состояние биржи."""
        if exchange_name not in self.exchange_stats:
            return {'status': 'unknown', 'message': 'Exchange not monitored'}
        
        return self.exchange_stats[exchange_name]
    
    def get_all_statuses(self) -> Dict[str, str]:
        """Возвращает статусы всех бирж."""
        return {name: stats['status'] for name, stats in self.exchange_stats.items()}
    
    async def start_monitoring(self, report_interval: int = 300):
        """Запускает периодический мониторинг и отчеты о состоянии."""
        if self._running:
            return
            
        self._running = True
        
        async def monitoring_loop():
            while self._running:
                try:
                    # Логируем статусы всех бирж
                    statuses = self.get_all_statuses()
                    healthy = [name for name, status in statuses.items() if status == 'healthy']
                    degraded = [name for name, status in statuses.items() if status == 'degraded']
                    down = [name for name, status in statuses.items() if status == 'down']
                    
                    logger.info(
                        f"Отчет о состоянии бирж: "
                        f"Доступно: {len(healthy)}, "
                        f"Снижена производительность: {len(degraded)}, "
                        f"Недоступно: {len(down)}"
                    )
                    
                    if degraded:
                        logger.warning(f"Биржи со сниженной производительностью: {', '.join(degraded)}")
                    if down:
                        logger.error(f"Недоступные биржи: {', '.join(down)}")
                        
                except Exception as e:
                    logger.error(f"Ошибка в мониторинге: {str(e)}")
                    
                await asyncio.sleep(report_interval)
        
        self._monitor_task = asyncio.create_task(monitoring_loop())
        logger.info(f"Запущен мониторинг здоровья бирж с интервалом отчетов {report_interval} сек")
    
    async def stop_monitoring(self):
        """Останавливает мониторинг."""
        if not self._running:
            return
            
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        
        logger.info("Мониторинг здоровья бирж остановлен")

# Создаем глобальный экземпляр монитора для использования во всем приложении
health_monitor = ExchangeHealthMonitor() 