"""Phase 8 -- Trade and fact table loading.

Loads the five source files into staging, then populates DimTrade and
the four Phase 8 fact tables.
"""

import os
import subprocess
import time

from .common import (
    ALPINE,
    DB,
    DB_PASSWORD,
    ROOT,
    Fail,
    container,
    container_running,
    docker,
    mariadb,
    may_replace,
    read_sql,
    run_sql_text,
    scalar,
    staging_volume,
)

NUMBER = 8
TITLE = "DimTrade and Fact Tables"
DESCRIPTION = "Load trade, cash, holding and watch data"

STAGE_SQL = os.path.join(ROOT, "sql", "di", "06_facts.sql")

SOURCE_FILES = [
    ("Trade.txt", "stg_trade", [
        "T_ID", "T_DTS", "T_ST_ID", "T_TT_ID", "T_IS_CASH",
        "T_S_SYMB", "T_QTY", "T_BID_PRICE", "T_CA_ID",
        "T_EXEC_NAME", "T_TRADE_PRICE", "T_CHRG", "T_COMM", "T_TAX",
    ]),
    ("TradeHistory.txt", "stg_trade_history", [
        "TH_T_ID", "TH_DTS", "TH_ST_ID",
    ]),
    ("CashTransaction.txt", "stg_cash_transaction", [
        "CT_CA_ID", "CT_DTS", "CT_AMT", "CT_NAME",
    ]),
    ("HoldingHistory.txt", "stg_holding_history", [
        "HH_H_T_ID", "HH_T_ID", "HH_BEFORE_QTY", "HH_AFTER_QTY",
    ]),
    ("WatchHistory.txt", "stg_watch_history", [
        "W_C_ID", "W_S_SYMB", "W_DTS", "W_ACTION",
    ]),
]

STAGE_TABLES = [item[1] for item in SOURCE_FILES]

WAREHOUSE_TABLES = [
    "DimTrade",
    "FactCashBalances",
    "FactHoldings",
    "FactWatches",
]

SQL_STEPS = [
    ("a", "DimTrade", "12_dimtrade.sql"),
    ("b", "FactCashBalances", "13_factcashbalances.sql"),
    ("c", "FactHoldings", "14_factholdings.sql"),
    ("d", "FactWatches", "15_factwatches.sql"),
]


def _source_row_count(scale, filename):
    """Count source records directly from the staging Docker volume."""
    volume = staging_volume(scale)
    path = f"/s/sf{scale}/Batch1/{filename}"

    result = docker(
        "run", "--rm", "-v", f"{volume}:/s:ro", ALPINE,
        "awk", "END { print NR }", path,
    )
    value = result.stdout.strip()

    if not value.isdigit():
        raise Fail(f"Could not count records in {filename}")

    return int(value)


def _load_one_file(ctr, scale, filename, table, columns):
    """Stream one pipe-delimited source file into its staging table."""
    volume = staging_volume(scale)
    source_path = f"/s/sf{scale}/Batch1/{filename}"
    column_list = ", ".join(f"`{column}`" for column in columns)

    print(f"  Loading {filename} into {table} ...")
    started = time.time()

    reader = subprocess.Popen(
        [
            "docker", "run", "--rm",
            "-v", f"{volume}:/s:ro",
            ALPINE, "cat", source_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    loader = subprocess.Popen(
        [
            "docker", "exec", "-i", ctr,
            "mariadb", "-uroot", f"-p{DB_PASSWORD}",
            "--local-infile=1", DB, "-e",
            (
                f"LOAD DATA LOCAL INFILE '/dev/stdin' "
                f"INTO TABLE tpcdi_stage.{table} "
                "FIELDS TERMINATED BY '|' "
                "LINES TERMINATED BY '\\r\\n' "
                f"({column_list})"
            ),
        ],
        stdin=reader.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    reader.stdout.close()
    reader.wait()
    reader_err = reader.stderr.read()
    _, loader_err = loader.communicate()

    if reader.returncode != 0:
        raise Fail(
            f"Could not read {filename}: "
            f"{(reader_err or b'').decode(errors='replace')[:300]}"
        )

    if loader.returncode != 0:
        detail = (loader_err or "").strip()
        raise Fail(f"Loading {table} failed: {detail[-500:]}")

    expected = _source_row_count(scale, filename)
    actual = int(scalar(
        ctr, f"SELECT COUNT(*) FROM tpcdi_stage.{table}", "0"
    ))

    if actual != expected:
        raise Fail(
            f"{table}: expected {expected} rows from {filename}, "
            f"but staged {actual}"
        )

    print(
        f"    {actual:,} rows loaded and verified "
        f"in {time.time() - started:.1f}s"
    )


def load_staging(ctr, scale):
    """Create staging tables and load all five source files."""
    print("\nCreating Phase 8 staging tables ...")
    mariadb(ctr, sql_file=STAGE_SQL)

    # Empty staging tables before loading to prevent duplicate source rows.
    for table in STAGE_TABLES:
        mariadb(ctr, sql=f"DELETE FROM tpcdi_stage.{table}")

    print("\nLoading Phase 8 source files ...")
    for filename, table, columns in SOURCE_FILES:
        _load_one_file(ctr, scale, filename, table, columns)

    print("\nAll five Phase 8 staging tables loaded successfully.")


def _clear_warehouse(ctr):
    """Clear Phase 8 targets before rebuilding them."""
    print("\nClearing existing Phase 8 warehouse data ...")

    # Clear fact tables before the dimension table.
    for table in (
        "FactWatches",
        "FactHoldings",
        "FactCashBalances",
        "DimTrade",
    ):
        mariadb(ctr, sql=f"DELETE FROM tpcdi.{table}")
        print(f"  emptied {table}")


def _run_step(ctr, letter, label, filename):
    """Execute one Phase 8 warehouse transformation."""
    path = os.path.join(ROOT, "sql", "di", filename)

    if not os.path.isfile(path):
        raise Fail(f"Missing Phase 8 SQL file: {path}")

    print(f"\nstep {letter}: {label} ...")
    started = time.time()

    # read_sql removes standalone diagnostic SELECT statements.
    sql = read_sql(path)
    run_sql_text(ctr, sql)

    print(f"  done in {time.time() - started:.1f}s")


def run(scale, force=False, keep=False, **_opts):
    """Load Phase 8 staging data and populate warehouse targets."""
    ctr = container(scale)

    if not container_running(ctr):
        raise Fail(f"{ctr} is not running. Run Phase 3 first.")

    present = [
        f"tpcdi_stage.{table}: {count}"
        for table in STAGE_TABLES
        if (count := scalar(
            ctr, f"SELECT COUNT(*) FROM tpcdi_stage.{table}", "0"
        )).isdigit() and int(count) > 0
    ]

    present += [
        f"{table}: {count}"
        for table in WAREHOUSE_TABLES
        if (count := scalar(
            ctr, f"SELECT COUNT(*) FROM tpcdi.{table}", "0"
        )).isdigit() and int(count) > 0
    ]

    if present:
        if not may_replace(
            f"Phase 8 data for scale factor {scale}",
            present,
            force,
            keep,
        ):
            return

        _clear_warehouse(ctr)

    load_staging(ctr, scale)

    print("\nPopulating Phase 8 warehouse tables ...")
    for letter, label, filename in SQL_STEPS:
        _run_step(ctr, letter, label, filename)

    print("\nPhase 8 warehouse loading completed.")

    for table in WAREHOUSE_TABLES:
        count = scalar(
            ctr, f"SELECT COUNT(*) FROM tpcdi.{table}", "0"
        )
        print(f"  {table:<22} {count} rows")


def verify(scale, c):
    """Verify Phase 8 staging and warehouse tables."""
    ctr = container(scale)

    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run Phase 3 first")
        return

    print("X1     source files match staging row counts")

    for filename, table, _columns in SOURCE_FILES:
        expected = _source_row_count(scale, filename)
        actual = scalar(
            ctr, f"SELECT COUNT(*) FROM tpcdi_stage.{table}", "0"
        )

        c.want(
            actual == str(expected),
            f"{table}: {actual} rows = {expected} source rows",
        )

    print("\nX2     warehouse row counts match their source records")

    trade_source = scalar(
        ctr, "SELECT COUNT(*) FROM tpcdi_stage.stg_trade", "0"
    )
    trade_target = scalar(
        ctr, "SELECT COUNT(*) FROM tpcdi.DimTrade", "0"
    )
    c.want(
        trade_target == trade_source,
        f"DimTrade: {trade_target} rows = {trade_source} staged trades",
    )

    cash_expected = scalar(
        ctr,
        """
        SELECT COUNT(*)
        FROM (
            SELECT
                CAST(CT_CA_ID AS UNSIGNED) AS AccountID,
                DATE(STR_TO_DATE(CT_DTS, '%Y-%m-%d %H:%i:%s')) AS CashDate
            FROM tpcdi_stage.stg_cash_transaction
            GROUP BY AccountID, CashDate
        ) daily
        """,
        "0",
    )
    cash_actual = scalar(
        ctr, "SELECT COUNT(*) FROM tpcdi.FactCashBalances", "0"
    )
    c.want(
        cash_actual == cash_expected,
        f"FactCashBalances: {cash_actual} rows = "
        f"{cash_expected} account-date balances",
    )

    holdings_expected = scalar(
        ctr, "SELECT COUNT(*) FROM tpcdi_stage.stg_holding_history", "0"
    )
    holdings_actual = scalar(
        ctr, "SELECT COUNT(*) FROM tpcdi.FactHoldings", "0"
    )
    c.want(
        holdings_actual == holdings_expected,
        f"FactHoldings: {holdings_actual} rows = "
        f"{holdings_expected} holding records",
    )

    watches_expected = scalar(
        ctr,
        "SELECT COUNT(*) FROM tpcdi_stage.stg_watch_history "
        "WHERE W_ACTION = 'ACTV'",
        "0",
    )
    watches_actual = scalar(
        ctr, "SELECT COUNT(*) FROM tpcdi.FactWatches", "0"
    )
    c.want(
        watches_actual == watches_expected,
        f"FactWatches: {watches_actual} rows = "
        f"{watches_expected} activation events",
    )

    print("\nX3     watch activation and cancellation counts")

    active_expected = scalar(
        ctr,
        "SELECT COUNT(*) FROM tpcdi_stage.stg_watch_history a "
        "WHERE a.W_ACTION = 'ACTV' "
        "AND NOT EXISTS ("
        "SELECT 1 FROM tpcdi_stage.stg_watch_history c "
        "WHERE c.W_ACTION = 'CNCL' "
        "AND c.W_C_ID = a.W_C_ID "
        "AND c.W_S_SYMB = a.W_S_SYMB "
        "AND c.W_DTS > a.W_DTS"
        ")",
        "0",
    )
    cancelled_expected = scalar(
        ctr,
        "SELECT COUNT(*) FROM tpcdi_stage.stg_watch_history "
        "WHERE W_ACTION = 'CNCL'",
        "0",
    )
    active_actual = scalar(
        ctr,
        "SELECT COUNT(*) FROM tpcdi.FactWatches "
        "WHERE SK_DateID_DateRemoved IS NULL",
        "0",
    )
    cancelled_actual = scalar(
        ctr,
        "SELECT COUNT(*) FROM tpcdi.FactWatches "
        "WHERE SK_DateID_DateRemoved IS NOT NULL",
        "0",
    )

    c.want(
        active_actual == active_expected,
        f"Active watches: {active_actual} = {active_expected} source activations",
    )
    c.want(
        cancelled_actual == cancelled_expected,
        f"Cancelled watches: {cancelled_actual} = {cancelled_expected} source cancellations",
    )
