import os
import io
import sqlite3
import datetime

import pandas as pd
from dash import html, dcc, dash_table, Input, Output, State, callback

import Assets.styles as s

# ── DB helpers ────────────────────────────────────────────────────────────────
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'dashboard.db'
)

# Served transactions source = the computed 'transactions_final' DB table (has
# Unit Price, Weight, Lat/Lon, Avg_Price, etc.). transactions.csv is the backup
# and automatic fallback. The raw 'transactions' table lacks the derived columns.
_TRANS_TABLE = 'transactions_final'
_TRANS_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'Raws', 'transactions.csv'
)


def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.execute('PRAGMA journal_mode=WAL')
    return c


def _trans_table_rows() -> int:
    """Row count of the served transactions_final table, or -1 if unavailable."""
    if not os.path.exists(_DB_PATH):
        return -1
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (_TRANS_TABLE,)
            ).fetchone()
            if row is None:
                return -1
            return conn.execute(f'SELECT COUNT(*) FROM "{_TRANS_TABLE}"').fetchone()[0]
    except Exception:
        return -1


def _load_transactions_frame() -> pd.DataFrame:
    """Full computed transactions frame: DB table 'transactions_final' preferred,
    transactions.csv backup as fallback."""
    if _trans_table_rows() > 0:
        try:
            with _conn() as conn:
                return pd.read_sql(f'SELECT * FROM "{_TRANS_TABLE}"', conn)
        except Exception:
            pass
    return pd.read_csv(_TRANS_CSV, low_memory=False)


# ── Table catalogue ───────────────────────────────────────────────────────────
# Each entry: (table_name, display_label, group, has_date_col, internal_cols_to_drop)
_TABLES = [
    # ── Data tables ──────────────────────────────────────────────────────────
    ('transactions',       'Transactions (sales data)',     'Data',      True,
     ['row_hash', 'ingested_at']),
    ('invoices',           'Invoices (invoice checker)',    'Data',      True,
     ['row_hash', 'ingested_at']),
    ('inventory',          'Inventory (stock on hand)',     'Data',      False,
     ['row_hash', 'ingested_at']),
    # ── Reference tables ─────────────────────────────────────────────────────
    ('products_ref',       'Products Reference',           'Reference',  False,
     ['derived_at']),
    ('customers_ref',      'Customers Reference',          'Reference',  False,
     ['derived_at']),
    ('price_list',         'Price List',                   'Reference',  False,
     ['row_hash']),
    # ── Admin override tables ─────────────────────────────────────────────────
    ('price_overrides',    'Price Overrides (admin)',      'Admin',      False, []),
    ('customer_overrides', 'Customer Overrides (admin)',   'Admin',      False, []),
    ('item_overrides',     'Item Overrides (admin)',       'Admin',      False, []),
    # ── Log / audit tables ────────────────────────────────────────────────────
    ('ingestion_log',      'Ingestion Log',               'Log',        False, []),
    ('validation_errors',  'Validation Errors',           'Log',        False, []),
    ('ref_log',            'Reference Change Log',        'Log',        False, []),
]

_TABLE_MAP   = {t[0]: t for t in _TABLES}
_DROPDOWN_OPTIONS = (
    [{'label': '[Format]  Sales Analysis', 'value': 'sales_analysis'}]
    + [{'label': f'[{grp}]  {label}', 'value': tbl}
       for tbl, label, grp, *_ in _TABLES]
)

# Sheet-2 column order for the transactions export (same as original)
_TRANS_COL_ORDER = [
    'Counter', 'Date', 'AutoID', 'Tran', 'Account', 'Customer Names',
    'Item Code', 'Name', 'QTY PCS', 'Quantity', 'Value', 'Unit Price',
    'Group', 'Items 2', 'Items', 'Item Category', 'B2B', 'Sales Rep',
    'Location', 'WarehouseName', 'Distance from Nairobi', 'Weight',
    'Lat', 'Lon', 'Pack Size Match',
    'Avg_Price', 'Price_Deviation_Pct', 'Price_Flag', 'Weight_Per_Unit', 'Rev_Per_Km',
]

# ── Sales Analysis export format ───────────────────────────────────────────────
# A reshaped view of the transactions data using the Sales Analysis column names.
# Left = export column name, right = source column in the computed transactions
# data. 'Item Category' is NOT a native transactions column — it is looked up
# from the master Item List sheet (Simple Code -> Item Category) by Item Code.
_MASTER_XLSX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'Raws', 'Transactions from Jan to Sep 25.xlsx'
)

_SALES_ANALYSIS_MAP = [
    ('Item Code',        'Item Code'),
    ('Item Description', 'Name'),
    ('Inv No.',          'Group'),
    ('Customer Code',    'Account'),
    ('Customer Names',   'Customer Names'),
    ('ItemCategoryName', 'Item Category'),
    ('Warehouse Name',   'WarehouseName'),
    ('Weight',           'Weight'),
    ('Quantity',         'Quantity'),
    ('Amount',           'Value'),
    ('Area',             'Location'),
    ('Rep',              'Sales Rep'),
]

# Cached Item Code -> Item Category lookup (loaded once per process)
_ITEM_CAT_CACHE = None

_BTN       = s.BTN_STYLE
_BTN_GREEN = {**s.BTN_STYLE, 'backgroundColor': '#27ae60'}
_HDR       = {'backgroundColor': '#2c3e50', 'color': 'white', 'fontWeight': 'bold',
              'textAlign': 'center', 'padding': '8px'}
_CELL      = {'textAlign': 'left', 'padding': '6px', 'fontFamily': 'Segoe UI', 'fontSize': '12px'}

layout = html.Div([
    html.H1("Database Export", style=s.HEADER_TITLE),

    html.Div([
        html.P(
            "Select any database table and download it as a CSV file. "
            "Column names match exactly what the dashboard uses internally.",
            style={'textAlign': 'center', 'color': '#7f8c8d', 'marginBottom': '20px',
                   'fontSize': '13px'}
        ),

        # ── Table selector ────────────────────────────────────────────────────
        html.Div([
            html.Label("SELECT TABLE:", style={
                'fontWeight': 'bold', 'fontSize': '11px',
                'color': '#95a5a6', 'marginRight': '8px'
            }),
            dcc.Dropdown(
                id='export-table-select',
                options=_DROPDOWN_OPTIONS,
                value='transactions',
                clearable=False,
                style={'flex': '1', 'fontSize': '13px', 'minWidth': '320px'},
            ),
        ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px',
                  'marginBottom': '15px'}),

        # ── Column preview ────────────────────────────────────────────────────
        html.Div(id='export-col-preview',
                 style={'backgroundColor': '#f8f9fa', 'borderRadius': '6px',
                        'padding': '10px 15px', 'marginBottom': '15px',
                        'border': '1px solid #e0e0e0', 'fontSize': '12px',
                        'color': '#555'}),

        # ── Filters (shown only when relevant) ────────────────────────────────
        html.Div(id='export-filters-section', children=[
            html.Div([
                # Date range
                html.Div([
                    html.Label("DATE RANGE:", style={
                        'fontWeight': 'bold', 'fontSize': '11px',
                        'color': '#95a5a6', 'marginRight': '8px'
                    }),
                    dcc.DatePickerRange(
                        id='export-date-range',
                        display_format='DD MMM YYYY',
                        clearable=True,
                        style={'display': 'inline-block'},
                    ),
                ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '20px'}),

                # Tran type (transactions only)
                html.Div(id='export-tran-wrapper', children=[
                    html.Label("TRAN TYPE:", style={
                        'fontWeight': 'bold', 'fontSize': '11px',
                        'color': '#95a5a6', 'marginRight': '8px'
                    }),
                    dcc.Dropdown(
                        id='export-tran-filter',
                        options=[
                            {'label': 'All', 'value': 'ALL'},
                            {'label': 'INV (Invoices)', 'value': 'INV'},
                            {'label': 'CRN (Credits)', 'value': 'CRN'},
                        ],
                        value='ALL',
                        clearable=False,
                        style={'width': '180px', 'fontSize': '13px'},
                    ),
                ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '20px'}),

                # Segment
                html.Div(id='export-segment-wrapper', children=[
                    html.Label("SEGMENT:", style={
                        'fontWeight': 'bold', 'fontSize': '11px',
                        'color': '#95a5a6', 'marginRight': '8px'
                    }),
                    dcc.RadioItems(
                        id='export-segment',
                        options=['All', 'B2B', 'B2C'],
                        value='All',
                        inline=True,
                        style={'fontSize': '13px'},
                    ),
                ], style={'display': 'flex', 'alignItems': 'center'}),
            ], style={
                'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap',
                'backgroundColor': '#f8f9fa', 'padding': '12px 15px',
                'borderRadius': '8px',
                'border': f'1px solid {s.COLORS.get("border", "#e0e0e0")}',
                'gap': '10px',
            }),
        ], style={'marginBottom': '15px'}),

        # ── Action row ────────────────────────────────────────────────────────
        html.Div([
            html.Button("Preview (first 50 rows)", id='btn-preview-export',
                        style=_BTN, n_clicks=0),
            html.Button("Download Full CSV", id='btn-download-export',
                        style={**_BTN_GREEN, 'marginLeft': '10px'}, n_clicks=0),
            dcc.Download(id='export-download'),
            html.Span(id='export-count-msg',
                      style={'marginLeft': '15px', 'color': '#555',
                             'fontSize': '13px', 'fontWeight': 'bold'}),
        ], style={'marginBottom': '15px', 'display': 'flex', 'alignItems': 'center'}),

        html.Div(id='export-error-msg',
                 style={'color': '#e74c3c', 'fontWeight': 'bold', 'marginBottom': '10px'}),

        # ── Data preview table ────────────────────────────────────────────────
        html.Div(id='export-preview-table'),

    ], style={**s.SECTION, 'maxWidth': '1200px', 'margin': '0 auto'}),
], style=s.MAIN_CONTAINER)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_table_info(table_name: str):
    """Return (columns, row_count, has_date) for the given table."""
    # Sales Analysis format: fixed export columns, sourced from transactions
    if table_name == 'sales_analysis':
        cols = [name for name, _ in _SALES_ANALYSIS_MAP]
        rows = _trans_table_rows()
        cnt = rows if rows > 0 else 0
        if cnt == 0 and os.path.exists(_TRANS_CSV):
            try:
                cnt = len(pd.read_csv(_TRANS_CSV, usecols=[0]))
            except Exception:
                cnt = 0
        return cols, cnt, True

    entry = _TABLE_MAP.get(table_name)
    if not entry:
        return [], 0, False
    _, _, _, has_date, _ = entry
    # transactions: columns + row count from the computed source (DB table -> CSV)
    if table_name == 'transactions':
        rows = _trans_table_rows()
        if rows > 0:
            try:
                with _conn() as conn:
                    hdr = pd.read_sql(f'SELECT * FROM "{_TRANS_TABLE}" LIMIT 0', conn)
                return list(hdr.columns), rows, has_date
            except Exception:
                pass
        if os.path.exists(_TRANS_CSV):
            try:
                hdr = pd.read_csv(_TRANS_CSV, nrows=0)
                cnt_df = pd.read_csv(_TRANS_CSV, usecols=[0])
                return list(hdr.columns), len(cnt_df), has_date
            except Exception:
                pass
    try:
        with _conn() as conn:
            cols = [r[1] for r in conn.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()]
            cnt = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        return cols, cnt, has_date
    except Exception:
        return [], 0, has_date


def _date_filter_sql(table_name: str, start_date, end_date):
    """Return (WHERE clauses list, params list) for date filtering."""
    entry = _TABLE_MAP.get(table_name)
    if not entry or not entry[3]:   # no date column
        return [], []

    # transactions stores dates as DD/MM/YYYY strings
    if table_name == 'transactions':
        clauses, params = [], []
        if start_date:
            clauses.append(
                "date(substr(Date,7,4)||'-'||substr(Date,4,2)||'-'||substr(Date,1,2)) >= date(?)"
            )
            params.append(start_date[:10])
        if end_date:
            clauses.append(
                "date(substr(Date,7,4)||'-'||substr(Date,4,2)||'-'||substr(Date,1,2)) <= date(?)"
            )
            params.append(end_date[:10])
        return clauses, params

    # invoices uses DD/MM/YYYY too
    if table_name == 'invoices':
        clauses, params = [], []
        if start_date:
            clauses.append(
                "date(substr(Date,7,4)||'-'||substr(Date,4,2)||'-'||substr(Date,1,2)) >= date(?)"
            )
            params.append(start_date[:10])
        if end_date:
            clauses.append(
                "date(substr(Date,7,4)||'-'||substr(Date,4,2)||'-'||substr(Date,1,2)) <= date(?)"
            )
            params.append(end_date[:10])
        return clauses, params

    return [], []


def _build_query(table_name, start_date, end_date, tran_filter, segment):
    clauses, params = _date_filter_sql(table_name, start_date, end_date)

    if table_name == 'transactions':
        if tran_filter and tran_filter != 'ALL':
            clauses.append('Tran = ?')
            params.append(tran_filter)
        if segment and segment != 'All':
            clauses.append('UPPER(B2B) = ?')
            params.append(segment.upper())

    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    return f'SELECT * FROM "{table_name}" {where}', params


def _fetch_transactions_from_csv(start_date, end_date, tran_filter, segment) -> pd.DataFrame:
    """Read the computed transactions frame (DB table preferred, CSV fallback)
    so derived columns are present, then apply the requested filters."""
    df = _load_transactions_frame()

    # Date filter — dates are DD/MM/YYYY strings
    if start_date or end_date:
        dates = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce')
        if start_date:
            df = df[dates >= pd.Timestamp(start_date[:10])]
            dates = dates[df.index]
        if end_date:
            df = df[pd.to_datetime(df['Date'], dayfirst=True, errors='coerce') <= pd.Timestamp(end_date[:10])]

    if tran_filter and tran_filter != 'ALL' and 'Tran' in df.columns:
        df = df[df['Tran'].astype(str).str.strip() == tran_filter]

    if segment and segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    # Drop internal pipeline columns
    entry = _TABLE_MAP.get('transactions')
    if entry:
        df = df.drop(columns=[c for c in entry[4] if c in df.columns])

    # Reorder columns
    ordered = [c for c in _TRANS_COL_ORDER if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]
    return df[ordered + rest].reset_index(drop=True)


def _item_category_lookup() -> dict:
    """Item Code -> Item Category, read once from the master Item List sheet."""
    global _ITEM_CAT_CACHE
    if _ITEM_CAT_CACHE is not None:
        return _ITEM_CAT_CACHE

    lut = {}
    for engine in (None, 'calamine'):
        try:
            kw = {'sheet_name': 'Item List'}
            if engine:
                kw['engine'] = engine
            il = pd.read_excel(_MASTER_XLSX, **kw)
            il = il[['Simple Code', 'Item Category']].dropna(subset=['Simple Code'])
            code = il['Simple Code'].astype(str).str.strip()
            cat = il['Item Category'].astype(str).str.strip().replace({'nan': ''})
            lut = dict(zip(code, cat))
            break
        except Exception:
            continue

    _ITEM_CAT_CACHE = lut
    return lut


def _build_sales_analysis(start_date, end_date, tran_filter, segment) -> pd.DataFrame:
    """Transactions reshaped to the Sales Analysis format: selected columns
    renamed, with ItemCategoryName looked up from the master Item List sheet."""
    df = _fetch_transactions_from_csv(start_date, end_date, tran_filter, segment)

    # Inject Item Category via Item Code -> Simple Code lookup (fallback: blank)
    lut = _item_category_lookup()
    if 'Item Code' in df.columns:
        codes = df['Item Code'].astype(str).str.strip()
        df['Item Category'] = codes.map(lut).fillna('')
    else:
        df['Item Category'] = ''

    out = pd.DataFrame(index=df.index)
    for export_name, src in _SALES_ANALYSIS_MAP:
        out[export_name] = df[src] if src in df.columns else ''
    return out.reset_index(drop=True)


def _fetch_df(table_name, start_date, end_date, tran_filter, segment) -> pd.DataFrame:
    # Sales Analysis: transactions reshaped to the Sales Analysis column names
    if table_name == 'sales_analysis':
        return _build_sales_analysis(start_date, end_date, tran_filter, segment)

    # transactions: use computed CSV so Unit Price, Weight, Lat/Lon etc. are populated
    if table_name == 'transactions' and os.path.exists(_TRANS_CSV):
        return _fetch_transactions_from_csv(start_date, end_date, tran_filter, segment)

    q, params = _build_query(table_name, start_date, end_date, tran_filter, segment)
    with _conn() as conn:
        df = pd.read_sql(q, conn, params=params)

    # Drop internal pipeline columns
    entry = _TABLE_MAP.get(table_name)
    if entry:
        drop = entry[4]
        df = df.drop(columns=[c for c in drop if c in df.columns])

    return df


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks
# ══════════════════════════════════════════════════════════════════════════════

@callback(
    Output('export-col-preview',      'children'),
    Output('export-filters-section',  'style'),
    Output('export-tran-wrapper',     'style'),
    Output('export-segment-wrapper',  'style'),
    Input('export-table-select', 'value'),
)
def update_table_info(table_name):
    """Update column preview and show/hide filters based on selected table."""
    if not table_name:
        return "Select a table above.", {'display': 'none'}, {'display': 'none'}, {'display': 'none'}

    cols, cnt, has_date = _get_table_info(table_name)
    entry = _TABLE_MAP.get(table_name, ())
    drop_cols = entry[4] if len(entry) > 4 else []
    display_cols = [c for c in cols if c not in drop_cols]

    col_text = html.Div([
        html.Span(f"{cnt:,} rows  •  {len(display_cols)} columns:  ",
                  style={'fontWeight': 'bold', 'color': '#2c3e50'}),
        html.Span(', '.join(display_cols), style={'color': '#555'}),
    ])

    _txn_like = table_name in ('transactions', 'sales_analysis')
    filter_style   = {'marginBottom': '15px'} if has_date else {'display': 'none'}
    tran_style     = {'display': 'flex', 'alignItems': 'center', 'marginRight': '20px'} \
                     if _txn_like else {'display': 'none'}
    seg_style      = {'display': 'flex', 'alignItems': 'center'} \
                     if _txn_like else {'display': 'none'}

    return col_text, filter_style, tran_style, seg_style


@callback(
    Output('export-preview-table', 'children'),
    Output('export-count-msg',     'children'),
    Output('export-error-msg',     'children', allow_duplicate=True),
    Input('btn-preview-export',    'n_clicks'),
    State('export-table-select',   'value'),
    State('export-date-range',     'start_date'),
    State('export-date-range',     'end_date'),
    State('export-tran-filter',    'value'),
    State('export-segment',        'value'),
    prevent_initial_call=True,
)
def preview_table(n, table_name, start_date, end_date, tran_filter, segment):
    if not table_name:
        return html.Div(), "", "Select a table first."
    try:
        # Sales Analysis: build the reshaped format for preview
        if table_name == 'sales_analysis':
            df_prev = _build_sales_analysis(start_date, end_date, tran_filter, segment)
            total = len(df_prev)
            df_prev = df_prev.head(50)
        # For transactions use the computed CSV so derived columns show in preview
        elif table_name == 'transactions' and os.path.exists(_TRANS_CSV):
            df_prev = _fetch_transactions_from_csv(start_date, end_date, tran_filter, segment)
            total = len(df_prev)
            df_prev = df_prev.head(50)
        else:
            q, params = _build_query(table_name, start_date, end_date, tran_filter, segment)
            with _conn() as conn:
                total = conn.execute(f"SELECT COUNT(*) FROM ({q})", params).fetchone()[0]
                df_prev = pd.read_sql(f"{q} LIMIT 50", conn, params=params)
            entry = _TABLE_MAP.get(table_name, ())
            drop_cols = entry[4] if len(entry) > 4 else []
            df_prev = df_prev.drop(columns=[c for c in drop_cols if c in df_prev.columns])

        cols = [{'name': c, 'id': c} for c in df_prev.columns]
        tbl = dash_table.DataTable(
            data=df_prev.to_dict('records'),
            columns=cols,
            page_size=15,
            filter_action='native',
            sort_action='native',
            style_table={'overflowX': 'auto'},
            style_header=_HDR,
            style_cell=_CELL,
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}],
        )
        msg = f"{total:,} total rows (showing first 50)"
        return tbl, msg, ""
    except Exception as e:
        return html.Div(), "", f"Preview failed: {e}"


@callback(
    Output('export-download', 'data'),
    Output('export-error-msg', 'children'),
    Input('btn-download-export', 'n_clicks'),
    State('export-table-select',  'value'),
    State('export-date-range',    'start_date'),
    State('export-date-range',    'end_date'),
    State('export-tran-filter',   'value'),
    State('export-segment',       'value'),
    prevent_initial_call=True,
)
def download_csv(n, table_name, start_date, end_date, tran_filter, segment):
    if not table_name:
        return None, "Select a table first."
    try:
        df = _fetch_df(table_name, start_date, end_date, tran_filter, segment)
        if df.empty:
            return None, "No data found for the selected filters."

        date_str = datetime.date.today().strftime('%Y-%m-%d')
        filename = f"{table_name}_export_{date_str}.csv"

        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return {'content': buf.getvalue(), 'filename': filename, 'type': 'text/csv'}, ""
    except Exception as e:
        return None, f"Export failed: {e}"
