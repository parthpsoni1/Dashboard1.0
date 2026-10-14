import sys
import os
import pandas as pd
import numpy as np

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import dash
from dash import html, dcc, dash_table, Input, Output, State, callback, ctx
from dash.dash_table.Format import Format, Scheme, Group

import sqlite3
import Assets.styles as s
from data_loader import df_global, get_latest_data

# ── Constants ────────────────────────────────────────────────────────────────
INVOICE_FILE_PATH = os.path.join(root_dir, 'Raws', 'invoices_final.csv')
_DB_PATH = os.path.join(root_dir, 'data', 'dashboard.db')
TOLERANCE_THRESHOLD = 0.02   # ±2 % triggers a flag

# ── Shared table styles ───────────────────────────────────────────────────────
_HDR_DARK  = {'backgroundColor': '#34495e', 'color': 'white',   'fontWeight': 'bold', 'textAlign': 'center'}
_HDR_MID   = {'backgroundColor': '#2c3e50', 'color': 'white',   'fontWeight': 'bold', 'textAlign': 'center'}
_HDR_LIGHT = {'backgroundColor': '#ecf0f1', 'color': '#2c3e50', 'fontWeight': 'bold', 'textAlign': 'center', 'fontSize': '12px'}
_CELL      = {'textAlign': 'left', 'padding': '10px', 'fontFamily': 'Segoe UI', 'fontSize': '13px'}
_CELL_SM   = {'textAlign': 'left', 'padding': '8px',  'fontFamily': 'Segoe UI', 'fontSize': '12px'}

_STATUS_COND = [
    {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'},
    {'if': {'filter_query': '{Status} = "Undercharged"'},            'backgroundColor': '#fadbd8', 'color': '#c0392b', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Status} = "Overcharged"'},             'backgroundColor': '#d5f5e3', 'color': '#27ae60', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Status} = "Match/Tolerance"'},         'backgroundColor': '#fcf3cf', 'color': '#d35400', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Invoice_Status} = "Undercharged"'},    'backgroundColor': '#fadbd8', 'color': '#c0392b', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Invoice_Status} = "Overcharged"'},     'backgroundColor': '#d5f5e3', 'color': '#27ae60', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Invoice_Status} = "Match/Tolerance"'}, 'backgroundColor': '#fcf3cf', 'color': '#d35400', 'fontWeight': 'bold'},
]

_LINE_COND = [
    {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'},
    {'if': {'filter_query': '{Pct_Diff} < -0.02', 'column_id': 'Variance'},  'color': '#e74c3c', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Pct_Diff} < -0.02', 'column_id': 'Pct_Diff'},  'color': '#e74c3c', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Pct_Diff} > 0.02',  'column_id': 'Variance'},  'color': '#27ae60', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Pct_Diff} > 0.02',  'column_id': 'Pct_Diff'},  'color': '#27ae60', 'fontWeight': 'bold'},
    {'if': {'filter_query': '{Pct_Diff} >= -0.02 && {Pct_Diff} <= 0.02', 'column_id': 'Variance'}, 'color': '#2c3e50'},
    {'if': {'filter_query': '{Pct_Diff} >= -0.02 && {Pct_Diff} <= 0.02', 'column_id': 'Pct_Diff'}, 'color': '#2c3e50'},
]

_HOVER_CSS = [{"selector": "tr:hover", "rule": "background-color: #d6eaf8 !important;"}]


# ── Layout ───────────────────────────────────────────────────────────────────
layout = html.Div([
    dcc.Store(id='invoice-data-store'),
    dcc.Store(id='selected-so-store'),

    # ── Header / filters ────────────────────────────────────────────────────
    html.Div([
        html.H1("Invoice Pricing Reconciliation", style=s.HEADER_TITLE),

        html.Div([
            html.Div([
                html.Span("SEGMENT:", style={'fontWeight': 'bold', 'fontSize': '11px',
                                             'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.RadioItems(id='audit-segment', options=['All', 'B2B', 'B2C'],
                               value='All', inline=True,
                               style={'marginRight': '20px', 'display': 'inline-block',
                                      'fontSize': '14px'}),
            ], style={'display': 'inline-block'}),

            html.Span("DATE RANGE:", style={'fontWeight': 'bold', 'fontSize': '11px',
                                            'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.DatePickerRange(id='audit-date-picker', display_format='DD MMM YYYY',
                                clearable=True,
                                style={'display': 'inline-block', 'marginRight': '20px'}),

            html.Button("🔄 LOAD INVOICES", id='btn-run-audit', style=s.BTN_STYLE),
        ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap',
                  'justifyContent': 'center', 'backgroundColor': '#f8f9fa',
                  'padding': '15px', 'borderRadius': '8px', 'marginBottom': '20px',
                  'border': f'1px solid {s.COLORS.get("border", "#e0e0e0")}'}),

        html.Div(id='audit-error-message',
                 style={'textAlign': 'center', 'color': '#e74c3c', 'fontWeight': 'bold'}),
    ], style=s.SECTION),

    # ── KPI cards ───────────────────────────────────────────────────────────
    html.Div(id='audit-kpis',
             style={'display': 'flex', 'justifyContent': 'space-between', 'marginBottom': '20px'}),

    # ── LEVEL 1 — Sales Order summary ───────────────────────────────────────
    html.Div([
        html.H3("Sales Order Summary  (click a row to view invoices)",
                style={'textAlign': 'center', 'color': s.COLORS['primary'],
                       'marginBottom': '15px', 'fontSize': '16px'}),
        dash_table.DataTable(
            id='so-summary-table',
            data=[], columns=[],
            filter_action='native', sort_action='native',
            page_size=15,
            style_table={'overflowX': 'auto', 'cursor': 'pointer'},
            style_header=_HDR_DARK,
            style_cell=_CELL,
            style_data_conditional=_STATUS_COND,
            style_data={'transition': 'background-color 0.2s ease'},
            css=_HOVER_CSS,
        ),
    ], style=s.SECTION),

    # ── LEVEL 2 — Invoice list for selected SO ───────────────────────────────
    html.Div(id='invoice-section', style={'display': 'none'}, children=[
        html.Div([
            html.H3(id='invoice-section-title',
                    style={'textAlign': 'center', 'color': '#2980b9',
                           'marginBottom': '15px', 'fontSize': '15px'}),
            dash_table.DataTable(
                id='invoice-sub-table',
                data=[], columns=[],
                filter_action='native', sort_action='native',
                page_size=10,
                style_table={'overflowX': 'auto', 'cursor': 'pointer'},
                style_header=_HDR_MID,
                style_cell=_CELL,
                style_data_conditional=_STATUS_COND,
                style_data={'transition': 'background-color 0.2s ease'},
                css=_HOVER_CSS,
            ),
        ], style={**s.SECTION, 'borderTop': '3px solid #2980b9'}),
    ]),

    # ── LEVEL 3 — Line items for selected invoice ────────────────────────────
    html.Div(id='detail-section', style={'display': 'none'}, children=[
        html.Div([
            html.H3(id='detail-title',
                    style={'textAlign': 'center', 'color': '#e67e22', 'marginBottom': '15px'}),
            html.Div(id='detail-table-container'),
        ], style={**s.SECTION, 'borderTop': '3px solid #e67e22'}),
    ]),

], style=s.MAIN_CONTAINER)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _kpi(title, val, color, is_currency=False):
    val_str = f"KES {val:,.0f}" if is_currency else f"{val:,.0f}"
    return html.Div([
        html.Div(style={'height': '5px', 'backgroundColor': color,
                        'borderTopLeftRadius': '8px', 'borderTopRightRadius': '8px'}),
        html.Div([
            html.H4(title, style={'color': '#95a5a6', 'fontSize': '12px', 'fontWeight': 'bold',
                                  'textAlign': 'center', 'margin': '15px 0 5px 0'}),
            html.H2(val_str, style={'margin': '0 0 15px 0', 'color': '#2c3e50',
                                    'fontSize': '24px', 'textAlign': 'center'}),
        ], style={'padding': '10px'}),
    ], style={'backgroundColor': 'white', 'borderRadius': '8px', 'flex': '1',
              'margin': '0 10px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
              'border': '1px solid #eee'})


def _inv_status(pct):
    if pct < -TOLERANCE_THRESHOLD: return 'Undercharged'
    if pct >  TOLERANCE_THRESHOLD: return 'Overcharged'
    return 'Match/Tolerance'


def _prepare_data(n_clicks, start_date, end_date, segment):
    """Load + filter invoices_final.csv; return enriched DataFrame or (None, err_msg)."""
    if not n_clicks:
        return None, ""
    if not os.path.exists(INVOICE_FILE_PATH):
        return None, f"Error: {INVOICE_FILE_PATH} not found."

    try:
        inv_df = pd.read_csv(INVOICE_FILE_PATH, sep=None, engine='python', encoding='utf-8-sig')
        inv_df.columns = [str(c).strip() for c in inv_df.columns]
    except Exception as e:
        return None, f"Fatal Error reading CSV: {e}"

    if inv_df.empty:
        return None, "Error: CSV is empty."

    # Date parse + filter
    if 'Date' in inv_df.columns:
        inv_df['Date'] = pd.to_datetime(inv_df['Date'], dayfirst=True, errors='coerce')
        if start_date:
            inv_df = inv_df[inv_df['Date'] >= pd.to_datetime(start_date)]
        if end_date:
            inv_df = inv_df[inv_df['Date'] <= pd.to_datetime(end_date)]

    # Segment filter
    if segment != 'All':
        if 'B2B' not in inv_df.columns:
            _df_live, _, _, _ = get_latest_data()
            if (_df_live is not None and not _df_live.empty
                    and 'Customer Names' in _df_live.columns
                    and 'B2B' in _df_live.columns):
                cmap = (_df_live.dropna(subset=['Customer Names', 'B2B'])
                        .drop_duplicates('Customer Names')
                        .assign(k=lambda d: d['Customer Names'].str.strip().str.upper())
                        .set_index('k')['B2B'].to_dict())
                inv_df['B2B'] = (inv_df['Customer Name']
                                 .astype(str).str.strip().str.upper()
                                 .map(cmap).fillna('Unknown'))
            else:
                inv_df['B2B'] = 'Unknown'
        inv_df = inv_df[inv_df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    if inv_df.empty:
        return None, "No invoices found for the selected filters."

    # Numeric columns
    for col in ['Order QTY', 'Unit Price Excl', 'Master Price (Excl)', 'Difference', '% Diff.', 'Old Price']:
        inv_df[col] = pd.to_numeric(inv_df.get(col, pd.Series(dtype=float)), errors='coerce').fillna(0)

    inv_df['Variance']     = inv_df['Unit Price Excl'] - inv_df['Master Price (Excl)']
    inv_df['Total_Billed'] = inv_df['Unit Price Excl'] * inv_df['Order QTY']
    inv_df['Total_Impact'] = inv_df['Variance']        * inv_df['Order QTY']
    inv_df['Pct_Diff']     = np.where(
        inv_df['Unit Price Excl'] > 0,
        inv_df['Variance'] / inv_df['Unit Price Excl'], 0
    )
    inv_df['Line_Status'] = np.select(
        [inv_df['Pct_Diff'] < -TOLERANCE_THRESHOLD,
         inv_df['Pct_Diff'] >  TOLERANCE_THRESHOLD],
        ['Undercharged', 'Overcharged'], default='Match/Tolerance'
    )

    # SO key: use Sales_Order_No when populated, else Invoice No as fallback
    if 'Sales_Order_No' not in inv_df.columns:
        inv_df['Sales_Order_No'] = ''
    inv_df['_so_key'] = inv_df['Sales_Order_No'].where(
        inv_df['Sales_Order_No'].fillna('').astype(str).str.strip() != '',
        inv_df['Order No.']
    )

    # Enrich Description with proper item name from products_ref.
    # The invoices CSV often has hierarchy-level names (e.g. "Glass - 126 - Decorated")
    # in Description instead of the actual SKU description.  products_ref.Name holds
    # the full description (e.g. "GLASS NO 126 - DECORATED PARTY GLASS - BUNDLE").
    if 'Item/Account' in inv_df.columns:
        try:
            with sqlite3.connect(_DB_PATH) as _dbc:
                prod_ref = pd.read_sql(
                    'SELECT "Item Code" AS "Item/Account", Name AS _prod_name FROM products_ref',
                    _dbc
                )
            prod_ref['Item/Account'] = prod_ref['Item/Account'].astype(str).str.strip()
            inv_df = inv_df.merge(prod_ref, on='Item/Account', how='left')
            # Use the DB name when it exists; keep original Description as fallback
            inv_df['Display_Name'] = np.where(
                inv_df['_prod_name'].notna() & (inv_df['_prod_name'].astype(str).str.strip() != ''),
                inv_df['_prod_name'],
                inv_df.get('Description', inv_df['Item/Account'])
            )
            inv_df = inv_df.drop(columns=['_prod_name'], errors='ignore')
        except Exception:
            inv_df['Display_Name'] = inv_df.get('Description', inv_df['Item/Account'])
    else:
        inv_df['Display_Name'] = inv_df.get('Description', '')

    # Format dates for display after filtering
    if 'Date' in inv_df.columns:
        inv_df['Date'] = inv_df['Date'].dt.strftime('%d-%b-%Y').fillna('')

    return inv_df, ""


# ══════════════════════════════════════════════════════════════════════════════
# Callback 1 — Load button → KPIs + Level-1 SO Summary
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('audit-kpis',          'children'),
    Output('so-summary-table',    'data'),
    Output('so-summary-table',    'columns'),
    Output('so-summary-table',    'active_cell'),
    Output('invoice-data-store',  'data'),
    Output('selected-so-store',   'data'),
    Output('invoice-section',     'style'),
    Output('detail-section',      'style'),
    Output('audit-error-message', 'children'),
    Input('btn-run-audit', 'n_clicks'),
    State('audit-date-picker', 'start_date'),
    State('audit-date-picker', 'end_date'),
    State('audit-segment',     'value'),
)
def run_invoice_reconciliation(n_clicks, start_date, end_date, segment):
    _HIDDEN = {'display': 'none'}
    if not n_clicks:
        return [], [], [], None, None, None, _HIDDEN, _HIDDEN, ""

    inv_df, err = _prepare_data(n_clicks, start_date, end_date, segment)
    if inv_df is None:
        return [], [], [], None, None, None, _HIDDEN, _HIDDEN, err

    # Build SO-level summary
    so_df = (
        inv_df.groupby('_so_key').agg(
            SO_No        =('Sales_Order_No', 'first'),
            Customer_Name=('Customer Name',  'first'),
            Num_Invoices =('Order No.',      'nunique'),
            Items_Flagged=('Description',    'count'),
            Net_Variance =('Total_Impact',   'sum'),
            Total_Billed =('Total_Billed',   'sum'),
        ).reset_index()
    )
    so_df.rename(columns={'_so_key': 'SO_Key'}, inplace=True)
    so_df['SO_No'] = so_df['SO_No'].where(
        so_df['SO_No'].fillna('').str.strip() != '', so_df['SO_Key']
    )
    so_df['SO_Pct'] = np.where(so_df['Total_Billed'] > 0,
                                so_df['Net_Variance'] / so_df['Total_Billed'], 0)
    so_df['Status'] = so_df['SO_Pct'].apply(_inv_status)
    so_df = so_df.sort_values('Net_Variance')

    # KPI cards
    leakage = so_df.loc[so_df['Status'] == 'Undercharged', 'Net_Variance'].sum()
    kpis = [
        _kpi("Total SOs Scanned",     len(so_df),                                        '#3498db'),
        _kpi("Undercharged SOs",      (so_df['Status'] == 'Undercharged').sum(),         '#e74c3c'),
        _kpi("Overcharged SOs",       (so_df['Status'] == 'Overcharged').sum(),          '#27ae60'),
        _kpi("Match / Tolerance SOs", (so_df['Status'] == 'Match/Tolerance').sum(),      '#f39c12'),
        _kpi("Revenue Leakage (KES)", leakage,                                           '#c0392b', True),
    ]

    cols = [
        {'name': 'Sales Order',        'id': 'SO_No'},
        {'name': 'Customer',           'id': 'Customer_Name'},
        {'name': '# Invoices',         'id': 'Num_Invoices',  'type': 'numeric'},
        {'name': 'Items Flagged',      'id': 'Items_Flagged', 'type': 'numeric'},
        {'name': 'Net Variance (KES)', 'id': 'Net_Variance',  'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
        {'name': 'Status',             'id': 'Status'},
    ]

    records = so_df.to_dict('records')
    for r in records:
        r['id'] = r['SO_Key']   # row_id used for drill-down click

    return (kpis, records, cols, None,
            inv_df.to_dict('records'), None,
            _HIDDEN, _HIDDEN, "")


# ══════════════════════════════════════════════════════════════════════════════
# Callback 2 — SO row clicked → Level-2 Invoice list
# NOTE: allow_duplicate=True required on outputs shared with Callback 1
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('invoice-section',       'style',    allow_duplicate=True),
    Output('invoice-section-title', 'children'),
    Output('invoice-sub-table',     'data'),
    Output('invoice-sub-table',     'columns'),
    Output('invoice-sub-table',     'active_cell'),
    Output('selected-so-store',     'data',     allow_duplicate=True),
    Output('detail-section',        'style',    allow_duplicate=True),
    Input('so-summary-table', 'active_cell'),
    State('invoice-data-store', 'data'),
    prevent_initial_call=True,
)
def drill_down_so(active_cell, store_data):
    _HIDDEN = {'display': 'none'}
    _SHOW   = {'display': 'block'}

    if not active_cell or not store_data or 'row_id' not in active_cell:
        return _HIDDEN, "", [], [], None, None, _HIDDEN

    selected_so = active_cell['row_id']
    inv_df  = pd.DataFrame(store_data)
    so_rows = inv_df[inv_df['_so_key'] == selected_so].copy()
    if so_rows.empty:
        return _HIDDEN, "", [], [], None, None, _HIDDEN

    customer = so_rows['Customer Name'].iloc[0]
    so_label = so_rows['Sales_Order_No'].iloc[0] if 'Sales_Order_No' in so_rows.columns else ''
    if not str(so_label).strip():
        so_label = selected_so

    for col in ['Total_Billed', 'Total_Impact', 'Pct_Diff']:
        if col not in so_rows.columns:
            so_rows[col] = 0

    inv_summary = (
        so_rows.groupby('Order No.').agg(
            Customer_Name =('Customer Name', 'first'),
            Date          =('Date',          'first'),
            Items_Flagged =('Description',   'count'),
            Net_Variance  =('Total_Impact',  'sum'),
            Total_Billed  =('Total_Billed',  'sum'),
        ).reset_index()
    )
    inv_summary['Inv_Pct']       = np.where(
        inv_summary['Total_Billed'] > 0,
        inv_summary['Net_Variance'] / inv_summary['Total_Billed'], 0
    )
    inv_summary['Invoice_Status'] = inv_summary['Inv_Pct'].apply(_inv_status)
    inv_summary = inv_summary.sort_values('Net_Variance')

    cols = [
        {'name': 'Invoice No',         'id': 'Order No.'},
        {'name': 'Date',               'id': 'Date'},
        {'name': 'Items Flagged',      'id': 'Items_Flagged', 'type': 'numeric'},
        {'name': 'Net Variance (KES)', 'id': 'Net_Variance',  'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
        {'name': 'Status',             'id': 'Invoice_Status'},
    ]

    records = inv_summary.to_dict('records')
    for r in records:
        r['id'] = r['Order No.']

    title = f"Invoices under  {so_label}  —  {customer}"
    return _SHOW, title, records, cols, None, selected_so, _HIDDEN


# ══════════════════════════════════════════════════════════════════════════════
# Callback 3 — Invoice row clicked → Level-3 Line items
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('detail-section',         'style',    allow_duplicate=True),
    Output('detail-title',           'children'),
    Output('detail-table-container', 'children'),
    Input('invoice-sub-table', 'active_cell'),
    State('invoice-data-store', 'data'),
    State('selected-so-store',  'data'),
    prevent_initial_call=True,
)
def drill_down_invoice(active_cell, store_data, selected_so):
    _HIDDEN = {'display': 'none'}
    _SHOW   = {'display': 'block', 'marginTop': '10px'}

    if not active_cell or not store_data or 'row_id' not in active_cell:
        return _HIDDEN, "", html.Div()

    selected_invoice = active_cell['row_id']
    inv_df    = pd.DataFrame(store_data)
    detail_df = inv_df[inv_df['Order No.'] == selected_invoice].copy()

    if detail_df.empty:
        return _HIDDEN, "", html.Div()

    customer = detail_df['Customer Name'].iloc[0]
    so_label = (detail_df['Sales_Order_No'].iloc[0]
                if 'Sales_Order_No' in detail_df.columns else '')

    detail_df['Pct_Diff'] = pd.to_numeric(
        detail_df.get('Pct_Diff', 0), errors='coerce'
    ).fillna(0)

    columns = [
        {'name': 'Date',                'id': 'Date'},
        {'name': 'Item Code',           'id': 'Item/Account'},
        {'name': 'Item',                'id': 'Display_Name'},
        {'name': 'Qty',                 'id': 'Order QTY',           'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Old Price (O.P)',     'id': 'Old Price',           'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
        {'name': 'Master Price (N.P)',  'id': 'Master Price (Excl)', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
        {'name': 'Invoice Price (I.P)', 'id': 'Unit Price Excl',     'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
        {'name': 'Variance',            'id': 'Variance',            'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
        {'name': '% Diff',              'id': 'Pct_Diff',            'type': 'numeric',
         'format': Format(scheme=Scheme.percentage, precision=1)},
    ]
    if 'State' in detail_df.columns:
        columns.append({'name': 'State', 'id': 'State'})

    detail_table = dash_table.DataTable(
        data=detail_df.to_dict('records'),
        columns=columns,
        sort_action='native',
        style_table={'overflowX': 'auto', 'minWidth': '100%'},
        style_header=_HDR_LIGHT,
        style_cell=_CELL_SM,
        style_data_conditional=_LINE_COND,
    )

    so_part = f"  (SO: {so_label})" if str(so_label).strip() else ""
    title   = f"Line Items for {selected_invoice}{so_part}  —  {customer}"
    return _SHOW, title, detail_table
