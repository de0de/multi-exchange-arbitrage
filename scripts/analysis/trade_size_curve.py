"""
Кривая «прибыль от размера сделки»: сколько денег имеет смысл заводить в одну
спот-спот сделку при реальной глубине стакана.

Зачем. Проверка 2026-08-18 показала, что найденные возможности реальны по цене
и в основном открыты по переводам, но ТОНКИЕ: глубина стакана в пределах
собираемых 20 уровней редко превышает пару тысяч долларов. Рабочий депозит
$1000 может оказаться просто больше этого рынка. Скрипт считает, при каком
объёме прибыль максимальна, по уже собранным данным — без единой сделки.

МОДЕЛЬ (важно понимать её допущения, иначе числа обманут).

Из `arbitrage_opportunities` по каждому сигналу известны три величины:
  s      = net_spread_percent           — спред уже за вычетом торговых комиссий
  D      = slippage_limited_volume_usdt — сколько USDT «влезает» в стакан
  I_full = buy_slippage + sell_slippage — проскальзывание при съедании ВСЕГО
                                          стакана (levels_consumed = 20)

I_full и D относятся к полному объёму стакана, потому что слиппедж считался с
`target_volume` = суточный объём (известный дефект `_calc_slippage`). Нам нужна
зависимость проскальзывания ОТ объёма, а не одна точка. Принимаем линейную
лестницу цен в стакане: при равномерных уровнях средневзвешенная цена растёт
пропорционально съеденной доле, то есть

    I(V) = I_full * V / D,   V <= D

Отсюда прибыль сделки объёмом V (F — фиксированная комиссия за вывод):

    P(V) = V * (s - I_full * V / D) / 100 - F

Это парабола ветвями вниз, максимум в точке

    V* = s * D / (2 * I_full)      (ограничен сверху глубиной D)

ЧЕГО МОДЕЛЬ НЕ УЧИТЫВАЕТ:
  - Глубина D — только 20 уровней (`OrderBookCollector`, limit=20). Реальный
    стакан глубже, поэтому оценка КОНСЕРВАТИВНА.
  - Линейность лестницы — упрощение, реальные уровни неравномерны.
  - Дрейф цены за время перевода монеты между биржами не моделируется вовсе.
  - Сигналы повторяются каждый цикл; торговать каждый нельзя. Поэтому
    агрегируем по КОМБИНАЦИИ (пара + биржа покупки + биржа продажи), беря
    медиану по циклам, а не суммируем строки.
  - Комиссия за вывод — плоский параметр, а не по монете и сети.
  - Исполнимость перевода не проверяется: часть комбинаций заблокирована
    (см. verify_persistent_spreads.py).

ТОЛЬКО ЧТЕНИЕ архивов.

Запуск:
    python scripts/analysis/trade_size_curve.py \
        --path "data/archive/arbitrage_opportunities_*.csv.gz"
"""
import argparse
import csv
import glob
import gzip
import json
import statistics
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

GRID = [25, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000]


def load(paths: List[str], max_rows: int):
    files: List[str] = []
    for p in paths:
        files.extend(sorted(glob.glob(p)))
    if not files:
        sys.exit("По маске {} файлов не найдено.".format(paths))

    acc: Dict[Tuple[str, str, str], List[Tuple[float, float, float]]] = defaultdict(list)
    seen = 0
    skipped = 0
    for path in files:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("suspected_collision") in ("1", "True", "t"):
                    continue
                if row.get("slippage_available") not in ("1", "True", "t"):
                    continue
                try:
                    s = float(row["net_spread_percent"])
                    depth = float(row["slippage_limited_volume_usdt"])
                    buy_i = json.loads(row["buy_slippage"])["price_impact_percent"]
                    sell_i = json.loads(row["sell_slippage"])["price_impact_percent"]
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    skipped += 1
                    continue
                impact = float(buy_i) + float(sell_i)
                if s <= 0 or depth <= 0 or impact <= 0:
                    skipped += 1
                    continue
                seen += 1
                if seen > max_rows:
                    sys.exit("Больше {} строк — сузьте выборку.".format(max_rows))
                key = (row["standardized_pair"], row["exchange_buy"], row["exchange_sell"])
                acc[key].append((s, depth, impact))
    return acc, seen, skipped


def profit(volume, spread, depth, impact, fee):
    """Прибыль сделки объёмом volume. Выше глубины стакана сделка невозможна."""
    if volume > depth:
        return float("-inf")
    return volume * (spread - impact * volume / depth) / 100.0 - fee


def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    idx = min(int(q * len(sorted_vals)), len(sorted_vals) - 1)
    return sorted_vals[idx]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", nargs="+", required=True)
    parser.add_argument("--withdraw-fee", type=float, default=0.5,
                        help="фиксированная комиссия за вывод, USD")
    parser.add_argument("--max-rows", type=int, default=8000000)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    acc, seen, skipped = load(args.path, args.max_rows)
    if not acc:
        sys.exit("Подходящих строк нет.")

    fee = args.withdraw_fee
    combos = []
    for key, vals in acc.items():
        spread = statistics.median(v[0] for v in vals)
        depth = statistics.median(v[1] for v in vals)
        impact = statistics.median(v[2] for v in vals)
        v_opt = min(spread * depth / (2 * impact), depth)
        combos.append({
            "pair": key[0],
            "buy": key[1],
            "sell": key[2],
            "cycles": len(vals),
            "spread": spread,
            "depth": depth,
            "impact_full": impact,
            "v_opt": v_opt,
            "p_opt": profit(v_opt, spread, depth, impact, fee),
        })

    print("=" * 88)
    print("КРИВАЯ «ПРИБЫЛЬ ОТ РАЗМЕРА СДЕЛКИ»")
    print("=" * 88)
    print("Строк-сигналов учтено: {} (отброшено {})".format(seen, skipped))
    print("Комбинаций (пара + биржа покупки + биржа продажи): {}".format(len(combos)))
    print("Комиссия за вывод в модели: ${:.2f}".format(fee))

    depths = sorted(c["depth"] for c in combos)
    print()
    print("ГЛУБИНА СТАКАНА (медиана по комбинации, USDT):")
    for q, label in ((0.10, "10%"), (0.25, "25%"), (0.50, "медиана"),
                     (0.75, "75%"), (0.90, "90%"), (0.99, "99%")):
        print("  {:>8}: {:>10.0f}".format(label, quantile(depths, q)))
    print("  {:>8}: {:>10.0f}".format("максимум", depths[-1]))

    print()
    print("-" * 88)
    print("КРИВАЯ: фиксированный размер сделки против результата")
    print("-" * 88)
    print("{:>9}{:>13}{:>8}{:>12}{:>12}{:>15}".format(
        "объём $", "прибыльных", "доля", "медиана $", "75-й перц", "сумма топ-20"))
    for volume in GRID:
        profits = [profit(volume, c["spread"], c["depth"], c["impact_full"], fee)
                   for c in combos]
        good = sorted((x for x in profits if x > 0), reverse=True)
        share = len(good) / len(combos) * 100
        median_p = statistics.median(good) if good else 0.0
        p75 = good[max(int(len(good) * 0.25) - 1, 0)] if good else 0.0
        print("{:>9}{:>13}{:>7.1f}%{:>12.2f}{:>12.2f}{:>15.2f}".format(
            volume, len(good), share, median_p, p75, sum(good[:20])))

    v_opts = sorted(c["v_opt"] for c in combos if c["p_opt"] > 0)
    print()
    if v_opts:
        print("ОПТИМАЛЬНЫЙ ОБЪЁМ ПО КОМБИНАЦИИ (среди прибыльных):")
        for q, label in ((0.25, "25%"), (0.50, "медиана"), (0.75, "75%"), (0.90, "90%")):
            print("  {:>8}: ${:>9.0f}".format(label, quantile(v_opts, q)))
        print("  прибыльных комбинаций: {} из {}".format(len(v_opts), len(combos)))

    print()
    print("ТОП-12 КОМБИНАЦИЙ ПО ПРИБЫЛИ В ОПТИМУМЕ:")
    print("{:<15}{:>8}{:>10}{:>9}{:>9}  связка".format(
        "пара", "спред%", "глубина$", "V*$", "приб.$"))
    for c in sorted(combos, key=lambda c: -c["p_opt"])[:12]:
        print("{:<15}{:>8.2f}{:>10.0f}{:>9.0f}{:>9.2f}  {}→{}".format(
            c["pair"], c["spread"], c["depth"], c["v_opt"], c["p_opt"],
            c["buy"], c["sell"]))

    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(combos[0].keys()))
            writer.writeheader()
            writer.writerows(combos)
        print("\nПодробности: {}".format(args.csv))
    print()
    print("=" * 88)


if __name__ == "__main__":
    main()
