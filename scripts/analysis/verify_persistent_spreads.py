"""
Проверка долгоживущих спредов по внешним источникам: были ли они реальны
и можно ли их вообще исполнить.

Зачем. Калибровка показала, что поток сигналов заполнен спредами, живущими
часами. Соблазн отсечь их по длительности оказался ошибочным: выборочная
проверка (2026-08-16) показала, что цены РЕАЛЬНЫ (ZIL 17.25% подтверждён
свечами Binance и MEXC с точностью до сотых), а держатся такие разрывы на
заблокированных переводах — и блокировка есть НЕ ВЕЗДЕ. COTI и PYR имеют
открытые переводы, то есть это потенциально рабочие возможности, которые
фильтр по длительности выбросил бы вместе с мусором.

Скрипт делает то же самое, но по всему списку, а не по трём парам:
  1. Берёт список долгожителей (persistent_spread_profile.collect_persistent).
  2. Тянет часовые свечи обеих бирж вокруг момента сигнала и считает
     фактический разрыв цен — проверка "спред был реален".
  3. Тянет статус ввода/вывода монеты — проверка "спред исполним".
  4. Раскладывает пары по категориям (см. VERDICTS).

КЛЮЧИ. По умолчанию не нужны: свечи и статус переводов Gate.io/KuCoin
берутся с публичных эндпоинтов. Binance и MEXC статус переводов публично
не отдают — без ключей их ноги честно помечаются UNVERIFIABLE, а не
выдаются за проверенные. Если в .env заданы `BINANCE_API_KEY`/
`BINANCE_API_SECRET` и/или `MEXC_API_KEY`/`MEXC_API_SECRET`, скрипт
подтянет статус и по ним (по одному bulk-запросу на биржу).
Нужны ТОЛЬКО права на чтение: используемые эндпоинты
(`/sapi/v1/capital/config/getall`, `/api/v3/capital/config/getall`) ничего
не торгуют и не выводят. Ключи читаются из окружения и никуда не
печатаются — ни в лог, ни в сообщения об ошибках.

ЧТО НЕ ПОМОЖЕТ:
  - Демо/testnet-аккаунты. Они показывают фиктивные кошельки и на вопрос
    о реальном статусе сетей не отвечают в принципе.
  - Историческая глубина стакана. Её публично не отдаёт никто, поэтому
    вопрос "хватило бы ликвидности на $1000" здесь НЕ решается —
    подтверждается только факт расхождения цен.

ТОЛЬКО ЧТЕНИЕ: архивы с диска + публичные GET к биржам. Ни наша БД, ни
прод не затрагиваются.

Запуск:
    python scripts/analysis/verify_persistent_spreads.py \
        --path "E:/архив/multi-exchange-arbitrage/spread_history_2026-08-16.csv.gz" \
        --min-cycles 350 --csv verify.csv
"""
import argparse
import hashlib
import hmac
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, ".")
from scripts.analysis.persistent_spread_profile import collect_persistent  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv()          # ключи Binance/MEXC, если пользователь их завёл
except ImportError:
    pass

# Наши slug'и бирж -> человекочитаемые имена уже приходят из spread_history
# в виде display-имён ("Binance", "Gate.io", ...). Приводим к ключам API.
EXCHANGE_KEY = {
    "Binance": "binance",
    "KuCoin": "kucoin",
    "Gate.io": "gate",
    "MEXC": "mexc",
    "Bybit": "bybit",
}

# Котируемые валюты, которыми ограничен сканер (SpreadMonitor.allowed_quote_currencies).
# Нужны, чтобы разобрать "ZILUSDT" на базу и котировку: в spread_history
# отдельных колонок base/quote нет.
QUOTES = ("USDT", "USDC", "BTC", "ETH")

VERDICTS = {
    "OPEN": "цена подтверждена, перевод открыт — кандидат в реальные",
    "BLOCKED": "цена подтверждена, но перевод по нужному направлению закрыт",
    "UNVERIFIABLE": "цена подтверждена, статус перевода без ключей не узнать",
    "MISMATCH": "свечи не подтверждают разрыв — вопрос к нашим данным",
    "NO_DATA": "нет свечей (делистинг/другое имя символа)",
}

_last_call: Dict[str, float] = {}
MIN_INTERVAL = 0.22   # с, на хост — с запасом от любых публичных лимитов


def _throttle(host: str) -> None:
    now = time.time()
    wait = _last_call.get(host, 0.0) + MIN_INTERVAL - now
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.time()


def _get(url: str, host: str) -> Optional[object]:
    _throttle(host)
    # Тикеры бывают какие угодно, вплоть до иероглифов (龙虾USDT на MEXC) —
    # без квотирования urllib падает на encode('ascii') и роняет весь прогон.
    safe = urllib.parse.quote(url, safe=":/?&=,._-~%")
    req = urllib.request.Request(safe, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except Exception:
        # Одна кривая монета из двух сотен не должна обрывать прогон;
        # отсутствие ответа трактуется как "нет данных" и видно в отчёте.
        return None


def split_pair(std_pair: str) -> Optional[Tuple[str, str]]:
    for q in QUOTES:
        if std_pair.endswith(q) and len(std_pair) > len(q):
            return std_pair[:-len(q)], q
    return None


# --------------------------------------------------------------------------
# Свечи
# --------------------------------------------------------------------------

def klines_median(exchange: str, base: str, quote: str,
                  start: int, end: int) -> Optional[float]:
    """Медиана close за окно [start, end] в секундах. None — нет данных."""
    if exchange == "binance":
        d = _get(f"https://api.binance.com/api/v3/klines?symbol={base}{quote}"
                 f"&interval=1h&startTime={start * 1000}&endTime={end * 1000}",
                 "binance")
        vals = [float(k[4]) for k in d] if isinstance(d, list) else []
    elif exchange == "mexc":
        d = _get(f"https://api.mexc.com/api/v3/klines?symbol={base}{quote}"
                 f"&interval=60m&startTime={start * 1000}&endTime={end * 1000}",
                 "mexc")
        vals = [float(k[4]) for k in d] if isinstance(d, list) else []
    elif exchange == "gate":
        d = _get(f"https://api.gateio.ws/api/v4/spot/candlesticks"
                 f"?currency_pair={base}_{quote}&interval=1h&from={start}&to={end}",
                 "gate")
        # Gate: [timestamp, volume, close, high, low, open, ...]
        vals = [float(k[2]) for k in d] if isinstance(d, list) else []
    elif exchange == "kucoin":
        d = _get(f"https://api.kucoin.com/api/v1/market/candles?type=1hour"
                 f"&symbol={base}-{quote}&startAt={start}&endAt={end}", "kucoin")
        rows = (d or {}).get("data") if isinstance(d, dict) else None
        # KuCoin: [time, open, close, high, low, volume, turnover], новые сверху
        vals = [float(c[2]) for c in rows] if rows else []
    else:
        return None
    return statistics.median(vals) if vals else None


# --------------------------------------------------------------------------
# Статус переводов
# --------------------------------------------------------------------------

_transfer_cache: Dict[Tuple[str, str], Optional[Tuple[bool, bool]]] = {}

# Binance и MEXC отдают статус только по подписанному запросу, зато СРАЗУ
# по всем монетам — поэтому тянем один раз и кладём в кеш целиком.
# Ключи берутся из окружения и нигде не логируются. Их отсутствие — не
# ошибка: биржа просто останется в категории UNVERIFIABLE.
_bulk_cache: Dict[str, Optional[Dict[str, Tuple[bool, bool]]]] = {}

BULK_KEYS = {
    "binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET",
                "https://api.binance.com/sapi/v1/capital/config/getall",
                "X-MBX-APIKEY"),
    "mexc": ("MEXC_API_KEY", "MEXC_API_SECRET",
             "https://api.mexc.com/api/v3/capital/config/getall",
             "X-MEXC-APIKEY"),
}


def _load_bulk(exchange: str) -> Optional[Dict[str, Tuple[bool, bool]]]:
    """
    {монета: (можно_вывести, можно_ввести)} по подписанному bulk-эндпоинту.

    None — ключей нет или запрос не удался. Права нужны минимальные, только
    чтение: эндпоинт не торгует и не выводит, он лишь описывает монеты.
    """
    if exchange in _bulk_cache:
        return _bulk_cache[exchange]

    key_env, secret_env, url, header = BULK_KEYS[exchange]
    api_key, secret = os.getenv(key_env), os.getenv(secret_env)
    if not api_key or not secret:
        _bulk_cache[exchange] = None
        return None

    query = f"timestamp={int(time.time() * 1000)}&recvWindow=10000"
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    _throttle(exchange)
    req = urllib.request.Request(f"{url}?{query}&signature={sig}",
                                 headers={header: api_key,
                                          "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # Тело ошибки биржи диагностично (Binance -2015 = ключ/IP/права) и
        # секретов не содержит. URL с подписью и сам ключ НЕ печатаем.
        body = {}
        try:
            body = json.loads(e.read().decode())
            detail = f"код {body.get('code')}: {body.get('msg')}"
        except Exception:
            detail = f"HTTP {e.code}"
        print(f"  [{exchange}] отказ — {detail}")
        if str(body.get("code")) in ("-2015", "700003", "10007"):
            print(f"  [{exchange}] это обычно белый список IP или права ключа: "
                  f"проверь, что текущий внешний IP внесён в whitelist")
        _bulk_cache[exchange] = None
        return None
    except Exception as e:
        print(f"  [{exchange}] bulk-запрос не удался ({type(e).__name__}), "
              f"биржа останется UNVERIFIABLE")
        _bulk_cache[exchange] = None
        return None

    out: Dict[str, Tuple[bool, bool]] = {}
    if isinstance(data, list):
        for c in data:
            nets = c.get("networkList") or []
            out[c.get("coin", "").upper()] = (
                any(n.get("withdrawEnable") for n in nets),
                any(n.get("depositEnable") for n in nets),
            )
    _bulk_cache[exchange] = out or None
    print(f"  [{exchange}] статус переводов получен по {len(out)} монетам")
    return _bulk_cache[exchange]


def transfer_status(exchange: str, coin: str) -> Optional[Tuple[bool, bool]]:
    """
    (можно_вывести, можно_ввести) или None, если публично не узнать.

    Агрегируем по сетям: достаточно ОДНОЙ рабочей сети, чтобы направление
    считалось открытым. Это оптимистичная оценка — в реальном исполнении
    сеть у двух бирж должна ещё и совпасть (случай COTI: на KuCoin ERC20
    закрыт, а нативная сеть открыта).
    """
    key = (exchange, coin)
    if key in _transfer_cache:
        return _transfer_cache[key]

    result: Optional[Tuple[bool, bool]] = None
    if exchange == "gate":
        d = _get(f"https://api.gateio.ws/api/v4/spot/currencies/{coin}", "gate")
        if isinstance(d, dict) and "withdraw_disabled" in d:
            result = (not d.get("withdraw_disabled", False),
                      not d.get("deposit_disabled", False))
    elif exchange == "kucoin":
        d = _get(f"https://api.kucoin.com/api/v3/currencies/{coin}", "kucoin")
        data = (d or {}).get("data") if isinstance(d, dict) else None
        chains = (data or {}).get("chains") if data else None
        if chains:
            result = (any(c.get("isWithdrawEnabled") for c in chains),
                      any(c.get("isDepositEnabled") for c in chains))
    elif exchange in BULK_KEYS:
        bulk = _load_bulk(exchange)
        if bulk is not None:
            # Монеты нет в списке — это не "закрыто", а "не знаем":
            # тикер на бирже может называться иначе.
            result = bulk.get(coin.upper())

    _transfer_cache[key] = result
    return result


# --------------------------------------------------------------------------

def verify_one(rec: dict, window_hours: int, tolerance: float) -> dict:
    parts = split_pair(rec["pair"])
    out = dict(rec)
    out.update({"verdict": "NO_DATA", "actual_gap": None,
                "buy_price": None, "sell_price": None, "transfer_note": ""})
    if not parts:
        out["transfer_note"] = "не разобрать базу/котировку"
        return out
    base, quote = parts

    buy_ex = EXCHANGE_KEY.get(rec["buy_exchange"])
    sell_ex = EXCHANGE_KEY.get(rec["sell_exchange"])
    if not buy_ex or not sell_ex:
        out["transfer_note"] = "неизвестная биржа"
        return out

    mid = int(rec["mid_timestamp"])
    start, end = mid - window_hours * 1800, mid + window_hours * 1800
    buy_price = klines_median(buy_ex, base, quote, start, end)
    sell_price = klines_median(sell_ex, base, quote, start, end)
    out["buy_price"], out["sell_price"] = buy_price, sell_price
    if not buy_price or not sell_price:
        return out

    gap = (sell_price - buy_price) / buy_price * 100.0
    out["actual_gap"] = gap

    recorded = rec["median"]
    # Подтверждением считаем совпадение знака и порядка величины: свечи —
    # это цены сделок за час, наш спред — bid/ask в моменте, они не обязаны
    # совпадать до сотых (хотя по ZIL совпали).
    ok = gap > 0 and abs(gap - recorded) <= max(tolerance, recorded * 0.4)
    if not ok:
        out["verdict"] = "MISMATCH"
        return out

    # Исполнимость: вывести с биржи покупки, ввести на биржу продажи.
    wd = transfer_status(buy_ex, base)
    dep = transfer_status(sell_ex, base)
    notes = []
    blocked = False
    unknown = False
    if wd is None:
        notes.append(f"вывод с {rec['buy_exchange']}: нужен ключ")
        unknown = True
    elif not wd[0]:
        notes.append(f"вывод с {rec['buy_exchange']} ЗАКРЫТ")
        blocked = True
    if dep is None:
        notes.append(f"ввод на {rec['sell_exchange']}: нужен ключ")
        unknown = True
    elif not dep[1]:
        notes.append(f"ввод на {rec['sell_exchange']} ЗАКРЫТ")
        blocked = True

    out["transfer_note"] = "; ".join(notes) if notes else "обе стороны открыты"
    out["verdict"] = "BLOCKED" if blocked else ("UNVERIFIABLE" if unknown else "OPEN")
    return out


def main() -> None:
    try:
        # line_buffering=True обязателен: reconfigure() иначе возвращает
        # буферизацию даже под python -u, и при падении посреди прогона
        # весь уже посчитанный прогресс теряется вместе с буфером.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
    except (AttributeError, OSError):
        pass

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", nargs="+", required=True)
    p.add_argument("--min-spread", type=float, default=0.5)
    p.add_argument("--fee-percent", type=float, default=0.2)
    p.add_argument("--min-cycles", type=int, default=350)
    p.add_argument("--window-hours", type=int, default=4,
                   help="ширина окна свечей вокруг момента сигнала")
    p.add_argument("--tolerance", type=float, default=1.0,
                   help="допуск в п.п. при сверке разрыва со свечами")
    p.add_argument("--exclude-collisions", action="store_true", default=True)
    p.add_argument("--include-collisions", dest="exclude_collisions",
                   action="store_false")
    p.add_argument("--limit", type=int, default=None, help="проверить только N первых")
    p.add_argument("--csv", default=None)
    args = p.parse_args()

    threshold = args.min_spread + args.fee_percent
    stats, step, _ = collect_persistent(args.path, threshold, args.min_cycles,
                                        args.exclude_collisions)
    if not stats:
        sys.exit("Долгоживущих пар не найдено.")
    if args.limit:
        stats = stats[:args.limit]

    print(f"К проверке {len(stats)} пар. Свечи + статус переводов, "
          f"публичные эндпоинты, ~{MIN_INTERVAL:.2f} с между запросами к хосту.")
    print("-" * 100)

    results = []
    for i, rec in enumerate(stats, 1):
        r = verify_one(rec, args.window_hours, args.tolerance)
        results.append(r)
        gap = f"{r['actual_gap']:8.2f}" if r["actual_gap"] is not None else "     n/a"
        print(f"{i:>4}/{len(stats)} {r['pair']:<16} наш {r['median']:>8.2f}%  "
              f"свечи {gap}%  {r['verdict']:<13} {r['transfer_note']}")

    print("-" * 100)
    print("ИТОГО")
    for v, descr in VERDICTS.items():
        n = sum(1 for r in results if r["verdict"] == v)
        cyc = sum(r["cycles"] for r in results if r["verdict"] == v)
        total_cyc = sum(r["cycles"] for r in results) or 1
        print(f"  {v:<13} {n:>4} пар, {cyc:>7} сигн. циклов "
              f"({cyc / total_cyc * 100:5.1f}%) — {descr}")

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\nПодробности: {args.csv}")


if __name__ == "__main__":
    main()
