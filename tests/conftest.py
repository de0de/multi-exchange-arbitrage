import sys
from pathlib import Path

# Проект не упакован (нет setup.py/pyproject с package-config), поэтому
# добавляем корень репозитория в sys.path вручную — иначе `from config...`
# и `from src...` не резолвятся при запуске pytest из tests/.
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
