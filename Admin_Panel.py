import sys
import os
import sqlite3
import datetime
import threading
import time

import pandas as pd
from dash import html, dcc, dash_table, Input, Output, State, callback, ctx

import Assets.styles as s

# ── DB helpers ────────────────────────────────────────────────────────────────
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'dashboard.db'
)


def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.execute('PRAGMA journal_mode=WAL')
    return c


def _ensure_override_tables():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS price_overrides (
                item_code     TEXT    NOT NULL,
                customer_name TEXT    NOT NULL DEFAULT '',
                master_price  REAL,
                old_price     REAL,
                pct_increase  REAL,
                updated_by    TEXT,
                updated_at    TEXT,
                PRIMARY KEY (item_code, customer_name)
            );
            CREATE TABLE IF NOT EXISTS customer_overrides (
                account       TEXT PRIMARY KEY,
                customer_name TEXT,
                rep_name      TEXT,
                customer_area TEXT,
                b2b           TEXT,
                added_by      TEXT,
                added_at      TEXT
            );
            CREATE TABLE IF NOT EXISTS item_overrides (
                item_code     TEXT PRIMARY KEY,
                name          TEXT,
                items_2       TEXT,
                items         TEXT,
                b2b           TEXT,
                added_by      TEXT,
                added_at      TEXT
            );
            CREATE TABLE IF NOT EXISTS transaction_overrides (
                autoid        TEXT PRIMARY KEY,
                rep_name      TEXT,
                orig_rep      TEXT,
                customer_name TEXT,
                invoice_no    TEXT,
                txn_date      TEXT,
                updated_by    TEXT,
                updated_at    TEXT
            );
        """)


_ensure_override_tables()

# ── Shared styles ─────────────────────────────────────────────────────────────
_HDR = {'backgroundColor': '#2c3e50', 'color': 'white', 'fontWeight': 'bold',
        'textAlign': 'center', 'padding': '10px'}
_CELL = {'textAlign': 'left', 'padding': '8px', 'fontFamily': 'Segoe UI', 'fontSize': '13px'}

_INPUT = {
    'width': '100%', 'padding': '8px', 'borderRadius': '4px',
    'border': '1px solid #ccc', 'fontFamily': 'Segoe UI', 'fontSize': '13px',
    'boxSizing': 'border-box'
}
_BTN_GREEN = {**s.BTN_STYLE, 'backgroundColor': '#27ae60'}
_BTN_BLUE = {**s.BTN_STYLE, 'backgroundColor': '#2980b9'}

_LABEL = {'fontWeight': 'bold', 'fontSize': '12px', 'color': '#7f8c8d',
          'marginBottom': '4px', 'display': 'block'}

_MSG_OK  = {'color': '#27ae60', 'fontWeight': 'bold', 'marginTop': '10px'}
_MSG_ERR = {'color': '#e74c3c', 'fontWeight': 'bold', 'marginTop': '10px'}


def _field(label, id_, placeholder='', type_='text', value=''):
    return html.Div([
        html.Label(label, style=_LABEL),
        dcc.Input(id=id_, type=type_, placeholder=placeholder, value=value,
                  style=_INPUT, debounce=False),
    ], style={'marginBottom': '12px'})


def _row(*cols, gap='15px'):
    return html.Div(list(cols),
                    style={'display': 'grid',
                           'gridTemplateColumns': ' '.join(['1fr'] * len(cols)),
                           'gap': gap, 'marginBottom': '12px'})


# ── Layout ───────────────────────────────────────────────────────────────────
layout = html.Div([
    html.H1("Data Administration Panel",
            style={**s.HEADER_TITLE, 'marginBottom': '5px'}),
    html.P("Changes here are written to the database and picked up on the next pipeline run.",
           style={'textAlign': 'center', 'color': '#95a5a6', 'marginBottom': '20px',
                  'fontSize': '13px'}),

    dcc.Tabs(id='admin-tabs', value='prices', children=[

        # ── TAB 1: PRICE MANAGER ─────────────────────────────────────────────
        dcc.Tab(label='Price Manager', value='prices', children=[
            html.Div([
                html.P(
                    "Search an item code from the invoices database and set the correct "
                    "Old Price and Master Price. These corrections are loaded by the pipeline "
                    "BEFORE the Master Price List Excel, so they always win.",
                    style={'color': '#555', 'marginBottom': '15px', 'fontSize': '13px'}
                ),
                _row(
                    html.Div([
                        html.Label("Search Item Code", style=_LABEL),
                        dcc.Input(id='price-search', type='text',
                                  placeholder='e.g. B/GLASS/124/B  (leave blank to load all overrides)',
                                  style=_INPUT, debounce=True),
                    ]),
                    html.Div([
                        html.Label(" ", style=_LABEL),
                        html.Button("Load", id='btn-load-prices', style=_BTN_BLUE,
                                    n_clicks=0),
                    ], style={'paddingTop': '4px'}),
                ),

                dash_table.DataTable(
                    id='admin-price-table',
                    columns=[
                        {'name': 'Item Code',       'id': 'item_code',     'editable': False},
                        {'name': 'Description',     'id': 'description',   'editable': False},
                        {'name': 'Old Price',       'id': 'old_price',     'editable': True,
                         'type': 'numeric'},
                        {'name': 'Master Price',    'id': 'master_price',  'editable': True,
                         'type': 'numeric'},
                        {'name': '% Increase',      'id': 'pct_increase',  'editable': True,
                         'type': 'numeric'},
                        {'name': 'Customer (opt)',  'id': 'customer_name', 'editable': True},
                        {'name': 'Last Updated',    'id': 'updated_at',    'editable': False},
                    ],
                    data=[],
                    editable=True,
                    row_deletable=True,
                    row_selectable=False,
                    sort_action='native',
                    filter_action='native',
                    page_size=20,
                    style_header=_HDR,
                    style_cell=_CELL,
                    style_table={'overflowX': 'auto'},
                    style_data_conditional=[
                        {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}
                    ],
                ),
                html.Div(id='price-msg', style=_MSG_OK),
                html.Div([
                    html.Button("Save Price Overrides", id='btn-save-prices',
                                style=_BTN_GREEN, n_clicks=0),
                    html.Span("  Changes apply on next pipeline run (re-upload Sage TL).",
                              style={'color': '#888', 'fontSize': '12px', 'marginLeft': '10px'}),
                ], style={'marginTop': '15px'}),
            ], style={**s.SECTION, 'marginTop': '15px'}),
        ]),

        # ── TAB 2: CUSTOMER MANAGER ──────────────────────────────────────────
        dcc.Tab(label='Customer Manager', value='customers', children=[
            html.Div([
                html.P(
                    "Add or update customer reference data. These entries are loaded FIRST "
                    "before the Excel Customers sheet, so they take priority for Account → Name "
                    "and Name → Rep/Area lookups.",
                    style={'color': '#555', 'marginBottom': '15px', 'fontSize': '13px'}
                ),
                _row(
                    _field("Account Code *", 'cust-account', 'e.g. GOM001'),
                    _field("Customer Name *", 'cust-name', 'e.g. GONGONI MARKET'),
                ),
                _row(
                    _field("Rep Name", 'cust-rep', 'e.g. GEORGE'),
                    _field("Customer Area", 'cust-area', 'e.g. Nairobi'),
                    html.Div([
                        html.Label("B2B / B2C", style=_LABEL),
                        dcc.Dropdown(id='cust-b2b',
                                     options=['B2B', 'B2C', 'Unknown'],
                                     value='B2C',
                                     clearable=False,
                                     style={'fontSize': '13px'}),
                    ]),
                ),
                html.Button("Add / Update Customer", id='btn-add-customer',
                            style=_BTN_GREEN, n_clicks=0),
                html.Div(id='cust-msg', style=_MSG_OK),

                html.Hr(style={'margin': '20px 0'}),
                html.H4("Existing Customer Overrides",
                        style={'color': s.COLORS['primary'], 'marginBottom': '10px'}),
                html.Button("Refresh", id='btn-refresh-customers', style=_BTN_BLUE,
                            n_clicks=0),
                dash_table.DataTable(
                    id='cust-table',
                    columns=[
                        {'name': 'Account',       'id': 'account'},
                        {'name': 'Customer Name', 'id': 'customer_name'},
                        {'name': 'Rep Name',      'id': 'rep_name'},
                        {'name': 'Area',          'id': 'customer_area'},
                        {'name': 'B2B',           'id': 'b2b'},
                        {'name': 'Added At',      'id': 'added_at'},
                    ],
                    data=[],
                    sort_action='native',
                    filter_action='native',
                    page_size=15,
                    row_deletable=True,
                    style_header=_HDR,
                    style_cell=_CELL,
                    style_table={'overflowX': 'auto', 'marginTop': '10px'},
                    style_data_conditional=[
                        {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}
                    ],
                ),
                html.Div([
                    html.Button("Save Deletions", id='btn-save-cust-deletions',
                                style={**s.BTN_STYLE, 'backgroundColor': '#c0392b'},
                                n_clicks=0),
                ], style={'marginTop': '10px'}),
                html.Div(id='cust-del-msg', style=_MSG_ERR),
            ], style={**s.SECTION, 'marginTop': '15px'}),
        ]),

        # ── TAB 3: ITEM MANAGER ──────────────────────────────────────────────
        dcc.Tab(label='Item Manager', value='items', children=[
            html.Div([
                html.P(
                    "Add new items or correct hierarchy data for existing items. "
                    "These overrides are applied during the Item List enrichment step "
                    "after Item List join, so they take priority over the Excel Item List.",
                    style={'color': '#555', 'marginBottom': '15px', 'fontSize': '13px'}
                ),
                _row(
                    _field("Item Code *", 'item-code', 'e.g. B/GLASS/124/B'),
                    _field("Description / Name *", 'item-name', 'e.g. GLASS 124 BLUE'),
                ),
                _row(
                    _field("Items 2 (Grp)", 'item-items2', 'e.g. Glass - 124'),
                    _field("Items (Sub)", 'item-items', 'e.g. Glass - 124 - Kings'),
                    html.Div([
                        html.Label("B2B / B2C", style=_LABEL),
                        dcc.Dropdown(id='item-b2b',
                                     options=['B2B', 'B2C', 'Unknown'],
                                     value='B2C',
                                     clearable=False,
                                     style={'fontSize': '13px'}),
                    ]),
                ),
                html.Button("Add / Update Item", id='btn-add-item',
                            style=_BTN_GREEN, n_clicks=0),
                html.Div(id='item-msg', style=_MSG_OK),

                html.Hr(style={'margin': '20px 0'}),
                html.H4("Existing Item Overrides",
                        style={'color': s.COLORS['primary'], 'marginBottom': '10px'}),
                html.Button("Refresh", id='btn-refresh-items', style=_BTN_BLUE,
                            n_clicks=0),
                dash_table.DataTable(
                    id='item-table',
                    columns=[
                        {'name': 'Item Code', 'id': 'item_code'},
                        {'name': 'Name',      'id': 'name'},
                        {'name': 'Items 2',   'id': 'items_2'},
                        {'name': 'Items',     'id': 'items'},
                        {'name': 'B2B',       'id': 'b2b'},
                        {'name': 'Added At',  'id': 'added_at'},
                    ],
                    data=[],
                    sort_action='native',
                    filter_action='native',
                    page_size=15,
                    row_deletable=True,
                    style_header=_HDR,
                    style_cell=_CELL,
                    style_table={'overflowX': 'auto', 'marginTop': '10px'},
                    style_data_conditional=[
                        {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}
                    ],
                ),
                html.Div([
                    html.Button("Save Deletions", id='btn-save-item-deletions',
                                style={**s.BTN_STYLE, 'backgroundColor': '#c0392b'},
                                n_clicks=0),
                ], style={'marginTop': '10px'}),
                html.Div(id='item-del-msg', style=_MSG_ERR),
            ], style={**s.SECTION, 'marginTop': '15px'}),
        ]),

        # ── TAB 4: TRANSACTION EDITOR ────────────────────────────────────────
        dcc.Tab(label='Transaction Editor', value='transactions', children=[
            html.Div([
                html.P(
                    "Reassign individual transactions to the rep who actually made the "
                    "sale. Use this when a rep sells to a customer that belongs to another "
                    "rep — attribution is otherwise credited to the customer's owner. "
                    "Search for the lines, select them, choose the correct rep, and Apply. "
                    "Changes take effect on the live dashboard immediately and are "
                    "re-applied automatically on every pipeline run.",
                    style={'color': '#555', 'marginBottom': '15px', 'fontSize': '13px'}
                ),
                _row(
                    _field("Customer contains", 'txn-search-cust', 'e.g. GONGONI'),
                    _field("Invoice No (Group)", 'txn-search-inv', 'e.g. INV12345'),
                    _field("Item Code contains", 'txn-search-item', 'e.g. B/GLASS'),
                ),
                _row(
                    _field("Date from", 'txn-search-from', 'YYYY-MM-DD'),
                    _field("Date to", 'txn-search-to', 'YYYY-MM-DD'),
                    html.Div([
                        html.Label(" ", style=_LABEL),
                        html.Button("Search Transactions", id='btn-txn-search',
                                    style=_BTN_BLUE, n_clicks=0),
                    ], style={'paddingTop': '4px'}),
                ),
                html.Div(id='txn-search-msg', style={'color': '#555', 'fontSize': '13px',
                                                     'marginBottom': '8px'}),

                dash_table.DataTable(
                    id='txn-table',
                    columns=[
                        {'name': 'AutoID',      'id': 'AutoID',         'editable': False},
                        {'name': 'Date',        'id': 'Date',           'editable': False},
                        {'name': 'Invoice',     'id': 'Group',          'editable': False},
                        {'name': 'Account',     'id': 'Account',        'editable': False},
                        {'name': 'Customer',    'id': 'Customer Names',  'editable': False},
                        {'name': 'Item Code',   'id': 'Item Code',      'editable': False},
                        {'name': 'Item',        'id': 'Name',           'editable': False},
                        {'name': 'Value',       'id': 'Value',          'editable': False,
                         'type': 'numeric'},
                        {'name': 'Current Rep', 'id': 'Sales Rep',      'editable': False},
                    ],
                    data=[],
                    row_selectable='multi',
                    selected_rows=[],
                    sort_action='native',
                    filter_action='native',
                    page_size=15,
                    style_header=_HDR,
                    style_cell=_CELL,
                    style_table={'overflowX': 'auto', 'marginTop': '10px'},
                    style_data_conditional=[
                        {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}
                    ],
                ),

                html.Div([
                    html.Div([
                        html.Label("Reassign selected to rep", style=_LABEL),
                        dcc.Dropdown(id='txn-reassign-rep', options=[], value=None,
                                     placeholder='Select rep…',
                                     style={'fontSize': '13px'}),
                    ], style={'flex': '1'}),
                    html.Div([
                        html.Label(" ", style=_LABEL),
                        html.Button("Apply to Selected", id='btn-txn-apply',
                                    style=_BTN_GREEN, n_clicks=0),
                    ], style={'paddingTop': '4px'}),
                ], style={'display': 'flex', 'gap': '15px', 'alignItems': 'flex-start',
                          'marginTop': '15px'}),
                html.Div(id='txn-apply-msg', style=_MSG_OK),

                html.Hr(style={'margin': '20px 0'}),
                html.H4("Existing Transaction Reassignments",
                        style={'color': s.COLORS['primary'], 'marginBottom': '10px'}),
                html.Button("Refresh", id='btn-refresh-txn', style=_BTN_BLUE, n_clicks=0),
                dash_table.DataTable(
                    id='txn-ov-table',
                    columns=[
                        {'name': 'AutoID',        'id': 'autoid'},
                        {'name': 'Date',          'id': 'txn_date'},
                        {'name': 'Invoice',       'id': 'invoice_no'},
                        {'name': 'Customer',      'id': 'customer_name'},
                        {'name': 'Original Rep',  'id': 'orig_rep'},
                        {'name': 'Reassigned To', 'id': 'rep_name'},
                        {'name': 'Updated At',    'id': 'updated_at'},
                    ],
                    data=[],
                    sort_action='native',
                    filter_action='native',
                    page_size=15,
                    row_deletable=True,
                    style_header=_HDR,
                    style_cell=_CELL,
                    style_table={'overflowX': 'auto', 'marginTop': '10px'},
                    style_data_conditional=[
                        {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}
                    ],
                ),
                html.Div([
                    html.Button("Save Deletions (revert to original rep)",
                                id='btn-save-txn-deletions',
                                style={**s.BTN_STYLE, 'backgroundColor': '#c0392b'},
                                n_clicks=0),
                ], style={'marginTop': '10px'}),
                html.Div(id='txn-del-msg', style=_MSG_ERR),

                html.Hr(style={'margin': '20px 0'}),
                html.Div([
                    html.Button("Push Changes & Restart Server",
                                id='btn-txn-push-restart',
                                style={**s.BTN_STYLE, 'backgroundColor': '#8e44ad'},
                                n_clicks=0),
                    html.Span("  Re-applies every reassignment to the data and restarts "
                              "the server (~15s) so all pages reload cleanly with the "
                              "changes. Use this if a page is still showing an old rep.",
                              style={'color': '#888', 'fontSize': '12px', 'marginLeft': '10px'}),
                ], style={'marginTop': '5px'}),
                html.Div(id='txn-restart-msg', style=_MSG_OK),
            ], style={**s.SECTION, 'marginTop': '15px'}),
        ]),
    ]),
], style=s.MAIN_CONTAINER)


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — Price Manager
# ══════════════════════════════════════════════════════════════════════════════

@callback(
    Output('admin-price-table', 'data'),
    Output('price-msg', 'children', allow_duplicate=True),
    Input('btn-load-prices', 'n_clicks'),
    State('price-search', 'value'),
    prevent_initial_call=True,
)
def load_prices(n, search):
    """Load invoice items + merge with existing price_overrides."""
    try:
        with _conn() as conn:
            q = """
                SELECT DISTINCT
                    i."Item/Account"  AS item_code,
                    i."Description"   AS description,
                    i."Old Price"      AS inv_old,
                    i."Master Price (Excl)" AS inv_master,
                    i."% Increase"    AS inv_pct
                FROM invoices i
                WHERE i."Item/Account" IS NOT NULL
            """
            params = []
            if search and search.strip():
                q += ' AND UPPER(i."Item/Account") LIKE ?'
                params.append(f'%{search.strip().upper()}%')
            q += ' ORDER BY i."Item/Account"'
            inv_df = pd.read_sql(q, conn, params=params)

            # Load existing overrides
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if 'price_overrides' in tables:
                ov_df = pd.read_sql(
                    "SELECT item_code, customer_name, master_price, old_price, "
                    "pct_increase, updated_at FROM price_overrides",
                    conn
                )
            else:
                ov_df = pd.DataFrame(columns=[
                    'item_code', 'customer_name', 'master_price',
                    'old_price', 'pct_increase', 'updated_at'
                ])

        # Merge: overrides take precedence over invoice values
        merged = []
        for _, row in inv_df.iterrows():
            ic = str(row['item_code']).strip()
            ov = ov_df[ov_df['item_code'] == ic]
            if not ov.empty:
                for _, ov_row in ov.iterrows():
                    merged.append({
                        'item_code':     ic,
                        'description':   row.get('description', ''),
                        'old_price':     ov_row.get('old_price'),
                        'master_price':  ov_row.get('master_price'),
                        'pct_increase':  ov_row.get('pct_increase'),
                        'customer_name': ov_row.get('customer_name', ''),
                        'updated_at':    ov_row.get('updated_at', ''),
                    })
            else:
                merged.append({
                    'item_code':     ic,
                    'description':   row.get('description', ''),
                    'old_price':     row.get('inv_old'),
                    'master_price':  row.get('inv_master'),
                    'pct_increase':  row.get('inv_pct'),
                    'customer_name': '',
                    'updated_at':    '',
                })

        # Also include any overrides for items NOT in the search result
        if merged:
            found_codes = {r['item_code'] for r in merged}
            extra = ov_df[~ov_df['item_code'].isin(found_codes)]
            for _, ov_row in extra.iterrows():
                merged.append({
                    'item_code':     ov_row['item_code'],
                    'description':   '',
                    'old_price':     ov_row.get('old_price'),
                    'master_price':  ov_row.get('master_price'),
                    'pct_increase':  ov_row.get('pct_increase'),
                    'customer_name': ov_row.get('customer_name', ''),
                    'updated_at':    ov_row.get('updated_at', ''),
                })

        return merged, f"Loaded {len(merged):,} item(s)."
    except Exception as e:
        return [], f"Error: {e}"


@callback(
    Output('price-msg', 'children'),
    Input('btn-save-prices', 'n_clicks'),
    State('admin-price-table', 'data'),
    prevent_initial_call=True,
)
def save_prices(n, rows):
    if not rows:
        return "Nothing to save."
    now = datetime.datetime.now().isoformat(timespec='seconds')
    saved = 0
    errors = []
    try:
        with _conn() as conn:
            for r in rows:
                ic = str(r.get('item_code', '')).strip()
                if not ic:
                    continue
                cust = str(r.get('customer_name', '') or '').strip()
                try:
                    conn.execute("""
                        INSERT INTO price_overrides
                            (item_code, customer_name, master_price, old_price, pct_increase, updated_at)
                        VALUES (?,?,?,?,?,?)
                        ON CONFLICT(item_code, customer_name) DO UPDATE SET
                            master_price = excluded.master_price,
                            old_price    = excluded.old_price,
                            pct_increase = excluded.pct_increase,
                            updated_at   = excluded.updated_at
                    """, (ic, cust,
                          r.get('master_price'), r.get('old_price'),
                          r.get('pct_increase'), now))
                    saved += 1
                except Exception as e:
                    errors.append(f"{ic}: {e}")
            conn.commit()
    except Exception as e:
        return f"Save failed: {e}"
    msg = f"Saved {saved:,} price override(s)."
    if errors:
        msg += f"  Errors: {'; '.join(errors[:3])}"
    return msg


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — Customer Manager
# ══════════════════════════════════════════════════════════════════════════════

@callback(
    Output('cust-msg', 'children'),
    Output('cust-table', 'data', allow_duplicate=True),
    Input('btn-add-customer', 'n_clicks'),
    State('cust-account', 'value'),
    State('cust-name', 'value'),
    State('cust-rep', 'value'),
    State('cust-area', 'value'),
    State('cust-b2b', 'value'),
    prevent_initial_call=True,
)
def add_customer(n, account, name, rep, area, b2b):
    account = (account or '').strip()
    name = (name or '').strip()
    if not account or not name:
        return "Account Code and Customer Name are required.", []
    now = datetime.datetime.now().isoformat(timespec='seconds')
    try:
        with _conn() as conn:
            conn.execute("""
                INSERT INTO customer_overrides
                    (account, customer_name, rep_name, customer_area, b2b, added_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(account) DO UPDATE SET
                    customer_name = excluded.customer_name,
                    rep_name      = excluded.rep_name,
                    customer_area = excluded.customer_area,
                    b2b           = excluded.b2b,
                    added_at      = excluded.added_at
            """, (account, name, rep or '', area or '', b2b or '', now))
            conn.commit()
            rows = pd.read_sql("SELECT * FROM customer_overrides ORDER BY added_at DESC", conn)
        return f"Customer '{name}' saved.", rows.to_dict('records')
    except Exception as e:
        return f"Error: {e}", []


@callback(
    Output('cust-table', 'data'),
    Input('btn-refresh-customers', 'n_clicks'),
    prevent_initial_call=True,
)
def refresh_customers(n):
    try:
        with _conn() as conn:
            df = pd.read_sql("SELECT * FROM customer_overrides ORDER BY added_at DESC", conn)
        return df.to_dict('records')
    except Exception:
        return []


@callback(
    Output('cust-del-msg', 'children'),
    Input('btn-save-cust-deletions', 'n_clicks'),
    State('cust-table', 'data'),
    prevent_initial_call=True,
)
def save_cust_deletions(n, remaining_rows):
    if remaining_rows is None:
        return ""
    kept = {str(r.get('account', '')).strip() for r in remaining_rows if r.get('account')}
    try:
        with _conn() as conn:
            all_accs = [r[0] for r in conn.execute(
                "SELECT account FROM customer_overrides"
            ).fetchall()]
            to_del = [a for a in all_accs if a not in kept]
            if to_del:
                placeholders = ','.join('?' * len(to_del))
                conn.execute(
                    f"DELETE FROM customer_overrides WHERE account IN ({placeholders})",
                    to_del
                )
                conn.commit()
                return f"Deleted {len(to_del)} customer(s)."
        return "No deletions needed."
    except Exception as e:
        return f"Error: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — Item Manager
# ══════════════════════════════════════════════════════════════════════════════

@callback(
    Output('item-msg', 'children'),
    Output('item-table', 'data', allow_duplicate=True),
    Input('btn-add-item', 'n_clicks'),
    State('item-code', 'value'),
    State('item-name', 'value'),
    State('item-items2', 'value'),
    State('item-items', 'value'),
    State('item-b2b', 'value'),
    prevent_initial_call=True,
)
def add_item(n, code, name, items2, items, b2b):
    code = (code or '').strip()
    name = (name or '').strip()
    if not code or not name:
        return "Item Code and Name are required.", []
    now = datetime.datetime.now().isoformat(timespec='seconds')
    try:
        with _conn() as conn:
            conn.execute("""
                INSERT INTO item_overrides
                    (item_code, name, items_2, items, b2b, added_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(item_code) DO UPDATE SET
                    name     = excluded.name,
                    items_2  = excluded.items_2,
                    items    = excluded.items,
                    b2b      = excluded.b2b,
                    added_at = excluded.added_at
            """, (code, name, items2 or '', items or '', b2b or '', now))
            conn.commit()
            rows = pd.read_sql("SELECT * FROM item_overrides ORDER BY added_at DESC", conn)
        return f"Item '{code}' saved.", rows.to_dict('records')
    except Exception as e:
        return f"Error: {e}", []


@callback(
    Output('item-table', 'data'),
    Input('btn-refresh-items', 'n_clicks'),
    prevent_initial_call=True,
)
def refresh_items(n):
    try:
        with _conn() as conn:
            df = pd.read_sql("SELECT * FROM item_overrides ORDER BY added_at DESC", conn)
        return df.to_dict('records')
    except Exception:
        return []


@callback(
    Output('item-del-msg', 'children'),
    Input('btn-save-item-deletions', 'n_clicks'),
    State('item-table', 'data'),
    prevent_initial_call=True,
)
def save_item_deletions(n, remaining_rows):
    if remaining_rows is None:
        return ""
    kept = {str(r.get('item_code', '')).strip() for r in remaining_rows if r.get('item_code')}
    try:
        with _conn() as conn:
            all_codes = [r[0] for r in conn.execute(
                "SELECT item_code FROM item_overrides"
            ).fetchall()]
            to_del = [c for c in all_codes if c not in kept]
            if to_del:
                placeholders = ','.join('?' * len(to_del))
                conn.execute(
                    f"DELETE FROM item_overrides WHERE item_code IN ({placeholders})",
                    to_del
                )
                conn.commit()
                return f"Deleted {len(to_del)} item(s)."
        return "No deletions needed."
    except Exception as e:
        return f"Error: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — Transaction Editor (per-transaction rep reassignment)
# ══════════════════════════════════════════════════════════════════════════════

_TXN_COLS = ['AutoID', 'Date', 'Group', 'Account', 'Customer Names',
             'Item Code', 'Name', 'Value', 'Sales Rep']
# yyyymmdd sort/compare key derived from a dd/mm/yyyy Date string
_DATE_KEY = '(substr("Date",7,4)||substr("Date",4,2)||substr("Date",1,2))'
_TXN_RESULT_CAP = 2000


@callback(
    Output('txn-table', 'data'),
    Output('txn-table', 'selected_rows'),
    Output('txn-reassign-rep', 'options'),
    Output('txn-search-msg', 'children'),
    Input('btn-txn-search', 'n_clicks'),
    State('txn-search-cust', 'value'),
    State('txn-search-inv', 'value'),
    State('txn-search-item', 'value'),
    State('txn-search-from', 'value'),
    State('txn-search-to', 'value'),
    prevent_initial_call=True,
)
def search_transactions(n, cust, inv, item, dfrom, dto):
    cust = (cust or '').strip()
    inv = (inv or '').strip()
    item = (item or '').strip()
    dfrom = (dfrom or '').strip()
    dto = (dto or '').strip()

    if not any([cust, inv, item, dfrom, dto]):
        return [], [], [], "Enter at least one search filter (customer, invoice, item, or date range)."

    where, params = [], []
    if cust:
        where.append('UPPER("Customer Names") LIKE ?'); params.append(f'%{cust.upper()}%')
    if inv:
        where.append('UPPER("Group") LIKE ?'); params.append(f'%{inv.upper()}%')
    if item:
        where.append('UPPER("Item Code") LIKE ?'); params.append(f'%{item.upper()}%')
    if dfrom:
        where.append(f'{_DATE_KEY} >= ?'); params.append(dfrom.replace('-', ''))
    if dto:
        where.append(f'{_DATE_KEY} <= ?'); params.append(dto.replace('-', ''))

    col_list = ', '.join(f'"{c}"' for c in _TXN_COLS)
    q = (f'SELECT {col_list} FROM transactions_final WHERE '
         + ' AND '.join(where)
         + f' ORDER BY {_DATE_KEY} DESC LIMIT {_TXN_RESULT_CAP + 1}')
    try:
        with _conn() as conn:
            rows = pd.read_sql(q, conn, params=params)
            reps = pd.read_sql(
                'SELECT DISTINCT "Sales Rep" AS r FROM transactions_final '
                'WHERE "Sales Rep" IS NOT NULL AND TRIM("Sales Rep") <> "" '
                'ORDER BY r', conn
            )
    except Exception as e:
        return [], [], [], f"Error: {e}"

    rep_opts = [{'label': r, 'value': r} for r in reps['r'].astype(str).tolist()]
    truncated = len(rows) > _TXN_RESULT_CAP
    rows = rows.iloc[:_TXN_RESULT_CAP]
    msg = f"Found {len(rows):,} line(s)."
    if truncated:
        msg += f" Showing first {_TXN_RESULT_CAP:,} — narrow your search to see more."
    return rows.to_dict('records'), [], rep_opts, msg


@callback(
    Output('txn-apply-msg', 'children'),
    Output('txn-table', 'data', allow_duplicate=True),
    Output('txn-ov-table', 'data', allow_duplicate=True),
    Input('btn-txn-apply', 'n_clicks'),
    State('txn-table', 'data'),
    State('txn-table', 'selected_rows'),
    State('txn-reassign-rep', 'value'),
    prevent_initial_call=True,
)
def apply_reassignment(n, rows, selected, new_rep):
    new_rep = (new_rep or '').strip()
    if not selected:
        return "Select one or more transactions first.", (rows or []), _load_txn_overrides()
    if not new_rep:
        return "Choose a rep to reassign to.", (rows or []), _load_txn_overrides()

    now = datetime.datetime.now().isoformat(timespec='seconds')
    applied, skipped = 0, 0
    applied_ids = []
    try:
        with _conn() as conn:
            for idx in selected:
                if idx < 0 or idx >= len(rows):
                    continue
                r = rows[idx]
                autoid = str(r.get('AutoID', '')).strip()
                if not autoid:
                    continue
                cur_rep = str(r.get('Sales Rep', '') or '').strip()
                if cur_rep == new_rep:
                    skipped += 1
                    continue
                conn.execute("""
                    INSERT INTO transaction_overrides
                        (autoid, rep_name, orig_rep, customer_name, invoice_no, txn_date, updated_at)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(autoid) DO UPDATE SET
                        rep_name   = excluded.rep_name,
                        updated_at = excluded.updated_at
                """, (autoid, new_rep, cur_rep,
                      str(r.get('Customer Names', '') or ''),
                      str(r.get('Group', '') or ''),
                      str(r.get('Date', '') or ''), now))
                # Apply to the served table so the dashboard reflects it immediately.
                conn.execute(
                    'UPDATE transactions_final SET "Rep Name" = ?, "Sales Rep" = ? '
                    'WHERE "AutoID" = ?', (new_rep, new_rep, autoid)
                )
                r['Sales Rep'] = new_rep
                applied += 1
                applied_ids.append(autoid)
            conn.commit()
            verified = 0
            if applied:
                # Flush WAL into the main DB so every reader process/machine sees
                # the change and the file mtime bumps (forces the RAM cache reload).
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                # Ground-truth check: how many served rows now actually carry the
                # new rep. If this is 0, the write isn't matching the data (not a
                # caching problem); if it matches `applied`, the data is updated.
                ph = ','.join('?' * len(applied_ids))
                verified = conn.execute(
                    f'SELECT COUNT(*) FROM transactions_final '
                    f'WHERE "AutoID" IN ({ph}) AND "Sales Rep" = ?',
                    applied_ids + [new_rep]
                ).fetchone()[0]
    except Exception as e:
        return f"Error: {e}", (rows or []), _load_txn_overrides()

    msg = (f"Reassigned {applied:,} transaction(s) to '{new_rep}'. "
           f"Verified {verified:,}/{applied:,} row(s) updated in the live data.")
    if skipped:
        msg += f" ({skipped:,} already on that rep, skipped.)"
    if applied and verified == 0:
        msg += (" ⚠ The data rows did not change — the AutoID may not match the served "
                "table. Tell your admin/developer so the key format can be checked.")
    else:
        msg += " Reload the dashboard (Ctrl+F5) to see updated attribution."
    return msg, rows, _load_txn_overrides()


def _load_txn_overrides():
    try:
        with _conn() as conn:
            df = pd.read_sql(
                "SELECT autoid, txn_date, invoice_no, customer_name, orig_rep, "
                "rep_name, updated_at FROM transaction_overrides ORDER BY updated_at DESC",
                conn
            )
        return df.to_dict('records')
    except Exception:
        return []


@callback(
    Output('txn-ov-table', 'data'),
    Input('btn-refresh-txn', 'n_clicks'),
    Input('admin-tabs', 'value'),
)
def refresh_txn_overrides(n, tab):
    # Fires on page load and whenever the tab changes, so the reassignments
    # list is populated automatically (not only after clicking Refresh).
    return _load_txn_overrides()


@callback(
    Output('txn-restart-msg', 'children'),
    Input('btn-txn-push-restart', 'n_clicks'),
    prevent_initial_call=True,
)
def push_and_restart(n):
    """Re-apply all reassignments to transactions_final, then restart the server
    so every page reloads with a fresh cache (mirrors the upload-tab behaviour)."""
    try:
        with _conn() as conn:
            ov = conn.execute(
                "SELECT autoid, rep_name FROM transaction_overrides "
                "WHERE rep_name IS NOT NULL AND TRIM(rep_name) <> ''"
            ).fetchall()
            for autoid, rep in ov:
                conn.execute(
                    'UPDATE transactions_final SET "Rep Name" = ?, "Sales Rep" = ? '
                    'WHERE "AutoID" = ?', (rep, rep, str(autoid))
                )
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except Exception as e:
        return f"Error pushing changes: {e}"

    _exe, _argv = sys.executable, sys.argv[:]

    def _restart():
        time.sleep(1.5)
        os.execv(_exe, [_exe] + _argv)

    threading.Thread(target=_restart, daemon=True).start()
    return (f"Pushed {len(ov):,} reassignment(s) to the data. Server is restarting — "
            "wait ~15 seconds, then refresh your browser.")


@callback(
    Output('txn-del-msg', 'children'),
    Output('txn-ov-table', 'data', allow_duplicate=True),
    Input('btn-save-txn-deletions', 'n_clicks'),
    State('txn-ov-table', 'data'),
    prevent_initial_call=True,
)
def save_txn_deletions(n, remaining_rows):
    if remaining_rows is None:
        return "", _load_txn_overrides()
    kept = {str(r.get('autoid', '')).strip() for r in remaining_rows if r.get('autoid')}
    try:
        with _conn() as conn:
            all_ov = conn.execute(
                "SELECT autoid, orig_rep FROM transaction_overrides"
            ).fetchall()
            to_del = [(a, orig) for (a, orig) in all_ov if str(a).strip() not in kept]
            if not to_del:
                return "No deletions needed.", _load_txn_overrides()
            for autoid, orig in to_del:
                # Revert the served table back to the original (customer-derived) rep.
                conn.execute(
                    'UPDATE transactions_final SET "Rep Name" = ?, "Sales Rep" = ? '
                    'WHERE "AutoID" = ?', (orig or '', orig or '', str(autoid))
                )
                conn.execute(
                    "DELETE FROM transaction_overrides WHERE autoid = ?", (str(autoid),)
                )
            conn.commit()
            # Flush WAL into the main DB so all reader processes see the revert.
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return (f"Reverted and removed {len(to_del)} reassignment(s). "
                "Reload the dashboard to see the change."), _load_txn_overrides()
    except Exception as e:
        return f"Error: {e}", _load_txn_overrides()
