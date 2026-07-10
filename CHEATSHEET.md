⚠️ **Все git-команды в этом проекте выполняются ТОЛЬКО вручную пользователем.** 
Cline не должен пытаться выполнить их сам — это вызывает зависание терминала.

# Шпаргалка — команды для ручного выполнения

> Команды, которые Cline не выполняет сам из-за известных багов терминала. 
> Выполнять вручную в терминале VS Code.

## Git

**Проверить статус:**
git status

**Закоммитить изменения:**
git add .
git commit -m "короткое описание изменений"

**Просмотреть историю коммитов:**
git log --oneline

**Работа с ветками:**
git checkout -b feature/название-ветки     # создать и перейти
git checkout master                         # вернуться на master
git merge feature/название-ветки            # слить ветку в master
git branch -d feature/название-ветки        # удалить отработанную ветку

**Запушить на GitHub:**
git push

**Отменить незакоммиченные изменения:**
git restore .

## Python

**Запуск приложения:**
D:\multi-exchange-arbitrage\venv\Scripts\python.exe main.py

**Установка зависимостей:**
D:\multi-exchange-arbitrage\venv\Scripts\pip.exe install -r requirements.txt

**Быстрый тест (создать tmp_test_*.py и запустить):**
D:\multi-exchange-arbitrage\venv\Scripts\python.exe tmp_test_название.py

**Просмотр лога:**
Get-Content D:\multi-exchange-arbitrage\logs\arbitrage_2026-07-09.log -Tail 50

**Активировать venv (если нужно):**
.\venv\Scripts\activate

## Прочее

**На случай зависания**
Ctrl+Shift+P → введи Developer: Reload Window   # перезапуск Cline
<!-- Место для дополнительных команд/заметок пользователя -->
