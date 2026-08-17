"""
Офлайн-калибровка фильтров спот-спот сигнала на накопленной spread_history.

ЧТО ЭТО. Разведка стратегий Hummingbot (2026-08-16) показала, что таймера
"спред должен прожить N минут" там нет нигде; вместо него используются три
приёма. Скрипт считает на РЕАЛЬНЫХ данных, что каждый из них сделал бы с
нашим потоком сигналов, чтобы параметры (размер окна, множитель сигмы,
порог z-score) выбирались по факту, а не по предположению.

Считаются четыре блока:

  A. Живучесть сигнала — распределение длин серий подряд идущих циклов,
     в которых спред держится выше порога. Прямая проверка исходной
     гипотезы "одиночный выброс = шум": если серий длины 1 почти нет,
     фильтровать нечего.
  B. Консервативное окно (Hummingbot XEMM, ORDER_ADJUST_SAMPLE_WINDOW):
     за окно из W циклов берём худшие цены — max(ask) и min(bid) — и
     считаем спред по ним. Одиночный выброс гасится сам.
  C. z-score (Hummingbot stat_arb): сигнал не "спред > 0.5%", а "спред
     отклонился от собственной нормы этой пары больше чем на k сигм".
  D. Порог, зависящий от волатильности (Hummingbot cross_exchange_mining):
     порог = min_spread + m * sigma(mid-price за окно).

ТОЛЬКО ЧТЕНИЕ. Скрипт ничего не пишет в БД и не импортирует код бота
(кроме config.settings для DSN, и то опционально). Запускать на dev-БД
или на скачанных архивах, не на проде.

Запуск:
    python scripts/analysis/spread_window_calibration.py --source pg
    python scripts/analysis/spread_window_calibration.py \
        --source archive --path "data/archive/spread_history_*.csv.gz"

Полезные ключи:
    --min-spread / --fee-percent  порог сигнала (см. "Порог" ниже)
    --windows 2,3,4,6,8,12        размеры окна для блока B
    --detail-csv out.csv          выгрузка по парам для ручного разбора

ОБЪЁМ. Прод пишет порядка 2 млн строк spread_history в сутки (~26 МБ .gz).
Скрипт держит выборку в памяти целиком, поэтому считать сразу за две недели
не нужно и не выйдет: берите срез в 2-3 суток. Для архивов срез задаётся
просто маской файлов (они посуточные), для БД — ключами --since/--until.
Предохранитель --max-rows не даст молча съесть всю память.

ПОРОГ. spread_history хранит СЫРОЙ спред (до комиссий), а SpreadMonitor
сигналит по net = raw - комиссии обеих ног, сравнивая net с
MIN_SPREAD_PERCENT. Поэтому эквивалентный порог в терминах этой таблицы —
min_spread + fee_percent (по умолчанию 0.5 + 0.2 = 0.7%).

ОГРАНИЧЕНИЯ ДАННЫХ (важно при чтении результатов):
  1. Ряд разрежен. Строка пишется, только если спред >= history_min_spread
     (0.2%) ИЛИ раз в 300 с идёт полный снэпшот. Отсутствие пары в цикле
     трактуется как "спред был ниже 0.2%" — для консервативной агрегации
     это верно по смыслу (пропуск гасит сигнал), но отличить "спред был
     мал" от "пары не было в котировках" по этой таблице нельзя.
  2. best_bid и best_ask в одной строке могут быть с РАЗНЫХ пар бирж в
     разные циклы. Блок B из-за этого считается в двух вариантах, а доля
     окон со сменой комбинации бирж выводится отдельной диагностикой.
  3. Комиссии в пороге — плоские (--fee-percent), а не по каждой бирже.
"""
import argparse
import csv
import glob
import gzip
import math
import statistics
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

# Порог записи в spread_history (SpreadMonitor.history_min_spread_percent).
# Используется как верхняя граница для циклов, в которых пары нет.
DEFAULT_HISTORY_FLOOR = 0.2

COLUMNS = (
    "standardized_pair",
    "best_bid",
    "best_ask",
    "best_bid_exchange",
    "best_ask_exchange",
    "raw_spread_percent",
    "suspected_collision",
    "timestamp",
)


class PairSeries:
    """Ряд наблюдений по одной паре, выровненный по глобальной сетке циклов."""

    __slots__ = ("cycles", "bid", "ask", "spread", "combo", "_pos")

    def __init__(self):
        self.cycles: List[int] = []      # индексы циклов, где пара присутствует
        self.bid: List[float] = []
        self.ask: List[float] = []
        self.spread: List[float] = []
        self.combo: List[Tuple[str, str]] = []   # (биржа bid, биржа ask)
        self._pos: Optional[Dict[int, int]] = None

    def by_cycle(self) -> Dict[int, int]:
        """
        {индекс цикла -> позиция в списках}, считается один раз.

        Без кеша индекс перестраивался бы заново на каждый размер окна и
        каждый блок отчёта — на миллионах строк это доминирует над всей
        остальной работой скрипта.
        """
        if self._pos is None:
            self._pos = {c: i for i, c in enumerate(self.cycles)}
        return self._pos


# --------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------

def _row_ok(pair, bid, ask, spread, collision, exclude_collisions: bool) -> bool:
    if pair is None or bid is None or ask is None or spread is None:
        return False
    if bid <= 0 or ask <= 0:
        return False
    if exclude_collisions and collision:
        return False
    return True


def load_from_pg(
    dsn: str,
    since: Optional[float],
    until: Optional[float],
    exclude_collisions: bool,
    max_rows: int,
) -> Iterator[tuple]:
    """Читает spread_history серверным курсором (не буферизует всё в клиенте)."""
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg не установлен; используйте --source archive")

    where, params = [], []
    if since is not None:
        where.append("timestamp >= %s")
        params.append(since)
    if until is not None:
        where.append("timestamp <= %s")
        params.append(until)
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT standardized_pair, best_bid, best_ask,
               best_bid_exchange, best_ask_exchange,
               raw_spread_percent, suspected_collision, timestamp
        FROM spread_history
        {clause}
        ORDER BY timestamp
    """
    with psycopg.connect(dsn) as conn:
        conn.execute("SET default_transaction_read_only = on")
        with conn.cursor(name="spread_hist_cur") as cur:
            cur.itersize = 50000
            cur.execute(sql, params)
            seen = 0
            for pair, bid, ask, bid_ex, ask_ex, spread, coll, ts in cur:
                if not _row_ok(pair, bid, ask, spread, coll, exclude_collisions):
                    continue
                seen += 1
                if seen > max_rows:
                    sys.exit(
                        f"Прочитано больше {max_rows} строк. Сузьте окно "
                        f"(--since/--until) или поднимите --max-rows."
                    )
                yield pair, float(bid), float(ask), bid_ex or "", ask_ex or "", \
                    float(spread), float(ts)


def load_from_archive(
    patterns: Sequence[str],
    since: Optional[float],
    until: Optional[float],
    exclude_collisions: bool,
    max_rows: int,
) -> Iterator[tuple]:
    """Читает выгрузки HistoryArchiver (.csv.gz, первая строка — имена колонок)."""
    files: List[str] = []
    for pattern in patterns:
        files.extend(sorted(glob.glob(pattern)))
    if not files:
        sys.exit(f"По маске {patterns} не найдено ни одного файла.")

    seen = 0
    for path in files:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                sys.exit(f"{path}: в файле нет колонок {missing}")
            for row in reader:
                try:
                    bid = float(row["best_bid"]) if row["best_bid"] else None
                    ask = float(row["best_ask"]) if row["best_ask"] else None
                    spread = float(row["raw_spread_percent"]) if row["raw_spread_percent"] else None
                    ts = float(row["timestamp"])
                    coll = int(row["suspected_collision"] or 0)
                except (ValueError, KeyError):
                    continue
                pair = row["standardized_pair"]
                if not _row_ok(pair, bid, ask, spread, coll, exclude_collisions):
                    continue
                if since is not None and ts < since:
                    continue
                if until is not None and ts > until:
                    continue
                seen += 1
                if seen > max_rows:
                    sys.exit(f"Прочитано больше {max_rows} строк. Сузьте окно.")
                yield pair, bid, ask, row["best_bid_exchange"] or "", \
                    row["best_ask_exchange"] or "", spread, ts


def build_series(rows: Iterator[tuple]) -> Tuple[List[float], Dict[str, PairSeries]]:
    """
    Собирает глобальную сетку циклов и ряды по парам.

    Цикл определяется по timestamp: SpreadMonitor.scan() берёт time.time()
    один раз и проставляет его ВСЕМ строкам своего прохода, поэтому набор
    различных timestamp — это ровно набор циклов сканирования.
    """
    raw: Dict[str, List[tuple]] = defaultdict(list)
    stamps = set()
    # Пар бирж всего несколько десятков на тысячи строк — держим по одному
    # экземпляру каждого кортежа, иначе на многомиллионной выборке эти
    # кортежи весят больше, чем сами числа.
    combos: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for pair, bid, ask, bid_ex, ask_ex, spread, ts in rows:
        key = (bid_ex, ask_ex)
        combo = combos.setdefault(key, key)
        raw[pair].append((ts, bid, ask, combo, spread))
        stamps.add(ts)

    grid = sorted(stamps)
    index = {ts: i for i, ts in enumerate(grid)}

    series: Dict[str, PairSeries] = {}
    # Разбираем raw по одной паре и сразу освобождаем — иначе в пике в
    # памяти лежат и исходные кортежи, и уже построенные ряды.
    for pair in list(raw):
        items = raw.pop(pair)
        items.sort(key=lambda r: r[0])
        ps = PairSeries()
        for ts, bid, ask, combo, spread in items:
            ps.cycles.append(index[ts])
            ps.bid.append(bid)
            ps.ask.append(ask)
            ps.spread.append(spread)
            ps.combo.append(combo)
        series[pair] = ps
    return grid, series


# --------------------------------------------------------------------------
# Блок A — живучесть сигнала
# --------------------------------------------------------------------------

def signal_runs(ps: PairSeries, threshold: float, n_cycles: int) -> Tuple[List[int], int]:
    """
    Длины серий ПОДРЯД идущих циклов, где спред >= threshold.

    Подряд — по индексу цикла: разрыв в сетке (пара пропала из истории)
    рвёт серию, потому что пропуск означает спред ниже порога записи.

    Возвращает (длины завершённых серий, число обрезанных краем выборки).
    Серия, начинающаяся на нулевом цикле или заканчивающаяся на последнем,
    могла тянуться за границу данных — её истинная длина неизвестна, и в
    гистограмму она не идёт, иначе короткий срез набивает левый столбец.
    """
    runs: List[int] = []
    truncated = 0
    current = 0
    start_cycle = None
    prev_cycle = None

    def close(end_cycle: int) -> None:
        nonlocal truncated
        if start_cycle == 0 or end_cycle == n_cycles - 1:
            truncated += 1
        else:
            runs.append(current)

    for cycle, spread in zip(ps.cycles, ps.spread):
        hit = spread >= threshold
        contiguous = prev_cycle is not None and cycle == prev_cycle + 1
        if hit and contiguous and current > 0:
            current += 1
        elif hit:
            if current:
                close(prev_cycle)
            current = 1
            start_cycle = cycle
        elif current:
            close(prev_cycle)
            current = 0
        prev_cycle = cycle
    if current:
        close(prev_cycle)
    return runs, truncated


# --------------------------------------------------------------------------
# Блок B — консервативное окно
# --------------------------------------------------------------------------

def conservative_window(
    ps: PairSeries,
    window: int,
    threshold: float,
    floor: float,
) -> Tuple[int, int, int, int]:
    """
    Сколько базовых сигналов пары переживает окно размера `window`.

    Возвращает (оценимых сигналов, выжило по ценам, выжило по спредам,
    неоценимых сигналов).

    Вариант "по ценам" (как XEMM): нужны все `window` циклов подряд;
    берём max(ask) и min(bid) по окну и пересчитываем спред. Если хотя бы
    одного цикла окна нет — сигнал отбрасывается (данных нет = считаем,
    что спреда не было).

    Вариант "по спредам": min(spread) по окну, отсутствующий цикл даёт
    `floor` (порог записи истории) как верхнюю оценку. Работает всегда,
    но оценка мягче: min по спредам >= спред по худшим ценам.

    Сигналы из первых `window - 1` циклов выборки НЕ оцениваются и в
    знаменатель не идут: их окно уходит левее начала данных, и пропуск
    там означает "не знаем", а не "спред был низкий". Без этого срез
    выглядит тем строже, чем он короче.
    """
    pos = ps.by_cycle()
    base = survived_price = survived_spread = unevaluable = 0

    for cycle, spread in zip(ps.cycles, ps.spread):
        if spread < threshold:
            continue
        if cycle - window + 1 < 0:
            unevaluable += 1
            continue
        base += 1
        wanted = range(cycle - window + 1, cycle + 1)

        # --- по ценам
        idxs = [pos.get(c) for c in wanted]
        if all(j is not None for j in idxs):
            cons_ask = max(ps.ask[j] for j in idxs)
            cons_bid = min(ps.bid[j] for j in idxs)
            if cons_ask > 0 and (cons_bid - cons_ask) / cons_ask * 100.0 >= threshold:
                survived_price += 1

        # --- по спредам
        worst = min(ps.spread[pos[c]] if c in pos else floor for c in wanted)
        if worst >= threshold:
            survived_spread += 1

    return base, survived_price, survived_spread, unevaluable


def combo_stability(ps: PairSeries, window: int, threshold: float) -> Tuple[int, int]:
    """(окон проверено, окон со сменой пары бирж) — диагностика к блоку B."""
    pos = ps.by_cycle()
    checked = changed = 0
    for cycle, spread in zip(ps.cycles, ps.spread):
        if spread < threshold or cycle - window + 1 < 0:
            continue
        idxs = [pos.get(c) for c in range(cycle - window + 1, cycle + 1)]
        if any(j is None for j in idxs):
            continue
        checked += 1
        if len({ps.combo[j] for j in idxs}) > 1:
            changed += 1
    return checked, changed


# --------------------------------------------------------------------------
# Блок C — z-score
# --------------------------------------------------------------------------

def zscore_survivors(
    ps: PairSeries,
    lookback: int,
    ks: Sequence[float],
    threshold: float,
    floor: float,
    min_samples: int,
) -> Tuple[int, Dict[float, int], int]:
    """
    Сколько базовых сигналов переживает фильтр по z-score.

    Норма (mean/std) считается по `lookback` предыдущих циклов, ТЕКУЩИЙ в
    неё не входит — иначе выброс сам себя нормализует. Пропущенные циклы
    заполняются `floor`: без этого среднее завышено (в историю попадают
    только спреды >= 0.2%) и z-score систематически занижается.

    Возвращает (оценимых, {k: выжило}, пропущено из-за нехватки истории).
    Пропущенные в знаменатель не идут — иначе доли зависят от того, какая
    часть выборки пришлась на её начало.
    """
    pos = ps.by_cycle()
    base = 0
    survived = {k: 0 for k in ks}
    skipped = 0

    for cycle, spread in zip(ps.cycles, ps.spread):
        if spread < threshold:
            continue
        window = [
            ps.spread[pos[c]] if c in pos else floor
            for c in range(cycle - lookback, cycle)
            if c >= 0
        ]
        if len(window) < min_samples:
            skipped += 1
            continue
        mean = statistics.fmean(window)
        std = statistics.pstdev(window)
        if std == 0:
            skipped += 1
            continue
        base += 1
        z = (spread - mean) / std
        for k in ks:
            if z >= k:
                survived[k] += 1
    return base, survived, skipped


# --------------------------------------------------------------------------
# Блок D — порог, зависящий от волатильности
# --------------------------------------------------------------------------

def volatility_survivors(
    ps: PairSeries,
    window: int,
    multipliers: Sequence[float],
    threshold: float,
    min_samples: int,
) -> Tuple[int, Dict[float, int], int]:
    """
    Порог = threshold + m * sigma, где sigma — волатильность mid в процентах.

    sigma считается как в InstantVolatilityIndicator у Hummingbot:
    sqrt(sum(diff^2) / n) по ряду mid, затем нормируется на текущий mid.
    Разность соседних значений, а не отклонение от среднего — иначе тренд
    внутри окна выдаёт себя за волатильность.
    """
    pos = ps.by_cycle()
    base = 0
    survived = {m: 0 for m in multipliers}
    skipped = 0

    for cycle, spread in zip(ps.cycles, ps.spread):
        if spread < threshold:
            continue
        mids = [
            (ps.bid[pos[c]] + ps.ask[pos[c]]) / 2.0
            for c in range(cycle - window + 1, cycle + 1)
            if c in pos
        ]
        if len(mids) < min_samples or mids[-1] <= 0:
            skipped += 1
            continue
        base += 1
        diffs = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
        sigma_abs = math.sqrt(sum(d * d for d in diffs) / len(mids))
        sigma_pct = sigma_abs / mids[-1] * 100.0
        for m in multipliers:
            if spread >= threshold + m * sigma_pct:
                survived[m] += 1
    return base, survived, skipped


# --------------------------------------------------------------------------
# Отчёт
# --------------------------------------------------------------------------

def pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:5.1f}%" if whole else "    —"


def main() -> None:
    # Отчёт на русском, а консоль под Windows часто в cp866/cp1251 —
    # без этого вывод превращается в кракозябры при перенаправлении в файл.
    try:
        # line_buffering=True обязателен: reconfigure() иначе возвращает
        # буферизацию даже под python -u, и при падении посреди прогона
        # весь уже посчитанный прогресс теряется вместе с буфером.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
    except (AttributeError, OSError):
        pass

    p = argparse.ArgumentParser(
        description="Офлайн-калибровка фильтров спот-спот сигнала на spread_history.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--source", choices=("pg", "archive"), default="pg")
    p.add_argument("--dsn", default=None,
                   help="строка подключения; по умолчанию собирается из config.settings")
    p.add_argument("--path", nargs="+", default=["data/archive/spread_history_*.csv.gz"],
                   help="маски файлов для --source archive")
    p.add_argument("--since", type=float, default=None, help="unix-время, включительно")
    p.add_argument("--until", type=float, default=None, help="unix-время, включительно")
    p.add_argument("--min-spread", type=float, default=0.5,
                   help="MIN_SPREAD_PERCENT — как в настройках бота")
    p.add_argument("--fee-percent", type=float, default=0.2,
                   help="суммарная комиссия обеих ног; порог = min-spread + fee")
    p.add_argument("--history-floor", type=float, default=DEFAULT_HISTORY_FLOOR,
                   help="порог записи в spread_history: чем считать пропущенный цикл")
    p.add_argument("--windows", default="2,3,4,6,8,12",
                   help="размеры окна (в циклах) для блока B")
    p.add_argument("--zscore-lookback", type=int, default=60)
    p.add_argument("--zscore-k", default="1.5,2,2.5,3")
    p.add_argument("--vol-window", type=int, default=12)
    p.add_argument("--vol-multipliers", default="1,2,3")
    p.add_argument("--min-samples", type=int, default=5,
                   help="минимум наблюдений в окне для блоков C и D")
    p.add_argument("--include-collisions", action="store_true",
                   help="не отбрасывать строки с suspected_collision=1")
    p.add_argument("--max-rows", type=int, default=5_000_000)
    p.add_argument("--detail-csv", default=None,
                   help="выгрузить детализацию блока B по парам")
    args = p.parse_args()

    threshold = args.min_spread + args.fee_percent
    windows = [int(w) for w in args.windows.split(",") if w.strip()]
    ks = [float(k) for k in args.zscore_k.split(",") if k.strip()]
    ms = [float(m) for m in args.vol_multipliers.split(",") if m.strip()]

    if args.source == "pg":
        dsn = args.dsn
        if dsn is None:
            sys.path.insert(0, ".")
            from config import settings
            dsn = (f"host={settings.PG_HOST} port={settings.PG_PORT} "
                   f"dbname={settings.PG_DB} user={settings.PG_USER} "
                   f"password={settings.PG_PASSWORD}")
        rows = load_from_pg(dsn, args.since, args.until,
                            not args.include_collisions, args.max_rows)
    else:
        rows = load_from_archive(args.path, args.since, args.until,
                                 not args.include_collisions, args.max_rows)

    grid, series = build_series(rows)
    if not grid:
        sys.exit("Данных нет: spread_history пуста или всё отфильтровано.")

    intervals = [grid[i] - grid[i - 1] for i in range(1, len(grid))]
    step = statistics.median(intervals) if intervals else 0.0
    span = grid[-1] - grid[0]

    print("=" * 78)
    print("КАЛИБРОВКА ФИЛЬТРОВ СПОТ-СПОТ СИГНАЛА")
    print("=" * 78)
    print(f"Циклов:            {len(grid)}")
    print(f"Пар:               {len(series)}")
    print(f"Охват:             {span / 3600:.2f} ч ({span:.0f} с)")
    print(f"Медианный цикл:    {step:.1f} с")
    print(f"Порог сигнала:     {threshold:.2f}% сырого спреда "
          f"(min_spread {args.min_spread} + комиссии {args.fee_percent})")
    print(f"Пропущенный цикл:  считается спредом {args.history_floor}%")

    max_need = max(windows + [args.zscore_lookback, args.vol_window])
    if len(grid) < max_need * 3:
        print()
        print(f"!! ВНИМАНИЕ: циклов ({len(grid)}) мало для самого длинного окна "
              f"({max_need}). Результаты ниже — проверка работоспособности, "
              f"не калибровка. Нужны данные хотя бы за сутки.")

    # ---------------- A ----------------
    all_runs: Counter = Counter()
    truncated_runs = 0
    for ps in series.values():
        runs, truncated = signal_runs(ps, threshold, len(grid))
        truncated_runs += truncated
        for r in runs:
            all_runs[r] += 1
    total_runs = sum(all_runs.values())
    total_cycles_in_runs = sum(length * n for length, n in all_runs.items())

    print()
    print("-" * 78)
    print("A. ЖИВУЧЕСТЬ СИГНАЛА — сколько циклов подряд держится спред выше порога")
    print("-" * 78)
    if not total_runs:
        print("Сигналов выше порога нет вообще.")
    else:
        print(f"{'длина серии':>12} {'серий':>8} {'доля серий':>11} "
              f"{'циклов':>8} {'доля циклов':>12}   ~секунд")
        for length in sorted(all_runs):
            n = all_runs[length]
            print(f"{length:>12} {n:>8} {pct(n, total_runs):>11} "
                  f"{length * n:>8} {pct(length * n, total_cycles_in_runs):>12}"
                  f"   {length * step:.0f}")
        one_shot = all_runs.get(1, 0)
        print()
        print(f"Одиночных выбросов: {one_shot} из {total_runs} серий "
              f"({pct(one_shot, total_runs).strip()}), "
              f"{pct(one_shot, total_cycles_in_runs).strip()} всех сигнальных циклов.")
        print("Это и есть верхняя граница того, что может дать любой фильтр"
              " по длительности.")
        print(f"Обрезано краем выборки и не учтено: {truncated_runs} серий "
              f"(начинались на первом цикле или продолжались за последний).")

    # ---------------- B ----------------
    print()
    print("-" * 78)
    print("B. КОНСЕРВАТИВНОЕ ОКНО — max(ask)/min(bid) по окну (Hummingbot XEMM)")
    print("-" * 78)
    print(f"{'окно':>5} {'~сек':>6} {'оценимых':>9} {'по ценам':>10} {'ост.':>7} "
          f"{'по спредам':>12} {'ост.':>7} {'смена бирж':>12} {'не оценено':>11}")
    detail_rows: List[tuple] = []
    for w in windows:
        base = surv_p = surv_s = checked = changed = uneval = 0
        for pair, ps in series.items():
            b, sp, ss, un = conservative_window(ps, w, threshold, args.history_floor)
            c, ch = combo_stability(ps, w, threshold)
            base += b
            surv_p += sp
            surv_s += ss
            checked += c
            changed += ch
            uneval += un
            if b and args.detail_csv:
                detail_rows.append((pair, w, b, sp, ss))
        print(f"{w:>5} {w * step:>6.0f} {base:>9} {surv_p:>10} {pct(surv_p, base):>7} "
              f"{surv_s:>12} {pct(surv_s, base):>7} {pct(changed, checked):>12} "
              f"{uneval:>11}")
    print()
    print("'не оценено' — сигналы из первых циклов выборки, их окно уходит за")
    print("начало данных; в знаменатель они не входят.")
    print("'по ценам' строже: требует все циклы окна и берёт худшие цены.")
    print("'смена бирж' — доля окон, где лучшая пара бирж менялась внутри окна;")
    print("чем она выше, тем осторожнее нужно читать колонку 'по ценам'.")

    # ---------------- C ----------------
    print()
    print("-" * 78)
    print(f"C. Z-SCORE — отклонение от нормы пары, lookback {args.zscore_lookback} "
          f"циклов (~{args.zscore_lookback * step / 60:.0f} мин)")
    print("-" * 78)
    base = 0
    surv: Dict[float, int] = {k: 0 for k in ks}
    skipped = 0
    for ps in series.values():
        b, s, sk = zscore_survivors(ps, args.zscore_lookback, ks, threshold,
                                    args.history_floor, args.min_samples)
        base += b
        skipped += sk
        for k in ks:
            surv[k] += s[k]
    print(f"{'k (сигм)':>10} {'выжило':>9} {'остаток':>9}")
    for k in ks:
        print(f"{k:>10} {surv[k]:>9} {pct(surv[k], base):>9}")
    print(f"\nОценимых сигналов {base}; ещё {skipped} не оценены "
          f"(мало истории или нулевая дисперсия) и в знаменатель не вошли.")

    # ---------------- D ----------------
    print()
    print("-" * 78)
    print(f"D. ПОРОГ + m*SIGMA — волатильность mid за {args.vol_window} циклов "
          f"(~{args.vol_window * step:.0f} с)")
    print("-" * 78)
    base = 0
    surv_m: Dict[float, int] = {m: 0 for m in ms}
    skipped = 0
    for ps in series.values():
        b, s, sk = volatility_survivors(ps, args.vol_window, ms, threshold,
                                        args.min_samples)
        base += b
        skipped += sk
        for m in ms:
            surv_m[m] += s[m]
    print(f"{'m':>10} {'выжило':>9} {'остаток':>9}")
    for m in ms:
        print(f"{m:>10} {surv_m[m]:>9} {pct(surv_m[m], base):>9}")
    print(f"\nОценимых сигналов {base}; ещё {skipped} не оценены "
          f"(мало наблюдений в окне) и в знаменатель не вошли.")

    if args.detail_csv and detail_rows:
        with open(args.detail_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["pair", "window", "base_signals",
                             "survived_price", "survived_spread"])
            writer.writerows(detail_rows)
        print(f"\nДетализация блока B: {args.detail_csv} ({len(detail_rows)} строк)")

    print()
    print("=" * 78)


if __name__ == "__main__":
    main()
