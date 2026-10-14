"""
purge_sage_rows.py — Remove distorted Sage transaction rows from dashboard.db

Usage (dry run — shows what WOULD be deleted, does nothing):
    python purge_sage_rows.py --from 2026-06-16
    python purge_sage_rows.py --blank-location

Usage (delete and rebuild transactions.csv):
    python purge_sage_rows.py --from 2026-06-16 --confirm
    python purge_sage_rows.py --blank-location --confirm

Filtering options (can be combined — rows matching ANY criterion are deleted):
    --from YYYY-MM-DD      Delete rows with Date >= this date  (also accepts YYYY/MM/DD)
    --to   YYYY-MM-DD      Delete rows with Date <= this date  (also accepts YYYY/MM/DD)
    --blank-location       Delete rows where Location is NULL / '' / 'Unknown'
    --zero-quantity        Delete rows where Quantity is 0 or NULL

After confirmed deletion the script automatically calls:
    python CSVcreator.py --build-only
to rebuild transactions.csv from the updated DB so the dashboard reflects
the change immediately. Then re-upload the fresh Sage TL file via the admin
upload page to repopulate the purged rows with clean data.
"""

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(_SCRIPT_DIR, 'data', 'dashboard.db')
CSVCREATOR   = os.path.join(_SCRIPT_DIR, 'CSVcreator.py')


def _normalise_date(raw: str) -> str:
    """Accept YYYY-MM-DD or YYYY/MM/DD and return YYYY-MM-DD."""
    s = raw.strip().replace('/', '-')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        print(f"ERROR: Date '{raw}' is not in YYYY-MM-DD or YYYY/MM/DD format.")
        sys.exit(1)
    return s


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# Date column is stored as DD/MM/YYYY strings.
# Convert to YYYY-MM-DD for ISO comparison:
_DATE_ISO = "SUBSTR(Date,7,4)||'-'||SUBSTR(Date,4,2)||'-'||SUBSTR(Date,1,2)"


def _build_where(args) -> tuple[str, list]:
    clauses: list[str] = []
    params:  list      = []

    if args.date_from:
        clauses.append(f"{_DATE_ISO} >= ?")
        params.append(_normalise_date(args.date_from))

    if args.date_to:
        clauses.append(f"{_DATE_ISO} <= ?")
        params.append(_normalise_date(args.date_to))

    if args.blank_location:
        clauses.append("(Location IS NULL OR TRIM(Location) = '' OR Location = 'Unknown')")

    if args.zero_quantity:
        clauses.append("(Quantity IS NULL OR Quantity = 0)")

    if not clauses:
        print("ERROR: Specify at least one filter: --from, --to, --blank-location, --zero-quantity")
        sys.exit(1)

    where = " OR ".join(f"({c})" for c in clauses)
    return where, params


def _preview(conn: sqlite3.Connection, where: str, params: list) -> int:
    cur = conn.cursor()

    cur.execute(f"SELECT COUNT(*) FROM transactions WHERE {where}", params)
    count = cur.fetchone()[0]

    print(f"\n{'=' * 60}")
    print(f"  Rows matching delete filter: {count:,}")
    print(f"{'=' * 60}")

    if count == 0:
        print("  Nothing to delete.")
        return 0

    cur.execute(
        f"SELECT MIN({_DATE_ISO}), MAX({_DATE_ISO}) FROM transactions WHERE {where}", params
    )
    row = cur.fetchone()
    print(f"  Date range : {row[0]}  to  {row[1]}")

    cur.execute(f"SELECT SUM(Value) FROM transactions WHERE {where}", params)
    total_value = cur.fetchone()[0] or 0
    print(f"  Total Value: Ksh {total_value:,.2f}")

    cur.execute(
        f"""
        SELECT [Customer Names], COUNT(*) AS rows, SUM(Value) AS val
        FROM   transactions
        WHERE  {where}
        GROUP  BY [Customer Names]
        ORDER  BY rows DESC
        LIMIT  10
        """,
        params,
    )
    print("\n  Top customers affected:")
    print(f"  {'Customer':<35} {'Rows':>6}  {'Value (Ksh)':>14}")
    print(f"  {'-'*35} {'-'*6}  {'-'*14}")
    for r in cur.fetchall():
        print(f"  {str(r[0]):<35} {r[1]:>6,}  {(r[2] or 0):>14,.2f}")

    cur.execute(
        f"""
        SELECT
            SUM(CASE WHEN Location IS NULL OR TRIM(Location)='' OR Location='Unknown' THEN 1 ELSE 0 END),
            SUM(CASE WHEN Quantity IS NULL OR Quantity=0 THEN 1 ELSE 0 END)
        FROM transactions WHERE {where}
        """,
        params,
    )
    stats = cur.fetchone()
    print(f"\n  Blank Location within target rows : {stats[0]:,}")
    print(f"  Zero/null Quantity within target  : {stats[1]:,}")

    total_cur = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"\n  Total rows in DB now : {total_cur:,}")
    print(f"  Rows remaining after : {total_cur - count:,}")
    print(f"{'=' * 60}\n")

    return count


def _backup(db_path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.replace(".db", f"_backup_{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _rebuild_csv() -> bool:
    """Call CSVcreator.py --build-only to regenerate transactions.csv from DB."""
    print("\nRebuilding transactions.csv from updated DB...")
    result = subprocess.run(
        [sys.executable, CSVCREATOR, '--build-only'],
        cwd=_SCRIPT_DIR,
    )
    if result.returncode == 0:
        print("  transactions.csv rebuilt successfully.")
        return True
    else:
        print(f"  WARNING: CSVcreator --build-only exited with code {result.returncode}.")
        print("  Run it manually:  python CSVcreator.py --build-only")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Purge distorted Sage rows from dashboard.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                        help="Delete rows with Date >= this date (also accepts YYYY/MM/DD)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                        help="Delete rows with Date <= this date (also accepts YYYY/MM/DD)")
    parser.add_argument("--blank-location", action="store_true",
                        help="Delete rows where Location is blank / Unknown")
    parser.add_argument("--zero-quantity", action="store_true",
                        help="Delete rows where Quantity is 0 or NULL")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete (default is dry-run preview only)")
    parser.add_argument("--no-rebuild", action="store_true",
                        help="Skip automatic CSV rebuild after deletion")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"Path to dashboard.db (default: {DB_PATH})")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: DB not found at {args.db}")
        sys.exit(1)

    where, params = _build_where(args)

    conn = _connect(args.db)
    count = _preview(conn, where, params)
    conn.close()

    if count == 0:
        sys.exit(0)

    if not args.confirm:
        print("DRY RUN - no data was deleted.")
        print("Re-run with --confirm to actually delete the rows above.\n")
        sys.exit(0)

    # ── Confirmed delete ─────────────────────────────────────────────────────
    print("Creating backup before deletion...")
    backup = _backup(args.db)
    print(f"  Backup saved: {backup}\n")

    conn = _connect(args.db)
    cur = conn.cursor()
    cur.execute(f"DELETE FROM transactions WHERE {where}", params)
    deleted = cur.rowcount
    conn.commit()
    cur.execute("DELETE FROM validation_errors")
    conn.commit()
    conn.close()

    print(f"Deleted {deleted:,} rows from transactions.")
    print("Cleared validation_errors (stale flags removed).")

    # ── Rebuild CSV ──────────────────────────────────────────────────────────
    if not args.no_rebuild:
        _rebuild_csv()

    print("\nNext steps:")
    print("  1. Re-upload the Sage TL file via the admin upload page.")
    print("     The pipeline will re-ingest with corrected Quantity, Location, Lat, Lon.")
    print(f"\n  Backup at: {backup}\n")


if __name__ == "__main__":
    main()
