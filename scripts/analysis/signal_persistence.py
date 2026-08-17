"""
Живучесть НАСТОЯЩИХ сигналов — по arbitrage_opportunities, а не по spread_history.

Зачем отдельный скрипт. Калибровка (spread_window_calibration.py) считалась
по `spread_history`, а эта таблица пишется РАНЬШЕ проверок качества:
в `SpreadMonitor.scan()` агрегат best_bid/best_ask складывается до фильтра
рассинхрона ног (`max_leg_skew_seconds`), до фильтра по объёму и до вычета
комиссий — всё это применяется ниже, в цикле отбора кандидатов. То есть
`spread_history` — надмножество сигналов, и доли, посчитанные по нему,
к реальному потоку сигналов относятся лишь приблизительно.

Здесь считается то же самое по `arbitrage_opportunities`, где все фильтры
уже применены. Единица наблюдения — не пара, а КОМБИНАЦИЯ
(пара, биржа покупки, биржа продажи): именно она и есть сигнал.

ТОЛЬКО ЧТЕНИЕ архивов.

Запуск:
    python scripts/analysis/signal_persistence.py \
        --path "E:/архив/multi-exchange-arbitrage/arbitrage_opportunities_2026-08-16.csv.gz"
"""
import argparse
import csv
import glob
import gzip
import statistics
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple


def load(paths: List[str], exclude_collisions: bool, max_rows: int):
    files: List[str] = []
    for p in paths:
        files.extend(sorted(glob.glob(p)))
    if not files:
        sys.exit(f"По маске {paths} файлов не найдено.")

    combos: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    spreads: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    stamps = set()
    seen = 0
    slippage_yes = 0

    for path in files:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if exclude_collisions and row.get("suspected_collision") in ("1", "True", "t"):
                    continue
                try:
                    ts = float(row["timestamp"])
                    net = float(row["net_spread_percent"])
                except (ValueError, KeyError, TypeError):
                    continue
                seen += 1
                if seen > max_rows:
                    sys.exit(f"Больше {max_rows} строк — сузьте выборку.")
                if row.get("slippage_available") in ("1", "True", "t"):
                    slippage_yes += 1
                key = (row["standardized_pair"], row["exchange_buy"], row["exchange_sell"])
                combos[key].append(ts)
                spreads[key].append(net)
                stamps.add(ts)
    return combos, spreads, sorted(stamps), seen, slippage_yes


def build_cycles(stamps: List[float], gap: float) -> Dict[float, int]:
    """
    {timestamp -> индекс цикла}, циклы восстановлены по разрывам во времени.

    В отличие от spread_history, где весь проход пишется с одним `now`, в
    arbitrage_opportunities время проставляется КАЖДОЙ строке отдельно:
    `ArbitrageOpportunity.timestamp` — это `default_factory` в датаклассе,
    то есть момент создания объекта. Внутри одного скана строки расходятся
    на микросекунды, между сканами — на секунды, поэтому цикл определяется
    как группа отметок, разделённая паузой больше `gap`.
    """
    out: Dict[float, int] = {}
    idx = 0
    prev = None
    for ts in stamps:
        if prev is not None and ts - prev > gap:
            idx += 1
        out[ts] = idx
        prev = ts
    return out


def runs_of(cycle_idx: List[int]) -> List[int]:
    """Длины серий подряд идущих циклов."""
    if not cycle_idx:
        return []
    out, cur = [], 1
    for a, b in zip(cycle_idx, cycle_idx[1:]):
        if b == a + 1:
            cur += 1
        else:
            out.append(cur)
            cur = 1
    out.append(cur)
    return out


def pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:5.1f}%" if whole else "    —"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
    except (AttributeError, OSError):
        pass

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", nargs="+", required=True)
    p.add_argument("--exclude-collisions", action="store_true", default=True)
    p.add_argument("--include-collisions", dest="exclude_collisions",
                   action="store_false")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--max-rows", type=int, default=8_000_000)
    p.add_argument("--cycle-gap", type=float, default=2.0,
                   help="пауза в секундах, разделяющая два цикла сканирования")
    args = p.parse_args()

    combos, spreads, grid, total, slip = load(args.path, args.exclude_collisions,
                                              args.max_rows)
    if not grid:
        sys.exit("Данных нет.")
    index = build_cycles(grid, args.cycle_gap)
    n_cycles = max(index.values()) + 1
    # Шаг цикла считаем по началам циклов, а не по соседним строкам:
    # внутри цикла строки идут вплотную и медиана по ним равна нулю.
    starts = [ts for i, ts in enumerate(grid) if i == 0 or index[ts] != index[grid[i - 1]]]
    intervals = [starts[i] - starts[i - 1] for i in range(1, len(starts))]
    step = statistics.median(intervals) if intervals else 0.0

    print("=" * 92)
    print("ЖИВУЧЕСТЬ НАСТОЯЩИХ СИГНАЛОВ (arbitrage_opportunities)")
    print("=" * 92)
    print(f"Строк-сигналов:        {total}")
    print(f"Циклов:                {n_cycles}")
    print(f"Медианный цикл:        {step:.1f} с")
    print(f"Уникальных комбинаций: {len(combos)} (пара + биржа покупки + биржа продажи)")
    print(f"Уникальных пар:        {len({k[0] for k in combos})}")
    print(f"Со слиппеджем из стакана: {slip} ({pct(slip, total).strip()})")

    hist: Counter = Counter()
    truncated = 0
    per_combo: List[tuple] = []
    for key, tss in combos.items():
        idx = sorted({index[t] for t in tss})
        rr = runs_of(idx)
        # серии у краёв выборки обрезаны — в гистограмму не идут
        edge = (idx[0] == 0, idx[-1] == n_cycles - 1)
        for n, r in enumerate(rr):
            first = (n == 0 and edge[0])
            last = (n == len(rr) - 1 and edge[1])
            if first or last:
                truncated += 1
            else:
                hist[r] += 1
        per_combo.append((key, len(idx), statistics.median(spreads[key]), max(rr)))

    tot_runs = sum(hist.values())
    tot_cyc = sum(k * v for k, v in hist.items())

    print()
    print("-" * 92)
    print("РАСПРЕДЕЛЕНИЕ ДЛИН СЕРИЙ (подряд идущие циклы одной комбинации)")
    print("-" * 92)
    print(f"{'длина':>8}{'серий':>9}{'доля серий':>12}{'циклов':>9}{'доля циклов':>13}{'~сек':>8}")
    shown = 0
    for length in sorted(hist):
        if shown >= 15:
            break
        n = hist[length]
        print(f"{length:>8}{n:>9}{pct(n, tot_runs):>12}{length * n:>9}"
              f"{pct(length * n, tot_cyc):>13}{length * step:>8.0f}")
        shown += 1

    print()
    for lim in (1, 7, 14, 70, 350):
        runs = sum(v for k, v in hist.items() if k <= lim)
        cyc = sum(k * v for k, v in hist.items() if k <= lim)
        print(f"серии <= {lim:>4} циклов (~{lim * step / 60:6.1f} мин): "
              f"{pct(runs, tot_runs)} серий, {pct(cyc, tot_cyc)} сигналов")
    for lim in (350, 1000):
        runs = sum(v for k, v in hist.items() if k >= lim)
        cyc = sum(k * v for k, v in hist.items() if k >= lim)
        print(f"серии >= {lim:>4} циклов (~{lim * step / 3600:5.1f} ч):   "
              f"{pct(runs, tot_runs)} серий, {pct(cyc, tot_cyc)} сигналов")
    print(f"\nОбрезано краем выборки и не учтено: {truncated} серий.")

    per_combo.sort(key=lambda x: -x[1])
    print()
    print("-" * 92)
    print(f"ТОП-{args.top} КОМБИНАЦИЙ ПО ДЛИТЕЛЬНОСТИ")
    print("-" * 92)
    print(f"{'пара':<16}{'циклов':>8}{'часов':>7}{'net спред':>11}  связка")
    for (pair, be, se), n, med, _ in per_combo[:args.top]:
        print(f"{pair:<16}{n:>8}{n * step / 3600:>7.1f}{med:>11.2f}  {be}→{se}")

    print()
    print("=" * 92)


if __name__ == "__main__":
    main()
