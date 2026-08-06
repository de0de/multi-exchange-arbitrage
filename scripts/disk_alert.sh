#!/usr/bin/env bash
#
# Проверка свободного места на корневом диске -> push-монитор Uptime Kuma.
#
# ПОЧЕМУ ОТДЕЛЬНЫМ systemd-ТАЙМЕРОМ, А НЕ ВНУТРИ БОТА: если бот упадёт или
# зависнет, проверка диска не должна умереть вместе с ним — место при этом
# продолжит кончаться (архиватор не отработает, PostgreSQL продолжит писать).
# Тот же класс ошибки уже был пойман с health_monitor (PLAN.md 5.4):
# мониторинг, живущий внутри наблюдаемого процесса, молчит именно тогда,
# когда он нужен.
#
# Побочная выгода: если сломается сам таймер, Kuma заметит и это — пинги
# перестанут приходить, монитор упадёт по heartbeat.
#
# Конфигурация — в /root/multi-exchange-arbitrage/.env (там же, где .env
# бота; файл в .gitignore, поэтому push-токен не попадает в публичный репо):
#   DISK_ALERT_PUSH_URL=http://127.0.0.1:3001/api/push/<token>
#   DISK_ALERT_THRESHOLD_GB=10
#
# 127.0.0.1, а не localhost: Kuma слушает только IPv4, localhost может
# отрезолвиться в ::1 и дать Connection refused.

set -uo pipefail

ENV_FILE="${DISK_ALERT_ENV_FILE:-/root/multi-exchange-arbitrage/.env}"
MOUNT_POINT="${DISK_ALERT_MOUNT:-/}"
DEFAULT_THRESHOLD_GB=10

read_env() {
    # Своя выборка вместо `source`: .env содержит значения с символами,
    # которые шелл попытался бы исполнить, и пароли, которые незачем
    # тащить в окружение этого скрипта.
    [ -r "$ENV_FILE" ] || return 0
    grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r'
}

PUSH_URL="$(read_env DISK_ALERT_PUSH_URL)"
THRESHOLD_GB="$(read_env DISK_ALERT_THRESHOLD_GB)"
[ -n "$THRESHOLD_GB" ] || THRESHOLD_GB="$DEFAULT_THRESHOLD_GB"

if [ -z "$PUSH_URL" ]; then
    echo "DISK_ALERT_PUSH_URL не задан в $ENV_FILE — проверка пропущена" >&2
    exit 0
fi

AVAIL_KB="$(df --output=avail -k "$MOUNT_POINT" 2>/dev/null | tail -1 | tr -d ' ')"
USED_PCT="$(df --output=pcent "$MOUNT_POINT" 2>/dev/null | tail -1 | tr -d ' %')"

if ! [ "$AVAIL_KB" -ge 0 ] 2>/dev/null; then
    # df не отработал — честнее сообщить об этом как о проблеме, чем
    # промолчать и оставить монитор зелёным на непроверенном диске.
    curl -sS -G --max-time 15 "$PUSH_URL" \
        --data-urlencode "status=down" \
        --data-urlencode "msg=Не удалось прочитать df по $MOUNT_POINT" \
        >/dev/null 2>&1
    exit 1
fi

AVAIL_MB=$(( AVAIL_KB / 1024 ))
AVAIL_GB="$(awk "BEGIN{printf \"%.1f\", $AVAIL_KB/1048576}")"
THRESHOLD_KB="$(awk "BEGIN{printf \"%d\", $THRESHOLD_GB*1048576}")"

if [ "$AVAIL_KB" -ge "$THRESHOLD_KB" ]; then
    STATUS="up"
    MSG="OK: свободно ${AVAIL_GB} ГБ (занято ${USED_PCT}%), порог ${THRESHOLD_GB} ГБ"
else
    STATUS="down"
    MSG="МАЛО МЕСТА: свободно ${AVAIL_GB} ГБ (занято ${USED_PCT}%), порог ${THRESHOLD_GB} ГБ"
fi

# ping= несёт свободное место в МЕГАБАЙТАХ. Это намеренный приём: Kuma
# рисует значение ping как график "response time", и таким образом в UI
# появляется график свободного места во времени — видно тренд, а не только
# факт срабатывания. Читать эту кривую как мегабайты, не как миллисекунды.
curl -sS -G --max-time 15 "$PUSH_URL" \
    --data-urlencode "status=$STATUS" \
    --data-urlencode "msg=$MSG" \
    --data-urlencode "ping=$AVAIL_MB" \
    >/dev/null

RC=$?
if [ $RC -ne 0 ]; then
    echo "Не удалось отправить push в Uptime Kuma (curl rc=$RC)" >&2
    exit $RC
fi

echo "$MSG (отправлено как $STATUS)"
