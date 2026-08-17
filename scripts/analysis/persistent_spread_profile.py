"""
Профиль долгоживущих спредов: что это за пары и на чём они держатся.

Зачем. Калибровка (spread_window_calibration.py) показала, что поток
сигналов заполнен не мгновенным шумом, а спредами, висящими часами.
Прежде чем такие сигналы отсекать, нужно понять, ЧТО это: реальные
неторгуемые расхождения (закрытый вывод, мёртвая ликвидность), коллизии
тикеров ниже порога 20% — или всё-таки настоящие возможности, которые мы
просто не пробовали взять. Скрипт не отвечает на этот вопрос сам, он
готовит короткий список подозреваемых для проверки по внешним источникам.

ТОЛЬКО ЧТЕНИЕ архивов. Ни БД, ни бирж не трогает.

Запуск:
    python scripts/analysis/persistent_spread_profile.py \
        --path "E:/архив/multi-exchange-arbitrage/spread_history_2026-08-16.csv.gz" \
        --min-cycles 350 --top 40
"""
import argparse
import statistics
import sys
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, ".")
from scripts.analysis.spread_window_calibration import (  # noqa: E402
    build_series, load_from_archive,
)


def collect_persistent(
    paths: List[str],
    threshold: float,
    min_cycles: int,
    exclude_collisions: bool,
    max_rows: int = 5_000_000,
) -> tuple:
    """
    Список долгоживущих пар + шаг цикла. Вынесено отдельно, чтобы
    verify_persistent_spreads.py брал ровно тот же список, а не повторял
    логику отбора со своими отличиями.

    Возвращает (список словарей, медианный шаг цикла в секундах,
    число циклов в выборке).
    """
    rows = load_from_archive(paths, None, None, exclude_collisions, max_rows)
    grid, series = build_series(rows)
    if not grid:
        return [], 0.0, 0

    intervals = [grid[i] - grid[i - 1] for i in range(1, len(grid))]
    step = statistics.median(intervals) if intervals else 0.0

    stats: List[dict] = []
    for pair, ps in series.items():
        hits = [i for i, s in enumerate(ps.spread) if s >= threshold]
        if len(hits) < min_cycles:
            continue
        spreads = [ps.spread[i] for i in hits]
        combos: Dict[tuple, int] = defaultdict(int)
        for i in hits:
            combos[ps.combo[i]] += 1
        top_combo, top_combo_n = max(combos.items(), key=lambda kv: kv[1])
        # Момент, по которому потом сверяемся со свечами: середина
        # сигнальных циклов, а не край — чтобы попасть в устойчивую часть.
        mid_cycle = ps.cycles[hits[len(hits) // 2]]
        stats.append({
            "pair": pair,
            "cycles": len(hits),
            "hours": len(hits) * step / 3600.0,
            "coverage": len(ps.cycles) / len(grid),
            "median": statistics.median(spreads),
            "min": min(spreads),
            "max": max(spreads),
            "buy_exchange": top_combo[1],    # где покупаем (там был лучший ask)
            "sell_exchange": top_combo[0],   # где продаём (там был лучший bid)
            "combo": f"{top_combo[1]}→{top_combo[0]}",
            "combo_share": top_combo_n / len(hits),
            "n_combos": len(combos),
            "mid_timestamp": grid[mid_cycle],
        })
    stats.sort(key=lambda d: d["cycles"], reverse=True)
    return stats, step, len(grid)


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
    p.add_argument("--min-cycles", type=int, default=350,
                   help="минимальная суммарная длительность в циклах, чтобы попасть в список")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--max-rows", type=int, default=5_000_000)
    p.add_argument("--exclude-collisions", action="store_true",
                   help="убрать строки с suspected_collision=1 (спред >= 20%%) — "
                        "именно так считает spread_window_calibration.py по умолчанию")
    p.add_argument("--csv", default=None)
    args = p.parse_args()

    threshold = args.min_spread + args.fee_percent

    # По умолчанию коллизии НЕ исключаем: важно видеть, сколько среди
    # долгожителей помеченных подозрительными. С --exclude-collisions
    # получаем ровно ту популяцию, на которой считалась калибровка.
    stats, step, n_cycles = collect_persistent(
        args.path, threshold, args.min_cycles,
        args.exclude_collisions, args.max_rows)
    if not stats:
        sys.exit("Данных нет или ни одна пара не прошла порог длительности.")

    print("=" * 100)
    print(f"ДОЛГОЖИВУЩИЕ СПРЕДЫ: пары с >= {args.min_cycles} сигнальных циклов "
          f"(~{args.min_cycles * step / 3600:.1f} ч) за выборку")
    print(f"Циклов в выборке {n_cycles}, медианный цикл {step:.1f} с, "
          f"порог {threshold:.2f}%")
    print("=" * 100)
    print(f"{'пара':<18}{'часов':>7}{'спред мед.':>12}{'мин':>8}{'макс':>9}"
          f"{'связка (купить→продать)':>30}{'её доля':>9}{'связок':>8}")
    for d in stats[:args.top]:
        print(f"{d['pair']:<18}{d['hours']:>7.1f}{d['median']:>12.2f}"
              f"{d['min']:>8.2f}{d['max']:>9.2f}{d['combo']:>30}"
              f"{d['combo_share'] * 100:>8.0f}%{d['n_combos']:>8}")

    total_cycles = sum(d["cycles"] for d in stats)
    print()
    print(f"Пар в списке: {len(stats)}; суммарно {total_cycles} сигнальных циклов.")
    print("Спред 'мин' близкий к порогу — признак живого рынка; спред, годами")
    print("стоящий на одном значении (мин ≈ макс), — признак мёртвой пары.")

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(stats[0].keys()))
            w.writeheader()
            w.writerows(stats)
        print(f"\nПолный список: {args.csv} ({len(stats)} строк)")


if __name__ == "__main__":
    main()
