import pandas as pd
import sys
import os
import time
import sqlite3
from loguru import logger

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAWS_DIR = os.path.join(BASE_DIR, 'Raws')
DATA_DIR = os.path.join(BASE_DIR, 'data')

# Served source of truth = computed tables in the SQLite DB, written by
# CSVcreator._build_* from the same frame as the CSVs. The CSVs are kept as a
# backup / export artifact and are used as an automatic fallback whenever a DB
# table is missing or empty, so the dashboard degrades gracefully.
DB_PATH = os.path.join(DATA_DIR, 'dashboard.db')
TRANS_TABLE = 'transactions_final'
INV_TABLE = 'inventory_final'

TRANS_FILE = os.path.join(RAWS_DIR, 'transactions.csv')
INVENTORY_FILE = os.path.join(RAWS_DIR, 'inventory.csv')

METRIC_MODE = 'qty'  # qty or weight

# --- GLOBALS FOR ALL OTHER PAGES ---
df_global = None
df_soh_global = None
last_date_global = None
years_global = []

# --- SMART CACHE FOR V1 LIVE RELOADING ---
_cache = {
    'df': pd.DataFrame(),
    'soh': pd.DataFrame(),
    'last_date': "N/A",
    'years': [],
    'last_modified_time': 0
}


def _db_mtime() -> float:
    """Freshness signal for the served DB — newest of the db file and its WAL."""
    m = 0.0
    for p in (DB_PATH, DB_PATH + '-wal'):
        if os.path.exists(p):
            m = max(m, os.path.getmtime(p))
    return m


def _db_table_count(table: str) -> int:
    """Row count for a served table, or -1 if the DB / table is unavailable."""
    if not os.path.exists(DB_PATH):
        return -1
    try:
        con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
        try:
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None:
                return -1
            return con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        finally:
            con.close()
    except Exception:
        return -1


def _read_db_table(table: str) -> pd.DataFrame:
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        return pd.read_sql(f'SELECT * FROM "{table}"', con)
    finally:
        con.close()


def _load_transactions_raw(use_db: bool):
    """Return (raw transactions df, source label). DB table -> CSV backup -> empty.
    Never raises: server boot must survive a missing/empty/corrupt source."""
    empty = pd.DataFrame(columns=['Date', 'Value', 'QTY PCS', 'Quantity',
                                  'Distance from Nairobi'])
    if use_db:
        try:
            return _read_db_table(TRANS_TABLE), f"DB table '{TRANS_TABLE}'"
        except Exception as e:
            logger.error(f"⚠️ DB read of {TRANS_TABLE} failed ({e}); falling back to CSV.")
    if os.path.exists(TRANS_FILE) and os.path.getsize(TRANS_FILE) > 0:
        try:
            return pd.read_csv(TRANS_FILE, low_memory=False), \
                f"CSV backup ({os.path.basename(TRANS_FILE)})"
        except Exception as e:
            logger.error(f"⚠️ Transactions CSV unreadable ({e}) — serving empty dataset.")
            return empty.copy(), "empty (no readable source)"
    logger.error("⚠️ No transactions source (DB table + CSV both unavailable) — "
                 "serving empty dataset (dashboard will show no data).")
    return empty.copy(), "empty (no source)"


def _normalize_soh(df_soh_raw: pd.DataFrame) -> pd.DataFrame:
    """Reduce any inventory frame to unique Items with summed Qty On Hand."""
    df_soh_raw = df_soh_raw.copy()
    df_soh_raw.columns = df_soh_raw.columns.str.strip()
    if 'Items' not in df_soh_raw.columns:
        item_col = next((c for c in df_soh_raw.columns if 'item' in c.lower()), None)
        if item_col: df_soh_raw.rename(columns={item_col: 'Items'}, inplace=True)
    if 'Qty On Hand' not in df_soh_raw.columns:
        qty_col = next((c for c in df_soh_raw.columns if 'qty' in c.lower() or 'stock' in c.lower()), None)
        if qty_col: df_soh_raw.rename(columns={qty_col: 'Qty On Hand'}, inplace=True)
    if 'Items' not in df_soh_raw.columns: df_soh_raw['Items'] = 'Unknown'
    if 'Qty On Hand' not in df_soh_raw.columns: df_soh_raw['Qty On Hand'] = 0
    df_soh_raw['Qty On Hand'] = pd.to_numeric(df_soh_raw['Qty On Hand'], errors='coerce').fillna(0)
    return df_soh_raw.groupby('Items')['Qty On Hand'].sum().reset_index()


def _load_inventory_soh():
    """Return (normalized SOH df, source label). DB table -> CSV backup -> empty."""
    empty = pd.DataFrame(columns=['Items', 'Qty On Hand'])
    if _db_table_count(INV_TABLE) > 0:
        try:
            return _normalize_soh(_read_db_table(INV_TABLE)), f"DB table '{INV_TABLE}'"
        except Exception as e:
            logger.warning(f"⚠️ DB read of {INV_TABLE} failed ({e}); falling back to CSV.")
    if os.path.exists(INVENTORY_FILE) and os.path.getsize(INVENTORY_FILE) > 0:
        try:
            return _normalize_soh(pd.read_csv(INVENTORY_FILE)), \
                f"CSV backup ({os.path.basename(INVENTORY_FILE)})"
        except Exception as e:
            logger.warning(f"⚠️ Inventory CSV unreadable ({e}); continuing without stock data.")
            return empty, "empty"
    logger.warning("⚠️ No inventory source; continuing without stock-on-hand data.")
    return empty, "empty"


@logger.catch(reraise=True)
def get_latest_data(force_reload=False):
    """Load transactions + inventory from the served DB tables (CSV as fallback).
    Cached in RAM; reloads when the underlying source changes."""
    # Choose the transactions source: prefer the computed DB table, else the CSV.
    use_db_tx = _db_table_count(TRANS_TABLE) > 0
    if use_db_tx:
        current_mtime = _db_mtime()
    else:
        current_mtime = os.path.getmtime(TRANS_FILE) if os.path.exists(TRANS_FILE) else 0.0

    # If the source is newer than our RAM cache, reload!
    if force_reload or current_mtime > _cache['last_modified_time']:
        if not force_reload:
            logger.info("🔄 Source update detected! Reloading fresh data...")
        else:
            logger.info("📂 STARTING INITIAL DATA LOAD...")

        start_time = time.time()

        # 1. Load Transactions (DB table -> CSV backup -> empty; never crash boot)
        df, tx_source = _load_transactions_raw(use_db_tx)
        raw_row_count = len(df)

        df.columns = df.columns.str.strip()
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
        df = df.dropna(subset=['Date'])

        for col in ['Items', 'Name', 'Items 2', 'Location', 'Sales Rep', 'Customer Names']:
            if col not in df.columns: df[col] = 'Unknown'
            df[col] = df[col].astype(str).replace('nan', 'Unknown').str.strip()

        weight_col = next((c for c in df.columns if 'weight' in c.lower()), None)
        if weight_col:
            df['Weight'] = df[weight_col].astype(str).str.replace(r'[^\d.]', '', regex=True)
            df['Weight'] = pd.to_numeric(df['Weight'], errors='coerce').fillna(0)
        else:
            df['Weight'] = 0

        df['Value'] = pd.to_numeric(df['Value'], errors='coerce').fillna(0)
        df['QTY PCS'] = pd.to_numeric(df['QTY PCS'], errors='coerce').fillna(0)

        if 'Distance from Nairobi' not in df.columns: df['Distance from Nairobi'] = 0
        df['Distance'] = pd.to_numeric(df['Distance from Nairobi'], errors='coerce').fillna(0)

        # --- TRANSACTION DIAGNOSTICS ---
        logger.info("\n" + "=" * 40)
        logger.info("📊 TRANSACTION DATA DIAGNOSTICS")
        logger.info("=" * 40)
        logger.info(f"Source:             {tx_source}")
        logger.info(f"Raw Rows Read:      {raw_row_count:,}")
        logger.info(f"Valid Date Rows:    {len(df):,}")
        if not df.empty:
            logger.info(
                f"Date Range Found:   {df['Date'].min().strftime('%d-%b-%Y')} to {df['Date'].max().strftime('%d-%b-%Y')}")
            logger.info(f"Total Gross Value:  Ksh {df['Value'].sum():,.2f}")
            logger.info(f"Total Volume (Qty): {df['QTY PCS'].sum():,.2f}")
        logger.info(f"Columns Detected:   {', '.join(df.columns.tolist())}")
        logger.info("=" * 40 + "\n")

        # 2. Load Inventory (DB table -> CSV backup -> empty; secondary dataset)
        df_soh, inv_source = _load_inventory_soh()
        logger.info("\n" + "=" * 40)
        logger.info("📦 INVENTORY DATA DIAGNOSTICS")
        logger.info("=" * 40)
        logger.info(f"Source:             {inv_source}")
        logger.info(f"Unique Items Found: {len(df_soh):,}")
        logger.info(f"Total Qty in Stock: {df_soh['Qty On Hand'].sum():,.2f}")
        logger.info("=" * 40 + "\n")

        # 3. Update the RAM Cache
        _cache['df'] = df
        _cache['soh'] = df_soh

        if not df.empty:
            _cache['last_date'] = df['Date'].max().strftime('%d %b %Y')
            _cache['years'] = [int(y) for y in sorted(df['Date'].dt.year.dropna().unique(), reverse=True)]
        else:
            _cache['last_date'] = "N/A"
            _cache['years'] = []

        _cache['last_modified_time'] = current_mtime
        logger.success(f"✅ Data Ready in {time.time() - start_time:.2f}s")

    return _cache['df'], _cache['soh'], _cache['last_date'], _cache['years']


@logger.catch(reraise=True)
def load_initial_data():
    """Runs once on boot to populate the static variables for legacy pages."""
    global df_global, df_soh_global, last_date_global, years_global
    df, soh, last_date, years = get_latest_data(force_reload=True)
    df_global = df
    df_soh_global = soh
    last_date_global = last_date
    years_global = years


# Run once on server boot
load_initial_data()