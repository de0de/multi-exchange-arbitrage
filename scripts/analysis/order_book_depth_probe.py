"""
Замер: насколько глубоко надо собирать стакан, чтобы рекомендация по объёму
сделки была точной.

Зачем. `OrderBookCollector` собирает `limit=20` уровней. Кривая «прибыль от
размера сделки» (trade_size_curve.py) упирается в эту границу: глубина D и
проскальзывание I_full считаются по двадцати уровням, и всё, что лежит глубже,
модели не видно. Прежде чем менять `limit` на проде, надо узнать ЭМПИРИЧЕСКИ,
на каком уровне накопленный объём перекрывает интересующие нас суммы — а не
выбирать число наугад.

Скрипт запрашивает стакан напрямую у бирж с большим лимитом и показывает:
  - сколько уровней биржа реально отдаёт;
  - накопленный объём в USDT на 20-м, 50-м, 100-м уровне и на всей выдаче;
  - на каком уровне накопленный объём впервые перекрывает пороги
    (по умолчанию 2000 и 5000 USDT — медиана и 90-й перцентиль оптимума).

ТОЛЬКО ЧТЕНИЕ, публичные эндпоинты, ключи не нужны. Прод не затрагивается:
скрипт не импортирует код бота и ничего не пишет в БД. Но помни, что лимиты
бирж считаются НА IP — не запускать с сервера, пока там работает бот.

Запуск:
    python scripts/analysis/order_book_depth_probe.py
    python scripts/analysis/order_book_depth_probe.py --targets "COTIUSDT:Gate.io,MANUSDT:KuCoin"
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

# Пары взяты из категории OPEN (verify_persistent_spreads.py): цена
# подтверждена свечами, переводы открыты — то есть именно те, для которых
# рекомендация по объёму имеет практический смысл.
DEFAULT_TARGETS = [
    ("COTIUSDT", "Gate.io"), ("COTIUSDT", "Binance"),
    ("MANUSDT", "Gate.io"), ("MANUSDT", "KuCoin"),
    ("HPPUSDT", "KuCoin"), ("HPPUSDT", "Gate.io"),
    ("WKCUSDT", "Gate.io"), ("WKCUSDT", "MEXC"),
    ("BMTUSDT", "Gate.io"), ("BMTUSDT", "Binance"),
    ("BICOUSDT", "Gate.io"), ("BICOUSDT", "Binance"),
    ("TOWERUSDT", "MEXC"), ("TOWERUSDT", "KuCoin"),
]

QUOTES = ("USDT", "USDC", "BTC", "ETH")
MIN_INTERVAL = 0.25
_last = {}


def _throttle(host: str) -> None:
    wait = _last.get(host, 0.0) + MIN_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()


def _get(url: str, host: str):
    _throttle(host)
    safe = urllib.parse.quote(url, safe=":/?&=,._-~%")
    req = urllib.request.Request(safe, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except Exception as exc:
        return {"__error__": "{}: {}".format(type(exc).__name__, exc)}


def split_pair(pair: str) -> Optional[Tuple[str, str]]:
    for q in QUOTES:
        if pair.endswith(q) and len(pair) > len(q):
            return pair[:-len(q)], q
    return None


def fetch_levels(exchange: str, pair: str, want: int) -> Tuple[List[Tuple[float, float]], str]:
    """Возвращает (уровни asks [(цена, объём_в_базе)], примечание)."""
    parts = split_pair(pair)
    if not parts:
        return [], "не разобрать пару"
    base, quote = parts

    if exchange == "Binance":
        d = _get("https://api.binance.com/api/v3/depth?symbol={}{}&limit={}".format(
            base, quote, want), "binance")
        if isinstance(d, dict) and "asks" in d:
            return [(float(p), float(v)) for p, v in d["asks"]], ""
    elif exchange == "MEXC":
        d = _get("https://api.mexc.com/api/v3/depth?symbol={}{}&limit={}".format(
            base, quote, want), "mexc")
        if isinstance(d, dict) and "asks" in d:
            return [(float(p), float(v)) for p, v in d["asks"]], ""
    elif exchange == "Gate.io":
        d = _get("https://api.gateio.ws/api/v4/spot/order_book?currency_pair={}_{}&limit={}".format(
            base, quote, want), "gate")
        if isinstance(d, dict) and "asks" in d:
            return [(float(p), float(v)) for p, v in d["asks"]], ""
    elif exchange == "KuCoin":
        # Публично доступны только фиксированные срезы 20 и 100 уровней;
        # полный стакан (level2) требует ключа — это само по себе ограничение
        # для выбора limit на этой бирже.
        d = _get("https://api.kucoin.com/api/v1/market/orderbook/level2_100?symbol={}-{}".format(
            base, quote), "kucoin")
        data = d.get("data") if isinstance(d, dict) else None
        if data and data.get("asks"):
            return [(float(p), float(v)) for p, v in data["asks"]], "публичный максимум 100"
    elif exchange == "Bybit":
        d = _get("https://api.bybit.com/v5/market/orderbook?category=spot&symbol={}{}&limit={}".format(
            base, quote, min(want, 200)), "bybit")
        res = d.get("result") if isinstance(d, dict) else None
        if res and res.get("a"):
            return [(float(p), float(v)) for p, v in res["a"]], "максимум 200 (spot)"
    return [], "нет данных"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", default=None,
                    help="список ПАРА:БИРЖА через запятую; по умолчанию — набор OPEN-пар")
    ap.add_argument("--want", type=int, default=500, help="запрашиваемая глубина")
    ap.add_argument("--thresholds", default="1000,2000,5000",
                    help="пороги накопленного объёма в USDT")
    args = ap.parse_args()

    if args.targets:
        targets = []
        for item in args.targets.split(","):
            pair, _, ex = item.partition(":")
            targets.append((pair.strip(), ex.strip()))
    else:
        targets = DEFAULT_TARGETS
    thresholds = [float(x) for x in args.thresholds.split(",") if x.strip()]

    print("=" * 96)
    print("ЗАМЕР ГЛУБИНЫ СТАКАНА: сколько уровней нужно собирать")
    print("=" * 96)
    print("Запрашиваем до {} уровней. Колонки «на N ур.» — накопленный объём в USDT."
          .format(args.want))
    print()
    header = "{:<14}{:<9}{:>7}{:>11}{:>11}{:>11}{:>11}".format(
        "пара", "биржа", "уровн", "на 20 ур.", "на 50 ур.", "на 100 ур.", "вся выдача")
    header += "".join("{:>12}".format("ур.>${:.0f}".format(t)) for t in thresholds)
    print(header)
    print("-" * len(header))

    for pair, ex in targets:
        levels, note = fetch_levels(ex, pair, args.want)
        if not levels:
            print("{:<14}{:<9}{:>7}  {}".format(pair, ex, 0, note or "нет данных"))
            continue
        cum = []
        total = 0.0
        for price, vol in levels:
            total += price * vol
            cum.append(total)

        def at(n):
            return cum[min(n, len(cum)) - 1] if cum else 0.0

        row = "{:<14}{:<9}{:>7}{:>11.0f}{:>11.0f}{:>11.0f}{:>11.0f}".format(
            pair, ex, len(levels), at(20), at(50), at(100), cum[-1])
        for t in thresholds:
            idx = next((i + 1 for i, c in enumerate(cum) if c >= t), None)
            row += "{:>12}".format(idx if idx else "нет")
        print(row + ("  " + note if note else ""))

    print()
    print("Как читать: «ур.>$2000» — на каком уровне накопленный объём впервые")
    print("перекрывает $2000. Если там число больше 20, значит текущий limit=20")
    print("недооценивает глубину, и на сколько именно — видно из соседних колонок.")
    print("=" * 96)


if __name__ == "__main__":
    main()
