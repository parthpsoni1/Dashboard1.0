import sys
import os
from datetime import timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir    = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

import dash
from dash import html, dcc, Input, Output, State, ctx, callback, no_update
from dash.dash_table.Format import Format, Scheme, Group
from dash import dash_table

from data_loader import df_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security

try:
    from statsmodels.tsa.seasonal import seasonal_decompose
except ImportError:
    seasonal_decompose = None

# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

available_years = years_global if years_global else []

def _get_df(auth_data):
    _df, _, _, _ = get_latest_data()
    df = enforce_row_level_security(_df, auth_data)
    if not df.empty:
        df['Month_Year']   = df['Date'].dt.to_period('M').astype(str)
        df['Year']         = df['Date'].dt.year.astype(str)
        df['Week_Year']    = df['Date'].dt.to_period('W').dt.start_time.dt.strftime('%Y-%m-%d')
        df['Quarter_Year'] = (df['Date'].dt.year.astype(str) + '-Q' +
                              df['Date'].dt.quarter.astype(str))
        df['Value']   = pd.to_numeric(df['Value'],   errors='coerce').fillna(0)
        df['QTY PCS'] = pd.to_numeric(df['QTY PCS'], errors='coerce').fillna(0)
        df['Basket_ID'] = df['Customer Names'] + "_" + df['Date'].astype(str)
    return df

def _safe_div(a, b):
    return (a / b).replace([np.inf, -np.inf], np.nan).fillna(0)

def _qlabel(q):
    parts = q.split('-Q')
    return f"Q{parts[1]} {parts[0]}" if len(parts) == 2 else q

def calculate_rfm(df_filtered, snapshot_date):
    if df_filtered.empty:
        return pd.DataFrame()
    rfm = df_filtered.groupby('Customer Names').agg({
        'Date':      lambda x: (snapshot_date - x.max()).days,
        'Value':     'sum',
        'Basket_ID': 'nunique',
        'B2B':       'first',
    }).rename(columns={'Date': 'Recency', 'Value': 'Monetary', 'Basket_ID': 'Frequency'})

    def safe_qcut(series, k, labels):
        try:
            return pd.qcut(series.rank(method='first'), k, labels=labels)
        except ValueError:
            return pd.Series([labels[0]] * len(series), index=series.index)

    rfm['R_Score'] = safe_qcut(rfm['Recency'],   5, labels=[5, 4, 3, 2, 1])
    rfm['F_Score'] = safe_qcut(rfm['Frequency'], 5, labels=[1, 2, 3, 4, 5])
    rfm['M_Score'] = safe_qcut(rfm['Monetary'],  5, labels=[1, 2, 3, 4, 5])
    rfm['RFM_Score'] = rfm[['R_Score', 'F_Score', 'M_Score']].astype(int).sum(axis=1)

    def _segment(row):
        s = row['RFM_Score']
        if s >= 14: return 'Champions'
        if s >= 11: return 'Loyal Customers'
        if s >= 8:  return 'Potential Loyalists'
        if s >= 6:  return 'At Risk'
        if s >= 4:  return 'Needs Attention'
        return 'Lost'

    def _action(row):
        seg = row['Segment']
        if seg == 'Champions':          return "Reward & Request Referrals"
        if seg == 'Loyal Customers':    return "Upsell Higher Margin Items"
        if seg == 'Potential Loyalists':return "Offer Loyalty Program / Volume Discount"
        if seg == 'At Risk':            return "Send 'We Miss You' Targeted Promotion"
        if seg == 'Needs Attention':    return "Reactivation Campaign"
        return "Do Not Spend Marketing Budget Here"

    rfm['Segment']          = rfm.apply(_segment, axis=1)
    rfm['Suggested Action'] = rfm.apply(_action,  axis=1)
    rfm.reset_index(inplace=True)
    return rfm

def remove_outliers_iqr(x, y):
    df_tmp = pd.DataFrame({'x': x, 'y': y})
    Q1, Q3 = df_tmp.quantile(0.25), df_tmp.quantile(0.75)
    IQR    = Q3 - Q1
    mask   = ~((df_tmp < (Q1 - 1.5 * IQR)) | (df_tmp > (Q3 + 1.5 * IQR))).any(axis=1)
    return x[mask], y[mask], len(x) - mask.sum()

def calc_regression(x, y, mode='linear'):
    if len(x) < 2:
        return None, None, None, None, None
    if mode == 'linear':
        coeffs = np.polyfit(x, y, 1)
        y_pred = np.poly1d(coeffs)(x)
        m      = coeffs[0]
        eq     = f"y = {coeffs[0]:.4f}x + {coeffs[1]:.2f}"
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2     = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        return x, y_pred, f"{eq} (R²={r2:.2f})", r2, m
    elif mode == 'power':
        mask = (x > 0) & (y > 0)
        xc, yc = x[mask], y[mask]
        if len(xc) < 2:
            return None, None, None, None, None
        coeffs   = np.polyfit(np.log(xc), np.log(yc), 1)
        b, a     = coeffs[0], np.exp(coeffs[1])
        y_pred   = a * np.power(xc, b)
        m        = b
        eq       = f"y = {a:.2f}x^{b:.3f}"
        sort_idx = np.argsort(xc)
        return xc.iloc[sort_idx], y_pred.iloc[sort_idx], eq, 0, m
    return None, None, None, None, None

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_MODE_OPTS = [
    {'label': 'Weekly',    'value': 'Weekly'},
    {'label': 'Monthly',   'value': 'Monthly'},
    {'label': 'Quarterly', 'value': 'Quarterly'},
    {'label': 'Yearly',    'value': 'Yearly'},
]
_HIER_OPTS = [
    {'label': 'Grp',  'value': 'Items 2'},
    {'label': 'Sub',  'value': 'Items'},
    {'label': 'Item', 'value': 'Name'},
]
_LBL  = {'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}
_DD   = {'display': 'inline-block'}
_CTRL = {
    'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
    'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '8px',
    'marginBottom': '20px', 'border': f'1px solid {s.COLORS["border"]}',
    'flexWrap': 'wrap', 'gap': '10px',
}
_INSIGHT = {
    'backgroundColor': '#e8f4f8', 'borderLeft': '4px solid #3498db', 'padding': '15px',
    'borderRadius': '4px', 'marginBottom': '20px', 'color': '#2c3e50',
    'fontSize': '14px', 'lineHeight': '1.5',
}
_SEG_COLORS = {
    "Champions": "#27ae60", "Loyal Customers": "#2ecc71",
    "Potential Loyalists": "#3498db", "At Risk": "#f39c12",
    "Needs Attention": "#e67e22", "Lost": "#c0392b",
}

# ─────────────────────────────────────────────────────────────────────────────
# TAB LAYOUTS
# ─────────────────────────────────────────────────────────────────────────────

# ── Tab 1: Multi-Period ───────────────────────────────────────────────────────
tab_multiperiod = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='mp-mode', options=_MODE_OPTS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("PERIODS (select 2–4):", style=_LBL),
                  dcc.Dropdown(id='mp-periods', multi=True,
                               placeholder="Select periods to compare…",
                               style={'width': '500px', 'minWidth': '300px'})], style=_DD),
        html.Div([html.Span("CUSTOMERS (optional):", style=_LBL),
                  dcc.Dropdown(id='mp-custs', multi=True,
                               placeholder="All customers",
                               style={'width': '300px'})], style=_DD),
        html.Div([html.Span("SEGMENT:", style=_LBL),
                  dcc.Dropdown(id='mp-segment',
                               options=[{'label': 'All', 'value': 'All'},
                                        {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', clearable=False,
                               style={'width': '90px'})], style=_DD),
    ], style=_CTRL),
    html.Div(id='mp-kpi-row', style={'display': 'flex', 'gap': '10px',
                                      'marginBottom': '20px', 'flexWrap': 'wrap'}),
    html.Div([dcc.Graph(id='mp-bar',     style={'height': '70vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='mp-heatmap', style={'height': '60vh'})],
             style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
])

# ── Tab 2: Rank Movement ─────────────────────────────────────────────────────
tab_rank = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='rk-mode', options=_MODE_OPTS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("BASE:", style=_LBL),
                  dcc.Dropdown(id='rk-base', multi=True,
                               placeholder="Select base period(s)…",
                               style={'width': '260px', 'minWidth': '180px'})], style=_DD),
        html.Div([html.Span("TARGET:", style=_LBL),
                  dcc.Dropdown(id='rk-target', multi=True,
                               placeholder="Select target period(s)…",
                               style={'width': '260px', 'minWidth': '180px'})], style=_DD),
        html.Div([html.Span("TOP N:", style=_LBL),
                  dcc.Dropdown(id='rk-topn',
                               options=[{'label': str(n), 'value': n} for n in [10, 20, 30, 50]],
                               value=20, clearable=False, style={'width': '75px'})], style=_DD),
        html.Div([html.Span("SEGMENT:", style=_LBL),
                  dcc.Dropdown(id='rk-segment',
                               options=[{'label': 'All', 'value': 'All'},
                                        {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', clearable=False, style={'width': '90px'})], style=_DD),
    ], style=_CTRL),
    html.Div([dcc.Graph(id='rk-bump', style={'height': '70vh'})], style=s.CHART_CONTAINER),
    html.Div([
        html.H3("Rank Movement Table",
                style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '15px'}),
        dash_table.DataTable(
            id='rk-table',
            columns=[
                {'name': 'Customer',    'id': 'Customer'},
                {'name': 'Base Rev',    'id': 'Base_Rev',    'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'Target Rev',  'id': 'Target_Rev',  'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'Base Rank',   'id': 'Base_Rank'},
                {'name': 'Target Rank', 'id': 'Target_Rank'},
                {'name': 'Change',      'id': 'Rank_Change'},
            ],
            sort_action='native', page_size=25,
            style_header={'backgroundColor': '#2c3e50', 'color': 'white',
                          'fontWeight': 'bold', 'fontSize': '12px'},
            style_cell={'textAlign': 'left', 'fontFamily': 'sans-serif',
                        'fontSize': '12px', 'padding': '8px'},
            style_data_conditional=[
                {'if': {'filter_query': '{Rank_Change} contains "▲"'},
                 'color': '#27ae60', 'fontWeight': 'bold'},
                {'if': {'filter_query': '{Rank_Change} contains "▼"'},
                 'color': '#e74c3c', 'fontWeight': 'bold'},
                {'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'},
            ]
        )
    ], style={**s.SECTION, 'marginTop': '20px'}),
])

# ── Tab 3: Freq / Recency ────────────────────────────────────────────────────
tab_freq = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='fr-mode', options=_MODE_OPTS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("BASE:", style=_LBL),
                  dcc.Dropdown(id='fr-base', multi=True,
                               placeholder="Select base period(s)…",
                               style={'width': '260px', 'minWidth': '180px'})], style=_DD),
        html.Div([html.Span("TARGET:", style=_LBL),
                  dcc.Dropdown(id='fr-target', multi=True,
                               placeholder="Select target period(s)…",
                               style={'width': '260px', 'minWidth': '180px'})], style=_DD),
        html.Div([html.Span("TOP N:", style=_LBL),
                  dcc.Dropdown(id='fr-topn',
                               options=[{'label': str(n), 'value': n} for n in [10, 20, 30]],
                               value=20, clearable=False, style={'width': '75px'})], style=_DD),
        html.Div([html.Span("SEGMENT:", style=_LBL),
                  dcc.Dropdown(id='fr-segment',
                               options=[{'label': 'All', 'value': 'All'},
                                        {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', clearable=False, style={'width': '90px'})], style=_DD),
    ], style=_CTRL),
    html.Div([dcc.Graph(id='fr-scatter', style={'height': '70vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='fr-bar',     style={'height': '60vh'})],
             style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
    html.Div([
        html.H3("Recency / Frequency Summary",
                style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '15px'}),
        dash_table.DataTable(
            id='fr-table',
            columns=[
                {'name': 'Customer',              'id': 'Customer'},
                {'name': 'Last Purchase',         'id': 'Last_Date'},
                {'name': 'Days Since',            'id': 'Days_Since',   'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0)},
                {'name': 'Order Days (Target)',   'id': 'Order_Days_T', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0)},
                {'name': 'Order Days (Base)',     'id': 'Order_Days_B', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0)},
                {'name': 'Avg Gap Days (Target)', 'id': 'Avg_Gap_T',    'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=1)},
                {'name': 'Avg Gap Days (Base)',   'id': 'Avg_Gap_B',    'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=1)},
                {'name': 'Revenue (Target)',      'id': 'Rev_T',        'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'Revenue (Base)',        'id': 'Rev_B',        'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
            ],
            sort_action='native', page_size=20,
            style_header={'backgroundColor': '#8e44ad', 'color': 'white',
                          'fontWeight': 'bold', 'fontSize': '12px'},
            style_cell={'textAlign': 'left', 'fontFamily': 'sans-serif',
                        'fontSize': '12px', 'padding': '8px'},
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}]
        )
    ], style={**s.SECTION, 'marginTop': '20px'}),
])

# ── Tab 4: Seasonality (+ decomposition toggle) ──────────────────────────────
tab_season = html.Div([
    html.Div([
        html.Div([html.Span("CUSTOMERS:", style=_LBL),
                  dcc.Dropdown(id='sea-custs', multi=True,
                               placeholder="Select up to 4 customers…",
                               style={'width': '400px'})], style=_DD),
        html.Div([html.Span("SEGMENT:", style=_LBL),
                  dcc.Dropdown(id='sea-segment',
                               options=[{'label': 'All', 'value': 'All'},
                                        {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', clearable=False, style={'width': '90px'})], style=_DD),
        html.Div([html.Span("VIEW:", style=_LBL),
                  dcc.RadioItems(id='sea-view',
                                 options=[{'label': 'Monthly',   'value': 'monthly'},
                                          {'label': 'Quarterly', 'value': 'quarterly'}],
                                 value='monthly', inline=True,
                                 style={'fontSize': '14px'})], style=_DD),
    ], style=_CTRL),
    html.P("Each line = one calendar year. Overlaid on the same axis to spot seasonal patterns.",
           style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginBottom': '5px'}),
    html.Div([dcc.Graph(id='sea-lines',   style={'height': '70vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='sea-heatmap', style={'height': '50vh'})],
             style={**s.CHART_CONTAINER, 'marginTop': '20px'}),

    # ── Decomposition toggle ──
    html.Div([
        dcc.Checklist(id='sea-decomp-toggle',
                      options=[{'label': '  Show Time-Series Decomposition (Trend / Seasonal / Residuals)',
                                'value': 'show'}],
                      value=[],
                      style={'fontWeight': 'bold', 'color': '#2c3e50', 'marginTop': '20px'})
    ]),
    html.Div(id='sea-decomp-container', style={'display': 'none'}, children=[
        html.Div([
            html.Div([html.Span("CONTEXT:", style=_LBL),
                      dcc.RadioItems(id='sea-decomp-ctx',
                                     options=[{'label': 'Item',     'value': 'item'},
                                              {'label': 'Customer', 'value': 'cust'},
                                              {'label': 'Segment',  'value': 'seg'},
                                              {'label': 'Global',   'value': 'global'}],
                                     value='global', inline=True,
                                     style={'fontSize': '13px'})], style=_DD),
            html.Div([html.Span("TARGET:", style=_LBL),
                      dcc.Dropdown(id='sea-decomp-target', placeholder="Select or leave Global…",
                                   style={'width': '250px'})], style=_DD),
            html.Div([html.Span("MODEL:", style=_LBL),
                      dcc.RadioItems(id='sea-decomp-model',
                                     options=[{'label': 'Multiplicative', 'value': 'multiplicative'},
                                              {'label': 'Additive',       'value': 'additive'}],
                                     value='multiplicative', inline=True,
                                     style={'fontSize': '13px'})], style=_DD),
        ], style=_CTRL),
        dcc.Graph(id='sea-decomp-chart', style={'height': '600px'}),
    ]),
])

# ── Tab 5: Wallet Share ───────────────────────────────────────────────────────
tab_wallet = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='ws-mode', options=_MODE_OPTS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("PERIOD:", style=_LBL),
                  dcc.Dropdown(id='ws-period', clearable=False, style={'width': '130px'})], style=_DD),
        html.Div([html.Span("LEVEL:", style=_LBL),
                  dcc.RadioItems(id='ws-hier', options=_HIER_OPTS, value='Items 2',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("TOP CUSTOMERS:", style=_LBL),
                  dcc.Dropdown(id='ws-topn',
                               options=[{'label': str(n), 'value': n} for n in [10, 20, 30]],
                               value=15, clearable=False, style={'width': '75px'})], style=_DD),
        html.Div([html.Span("SEGMENT:", style=_LBL),
                  dcc.Dropdown(id='ws-segment',
                               options=[{'label': 'All', 'value': 'All'},
                                        {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', clearable=False, style={'width': '90px'})], style=_DD),
    ], style=_CTRL),
    html.P("Cell colour = % of that customer's spend in that category. Pale = low, dark = high concentration.",
           style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginBottom': '5px'}),
    html.Div([dcc.Graph(id='ws-heatmap', style={'height': '75vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='ws-bar',     style={'height': '55vh'})],
             style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
])

# ── Tab 6: Customer Health / RFM ─────────────────────────────────────────────
tab_rfm = html.Div([
    html.Div([
        html.Span("FILTER:", style={**_LBL, 'marginRight': '10px'}),
        dcc.RadioItems(id='rfm-segment-filter',
                       options=[{'label': 'All', 'value': 'All'},
                                 {'label': 'B2B', 'value': 'B2B'},
                                 {'label': 'B2C', 'value': 'B2C'}],
                       value='All', inline=True, style={'display': 'inline-block'}),
        html.Button("Reset Table", id='rfm-reset-btn',
                    style={**s.BTN_STYLE, 'marginLeft': 'auto', 'marginRight': '10px'}),
        html.Button("📥 Download Hit List", id='rfm-download-btn',
                    style={'backgroundColor': '#27ae60', 'color': 'white', 'border': 'none',
                           'padding': '8px 16px', 'borderRadius': '4px', 'cursor': 'pointer',
                           'fontWeight': 'bold'}),
        dcc.Download(id='download-rfm-csv'),
    ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '20px',
              'padding': '10px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

    html.Div(id='rfm-auto-insight', style=_INSIGHT),

    html.Div([
        html.Div([dcc.Graph(id='rfm-bar-chart', style={'height': '450px'})],
                 style={'flex': '1', 'minWidth': '0', 'backgroundColor': 'white', 'padding': '10px',
                        'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
                        'border': '1px solid #e0e0e0'}),
        html.Div([dcc.Graph(id='rfm-3d-chart',  style={'height': '450px'})],
                 style={'flex': '1', 'minWidth': '0', 'backgroundColor': 'white', 'padding': '10px',
                        'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
                        'border': '1px solid #e0e0e0'}),
    ], style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px'}),

    html.H3("Customer Action Playbook", style={'textAlign': 'center', 'marginTop': '20px'}),
    dash_table.DataTable(
        id='rfm-table',
        columns=[
            {'name': 'Customer',        'id': 'Customer Names'},
            {'name': 'Health Segment',  'id': 'Segment'},
            {'name': 'Suggested Action','id': 'Suggested Action'},
            {'name': 'Total Sales',     'id': 'Monetary',  'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
            {'name': 'Days Inactive',   'id': 'Recency'},
        ],
        style_table={'height': '400px', 'overflowY': 'auto'},
        sort_action='native', filter_action='native',
        style_header={'backgroundColor': s.COLORS['primary'], 'color': 'white', 'fontWeight': 'bold'},
        style_cell={'textAlign': 'left', 'padding': '10px'},
        style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': 'rgb(248,248,248)'}],
    ),
], style=s.SECTION)

# ── Tab 7: Cross-Selling ─────────────────────────────────────────────────────
tab_basket = html.Div([
    html.Div([
        html.Span("HIERARCHY:", style={**_LBL, 'marginRight': '10px'}),
        dcc.RadioItems(id='basket-hier',
                       options=[{'label': 'Grp',  'value': 'Items 2'},
                                 {'label': 'Sub',  'value': 'Items'},
                                 {'label': 'Item', 'value': 'Name'}],
                       value='Items', inline=True,
                       style={'display': 'inline-block', 'marginRight': '20px'}),
        html.Span("TARGET:", style={**_LBL, 'marginRight': '10px'}),
        dcc.Dropdown(id='basket-item-select', placeholder="Select Item/Group…",
                     style={'width': '300px', 'display': 'inline-block'}),
    ], style={'marginBottom': '20px', 'padding': '15px',
              'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

    html.Div(id='basket-auto-insight', style=_INSIGHT),
    html.Div([dcc.Graph(id='basket-chart', style={'height': '60vh'})], style=s.CHART_CONTAINER),

    html.Div(id='basket-comparison-container',
             style={'display': 'none', 'marginTop': '20px', 'padding': '20px',
                    'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'},
             children=[
                 html.Div([
                     html.H3("Cross-Sell Pitch Opportunity",
                             style={'display': 'inline-block', 'marginRight': '20px'}),
                     html.Button("📥 Download Target List", id='basket-download-btn',
                                 style={'backgroundColor': '#27ae60', 'color': 'white', 'border': 'none',
                                        'padding': '8px 16px', 'borderRadius': '4px', 'cursor': 'pointer',
                                        'fontWeight': 'bold'}),
                     dcc.Download(id='download-basket-csv'),
                 ], style={'marginBottom': '10px'}),
                 html.P(id='basket-pitch-insight', style={'color': '#7f8c8d'}),
                 dcc.Graph(id='basket-compare-chart'),
             ]),
], style=s.SECTION)

# ── Tab 8: Sales Trajectory ───────────────────────────────────────────────────
tab_forecast = html.Div([
    html.Div([
        html.Span("CONTEXT:", style={**_LBL, 'marginRight': '10px'}),
        dcc.RadioItems(id='forecast-context',
                       options=[{'label': 'Item',     'value': 'item'},
                                 {'label': 'Customer', 'value': 'cust'},
                                 {'label': 'Rep',      'value': 'rep'},
                                 {'label': 'Segment',  'value': 'seg'}],
                       value='item', inline=True,
                       style={'display': 'inline-block', 'marginRight': '20px'}),
        dcc.Dropdown(id='forecast-target-select', placeholder="Select Target…",
                     style={'width': '250px', 'display': 'inline-block', 'marginRight': '20px'}),
        html.Div([
            html.Label("Target Growth:", style={'fontWeight': 'bold', 'fontSize': '11px', 'marginRight': '5px'}),
            dcc.Input(id='forecast-goal-input', type='number', value=10, min=-50, max=200, step=1,
                      style={'width': '60px'}),
            html.Span("%", style={'fontSize': '12px', 'marginLeft': '2px'}),
        ], style={'display': 'inline-block', 'borderLeft': '1px solid #ddd', 'paddingLeft': '15px'}),
    ], style={'marginBottom': '20px', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

    html.Div(id='forecast-auto-insight', style=_INSIGHT),
    html.Div([dcc.Graph(id='forecast-chart', style={'height': '60vh'})], style=s.CHART_CONTAINER),
], style=s.SECTION)

# ── Tab 9: Pricing Power ──────────────────────────────────────────────────────
tab_elasticity = html.Div([
    html.Div([
        html.Span("HIERARCHY:", style={**_LBL, 'marginRight': '10px'}),
        dcc.RadioItems(id='elasticity-hier',
                       options=[{'label': 'Grp',  'value': 'Items 2'},
                                 {'label': 'Sub',  'value': 'Items'},
                                 {'label': 'Item', 'value': 'Name'}],
                       value='Items', inline=True,
                       style={'display': 'inline-block', 'marginRight': '20px'}),
        html.Span("ITEM:", style={**_LBL, 'marginRight': '10px'}),
        dcc.Dropdown(id='elasticity-item-select', placeholder="Select Item (or ALL)…", value='ALL',
                     style={'width': '250px', 'display': 'inline-block', 'marginRight': '20px'}),
        html.Div(id='simulator-container', children=[
            html.Span("SIMULATE PRICE CHANGE:", style={**_LBL, 'marginRight': '10px'}),
            dcc.Slider(id='price-simulator-slider', min=-30, max=30, step=1, value=-5,
                       marks={-30: '-30%', 0: '0%', 30: '+30%'}),
        ], style={'width': '300px', 'display': 'inline-block', 'verticalAlign': 'middle',
                  'marginLeft': '20px', 'borderLeft': '1px solid #ddd', 'paddingLeft': '20px'}),
    ], style={'marginBottom': '20px', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

    html.Div(id='elasticity-auto-insight', style=_INSIGHT),

    html.Div(id='simulator-cards', children=[
        html.Div(id='sim-vol-card',
                 style={'flex': '1', 'backgroundColor': 'white', 'padding': '20px',
                        'borderRadius': '8px', 'textAlign': 'center',
                        'border': '1px solid #eee', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)'}),
        html.Div(id='sim-rev-card',
                 style={'flex': '1', 'backgroundColor': 'white', 'padding': '20px',
                        'borderRadius': '8px', 'textAlign': 'center',
                        'border': '1px solid #eee', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)'}),
    ], style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px'}),

    html.Div([
        dcc.Checklist(id='elasticity-advanced-toggle',
                      options=[{'label': ' Show Advanced Chart Controls', 'value': 'show'}],
                      value=[], style={'marginBottom': '10px', 'fontWeight': 'bold', 'color': '#2c3e50'}),
        html.Div(id='elasticity-advanced-controls', style={'display': 'none'}, children=[
            html.Span("CLEANING: ", style={**_LBL, 'marginRight': '10px'}),
            dcc.Checklist(id='elasticity-options',
                          options=[{'label': ' Remove Outliers (IQR)', 'value': 'outlier'},
                                   {'label': ' Power Law',             'value': 'power'},
                                   {'label': ' Linear',                'value': 'linear'}],
                          value=['power'], inline=True,
                          style={'display': 'inline-block', 'marginRight': '20px', 'fontSize': '13px'}),
            html.Span("AXIS SCALE: ", style={**_LBL, 'marginRight': '10px'}),
            dcc.RadioItems(id='elasticity-scale',
                           options=[{'label': ' Logarithmic', 'value': 'log'},
                                    {'label': ' Linear',      'value': 'linear'}],
                           value='log', inline=True, style={'fontSize': '13px'}),
        ]),
    ]),

    html.Div([dcc.Graph(id='elasticity-chart', style={'height': '60vh'})], style=s.CHART_CONTAINER),
    html.Div(id='elasticity-stats'),
    html.Div(id='elasticity-examples-container', style={'marginTop': '30px'}),
], style=s.SECTION)

# ─────────────────────────────────────────────────────────────────────────────
# ROOT LAYOUT
# ─────────────────────────────────────────────────────────────────────────────

layout = html.Div([
    # Stores
    dcc.Store(id='rfm-data-store'),
    dcc.Store(id='store-basket-item'),
    dcc.Store(id='snap-mp'),    dcc.Store(id='snap-mp-hm'),
    dcc.Store(id='snap-rk'),    dcc.Store(id='snap-fr'),
    dcc.Store(id='snap-sea'),   dcc.Store(id='snap-sea-hm'),
    dcc.Store(id='snap-ws'),
    dcc.Store(id='snap-rfm-bar'), dcc.Store(id='snap-rfm-3d'),
    dcc.Store(id='snap-basket-main'), dcc.Store(id='snap-basket-comp'),
    dcc.Store(id='snap-forecast'),
    dcc.Store(id='snap-elasticity'),
    dcc.Download(id='ecc-dl'),

    html.Div([
        html.H1("Advanced Customer Analytics", style=s.HEADER_TITLE),
        html.P("Multi-period · Rank movement · Recency · Seasonality · Wallet share · "
               "RFM health · Cross-selling · Trajectory · Pricing power",
               style={'textAlign': 'center', 'color': '#7f8c8d', 'marginBottom': '10px'}),

        # ── Header KPI strip ────────────────────────────────────────────────
        html.Div(id='ecc-header-kpi-row',
                 style={'display': 'flex', 'gap': '10px', 'marginBottom': '14px',
                        'flexWrap': 'wrap'}),

        html.Div([
            html.Label("Year (for RFM / Cross-Sell / Pricing):",
                       style={'fontWeight': 'bold', 'marginRight': '10px', 'color': '#7f8c8d'}),
            dcc.Dropdown(id='year-select',
                         options=[{'label': str(y), 'value': y} for y in available_years],
                         value=available_years[0] if available_years else None,
                         clearable=False, style={'width': '110px', 'display': 'inline-block'}),
            html.Button("📸 Snapshot", id='ecc-snap', style={**s.BTN_STYLE, 'marginLeft': '20px'}),
        ], style={'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
                  'marginBottom': '15px'}),

        dcc.Tabs(id='ecc-tabs', value='tab-multiperiod', children=[
            dcc.Tab(label='Multi-Period',     value='tab-multiperiod'),
            dcc.Tab(label='Rank Movement',    value='tab-rank'),
            dcc.Tab(label='Freq / Recency',   value='tab-freq'),
            dcc.Tab(label='Seasonality',      value='tab-season'),
            dcc.Tab(label='Wallet Share',     value='tab-wallet'),
            dcc.Tab(label='Customer Health',  value='tab-rfm'),
            dcc.Tab(label='Cross-Selling',    value='tab-basket'),
            dcc.Tab(label='Sales Trajectory', value='tab-forecast'),
            dcc.Tab(label='Pricing Power',    value='tab-elasticity'),
        ], style={'fontWeight': 'bold', 'fontSize': '14px'}),
    ], style=s.SECTION),

    html.Div(tab_multiperiod, id='ecc-con-mp'),
    html.Div(tab_rank,        id='ecc-con-rk'),
    html.Div(tab_freq,        id='ecc-con-fr'),
    html.Div(tab_season,      id='ecc-con-sea'),
    html.Div(tab_wallet,      id='ecc-con-ws'),
    html.Div(tab_rfm,         id='ecc-con-rfm'),
    html.Div(tab_basket,      id='ecc-con-basket'),
    html.Div(tab_forecast,    id='ecc-con-forecast'),
    html.Div(tab_elasticity,  id='ecc-con-elasticity'),
], style=s.MAIN_CONTAINER)


# ─────────────────────────────────────────────────────────────────────────────
# TAB VISIBILITY
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    [Output('ecc-con-mp',        'style'), Output('ecc-con-rk',       'style'),
     Output('ecc-con-fr',        'style'), Output('ecc-con-sea',      'style'),
     Output('ecc-con-ws',        'style'), Output('ecc-con-rfm',      'style'),
     Output('ecc-con-basket',    'style'), Output('ecc-con-forecast',  'style'),
     Output('ecc-con-elasticity','style')],
    Input('ecc-tabs', 'value')
)
def _show_tab(tab):
    show, hide = {'display': 'block'}, {'display': 'none'}
    order = ['tab-multiperiod', 'tab-rank', 'tab-freq', 'tab-season', 'tab-wallet',
             'tab-rfm', 'tab-basket', 'tab-forecast', 'tab-elasticity']
    return [show if tab == t else hide for t in order]


# ─────────────────────────────────────────────────────────────────────────────
# SHARED DROPDOWN POPULATION
# ─────────────────────────────────────────────────────────────────────────────

def _period_opts(df, mode):
    if mode == 'Weekly':    vals = sorted(df['Week_Year'].unique(),    reverse=True)
    elif mode == 'Monthly': vals = sorted(df['Month_Year'].unique(),   reverse=True)
    elif mode == 'Quarterly':
        vals = sorted(df['Quarter_Year'].unique(), reverse=True)
        return [{'label': _qlabel(v), 'value': v} for v in vals]
    else: vals = sorted(df['Year'].unique(), reverse=True)
    return [{'label': v, 'value': v} for v in vals]


@callback(
    [Output('mp-periods', 'options'),
     Output('rk-base',   'options'), Output('rk-base',   'value'),
     Output('rk-target', 'options'), Output('rk-target', 'value'),
     Output('fr-base',   'options'), Output('fr-base',   'value'),
     Output('fr-target', 'options'), Output('fr-target', 'value'),
     Output('ws-period', 'options'), Output('ws-period', 'value')],
    [Input('mp-mode', 'value'), Input('rk-mode', 'value'),
     Input('fr-mode', 'value'), Input('ws-mode', 'value')],
    State('auth-store', 'data')
)
def _populate_period_dropdowns(mp_m, rk_m, fr_m, ws_m, auth_data):
    df = _get_df(auth_data)
    if df.empty:
        empty = []
        return empty, empty, None, empty, None, empty, None, empty, None, empty, None

    def _bt(opts):
        # Return default selections as lists (rk/fr dropdowns are now multi=True)
        base   = [opts[1]['value']] if len(opts) > 1 else ([opts[0]['value']] if opts else [])
        target = [opts[0]['value']] if opts else []
        return base, target

    mp_opts = _period_opts(df, mp_m)
    rk_opts = _period_opts(df, rk_m); rk_b, rk_t = _bt(rk_opts)
    fr_opts = _period_opts(df, fr_m); fr_b, fr_t = _bt(fr_opts)
    ws_opts = _period_opts(df, ws_m); ws_val = ws_opts[0]['value'] if ws_opts else None
    return (mp_opts, rk_opts, rk_b, rk_opts, rk_t,
            fr_opts, fr_b, fr_opts, fr_t, ws_opts, ws_val)


# ─────────────────────────────────────────────────────────────────────────────
# HEADER KPI CARDS
# ─────────────────────────────────────────────────────────────────────────────

def _kpi_card(title, value_str, color, sub=''):
    return html.Div([
        html.Div(style={'height': '5px', 'backgroundColor': color,
                        'borderTopLeftRadius': '8px', 'borderTopRightRadius': '8px'}),
        html.Div([
            html.H4(title, style={'color': '#95a5a6', 'fontSize': '11px', 'fontWeight': 'bold',
                                  'textAlign': 'center', 'margin': '10px 0 3px',
                                  'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
            html.H2(value_str, style={'margin': '0 0 3px', 'color': '#2c3e50',
                                      'fontSize': '20px', 'textAlign': 'center'}),
            html.P(sub, style={'margin': '0 0 10px', 'color': '#95a5a6',
                               'fontSize': '11px', 'textAlign': 'center'}),
        ], style={'padding': '0 12px'}),
    ], style={'backgroundColor': 'white', 'borderRadius': '8px', 'flex': '1',
              'border': '1px solid #eee', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
              'minWidth': '130px'})


def _kpi_pct_card(pct, cy, ly):
    color = '#27ae60' if pct >= 0 else '#e74c3c'
    arrow = '▲' if pct >= 0 else '▼'
    return html.Div([
        html.Div(style={'height': '5px', 'backgroundColor': '#f1c40f',
                        'borderTopLeftRadius': '8px', 'borderTopRightRadius': '8px'}),
        html.Div([
            html.H4("YoY Δ %", style={'color': '#95a5a6', 'fontSize': '11px', 'fontWeight': 'bold',
                                      'textAlign': 'center', 'margin': '10px 0 3px',
                                      'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
            html.H2(f"{arrow} {abs(pct):.1f}%",
                    style={'margin': '0 0 3px', 'color': color,
                           'fontSize': '20px', 'textAlign': 'center'}),
            html.P(f"{cy} vs {ly}", style={'margin': '0 0 10px', 'color': '#95a5a6',
                                           'fontSize': '11px', 'textAlign': 'center'}),
        ], style={'padding': '0 12px'}),
    ], style={'backgroundColor': 'white', 'borderRadius': '8px', 'flex': '1',
              'border': '1px solid #eee', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
              'minWidth': '130px'})


@callback(
    Output('ecc-header-kpi-row', 'children'),
    Input('year-select', 'value'),
    State('auth-store', 'data'),
)
def update_ecc_kpis(year, auth_data):
    if not year:
        return []
    df = _get_df(auth_data)
    if df.empty:
        return []

    cy, ly = int(year), int(year) - 1
    df_cy  = df[df['Date'].dt.year == cy]
    df_ly  = df[df['Date'].dt.year == ly]

    cy_rev = df_cy['Value'].sum()
    ly_rev = df_ly['Value'].sum()
    delta  = cy_rev - ly_rev
    pct    = (delta / ly_rev * 100) if ly_rev else 0

    cust_col    = next((c for c in ['Customer Names', 'Customer', 'Client']
                        if c in df_cy.columns), None)
    active_cy   = df_cy[cust_col].nunique() if cust_col else 0
    active_ly   = df_ly[cust_col].nunique() if cust_col else 0
    avg_tx_cy   = cy_rev / len(df_cy) if len(df_cy) else 0

    def _fmt(v):
        if v >= 1_000_000: return f"Ksh {v/1_000_000:.2f}M"
        if v >= 1_000:     return f"Ksh {v/1_000:.1f}K"
        return f"Ksh {v:,.0f}"

    return [
        _kpi_card(f"Revenue {cy}",     _fmt(cy_rev),  '#3498db', f"Full year {cy}"),
        _kpi_card(f"Revenue {ly}",     _fmt(ly_rev),  '#9b59b6', f"Full year {ly}"),
        _kpi_card("Δ Revenue",         _fmt(abs(delta)), '#e67e22',
                  f"{'▲ Up' if delta >= 0 else '▼ Down'} vs {ly}"),
        _kpi_pct_card(pct, cy, ly),
        _kpi_card("Active Customers",  f"{active_cy:,}", '#1abc9c',
                  f"{active_ly:,} in {ly}"),
        _kpi_card("Avg Transaction",   f"Ksh {avg_tx_cy:,.0f}", '#34495e',
                  f"Per line {cy}"),
    ]


@callback(
    [Output('mp-custs',  'options'),
     Output('sea-custs', 'options')],
    Input('ecc-tabs', 'value'),
    State('auth-store', 'data')
)
def _populate_cust_dropdowns(_tab, auth_data):
    df       = _get_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    if df.empty or not cust_col:
        return [], []
    opts = [{'label': c, 'value': c} for c in sorted(df[cust_col].dropna().unique())]
    return opts, opts


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — MULTI-PERIOD
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    [Output('mp-kpi-row', 'children'),
     Output('mp-bar',     'figure'),
     Output('mp-heatmap', 'figure'),
     Output('snap-mp',    'data'),
     Output('snap-mp-hm', 'data')],
    [Input('mp-periods', 'value'), Input('mp-mode',    'value'),
     Input('mp-custs',   'value'), Input('mp-segment', 'value')],
    State('auth-store', 'data')
)
def update_multiperiod(periods, mode, custs, segment, auth_data):
    empty = [], go.Figure(), go.Figure(), {}, {}
    if not periods or len(periods) < 2:
        return empty

    df = _get_df(auth_data)
    if df.empty:
        return empty

    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    vol_col  = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl    = 'Qty'     if METRIC_MODE == 'qty' else 'Kg'
    col_map  = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year',
                'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col      = col_map.get(mode, 'Month_Year')
    periods  = periods[:4]

    def _label(p):
        return _qlabel(p) if mode == 'Quarterly' else p

    agg_list = []
    for p in periods:
        dff = df[df[col] == p]
        if custs:
            dff = dff[dff[cust_col].isin(custs)]
        agg = dff.groupby(cust_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
        agg['Period']    = _label(p)
        agg['Avg_Price'] = _safe_div(agg['Value'], agg['Quantity'])
        agg_list.append(agg)

    combined = pd.concat(agg_list)
    palette  = ['#3498db', '#2ecc71', '#f39c12', '#9b59b6']

    kpi_cards = []
    for i, p in enumerate(periods):
        lbl = _label(p)
        rev = combined[combined['Period'] == lbl]['Value'].sum()
        kpi_cards.append(html.Div([
            html.Div(style={'height': '5px', 'backgroundColor': palette[i % 4],
                            'borderTopLeftRadius': '6px', 'borderTopRightRadius': '6px'}),
            html.Div([
                html.H4(lbl, style={'color': '#95a5a6', 'fontSize': '12px', 'fontWeight': 'bold',
                                    'textAlign': 'center', 'margin': '10px 0 4px'}),
                html.H2(f"Ksh {rev:,.0f}", style={'margin': '0 0 10px', 'color': '#2c3e50',
                                                    'fontSize': '22px', 'textAlign': 'center'}),
            ])
        ], style={'backgroundColor': 'white', 'borderRadius': '6px', 'flex': '1',
                  'border': '1px solid #eee', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)'}))

    top_custs = combined.groupby(cust_col)['Value'].sum().nlargest(15).index.tolist()
    plot_df   = combined[combined[cust_col].isin(top_custs)].sort_values('Value', ascending=True)
    hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")

    fig_bar = go.Figure()
    for i, p in enumerate(periods):
        lbl = _label(p)
        sub = plot_df[plot_df['Period'] == lbl]
        sub_a = (pd.DataFrame({cust_col: top_custs})
                 .merge(sub, on=cust_col, how='left').fillna(0))
        fig_bar.add_trace(go.Bar(
            y=sub_a[cust_col], x=sub_a['Value'], orientation='h',
            name=lbl, marker_color=palette[i % 4], opacity=0.85,
            customdata=np.column_stack([sub_a[vol_col],
                                        _safe_div(sub_a['Value'], sub_a[vol_col])]),
            hovertemplate=hover_tmpl,
        ))
    fig_bar.update_layout(title=f"Top 15 Customers — {len(periods)} Periods Side-by-Side",
                          barmode='group', margin=dict(l=170),
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    pivot = combined.pivot_table(index=cust_col, columns='Period', values='Value',
                                 aggfunc='sum').fillna(0)
    pivot = pivot.loc[pivot.sum(axis=1).nlargest(20).index]
    col_order = [_label(p) for p in periods]
    pivot = pivot[[c for c in col_order if c in pivot.columns]]
    fig_hm = px.imshow(pivot, aspect='auto', color_continuous_scale='Blues',
                       labels={'color': 'Revenue (Ksh)'},
                       title="Revenue Heatmap: Top 20 Customers × Periods")
    fig_hm.update_traces(hovertemplate="<b>%{y}</b> — %{x}<br>Rev: Ksh %{z:,.0f}<extra></extra>")
    fig_hm.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                         margin=dict(l=160))
    return kpi_cards, fig_bar, fig_hm, fig_bar, fig_hm


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — RANK MOVEMENT
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    [Output('rk-bump',  'figure'),
     Output('rk-table', 'data'),
     Output('snap-rk',  'data')],
    [Input('rk-base',    'value'), Input('rk-target',  'value'),
     Input('rk-mode',    'value'), Input('rk-topn',    'value'),
     Input('rk-segment', 'value')],
    State('auth-store', 'data')
)
def update_rank(base_p, target_p, mode, top_n, segment, auth_data):
    empty = go.Figure(), [], {}
    # Normalise: accept string (legacy) or list (multi-select)
    base_p   = [base_p]   if isinstance(base_p,   str) else (base_p   or [])
    target_p = [target_p] if isinstance(target_p, str) else (target_p or [])
    if not base_p or not target_p:
        return empty
    df = _get_df(auth_data)
    if df.empty:
        return empty
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    if not cust_col:
        return empty
    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year',
               'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col = col_map.get(mode, 'Month_Year')

    def _period_label(periods):
        lbls = [_qlabel(p) if mode == 'Quarterly' else p for p in periods]
        return lbls[0] if len(lbls) == 1 else f"{lbls[0]} – {lbls[-1]}"

    lbl_b = _period_label(base_p)
    lbl_t = _period_label(target_p)

    base_agg   = df[df[col].isin(base_p)].groupby(cust_col)['Value'].sum().reset_index()
    target_agg = df[df[col].isin(target_p)].groupby(cust_col)['Value'].sum().reset_index()
    merged = pd.merge(base_agg.rename(columns={'Value': 'Base_Rev'}),
                      target_agg.rename(columns={'Value': 'Target_Rev'}),
                      on=cust_col, how='outer').fillna(0)
    merged['Base_Rank']   = merged['Base_Rev'].rank(ascending=False, method='min').astype(int)
    merged['Target_Rank'] = merged['Target_Rev'].rank(ascending=False, method='min').astype(int)
    merged['Delta']       = merged['Base_Rank'] - merged['Target_Rank']
    merged['_max']        = merged[['Base_Rev', 'Target_Rev']].max(axis=1)
    top = merged.nlargest(int(top_n), '_max').sort_values('Target_Rank')

    def _arrow(d):
        if d > 0: return f"▲ {d}"
        if d < 0: return f"▼ {abs(d)}"
        return "–"

    table_data = [{'Customer': r[cust_col], 'Base_Rev': r['Base_Rev'],
                   'Target_Rev': r['Target_Rev'], 'Base_Rank': int(r['Base_Rank']),
                   'Target_Rank': int(r['Target_Rank']), 'Rank_Change': _arrow(r['Delta'])}
                  for _, r in top.iterrows()]

    palette = px.colors.qualitative.Set2
    fig = go.Figure()
    for i, (_, row) in enumerate(top.iterrows()):
        name  = row[cust_col]
        color = palette[i % len(palette)]
        fig.add_trace(go.Scatter(
            x=[lbl_b, lbl_t], y=[row['Base_Rank'], row['Target_Rank']],
            mode='lines+markers+text', name=name,
            line=dict(color=color, width=2), marker=dict(size=10, color=color),
            text=[str(int(row['Base_Rank'])), str(int(row['Target_Rank']))],
            textposition=['middle left', 'middle right'],
        ))
    fig.update_layout(title=f"Customer Rank Movement: {lbl_b} → {lbl_t} (Top {top_n})",
                      yaxis=dict(autorange='reversed', title='Rank (1 = highest revenue)',
                                 gridcolor='#eee'),
                      showlegend=False,
                      plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
    return fig, table_data, fig


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — FREQ / RECENCY
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    [Output('fr-scatter', 'figure'),
     Output('fr-bar',     'figure'),
     Output('fr-table',   'data'),
     Output('snap-fr',    'data')],
    [Input('fr-base',    'value'), Input('fr-target',  'value'),
     Input('fr-mode',    'value'), Input('fr-topn',    'value'),
     Input('fr-segment', 'value')],
    State('auth-store', 'data')
)
def update_freq(base_p, target_p, mode, top_n, segment, auth_data):
    empty = go.Figure(), go.Figure(), [], {}
    # Normalise: accept string (legacy) or list (multi-select)
    base_p   = [base_p]   if isinstance(base_p,   str) else (base_p   or [])
    target_p = [target_p] if isinstance(target_p, str) else (target_p or [])
    if not base_p or not target_p:
        return empty
    df = _get_df(auth_data)
    if df.empty:
        return empty
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    if not cust_col:
        return empty
    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year',
               'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col = col_map.get(mode, 'Month_Year')

    def _period_label(periods):
        lbls = [_qlabel(p) if mode == 'Quarterly' else p for p in periods]
        return lbls[0] if len(lbls) == 1 else f"{lbls[0]} – {lbls[-1]}"

    lbl_b = _period_label(base_p)
    lbl_t = _period_label(target_p)

    def _freq_stats(dff):
        if dff.empty:
            return pd.DataFrame(columns=[cust_col, 'Rev', 'Order_Days', 'Avg_Gap', 'Last_Date'])
        agg = dff.groupby(cust_col).agg(
            Rev       =('Value', 'sum'),
            Order_Days=('Date',  'nunique'),
            Last_Date =('Date',  'max'),
            First_Date=('Date',  'min'),
        ).reset_index()
        span = (agg['Last_Date'] - agg['First_Date']).dt.days
        agg['Avg_Gap'] = np.where(agg['Order_Days'] > 1, span / (agg['Order_Days'] - 1), 0)
        return agg

    df_t = _freq_stats(df[df[col].isin(target_p)])
    df_b = _freq_stats(df[df[col].isin(base_p)])
    global_last = df.groupby(cust_col)['Date'].max().reset_index()
    global_last.columns = [cust_col, 'Global_Last']
    global_last['Days_Since'] = (df['Date'].max() - global_last['Global_Last']).dt.days

    merged = pd.merge(
        df_t.rename(columns={'Rev': 'Rev_T', 'Order_Days': 'OD_T',
                             'Avg_Gap': 'AG_T', 'Last_Date': 'Last_T', 'First_Date': 'First_T'}),
        df_b.rename(columns={'Rev': 'Rev_B', 'Order_Days': 'OD_B',
                             'Avg_Gap': 'AG_B', 'Last_Date': 'Last_B', 'First_Date': 'First_B'}),
        on=cust_col, how='outer').fillna(0)
    merged = pd.merge(merged, global_last, on=cust_col, how='left')
    merged['_sort'] = merged[['Rev_T', 'Rev_B']].max(axis=1)
    top = merged.nlargest(int(top_n), '_sort')

    fig_scat = px.scatter(top, x='OD_B', y='OD_T',
                          size=top['Rev_T'].clip(lower=1), color='Days_Since',
                          hover_name=cust_col,
                          hover_data={'OD_B': True, 'OD_T': True,
                                      'Rev_T': ':,.0f', 'Rev_B': ':,.0f'},
                          color_continuous_scale='RdYlGn_r',
                          labels={'OD_B': f'Order Days ({lbl_b})',
                                  'OD_T': f'Order Days ({lbl_t})',
                                  'Days_Since': 'Days Since Last Purchase'},
                          title=f"Buying Frequency: {lbl_b} vs {lbl_t}")
    max_od = max(top['OD_B'].max(), top['OD_T'].max(), 1)
    fig_scat.add_shape(type='line', x0=0, y0=0, x1=max_od, y1=max_od,
                       line=dict(dash='dash', color='gray'))
    fig_scat.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    top_bar = top.sort_values('AG_T', ascending=True).head(20)
    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(y=top_bar[cust_col], x=top_bar['AG_B'], orientation='h',
                             name=f'Base ({lbl_b})', marker_color='#3498db', opacity=0.7,
                             hovertemplate="<b>%{y}</b><br>Avg Gap Base: %{x:.1f} days<extra></extra>"))
    fig_bar.add_trace(go.Bar(y=top_bar[cust_col], x=top_bar['AG_T'], orientation='h',
                             name=f'Target ({lbl_t})', marker_color='#e74c3c',
                             hovertemplate="<b>%{y}</b><br>Avg Gap Target: %{x:.1f} days<extra></extra>"))
    fig_bar.update_layout(title="Average Days Between Orders — Base vs Target",
                          barmode='group', margin=dict(l=160),
                          xaxis_title='Avg days between orders',
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    table_data = [{'Customer': r[cust_col],
                   'Last_Date': str(r['Global_Last'])[:10] if r['Global_Last'] != 0 else '–',
                   'Days_Since': int(r['Days_Since']) if r['Days_Since'] else 0,
                   'Order_Days_T': int(r['OD_T']), 'Order_Days_B': int(r['OD_B']),
                   'Avg_Gap_T': round(float(r['AG_T']), 1),
                   'Avg_Gap_B': round(float(r['AG_B']), 1),
                   'Rev_T': r['Rev_T'], 'Rev_B': r['Rev_B']}
                  for _, r in top.iterrows()]
    return fig_scat, fig_bar, table_data, fig_scat


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — SEASONALITY
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    [Output('sea-lines',   'figure'),
     Output('sea-heatmap', 'figure'),
     Output('snap-sea',    'data'),
     Output('snap-sea-hm', 'data')],
    [Input('sea-custs',   'value'),
     Input('sea-segment', 'value'),
     Input('sea-view',    'value')],
    State('auth-store', 'data')
)
def update_season(custs, segment, view, auth_data):
    empty = go.Figure(), go.Figure(), {}, {}
    df = _get_df(auth_data)
    if df.empty:
        return empty
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    if custs and cust_col:
        df = df[df[cust_col].isin(custs)]

    if view == 'monthly':
        df['_x'] = df['Date'].dt.month
        x_labels, x_tick_vals = ['Jan','Feb','Mar','Apr','May','Jun',
                                  'Jul','Aug','Sep','Oct','Nov','Dec'], list(range(1, 13))
        title_sfx = "Monthly Seasonal Pattern"
    else:
        df['_x'] = df['Date'].dt.quarter
        x_labels, x_tick_vals = ['Q1','Q2','Q3','Q4'], [1, 2, 3, 4]
        title_sfx = "Quarterly Seasonal Pattern"

    df['_year'] = df['Date'].dt.year.astype(str)
    trend  = df.groupby(['_year', '_x'])['Value'].sum().reset_index()
    years  = sorted(trend['_year'].unique())
    palette = px.colors.qualitative.Plotly

    fig_line = go.Figure()
    for i, yr in enumerate(years):
        sub = trend[trend['_year'] == yr].sort_values('_x')
        fig_line.add_trace(go.Scatter(x=sub['_x'], y=sub['Value'],
                                      mode='lines+markers', name=str(yr),
                                      line=dict(color=palette[i % len(palette)], width=2.5),
                                      marker=dict(size=7),
                                      hovertemplate=f"<b>{yr}</b><br>%{{x}}<br>Rev: Ksh %{{y:,.0f}}<extra></extra>"))
    lbl = (f"{', '.join(str(c) for c in custs[:3])}{'…' if len(custs or []) > 3 else ''}"
           if custs else "All Customers")
    fig_line.update_layout(title=f"{title_sfx} — {lbl}",
                           xaxis=dict(tickvals=x_tick_vals, ticktext=x_labels),
                           yaxis_title='Revenue (Ksh)',
                           plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    pivot = trend.pivot_table(index='_year', columns='_x', values='Value',
                              aggfunc='sum').fillna(0)
    pivot.columns = [x_labels[int(c) - 1] for c in pivot.columns]
    fig_hm = px.imshow(pivot, aspect='auto', color_continuous_scale='YlOrRd',
                       labels={'color': 'Revenue (Ksh)'},
                       title=f"Revenue Heatmap: Year × {'Month' if view == 'monthly' else 'Quarter'}")
    fig_hm.update_traces(hovertemplate="<b>%{y}</b> — %{x}<br>Rev: Ksh %{z:,.0f}<extra></extra>")
    fig_hm.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
    return fig_line, fig_hm, fig_line, fig_hm


@callback(Output('sea-decomp-container', 'style'), Input('sea-decomp-toggle', 'value'))
def _toggle_decomp(val):
    return {'display': 'block'} if 'show' in val else {'display': 'none'}


@callback(
    Output('sea-decomp-target', 'options'),
    [Input('sea-decomp-ctx', 'value')],
    State('auth-store', 'data')
)
def _decomp_target_opts(ctx_val, auth_data):
    if ctx_val == 'global':
        return [{'label': 'All Data', 'value': 'ALL'}]
    if ctx_val == 'seg':
        return [{'label': 'B2B', 'value': 'B2B'}, {'label': 'B2C', 'value': 'B2C'}]
    df = _get_df(auth_data)
    col_map = {'item': 'Name', 'cust': 'Customer Names'}
    col = next((c for c in [col_map.get(ctx_val), 'Items'] if c in df.columns), None)
    if not col:
        return []
    return [{'label': i, 'value': i} for i in sorted(df[col].dropna().unique())]


@callback(
    Output('sea-decomp-chart', 'figure'),
    [Input('sea-decomp-target', 'value'),
     Input('sea-decomp-ctx',   'value'),
     Input('sea-decomp-model', 'value'),
     Input('year-select',      'value')],
    State('auth-store', 'data')
)
def update_decomp(target, ctx_val, model, year, auth_data):
    if not target or not year:
        return go.Figure(layout={'title': 'Select a target above to run decomposition'})
    df = _get_df(auth_data)
    df_yr = df[df['Date'].dt.year == int(year)].copy()
    if target != 'ALL':
        if ctx_val == 'seg' and 'B2B' in df.columns:
            df_yr = df_yr[df_yr['B2B'].astype(str).str.strip().str.upper() == target.upper()]
        else:
            col_map = {'item': 'Name', 'cust': 'Customer Names'}
            col = next((c for c in [col_map.get(ctx_val), 'Items'] if c in df.columns), None)
            if col:
                df_yr = df_yr[df_yr[col].astype(str).str.strip().str.upper() == target.upper()]

    daily = df_yr.groupby('Date')['Value'].sum().reset_index()
    if daily.empty or len(daily) < 14:
        return go.Figure(layout={'title': 'Not enough data (minimum 14 days required)'})
    if not seasonal_decompose:
        return go.Figure(layout={'title': 'statsmodels not installed — pip install statsmodels'})

    idx   = pd.date_range(daily['Date'].min(), daily['Date'].max(), freq='D')
    daily = daily.set_index('Date').reindex(idx, fill_value=0)

    try:
        safe_vals = (daily['Value'].clip(lower=0) + 1) if model == 'multiplicative' else daily['Value'].fillna(0)
        decomp    = seasonal_decompose(safe_vals, model=model, period=7)
        sfx       = 'Multiplicative' if model == 'multiplicative' else 'Additive'
    except Exception:
        try:
            decomp = seasonal_decompose(daily['Value'].fillna(0), model='additive', period=7)
            sfx    = 'Additive (fallback)'
        except Exception as e2:
            return go.Figure(layout={'title': f'Decomposition failed: {e2}'})

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=("Trend", f"Seasonal ({sfx})", "Residuals"))
    fig.add_trace(go.Scatter(x=daily.index, y=decomp.trend,    name='Trend',
                             line=dict(color='#2980b9')), row=1, col=1)
    fig.add_trace(go.Scatter(x=daily.index, y=decomp.seasonal, name='Seasonality',
                             line=dict(color='#27ae60')), row=2, col=1)
    fig.add_trace(go.Scatter(x=daily.index, y=decomp.resid,    name='Residuals',
                             mode='markers', marker=dict(color='#7f8c8d', size=3)), row=3, col=1)
    fig.update_layout(title=f"Decomposition ({sfx}): {target}",
                      showlegend=False,
                      plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — WALLET SHARE
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    [Output('ws-heatmap', 'figure'),
     Output('ws-bar',     'figure'),
     Output('snap-ws',    'data')],
    [Input('ws-period',  'value'), Input('ws-mode',    'value'),
     Input('ws-hier',    'value'), Input('ws-topn',    'value'),
     Input('ws-segment', 'value')],
    State('auth-store', 'data')
)
def update_wallet(period, mode, h_col, top_n, segment, auth_data):
    empty = go.Figure(), go.Figure(), {}
    if not period:
        return empty
    df = _get_df(auth_data)
    if df.empty or h_col not in df.columns:
        return empty
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    if not cust_col:
        return empty
    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year',
               'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    dff = df[df[col_map.get(mode, 'Month_Year')] == period]
    if dff.empty:
        return empty

    top_custs  = dff.groupby(cust_col)['Value'].sum().nlargest(int(top_n)).index.tolist()
    dff        = dff[dff[cust_col].isin(top_custs)]
    pivot_abs  = dff.pivot_table(index=cust_col, columns=h_col, values='Value', aggfunc='sum').fillna(0)
    row_totals = pivot_abs.sum(axis=1)
    pivot_pct  = pivot_abs.div(row_totals, axis=0) * 100
    col_order  = pivot_abs.sum().sort_values(ascending=False).index.tolist()
    pivot_pct  = pivot_pct[col_order].loc[row_totals.sort_values(ascending=False).index]

    lbl_period = _qlabel(period) if mode == 'Quarterly' else period
    fig_hm = px.imshow(pivot_pct, aspect='auto', color_continuous_scale='Blues',
                       zmin=0, zmax=100, labels={'color': '% of Customer Spend'},
                       title=f"Wallet Share (%) — Top {top_n} Customers × {h_col} ({lbl_period})")
    fig_hm.update_traces(hovertemplate="<b>%{y}</b><br>%{x}<br>Share: %{z:.1f}%<extra></extra>")
    fig_hm.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                         margin=dict(l=160), xaxis_tickangle=-40)

    top_cat = pivot_pct.max(axis=1).reset_index()
    top_cat.columns = [cust_col, 'Top_Cat_Pct']
    top_cat['Top_Category'] = pivot_pct.idxmax(axis=1).values
    top_cat = top_cat.sort_values('Top_Cat_Pct', ascending=True)
    fig_bar = go.Figure(go.Bar(
        y=top_cat[cust_col], x=top_cat['Top_Cat_Pct'], orientation='h',
        marker_color=['#e74c3c' if v > 70 else ('#f39c12' if v > 40 else '#2ecc71')
                      for v in top_cat['Top_Cat_Pct']],
        customdata=top_cat['Top_Category'],
        hovertemplate="<b>%{y}</b><br>Top category: %{customdata}<br>Share: %{x:.1f}%<extra></extra>",
    ))
    fig_bar.update_layout(
        title="Concentration Risk — % of Revenue from Single Top Category",
        xaxis_title='% of revenue from top category (red > 70%, amber > 40%)',
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', margin=dict(l=160))
    fig_bar.add_vline(x=70, line_dash='dash', line_color='#e74c3c', opacity=0.5)
    fig_bar.add_vline(x=40, line_dash='dash', line_color='#f39c12', opacity=0.5)
    return fig_hm, fig_bar, fig_hm


# ─────────────────────────────────────────────────────────────────────────────
# TAB 6 — CUSTOMER HEALTH / RFM
# ─────────────────────────────────────────────────────────────────────────────

@callback(Output('rfm-data-store', 'data'),
          [Input('year-select', 'value')],
          [State('auth-store', 'data')])
def compute_rfm(year, auth_data):
    if not year:
        return []
    df = _get_df(auth_data)
    df_year = df[df['Date'].dt.year == int(year)]
    if df_year.empty:
        return []
    snapshot = df_year['Date'].max() + timedelta(days=1)
    return calculate_rfm(df_year, snapshot).to_dict('records')


@callback(
    [Output('rfm-bar-chart',   'figure'),
     Output('rfm-3d-chart',    'figure'),
     Output('rfm-table',       'data'),
     Output('rfm-auto-insight','children'),
     Output('snap-rfm-bar',    'data'),
     Output('snap-rfm-3d',     'data')],
    [Input('ecc-tabs',            'value'),
     Input('rfm-data-store',      'data'),
     Input('rfm-segment-filter',  'value'),
     Input('rfm-bar-chart',       'clickData'),
     Input('rfm-reset-btn',       'n_clicks')],
)
def update_rfm(tab, data, seg_filter, click_data, reset_click):
    if tab != 'tab-rfm':
        return [no_update] * 6
    if not data:
        return go.Figure(), go.Figure(), [], "No data available.", {}, {}
    rfm_df = pd.DataFrame(data)
    if seg_filter != 'All' and 'B2B' in rfm_df.columns:
        rfm_df = rfm_df[rfm_df['B2B'].astype(str).str.strip().str.upper() == seg_filter.upper()]

    segments  = ['Champions', 'Loyal Customers', 'Potential Loyalists', 'At Risk', 'Needs Attention', 'Lost']
    seg_counts = rfm_df['Segment'].value_counts().reindex(segments, fill_value=0).reset_index()
    seg_counts.columns = ['Segment', 'Count']

    fig_bar = px.bar(seg_counts, x='Segment', y='Count', color='Segment',
                     color_discrete_map=_SEG_COLORS, title="Customer Segments Distribution")
    fig_bar.update_layout(clickmode='event+select', showlegend=False,
                          margin=dict(l=20, r=20, t=40, b=20),
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    fig_3d = px.scatter_3d(rfm_df, x='Recency', y='Frequency', z='Monetary',
                           color='Segment', color_discrete_map=_SEG_COLORS,
                           hover_data=['Customer Names'], title="RFM 3D Cluster View")
    fig_3d.update_layout(margin=dict(l=0, r=0, b=0, t=30), paper_bgcolor='rgba(0,0,0,0)')

    at_risk_count = len(rfm_df[rfm_df['Segment'] == 'At Risk'])
    at_risk_rev   = rfm_df[rfm_df['Segment'] == 'At Risk']['Monetary'].sum()
    champ_count   = len(rfm_df[rfm_df['Segment'] == 'Champions'])
    insight = html.Div([
        html.Strong("🤖 Auto-Analyst Insight: "),
        html.Span("You have "),
        html.Strong(f"{at_risk_count} customers in the 'At Risk' segment", style={'color': '#e74c3c'}),
        html.Span(" representing "),
        html.Strong(f"Ksh {at_risk_rev:,.0f} in historical value.", style={'color': '#e74c3c'}),
        html.Span(f" Your {champ_count} 'Champions' should be targeted for referrals and premium upsells. "
                  "Click any bar to filter the Action Playbook below."),
    ])

    dff_table = rfm_df.copy()
    if ctx.triggered_id == 'rfm-bar-chart' and click_data:
        dff_table = dff_table[dff_table['Segment'] == click_data['points'][0]['x']]
    return fig_bar, fig_3d, dff_table.to_dict('records'), insight, fig_bar, fig_3d


@callback(Output('download-rfm-csv', 'data'),
          Input('rfm-download-btn', 'n_clicks'),
          State('rfm-table', 'data'),
          prevent_initial_call=True)
def download_rfm_hitlist(n_clicks, table_data):
    if not table_data:
        return dash.no_update
    return dcc.send_data_frame(pd.DataFrame(table_data).to_csv,
                               "Action_Playbook_Target_List.csv", index=False)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 7 — CROSS-SELLING
# ─────────────────────────────────────────────────────────────────────────────

@callback(Output('basket-item-select', 'options'),
          [Input('basket-hier', 'value')],
          [State('auth-store', 'data')])
def update_basket_opts(h_col, auth_data):
    df = _get_df(auth_data)
    return [{'label': i, 'value': i} for i in sorted(df[h_col].dropna().unique())] \
        if h_col in df.columns else []


@callback(
    [Output('basket-chart',              'figure'),
     Output('basket-auto-insight',       'children'),
     Output('basket-comparison-container','style'),
     Output('basket-compare-chart',      'figure'),
     Output('basket-pitch-insight',      'children'),
     Output('snap-basket-main',          'data'),
     Output('snap-basket-comp',          'data')],
    [Input('ecc-tabs',           'value'),
     Input('basket-item-select', 'value'),
     Input('year-select',        'value'),
     Input('basket-hier',        'value'),
     Input('basket-chart',       'clickData')],
    [State('auth-store', 'data')]
)
def update_basket_chart(tab, target, year, h_col, click_data, auth_data):
    if tab != 'tab-basket':
        return [no_update] * 7
    if not target:
        return go.Figure(), "Select an item to discover cross-selling opportunities.", \
               {'display': 'none'}, go.Figure(), "", {}, {}

    df_year = _get_df(auth_data)
    df_year = df_year[df_year['Date'].dt.year == int(year)].copy()

    item_counts  = df_year['Basket_ID'].nunique()
    item_support = df_year.groupby(h_col)['Basket_ID'].nunique() / item_counts
    target_baskets = df_year[df_year[h_col].astype(str).str.strip().str.upper() ==
                             str(target).strip().upper()]['Basket_ID'].unique()
    df_co = df_year[df_year['Basket_ID'].isin(target_baskets)]
    df_co = df_co[df_co[h_col].astype(str).str.strip().str.upper() != str(target).strip().upper()]

    co_counts = df_co.groupby(h_col)['Basket_ID'].nunique().reset_index()
    co_counts.columns = [h_col, 'Freq_AB']
    co_counts = co_counts[co_counts['Freq_AB'] >= 3]
    if co_counts.empty:
        return go.Figure(layout={'title': "No strong associations"}), \
               "Not enough data.", {'display': 'none'}, go.Figure(), "", {}, {}

    co_counts = co_counts.set_index(h_col)['Freq_AB']
    confidence = co_counts / len(target_baskets)
    lift = (confidence / item_support).dropna().sort_values(ascending=False).head(15)
    lift_df = lift.reset_index()
    lift_df.columns = ['Item', 'Lift']
    lift_df = lift_df.merge(co_counts.reset_index(), left_on='Item', right_on=h_col, how='left')

    fig = px.bar(lift_df, y='Item', x='Freq_AB', orientation='h',
                 title=f"Highly Associated Items to: {target}",
                 color='Lift', color_continuous_scale='Teal',
                 labels={'Freq_AB': 'Co-occurrence Count'})
    fig.update_traces(hovertemplate="<b>%{y}</b><br>Co-occurrences: %{x}<br>Lift: %{marker.color:.2f}<extra></extra>")
    fig.update_layout(yaxis={'categoryorder': 'total ascending'},
                      plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    top_assoc = lift_df.iloc[0]['Item']
    top_lift  = lift_df.iloc[0]['Lift']
    insight   = html.Div([
        html.Strong("🤖 Auto-Analyst Insight: "),
        html.Span(f"Customers who buy {target} are "),
        html.Strong(f"{top_lift:.1f}x more likely", style={'color': '#27ae60'}),
        html.Span(f" to also buy "), html.Strong(f"{top_assoc}."),
        html.Span(f" Click '{top_assoc}' in the chart to generate a Hit List."),
    ])

    comp_style, fig_comp, pitch = {'display': 'none'}, go.Figure(), ""
    if click_data:
        comp_item  = click_data['points'][0]['y']
        comp_style = {'display': 'block', 'marginTop': '20px', 'padding': '20px',
                      'backgroundColor': 'white', 'borderRadius': '8px', 'border': '1px solid #e0e0e0'}
        df_trend = df_year[df_year[h_col].isin([target, comp_item])]
        trend    = df_trend.groupby([pd.Grouper(key='Date', freq='ME'), h_col])['Value'].sum().reset_index()
        pivot_t  = trend.pivot(index='Date', columns=h_col, values='Value').fillna(0)
        corr     = pivot_t[target].corr(pivot_t[comp_item]) \
                   if target in pivot_t and comp_item in pivot_t else 0
        target_cust = set(df_year[df_year[h_col].astype(str).str.strip().str.upper() ==
                                  str(target).strip().upper()]['Customer Names'])
        comp_cust   = set(df_year[df_year[h_col].astype(str).str.strip().str.upper() ==
                                  str(comp_item).strip().upper()]['Customer Names'])
        missed = len(target_cust - comp_cust)
        pitch  = (f"There are {missed} unique customers who bought '{target}' but NEVER bought "
                  f"'{comp_item}'. Download the target list to execute the cross-sell campaign.")
        fig_comp = px.line(trend, x='Date', y='Value', color=h_col,
                           title=f"Trend Comparison: Correlation {corr:.2f}", markers=True)
        fig_comp.update_layout(hovermode='x unified',
                               plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    return fig, insight, comp_style, fig_comp, pitch, fig, fig_comp


@callback(Output('download-basket-csv', 'data'),
          Input('basket-download-btn', 'n_clicks'),
          [State('basket-item-select', 'value'), State('basket-chart', 'clickData'),
           State('year-select', 'value'), State('basket-hier', 'value'),
           State('auth-store', 'data')],
          prevent_initial_call=True)
def download_cross_sell(n_clicks, target, click_data, year, h_col, auth_data):
    if not target or not click_data:
        return dash.no_update
    comp_item   = click_data['points'][0]['y']
    df_year     = _get_df(auth_data)
    df_year     = df_year[df_year['Date'].dt.year == int(year)]
    target_cust = set(df_year[df_year[h_col].astype(str).str.strip().str.upper() ==
                              str(target).strip().upper()]['Customer Names'])
    comp_cust   = set(df_year[df_year[h_col].astype(str).str.strip().str.upper() ==
                              str(comp_item).strip().upper()]['Customer Names'])
    hit_list    = list(target_cust - comp_cust)
    return dcc.send_data_frame(
        pd.DataFrame({'Target Customers': hit_list, 'Action': f'Pitch {comp_item}'}).to_csv,
        f"CrossSell_{target}_to_{comp_item}.csv", index=False)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 8 — SALES TRAJECTORY
# ─────────────────────────────────────────────────────────────────────────────

@callback(Output('forecast-target-select', 'options'),
          [Input('forecast-context', 'value')],
          [State('auth-store', 'data')])
def update_forecast_opts(ctx_val, auth_data):
    if ctx_val == 'seg':
        return [{'label': 'B2B', 'value': 'B2B'}, {'label': 'B2C', 'value': 'B2C'}]
    df      = _get_df(auth_data)
    col_map = {'item': 'Name', 'cust': 'Customer Names', 'rep': 'Sales Rep'}
    col     = next((c for c in [col_map.get(ctx_val), 'Items'] if c in df.columns), None)
    if not col:
        return []
    return ([{'label': i, 'value': i} for i in sorted(df[col].dropna().unique())] +
            [{'label': 'Global', 'value': 'ALL'}])


@callback(
    [Output('forecast-chart',       'figure'),
     Output('forecast-auto-insight','children'),
     Output('snap-forecast',        'data')],
    [Input('ecc-tabs',                'value'),
     Input('forecast-target-select', 'value'),
     Input('forecast-context',       'value'),
     Input('year-select',            'value'),
     Input('forecast-goal-input',    'value')],
    [State('auth-store', 'data')]
)
def update_forecast_chart(tab, target, ctx_val, year, growth_target, auth_data):
    if tab != 'tab-forecast':
        return [no_update] * 3
    if not target:
        return go.Figure(), "Select a target to run projection.", {}

    df    = _get_df(auth_data)
    df_yr = df[df['Date'].dt.year == int(year)].copy()
    if target != 'ALL':
        if ctx_val == 'seg' and 'B2B' in df.columns:
            df_yr = df_yr[df_yr['B2B'].astype(str).str.strip().str.upper() == target.upper()]
        else:
            col_map = {'item': 'Name', 'cust': 'Customer Names', 'rep': 'Sales Rep'}
            col = next((c for c in [col_map.get(ctx_val), 'Items'] if c in df.columns), None)
            if col:
                df_yr = df_yr[df_yr[col].astype(str).str.strip().str.upper() == target.upper()]

    daily = df_yr.groupby('Date')['Value'].sum().reset_index()
    if daily.empty:
        return go.Figure(layout={'title': "No Data"}), "No data to analyze.", {}

    idx   = pd.date_range(daily['Date'].min(), daily['Date'].max(), freq='D')
    daily = (daily.set_index('Date').reindex(idx, fill_value=0).reset_index()
             .rename(columns={'index': 'Date'}).set_index('Date'))
    daily['MA30'] = daily['Value'].rolling(30).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily.index, y=daily['Value'],
                             name='Actual (Gross)', line=dict(color='#bdc3c7', width=1)))
    fig.add_trace(go.Scatter(x=daily.index, y=daily['MA30'],
                             name='30D Trend', line=dict(color='#27ae60', width=3)))

    insight_text = "Not enough data to calculate projection."
    train = daily.reset_index().tail(60)
    if len(train) > 5:
        x          = np.arange(len(train))
        p          = np.poly1d(np.polyfit(x, train['Value'], 1))
        future_d   = [daily.index.max() + timedelta(days=i) for i in range(1, 31)]
        proj_vals  = [max(0, v) for v in p(np.arange(len(train), len(train) + 30))]
        fig.add_trace(go.Scatter(x=future_d, y=proj_vals, name='30-Day Projection',
                                 line=dict(color='#c0392b', dash='dot', width=3)))
        last_actual   = daily['MA30'].iloc[-1] if not np.isnan(daily['MA30'].iloc[-1]) else daily['Value'].iloc[-1]
        if growth_target is not None:
            target_val = last_actual * (1 + growth_target / 100)
            fig.add_trace(go.Scatter(x=[daily.index.max(), future_d[-1]],
                                     y=[last_actual, target_val],
                                     name=f'Target (+{growth_target}%)',
                                     line=dict(color='green', dash='longdash', width=2)))
            gap = target_val - proj_vals[-1]
            if gap > 0:
                insight_text = html.Div([html.Strong("🤖 Auto-Analyst Insight: "),
                                         html.Span("Based on current trajectory you will fall short. Need an extra "),
                                         html.Strong(f"Ksh {gap:,.0f}/day", style={'color': '#c0392b'}),
                                         html.Span(" over 30 days to hit the target.")])
            else:
                insight_text = html.Div([html.Strong("🤖 Auto-Analyst Insight: "),
                                         html.Span("Projected to "),
                                         html.Strong(f"beat your +{growth_target}% goal",
                                                     style={'color': '#27ae60'}),
                                         html.Span(" if current volume holds.")])
    fig.update_layout(title=f"Sales Trajectory vs Goals: {target}", hovermode='x unified',
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
    return fig, insight_text, fig


# ─────────────────────────────────────────────────────────────────────────────
# TAB 9 — PRICING POWER / ELASTICITY
# ─────────────────────────────────────────────────────────────────────────────

@callback(Output('elasticity-item-select', 'options'),
          [Input('elasticity-hier', 'value')],
          [State('auth-store', 'data')])
def update_el_opts(h_col, auth_data):
    df = _get_df(auth_data)
    return ([{'label': i, 'value': i} for i in sorted(df[h_col].dropna().unique())] +
            [{'label': 'Global', 'value': 'ALL'}]) if h_col in df.columns else []


@callback(Output('elasticity-advanced-controls', 'style'),
          [Input('elasticity-advanced-toggle', 'value')])
def toggle_ea_adv(val):
    return ({'display': 'block', 'padding': '15px', 'backgroundColor': '#f8f9fa',
             'borderRadius': '8px', 'marginBottom': '20px'}
            if 'show' in val else {'display': 'none'})


@callback(
    [Output('elasticity-chart',             'figure'),
     Output('elasticity-auto-insight',      'children'),
     Output('sim-vol-card',                 'children'),
     Output('sim-rev-card',                 'children'),
     Output('elasticity-examples-container','children'),
     Output('simulator-container',          'style'),
     Output('simulator-cards',              'style'),
     Output('snap-elasticity',              'data')],
    [Input('ecc-tabs',                 'value'),
     Input('elasticity-item-select',   'value'),
     Input('elasticity-hier',          'value'),
     Input('year-select',              'value'),
     Input('price-simulator-slider',   'value'),
     Input('elasticity-options',       'value'),
     Input('elasticity-scale',         'value')],
    [State('auth-store', 'data')]
)
def update_elasticity_chart(tab, target, h_col, year, price_delta_pct, options, scale, auth_data):
    if tab != 'tab-elasticity':
        return [no_update] * 8
    df      = _get_df(auth_data)
    df_year = df[df['Date'].dt.year == int(year)]
    hide_sim       = {'display': 'none'}
    show_sim_cont  = {'width': '300px', 'display': 'inline-block', 'verticalAlign': 'middle',
                      'marginLeft': '20px', 'borderLeft': '1px solid #ddd', 'paddingLeft': '20px'}
    show_sim_cards = {'display': 'flex', 'gap': '20px', 'marginBottom': '20px'}
    is_log         = (scale == 'log')

    if not target or target == 'ALL':
        data = df_year.groupby(h_col).agg({'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
        data = data[data['QTY PCS'] > 0]
        data['Unit_Price'] = data['Value'] / data['QTY PCS']
        data = data[data['Unit_Price'] > 0.01]
        if 'outlier' in options and len(data) > 10:
            x_clean, _, _ = remove_outliers_iqr(data['QTY PCS'], data['Unit_Price'])
            data = data.loc[x_clean.index]

        fig = px.scatter(data, x='QTY PCS', y='Unit_Price', hover_name=h_col,
                         log_x=is_log, log_y=is_log,
                         title=f"Global Pricing Landscape ({h_col})")
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        insight = html.Div([html.Strong("🤖 Auto-Analyst Insight: "),
                            html.Span("Select a specific product to activate the ROI Simulator.")])

        # Mini demand curve examples
        stats_list, top_items = [], df_year[h_col].value_counts().head(50).index.tolist()
        for item in top_items:
            d = df_year[df_year[h_col] == item].groupby('Date').agg(
                {'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
            d = d[d['QTY PCS'] > 0]; d['Unit_Price'] = d['Value'] / d['QTY PCS']
            if len(d) < 5: continue
            _, _, _, r2, m = calc_regression(d['QTY PCS'], d['Unit_Price'], mode='power')
            if m is not None and m < 0:
                e_d = 1 / m
                if e_d >= -15: stats_list.append({'Item': item, 'Elasticity (E_d)': e_d, 'R²': r2})

        examples_div = html.Div()
        if len(stats_list) >= 2:
            df_s = pd.DataFrame(stats_list).sort_values('Elasticity (E_d)', ascending=True)
            elastic_item   = df_s.iloc[0]['Item']
            inelastic_item = df_s.iloc[-1]['Item']

            def _mini(item_name, title, color):
                di = df_year[df_year[h_col] == item_name].groupby('Date').agg(
                    {'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
                di = di[di['QTY PCS'] > 0]; di['Unit_Price'] = di['Value'] / di['QTY PCS']
                f  = px.scatter(di, x='QTY PCS', y='Unit_Price', log_x=is_log, log_y=is_log,
                                title=f"{title}: {item_name}")
                px_d, py_d, _, _, _ = calc_regression(di['QTY PCS'], di['Unit_Price'], mode='power')
                if px_d is not None:
                    f.add_trace(go.Scatter(x=px_d, y=py_d, mode='lines',
                                           line=dict(color=color, width=3)))
                f.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                                showlegend=False, margin=dict(l=0, r=0, t=40, b=0))
                return html.Div([dcc.Graph(figure=f, style={'height': '350px'})],
                                style={'flex': '1', 'backgroundColor': 'white', 'padding': '10px',
                                       'borderRadius': '8px', 'border': '1px solid #e0e0e0'})

            examples_div = html.Div([
                html.H3("Real-World Demand Curve Examples",
                        style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '20px'}),
                html.Div([_mini(elastic_item,   "Most Elastic",   "#c0392b"),
                          _mini(inelastic_item, "Least Elastic",  "#27ae60")],
                         style={'display': 'flex', 'gap': '20px'}),
            ])

        return fig, insight, html.Div(), html.Div(), examples_div, hide_sim, hide_sim, fig

    # Single item
    dff  = df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(target).strip().upper()]
    data = dff.groupby('Date').agg({'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
    data = data[data['QTY PCS'] > 0]; data['Unit_Price'] = data['Value'] / data['QTY PCS']
    data = data[data['Unit_Price'] > 0.01]

    if 'outlier' in options and len(data) > 10:
        x_clean, _, removed = remove_outliers_iqr(data['QTY PCS'], data['Unit_Price'])
        data = data.loc[x_clean.index]

    fig = px.scatter(data, x='QTY PCS', y='Unit_Price', hover_name='Date',
                     log_x=is_log, log_y=is_log, title=f"Demand Curve: {target}")
    insight, vol_card, rev_card = "Not enough clean data.", html.H3("N/A"), html.H3("N/A")

    if 'linear' in options and len(data) > 2:
        lx, ly, leq, _, _ = calc_regression(data['QTY PCS'], data['Unit_Price'], mode='linear')
        if lx is not None:
            fig.add_trace(go.Scatter(x=lx, y=ly, mode='lines', name=f"Linear: {leq}",
                                     line=dict(color='green')))

    if len(data) > 2:
        px_d, py_d, peq, _, m = calc_regression(data['QTY PCS'], data['Unit_Price'], mode='power')
        if px_d is not None and m is not None:
            if 'power' in options:
                fig.add_trace(go.Scatter(x=px_d, y=py_d, mode='lines', name="Demand Curve",
                                         line=dict(color='red', width=3)))
            elasticity = 0 if m == 0 else max(-15, min(15, 1 / m))
            delta_p    = price_delta_pct / 100.0
            delta_v    = delta_p * elasticity
            base_p_val = data['Unit_Price'].median()
            base_v_val = data['QTY PCS'].median()
            new_rev    = base_p_val * (1 + delta_p) * base_v_val * (1 + delta_v)
            rev_impact = new_rev - base_p_val * base_v_val

            vol_color = '#27ae60' if delta_v >= 0 else '#c0392b'
            rev_color = '#27ae60' if rev_impact >= 0 else '#c0392b'
            vol_card  = html.Div([
                html.H4("Predicted Volume Impact",
                        style={'color': '#7f8c8d', 'fontSize': '12px', 'textTransform': 'uppercase'}),
                html.H2(f"{'+' if delta_v > 0 else ''}{delta_v * 100:.1f}%",
                        style={'color': vol_color, 'margin': '10px 0'}),
            ])
            rev_card = html.Div([
                html.H4("Predicted Revenue Impact (Per Transaction)",
                        style={'color': '#7f8c8d', 'fontSize': '12px', 'textTransform': 'uppercase'}),
                html.H2(f"{'+' if rev_impact > 0 else ''}Ksh {rev_impact:,.0f}",
                        style={'color': rev_color, 'margin': '10px 0'}),
            ])
            if m > 0:
                e_text = html.Strong("⚠️ Spurious Data (Positive Correlation).", style={'color': '#f39c12'})
                strat  = "Higher prices appear alongside higher volumes — likely a seasonal/demand effect. Use cautiously."
            elif abs(elasticity) > 1:
                e_text = html.Strong("Highly Elastic (Price Sensitive).", style={'color': '#c0392b'})
                strat  = "Customers are price-sensitive. Small discounts drive disproportionate volume gains."
            else:
                e_text = html.Strong("Inelastic (Price Insensitive).", style={'color': '#27ae60'})
                strat  = "Customers buy regardless of small price changes. You can safely nudge prices up to protect margins."
            insight = html.Div([html.Strong("🤖 Auto-Analyst Insight: "),
                                 html.Span("This product is "), e_text, html.Br(),
                                 html.Span(strat)])

    fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
    return fig, insight, vol_card, rev_card, html.Div(), show_sim_cont, show_sim_cards, fig


# ─────────────────────────────────────────────────────────────────────────────
# SNAPSHOT — all charts → single HTML report
# ─────────────────────────────────────────────────────────────────────────────

@callback(
    Output('ecc-dl', 'data'),
    Input('ecc-snap', 'n_clicks'),
    [State('snap-mp',         'data'), State('snap-mp-hm',       'data'),
     State('snap-rk',         'data'), State('snap-fr',          'data'),
     State('snap-sea',        'data'), State('snap-sea-hm',      'data'),
     State('snap-ws',         'data'),
     State('snap-rfm-bar',    'data'), State('snap-rfm-3d',      'data'),
     State('snap-basket-main','data'), State('snap-basket-comp', 'data'),
     State('snap-forecast',   'data'), State('snap-elasticity',  'data'),
     State('ecc-tabs',        'value')],
    prevent_initial_call=True
)
def download_ecc_snapshot(n, *args):
    figs = args[:-1]   # all stores
    tab  = args[-1]    # active tab name

    tab_labels = {
        'tab-multiperiod': 'Multi-Period Comparison',
        'tab-rank':        'Rank Movement',
        'tab-freq':        'Frequency / Recency',
        'tab-season':      'Seasonality',
        'tab-wallet':      'Wallet Share',
        'tab-rfm':         'Customer Health & RFM',
        'tab-basket':      'Cross-Selling Opportunities',
        'tab-forecast':    'Sales Trajectory',
        'tab-elasticity':  'Pricing Power',
    }
    title = tab_labels.get(tab, 'Advanced Customer Analytics')

    html_out = (
        f"<html><head><title>{title} Snapshot</title></head>"
        f"<body style='font-family:sans-serif;padding:20px;'>"
        f"<h1 style='text-align:center;'>{title} — Snapshot</h1>"
        f"<p style='text-align:center;color:#7f8c8d;'>"
        f"Generated {pd.Timestamp.now().strftime('%d %b %Y %H:%M')}</p>"
    )
    for fig_data in figs:
        if fig_data:
            html_out += (
                f"<div style='margin-bottom:30px;box-shadow:0 4px 6px rgba(0,0,0,0.1);"
                f"padding:10px;background:white;'>"
                f"{pio.to_html(go.Figure(fig_data), full_html=False, include_plotlyjs='cdn')}"
                f"</div>"
            )
    html_out += "</body></html>"
    return dict(content=html_out,
                filename=f"analytics_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")
