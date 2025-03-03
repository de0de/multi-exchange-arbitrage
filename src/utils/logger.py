import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime

def setup_logging(log_level=logging.INFO, log_dir='logs'):
    """
    Настраивает систему логирования с выводом в файл и в консоль.
    
    Args:
        log_level: Уровень логирования
        log_dir: Директория для хранения логов
    """
    # Создаем директорию для логов, если она не существует
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Генерируем имя файла лога с текущей датой
    current_date = datetime.now().strftime('%Y-%m-%d')
    log_file = os.path.join(log_dir, f'arbitrage_{current_date}.log')
    
    # Настраиваем формат логов
    log_format = logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Очищаем существующие обработчики, если они есть
    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)
    
    # Обработчик для файла с ротацией (максимум 10 файлов по 10 МБ)
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,  # 10 МБ
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(log_format)
    file_handler.setLevel(log_level)
    root_logger.addHandler(file_handler)
    
    # Обработчик для вывода в консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_format)
    console_handler.setLevel(log_level)
    root_logger.addHandler(console_handler)
    
    # Отдельные настройки для сторонних библиотек
    # Устанавливаем для них более высокий уровень, чтобы не засорять логи
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    
    logging.info("Система логирования инициализирована")
    return root_logger 