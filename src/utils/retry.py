import asyncio
import logging
import functools
from typing import Type, Callable, TypeVar, Any, Optional

T = TypeVar('T')

logger = logging.getLogger(__name__)

def async_retry(
    retries: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,),
    error_message: str = "Ошибка выполнения операции"
):
    """
    Декоратор для повторных попыток выполнения асинхронных функций при возникновении исключений.
    
    Args:
        retries: Максимальное количество повторных попыток
        delay: Начальная задержка между попытками (в секундах)
        backoff_factor: Множитель для увеличения задержки с каждой попыткой
        exceptions: Кортеж типов исключений, которые следует обрабатывать
        error_message: Базовое сообщение об ошибке для логирования
        
    Returns:
        Результат функции или последнее исключение, если все попытки не удались
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            current_delay = delay
            
            # Получаем название биржи из первого аргумента, если это объект с атрибутом exchange_name
            exchange_name = getattr(args[0], 'exchange_name', 'неизвестная биржа') if args else 'неизвестная биржа'
            
            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    # Подробное логирование с информацией о бирже, попытке и типе ошибки
                    logger.warning(
                        f"{error_message} для {exchange_name}. Попытка {attempt}/{retries}. "
                        f"Ошибка: {e.__class__.__name__}: {str(e)}. "
                        f"Повторная попытка через {current_delay:.1f} сек."
                    )
                    
                    if attempt < retries:
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logger.error(
                            f"Все {retries} попыток выполнения операции для {exchange_name} не удались. "
                            f"Последняя ошибка: {e.__class__.__name__}: {str(e)}"
                        )
            
            raise last_exception
        
        return wrapper
    
    return decorator 