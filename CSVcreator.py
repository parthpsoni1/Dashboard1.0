"""
CSVcreator.py  —  Two-Pass Data Ingestion Pipeline
====================================================
Run this script whenever the source Excel files are updated.

SOURCE FILES
  Primary   : Raws/Transactions from Jan to Sep 25.xlsx  (Sheet 2 = transactions,
              QIH = inventory, PackSize / Item List / Customers = reference sheets)
  Secondary : Raws/Price Checker 3.xlsx  (Final sheet = invoice checker data)

PASS 1 — INGEST  (Excel → SQLite .db)
  • First run   : full load of every sheet into the DB.
  • Subsequent  : row-level hash comparison → upsert only what changed.
  • Schema evolution: if the Excel gains new columns, the DB table is
    automatically altered to match — no crashes, no manual intervention.
  • Reference sheets (PackSize, Item List, Customers) are joined during
    ingest to rebuild derived columns (Items 2, Pack Size Match, etc.).
  • prices.csv is hash-checked; if changed, DB is updated and logged.
  • Price Checker 3.xlsx is hash-checked; Final sheet upserted to 'invoices' table.

PASS 2 — BUILD   (SQLite .db → final CSV files)
  • Joins transactions against current reference tables.
  • Applies calculated columns: Avg_Price, Price_Deviation_Pct, Price_Flag,
    Weight_Per_Unit, Rev_Per_Km.
  • Runs validation checks and writes a validation_report.csv.
  • Writes transactions.csv + inventory.csv + invoices_final.csv to Raws/.

Usage
-----
    python CSVcreator.py                          # normal run
    python CSVcreator.py --force-reload           # ignore all hashes, full reload
    python CSVcreator.py --build-only             # skip ingest, just rebuild CSVs (~8s)
    python CSVcreator.py --ingest-only            # ingest to DB, skip CSV output
    python CSVcreator.py --reset-db               # delete DB and do a full fresh load
    python CSVcreator.py --source path/to/file.xlsx  # override the transactions Excel
    python CSVcreator.py --diagnostics            # show source + DB status, then exit
"""

import os
import re
import sys
import hashlib
import sqlite3
import argparse
import datetime
import logging
from pathlib import Path

import pandas as pd
import numpy as np

# ── PATHS ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
RAWS_DIR   = BASE_DIR / 'Raws'
DATA_DIR   = BASE_DIR / 'data'
LOG_DIR    = BASE_DIR / 'logs'
DB_PATH    = DATA_DIR / 'dashboard.db'

# Preferred source file — checked FIRST before any auto-detection.
# Change this if the main Excel file is ever renamed.
# NOTE: 'Old Transactions from Jan to Sep 25.xlsx' is the full Jan 2024–May 2026
# dataset. 'Transactions from Jan to Sep 25.xlsx' was accidentally replaced with
# a 328-row partial export. Point here until the file is corrected.
EXCEL_PREFERRED = RAWS_DIR / 'Transactions from Jan to Sep 25.xlsx'

# Preferred invoice source file (Price Checker 3.xlsx → 'Final' sheet)
INVOICE_PREFERRED = RAWS_DIR / 'Price Checker 3.xlsx'

# Sales Orders New In — used to enrich invoices with Sales Order (Ord No:)
SALES_ORDERS_PREFERRED = RAWS_DIR / 'Sales Orders New In.xlsx'

# Cash Sales report — daily download that attributes Z099 cash transactions to reps
CASH_SALES_FILE = RAWS_DIR / 'Cash Sales.xlsx'

# Account prefix that identifies cash-sale accounts (Z099, Z099/A, Z099/B, Z099/C …)
CASH_ACCOUNT_PREFIX = 'Z099'

# Reference CSVs outside the main Excel
REF_SOURCES = {
    'price_list': RAWS_DIR / 'prices.csv',
}

# CSV output paths (data_loader.py reads from these exact locations)
CSV_OUT = {
    'transactions':   RAWS_DIR / 'transactions.csv',
    'inventory':      RAWS_DIR / 'inventory.csv',
    'invoices_final': RAWS_DIR / 'invoices_final.csv',
}

VALIDATION_REPORT = LOG_DIR / f"validation_{datetime.date.today()}.csv"

# ── LOGGING ───────────────r─────────────────────────────────────────────────────
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"csvcreator_{datetime.date.today()}.log",
            encoding='utf-8'
        ),
    ]
)
# Force UTF-8 on Windows stdout to avoid cp1252 UnicodeEncodeError on emoji
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

log = logging.getLogger('CSVcreator')

# ── INVENTORY KEEP COLS ────────────────────────────────────────────────────────
# Transactions are NOT filtered here — we keep ALL non-junk columns from the
# Excel and then enrich them via reference joins.
INV_KEEP_COLS = [
    'Item Code', 'Item Description', 'Items', 'Whse', 'Group',
    'Bin Location', 'Qty On Hand', 'Reorder Lvl', 'MaxLvl', 'QtyOnSO',
]


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _hash_row(series: pd.Series) -> str:
    return hashlib.md5('|'.join(str(v) for v in series).encode()).hexdigest()


def _hash_file(path: Path) -> str:
    if not path.exists():
        return ''
    return hashlib.md5(path.read_bytes()).hexdigest()


def _hash_df(df: pd.DataFrame) -> str:
    return hashlib.md5(df.to_csv(index=False).encode()).hexdigest()


def _now() -> str:
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _strip_junk_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop junk column headers that come from Excel exports or pandas merges:

      - Unnamed: N       — blank Excel columns
      - nan / NaN        — empty header read as NaN
      - nan.1, nan.2 …   — pandas duplicate suffix on an empty header
      - 0, 0.1, 0.2 …    — purely numeric headers (Excel ghost columns)
      - Counter.1, X.2 … — pandas .N duplicate suffix when the base name is
                            already being kept (e.g. two 'Counter' columns)
    """
    kept_names: set[str] = set()
    keep = []
    for c in df.columns:
        s = str(c).strip()

        # Unnamed: N  /  empty string
        if s.startswith('Unnamed') or s == '':
            continue
        # nan, NaN, nan.1, nan.2, NaN.3 …
        if re.match(r'^[Nn][Aa][Nn](\.\d+)?$', s):
            continue
        # Purely numeric: 0, 0.1, 0.2, 123 …
        try:
            float(s)
            continue
        except ValueError:
            pass
        # Pandas .N duplicate: "Counter.1" when "Counter" is already kept
        m = re.match(r'^(.+)\.(\d+)$', s)
        if m and m.group(1) in kept_names:
            continue

        kept_names.add(s)
        keep.append(c)

    dropped = len(df.columns) - len(keep)
    if dropped:
        log.debug(f"  Dropped {dropped} junk columns.")
    return df[keep].copy()


# ══════════════════════════════════════════════════════════════════════════════
#  EXCEL AUTO-DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _find_excel(override: Path = None) -> Path:
    """
    Return the source Excel path using this priority order:
      1. CLI --source override (if provided and exists)
      2. EXCEL_PREFERRED  ('Transactions from Jan to Sep 25.xlsx') if it exists
      3. Auto-detect: largest .xlsx in Raws/ that has a 'Sheet 2' sheet
    """
    # Priority 1: explicit CLI override
    if override is not None:
        if override.exists():
            return override
        log.warning(f"  --source file not found: {override}. Falling back.")

    # Priority 2: preferred filename
    if EXCEL_PREFERRED.exists():
        return EXCEL_PREFERRED

    # Priority 3: auto-detect by size (largest with Sheet 2 = most data)
    log.warning(f"  '{EXCEL_PREFERRED.name}' not found — scanning for alternatives ...")
    candidates = []
    for f in RAWS_DIR.glob('*.xlsx'):
        try:
            xl = pd.ExcelFile(f)
            if 'Sheet 2' in xl.sheet_names:
                candidates.append((f.stat().st_size, f))
        except Exception:
            continue

    if not candidates:
        log.error("  No .xlsx with 'Sheet 2' found in Raws/. Cannot continue.")
        sys.exit(1)

    candidates.sort(reverse=True)          # largest file = most rows
    chosen = candidates[0][1]
    log.warning(f"  Auto-selected fallback: {chosen.name} "
                f"({candidates[0][0]:,} bytes). "
                f"Set EXCEL_PREFERRED in CSVcreator.py to avoid this.")
    return chosen


def _find_invoice_excel() -> Path | None:
    """
    Return the invoice Excel path using this priority order:
      1. INVOICE_PREFERRED ('Price Checker 3.xlsx') if it exists
      2. Auto-detect: first .xlsx in Raws/ that contains a 'Final' sheet
    Returns None if no suitable file is found (invoice ingest is then skipped).
    """
    if INVOICE_PREFERRED.exists():
        return INVOICE_PREFERRED

    log.warning(f"  '{INVOICE_PREFERRED.name}' not found — scanning for 'Final' sheet ...")
    for f in sorted(RAWS_DIR.glob('*.xlsx')):
        try:
            xl = pd.ExcelFile(f)
            if 'Final' in xl.sheet_names:
                log.warning(f"  Auto-selected invoice fallback: {f.name}. "
                            f"Set INVOICE_PREFERRED in CSVcreator.py to avoid this.")
                return f
        except Exception:
            continue

    log.warning("  No invoice Excel with a 'Final' sheet found in Raws/. Invoice ingest skipped.")
    return None


def _find_sales_orders_excel() -> Path | None:
    """
    Return the 'Sales Orders New In.xlsx' path.
    Falls back to any .xlsx in Raws/ whose name contains 'sales orders'.
    Returns None if not found — Ord No enrichment is then silently skipped.
    """
    if SALES_ORDERS_PREFERRED.exists():
        return SALES_ORDERS_PREFERRED
    for f in sorted(RAWS_DIR.glob('*.xlsx')):
        if 'sales orders' in f.name.lower():
            return f
    return None


def _find_inventory_source() -> Path | None:
    """
    Return the newest Sage inventory (quantity-in-hand) export in Raws/.
    Accepts either the daily 'Inventory Listing*.xlsx' export or a classic
    'QIH.xlsx'; the most recently modified wins so the freshest SOH is used.
    Returns None if neither is present (inventory is then left unchanged).
    """
    candidates = list(RAWS_DIR.glob('Inventory Listing*.xlsx'))
    qih = RAWS_DIR / 'QIH.xlsx'
    if qih.exists():
        candidates.append(qih)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _parse_sales_orders_xlsx(filepath: Path) -> dict:
    """
    Parse the ERP-exported 'Sales Orders New In.xlsx' to build an
    {Inv_No -> Ord_No} mapping.

    The file is a grouped ERP report with a repeating structure:
      • Page/title rows  (col 0 = 'Order Entry Sales Orders', 'Date:', etc.)
      • Column header rows  (col 0 = 'Item/Account')
      • Data rows  (col 0 = item code, col 10 = Ord No:, col 11 = Inv No:)
      • Subtotal rows  (col 0 is purely numeric — skip)
      • Blank rows  (col 0 is NaN — skip)
    """
    try:
        df_raw = pd.read_excel(filepath, header=None)
    except Exception as e:
        log.warning(f"  Sales Orders parse failed ({filepath.name}): {e}")
        return {}

    _SKIP_VALUES = {'Item/Account', 'Order Entry Sales Orders',
                    'Date:', 'Orders:', 'nan', ''}
    mapping: dict[str, str] = {}

    for _, row in df_raw.iterrows():
        v0 = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ''
        if v0 in _SKIP_VALUES:
            continue
        # Skip subtotal rows (col 0 is purely numeric)
        try:
            float(v0)
            continue
        except ValueError:
            pass
        # Col 10 = Ord No:, Col 11 = Inv No: (Sage ERP report format only)
        if len(row) < 12:
            continue
        ord_no = str(row.iloc[10]).strip() if pd.notna(row.iloc[10]) else ''
        inv_no = str(row.iloc[11]).strip() if pd.notna(row.iloc[11]) else ''
        if not ord_no or not inv_no or ord_no == 'Ord No:' or inv_no == 'Inv No:':
            continue
        if inv_no not in mapping:           # first occurrence wins
            mapping[inv_no] = ord_no

    log.info(f"  Sales Orders '{filepath.name}': "
             f"{len(mapping):,} Inv_No → Ord_No mappings loaded.")
    return mapping


# ══════════════════════════════════════════════════════════════════════════════
#  INVENTORY TRANSACTIONS INGEST  (replaces Price Checker Final sheet)
# ══════════════════════════════════════════════════════════════════════════════

def _find_inventory_transactions_excel() -> Path | None:
    """
    Return the invoice source Excel file, checked in priority order:
      1. Sales Orders New In.xlsx  (preferred — Sage download that includes SO numbers)
      2. Most-recently-modified Inventory Transactions*.xlsx in Raws/  (timestamped exports)
    Returns None if neither exists (caller will fall back to Price Checker Final sheet).
    """
    if SALES_ORDERS_PREFERRED.exists():
        return SALES_ORDERS_PREFERRED
    matches = sorted(
        RAWS_DIR.glob('Inventory Transactions*.xlsx'),
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    return matches[0] if matches else None


def _read_excel_robust(filepath: Path, **kwargs):
    """Try calamine → openpyxl engines so openpyxl version-quirks don't block us."""
    for engine in ('calamine', 'openpyxl', 'xlrd'):
        try:
            return pd.read_excel(filepath, engine=engine, **kwargs)
        except Exception:
            continue
    raise IOError(f"Could not read {filepath.name} with any available engine.")


def _parse_inventory_transactions_xlsx(filepath: Path) -> pd.DataFrame:
    """
    Parse a Sage 'Inventory Transactions*.xlsx' ERP export.

    File structure (repeating per page):
      • Title / date-range rows   (col 0 NaN or 'Inventory Transactions')
      • Column header row         (col 0 == 'Item Code')
      • Data rows                 (actual transaction lines)
      • Page-footer rows          (col 0 NaN, col 17 'Page N of M')

    Relevant columns (by position in the header row):
      0  Item Code   |  2  Date  |  4  Tran  |  5  Account
      7  Rep (=SO No)|  8  Group (=Invoice No)|  11 Quantity
      12 WarehouseName  |  15 Value  |  17 AutoID
    """
    try:
        df_raw = _read_excel_robust(filepath, header=None)
    except IOError as e:
        log.error(f"  _parse_inventory_transactions_xlsx: {e}")
        return pd.DataFrame()

    # Locate the first header row
    header_idx = None
    for i, row in df_raw.iterrows():
        if str(row.iloc[0]).strip() == 'Item Code':
            header_idx = i
            break

    if header_idx is None:
        log.error(f"  No 'Item Code' header row found in {filepath.name}.")
        return pd.DataFrame()

    # Re-read with header so we get named columns
    df = _read_excel_robust(filepath, header=header_idx)
    df.columns = [str(c).strip() for c in df.columns]

    # Drop completely empty rows and non-data rows (page headers repeat)
    df = df.dropna(subset=['Item Code'])
    df = df[~df['Item Code'].astype(str).str.strip().isin(
        ['Item Code', 'Inventory Transactions', 'nan', '']
    )]

    # Keep INV (invoices) and CRN (credit notes); drop header/total rows
    if 'Tran' in df.columns:
        df = df[df['Tran'].astype(str).str.strip().isin(['INV', 'CRN'])].copy()

    if df.empty:
        log.warning(f"  {filepath.name}: 0 INV rows after filtering.")
        return pd.DataFrame()

    # Rename to stable names
    # 'Rep' holds the Sales Order No, 'Group' holds the Invoice No
    rename = {
        'Item Code':     'Item_Code',
        'Date':          'Date',
        'Tran':          'Tran',
        'Account':       'Account',
        'Rep':           'SO_No',
        'Group':         'Inv_No',
        'Quantity':      'Quantity',
        'WarehouseName': 'Warehouse',
        'Value':         'Value',
        'AutoID':        'AutoID',
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    # Coerce types
    df['Item_Code'] = df['Item_Code'].astype(str).str.strip()
    df['Account']   = df['Account'].astype(str).str.strip() if 'Account' in df.columns else ''
    df['Inv_No']    = df['Inv_No'].astype(str).str.strip() if 'Inv_No' in df.columns else ''
    df['SO_No']     = df['SO_No'].astype(str).str.strip() if 'SO_No' in df.columns else ''
    df['Date']      = pd.to_datetime(df['Date'], errors='coerce')
    df['Quantity']  = pd.to_numeric(df['Quantity'], errors='coerce').fillna(0)
    df['Value']     = pd.to_numeric(df['Value'],    errors='coerce').fillna(0)

    # Remove zero-qty rows (can appear as header artefacts)
    df = df[df['Quantity'] != 0].copy()

    # Derive unit price
    df['Unit_Price'] = (df['Value'] / df['Quantity']).round(6)

    log.info(f"  Inv Txn '{filepath.name}': {len(df):,} INV lines parsed "
             f"({df['Inv_No'].nunique():,} invoices, {df['SO_No'].nunique():,} SOs).")
    return df


def _get_account_customer_map() -> dict:
    """
    Build {account_code -> customer_name} by combining:
      1. 'Customers' sheet in Transactions from Jan to Sep 25.xlsx  (primary — most complete)
      2. DISTINCT Account / Customer Names from the transactions table in DB  (supplement)
    """
    acc_map: dict[str, str] = {}

    # Source 1 – Customers sheet in the main Transactions xlsx
    if EXCEL_PREFERRED.exists():
        try:
            xl = pd.ExcelFile(EXCEL_PREFERRED, engine='calamine')
            if 'Customers' in xl.sheet_names:
                cl = _read_excel_robust(EXCEL_PREFERRED, sheet_name='Customers')
                cl.columns = cl.columns.str.strip()
                # Customers sheet columns: Customer, Name, Rep Name, Area Description,
                #                          Location, Distance from Nairobi, B2B, Match Cus Ref
                if {'Customer', 'Name'}.issubset(cl.columns):
                    for _, row in cl.dropna(subset=['Customer', 'Name']).iterrows():
                        acc  = str(row['Customer']).strip()
                        name = str(row['Name']).strip()
                        if acc and name:
                            acc_map[acc] = name
            log.info(f"  Customers sheet: {len(acc_map):,} account codes loaded.")
        except Exception as e:
            log.warning(f"  Customers sheet read failed: {e}")

    # Source 2 – transactions table in DB (supplements with any codes not in the sheet)
    try:
        with get_connection() as conn:
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'transactions' in existing:
                rows = conn.execute(
                    'SELECT DISTINCT Account, "Customer Names" FROM transactions '
                    'WHERE Account IS NOT NULL AND "Customer Names" IS NOT NULL'
                ).fetchall()
                added = 0
                for acc, name in rows:
                    key = str(acc).strip()
                    if key and key not in acc_map:
                        acc_map[key] = str(name).strip()
                        added += 1
                if added:
                    log.info(f"  transactions table supplemented {added:,} more account codes.")
    except Exception as e:
        log.warning(f"  Account map from transactions failed: {e}")

    # Source 3 – customer_overrides table (Admin Panel additions — overrides Excel)
    try:
        with get_connection() as conn:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'customer_overrides' in tbls:
                rows = conn.execute(
                    'SELECT account, customer_name FROM customer_overrides'
                ).fetchall()
                overridden = 0
                for acc, name in rows:
                    key = str(acc).strip()
                    if key and name:
                        acc_map[key] = str(name).strip()
                        overridden += 1
                if overridden:
                    log.info(f"  customer_overrides: {overridden:,} DB entries applied.")
    except Exception as _e:
        log.warning(f"  customer_overrides load failed (non-fatal): {_e}")

    log.info(f"  Account→Customer map: {len(acc_map):,} entries total.")
    return acc_map


def _build_invoice_price_map() -> tuple[dict, dict, dict]:
    """
    Build price lookup maps from Master Price List.xlsx and prices.csv.

    Priority order for each invoice line:
      1. specific_map   {(item_code, CUSTOMER_UPPER) -> (master_price, old_price, pct_inc)}
           — exact item + customer match from Master Price List.xlsx
      2. item_map       {item_code -> (master_price, old_price, pct_inc)}
           — any-customer fallback from Master Price List.xlsx (most-recent row per item)
      3. pct_only_map   {item_code -> pct_inc}
           — % Increase from prices.csv; master price is then derived from historical avg price

    Returns:
      (specific_map, item_map, pct_only_map)
    """
    specific:   dict = {}
    item_map:   dict = {}
    pct_only:   dict = {}

    # ── Source 0: price_overrides table (Admin Panel corrections — highest priority) ──
    try:
        with get_connection() as conn:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'price_overrides' in tbls:
                rows = conn.execute(
                    'SELECT item_code, customer_name, master_price, old_price, pct_increase '
                    'FROM price_overrides'
                ).fetchall()
                for item, cust, new_p, old_p, pct in rows:
                    item = str(item).strip()
                    cust = str(cust or '').strip()
                    val  = (
                        float(new_p) if new_p is not None else np.nan,
                        float(old_p) if old_p is not None else np.nan,
                        float(pct)   if pct   is not None else np.nan,
                    )
                    if cust:
                        specific[(item, cust.upper())] = val
                    item_map[item] = val
                if rows:
                    log.info(f"  price_overrides: {len(rows):,} DB corrections loaded.")
    except Exception as _e:
        log.warning(f"  price_overrides load failed (non-fatal): {_e}")

    # ── Source 1: Master Price List.xlsx (Raw sheet) ──────────────────────────
    mpl_path = RAWS_DIR / 'Master Price List.xlsx'
    if mpl_path.exists():
        try:
            mpl = _read_excel_robust(mpl_path, sheet_name='Raw')
            mpl.columns = mpl.columns.str.strip()
            # Sort descending by Date so the most-recent price wins the item_map slot
            if 'Date' in mpl.columns:
                mpl['Date'] = pd.to_datetime(mpl['Date'], errors='coerce')
                mpl = mpl.sort_values('Date', ascending=False)

            for _, row in mpl.iterrows():
                item = str(row.get('Item/Account', '')).strip()
                if not item or item == 'nan':
                    continue
                cust  = str(row.get('Customer Name', '')).strip().upper()
                new_p = pd.to_numeric(row.get('Master Price (Excl)', np.nan), errors='coerce')
                old_p = pd.to_numeric(row.get('Old Price',           np.nan), errors='coerce')
                pct   = pd.to_numeric(row.get('% Increase',          np.nan), errors='coerce')
                val   = (new_p, old_p, pct)

                if cust and cust != 'NAN':
                    # specific: first row wins for each (item, customer) pair
                    specific.setdefault((item, cust), val)
                # item fallback: first (most recent) row per item
                item_map.setdefault(item, val)

            log.info(f"  Master Price List.xlsx: {len(specific):,} specific "
                     f"+ {len(item_map):,} item-level entries.")
        except Exception as e:
            log.warning(f"  Failed to read Master Price List.xlsx: {e}")
    else:
        log.info("  Master Price List.xlsx not found — falling back to prices.csv only.")

    # ── Source 2: prices.csv  (% Increase fallback for items not in MPL) ─────
    pct_csv_path = REF_SOURCES.get('price_list')
    if pct_csv_path and pct_csv_path.exists():
        try:
            pc = pd.read_csv(pct_csv_path, sep=None, engine='python', encoding='utf-8-sig')
            pc.columns = pc.columns.str.strip()
            added = 0
            for _, row in pc.iterrows():
                item = str(row.get('Item/Account', '')).strip()
                pct  = pd.to_numeric(row.get('% Increase', np.nan), errors='coerce')
                if item and item not in pct_only:
                    pct_only[item] = pct
                    added += 1
            log.info(f"  prices.csv: {added:,} % Increase fallback entries.")
        except Exception as e:
            log.warning(f"  Failed to read prices.csv: {e}")

    return specific, item_map, pct_only


def _ingest_inv_transactions(force: bool = False):
    """
    Primary invoice ingest path.

    Source priority:
      1. Sales Orders New In.xlsx       — preferred Sage download (includes SO numbers)
      2. Inventory Transactions*.xlsx   — timestamped Sage exports
      3. Price Checker 3.xlsx Final     — legacy fallback (old format)

    Price enrichment:
      1. Master Price List.xlsx (Raw sheet) — item + customer specific
      2. Master Price List.xlsx             — item-only fallback (most recent row)
      3. prices.csv % Increase             — applied against historical average price
      4. NaN                               — item unknown in all references

    Falls back to _ingest_invoices() if no Inventory Transactions / Sales Orders file
    is found in Raws/.
    """
    filepath = _find_inventory_transactions_excel()

    if filepath is None:
        log.info("-- PASS 1C: No Inventory Transactions file in Raws/ "
                 "— falling back to Price Checker 3.xlsx Final sheet --")
        _ingest_invoices(force=force)
        return

    log.info(f"-- PASS 1C: Ingesting invoices from '{filepath.name}' --")

    new_hash = _hash_file(filepath)
    with get_connection() as conn:
        stored = conn.execute(
            "SELECT file_hash FROM ref_checksums WHERE table_name='invoices'"
        ).fetchone()
        old_hash = stored[0] if stored else None

    if not force and old_hash == new_hash:
        log.info(f"  invoices (Inventory Transactions): no change — skipping.")
        return

    # ── 1. Parse raw transaction lines ──
    df = _parse_inventory_transactions_xlsx(filepath)
    if df.empty:
        log.error("  0 rows parsed — aborting invoice ingest.")
        return

    # ── 2. Resolve Account codes → Customer Names ──
    acc_map = _get_account_customer_map()
    df['Customer Name'] = df['Account'].map(acc_map).fillna(df['Account'])

    # ── 3. Resolve Item Code → Description ──
    # Primary: Item List sheet (column H = VLOOKUP(,'Item List'!A:H,8,FALSE))
    # Fallback: products_ref derived table (only contains historically seen items)
    item_desc_map: dict = {}
    if EXCEL_PREFERRED.exists():
        try:
            xl = pd.ExcelFile(EXCEL_PREFERRED, engine='calamine')
            if 'Item List' in xl.sheet_names:
                il = _read_excel_robust(EXCEL_PREFERRED, sheet_name='Item List')
                il = _strip_junk_cols(il)
                il.columns = il.columns.str.strip()
                if 'Simple Code' in il.columns and len(il.columns) > 7:
                    name_col = il.columns[7]
                    item_desc_map = (
                        il.dropna(subset=['Simple Code'])
                        .set_index('Simple Code')[name_col]
                        .to_dict()
                    )
                    log.info(f"  Item descriptions: {len(item_desc_map):,} loaded from Item List.")
        except Exception as e:
            log.warning(f"  Item List description lookup failed: {e}")
    if not item_desc_map:
        try:
            with get_connection() as conn:
                pr = pd.read_sql('SELECT "Item Code", "Name" FROM products_ref', conn)
            item_desc_map = pr.set_index('Item Code')['Name'].to_dict()
            log.info(f"  Item descriptions: falling back to products_ref "
                     f"({len(item_desc_map):,} entries).")
        except Exception:
            pass
    df['Description'] = df['Item_Code'].map(item_desc_map).fillna(df['Item_Code'])
    missing_desc = (df['Description'] == df['Item_Code']).sum()
    if missing_desc:
        log.warning(f"  {missing_desc:,} items have no description in Item List "
                    f"— item code used as fallback.")

    # ── 4. Resolve Customer Name → Rep Name / Area / B2B group ──
    # Primary: Customers sheet (authoritative — covers new customers immediately)
    # Fallback: customers_ref derived table (only contains historically seen customers)
    rep_map: dict = {}
    area_map: dict = {}
    b2b_map: dict = {}
    if EXCEL_PREFERRED.exists():
        try:
            xl = pd.ExcelFile(EXCEL_PREFERRED, engine='calamine')
            if 'Customers' in xl.sheet_names:
                cl = _read_excel_robust(EXCEL_PREFERRED, sheet_name='Customers')
                cl = _strip_junk_cols(cl)
                cl.columns = cl.columns.str.strip()
                if 'Name' in cl.columns:
                    cl_keyed = cl.dropna(subset=['Name']).set_index('Name')
                    if 'Rep Name' in cl.columns:
                        rep_map  = cl_keyed['Rep Name'].to_dict()
                    area_col = next(
                        (c for c in ['Area Description', 'Customer Area', 'Area']
                         if c in cl.columns), None
                    )
                    if area_col:
                        area_map = cl_keyed[area_col].to_dict()
                    if 'B2B' in cl.columns:
                        b2b_map  = cl_keyed['B2B'].to_dict()
                    log.info(f"  Customer attributes: {len(rep_map):,} Rep / "
                             f"{len(area_map):,} Area / {len(b2b_map):,} B2B "
                             f"loaded from Customers sheet.")
        except Exception as e:
            log.warning(f"  Customers sheet Rep/Area/B2B lookup failed: {e}")
    if not rep_map:
        try:
            with get_connection() as conn:
                cr = pd.read_sql(
                    'SELECT "Customer Names","Rep Name","Customer Area","B2B" '
                    'FROM customers_ref', conn
                )
            rep_map  = cr.set_index('Customer Names')['Rep Name'].to_dict()
            area_map = cr.set_index('Customer Names')['Customer Area'].to_dict()
            b2b_map  = cr.set_index('Customer Names')['B2B'].to_dict()
            log.info(f"  Customer attributes: falling back to customers_ref.")
        except Exception:
            pass
    df['Rep Name'] = df['Customer Name'].map(rep_map).fillna('')
    df['Area']     = df['Customer Name'].map(area_map).fillna('')
    df['B2B']      = df['Customer Name'].map(b2b_map).fillna('')

    # ── 5. Look up master prices ──
    specific_map, item_map, pct_only_map = _build_invoice_price_map()

    # Build historical average unit price per item from the transactions table
    # Used as Old Price when an item is in prices.csv but not in Master Price List.xlsx
    hist_price_map: dict = {}
    try:
        with get_connection() as conn:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'transactions' in tbls:
                rows = conn.execute(
                    'SELECT "Item Code", AVG("Unit Price") FROM transactions '
                    'WHERE Tran = \'INV\' AND "Unit Price" > 0 '
                    'GROUP BY "Item Code"'
                ).fetchall()
                hist_price_map = {r[0]: r[1] for r in rows if r[0] and r[1]}
    except Exception as e:
        log.warning(f"  Historical price map failed: {e}")

    master_prices, old_prices, pct_increases = [], [], []

    for _, row in df.iterrows():
        item = str(row['Item_Code']).strip()
        cust = str(row['Customer Name']).strip().upper()

        # Priority 1: exact item + customer match
        v = specific_map.get((item, cust))
        # Priority 2: any customer for this item in Master Price List
        if v is None:
            v = item_map.get(item)

        if v is not None:
            master_prices.append(v[0])
            old_prices.append(v[1])
            pct_increases.append(v[2])
        elif item in pct_only_map:
            # Priority 3: % Increase from prices.csv; derive master price from history
            pct   = pct_only_map[item]
            old_p = hist_price_map.get(item, np.nan)
            new_p = (old_p * (1 + pct)
                     if pd.notna(old_p) and pd.notna(pct) else np.nan)
            master_prices.append(new_p)
            old_prices.append(old_p)
            pct_increases.append(pct)
        else:
            master_prices.append(np.nan)
            old_prices.append(np.nan)
            pct_increases.append(np.nan)

    df['Master Price (Excl)'] = master_prices
    df['Old Price']           = old_prices
    df['% Increase']          = pct_increases

    # ── 6. Compute variance columns ──
    df['Difference'] = df['Unit_Price'] - df['Master Price (Excl)']
    df['% Diff.']    = np.where(
        df['Unit_Price'] > 0,
        df['Difference'] / df['Unit_Price'],
        np.nan
    )
    df['% Diff Old'] = np.where(
        df['Old Price'].fillna(0) > 0,
        (df['Unit_Price'] - df['Old Price']) / df['Old Price'],
        np.nan
    )

    # ── 7. Map to invoices table schema ──
    df_out = pd.DataFrame({
        'Order No.':            df['Inv_No'],
        'Sales_Order_No':       df['SO_No'],
        'Customer Name':        df['Customer Name'],
        'Customer:':            df['Account'],
        'Description':          df['Description'],
        'Item/Account':         df['Item_Code'],
        'Date':                 df['Date'].dt.strftime('%d/%m/%Y').fillna(''),
        'Order QTY':            df['Quantity'],
        'Unit Price Excl':      df['Unit_Price'],
        'Order Tot':            df['Value'],
        'Confirm Tot':          df['Value'],
        'Total Qty':            df['Quantity'],
        'Master Price (Excl)':  df['Master Price (Excl)'],
        'Old Price':            df['Old Price'],
        'Difference':           df['Difference'],
        '% Diff.':              df['% Diff.'],
        '% Increase':           df['% Increase'],
        '% Diff Old':           df['% Diff Old'],
        'Rep Name':             df['Rep Name'],
        'Area':                 df['Area'],
        'Warehouse':            df.get('Warehouse', pd.Series([''] * len(df))),
        'State':                'Processed',
        'Tran':                 df['Tran'] if 'Tran' in df.columns else 'INV',
        'Concat':               df['Description'].astype(str) + df['Customer Name'].astype(str),
    })

    df_out['row_hash']    = df_out.apply(_hash_row, axis=1)
    df_out['ingested_at'] = _now()

    # ── 8. Write to DB ──
    rows_before = 0
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'invoices' in existing:
            rows_before = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

        df_out.to_sql('invoices', conn, if_exists='replace', index=False)

        conn.execute("""
            INSERT INTO ref_checksums (table_name, file_hash, last_updated, source_path)
            VALUES ('invoices', ?, ?, ?)
            ON CONFLICT(table_name) DO UPDATE SET
                file_hash=excluded.file_hash,
                last_updated=excluded.last_updated,
                source_path=excluded.source_path
        """, (new_hash, _now(), str(filepath)))

        conn.execute("""
            INSERT INTO ref_log
                (table_name, changed_at, old_hash, new_hash, rows_before, rows_after, notes)
            VALUES ('invoices',?,?,?,?,?,?)
        """, (_now(), old_hash, new_hash, rows_before, len(df_out),
              f"Inventory Transactions source{' (force)' if force else ''}"))

    _log_ingestion(str(filepath), 'invoices', total=len(df_out), inserted=len(df_out))
    log.info(f"  invoices: {len(df_out):,} rows from '{filepath.name}' "
             f"(was {rows_before:,}).")


# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE + DB DIAGNOSTICS
# ══════════════════════════════════════════════════════════════════════════════

def _print_source_diagnostics(excel_path: Path):
    """
    Print a comprehensive debug report about:
      - The source Excel file (path, size, modified date, sheets, date range)
      - The current database state (tables, row counts, last ingested date)
      - The output CSV files (existence, size, last modified)
    """
    sep = "=" * 65

    log.info(sep)
    log.info("  SOURCE FILE DIAGNOSTICS")
    log.info(sep)

    # ── Excel file info ──────────────────────────────────────────────────────
    if not excel_path.exists():
        log.error(f"  Source file not found: {excel_path}")
    else:
        stat = excel_path.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        log.info(f"  File     : {excel_path.name}")
        log.info(f"  Full path: {excel_path}")
        log.info(f"  Size     : {stat.st_size / 1_048_576:.1f} MB  ({stat.st_size:,} bytes)")
        log.info(f"  Modified : {mtime.strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            xl     = pd.ExcelFile(excel_path)
            sheets = xl.sheet_names
            log.info(f"  Sheets   : {sheets}")

            # Sheet 2 quick scan
            if 'Sheet 2' in sheets:
                df_hdr = pd.read_excel(excel_path, sheet_name='Sheet 2', nrows=0)
                clean_cols = [c for c in df_hdr.columns
                              if not str(c).startswith('Unnamed')
                              and str(c) not in ('nan', 'NaN', '')]
                log.info(f"  Sheet 2  : {len(clean_cols)} usable columns -> "
                         f"{clean_cols}")

                # Date range (read Date column only for speed)
                try:
                    df_dates = pd.read_excel(
                        excel_path, sheet_name='Sheet 2',
                        usecols=['Date'], engine='openpyxl'
                    )
                    df_dates['Date'] = pd.to_datetime(
                        df_dates['Date'], dayfirst=True, errors='coerce'
                    ).dropna()
                    df_dates = df_dates.dropna()
                    total_rows = len(df_dates)
                    date_min   = df_dates['Date'].min().strftime('%d %b %Y')
                    date_max   = df_dates['Date'].max().strftime('%d %b %Y')
                    years_in   = sorted(df_dates['Date'].dt.year.unique().tolist())
                    log.info(f"  Rows     : {total_rows:,} (valid dates)")
                    log.info(f"  Date min : {date_min}")
                    log.info(f"  Date max : {date_max}")
                    log.info(f"  Years    : {years_in}")
                except Exception as e:
                    log.warning(f"  Date scan failed: {e}")

            # Reference sheets present
            ref_present = [s for s in ['PackSize', 'Item List', 'Customers',
                                        'QIH', 'Reporter', 'Weights', 'Regions']
                           if s in sheets]
            log.info(f"  Ref sheets: {ref_present if ref_present else 'none detected'}")

        except Exception as e:
            log.error(f"  Could not inspect file: {e}")

    # ── Database state ───────────────────────────────────────────────────────
    log.info("")
    log.info("  DATABASE STATE")
    log.info("-" * 65)
    if not DB_PATH.exists():
        log.info(f"  DB does not exist yet: {DB_PATH}")
    else:
        db_stat = DB_PATH.stat()
        log.info(f"  DB path  : {DB_PATH}")
        log.info(f"  DB size  : {db_stat.st_size / 1_048_576:.2f} MB")
        log.info(f"  DB modified: "
                 f"{datetime.datetime.fromtimestamp(db_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            with get_connection() as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                for (t,) in tables:
                    cnt = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                    # For transactions / inventory, also show date range
                    extra = ''
                    if t == 'transactions':
                        try:
                            r = conn.execute(
                                "SELECT MIN(Date), MAX(Date) FROM transactions "
                                "WHERE Date IS NOT NULL AND Date != ''"
                            ).fetchone()
                            extra = f"  dates: {r[0]} -> {r[1]}"
                        except Exception:
                            pass
                    last_ing = ''
                    if t in ('transactions', 'inventory'):
                        try:
                            r2 = conn.execute(
                                f"SELECT MAX(ingested_at) FROM [{t}]"
                            ).fetchone()
                            last_ing = f"  last ingested: {r2[0]}"
                        except Exception:
                            pass
                    log.info(f"  {t:<28} {cnt:>8,} rows{extra}{last_ing}")

                # Last pipeline run
                last_run = conn.execute(
                    "SELECT MAX(run_at) FROM ingestion_log"
                ).fetchone()[0]
                log.info(f"  Last pipeline run: {last_run or 'never'}")

                # Ref checksum status
                ck_rows = conn.execute(
                    "SELECT table_name, last_updated FROM ref_checksums"
                ).fetchall()
                for ck in ck_rows:
                    log.info(f"  ref [{ck[0]}] last updated: {ck[1]}")

        except Exception as e:
            log.warning(f"  Could not read DB: {e}")

    # ── Invoice source file ──────────────────────────────────────────────────
    log.info("")
    log.info("  INVOICE SOURCE FILE")
    log.info("-" * 65)
    inv_path = _find_invoice_excel()
    if inv_path is None:
        log.info(f"  Invoice file : NOT FOUND  (expected: {INVOICE_PREFERRED.name})")
    else:
        inv_stat = inv_path.stat()
        inv_mtime = datetime.datetime.fromtimestamp(inv_stat.st_mtime)
        log.info(f"  File     : {inv_path.name}")
        log.info(f"  Full path: {inv_path}")
        log.info(f"  Size     : {inv_stat.st_size / 1_048_576:.1f} MB  ({inv_stat.st_size:,} bytes)")
        log.info(f"  Modified : {inv_mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            xl_inv = pd.ExcelFile(inv_path)
            log.info(f"  Sheets   : {xl_inv.sheet_names}")
            if 'Final' in xl_inv.sheet_names:
                df_inv_hdr = pd.read_excel(inv_path, sheet_name='Final', nrows=0)
                inv_cols = [c for c in df_inv_hdr.columns
                            if not str(c).startswith('Unnamed')]
                # Quick row count
                df_inv_cnt = pd.read_excel(inv_path, sheet_name='Final',
                                           usecols=[inv_cols[0]])
                log.info(f"  Final rows: {len(df_inv_cnt):,}")
                log.info(f"  Final cols: {inv_cols}")
        except Exception as e:
            log.warning(f"  Could not inspect invoice file: {e}")

    # ── Output CSV files ─────────────────────────────────────────────────────
    log.info("")
    log.info("  OUTPUT CSV STATUS")
    log.info("-" * 65)
    for label, path in CSV_OUT.items():
        if path.exists():
            s    = path.stat()
            mtime_csv = datetime.datetime.fromtimestamp(s.st_mtime)
            # Quick row count
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                    rows = sum(1 for _ in fh) - 1   # minus header
            except Exception:
                rows = -1
            log.info(f"  {path.name:<32} {rows:>8,} rows  "
                     f"{s.st_size / 1_048_576:.1f} MB  "
                     f"modified: {mtime_csv.strftime('%Y-%m-%d %H:%M')}")
        else:
            log.info(f"  {path.name:<32} NOT FOUND")

    log.info(sep)


# ══════════════════════════════════════════════════════════════════════════════
#  REFERENCE SHEET ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════════

def _enrich_from_excel(df: pd.DataFrame, excel_path: Path) -> pd.DataFrame:
    """
    Join the following reference sheets (if present) to the transaction data:

      PackSize  (Code → Pack Size Match)
      Item List (Simple Code → Items 2 [Sub-Category], B2B, Name)
      Customers (Name → Rep Name, Area Description)

    All joins are left-joins so rows with no match are retained with NaN.
    """
    try:
        xl = pd.ExcelFile(excel_path, engine='openpyxl')
    except Exception as e:
        log.warning(f"  Could not open {excel_path.name} for enrichment: {e}")
        return df

    sheets = xl.sheet_names

    # ── 1. PackSize → Pack Size Match ──────────────────────────────────────
    # Only join if 'Pack Size Match' is not already a native column in Sheet 2
    if 'PackSize' in sheets and 'Item Code' in df.columns \
            and 'Pack Size Match' not in df.columns:
        try:
            df_pack = xl.parse('PackSize')
            df_pack = _strip_junk_cols(df_pack)
            df_pack.columns = df_pack.columns.str.strip()
            if 'Code' in df_pack.columns and 'Pack Size' in df_pack.columns:
                df_pack = (df_pack[['Code', 'Pack Size']]
                           .rename(columns={'Code': 'Item Code',
                                            'Pack Size': 'Pack Size Match'})
                           .drop_duplicates('Item Code'))
                df = df.merge(df_pack, on='Item Code', how='left')
                matched = df['Pack Size Match'].notna().sum()
                log.info(f"    PackSize join: {matched:,} / {len(df):,} rows matched.")
            else:
                log.debug("    PackSize: 'Code' or 'Pack Size' column not found, skipping.")
        except Exception as e:
            log.warning(f"    PackSize join failed (non-fatal): {e}")
    elif 'Pack Size Match' in df.columns:
        log.info(f"    PackSize: native column present in Sheet 2, join skipped.")

    # ── 2. Item List → Items 2, B2B, Name ─────────────────────────────────
    if 'Item List' in sheets and 'Item Code' in df.columns:
        try:
            df_items = xl.parse('Item List')
            df_items = _strip_junk_cols(df_items)
            df_items.columns = df_items.columns.str.strip()

            if 'Simple Code' in df_items.columns:
                # Item List hierarchy (three-level):
                #   Sub-Sub-Category → Items 2 (Grp):  "Basins - 304", "Crates - Bottle"
                #   3*Sub Category   → Items   (Sub):  "Basins - 38 - Lotus"
                #   Description      → Name    (Item): "BASIN NO 38 LOTUS - BUNDLE"
                # Sub-Category is too broad ("Basins", "Crates") — not used for Items 2.
                keep_cols  = ['Simple Code']
                rename_map = {'Simple Code': 'Item Code'}

                # Item Category: dropped on re-enrich, so it must be re-joined here
                # or it is silently lost from the transactions table.
                if 'Item Category' in df_items.columns:
                    keep_cols.append('Item Category')

                # Items 2 (Grp): prefer Sub-Sub-Category, fall back to Sub-Category
                if 'Sub-Sub-Category' in df_items.columns:
                    keep_cols.append('Sub-Sub-Category')
                    rename_map['Sub-Sub-Category'] = 'Items 2'
                elif 'Sub-Category' in df_items.columns:
                    keep_cols.append('Sub-Category')
                    rename_map['Sub-Category'] = 'Items 2'
                elif 'Group' in df_items.columns:
                    keep_cols.append('Group')
                    rename_map['Group'] = 'Items 2'

                # Items (Sub): use 3*Sub Category for the mid-level variant name
                if '3*Sub Category' in df_items.columns:
                    keep_cols.append('3*Sub Category')
                    rename_map['3*Sub Category'] = 'Items'

                # Map B2B vs B2C directly to the B2B column (not B2B_Ref).
                # The dashboard segment filter reads 'B2B'; Sage records have
                # no native B2B column, so this join is the only source.
                if 'B2B vs B2C' in df_items.columns:
                    keep_cols.append('B2B vs B2C')
                    rename_map['B2B vs B2C'] = 'B2B'

                # Capture the item display name (column 'Description' or
                # any column whose header suggests it is a product name).
                _name_candidates = ['Description', 'Product Name', 'Item Name',
                                    'Name', 'Product Description']
                for _nc in _name_candidates:
                    if _nc in df_items.columns and _nc not in keep_cols:
                        keep_cols.append(_nc)
                        rename_map[_nc] = 'Name'
                        break

                df_items = (df_items[keep_cols]
                            .rename(columns=rename_map)
                            .drop_duplicates('Item Code'))

                # Only merge columns that don't already exist in df
                new_cols = [c for c in df_items.columns
                            if c != 'Item Code' and c not in df.columns]
                if new_cols:
                    df = df.merge(df_items[['Item Code'] + new_cols],
                                  on='Item Code', how='left')
                    for nc in new_cols:
                        matched = df[nc].notna().sum()
                        log.info(f"    Item List join '{nc}': {matched:,} / {len(df):,} rows matched.")
        except Exception as e:
            log.warning(f"    Item List join failed (non-fatal): {e}")

    # ── 2b. item_overrides table (Admin Panel additions — override Item List) ──
    try:
        with get_connection() as conn:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'item_overrides' in tbls and 'Item Code' in df.columns:
                df_io = pd.read_sql(
                    'SELECT item_code, name, items_2, items, b2b FROM item_overrides',
                    conn
                )
                df_io.columns = df_io.columns.str.strip()
                df_io = df_io.rename(columns={
                    'item_code': 'Item Code', 'name': '_io_Name',
                    'items_2': '_io_Items2', 'items': '_io_Items',
                    'b2b': '_io_B2B',
                })
                df_io['Item Code'] = df_io['Item Code'].astype(str).str.strip()
                df = df.merge(df_io, on='Item Code', how='left')

                # Apply overrides for rows where item_overrides has a value
                for src, dst in [('_io_Name', 'Name'), ('_io_Items2', 'Items 2'),
                                  ('_io_Items', 'Items'), ('_io_B2B', 'B2B')]:
                    if src in df.columns:
                        mask = df[src].notna() & (df[src].astype(str).str.strip() != '')
                        if dst not in df.columns:
                            df[dst] = None
                        if mask.any():
                            df.loc[mask, dst] = df.loc[mask, src]
                            log.info(
                                f"    item_overrides '{dst}': applied {int(mask.sum()):,} DB corrections."
                            )
                        df = df.drop(columns=[src], errors='ignore')
    except Exception as _e:
        log.warning(f"    item_overrides load failed (non-fatal): {_e}")

    # ── 3. Customers → Rep Name, Customer Area, Location, Distance, Lat, Lon ──
    if 'Customers' in sheets and 'Customer Names' in df.columns:
        try:
            df_cust = xl.parse('Customers')
            df_cust = _strip_junk_cols(df_cust)
            df_cust.columns = df_cust.columns.str.strip()

            if 'Name' in df_cust.columns:
                keep_cols  = ['Name']
                rename_map = {'Name': 'Customer Names'}

                if 'Rep Name' in df_cust.columns:
                    keep_cols.append('Rep Name')
                if 'Area Description' in df_cust.columns:
                    keep_cols.append('Area Description')
                    rename_map['Area Description'] = 'Customer Area'
                # Location / geo columns — fill Sage rows that have blanks here
                for _geo in ['Location', 'Distance from Nairobi', 'Lat', 'Lon']:
                    if _geo in df_cust.columns:
                        keep_cols.append(_geo)
                # B2B/B2C is a CUSTOMER-level attribute. The Customers sheet is the
                # authoritative source and overrides the item-level 'B2B vs B2C'
                # value joined from the Item List, so re-classifying a customer
                # actually takes effect on re-enrich.
                if 'B2B' in df_cust.columns:
                    keep_cols.append('B2B')

                df_cust = (df_cust[keep_cols]
                           .rename(columns=rename_map))
                df_cust['Customer Names'] = df_cust['Customer Names'].astype(str).str.strip()
                df_cust = df_cust.drop_duplicates('Customer Names')

                # Columns not yet in df → merge directly (new column for all rows)
                new_cols  = [c for c in df_cust.columns
                             if c != 'Customer Names' and c not in df.columns]
                # Columns already in df → merge under temp name, then fill blank rows only
                fill_cols   = [c for c in df_cust.columns
                               if c != 'Customer Names' and c in df.columns]
                fill_rename = {c: f'_cust_{c}' for c in fill_cols}

                df_cust_m  = df_cust.rename(columns=fill_rename)
                merge_cols = new_cols + list(fill_rename.values())
                if merge_cols:
                    df = df.merge(df_cust_m[['Customer Names'] + merge_cols],
                                  on='Customer Names', how='left')
                    for nc in new_cols:
                        matched = df[nc].notna().sum()
                        log.info(f"    Customers join '{nc}': {matched:,} / {len(df):,} rows matched.")
                    # Customer-level attributes the Customers sheet owns outright:
                    # when the sheet has a value it OVERWRITES the existing one, so
                    # re-classifications (B2B->B2C) and Location/Distance corrections
                    # propagate on re-enrich. Everything else only fills blanks.
                    _authoritative = {'B2B', 'Location', 'Distance from Nairobi'}
                    for orig_col, tmp_col in fill_rename.items():
                        if tmp_col in df.columns:
                            has_ref = (df[tmp_col].notna() &
                                       ~df[tmp_col].astype(str).str.strip().isin(['', 'nan']))
                            if orig_col in _authoritative:
                                target, verb = has_ref, 'override'
                            else:
                                blank = (df[orig_col].isna() |
                                         df[orig_col].astype(str).str.strip().isin(['', 'nan', 'Unknown']))
                                target, verb = blank & has_ref, 'fill'
                            if target.any():
                                df.loc[target, orig_col] = df.loc[target, tmp_col]
                                log.info(f"    Customers join '{orig_col}' {verb}: {int(target.sum()):,} rows.")
                            df = df.drop(columns=[tmp_col])
        except Exception as e:
            log.warning(f"    Customers join failed (non-fatal): {e}")

    # ── 3b. Account-code based Customers override ─────────────────────────────
    # If the Customers sheet has an 'Account' column, use it as a second join
    # key so that account code → rep mappings override the name-based result.
    # This lets you re-assign a customer code to a different rep without
    # needing the customer name to match exactly.
    if 'Customers' in sheets and 'Account' in df.columns:
        try:
            df_cust2 = xl.parse('Customers')
            df_cust2 = _strip_junk_cols(df_cust2)
            df_cust2.columns = df_cust2.columns.str.strip()
            # The account-code column is named 'Customer' in the master Customers
            # sheet (values like Z099/A); older layouts used 'Account'. Without
            # this the whole account-code override silently never ran.
            acct_key_col = next((c for c in ['Account', 'Customer', 'Customer Code', 'Code']
                                 if c in df_cust2.columns), None)
            if acct_key_col:
                acct_cols = [acct_key_col]
                if 'Rep Name' in df_cust2.columns:
                    acct_cols.append('Rep Name')
                if 'Area Description' in df_cust2.columns:
                    acct_cols.append('Area Description')
                # B2B is resolved at ACCOUNT level: Z099 and Z099/A share the same
                # customer NAME but carry different segments, so only the account
                # code can tell them apart. This must win over the name-based join.
                if 'B2B' in df_cust2.columns:
                    acct_cols.append('B2B')
                df_cust2 = (df_cust2[acct_cols]
                            .dropna(subset=[acct_key_col])
                            .rename(columns={'Area Description': 'Customer Area',
                                             acct_key_col: 'Account'})
                            .drop_duplicates('Account'))
                df_cust2['Account'] = df_cust2['Account'].astype(str).str.strip()
                df['_acct_key'] = df['Account'].astype(str).str.strip()
                df = df.merge(
                    df_cust2.rename(columns={
                        'Account':      '_acct_key',
                        'Rep Name':     '_acct_Rep',
                        'Customer Area':'_acct_Area',
                        'B2B':          '_acct_B2B',
                    }),
                    on='_acct_key', how='left'
                )
                df = df.drop(columns=['_acct_key'], errors='ignore')
                for src, dst in [('_acct_Rep', 'Rep Name'), ('_acct_Area', 'Customer Area'),
                                 ('_acct_B2B', 'B2B')]:
                    if src in df.columns:
                        has_val = df[src].notna() & (df[src].astype(str).str.strip() != '')
                        if dst not in df.columns:
                            df[dst] = None
                        if has_val.any():
                            df.loc[has_val, dst] = df.loc[has_val, src]
                            log.info(f"    Customers account-code override '{dst}': {int(has_val.sum()):,} rows.")
                        df = df.drop(columns=[src], errors='ignore')
        except Exception as _e:
            log.warning(f"    Customers account-code override failed (non-fatal): {_e}")

    # ── 3c. Weights sheet → Weight (per-transaction gross weight) ─────────────
    # Sheet columns: Code (item code) + Weight (kg per piece or per pack).
    # Joined on Item Code; only fills blank/zero Weight rows so existing data wins.
    if 'Weights' in sheets and 'Item Code' in df.columns:
        try:
            df_wt = xl.parse('Weights')
            df_wt = _strip_junk_cols(df_wt)
            df_wt.columns = df_wt.columns.str.strip()
            code_col   = next((c for c in ['Code', 'Simple Code', 'Item Code', 'Item'] if c in df_wt.columns), None)
            weight_col = next((c for c in ['Weight', 'Weight (kg)', 'Kg'] if c in df_wt.columns), None)
            if code_col and weight_col:
                df_wt = (df_wt[[code_col, weight_col]]
                         .rename(columns={code_col: 'Item Code', weight_col: '_wt_Weight'})
                         .drop_duplicates('Item Code'))
                df_wt['Item Code'] = df_wt['Item Code'].astype(str).str.strip()
                df_wt['_wt_Weight'] = pd.to_numeric(df_wt['_wt_Weight'], errors='coerce')
                df = df.merge(df_wt, on='Item Code', how='left')
                if 'Weight' not in df.columns:
                    df['Weight'] = 0
                blank_wt = (df['Weight'].isna() | (pd.to_numeric(df['Weight'], errors='coerce').fillna(0) == 0))
                filled = blank_wt & df['_wt_Weight'].notna()
                if filled.any():
                    # The Weights sheet stores weight PER ORDER UNIT (per Quantity),
                    # so the transaction weight is Quantity x per-unit weight.
                    qty_w = pd.to_numeric(df.get('Quantity', pd.Series(0, index=df.index)),
                                          errors='coerce').fillna(0)
                    df.loc[filled, 'Weight'] = (
                        df.loc[filled, '_wt_Weight'] * qty_w[filled]
                    ).round(4)
                    log.info(f"    Weights sheet: filled {int(filled.sum()):,} blank/zero Weight rows "
                             f"(Quantity x per-unit weight).")
                df = df.drop(columns=['_wt_Weight'], errors='ignore')
            else:
                log.debug("    Weights: expected 'Code'/'Weight' columns not found, skipping.")
        except Exception as _e:
            log.warning(f"    Weights sheet join failed (non-fatal): {_e}")

    # ── 3d. customer_overrides table (Admin Panel additions — fill Rep/Area gaps) ──
    try:
        with get_connection() as conn:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'customer_overrides' in tbls and 'Customer Names' in df.columns:
                df_co = pd.read_sql(
                    'SELECT customer_name, rep_name, customer_area, b2b FROM customer_overrides',
                    conn
                )
                df_co = df_co.rename(columns={
                    'customer_name': 'Customer Names',
                    'rep_name':      '_co_Rep',
                    'customer_area': '_co_Area',
                    'b2b':           '_co_B2B',
                })
                df_co['Customer Names'] = df_co['Customer Names'].astype(str).str.strip()
                df = df.merge(df_co, on='Customer Names', how='left')

                for src, dst in [('_co_Rep', 'Rep Name'), ('_co_Area', 'Customer Area'),
                                  ('_co_B2B', 'B2B')]:
                    if src in df.columns:
                        if dst not in df.columns:
                            df[dst] = None
                        blank = df[dst].isna() | df[dst].astype(str).str.strip().isin(['', 'nan'])
                        filled = blank & df[src].notna()
                        if filled.any():
                            df.loc[filled, dst] = df.loc[filled, src]
                            log.info(
                                f"    customer_overrides '{dst}': filled {int(filled.sum()):,} gaps."
                            )
                        df = df.drop(columns=[src], errors='ignore')
    except Exception as _e:
        log.warning(f"    customer_overrides load failed (non-fatal): {_e}")

    # ── Drop _x / _y duplicate columns left by any merge ──────────────────
    # If both Pack Size Match_x and _y exist, keep _y (reference sheet value)
    # and rename to Pack Size Match, then drop both suffix columns.
    suffix_pairs = {}
    for c in df.columns:
        if c.endswith('_x') or c.endswith('_y'):
            base = c[:-2]
            suffix_pairs.setdefault(base, []).append(c)

    for base, variants in suffix_pairs.items():
        y_col = f'{base}_y'
        x_col = f'{base}_x'
        if base not in df.columns and y_col in df.columns:
            df = df.rename(columns={y_col: base})
            variants.remove(y_col)
        drop_these = [v for v in variants if v in df.columns]
        if drop_these:
            df = df.drop(columns=drop_these)
            log.debug(f"    Dropped merge-artefact columns: {drop_these}")

    return df


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE SETUP
# ══════════════════════════════════════════════════════════════════════════════

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    log.info(f"Initialising database at {DB_PATH}")
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ref_checksums (
                table_name   TEXT PRIMARY KEY,
                file_hash    TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                source_path  TEXT
            );
            CREATE TABLE IF NOT EXISTS ref_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name   TEXT    NOT NULL,
                changed_at   TEXT    NOT NULL,
                old_hash     TEXT,
                new_hash     TEXT    NOT NULL,
                rows_before  INTEGER DEFAULT 0,
                rows_after   INTEGER DEFAULT 0,
                notes        TEXT
            );
            CREATE TABLE IF NOT EXISTS ingestion_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at        TEXT    NOT NULL,
                source_file   TEXT,
                table_name    TEXT,
                rows_total    INTEGER DEFAULT 0,
                rows_inserted INTEGER DEFAULT 0,
                rows_updated  INTEGER DEFAULT 0,
                rows_skipped  INTEGER DEFAULT 0,
                status        TEXT    DEFAULT 'ok',
                notes         TEXT
            );
            CREATE TABLE IF NOT EXISTS validation_errors (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at       TEXT    NOT NULL,
                table_name   TEXT,
                auto_id      TEXT,
                date         TEXT,
                item_code    TEXT,
                account      TEXT,
                error_type   TEXT,
                error_detail TEXT
            );
        """)
    log.info("Database schema ready.")


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 1A — INGEST TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _ingest_transactions(force: bool = False):
    log.info("-- PASS 1A: Ingesting transactions --")

    excel_path = _find_excel()   # uses override baked in by main()
    if not excel_path.exists():
        log.error(f"Source Excel not found: {excel_path}")
        return

    log.info(f"  Reading '{excel_path.name}' -> Sheet 2 ...")
    try:
        df = _read_excel_robust(excel_path, sheet_name='Sheet 2')
    except Exception as e:
        log.error(f"  Failed to read Sheet 2: {e}")
        return

    df = _strip_junk_cols(df)
    log.info(f"  Sheet 2: {len(df):,} rows, {len(df.columns)} columns: "
             f"{list(df.columns)}")

    # ── DATE NORMALISATION ──
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
        n_bad = df['Date'].isna().sum()
        if n_bad:
            log.warning(f"  {n_bad:,} rows dropped: unparseable date.")
        df = df.dropna(subset=['Date'])
        df['Date'] = df['Date'].dt.strftime('%d/%m/%Y')

    # ── NUMERIC CLEANING ──
    for col in ['Value', 'QTY PCS', 'Quantity', 'Weight', 'Unit Price',
                'Distance from Nairobi', 'Lat', 'Lon']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # ── STRING CLEANING ──
    for col in ['Items', 'Name', 'Items 2', 'Location', 'Sales Rep',
                'Customer Names', 'B2B', 'Item Category', 'Item Code']:
        if col in df.columns:
            df[col] = (df[col].astype(str).str.strip()
                       .replace({'nan': 'Unknown', 'NaN': 'Unknown', '': 'Unknown'}))

    # ── ENRICH FROM REFERENCE SHEETS ──
    log.info(f"  Enriching from reference sheets in '{excel_path.name}' ...")
    df = _enrich_from_excel(df, excel_path)

    # ── AUTO-ID (use existing or generate surrogate) ──
    if 'AutoID' not in df.columns or df['AutoID'].isna().all():
        log.warning("  AutoID not found — generating surrogate keys.")
        df['AutoID'] = range(1, len(df) + 1)
    df['AutoID'] = df['AutoID'].astype(str).str.strip()

    # ── ROW HASH (computed after enrichment, excluding AutoID) ──
    hash_cols   = [c for c in df.columns if c != 'AutoID']
    df['row_hash']    = df[hash_cols].apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    _upsert_table(df, 'transactions', pk_col='AutoID', force=force)


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 1A — INGEST INVENTORY
# ══════════════════════════════════════════════════════════════════════════════

def _ingest_inventory(force: bool = False):
    log.info("-- PASS 1A: Ingesting inventory --")

    excel_path = _find_excel()
    if not excel_path.exists():
        return

    log.info(f"  Reading '{excel_path.name}' -> QIH ...")
    try:
        xl     = pd.ExcelFile(excel_path, engine='openpyxl')
        if 'QIH' not in xl.sheet_names:
            log.warning("  QIH sheet not found in Excel.")
            return
        df = xl.parse('QIH')
    except Exception as e:
        log.error(f"  Failed to read QIH: {e}")
        return

    df = _strip_junk_cols(df)

    # Normalise known numeric columns
    for col in ['Qty On Hand', 'Reorder Lvl', 'MaxLvl', 'QtyOnSO']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Keep only columns we know, ignore any extra
    present = [c for c in INV_KEEP_COLS if c in df.columns]
    df = df[present].copy()

    df['row_hash']    = df.apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    log.info(f"  Inventory rows: {len(df):,}")
    with get_connection() as conn:
        df.to_sql('inventory', conn, if_exists='replace', index=False)
    _log_ingestion(str(excel_path), 'inventory', total=len(df), inserted=len(df))
    log.info(f"  inventory table refreshed ({len(df):,} rows).")


# ══════════════════════════════════════════════════════════════════════════════
#  SCHEMA EVOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def _evolve_schema(conn: sqlite3.Connection, table: str, df: pd.DataFrame):
    """
    Add any columns present in df but missing from the SQLite table.
    SQLite does not support removing columns, so extra DB columns are left as-is
    (they'll appear as NULL for new rows, which is safe).
    """
    existing_cols = {r[1] for r in
                     conn.execute(f"PRAGMA table_info([{table}])").fetchall()}
    added = []
    for col in df.columns:
        if col not in existing_cols:
            dtype = df[col].dtype
            if pd.api.types.is_integer_dtype(dtype):
                sql_type = 'INTEGER'
            elif pd.api.types.is_float_dtype(dtype):
                sql_type = 'REAL'
            else:
                sql_type = 'TEXT'
            try:
                conn.execute(f'ALTER TABLE [{table}] ADD COLUMN [{col}] {sql_type}')
                added.append(col)
            except sqlite3.OperationalError:
                pass  # column may have been added in a concurrent step
    if added:
        log.info(f"  Schema evolved [{table}]: added {len(added)} column(s): {added}")


# ══════════════════════════════════════════════════════════════════════════════
#  UPSERT WITH INCREMENTAL CHANGE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def _upsert_table(df: pd.DataFrame, table: str, pk_col: str, force: bool = False):
    """
    Upsert df into the SQLite table.
    - If table does not exist (or force=True): full replace.
    - Otherwise: incremental — compare row hashes, insert/update only changes.
    - Schema evolution: ALTER TABLE if df has new columns.
    """
    with get_connection() as conn:
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        # ── Full load path ──
        if table not in existing_tables or force:
            df.to_sql(table, conn, if_exists='replace', index=False)
            _log_ingestion(str(_find_excel()), table,
                           total=len(df), inserted=len(df))
            log.info(f"  {table}: {len(df):,} rows written (full load).")
            return

        # ── Incremental path ──
        # 1. Evolve schema first so the table can accept all new columns
        _evolve_schema(conn, table, df)

        # 2. Read existing primary keys and hashes
        existing = pd.read_sql(
            f"SELECT [{pk_col}], row_hash FROM [{table}]", conn
        )
        existing_map = dict(zip(
            existing[pk_col].astype(str),
            existing['row_hash']
        ))

        new_rows, updated_rows, skipped = [], [], 0
        for _, row in df.iterrows():
            pk_val = str(row[pk_col])
            if pk_val not in existing_map:
                new_rows.append(row)
            elif existing_map[pk_val] != row['row_hash']:
                updated_rows.append(row)
            else:
                skipped += 1

        # 3. Insert genuinely new rows
        if new_rows:
            pd.DataFrame(new_rows).to_sql(
                table, conn, if_exists='append', index=False
            )

        # 4. Update changed rows (delete old + insert new)
        #    SQLite has a 999 bind-variable limit, so batch the DELETE.
        if updated_rows:
            upd_df = pd.DataFrame(updated_rows)
            SQLITE_MAX_VARS = 900
            pk_values = list(upd_df[pk_col].astype(str))
            for i in range(0, len(pk_values), SQLITE_MAX_VARS):
                batch = pk_values[i:i + SQLITE_MAX_VARS]
                placeholders = ','.join('?' * len(batch))
                conn.execute(
                    f"DELETE FROM [{table}] WHERE [{pk_col}] IN ({placeholders})",
                    batch
                )
            upd_df.to_sql(table, conn, if_exists='append', index=False)

    _log_ingestion(str(_find_excel()), table,
                   total=len(df),
                   inserted=len(new_rows),
                   updated=len(updated_rows),
                   skipped=skipped)
    log.info(f"  {table}: +{len(new_rows):,} new | "
             f"~{len(updated_rows):,} updated | "
             f"={skipped:,} unchanged.")


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 1B — REFERENCE TABLES
# ══════════════════════════════════════════════════════════════════════════════

def _ingest_reference_tables(force: bool = False):
    """Hash-check prices.csv (and any other REF_SOURCES). Reload only on change."""
    log.info("-- PASS 1B: Checking reference tables --")
    for table_name, source_path in REF_SOURCES.items():
        if not source_path.exists():
            log.warning(f"  Reference file not found, skipping: {source_path.name}")
            continue

        new_hash = _hash_file(source_path)
        with get_connection() as conn:
            stored = conn.execute(
                "SELECT file_hash FROM ref_checksums WHERE table_name=?",
                (table_name,)
            ).fetchone()
            old_hash = stored[0] if stored else None

        if not force and old_hash == new_hash:
            log.info(f"  {table_name}: no change detected (hash match).")
            continue

        log.info(f"  {table_name}: change detected -- reloading ...")
        df_ref = pd.read_csv(source_path)
        df_ref = _strip_junk_cols(df_ref)
        df_ref.columns = df_ref.columns.str.strip()
        df_ref['row_hash']   = df_ref.apply(_hash_row, axis=1)
        df_ref['valid_from'] = _now()

        rows_before = 0
        with get_connection() as conn:
            existing_tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if table_name in existing_tables:
                rows_before = conn.execute(
                    f"SELECT COUNT(*) FROM [{table_name}]"
                ).fetchone()[0]

            df_ref.to_sql(table_name, conn, if_exists='replace', index=False)
            conn.execute("""
                INSERT INTO ref_checksums (table_name, file_hash, last_updated, source_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(table_name) DO UPDATE SET
                    file_hash=excluded.file_hash,
                    last_updated=excluded.last_updated,
                    source_path=excluded.source_path
            """, (table_name, new_hash, _now(), str(source_path)))
            conn.execute("""
                INSERT INTO ref_log
                    (table_name, changed_at, old_hash, new_hash,
                     rows_before, rows_after, notes)
                VALUES (?,?,?,?,?,?,?)
            """, (table_name, _now(), old_hash, new_hash,
                  rows_before, len(df_ref),
                  'auto-detected change' if not force else 'force reload'))

        log.info(f"  {table_name}: {len(df_ref):,} rows loaded "
                 f"(was {rows_before:,} rows).")


def _build_products_ref():
    """Derive products_ref from unique Items in transactions."""
    log.info("-- PASS 1B: Building derived products_ref --")
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'transactions' not in existing:
            log.warning("  transactions not found; skipping products_ref.")
            return

        trans_cols = {r[1] for r in
                      conn.execute('PRAGMA table_info(transactions)').fetchall()}
        wanted = {'Items', 'Items 2', 'Item Category', 'Name', 'Item Code'}
        avail  = wanted & trans_cols
        if 'Items' not in avail:
            log.warning("  Items column not found; skipping products_ref.")
            return

        cols_sql = ', '.join(f'[{c}]' for c in avail)
        df = pd.read_sql(
            f"SELECT DISTINCT {cols_sql} FROM transactions", conn
        )

    if df.empty:
        return
    df = df.drop_duplicates(subset=['Items']).reset_index(drop=True)
    df['derived_at'] = _now()

    with get_connection() as conn:
        df.to_sql('products_ref', conn, if_exists='replace', index=False)
    log.info(f"  products_ref: {len(df):,} unique items catalogued.")


def _build_customers_ref():
    """Derive customers_ref from unique Customer Names in transactions."""
    log.info("-- PASS 1B: Building derived customers_ref --")
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'transactions' not in existing:
            return

        trans_cols = {r[1] for r in
                      conn.execute('PRAGMA table_info(transactions)').fetchall()}
        wanted = {'Customer Names', 'B2B', 'Location', 'Rep Name', 'Customer Area'}
        avail  = wanted & trans_cols
        if 'Customer Names' not in avail:
            return

        cols_sql = ', '.join(f'[{c}]' for c in avail)
        df = pd.read_sql(
            f"SELECT DISTINCT {cols_sql} FROM transactions", conn
        )

    df = df.drop_duplicates(subset=['Customer Names']).reset_index(drop=True)
    df['derived_at'] = _now()

    with get_connection() as conn:
        df.to_sql('customers_ref', conn, if_exists='replace', index=False)
    log.info(f"  customers_ref: {len(df):,} unique customers catalogued.")


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 2A — FORMULA ENGINE
# ══════════════════════════════════════════════════════════════════════════════

_REGION_GEO_CACHE = None


def _region_geo_lookup():
    """Region name (lowercased) -> (lat_map, lon_map) from the master Regions
    sheet. The Regions sheet is the authoritative Region -> Latitude/Longitude
    table; used to fill Lat/Lon for Locations whose customers carry no
    coordinates (e.g. Homabay, Narok). Cached for the process lifetime."""
    global _REGION_GEO_CACHE
    if _REGION_GEO_CACHE is not None:
        return _REGION_GEO_CACHE

    lat_map, lon_map = {}, {}
    try:
        path = _find_excel()
        if path.exists():
            dr = pd.read_excel(path, sheet_name='Regions')
            dr.columns = dr.columns.str.strip()
            if {'Region', 'Latitude', 'Longitude'}.issubset(dr.columns):
                dr = dr.dropna(subset=['Region'])
                key = dr['Region'].astype(str).str.strip().str.lower()
                lat_map = dict(zip(key, pd.to_numeric(dr['Latitude'], errors='coerce')))
                lon_map = dict(zip(key, pd.to_numeric(dr['Longitude'], errors='coerce')))
    except Exception as e:
        log.warning(f"    Regions geo lookup failed (non-fatal): {e}")

    _REGION_GEO_CACHE = (lat_map, lon_map)
    return _REGION_GEO_CACHE


def _apply_formulas(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all calculated columns. All division is guarded against inf/NaN."""

    # Avg_Price = Value / Quantity (price per order unit)
    qty_num = pd.to_numeric(df.get('Quantity', pd.Series(0, index=df.index)), errors='coerce').fillna(0)
    df['Avg_Price'] = (
        df['Value'] / qty_num.replace(0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).fillna(0).round(4)

    # Unit Price = Value / Quantity (same as Avg_Price; retained as separate column)
    df['Unit Price'] = df['Avg_Price']

    # Price deviation vs Unit Price
    df['Price_Deviation_Pct'] = np.where(
        df['Unit Price'] > 0,
        ((df['Avg_Price'] - df['Unit Price']) / df['Unit Price'] * 100)
          .replace([np.inf, -np.inf], np.nan).fillna(0).round(2),
        0.0
    )

    # Price_Flag: deviation > 5% from reference
    df['Price_Flag'] = df['Price_Deviation_Pct'].abs() > 5.0

    # Weight per unit
    if 'Weight' in df.columns:
        df['Weight_Per_Unit'] = (
            df['Weight'] / df['QTY PCS']
        ).replace([np.inf, -np.inf], np.nan).fillna(0).round(4)

    # Revenue per km (logistics efficiency proxy)
    dist_col = next(
        (c for c in df.columns if 'distance' in c.lower()), None
    )
    if dist_col:
        df['Rev_Per_Km'] = (
            df['Value'] / df[dist_col]
        ).replace([np.inf, -np.inf], np.nan).fillna(0).round(2)

    # ── Location → Lat/Lon fallback ───────────────────────────────────────────
    # The Customers sheet maps customer → city name (Location) but does not
    # carry coordinates.  Lat/Lon are city-level constants, so we derive them
    # from existing rows in the same DataFrame that already have coordinates.
    # This self-heals new Sage rows: as long as at least one historical row
    # for a given Location has Lat/Lon, every new row for that city gets filled.
    for geo_col in ['Lat', 'Lon']:
        if geo_col not in df.columns:
            df[geo_col] = np.nan
        df[geo_col] = pd.to_numeric(df[geo_col], errors='coerce')

    if 'Location' in df.columns:
        loc_lat = (df.dropna(subset=['Lat'])
                     .groupby('Location')['Lat'].first())
        loc_lon = (df.dropna(subset=['Lon'])
                     .groupby('Location')['Lon'].first())

        blank_lat = df['Lat'].isna() & df['Location'].notna() & \
                    ~df['Location'].astype(str).str.strip().isin(['', 'nan', 'Unknown'])
        if blank_lat.any():
            df.loc[blank_lat, 'Lat'] = df.loc[blank_lat, 'Location'].map(loc_lat)
            df.loc[blank_lat, 'Lon'] = df.loc[blank_lat, 'Location'].map(loc_lon)
            filled = df.loc[blank_lat, 'Lat'].notna().sum()
            log.info(f"    Geo fallback: filled Lat/Lon for {int(filled):,} rows via Location lookup.")

        # Authoritative fill from the Regions sheet for any Location still missing
        # coordinates (cities whose customers carry no Lat/Lon, e.g. Homabay, Narok).
        still_blank = df['Lat'].isna() & df['Location'].notna() & \
                      ~df['Location'].astype(str).str.strip().isin(['', 'nan', 'Unknown'])
        if still_blank.any():
            lat_map, lon_map = _region_geo_lookup()
            if lat_map:
                loc_key = df.loc[still_blank, 'Location'].astype(str).str.strip().str.lower()
                df.loc[still_blank, 'Lat'] = loc_key.map(lat_map)
                df.loc[still_blank, 'Lon'] = loc_key.map(lon_map)
                filled_r = df.loc[still_blank, 'Lat'].notna().sum()
                log.info(f"    Geo fallback: filled Lat/Lon for {int(filled_r):,} rows via Regions sheet.")

    return df


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 2B — VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _run_validation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Data-quality validation — flags structural/data errors only.
    Pricing deviations are intentionally excluded (business decisions, not errors).

    Each error record includes: Date, Item Code, Account (Z099/N002 etc.)
    so issues can be traced back to the source transaction immediately.
    """
    errors = []
    today  = pd.Timestamp.today().normalize()

    # Evolve validation_errors table to include the new columns if this is
    # an existing DB that predates the schema change.
    with get_connection() as conn:
        existing_vcols = {r[1] for r in
                          conn.execute("PRAGMA table_info(validation_errors)").fetchall()}
        for col, ctype in [('date', 'TEXT'), ('item_code', 'TEXT'), ('account', 'TEXT')]:
            if col not in existing_vcols:
                try:
                    conn.execute(f"ALTER TABLE validation_errors ADD COLUMN {col} {ctype}")
                except Exception:
                    pass

    def _flag(mask, etype, detail):
        for _, row in df[mask].iterrows():
            errors.append({
                'run_at':       _now(),
                'table_name':   'transactions',
                'auto_id':      str(row.get('AutoID', '')),
                'date':         str(row.get('Date', '')),
                'item_code':    str(row.get('Item Code', '')),
                'account':      str(row.get('Account', '')),
                'error_type':   etype,
                'error_detail': detail,
            })

    dates = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')

    # ── Structural / data-quality checks only ─────────────────────────────
    # Only flag truly zero value — negative values are legitimate CRN (credit note) rows
    _flag(df['Value'] == 0,
          'ZERO_VALUE',       'Transaction Value is zero')

    _flag((df['QTY PCS'] <= 0) & (df['Value'] > 0),
          'ZERO_QTY',         'QTY PCS is zero but Value > 0')

    if 'Quantity' in df.columns:
        _flag((df['Quantity'] <= 0) & (df['Value'] > 0),
              'ZERO_QUANTITY',  'Quantity is zero or blank but Value > 0 — source data missing Quantity')

    _flag((df['Value'] > 0) & (df['QTY PCS'] > 0) & (df['Avg_Price'] <= 0),
          'ZERO_AVG_PRICE',   'Avg_Price is 0 despite non-zero Value and QTY (div error)')

    _flag(dates > today,
          'FUTURE_DATE',      f'Transaction date is in the future (after {today.date()})')

    _flag(dates.isna(),
          'INVALID_DATE',     'Date could not be parsed (NaN / unrecognised format)')

    _flag(df['Customer Names'].isin(['Unknown', '', 'nan', 'NaN'])
          if 'Customer Names' in df.columns else pd.Series([False] * len(df)),
          'MISSING_CUSTOMER', 'Customer Names is blank or Unknown')

    _flag(df['Items'].isin(['Unknown', '', 'nan', 'NaN'])
          if 'Items' in df.columns else pd.Series([False] * len(df)),
          'MISSING_ITEM',     'Items (SKU) is blank or Unknown')

    # NaN in key numeric columns (indicates a failed parse or missing cell)
    for col, label in [('Value', 'Value'), ('QTY PCS', 'QTY PCS'),
                       ('Unit Price', 'Unit Price'), ('Weight', 'Weight')]:
        if col in df.columns:
            _flag(pd.to_numeric(df[col], errors='coerce').isna(),
                  'NAN_VALUE',
                  f'{label} column contains NaN / non-numeric cell')

    # NOTE: PRICE_DEVIATION deliberately omitted — pricing differences are
    # a business decision and should not appear in the data-quality log.

    err_df = pd.DataFrame(errors)
    if not err_df.empty:
        # Deduplicate: same auto_id + error_type from previous runs already logged
        with get_connection() as conn:
            prev = pd.read_sql(
                "SELECT auto_id, error_type FROM validation_errors", conn
            )
            prev_keys = set(zip(prev['auto_id'].astype(str),
                                prev['error_type'].astype(str)))
            new_errors = err_df[
                ~err_df.apply(
                    lambda r: (str(r['auto_id']), str(r['error_type'])) in prev_keys,
                    axis=1
                )
            ]
            if not new_errors.empty:
                new_errors.to_sql('validation_errors', conn,
                                  if_exists='append', index=False)

        err_df.to_csv(VALIDATION_REPORT, index=False)
        log.warning(f"  {len(err_df):,} data-quality issues found "
                    f"({len(new_errors):,} new) -> {VALIDATION_REPORT.name}")
    else:
        log.info("  Validation passed — no data-quality issues found.")

    return err_df


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 2C — BUILD FINAL CSVs
# ══════════════════════════════════════════════════════════════════════════════

def _build_transactions_csv():
    log.info("-- PASS 2: Building transactions.csv --")
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'transactions' not in existing:
            log.error("  transactions table missing -- run ingest first.")
            return

        df = pd.read_sql("SELECT * FROM transactions", conn)

        # Join price_list on Item Code <-> Item/Account
        if 'price_list' in existing and 'Item Code' in df.columns:
            try:
                df_price = pd.read_sql("SELECT * FROM price_list", conn)
                df_price = _strip_junk_cols(df_price)
                df_price.columns = df_price.columns.str.strip()
                if 'Item/Account' in df_price.columns:
                    keep = ['Item/Account']
                    rename = {'Item/Account': 'Item Code'}
                    if '% Increase' in df_price.columns:
                        keep.append('% Increase')
                        rename['% Increase'] = 'Ref_Pct_Increase'
                    df_price = (df_price[keep]
                                .rename(columns=rename)
                                .drop_duplicates('Item Code'))
                    df = df.merge(df_price, on='Item Code', how='left')
                    matched = df['Ref_Pct_Increase'].notna().sum() \
                              if 'Ref_Pct_Increase' in df.columns else 0
                    log.info(f"  price_list join: {matched:,} / {len(df):,} rows matched.")
            except Exception as e:
                log.warning(f"  price_list join failed (non-fatal): {e}")

    # ── Item List backfill: fix Name / Items 2 / Items for Sage records ─────────
    # Rows ingested with an older pipeline version may have:
    #   • Name = NaN  (Description lookup was missing from old enrichment code)
    #   • Items 2 = broad Sub-Category ("Basins") instead of Sub-Sub-Category ("Basins - 304")
    #   • Items = NaN or same as Items 2 (3*Sub Category was never mapped)
    # Re-apply the Item List lookup here so the CSV always contains the best
    # available hierarchy, regardless of which pipeline version ingested the row.
    excel_path = _find_excel()
    if excel_path.exists() and 'Item Code' in df.columns:
        try:
            _xl_ref = pd.ExcelFile(excel_path)
            if 'Item List' in _xl_ref.sheet_names:
                df_il = _xl_ref.parse('Item List')
                df_il = _strip_junk_cols(df_il)
                df_il.columns = df_il.columns.str.strip()
                if 'Simple Code' in df_il.columns:
                    df_il = df_il.rename(columns={'Simple Code': 'Item Code'})
                    df_il['Item Code'] = df_il['Item Code'].astype(str).str.strip()

                    il_keep   = ['Item Code']
                    il_rename = {}
                    # Items 2: Sub-Sub-Category preferred over Sub-Category
                    if 'Sub-Sub-Category' in df_il.columns:
                        il_keep.append('Sub-Sub-Category')
                        il_rename['Sub-Sub-Category'] = '_il_Items2'
                    elif 'Sub-Category' in df_il.columns:
                        il_keep.append('Sub-Category')
                        il_rename['Sub-Category'] = '_il_Items2'
                    # Items: 3*Sub Category gives the mid-level variant
                    if '3*Sub Category' in df_il.columns:
                        il_keep.append('3*Sub Category')
                        il_rename['3*Sub Category'] = '_il_Items'
                    # Name: Description is the individual SKU name
                    if 'Description' in df_il.columns:
                        il_keep.append('Description')
                        il_rename['Description'] = '_il_Name'

                    df_il = (df_il[il_keep]
                             .rename(columns=il_rename)
                             .drop_duplicates('Item Code'))
                    df = df.merge(df_il, on='Item Code', how='left')

                    # Sage records have blank Location (master Excel always has it).
                    # For Sage rows, overwrite Items 2 / Items even if already set to
                    # a broad Sub-Category value ("Chair") from the old enrichment code.
                    # Master Excel rows (Location not blank) are never touched here.
                    _sage_mask = (
                        df['Location'].isna() |
                        df['Location'].astype(str).str.strip().isin(['', 'nan'])
                    ) if 'Location' in df.columns else pd.Series(False, index=df.index)

                    # Patch Name where blank (applies to all rows)
                    if '_il_Name' in df.columns:
                        blank_name = df['Name'].isna() | df['Name'].astype(str).str.strip().isin(['', 'nan'])
                        if blank_name.any():
                            df.loc[blank_name, 'Name'] = df.loc[blank_name, '_il_Name']
                            log.info(f"  Item List Name patch: filled {int(blank_name.sum()):,} blank Name values.")
                        df = df.drop(columns=['_il_Name'], errors='ignore')

                    # Patch Items 2: fill blanks everywhere, ALSO overwrite Sage rows
                    if '_il_Items2' in df.columns:
                        blank_i2 = df['Items 2'].isna() | df['Items 2'].astype(str).str.strip().isin(['', 'nan'])
                        sage_i2_update = _sage_mask & df['_il_Items2'].notna()
                        update_mask = blank_i2 | sage_i2_update
                        if update_mask.any():
                            df.loc[update_mask, 'Items 2'] = df.loc[update_mask, '_il_Items2']
                            log.info(f"  Item List Items 2 patch: updated {int(update_mask.sum()):,} rows "
                                     f"({int(blank_i2.sum())} blank + {int(sage_i2_update.sum())} Sage rows).")
                        df = df.drop(columns=['_il_Items2'], errors='ignore')

                    # Patch Items: fill blanks everywhere, ALSO overwrite Sage rows
                    if '_il_Items' in df.columns:
                        blank_items = df['Items'].isna() | df['Items'].astype(str).str.strip().isin(['', 'nan'])
                        sage_items_update = _sage_mask & df['_il_Items'].notna()
                        update_mask2 = blank_items | sage_items_update
                        if update_mask2.any():
                            df.loc[update_mask2, 'Items'] = df.loc[update_mask2, '_il_Items']
                            log.info(f"  Item List Items patch: updated {int(update_mask2.sum()):,} rows "
                                     f"({int(blank_items.sum())} blank + {int(sage_items_update.sum())} Sage rows).")
                        df = df.drop(columns=['_il_Items'], errors='ignore')
        except Exception as e:
            log.warning(f"  Item List backfill failed (non-fatal): {e}")

    # ── Fill dashboard-required columns for Sage-ingested records ─────────────
    # Sage records lack B2B, Items, Name, Sales Rep natively; master Excel
    # records have them from Sheet 2 formulas. Fill gaps from available data.

    # B2B: old Sage records stored 'B2B_Ref' (now renamed to 'B2B' in enrichment).
    # Promote B2B_Ref → B2B for any records ingested before this fix.
    if 'B2B_Ref' in df.columns:
        if 'B2B' not in df.columns:
            df.rename(columns={'B2B_Ref': 'B2B'}, inplace=True)
        else:
            blank_b2b = df['B2B'].isna() | df['B2B'].astype(str).str.strip().isin(['', 'nan'])
            df.loc[blank_b2b, 'B2B'] = df.loc[blank_b2b, 'B2B_Ref']

    # Items (Sub level): fall back to Items 2 (Grp) where Items is absent/blank.
    if 'Items 2' in df.columns:
        if 'Items' not in df.columns:
            df['Items'] = df['Items 2']
        else:
            blank_items = df['Items'].isna() | df['Items'].astype(str).str.strip().isin(['', 'nan'])
            df.loc[blank_items, 'Items'] = df.loc[blank_items, 'Items 2']

    # Name (Item level): fall back to Description where Name is absent/blank.
    _desc_src = next((c for c in ['Description', 'Product Name'] if c in df.columns), None)
    if _desc_src:
        if 'Name' not in df.columns:
            df['Name'] = df[_desc_src]
        else:
            blank_name = df['Name'].isna() | df['Name'].astype(str).str.strip().isin(['', 'nan'])
            df.loc[blank_name, 'Name'] = df.loc[blank_name, _desc_src]

    # Sales Rep: fall back to Rep Name where Sales Rep is absent/blank.
    if 'Rep Name' in df.columns:
        if 'Sales Rep' not in df.columns:
            df['Sales Rep'] = df['Rep Name']
        else:
            blank_rep = df['Sales Rep'].isna() | df['Sales Rep'].astype(str).str.strip().isin(['', 'nan'])
            df.loc[blank_rep, 'Sales Rep'] = df.loc[blank_rep, 'Rep Name']

    # Drop internal, merge-artefact, and junk columns before exporting
    drop_always = [
        'row_hash', 'ingested_at',
        'Pack Size Match_x', 'Pack Size Match_y', 'B2B_Ref',
        'Counter.1', 'nan.1', 'nan.2',          # Excel ghost duplicates
    ]
    df = df.drop(columns=[c for c in drop_always if c in df.columns])
    # Generic: drop any remaining _x/_y suffix columns
    df = df.drop(columns=[c for c in df.columns
                          if (c.endswith('_x') or c.endswith('_y'))
                          and c[:-2] in df.columns])
    # Generic: drop any remaining pandas .N duplicate suffixes
    kept = set(df.columns)
    drop_dupes = [c for c in df.columns
                  if re.match(r'^(.+)\.(\d+)$', c)
                  and re.match(r'^(.+)\.(\d+)$', c).group(1) in kept]
    if drop_dupes:
        df = df.drop(columns=drop_dupes)

    # ── Cash Sales enrichment ────────────────────────────────────────────────
    # Mark every Z099* row as 'Cash'; override Rep Name + Customer Names where
    # the cash_sales table has a line-level match (inv_no + item_code).
    # Unmatched Z099 rows still get Sale_Type='Cash' but keep the generic rep.
    if 'Account' in df.columns:
        cash_mask = df['Account'].astype(str).str.upper().str.startswith(CASH_ACCOUNT_PREFIX)
        df['Sale_Type'] = np.where(cash_mask, 'Cash', 'Credit')
        try:
            with get_connection() as _cs_conn:
                _tbls = {r[0] for r in _cs_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if 'cash_sales' in _tbls and 'Group' in df.columns and 'Item Code' in df.columns:
                    df_cs = pd.read_sql(
                        "SELECT inv_no, item_code, rep_name, customer_name FROM cash_sales",
                        _cs_conn
                    )
                    df_cs['inv_no']    = df_cs['inv_no'].astype(str).str.strip()
                    df_cs['item_code'] = df_cs['item_code'].astype(str).str.strip()
                    df['_cs_inv']  = df['Group'].astype(str).str.strip()
                    df['_cs_item'] = df['Item Code'].astype(str).str.strip()
                    df = df.merge(
                        df_cs.rename(columns={
                            'inv_no':       '_cs_inv',
                            'item_code':    '_cs_item',
                            'rep_name':     '_cs_rep',
                            'customer_name':'_cs_cust',
                        }),
                        on=['_cs_inv', '_cs_item'], how='left'
                    )
                    matched = cash_mask & df['_cs_rep'].notna()
                    if matched.any():
                        df.loc[matched, 'Rep Name']       = df.loc[matched, '_cs_rep']
                        df.loc[matched, 'Sales Rep']      = df.loc[matched, '_cs_rep']
                        df.loc[matched, 'Customer Names'] = df.loc[matched, '_cs_cust']
                        log.info(f"  Cash Sales: {int(matched.sum()):,} rows rep/customer overridden.")
                    unmatched_n = int((cash_mask & df['_cs_rep'].isna()).sum())
                    if unmatched_n:
                        log.info(f"  Cash Sales: {unmatched_n:,} Z099 rows unmatched "
                                 f"(shown as Cash with account-level rep).")
                    df = df.drop(columns=['_cs_inv', '_cs_item', '_cs_rep', '_cs_cust'], errors='ignore')
        except Exception as _e:
            log.warning(f"  Cash Sales enrichment failed (non-fatal): {_e}")
    else:
        df['Sale_Type'] = 'Credit'

    # Ensure numeric safety before formula engine
    for col in ['Value', 'QTY PCS', 'Quantity', 'Unit Price', 'Weight']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Recompute QTY PCS = Quantity x Pack Size for ALL rows on every rebuild, so
    # corrected pack sizes flow through (not just rows where QTY PCS was 0).
    # Quantity carries the sign (negative for CRN); Pack Size Match is a positive
    # magnitude, so the sign is preserved. Missing/invalid pack size -> 1.
    if 'Quantity' in df.columns:
        pack = (
            pd.to_numeric(df.get('Pack Size Match', pd.Series(1, index=df.index)),
                          errors='coerce')
            .fillna(1).clip(lower=1)
        )
        df['QTY PCS'] = (
            pd.to_numeric(df['Quantity'], errors='coerce').fillna(0) * pack
        ).round(4)
        n_packed = int((pack > 1).sum())
        log.info(f"  QTY PCS recomputed: Quantity x Pack Size ({n_packed:,} rows had pack size > 1).")

    # ── Per-transaction rep overrides (Admin Panel → Transaction Editor) ──────
    # Manual reassignment of individual transactions to the rep who actually made
    # the sale (customer-derived attribution credits the wrong rep otherwise).
    # Keyed by AutoID and applied LAST so it wins over customer/cash-sales reps.
    if 'AutoID' in df.columns:
        try:
            with get_connection() as _to_conn:
                _tbls = {r[0] for r in _to_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if 'transaction_overrides' in _tbls:
                    to_df = pd.read_sql(
                        "SELECT autoid, rep_name FROM transaction_overrides "
                        "WHERE rep_name IS NOT NULL AND TRIM(rep_name) != ''",
                        _to_conn
                    )
                    if not to_df.empty:
                        rep_map = dict(zip(
                            to_df['autoid'].astype(str).str.strip(),
                            to_df['rep_name'].astype(str).str.strip()
                        ))
                        key = df['AutoID'].astype(str).str.strip()
                        mask = key.isin(rep_map)
                        if mask.any():
                            new_rep = key[mask].map(rep_map)
                            df.loc[mask, 'Rep Name'] = new_rep
                            if 'Sales Rep' in df.columns:
                                df.loc[mask, 'Sales Rep'] = new_rep
                            log.info(f"  Transaction overrides: {int(mask.sum()):,} "
                                     f"row(s) rep reassigned.")
        except Exception as _e:
            log.warning(f"  Transaction overrides failed (non-fatal): {_e}")

    df = _apply_formulas(df)
    _run_validation(df)

    # Persist the fully-computed frame to the DB as the SERVED source of truth.
    # The dashboard reads 'transactions_final'; the CSV below is a backup / export
    # artifact. Both come from this one frame, so they can never drift apart.
    try:
        with get_connection() as conn:
            df.to_sql('transactions_final', conn, if_exists='replace', index=False)
        log.info(f"  transactions_final table: {len(df):,} rows -> {DB_PATH.name}")
    except Exception as e:
        log.error(f"  Failed writing transactions_final table (non-fatal): {e}")

    df.to_csv(CSV_OUT['transactions'], index=False)
    log.info(f"  transactions.csv (backup): {len(df):,} rows -> {CSV_OUT['transactions']}")


def _build_inventory_csv():
    log.info("-- PASS 2: Building inventory.csv --")
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'inventory' not in existing:
            log.error("  inventory table missing -- run ingest first.")
            return
        df = pd.read_sql("SELECT * FROM inventory", conn)

    df = df.drop(
        columns=[c for c in ['row_hash', 'ingested_at'] if c in df.columns]
    )

    # Served source of truth (dashboard reads 'inventory_final'); CSV is a backup.
    try:
        with get_connection() as conn:
            df.to_sql('inventory_final', conn, if_exists='replace', index=False)
        log.info(f"  inventory_final table: {len(df):,} rows -> {DB_PATH.name}")
    except Exception as e:
        log.error(f"  Failed writing inventory_final table (non-fatal): {e}")

    df.to_csv(CSV_OUT['inventory'], index=False)
    log.info(f"  inventory.csv (backup): {len(df):,} rows -> {CSV_OUT['inventory']}")


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 1C — INGEST INVOICES  (Price Checker 3.xlsx → Final sheet)
# ══════════════════════════════════════════════════════════════════════════════

# Exact column names expected on the Final sheet — used for validation only.
INVOICE_FINAL_COLS = [
    'Order No.', 'Customer Name', 'Description', 'Date', 'Order QTY',
    'Unit Price Excl', 'Master Price (Excl)', 'Order Tot', 'Area',
    'Item/Account', 'Total Qty', 'Difference', 'Order Total', '% Diff.',
    'Old Price', 'Customer:', 'Confirm Tot', '% Increase', 'State',
    'Concat', '% Diff Old',
]


def _ingest_invoices(force: bool = False):
    """
    Ingest Price Checker 3.xlsx → 'Final' sheet → SQLite 'invoices' table.
    Uses MD5 file-hash to avoid redundant reloads (same logic as price_list).
    """
    log.info("-- PASS 1C: Ingesting invoices (Price Checker 3 / Final sheet) --")

    invoice_path = _find_invoice_excel()
    if invoice_path is None:
        log.warning("  Invoice source file not found — skipping invoice ingest.")
        return

    new_hash = _hash_file(invoice_path)
    with get_connection() as conn:
        stored = conn.execute(
            "SELECT file_hash FROM ref_checksums WHERE table_name='invoices'"
        ).fetchone()
        old_hash = stored[0] if stored else None

    if not force and old_hash == new_hash:
        log.info(f"  invoices: no change detected (hash match) — skipping reload.")
        return

    log.info(f"  Reading '{invoice_path.name}' -> Final sheet ...")
    try:
        df = pd.read_excel(invoice_path, sheet_name='Final')
    except Exception as e:
        log.error(f"  Failed to read Final sheet: {e}")
        return

    df = _strip_junk_cols(df)
    df.columns = df.columns.str.strip()
    log.info(f"  Final sheet: {len(df):,} rows, {len(df.columns)} columns: "
             f"{list(df.columns)}")

    # Validate that expected columns are present (warn but don't abort)
    missing_cols = [c for c in INVOICE_FINAL_COLS if c not in df.columns]
    if missing_cols:
        log.warning(f"  Final sheet is missing expected columns: {missing_cols}")

    # ── DATE NORMALISATION ──
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
        n_bad = df['Date'].isna().sum()
        if n_bad:
            log.warning(f"  {n_bad:,} invoice rows have unparseable dates (kept as NaT).")
        df['Date'] = df['Date'].dt.strftime('%d/%m/%Y')

    # ── NUMERIC COLUMNS ──
    for col in ['Order QTY', 'Unit Price Excl', 'Master Price (Excl)',
                'Order Tot', 'Total Qty', 'Difference', 'Order Total',
                '% Diff.', 'Old Price', 'Confirm Tot', '% Increase', '% Diff Old']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # ── ENRICH WITH SALES ORDER NUMBER (Ord No:) ──
    so_path = _find_sales_orders_excel()
    if so_path:
        ord_map = _parse_sales_orders_xlsx(so_path)
        if ord_map:
            df['Sales_Order_No'] = df['Order No.'].map(ord_map).fillna('')
            matched = (df['Sales_Order_No'] != '').sum()
            log.info(f"  Sales_Order_No: {matched:,}/{len(df):,} rows enriched "
                     f"from '{so_path.name}'.")
        else:
            df['Sales_Order_No'] = ''
            log.warning("  Sales Orders file found but yielded no mappings — "
                        "Sales_Order_No set to blank.")
    else:
        df['Sales_Order_No'] = ''
        log.info(f"  Sales Orders New In.xlsx not found in Raws/ — "
                 f"Sales_Order_No set to blank. Copy the file there to enable enrichment.")

    # ── ROW HASH + TIMESTAMP ──
    df['row_hash']    = df.apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    rows_before = 0
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'invoices' in existing:
            rows_before = conn.execute(
                "SELECT COUNT(*) FROM invoices"
            ).fetchone()[0]
        df.to_sql('invoices', conn, if_exists='replace', index=False)

        # Update ref_checksums so we don't reload unless file changes
        conn.execute("""
            INSERT INTO ref_checksums (table_name, file_hash, last_updated, source_path)
            VALUES ('invoices', ?, ?, ?)
            ON CONFLICT(table_name) DO UPDATE SET
                file_hash=excluded.file_hash,
                last_updated=excluded.last_updated,
                source_path=excluded.source_path
        """, (new_hash, _now(), str(invoice_path)))

        conn.execute("""
            INSERT INTO ref_log
                (table_name, changed_at, old_hash, new_hash,
                 rows_before, rows_after, notes)
            VALUES ('invoices',?,?,?,?,?,?)
        """, (_now(), old_hash, new_hash, rows_before, len(df),
              'auto-detected change' if not force else 'force reload'))

    _log_ingestion(str(invoice_path), 'invoices',
                   total=len(df), inserted=len(df))
    log.info(f"  invoices: {len(df):,} rows loaded (was {rows_before:,}).")


# ══════════════════════════════════════════════════════════════════════════════
#  PASS 2D — BUILD invoices_final.csv
# ══════════════════════════════════════════════════════════════════════════════

def _build_invoices_csv():
    log.info("-- PASS 2: Building invoices_final.csv --")
    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'invoices' not in existing:
            log.error("  invoices table missing -- run ingest first (needs Price Checker 3.xlsx).")
            return
        df = pd.read_sql("SELECT * FROM invoices", conn)

    # Drop internal columns before export
    df = df.drop(
        columns=[c for c in ['row_hash', 'ingested_at'] if c in df.columns]
    )

    # ── ENRICH WITH SALES ORDER NUMBER ──
    # Skip enrichment if Sales_Order_No is already populated (Inventory Transactions source
    # bakes SO numbers in directly).  Only apply the Sales Orders New In.xlsx join for
    # the old Price Checker path where the column is absent or all-blank.
    already_populated = (
        'Sales_Order_No' in df.columns
        and df['Sales_Order_No'].fillna('').astype(str).str.strip().ne('').any()
    )
    so_path = _find_sales_orders_excel() if not already_populated else None
    if so_path:
        ord_map = _parse_sales_orders_xlsx(so_path)
        if ord_map:
            df['Sales_Order_No'] = df['Order No.'].map(ord_map).fillna('')
            matched = (df['Sales_Order_No'] != '').sum()
            log.info(f"  Sales_Order_No: {matched:,}/{len(df):,} rows enriched "
                     f"from '{so_path.name}'.")
        else:
            if 'Sales_Order_No' not in df.columns:
                df['Sales_Order_No'] = ''
    else:
        if 'Sales_Order_No' not in df.columns:
            df['Sales_Order_No'] = ''
        if not already_populated:
            # Only warn when the column is genuinely missing from the source data
            log.info("  Sales_Order_No not in source data — column will be blank.")

    out_path = CSV_OUT['invoices_final']
    df.to_csv(out_path, index=False)
    log.info(f"  invoices_final.csv: {len(df):,} rows -> {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
#  CSV → DB IMPORT  (restore from backup transactions.csv)
# ══════════════════════════════════════════════════════════════════════════════

# Columns added by _apply_formulas() or reference joins during the BUILD step.
# These are dropped before importing so the BUILD step recomputes them cleanly.
_COMPUTED_COLS = {
    'Avg_Price', 'Price_Deviation_Pct', 'Price_Flag',
    'Weight_Per_Unit', 'Rev_Per_Km', 'Ref_Pct_Increase',
    'row_hash', 'ingested_at',
}


def _import_transactions_from_csv(csv_path: Path = None, force: bool = False):
    """
    Import a transactions CSV directly into the DB transactions table.

    Use this to restore from a backup CSV when the source Excel is unavailable
    or incomplete.  After importing, run --build-only to rebuild output CSVs.

    Args:
        csv_path: Path to CSV file.  Defaults to Raws/transactions.csv.
        force:    If True, re-hash and upsert every row (ignores existing hashes).
    """
    path = csv_path or CSV_OUT['transactions']
    log.info(f"-- CSV IMPORT: {path.name} -> transactions table --")

    if not path.exists():
        log.error(f"  File not found: {path}")
        return

    df = pd.read_csv(path, low_memory=False)
    df.columns = df.columns.str.strip()
    log.info(f"  Read {len(df):,} rows, {len(df.columns)} columns")

    # Drop computed / formula columns — they are rebuilt by _build_transactions_csv
    drop = [c for c in _COMPUTED_COLS if c in df.columns]
    if drop:
        df = df.drop(columns=drop)
        log.info(f"  Dropped computed columns: {drop}")

    # Drop junk column names
    df = _strip_junk_cols(df)

    # ── Date normalisation ──────────────────────────────────────────────────
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
        n_bad = df['Date'].isna().sum()
        if n_bad:
            log.warning(f"  {n_bad:,} rows dropped: unparseable date.")
        df = df.dropna(subset=['Date'])
        df['Date'] = df['Date'].dt.strftime('%d/%m/%Y')
        # Show year breakdown
        yr = pd.to_datetime(df['Date'], dayfirst=True).dt.year.value_counts().sort_index()
        for y, c in yr.items():
            log.info(f"    {y}: {c:,} rows")

    # ── Numeric cleaning ────────────────────────────────────────────────────
    for col in ['Value', 'QTY PCS', 'Quantity', 'Weight', 'Unit Price',
                'Distance from Nairobi', 'Lat', 'Lon']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # ── String cleaning ─────────────────────────────────────────────────────
    for col in ['Items', 'Name', 'Items 2', 'Location', 'Sales Rep',
                'Customer Names', 'B2B', 'Item Category', 'Item Code']:
        if col in df.columns:
            df[col] = (df[col].astype(str).str.strip()
                       .replace({'nan': 'Unknown', 'NaN': 'Unknown', '': 'Unknown'}))

    # ── AutoID (primary key) ────────────────────────────────────────────────
    if 'AutoID' not in df.columns:
        df['AutoID'] = ''
    df['AutoID'] = df['AutoID'].astype(str).str.strip()

    # Rows with no real AutoID get a stable hash-based synthetic ID so every
    # transaction row gets a unique PK and re-importing is idempotent.
    _NO_ID = {'nan', '', 'None', 'NaN', 'none'}
    nan_mask = df['AutoID'].isin(_NO_ID)
    if nan_mask.any():
        key_cols = [c for c in ['Date', 'Account', 'Item Code',
                                 'Customer Names', 'QTY PCS', 'Value']
                    if c in df.columns]
        df.loc[nan_mask, 'AutoID'] = (
            df[nan_mask][key_cols]
            .apply(lambda r: 'IMP_' + _hash_row(r)[:20], axis=1)
        )
        log.info(f"  Generated hash-based AutoIDs for {nan_mask.sum():,} "
                 f"rows that had no AutoID.")

    dupes = df['AutoID'].duplicated().sum()
    if dupes:
        log.warning(f"  {dupes:,} duplicate AutoID values after ID generation — "
                    f"keeping last occurrence.")
        df = df.drop_duplicates(subset=['AutoID'], keep='last')

    # ── Skip years already substantially covered by a proper XLSX ingest ──
    # This prevents duplicate rows when importing a backup CSV that overlaps
    # with data already loaded from the source Excel file.
    if not force:
        with get_connection() as conn:
            tbls = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'transactions' in tbls:
                yr_df = pd.read_sql(
                    "SELECT SUBSTR(Date,7,4) AS yr, COUNT(*) AS cnt "
                    "FROM transactions GROUP BY yr",
                    conn
                )
                # Consider a year "covered" if it has more than 500 rows already
                covered = set(
                    yr_df[yr_df['cnt'] > 500]['yr']
                    .dropna().astype(int).tolist()
                )
                if covered:
                    yr_series = pd.to_datetime(
                        df['Date'], dayfirst=True, errors='coerce').dt.year
                    before = len(df)
                    df = df[~yr_series.isin(covered)]
                    skipped = before - len(df)
                    if skipped:
                        log.info(
                            f"  Skipped {skipped:,} rows from years already in DB "
                            f"{sorted(covered)} — use --force-reload to override."
                        )
                    if df.empty:
                        log.info("  Nothing new to import after year filter. Done.")
                        _build_products_ref()
                        _build_customers_ref()
                        return

    # ── Row hash + ingested timestamp ───────────────────────────────────────
    hash_cols = [c for c in df.columns if c != 'AutoID']
    df['row_hash']    = df[hash_cols].apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    log.info(f"  Upserting {len(df):,} rows ...")
    _upsert_table(df, 'transactions', pk_col='AutoID', force=force)

    # Rebuild derived reference tables
    _build_products_ref()
    _build_customers_ref()
    log.info("  Import complete.  Run --build-only to rebuild output CSVs.")


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _log_ingestion(source_file, table, total, inserted=0, updated=0,
                   skipped=0, status='ok', notes=''):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO ingestion_log
                (run_at, source_file, table_name, rows_total,
                 rows_inserted, rows_updated, rows_skipped, status, notes)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (_now(), source_file, table, total,
              inserted, updated, skipped, status, notes))


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY REPORT
# ══════════════════════════════════════════════════════════════════════════════

def _print_summary():
    log.info("")
    log.info("=" * 60)
    log.info("  RUN SUMMARY")
    log.info("=" * 60)
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT table_name, rows_total, rows_inserted,
                   rows_updated, rows_skipped
            FROM ingestion_log
            WHERE run_at >= datetime('now', '-2 minutes')
            ORDER BY id
        """).fetchall()
        for r in rows:
            log.info(f"  {r[0]:<28} total={r[1]:>7,}  "
                     f"+new={r[2]:>6,}  ~upd={r[3]:>5,}  =skip={r[4]:>6,}")

        val_count = conn.execute("""
            SELECT COUNT(*) FROM validation_errors
            WHERE run_at >= datetime('now', '-2 minutes')
        """).fetchone()[0]

    log.info(f"  {'Validation issues':<28} {val_count:>7,}")
    log.info("=" * 60)
    log.info(f"  Database : {DB_PATH}")
    log.info(f"  CSVs     : {RAWS_DIR}")
    log.info("=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
#  SAGE DIRECT-INGEST  (Transactions Listing + QIH raw exports)
# ══════════════════════════════════════════════════════════════════════════════

def _validate_enrichment(df: pd.DataFrame) -> dict:
    """
    Hard failures only — block ingest and require user action.
    Returns {ref_sheet: [list of unmatched raw values]}.
    Empty dict = safe to proceed.

    Hard (blocks ingest):
      - Item List: Items 2 still NaN/Unknown → item code missing from Item List
        (broken product hierarchy; data cannot be grouped correctly)

    Soft (warning only, see _warn_enrichment_soft):
      - Customers: Rep Name blank → customer missing from Customers sheet
        (Rep Name/Area will be empty for those rows, non-critical)

    Not checked (expected partial):
      - PackSize: blank is valid for items without pack-size data
    """
    errors: dict[str, list] = {}

    if 'Items 2' in df.columns:
        bad = df[df['Items 2'].isna() | df['Items 2'].astype(str).isin(['nan', 'Unknown', ''])]
        if not bad.empty and 'Item Code' in df.columns:
            errors['Item List'] = sorted(bad['Item Code'].astype(str).unique().tolist())

    return errors


def _warn_enrichment_soft(df: pd.DataFrame):
    """
    Warn about soft enrichment gaps that don't block ingest.
    Customers missing from the reference sheet will have blank Rep Name and
    Customer Area — the Sales Rep chart and Area filter won't work for them.
    """
    if 'Rep Name' in df.columns:
        bad = df[df['Rep Name'].isna() | df['Rep Name'].astype(str).isin(['nan', ''])]
        if not bad.empty and 'Customer Names' in df.columns:
            missing = sorted(bad['Customer Names'].astype(str).unique().tolist())
            log.warning(
                f"  [Customers] {len(missing)} customer(s) not found in Customers sheet — "
                f"Rep Name / Customer Area will be blank for those rows:"
            )
            for v in missing[:20]:
                log.warning(f"    ⚠  {v}")
            if len(missing) > 20:
                log.warning(f"    … and {len(missing) - 20} more.")
            log.warning(
                "  To fix: add the customer(s) above to the Customers sheet in the master Excel."
            )


def _report_enrichment_errors(errors: dict):
    """Log a grouped enrichment-failure report grouped by reference sheet."""
    log.error("  ══ ENRICHMENT VALIDATION FAILURES ══")
    for ref_sheet, missing in errors.items():
        log.error(f"  [{ref_sheet}] — {len(missing)} unmatched value(s):")
        for v in missing[:30]:          # cap display at 30 items per sheet
            log.error(f"    ✗  {v}")
        if len(missing) > 30:
            log.error(f"    … and {len(missing) - 30} more.")
    log.error("  Update the reference sheets in the master Excel and retry.")


def _append_to_sales_orders(df_raw: pd.DataFrame):
    """
    Append raw Transactions Listing rows (including Rep/SO column) to
    Sales Orders New In.xlsx.  Only rows whose AutoID is not already
    present in the file are written.  The file is kept in a simple
    DataFrame-to-Excel format so _parse_inventory_transactions_xlsx()
    can read it (it scans for the 'Item Code' header row).
    """
    so_path = SALES_ORDERS_PREFERRED

    try:
        if so_path.exists():
            existing = _parse_inventory_transactions_xlsx(so_path)
            existing_ids = set(existing['AutoID'].astype(str).str.strip()) \
                if 'AutoID' in existing.columns else set()
        else:
            existing = pd.DataFrame()
            existing_ids = set()

        if 'AutoID' in df_raw.columns:
            new_rows = df_raw[~df_raw['AutoID'].astype(str).str.strip().isin(existing_ids)].copy()
        else:
            new_rows = df_raw.copy()

        if new_rows.empty:
            log.info("  Sales Orders New In.xlsx: no new rows to append.")
            return

        # Combine and write as a plain Excel file
        if not existing.empty:
            # existing has renamed columns; remap to original column names
            rev_rename = {
                'Item_Code': 'Item Code', 'SO_No': 'Rep', 'Inv_No': 'Group',
                'Warehouse': 'WarehouseName',
            }
            existing_out = existing.rename(columns=rev_rename)
            # Use union of columns — do NOT reindex to df_raw.columns, which would
            # silently drop columns that exist in the file but not in the new Sage
            # export (e.g. 'Account' is absent from Transactions Listing but present
            # in Sales Orders New In.xlsx).
            combined = pd.concat(
                [existing_out, new_rows],
                ignore_index=True,
                sort=False
            )
        else:
            combined = new_rows

        combined.to_excel(so_path, index=False)
        log.info(f"  Sales Orders New In.xlsx: appended {len(new_rows):,} rows "
                 f"(total {len(combined):,}).")

    except Exception as e:
        log.warning(f"  Sales Orders append failed (non-fatal): {e}")


def _ingest_transactions_listing(filepath: Path, force: bool = False):
    """
    Parse a raw Sage Transactions Listing .xlsx and ingest to the
    transactions DB table.

    File structure expected:
      Rows 1-8:   title / date-range block  (skipped via header=8)
      Row 9:      column headers
      Row 10+:    data rows, with repeating page-header rows mid-file
                  ('Item Code' in the Item Code cell) and a Totals row
                  / watermark row at variable positions near the bottom.

    Workflow:
      1. Parse raw rows (all columns, including Rep/SO number).
      2. Append raw rows to Sales Orders New In.xlsx for invoice checking.
      3. Drop Rep column; map Account codes → Customer Names.
      4. Enrich from master Excel reference sheets.
      5. Run grouped validation gate — abort if any lookups fail.
      6. Upsert enriched rows to transactions table.
    """
    log.info(f"-- SAGE INGEST: Transactions Listing '{filepath.name}' --")

    try:
        df_scan = _read_excel_robust(filepath, header=None)
    except Exception as e:
        log.error(f"  Failed to read '{filepath.name}': {e}")
        return

    # Auto-detect the header row by scanning for 'Item Code' in any column.
    # This is robust to different Sage export formats (6, 7, 8, or 9 header rows).
    header_idx = None
    for i, row in df_scan.iterrows():
        if any(str(v).strip() == 'Item Code' for v in row.values if pd.notna(v)):
            header_idx = i
            break

    if header_idx is None:
        log.error(
            "  'Item Code' header row not found in the file. "
            "Ensure this is a standard Sage Transactions Listing export."
        )
        return

    log.info(f"  Header row auto-detected at row {header_idx + 1} (1-indexed).")

    try:
        df_raw = _read_excel_robust(filepath, header=header_idx)
    except Exception as e:
        log.error(f"  Failed to re-read '{filepath.name}' with detected header: {e}")
        return

    df_raw.columns = [str(c).strip() for c in df_raw.columns]
    df_raw = _strip_junk_cols(df_raw)
    log.info(f"  Raw read: {len(df_raw):,} rows, columns: {list(df_raw.columns)}")

    if 'Item Code' not in df_raw.columns:
        log.error("  'Item Code' column not found after header detection — unexpected file structure.")
        return

    # Drop page-header repetition rows
    df_raw = df_raw[~df_raw['Item Code'].astype(str).str.strip().isin(['Item Code', 'nan', ''])]

    # Drop watermark rows (Item Code contains 'Sage', 'Evolution', etc.)
    wm = df_raw['Item Code'].astype(str).str.strip().str.lower().str.contains(
        r'sage|evolution|watermark', na=False, regex=True
    )
    df_raw = df_raw[~wm]

    # Drop Totals rows (blank/nan Item Code with numeric Quantity)
    if 'Quantity' in df_raw.columns:
        is_totals = (
            df_raw['Item Code'].astype(str).str.strip().isin(['', 'nan'])
        ) & pd.to_numeric(df_raw['Quantity'], errors='coerce').notna()
        df_raw = df_raw[~is_totals]

    df_raw = df_raw.dropna(subset=['Item Code'])
    log.info(f"  After cleaning: {len(df_raw):,} rows")

    # Keep INV (invoices) and CRN (credit notes); drop header/total/other rows
    if 'Tran' in df_raw.columns:
        df_raw = df_raw[df_raw['Tran'].astype(str).str.strip().isin(['INV', 'CRN'])].copy()
        log.info(f"  After INV+CRN filter: {len(df_raw):,} rows")

    if df_raw.empty:
        log.warning("  0 rows after filtering — nothing to ingest.")
        return

    # Step 1: append raw rows (with Rep/SO number) to Sales Orders New In.xlsx
    _append_to_sales_orders(df_raw.copy())

    # Step 2: prepare enrichment path (strip Rep/SO column)
    df = df_raw.copy()
    if 'Rep' in df.columns:
        df = df.drop(columns=['Rep'])

    # Quantity from the Sage TL report lands in the 'Quantity' column as-is.
    # QTY PCS (= Quantity × Pack Size) is calculated after enrichment once
    # Pack Size Match has been joined in from the PackSize reference sheet.

    # Date normalisation
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
        n_bad = df['Date'].isna().sum()
        if n_bad:
            log.warning(f"  {n_bad:,} rows dropped: unparseable date.")
        df = df.dropna(subset=['Date'])
        df['Date'] = df['Date'].dt.strftime('%d/%m/%Y')

    # Numeric cleaning
    for col in ['Value', 'QTY PCS', 'Quantity', 'Weight', 'Unit Price']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # String cleaning
    for col in ['Item Code', 'Account']:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # Map Account codes → Customer Names
    if 'Account' in df.columns:
        acc_map = _get_account_customer_map()
        df['Customer Names'] = df['Account'].map(acc_map)
        unmatched_accs = df['Customer Names'].isna().sum()
        if unmatched_accs:
            log.warning(f"  {unmatched_accs:,} rows have no Customer Name for their Account code.")
        df['Customer Names'] = df['Customer Names'].fillna(df['Account'])

    # AutoID: use the native Sage AutoID column when available.
    # If absent, generate surrogate keys prefixed with "SAGE-" so they
    # never collide with master-Excel AutoIDs (which are plain integers).
    if 'AutoID' not in df.columns or df['AutoID'].isna().all():
        log.warning("  AutoID not found — generating SAGE- surrogate keys.")
        df['AutoID'] = [f'SAGE-{i}' for i in range(1, len(df) + 1)]
    df['AutoID'] = df['AutoID'].astype(str).str.strip()

    # Step 3: enrich from master Excel reference sheets
    excel_path = _find_excel()
    if excel_path.exists():
        log.info(f"  Enriching from '{excel_path.name}' ...")
        df = _enrich_from_excel(df, excel_path)
    else:
        log.warning(f"  Master Excel not found ({excel_path.name}) — skipping enrichment.")

    # Step 3b: QTY PCS = Quantity × Pack Size Match
    # Pack Size Match is now present (joined by _enrich_from_excel above).
    # Sage TL only supplies raw unit Quantity; QTY PCS must be derived here.
    if 'Quantity' in df.columns:
        pack = (
            pd.to_numeric(df.get('Pack Size Match', pd.Series(1, index=df.index)),
                          errors='coerce')
            .fillna(1).clip(lower=1)
        )
        df['QTY PCS'] = (
            pd.to_numeric(df['Quantity'], errors='coerce').fillna(0) * pack
        ).round(4)
        n_packed = int((pack > 1).sum())
        log.info(f"  QTY PCS derived: Quantity × Pack Size ({n_packed:,} rows had pack size > 1).")

    # Step 4: grouped validation gate
    errors = _validate_enrichment(df)
    if errors:
        _report_enrichment_errors(errors)
        log.error("  INGEST ABORTED: fix the reference sheets above and retry.")
        sys.exit(1)

    # Row hash + timestamp
    hash_cols = [c for c in df.columns if c != 'AutoID']
    df['row_hash'] = df[hash_cols].apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    # Only insert AutoIDs that don't already exist in the DB.
    # Master Excel records (from _ingest_transactions) carry richer hierarchy
    # data (Items, Items 2, Name, Sales Rep) from Sheet 2 formulas. Overwriting
    # them with coarser Sage TL + Item List enrichment degrades data quality and
    # can break the Sub/Item drill-down hierarchy for historical dates.
    with get_connection() as conn:
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'transactions' in existing_tables and not force:
            sage_autoids = df['AutoID'].astype(str).tolist()
            placeholders = ','.join('?' * len(sage_autoids))
            already_in_db = {
                str(r[0]) for r in conn.execute(
                    f"SELECT AutoID FROM transactions WHERE AutoID IN ({placeholders})",
                    sage_autoids
                ).fetchall()
            }
            n_overlap = len(already_in_db)
            if n_overlap:
                log.info(
                    f"  Skipping {n_overlap:,} records already in DB from master Excel "
                    f"(preserving richer hierarchy data)."
                )
            df = df[~df['AutoID'].isin(already_in_db)].copy()
            if df.empty:
                log.info(
                    "  All Sage TL records are already in the DB — no new dates to add. "
                    "Upload a newer Sage TL export to extend the data beyond the master Excel."
                )
                _build_products_ref()
                _build_customers_ref()
                return

    _upsert_table(df, 'transactions', pk_col='AutoID', force=force)
    _build_products_ref()
    _build_customers_ref()
    log.info(f"  Transactions Listing ingest complete: {len(df):,} rows.")


def _ingest_qih(filepath: Path, force: bool = False):
    """
    Parse a raw Sage QIH.xlsx export and refresh the inventory table.

    File structure:
      Row 1:  title
      Row 2:  blank
      Row 3:  column headers
      Row 4+: data rows, watermark row at variable bottom position.
    """
    log.info(f"-- SAGE INGEST: inventory '{filepath.name}' --")

    try:
        # Sage inventory exports vary in layout: the classic QIH export and the
        # newer 'Inventory Listing' Report export both put the header a few rows
        # down (after a title / blank rows), sometimes at different offsets and
        # with page-break noise. Detect the header row dynamically so either file
        # works when dropped into the inventory slot.
        raw = _read_excel_robust(filepath, header=None)
        hdr_idx = None
        for i in range(min(12, len(raw))):
            cells = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
            if 'item code' in cells and any('qty on hand' in c for c in cells):
                hdr_idx = i
                break
        if hdr_idx is None:
            hdr_idx = 2  # fall back to the classic QIH layout (header on row 3)
        df = raw.iloc[hdr_idx + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr_idx].tolist()]
    except Exception as e:
        log.error(f"  Failed to read '{filepath.name}': {e}")
        return

    df = _strip_junk_cols(df)
    df.columns = [str(c).strip() for c in df.columns]
    log.info(f"  Raw read: {len(df):,} rows (header on row {hdr_idx + 1})")

    # Drop watermark rows
    if len(df.columns) > 0:
        first_col = df.columns[0]
        wm = df[first_col].astype(str).str.strip().str.lower().str.contains(
            r'sage|evolution|watermark', na=False, regex=True
        )
        df = df[~wm]

    # Drop repeated per-page header rows and page/title banners (Sage reprints
    # the column header + 'Page x of y' / report title on every printed page).
    if 'Item Code' in df.columns:
        ic = df['Item Code'].astype(str).str.strip()
        noise = ic.isin(['', 'nan', 'Item Code']) | \
                ic.str.contains('Inventory Item Listing|Page ', case=False, na=False)
        df = df[~noise]

    df = df.dropna(how='all')

    # Numeric normalisation
    for col in ['Qty On Hand', 'Reorder Lvl', 'MaxLvl', 'QtyOnSO']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Keep standard inventory columns
    # Do NOT add an 'Items' display-name column here: data_loader.py uses the
    # first 'item'-named column (Item Code) as the join key for inventory tables.
    # Adding 'Items' = descriptions would break that join in V1.
    present = [c for c in INV_KEEP_COLS if c in df.columns]
    df = df[present].copy()

    df['row_hash'] = df.apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    with get_connection() as conn:
        df.to_sql('inventory', conn, if_exists='replace', index=False)
    _log_ingestion(str(filepath), 'inventory', total=len(df), inserted=len(df))
    log.info(f"  QIH ingest complete: {len(df):,} rows.")


def _ingest_cash_sales(filepath: Path, force: bool = False):
    """
    Parse Cash Sales.xlsx and upsert into the cash_sales table.

    The file has a repeating structure: a header row (col 0 = 'Item/Account')
    followed by data rows, then blank separators, then the header repeats.
    We collect all data rows and upsert by (inv_no, item_code).

    This is an incremental / merge-append process: upload yesterday's extract
    and only new invoice lines are added.
    """
    log.info(f"-- CASH SALES INGEST: '{filepath.name}' --")

    try:
        df_raw = _read_excel_robust(filepath, header=None)
    except Exception as e:
        log.error(f"  Failed to read '{filepath.name}': {e}")
        return

    # Auto-detect columns from the first header row (col 0 == 'Item/Account')
    header_cols = None
    data_rows = []
    for _, row in df_raw.iterrows():
        v0 = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ''
        if v0 == 'Item/Account':
            if header_cols is None:
                header_cols = [str(v).strip() if pd.notna(v) else f'_col{i}'
                               for i, v in enumerate(row.values)]
            continue  # skip all header repetitions
        if not v0 or v0 == 'nan':
            continue  # skip blank / separator rows
        data_rows.append(row.values)

    if not data_rows or header_cols is None:
        log.warning("  No data rows found — nothing to ingest.")
        return

    df = pd.DataFrame(data_rows, columns=header_cols)
    log.info(f"  Raw read: {len(df):,} rows")

    col_map = {
        'Item/Account':  'item_code',
        'Description':   'description',
        'Customer:':     'account',
        'Customer Name': 'customer_name',
        'Rep Name':      'rep_name',
        'Inv Date':      'inv_date',
        'Inv No:':       'inv_no',
        'Order Qty':     'order_qty',
        'Confirmed':     'confirmed_qty',
        'Confirm Tot':   'confirm_tot',
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df = df[[c for c in col_map.values() if c in df.columns]].copy()

    # Drop rows missing the join keys (handles NaN, None, blank, 'nan' string)
    for req in ('inv_no', 'item_code'):
        if req in df.columns:
            df = df[df[req].notna()]
            df = df[~df[req].astype(str).str.strip().isin(['', 'nan', 'None'])]

    if df.empty:
        log.warning("  0 usable rows after cleaning.")
        return

    # Type coercions
    df['item_code']    = df['item_code'].astype(str).str.strip()
    df['inv_no']       = df['inv_no'].astype(str).str.strip()
    for col in ('account', 'customer_name', 'rep_name', 'description'):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    if 'inv_date' in df.columns:
        df['inv_date'] = pd.to_datetime(df['inv_date'], errors='coerce').dt.strftime('%d/%m/%Y')
    for col in ('order_qty', 'confirmed_qty', 'confirm_tot'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    df['ingested_at'] = _now()

    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_sales (
                inv_no        TEXT NOT NULL,
                item_code     TEXT NOT NULL,
                description   TEXT,
                account       TEXT,
                customer_name TEXT,
                rep_name      TEXT,
                inv_date      TEXT,
                order_qty     REAL,
                confirmed_qty REAL,
                confirm_tot   REAL,
                ingested_at   TEXT,
                PRIMARY KEY (inv_no, item_code)
            )
        """)

        if force:
            existing_keys = set()
        else:
            existing_keys = {
                (r[0], r[1]) for r in conn.execute(
                    "SELECT inv_no, item_code FROM cash_sales"
                ).fetchall()
            }

        new_df = df[~df.apply(
            lambda r: (str(r['inv_no']), str(r['item_code'])) in existing_keys, axis=1
        )].copy()

        if new_df.empty:
            log.info("  cash_sales: no new rows to add.")
            return

        cols = list(new_df.columns)
        conn.executemany(
            f"INSERT OR REPLACE INTO cash_sales ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            new_df[cols].values.tolist()
        )
        log.info(f"  cash_sales: +{len(new_df):,} new rows "
                 f"({new_df['inv_no'].nunique():,} invoices, "
                 f"{new_df['rep_name'].nunique() if 'rep_name' in new_df.columns else '?'} reps).")


def _reenrich_all():
    """
    Re-run _enrich_from_excel() for ALL rows in the transactions table.
    Use after updating Item List / Customers / PackSize / Weights / Regions
    sheets in the master Excel.

    The function drops the previously-enriched derived columns, re-applies
    all reference joins, validates the result, and overwrites the table.
    """
    log.info("-- REENRICH: Re-enriching all DB transactions from reference sheets --")

    with get_connection() as conn:
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if 'transactions' not in tbls:
            log.error("  transactions table not found — run ingest first.")
            return
        df = pd.read_sql("SELECT * FROM transactions", conn)

    if df.empty:
        log.info("  No transactions in DB to re-enrich.")
        return

    log.info(f"  Loaded {len(df):,} rows.")

    # Drop previously enriched columns so joins are clean.
    # Sales Rep is also dropped so the Customers sheet becomes authoritative
    # (the old Sage-assigned rep won't block the updated mapping).
    enriched_cols = [
        'Items 2', 'B2B_Ref', 'Pack Size Match', 'Rep Name', 'Customer Area',
        'Items', 'Name', 'Item Category', 'B2B', 'Sales Rep',
        'row_hash', 'ingested_at',
    ]
    df = df.drop(columns=[c for c in enriched_cols if c in df.columns])

    # Re-derive Customer Names from the Account code using the updated Customers
    # sheet (Customer -> Name). Without this the reenrich only re-joins downstream
    # attributes (rep/area/B2B) ONTO the existing name, so a renamed customer in
    # the Customers sheet never propagates. Mirrors the Sage-ingest logic.
    if 'Account' in df.columns:
        acc_map = _get_account_customer_map()
        remapped = df['Account'].map(acc_map)
        if 'Customer Names' in df.columns:
            # Only overwrite where the sheet/override actually supplies a name;
            # keep the existing name for account codes with no mapping.
            has_name = remapped.notna() & (remapped.astype(str).str.strip() != '')
            changed = int((has_name & (remapped != df['Customer Names'])).sum())
            df.loc[has_name, 'Customer Names'] = remapped[has_name]
        else:
            df['Customer Names'] = remapped.fillna(df['Account'])
            changed = int(remapped.notna().sum())
        log.info(f"  Customer Names re-derived from Account codes: "
                 f"{changed:,} rows updated.")

    excel_path = _find_excel()
    if not excel_path.exists():
        log.error(f"  Master Excel not found: {excel_path}")
        return

    log.info(f"  Enriching from '{excel_path.name}' ...")
    df = _enrich_from_excel(df, excel_path)

    # Promote Rep Name → Sales Rep so it appears in the CSV output
    if 'Rep Name' in df.columns:
        if 'Sales Rep' not in df.columns:
            df['Sales Rep'] = df['Rep Name']
        else:
            blank = df['Sales Rep'].isna() | df['Sales Rep'].astype(str).str.strip().isin(['', 'nan'])
            df.loc[blank, 'Sales Rep'] = df.loc[blank, 'Rep Name']
        log.info(f"  Sales Rep set from Rep Name: "
                 f"{df['Sales Rep'].notna().sum():,} / {len(df):,} rows have a rep.")

    errors = _validate_enrichment(df)
    if errors:
        _report_enrichment_errors(errors)
        log.warning("  Re-enrichment completed with warnings — DB NOT updated. "
                    "Fix reference sheets and retry.")
        return

    # Rebuild hashes
    hash_cols = [c for c in df.columns if c not in ('AutoID', 'row_hash', 'ingested_at')]
    df['row_hash'] = df[hash_cols].apply(_hash_row, axis=1)
    df['ingested_at'] = _now()

    with get_connection() as conn:
        df.to_sql('transactions', conn, if_exists='replace', index=False)

    _build_products_ref()
    _build_customers_ref()
    log.info(f"  Re-enrichment complete: {len(df):,} rows updated in DB.")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Dashboard Data Ingestion Pipeline'
    )
    parser.add_argument('--force-reload',  action='store_true',
                        help='Ignore all hashes and do a full reload')
    parser.add_argument('--build-only',    action='store_true',
                        help='Skip ingestion, only rebuild CSVs from existing DB')
    parser.add_argument('--ingest-only',   action='store_true',
                        help='Ingest to DB but skip CSV output')
    parser.add_argument('--reset-db',      action='store_true',
                        help='Delete the existing DB and start completely fresh')
    parser.add_argument('--source',        type=Path, default=None,
                        help='Override source Excel file path')
    parser.add_argument('--diagnostics',   action='store_true',
                        help='Print source + DB diagnostics and exit (no data changes)')
    parser.add_argument('--import-csv',   type=Path, metavar='PATH', nargs='?',
                        const=CSV_OUT['transactions'],
                        help='Import a backup transactions CSV into the DB. '
                             'Omit PATH to use Raws/transactions.csv. '
                             'Run --build-only afterwards to rebuild output CSVs.')
    parser.add_argument('--from-sage',   action='store_true',
                        help='Ingest directly from raw Sage exports: '
                             'Raws/Transactions Listing.xlsx + Raws/QIH.xlsx')
    parser.add_argument('--reenrich',    action='store_true',
                        help='Re-run reference-sheet enrichment for all existing DB '
                             'rows (use after updating Item List / Customers / etc.)')
    parser.add_argument('--from-cash-sales', action='store_true',
                        help='Ingest Raws/Cash Sales.xlsx into cash_sales table '
                             'then rebuild transactions.csv only (fast path)')
    args = parser.parse_args()

    # Resolve source file once so every function uses the same path
    _SOURCE_OVERRIDE = args.source

    # Monkey-patch _find_excel to bake in the override for this run
    import functools
    global _find_excel
    _orig_find = _find_excel
    _find_excel = functools.partial(_orig_find, override=_SOURCE_OVERRIDE)

    start = datetime.datetime.now()
    log.info("=" * 65)
    log.info(f"  DASHBOARD DATA PIPELINE  --  {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 65)

    # ── RESET: delete DB *before* opening any connection ──
    if args.reset_db and not args.diagnostics:
        if DB_PATH.exists():
            DB_PATH.unlink()
            log.info(f"  Existing database deleted: {DB_PATH.name}")
        args.force_reload = True

    # ── Initialise (creates DB if missing) ──
    init_db()

    # ── DIAGNOSTICS-ONLY MODE ──
    if args.diagnostics:
        _print_source_diagnostics(_find_excel())
        return

    # ── CSV IMPORT MODE ──
    if args.import_csv:
        _import_transactions_from_csv(csv_path=args.import_csv,
                                      force=args.force_reload)
        return

    # ── CASH SALES FAST PATH ──
    if args.from_cash_sales:
        if not CASH_SALES_FILE.exists():
            log.error(f"  Cash Sales file not found: {CASH_SALES_FILE}")
            sys.exit(1)
        _ingest_cash_sales(CASH_SALES_FILE, force=args.force_reload)
        _build_transactions_csv()
        elapsed = (datetime.datetime.now() - start).total_seconds()
        log.info(f"\n  Pipeline complete in {elapsed:.1f}s\n")
        return

    # ── SAGE DIRECT INGEST MODE ──
    if args.from_sage:
        tl_path = RAWS_DIR / 'Transactions Listing.xlsx'
        if not tl_path.exists():
            log.error(f"  Transactions Listing not found: {tl_path}")
            sys.exit(1)
        _ingest_transactions_listing(tl_path, force=args.force_reload)
        # Inventory / quantity-in-hand: newest of Inventory Listing*.xlsx / QIH.xlsx
        inv_src = _find_inventory_source()
        if inv_src is not None:
            _ingest_qih(inv_src, force=args.force_reload)
        else:
            log.warning("  No inventory export found (Inventory Listing*.xlsx or "
                        "QIH.xlsx) — inventory table left unchanged.")
        _ingest_inv_transactions(force=args.force_reload)
        if CASH_SALES_FILE.exists():
            _ingest_cash_sales(CASH_SALES_FILE, force=args.force_reload)
        if not args.ingest_only:
            _build_transactions_csv()
            _build_inventory_csv()
            _build_invoices_csv()
        elapsed = (datetime.datetime.now() - start).total_seconds()
        _print_summary()
        log.info(f"\n  Pipeline complete in {elapsed:.1f}s\n")
        return

    # ── REENRICH MODE ──
    if args.reenrich:
        _reenrich_all()
        if not args.ingest_only:
            _build_transactions_csv()
            _build_inventory_csv()
        elapsed = (datetime.datetime.now() - start).total_seconds()
        _print_summary()
        log.info(f"\n  Pipeline complete in {elapsed:.1f}s\n")
        return

    # ── Always print diagnostics at run start so you can see what's being used ──
    _print_source_diagnostics(_find_excel())
    log.info("")

    # ── PASS 1: INGEST ──
    if not args.build_only:
        _ingest_transactions(force=args.force_reload)
        _ingest_inventory(force=args.force_reload)
        _ingest_inv_transactions(force=args.force_reload)   # Inv Txn first; falls back to Price Checker
        _ingest_reference_tables(force=args.force_reload)
        _build_products_ref()
        _build_customers_ref()
        if CASH_SALES_FILE.exists():
            _ingest_cash_sales(CASH_SALES_FILE, force=args.force_reload)

    # ── PASS 2: BUILD ──
    if not args.ingest_only:
        _build_transactions_csv()
        _build_inventory_csv()
        _build_invoices_csv()

    elapsed = (datetime.datetime.now() - start).total_seconds()
    _print_summary()
    log.info(f"\n  Pipeline complete in {elapsed:.1f}s\n")


if __name__ == '__main__':
    main()
