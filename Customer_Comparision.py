import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine

import dash
from dash import html, dcc, Input, Output, State, dash_table, ctx, no_update, callback
import plotly.express as px
import plotly.graph_objects as go
from dash.dash_table.Format import Format, Scheme, Group
import pandas as pd
import numpy as np
import plotly.io as pio
from data_loader import df_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security

# ─────────────────────────────────────────────
# DATA HELPERS
# ─────────────────────────────────────────────

def get_secure_cc_df(auth_data):
    _df, _, _, _ = get_latest_data()
    df = enforce_row_level_security(_df, auth_data)
    if not df.empty:
        df['Month_Year'] = df['Date'].dt.to_period('M').astype(str)
        df['Year'] = df['Date'].dt.year.astype(str)
        df['Week_Year'] = df['Date'].dt.to_period('W').dt.start_time.dt.strftime('%Y-%m-%d')
        # Quarter format: '2024-Q1' sorts correctly chronologically
        df['Quarter_Year'] = df['Date'].dt.year.astype(str) + '-Q' + df['Date'].dt.quarter.astype(str)
    return df


def _safe_price(value_series, vol_series):
    """Division that silences inf and NaN into 0."""
    return (value_series / vol_series).replace([np.inf, -np.inf], np.nan).fillna(0)


def _apply_ytd_mask(df_ref, anchor_date):
    """Clips a previous-period dataframe to the same month/day as anchor_date (YTD parity)."""
    mask = (df_ref['Date'].dt.month < anchor_date.month) | \
           ((df_ref['Date'].dt.month == anchor_date.month) & (df_ref['Date'].dt.day <= anchor_date.day))
    return df_ref[mask]


def _get_period_dfs(df, col, mode, m_target_val, m_base_val):
    """
    Returns (dff_target, dff_ref) after applying YTD parity when mode is 'Yearly'.
    Accepts both single string values and lists of values (multi-select support).
    """
    tgt_list  = [m_target_val] if isinstance(m_target_val, str) else (m_target_val or [])
    base_list = [m_base_val]   if isinstance(m_base_val,   str) else (m_base_val   or [])

    dff_target  = df[df[col].isin(tgt_list)]
    dff_ref_raw = df[df[col].isin(base_list)]

    # YTD parity only makes sense for a single Yearly comparison
    if mode == 'Yearly' and len(tgt_list) == 1 and not dff_target.empty:
        anchor_date = dff_target['Date'].max()
        dff_ref = _apply_ytd_mask(dff_ref_raw, anchor_date)
    else:
        dff_ref = dff_ref_raw

    return dff_target, dff_ref


def _quarter_label(q_val):
    """Converts '2025-Q2' → 'Q2 2025' for display."""
    parts = q_val.split('-Q')
    return f"Q{parts[1]} {parts[0]}" if len(parts) == 2 else q_val


def _period_label(val, mode):
    """Return a readable label for a period value that may be a string or list."""
    lst = [val] if isinstance(val, str) else (val or [])
    if not lst:
        return '–'
    lbls = [_quarter_label(v) if mode == 'Quarterly' else v for v in lst]
    lbl = lbls[0] if len(lbls) == 1 else f"{lbls[0]} – {lbls[-1]}"
    if len(lst) == 1:
        today = pd.Timestamp.now()
        v = str(lst[0])
        if mode == 'Monthly' and v == today.to_period('M').strftime('%Y-%m'):
            lbl += ' (MTD)'
        elif mode == 'Quarterly' and v == f"{today.year}-Q{today.quarter}":
            lbl += ' (QTD)'
        elif mode == 'Yearly' and v == str(today.year):
            lbl += ' (YTD)'
        elif mode == 'Weekly':
            try:
                week_start = pd.Timestamp(v)
                curr_week_start = (today - pd.Timedelta(days=today.dayofweek)).normalize()
                if week_start.date() == curr_week_start.date():
                    lbl += ' (WTD)'
            except Exception:
                pass
    return lbl


# ─────────────────────────────────────────────
# V1-STYLE KPI CARD (no drilldown)
# ─────────────────────────────────────────────

def _cc_kpi(title, curr_v, prev_v=None, prefix="", suffix="", color='#3498db'):
    """Coloured-top-border KPI card matching the Executive Dashboard style."""
    if curr_v >= 1_000_000:
        val_str = f"{prefix}{curr_v / 1e6:.2f}M{suffix}"
    elif curr_v >= 1_000:
        val_str = f"{prefix}{curr_v:,.0f}{suffix}"
    else:
        val_str = f"{prefix}{curr_v:,.2f}{suffix}"

    delta_el = html.Div(style={'height': '12px'})
    if prev_v is not None and prev_v != 0:
        delta   = (curr_v - prev_v) / prev_v * 100
        sym     = "▲" if delta >= 0 else "▼"
        d_color = "#27ae60" if delta >= 0 else "#c0392b"
        delta_el = html.P(f"{sym} {abs(delta):.1f}% vs base",
                          style={'color': d_color, 'fontWeight': 'bold',
                                 'fontSize': '12px', 'textAlign': 'center',
                                 'margin': '0 0 10px 0'})

    return html.Div([
        html.Div(style={'height': '5px', 'backgroundColor': color,
                        'borderTopLeftRadius': '8px', 'borderTopRightRadius': '8px'}),
        html.Div([
            html.H4(title, style={'color': '#95a5a6', 'fontSize': '12px', 'fontWeight': 'bold',
                                  'textAlign': 'center', 'margin': '12px 0 5px 0',
                                  'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
            html.H2(val_str, style={'margin': '0 0 5px 0', 'color': '#2c3e50',
                                    'fontSize': '24px', 'textAlign': 'center'}),
            delta_el,
        ], style={'padding': '0 10px'}),
    ], style={'backgroundColor': 'white', 'borderRadius': '8px', 'flex': '1',
              'margin': '0 6px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
              'border': '1px solid #eee', 'minWidth': '130px'})


# ─────────────────────────────────────────────
# SHARED STYLE
# ─────────────────────────────────────────────

_CONTROLS = {
    'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
    'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '8px',
    'marginBottom': '20px', 'border': f'1px solid {s.COLORS["border"]}', 'flexWrap': 'wrap', 'gap': '10px'
}

_MODE_OPTIONS = [
    {'label': ' Weekly',    'value': 'Weekly'},
    {'label': ' Monthly',   'value': 'Monthly'},
    {'label': ' Quarterly', 'value': 'Quarterly'},
    {'label': ' Yearly',    'value': 'Yearly'},
]

_HIER_OPTIONS = [
    {'label': 'Grp',  'value': 'Items 2'},
    {'label': 'Sub',  'value': 'Items'},
    {'label': 'Item', 'value': 'Name'},
]

_LBL = {'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}
_DD  = {'display': 'inline-block'}
_BTN_ALL  = {'fontSize': '10px', 'padding': '2px 8px', 'cursor': 'pointer',
             'border': '1px solid #82e0aa', 'borderRadius': '3px',
             'backgroundColor': '#d5f5e3', 'color': '#1e8449', 'marginLeft': '3px'}
_BTN_NONE = {'fontSize': '10px', 'padding': '2px 8px', 'cursor': 'pointer',
             'border': '1px solid #f1948a', 'borderRadius': '3px',
             'backgroundColor': '#fadbd8', 'color': '#922b21', 'marginLeft': '3px'}
_DD_FLEX  = {'display': 'inline-flex', 'alignItems': 'center'}

_TABLE_STYLE = dict(
    style_header={'backgroundColor': '#fadbd8', 'fontWeight': 'bold', 'border': 'none'},
    style_cell={'textAlign': 'left', 'fontFamily': 'sans-serif', 'fontSize': '12px', 'padding': '10px'},
    style_table={'overflowX': 'auto'}
)
_TABLE_STYLE_GREEN = dict(
    style_header={'backgroundColor': '#d5f5e3', 'fontWeight': 'bold', 'border': 'none'},
    style_cell={'textAlign': 'left', 'fontFamily': 'sans-serif', 'fontSize': '12px', 'padding': '10px'},
    style_table={'overflowX': 'auto'}
)


# ─────────────────────────────────────────────
# TAB LAYOUTS
# ─────────────────────────────────────────────

layout_portfolio = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='port-period-mode', options=_MODE_OPTIONS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("BASE:", style=_LBL),
                  dcc.Dropdown(id='month-target', multi=True,
                               placeholder="Select base period(s)…",
                               style={'width': '260px', 'minWidth': '180px'}),
                  html.Button("All",  id='month-target-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='month-target-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
        html.Div([html.Span("TARGET:", style=_LBL),
                  dcc.Dropdown(id='month-ref', multi=True,
                               placeholder="Select target period(s)…",
                               style={'width': '260px', 'minWidth': '180px'}),
                  html.Button("All",  id='month-ref-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='month-ref-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
        html.Div([html.Span("SEGMENT:", style=_LBL),
                  dcc.Dropdown(id='cc-segment-filter',
                               options=[{'label': 'All', 'value': 'All'}, {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', clearable=False, style={'width': '100px'})], style=_DD),
    ], style=_CONTROLS),

    html.Div(id='kpi-container', style={'display': 'flex', 'gap': '10px', 'marginBottom': '20px', 'flexWrap': 'wrap'}),

    html.Div([dcc.Graph(id='chart-top-customers', style={'height': '80vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='retention-scatter',   style={'height': '80vh'})], style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
    html.Div([dcc.Graph(id='customer-flow-chart', style={'height': '80vh'})], style={**s.CHART_CONTAINER, 'marginTop': '20px'}),

    html.Div([
        html.H3("Biggest Movers (Customers)", style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '20px'}),
        html.Div([
            html.Div([html.H4(id='header-losers-port', children="❌ Losers", style={'color': '#c0392b', 'textAlign': 'center'}),
                      dash_table.DataTable(id='portfolio-losers', **_TABLE_STYLE)],
                     style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
            html.Div([html.H4(id='header-gainers-port', children="✅ Gainers", style={'color': '#27ae60', 'textAlign': 'center'}),
                      dash_table.DataTable(id='portfolio-gainers', **_TABLE_STYLE_GREEN)],
                     style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top', 'float': 'right'})
        ])
    ], style={**s.SECTION, 'marginTop': '20px'}),

    html.Div(id='port-drill-wrap', style={'display': 'none', 'padding': '20px', 'backgroundColor': 'white',
                                          'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}',
                                          'marginTop': '20px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)'}, children=[
        html.Div([
            html.H3(id='port-drill-title', style={'display': 'inline', 'color': '#2c3e50'}),
            html.Button("✕ Close", id='port-drill-close',
                        style={'fontSize': '12px', 'padding': '5px 14px', 'cursor': 'pointer',
                               'backgroundColor': '#e74c3c', 'color': 'white', 'border': 'none',
                               'borderRadius': '4px', 'marginLeft': '15px'}),
        ], style={'textAlign': 'center', 'marginBottom': '15px'}),
        html.P("Click a customer bar to see what they buy in this period.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginBottom': '5px'}),
        dcc.Graph(id='port-drill-chart', style={'height': '55vh'})
    ]),

    dcc.Store(id='store-retention'), dcc.Store(id='store-flow'), dcc.Store(id='store-top-customers'),
])


layout_single = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='single-period-mode', options=_MODE_OPTIONS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("LEVEL:", style=_LBL),
                  dcc.RadioItems(id='cc-hierarchy-select', options=_HIER_OPTIONS, value='Items',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([dcc.Dropdown(id='single-cust-select', placeholder="Select Customer(s)...",
                               multi=True, style={'width': '300px'}),
                  html.Button("All",  id='single-cust-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='single-cust-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
        html.Div([html.Span("BASE:", style=_LBL),
                  dcc.Dropdown(id='single-month-target', multi=True,
                               placeholder="Select base period(s)…",
                               style={'width': '260px', 'minWidth': '180px'}),
                  html.Button("All",  id='single-mtarget-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='single-mtarget-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
        html.Div([html.Span("TARGET:", style=_LBL),
                  dcc.Dropdown(id='single-month-ref', multi=True,
                               placeholder="Select target period(s)…",
                               style={'width': '260px', 'minWidth': '180px'}),
                  html.Button("All",  id='single-mref-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='single-mref-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
    ], style=_CONTROLS),

    html.Div(id='single-kpi-row', style={'display': 'flex', 'gap': '10px', 'marginBottom': '20px', 'flexWrap': 'wrap'}),

    html.Div([dcc.Graph(id='chart-grouped-comparison', style={'height': '83vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='single-item-scatter',      style={'height': '80vh'})], style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
    html.Div([dcc.Graph(id='single-waterfall',         style={'height': '80vh'})], style={**s.CHART_CONTAINER, 'marginTop': '20px'}),

    html.Div([
        html.H3("Biggest Movers (Items)", style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '20px'}),
        html.Div([
            html.Div([html.H4(id='header-losers',  children="❌ Losers",  style={'color': '#c0392b', 'textAlign': 'center'}),
                      dash_table.DataTable(id='single-losers', **_TABLE_STYLE)],
                     style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
            html.Div([html.H4(id='header-gainers', children="✅ Gainers", style={'color': '#27ae60', 'textAlign': 'center'}),
                      dash_table.DataTable(id='single-gainers', **_TABLE_STYLE_GREEN)],
                     style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top', 'float': 'right'})
        ])
    ], style={**s.SECTION, 'marginTop': '20px'}),

    html.Div(id='single-drill-wrap', style={'display': 'none', 'padding': '20px', 'backgroundColor': 'white',
                                            'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}',
                                            'marginTop': '20px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)'}, children=[
        html.Div([
            html.H3(id='single-drill-title', style={'display': 'inline', 'color': '#2c3e50'}),
            html.Button("✕ Close", id='single-drill-close',
                        style={'fontSize': '12px', 'padding': '5px 14px', 'cursor': 'pointer',
                               'backgroundColor': '#e74c3c', 'color': 'white', 'border': 'none',
                               'borderRadius': '4px', 'marginLeft': '15px'}),
        ], style={'textAlign': 'center', 'marginBottom': '15px'}),
        html.P("Single customer: click an item bar to see its period-by-period trend. Multi-customer: click a customer bar to see their product mix.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginBottom': '5px'}),
        dcc.Graph(id='single-drill-chart', style={'height': '55vh'})
    ]),

    dcc.Store(id='store-grouped-comp'), dcc.Store(id='store-single-scatter'), dcc.Store(id='store-single-waterfall'),
])


layout_product = html.Div([
    html.Div([
        html.Div([html.Span("MODE:", style=_LBL),
                  dcc.RadioItems(id='prod-period-mode', options=_MODE_OPTIONS, value='Monthly',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([html.Span("LEVEL:", style=_LBL),
                  dcc.RadioItems(id='prod-hier-select', options=_HIER_OPTIONS, value='Items',
                                 inline=True, style={'fontSize': '14px'})], style=_DD),
        html.Div([dcc.Dropdown(id='prod-item-select', placeholder="Select Product(s)...", multi=True, style={'width': '280px'}),
                  html.Button("All",  id='prod-item-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='prod-item-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
        html.Div([html.Span("BASE:", style=_LBL),
                  dcc.Dropdown(id='prod-month-target', multi=True,
                               placeholder="Select base period(s)…",
                               style={'width': '260px', 'minWidth': '180px'}),
                  html.Button("All",  id='prod-mtarget-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='prod-mtarget-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
        html.Div([html.Span("TARGET:", style=_LBL),
                  dcc.Dropdown(id='prod-month-ref', multi=True,
                               placeholder="Select target period(s)…",
                               style={'width': '260px', 'minWidth': '180px'}),
                  html.Button("All",  id='prod-mref-all',  n_clicks=0, style=_BTN_ALL),
                  html.Button("None", id='prod-mref-none', n_clicks=0, style=_BTN_NONE),
                  ], style=_DD_FLEX),
    ], style=_CONTROLS),

    html.Div(id='prod-kpi-row', style={'display': 'flex', 'gap': '10px', 'marginBottom': '20px', 'flexWrap': 'wrap'}),

    html.Div([dcc.Graph(id='chart-prod-customers',    style={'height': '80vh'})], style=s.CHART_CONTAINER),
    html.Div([dcc.Graph(id='prod-retention-scatter',  style={'height': '80vh'})], style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
    html.Div([dcc.Graph(id='prod-flow-chart',         style={'height': '80vh'})], style={**s.CHART_CONTAINER, 'marginTop': '20px'}),

    html.Div([
        html.H3("Biggest Customer Movers for this Product",
                style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '20px'}),
        html.Div([
            html.Div([html.H4(id='header-prod-losers',  children="❌ Losers",  style={'color': '#c0392b', 'textAlign': 'center'}),
                      dash_table.DataTable(id='prod-losers', **_TABLE_STYLE)],
                     style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
            html.Div([html.H4(id='header-prod-gainers', children="✅ Gainers", style={'color': '#27ae60', 'textAlign': 'center'}),
                      dash_table.DataTable(id='prod-gainers', **_TABLE_STYLE_GREEN)],
                     style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top', 'float': 'right'})
        ])
    ], style={**s.SECTION, 'marginTop': '20px'}),

    html.Div(id='prod-drill-wrap', style={'display': 'none', 'padding': '20px', 'backgroundColor': 'white',
                                          'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}',
                                          'marginTop': '20px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)'}, children=[
        html.Div([
            html.H3(id='prod-drill-title', style={'display': 'inline', 'color': '#2c3e50'}),
            html.Button("✕ Close", id='prod-drill-close',
                        style={'fontSize': '12px', 'padding': '5px 14px', 'cursor': 'pointer',
                               'backgroundColor': '#e74c3c', 'color': 'white', 'border': 'none',
                               'borderRadius': '4px', 'marginLeft': '15px'}),
        ], style={'textAlign': 'center', 'marginBottom': '15px'}),
        html.P("Click a customer bar to see their period-by-period trend for this product.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginBottom': '5px'}),
        dcc.Graph(id='prod-drill-chart', style={'height': '55vh'})
    ]),

    dcc.Store(id='store-prod-bar'), dcc.Store(id='store-prod-scatter'), dcc.Store(id='store-prod-waterfall'),
])


# ─────────────────────────────────────────────
# ROOT LAYOUT
# ─────────────────────────────────────────────

layout = html.Div([
    dcc.Download(id="customer-comparison-download-snapshot"),
    html.Div([
        html.H1("Customer Analysis & Comparison", style=s.HEADER_TITLE),
        html.Div([
            html.Div([
                dcc.Tabs(id="tabs-nav", value='tab-portfolio', children=[
                    dcc.Tab(label='Portfolio Overview',   value='tab-portfolio'),
                    dcc.Tab(label='Customer Deep Dive',   value='tab-single'),
                    dcc.Tab(label='Product Comparison',   value='tab-product'),
                ], style={'fontWeight': 'bold', 'fontSize': '16px'})
            ], style={'flex': 1}),
            html.Div([html.Button("📸 Snapshot", id="customer_comparison-btn-snapshot", style=s.BTN_STYLE)],
                     style={'marginLeft': '20px'})
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '20px'})
    ], style=s.SECTION),
    html.Div([
        html.Div(layout_portfolio, id='container-portfolio'),
        html.Div(layout_single,    id='container-single'),
        html.Div(layout_product,   id='container-product'),
    ])
], style=s.MAIN_CONTAINER)


# ─────────────────────────────────────────────
# CALLBACKS
# ─────────────────────────────────────────────

@callback(
    [Output('container-portfolio', 'style'),
     Output('container-single',    'style'),
     Output('container-product',   'style')],
    [Input('tabs-nav', 'value')]
)
def toggle_tabs(tab):
    show, hide = {'display': 'block'}, {'display': 'none'}
    if tab == 'tab-portfolio': return show, hide, hide
    if tab == 'tab-single':    return hide, show, hide
    return hide, hide, show


@callback(
    [Output('month-target',       'options'), Output('month-target',       'value'),
     Output('month-ref',          'options'), Output('month-ref',          'value'),
     Output('single-month-target','options'), Output('single-month-target','value'),
     Output('single-month-ref',   'options'), Output('single-month-ref',   'value'),
     Output('prod-month-target',  'options'), Output('prod-month-target',  'value'),
     Output('prod-month-ref',     'options'), Output('prod-month-ref',     'value')],
    [Input('port-period-mode',   'value'),
     Input('single-period-mode', 'value'),
     Input('prod-period-mode',   'value')],
    [State('auth-store', 'data')]
)
def update_options(p_mode, s_mode, pr_mode, auth_data):
    df = get_secure_cc_df(auth_data)
    if df.empty:
        return [], None, [], None, [], None, [], None, [], None, [], None

    def get_opts(mode):
        if mode == 'Weekly':
            return [{'label': w, 'value': w} for w in sorted(df['Week_Year'].unique(), reverse=True)]
        if mode == 'Monthly':
            return [{'label': m, 'value': m} for m in sorted(df['Month_Year'].unique(), reverse=True)]
        if mode == 'Quarterly':
            quarters = sorted(df['Quarter_Year'].unique(), reverse=True)
            return [{'label': _quarter_label(q), 'value': q} for q in quarters]
        # Yearly
        return [{'label': y, 'value': y} for y in sorted(df['Year'].unique(), reverse=True)]

    def base_target(opts):
        # Return lists — dropdowns are now multi=True
        base   = [opts[1]['value']] if len(opts) > 1 else ([opts[0]['value']] if opts else [])
        target = [opts[0]['value']] if opts else []
        return base, target

    p_opts  = get_opts(p_mode)
    s_opts  = get_opts(s_mode)
    pr_opts = get_opts(pr_mode)

    p_base,  p_tgt  = base_target(p_opts)
    s_base,  s_tgt  = base_target(s_opts)
    pr_base, pr_tgt = base_target(pr_opts)

    return (p_opts, p_base, p_opts, p_tgt,
            s_opts, s_base, s_opts, s_tgt,
            pr_opts, pr_base, pr_opts, pr_tgt)


@callback(
    Output('single-cust-select', 'options'),
    [Input('tabs-nav', 'value')],
    [State('auth-store', 'data')]
)
def update_cust_options(tab, auth_data):
    df = get_secure_cc_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    return [{'label': c, 'value': c} for c in sorted(df[cust_col].dropna().unique())] \
        if not df.empty and cust_col else []


@callback(
    Output('prod-item-select', 'options'),
    [Input('prod-hier-select', 'value')],
    [State('auth-store', 'data')]
)
def update_prod_item_options(hier, auth_data):
    df = get_secure_cc_df(auth_data)
    if df.empty or hier not in df.columns:
        return []
    return [{'label': v, 'value': v} for v in sorted(df[hier].dropna().unique())]


# ─── PORTFOLIO OVERVIEW ────────────────────────────────────────────────────────

@callback(
    [Output('kpi-container',         'children'),
     Output('chart-top-customers',   'figure'),
     Output('retention-scatter',     'figure'),
     Output('customer-flow-chart',   'figure'),
     Output('portfolio-gainers',     'data'),
     Output('portfolio-losers',      'data'),
     Output('header-gainers-port',   'children'),
     Output('header-losers-port',    'children'),
     Output('portfolio-gainers',     'columns'),
     Output('portfolio-losers',      'columns'),
     Output('store-retention',       'data'),
     Output('store-flow',            'data'),
     Output('store-top-customers',   'data')],
    [Input('month-target',        'value'),
     Input('month-ref',           'value'),
     Input('cc-segment-filter',   'value'),
     Input('port-period-mode',    'value')],
    [State('auth-store', 'data')]
)
def update_portfolio(m_base_val, m_target_val, segment, mode, auth_data):
    empty_res = [], go.Figure(), go.Figure(), go.Figure(), [], [], "", "", [], [], {}, {}, {}
    if not m_base_val or not m_target_val:
        return empty_res

    df = get_secure_cc_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year', 'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col = col_map.get(mode, 'Month_Year')

    dff_target, dff_ref = _get_period_dfs(df, col, mode, m_target_val, m_base_val)

    lbl_tgt = _period_label(m_target_val, mode)
    lbl_ref = _period_label(m_base_val,   mode)

    stats_header = s.get_chart_title("Top 10 Customer Comparison", dff_target, dff_ref, METRIC_MODE)
    vol_col  = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl    = 'Qty' if METRIC_MODE == 'qty' else 'Kg'
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')

    cust_target = dff_target.groupby(cust_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    cust_ref    = dff_ref.groupby(cust_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    cust_target['Avg_Price'] = _safe_price(cust_target['Value'], cust_target['Quantity'])
    cust_ref['Avg_Price']    = _safe_price(cust_ref['Value'],    cust_ref['Quantity'])

    df_merged = pd.merge(cust_target, cust_ref, on=cust_col, how='outer',
                         suffixes=('_tgt', '_ref')).fillna(0)
    df_merged['Avg_Price_tgt'] = _safe_price(df_merged['Value_tgt'], df_merged['Quantity_tgt'])
    df_merged['Avg_Price_ref'] = _safe_price(df_merged['Value_ref'], df_merged['Quantity_ref'])

    df_plot = df_merged.nlargest(10, 'Value_tgt').sort_values('Value_tgt', ascending=True)

    hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        y=df_plot[cust_col], x=df_plot['Value_ref'], orientation='h',
        name=f'Base: {lbl_ref}', marker_color='#3498db', opacity=0.6,
        customdata=np.column_stack([df_plot[f'{vol_col}_ref'], df_plot['Avg_Price_ref']]),
        hovertemplate=hover_tmpl
    ))
    fig_bar.add_trace(go.Bar(
        y=df_plot[cust_col], x=df_plot['Value_tgt'], orientation='h',
        name=f'Target: {lbl_tgt}', marker_color='#2ecc71',
        customdata=np.column_stack([df_plot[f'{vol_col}_tgt'], df_plot['Avg_Price_tgt']]),
        hovertemplate=hover_tmpl
    ))
    fig_bar.update_layout(title=stats_header, barmode='group', margin=dict(l=150),
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    rev_t  = df_merged['Value_tgt'].sum()
    rev_r  = df_merged['Value_ref'].sum()
    cnt_t  = len(df_merged[df_merged['Value_tgt'] > 0])
    cnt_r  = len(df_merged[df_merged['Value_ref'] > 0])
    delta_rev  = rev_t - rev_r
    delta_pct  = (delta_rev / rev_r * 100) if rev_r else 0
    kpis = [
        _cc_kpi(f"Revenue ({lbl_tgt})",  rev_t, color='#3498db'),
        _cc_kpi(f"Revenue ({lbl_ref})",  rev_r, color='#9b59b6'),
        _cc_kpi("Δ Revenue",             abs(delta_rev), prev_v=None, color='#e67e22',
                prefix="Ksh " if delta_rev >= 0 else "−Ksh "),
        _cc_kpi("YoY Δ %",              abs(delta_pct), prev_v=None, suffix="%",
                color='#27ae60' if delta_pct >= 0 else '#e74c3c',
                prefix="▲ " if delta_pct >= 0 else "▼ "),
        _cc_kpi(f"Active Customers ({lbl_tgt})", cnt_t, cnt_r, color='#1abc9c'),
    ]

    df_merged['Diff']   = df_merged['Value_tgt'] - df_merged['Value_ref']
    df_merged['Status'] = np.where(df_merged['Value_ref'] == 0, 'New',
                          np.where(df_merged['Value_tgt'] == 0, 'Lost',
                          np.where(df_merged['Diff'] > 0, 'Growth', 'Decline')))

    fig_scat = px.scatter(
        df_merged, x='Value_ref', y='Value_tgt', color='Status',
        hover_data={cust_col: True, 'Diff': True, f'{vol_col}_tgt': True, f'{vol_col}_ref': True},
        color_discrete_map={'New': '#3498db', 'Lost': '#e74c3c', 'Growth': '#2ecc71', 'Decline': '#f39c12'},
        title="Performance Scatter"
    )
    max_val = max(df_merged['Value_ref'].max(), df_merged['Value_tgt'].max()) if not df_merged.empty else 1000
    fig_scat.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val, line=dict(dash='dash', color='gray'))
    fig_scat.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    new_rev     = df_merged[df_merged['Status'] == 'New']['Value_tgt'].sum()
    lost_rev    = df_merged[df_merged['Status'] == 'Lost']['Value_ref'].sum() * -1
    growth_rev  = df_merged[df_merged['Status'] == 'Growth']['Diff'].sum()
    decline_rev = df_merged[df_merged['Status'] == 'Decline']['Diff'].sum()

    fig_flow = go.Figure(go.Waterfall(
        measure=["absolute", "relative", "relative", "relative", "relative", "total"],
        x=["Start", "Lost", "Decline", "Growth", "New", "End"],
        y=[rev_r, lost_rev, decline_rev, growth_rev, new_rev, 0],
        decreasing={"marker": {"color": "#e74c3c"}},
        increasing={"marker": {"color": "#2ecc71"}},
        totals={"marker": {"color": "#34495e"}}
    ))
    fig_flow.update_layout(title=f"Revenue Bridge: {lbl_ref} → {lbl_tgt}",
                           plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    total_gain = df_merged[df_merged['Diff'] > 0]['Diff'].sum()
    total_loss = df_merged[df_merged['Diff'] < 0]['Diff'].sum()
    table_cols = [
        {'name': 'Customer', 'id': cust_col},
        {'name': 'Target',   'id': 'Value_tgt', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Base',     'id': 'Value_ref', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Diff',     'id': 'Diff',      'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
    ]
    gainers = df_merged[df_merged['Diff'] > 0].sort_values('Diff', ascending=False).head(20).to_dict('records')
    losers  = df_merged[df_merged['Diff'] < 0].sort_values('Diff', ascending=True).head(20).to_dict('records')

    return (kpis, fig_bar, fig_scat, fig_flow, gainers, losers,
            f"✅ Gainers (+{total_gain:,.0f})", f"❌ Losers ({total_loss:,.0f})",
            table_cols, table_cols, fig_scat, fig_flow, fig_bar)


# ─── CUSTOMER DEEP DIVE (supports multi-select) ────────────────────────────────

@callback(
    [Output('single-kpi-row',          'children'),
     Output('chart-grouped-comparison','figure'),
     Output('single-item-scatter',     'figure'),
     Output('single-waterfall',        'figure'),
     Output('single-gainers',          'data'),
     Output('single-losers',           'data'),
     Output('header-gainers',          'children'),
     Output('header-losers',           'children'),
     Output('single-gainers',          'columns'),
     Output('single-losers',           'columns'),
     Output('store-grouped-comp',      'data'),
     Output('store-single-scatter',    'data'),
     Output('store-single-waterfall',  'data')],
    [Input('single-cust-select',    'value'),
     Input('single-month-target',   'value'),
     Input('single-month-ref',      'value'),
     Input('single-period-mode',    'value'),
     Input('cc-hierarchy-select',   'value')],
    [State('auth-store', 'data')]
)
def update_single_cust(cust, m_base_val, m_target_val, mode, h_col, auth_data):
    empty_res = [], go.Figure(), go.Figure(), go.Figure(), [], [], "✅ Gainers", "❌ Losers", [], [], {}, {}, {}
    if not cust or not m_base_val or not m_target_val:
        return empty_res

    df = get_secure_cc_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')

    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year', 'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col = col_map.get(mode, 'Month_Year')
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl   = 'Qty' if METRIC_MODE == 'qty' else 'Kg'

    lbl_tgt = _period_label(m_target_val, mode)
    lbl_ref = _period_label(m_base_val,   mode)

    # ── MULTI-CUSTOMER MODE ──────────────────────────────────────────────────
    cust_list = cust if isinstance(cust, list) else [cust]
    is_multi  = len(cust_list) > 1

    if is_multi:
        # Side-by-side revenue comparison for multiple customers
        rows = []
        for c in cust_list:
            df_c = df[df[cust_col].astype(str).str.strip().str.upper() == str(c).strip().upper()]
            dff_t, dff_r = _get_period_dfs(df_c, col, mode, m_target_val, m_base_val)
            val_t  = dff_t['Value'].sum()
            val_r  = dff_r['Value'].sum()
            vol_t  = dff_t[vol_col].sum()
            vol_r  = dff_r[vol_col].sum()
            qty_t  = dff_t['Quantity'].sum()
            qty_r  = dff_r['Quantity'].sum()
            rows.append({
                cust_col: c,
                'Value_tgt': val_t, 'Value_ref': val_r,
                f'{vol_col}_tgt': vol_t, f'{vol_col}_ref': vol_r,
                'Avg_Price_tgt': (val_t / qty_t) if qty_t > 0 else 0,
                'Avg_Price_ref': (val_r / qty_r) if qty_r > 0 else 0,
            })
        mc = pd.DataFrame(rows)
        mc['Diff'] = mc['Value_tgt'] - mc['Value_ref']
        mc_plot = mc.sort_values('Value_tgt', ascending=True)

        hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                      f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")

        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            y=mc_plot[cust_col], x=mc_plot['Value_ref'], orientation='h',
            name=f'Base: {lbl_ref}', marker_color='#3498db', opacity=0.6,
            customdata=np.column_stack([mc_plot[f'{vol_col}_ref'], mc_plot['Avg_Price_ref']]),
            hovertemplate=hover_tmpl
        ))
        fig_bar.add_trace(go.Bar(
            y=mc_plot[cust_col], x=mc_plot['Value_tgt'], orientation='h',
            name=f'Target: {lbl_tgt}', marker_color='#2ecc71',
            customdata=np.column_stack([mc_plot[f'{vol_col}_tgt'], mc_plot['Avg_Price_tgt']]),
            hovertemplate=hover_tmpl
        ))
        title_str = s.get_chart_title(
            f"Multi-Customer Revenue Comparison ({len(cust_list)} Customers)",
            pd.DataFrame({'Value': [mc['Value_tgt'].sum()], vol_col: [mc[f'{vol_col}_tgt'].sum()]}),
            pd.DataFrame({'Value': [mc['Value_ref'].sum()], vol_col: [mc[f'{vol_col}_ref'].sum()]}),
            METRIC_MODE
        )
        fig_bar.update_layout(title=title_str, barmode='group', margin=dict(l=180),
                              plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

        mc['Status'] = np.where(mc['Value_ref'] == 0, 'New',
                       np.where(mc['Value_tgt'] == 0, 'Lost',
                       np.where(mc['Diff'] > 0, 'Growth', 'Decline')))
        fig_scat = px.scatter(
            mc, x='Value_ref', y='Value_tgt', color='Status',
            hover_data={cust_col: True, 'Diff': True},
            color_discrete_map={'New': '#3498db', 'Lost': '#e74c3c', 'Growth': '#2ecc71', 'Decline': '#f39c12'},
            title="Customer Performance Matrix"
        )
        max_v = max(mc['Value_ref'].max(), mc['Value_tgt'].max(), 1)
        fig_scat.add_shape(type="line", x0=0, y0=0, x1=max_v, y1=max_v, line=dict(dash='dash', color='gray'))
        fig_scat.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

        mc_wf = mc.sort_values('Diff', ascending=True)
        wf_x = ["Start"] + mc_wf[cust_col].tolist() + ["End"]
        wf_y = [mc['Value_ref'].sum()] + mc_wf['Diff'].tolist() + [0]
        wf_m = ["absolute"] + ["relative"] * len(mc_wf) + ["total"]
        fig_wf = go.Figure(go.Waterfall(
            measure=wf_m, x=wf_x, y=wf_y,
            decreasing={"marker": {"color": "#e74c3c"}},
            increasing={"marker": {"color": "#2ecc71"}},
            totals={"marker": {"color": "#34495e"}}
        ))
        fig_wf.update_layout(title=f"Revenue Bridge: {lbl_ref} → {lbl_tgt}",
                             plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

        rev_t      = mc['Value_tgt'].sum()
        rev_r      = mc['Value_ref'].sum()
        delta_rev  = rev_t - rev_r
        delta_pct  = (delta_rev / rev_r * 100) if rev_r else 0
        kpis = [
            _cc_kpi(f"Revenue ({lbl_tgt})", rev_t, color='#3498db'),
            _cc_kpi(f"Revenue ({lbl_ref})", rev_r, color='#9b59b6'),
            _cc_kpi("Δ Revenue",  abs(delta_rev), color='#e67e22',
                    prefix="Ksh " if delta_rev >= 0 else "−Ksh "),
            _cc_kpi("YoY Δ %",   abs(delta_pct), suffix="%",
                    color='#27ae60' if delta_pct >= 0 else '#e74c3c',
                    prefix="▲ " if delta_pct >= 0 else "▼ "),
            _cc_kpi("Customers", len(cust_list), color='#1abc9c'),
        ]

        total_gain = mc[mc['Diff'] > 0]['Diff'].sum()
        total_loss = mc[mc['Diff'] < 0]['Diff'].sum()
        table_cols = [
            {'name': 'Customer', 'id': cust_col},
            {'name': 'Target',   'id': 'Value_tgt', 'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
            {'name': 'Base',     'id': 'Value_ref', 'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
            {'name': 'Diff',     'id': 'Diff',      'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        ]
        gainers = mc[mc['Diff'] > 0].sort_values('Diff', ascending=False).head(20).to_dict('records')
        losers  = mc[mc['Diff'] < 0].sort_values('Diff', ascending=True).head(20).to_dict('records')

        return (kpis, fig_bar, fig_scat, fig_wf, gainers, losers,
                f"✅ Gainers (+{total_gain:,.0f})", f"❌ Losers ({total_loss:,.0f})",
                table_cols, table_cols, fig_bar, fig_scat, fig_wf)

    # ── SINGLE CUSTOMER MODE ─────────────────────────────────────────────────
    single_cust = cust_list[0]
    dff = df[df[cust_col].astype(str).str.strip().str.upper() == str(single_cust).strip().upper()]

    dff_target, dff_ref = _get_period_dfs(dff, col, mode, m_target_val, m_base_val)

    stats_header = s.get_chart_title(f"Top 10 {h_col} Comparison", dff_target, dff_ref, METRIC_MODE)

    item_target = dff_target.groupby(h_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    item_ref    = dff_ref.groupby(h_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    item_target['Avg_Price'] = _safe_price(item_target['Value'], item_target['Quantity'])
    item_ref['Avg_Price']    = _safe_price(item_ref['Value'],    item_ref['Quantity'])

    item_merged = pd.merge(item_target, item_ref, on=h_col, how='outer', suffixes=('_tgt', '_ref')).fillna(0)
    item_merged['Avg_Price_tgt'] = _safe_price(item_merged['Value_tgt'], item_merged['Quantity_tgt'])
    item_merged['Avg_Price_ref'] = _safe_price(item_merged['Value_ref'], item_merged['Quantity_ref'])
    item_merged['Diff'] = item_merged['Value_tgt'] - item_merged['Value_ref']
    top_10 = item_merged.nlargest(10, 'Value_tgt').sort_values('Value_tgt', ascending=True)

    hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")

    fig_grouped = go.Figure()
    fig_grouped.add_trace(go.Bar(
        y=top_10[h_col], x=top_10['Value_ref'], orientation='h',
        name=f"Base: {lbl_ref}", marker_color='#3498db', opacity=0.6,
        customdata=np.column_stack([top_10[f'{vol_col}_ref'], top_10['Avg_Price_ref']]),
        hovertemplate=hover_tmpl
    ))
    fig_grouped.add_trace(go.Bar(
        y=top_10[h_col], x=top_10['Value_tgt'], orientation='h',
        name=f"Target: {lbl_tgt}", marker_color='#2ecc71',
        customdata=np.column_stack([top_10[f'{vol_col}_tgt'], top_10['Avg_Price_tgt']]),
        hovertemplate=hover_tmpl
    ))
    fig_grouped.update_layout(title=stats_header, barmode='group', margin=dict(l=150),
                              plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    rev_t = item_target['Value'].sum()
    rev_r = item_ref['Value'].sum()
    cnt_t = len(item_target[item_target['Value'] > 0])
    cnt_r = len(item_ref[item_ref['Value'] > 0])
    delta_rev = rev_t - rev_r
    delta_pct = (delta_rev / rev_r * 100) if rev_r else 0
    kpis = [
        _cc_kpi(f"Revenue ({lbl_tgt})",       rev_t, color='#3498db'),
        _cc_kpi(f"Revenue ({lbl_ref})",       rev_r, color='#9b59b6'),
        _cc_kpi("Δ Revenue", abs(delta_rev), color='#e67e22',
                prefix="Ksh " if delta_rev >= 0 else "−Ksh "),
        _cc_kpi("YoY Δ %",   abs(delta_pct), suffix="%",
                color='#27ae60' if delta_pct >= 0 else '#e74c3c',
                prefix="▲ " if delta_pct >= 0 else "▼ "),
        _cc_kpi(f"Unique {h_col}s ({lbl_tgt})", cnt_t, cnt_r, color='#1abc9c'),
    ]

    item_merged['Status'] = np.where(item_merged['Value_ref'] == 0, 'New',
                            np.where(item_merged['Value_tgt'] == 0, 'Dropped',
                            np.where(item_merged['Diff'] > 0, 'Increased', 'Decreased')))

    fig_scat = px.scatter(
        item_merged, x='Value_ref', y='Value_tgt', color='Status',
        hover_data={h_col: True, 'Diff': True, f'{vol_col}_tgt': True, f'{vol_col}_ref': True},
        color_discrete_map={'New': '#3498db', 'Dropped': '#e74c3c', 'Increased': '#2ecc71', 'Decreased': '#f39c12'},
        title="Mix Change Matrix"
    )
    max_val = max(item_merged['Value_ref'].max(), item_merged['Value_tgt'].max(), 1)
    fig_scat.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val, line=dict(dash='dash', color='gray'))
    fig_scat.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    merged_sorted = item_merged.sort_values('Diff', key=abs, ascending=False)
    top_movers    = merged_sorted.head(8).sort_values('Diff', ascending=True)
    others_diff   = merged_sorted.iloc[8:]['Diff'].sum() if len(merged_sorted) > 8 else 0
    labels        = ["Start"] + top_movers[h_col].tolist() + (["Others"] if others_diff != 0 else []) + ["End"]
    values        = [rev_r]   + top_movers['Diff'].tolist() + ([others_diff] if others_diff != 0 else []) + [0]
    measures      = ["absolute"] + ["relative"] * (len(values) - 2) + ["total"]

    fig_flow = go.Figure(go.Waterfall(
        measure=measures, x=labels, y=values,
        connector={"line": {"color": "rgb(63, 63, 63)"}},
        decreasing={"marker": {"color": "#e74c3c"}},
        increasing={"marker": {"color": "#2ecc71"}},
        totals={"marker": {"color": "#34495e"}}
    ))
    fig_flow.update_layout(title="Revenue Bridge", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    total_gain = item_merged[item_merged['Diff'] > 0]['Diff'].sum()
    total_loss = item_merged[item_merged['Diff'] < 0]['Diff'].sum()
    table_cols = [
        {'name': h_col,   'id': h_col},
        {'name': 'Target','id': 'Value_tgt', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Base',  'id': 'Value_ref', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Diff',  'id': 'Diff',      'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
    ]
    gainers = item_merged[item_merged['Diff'] > 0].sort_values('Diff', ascending=False).head(20).to_dict('records')
    losers  = item_merged[item_merged['Diff'] < 0].sort_values('Diff', ascending=True).head(20).to_dict('records')

    return (kpis, fig_grouped, fig_scat, fig_flow, gainers, losers,
            f"✅ Gainers (+{total_gain:,.0f})", f"❌ Losers ({total_loss:,.0f})",
            table_cols, table_cols, fig_grouped, fig_scat, fig_flow)


# ─── PRODUCT COMPARISON ────────────────────────────────────────────────────────

@callback(
    [Output('prod-kpi-row',           'children'),
     Output('chart-prod-customers',   'figure'),
     Output('prod-retention-scatter', 'figure'),
     Output('prod-flow-chart',        'figure'),
     Output('prod-gainers',           'data'),
     Output('prod-losers',            'data'),
     Output('header-prod-gainers',    'children'),
     Output('header-prod-losers',     'children'),
     Output('prod-gainers',           'columns'),
     Output('prod-losers',            'columns'),
     Output('store-prod-bar',         'data'),
     Output('store-prod-scatter',     'data'),
     Output('store-prod-waterfall',   'data')],
    [Input('prod-item-select',   'value'),
     Input('prod-month-target',  'value'),
     Input('prod-month-ref',     'value'),
     Input('prod-period-mode',   'value'),
     Input('prod-hier-select',   'value')],
    [State('auth-store', 'data')]
)
def update_product_analysis(product, m_base_val, m_target_val, mode, h_col, auth_data):
    empty_res = [], go.Figure(), go.Figure(), go.Figure(), [], [], "", "", [], [], {}, {}, {}
    if not product or not m_base_val or not m_target_val:
        return empty_res

    df = get_secure_cc_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')

    # Filter to the selected product(s)
    prod_list = product if isinstance(product, list) else [product]
    df_prod = df[df[h_col].astype(str).str.strip().str.upper().isin([str(p).strip().upper() for p in prod_list])]
    if df_prod.empty:
        return empty_res

    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year', 'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col = col_map.get(mode, 'Month_Year')
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl   = 'Qty' if METRIC_MODE == 'qty' else 'Kg'

    lbl_tgt = _period_label(m_target_val, mode)
    lbl_ref = _period_label(m_base_val,   mode)

    dff_target, dff_ref = _get_period_dfs(df_prod, col, mode, m_target_val, m_base_val)

    stats_header = s.get_chart_title(f"Customer Performance: {product}", dff_target, dff_ref, METRIC_MODE)

    cust_target = dff_target.groupby(cust_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    cust_ref    = dff_ref.groupby(cust_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    cust_target['Avg_Price'] = _safe_price(cust_target['Value'], cust_target['Quantity'])
    cust_ref['Avg_Price']    = _safe_price(cust_ref['Value'],    cust_ref['Quantity'])

    df_merged = pd.merge(cust_target, cust_ref, on=cust_col, how='outer',
                         suffixes=('_tgt', '_ref')).fillna(0)
    df_merged['Avg_Price_tgt'] = _safe_price(df_merged['Value_tgt'], df_merged['Quantity_tgt'])
    df_merged['Avg_Price_ref'] = _safe_price(df_merged['Value_ref'], df_merged['Quantity_ref'])
    df_merged['Diff']          = df_merged['Value_tgt'] - df_merged['Value_ref']
    df_plot = df_merged.nlargest(10, 'Value_tgt').sort_values('Value_tgt', ascending=True)

    hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        y=df_plot[cust_col], x=df_plot['Value_ref'], orientation='h',
        name=f'Base: {lbl_ref}', marker_color='#3498db', opacity=0.6,
        customdata=np.column_stack([df_plot[f'{vol_col}_ref'], df_plot['Avg_Price_ref']]),
        hovertemplate=hover_tmpl
    ))
    fig_bar.add_trace(go.Bar(
        y=df_plot[cust_col], x=df_plot['Value_tgt'], orientation='h',
        name=f'Target: {lbl_tgt}', marker_color='#2ecc71',
        customdata=np.column_stack([df_plot[f'{vol_col}_tgt'], df_plot['Avg_Price_tgt']]),
        hovertemplate=hover_tmpl
    ))
    fig_bar.update_layout(title=stats_header, barmode='group', margin=dict(l=160),
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    rev_t = df_merged['Value_tgt'].sum()
    rev_r = df_merged['Value_ref'].sum()
    cnt_t = len(df_merged[df_merged['Value_tgt'] > 0])
    cnt_r = len(df_merged[df_merged['Value_ref'] > 0])
    delta_rev = rev_t - rev_r
    delta_pct = (delta_rev / rev_r * 100) if rev_r else 0
    kpis = [
        _cc_kpi(f"Revenue ({lbl_tgt})",        rev_t, color='#3498db'),
        _cc_kpi(f"Revenue ({lbl_ref})",        rev_r, color='#9b59b6'),
        _cc_kpi("Δ Revenue", abs(delta_rev), color='#e67e22',
                prefix="Ksh " if delta_rev >= 0 else "−Ksh "),
        _cc_kpi("YoY Δ %",   abs(delta_pct), suffix="%",
                color='#27ae60' if delta_pct >= 0 else '#e74c3c',
                prefix="▲ " if delta_pct >= 0 else "▼ "),
        _cc_kpi(f"Active Customers ({lbl_tgt})", cnt_t, cnt_r, color='#1abc9c'),
    ]

    df_merged['Status'] = np.where(df_merged['Value_ref'] == 0, 'New',
                          np.where(df_merged['Value_tgt'] == 0, 'Lost',
                          np.where(df_merged['Diff'] > 0, 'Growth', 'Decline')))

    fig_scat = px.scatter(
        df_merged, x='Value_ref', y='Value_tgt', color='Status',
        hover_data={cust_col: True, 'Diff': True, f'{vol_col}_tgt': True, f'{vol_col}_ref': True},
        color_discrete_map={'New': '#3498db', 'Lost': '#e74c3c', 'Growth': '#2ecc71', 'Decline': '#f39c12'},
        title=f"Customer Position: {product}"
    )
    max_val = max(df_merged['Value_ref'].max(), df_merged['Value_tgt'].max(), 1)
    fig_scat.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val, line=dict(dash='dash', color='gray'))
    fig_scat.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    new_rev     = df_merged[df_merged['Status'] == 'New']['Value_tgt'].sum()
    lost_rev    = df_merged[df_merged['Status'] == 'Lost']['Value_ref'].sum() * -1
    growth_rev  = df_merged[df_merged['Status'] == 'Growth']['Diff'].sum()
    decline_rev = df_merged[df_merged['Status'] == 'Decline']['Diff'].sum()

    fig_flow = go.Figure(go.Waterfall(
        measure=["absolute", "relative", "relative", "relative", "relative", "total"],
        x=["Start", "Lost", "Decline", "Growth", "New", "End"],
        y=[rev_r, lost_rev, decline_rev, growth_rev, new_rev, 0],
        decreasing={"marker": {"color": "#e74c3c"}},
        increasing={"marker": {"color": "#2ecc71"}},
        totals={"marker": {"color": "#34495e"}}
    ))
    fig_flow.update_layout(title=f"Revenue Bridge for {product}: {lbl_ref} → {lbl_tgt}",
                           plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    total_gain = df_merged[df_merged['Diff'] > 0]['Diff'].sum()
    total_loss = df_merged[df_merged['Diff'] < 0]['Diff'].sum()
    table_cols = [
        {'name': 'Customer', 'id': cust_col},
        {'name': 'Target',   'id': 'Value_tgt', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Base',     'id': 'Value_ref', 'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
        {'name': 'Diff',     'id': 'Diff',      'type': 'numeric',
         'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
    ]
    gainers = df_merged[df_merged['Diff'] > 0].sort_values('Diff', ascending=False).head(20).to_dict('records')
    losers  = df_merged[df_merged['Diff'] < 0].sort_values('Diff', ascending=True).head(20).to_dict('records')

    return (kpis, fig_bar, fig_scat, fig_flow, gainers, losers,
            f"✅ Gainers (+{total_gain:,.0f})", f"❌ Losers ({total_loss:,.0f})",
            table_cols, table_cols, fig_bar, fig_scat, fig_flow)


# ─── DRILLDOWN: PORTFOLIO — click customer → see their top items ───────────────

@callback(
    [Output('port-drill-wrap',  'style'),
     Output('port-drill-title', 'children'),
     Output('port-drill-chart', 'figure')],
    [Input('chart-top-customers', 'clickData'),
     Input('port-drill-close',    'n_clicks')],
    [State('month-target',        'value'),
     State('month-ref',           'value'),
     State('port-period-mode',    'value'),
     State('cc-segment-filter',   'value'),
     State('auth-store',          'data')]
)
def port_drilldown(clk, _close, m_base_val, m_target_val, mode, segment, auth_data):
    _SHOW = {'display': 'block', 'padding': '20px', 'backgroundColor': 'white',
             'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}',
             'marginTop': '20px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)'}
    _HIDE = {'display': 'none'}

    if ctx.triggered_id == 'port-drill-close' or not clk:
        return _HIDE, "", go.Figure()

    customer = clk['points'][0]['y']
    df = get_secure_cc_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')
    h_col    = next((c for c in ['Items', 'Name', 'Items 2'] if c in df.columns), None)
    if not h_col:
        return _HIDE, "", go.Figure()

    df_c = df[df[cust_col].astype(str).str.strip().str.upper() == str(customer).strip().upper()]
    col_map = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year', 'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col     = col_map.get(mode, 'Month_Year')
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl   = 'Qty'     if METRIC_MODE == 'qty' else 'Kg'

    lbl_tgt = _period_label(m_target_val, mode)
    lbl_ref = _period_label(m_base_val,   mode)

    dff_tgt, dff_ref = _get_period_dfs(df_c, col, mode, m_target_val, m_base_val)

    itm_tgt = dff_tgt.groupby(h_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    itm_ref = dff_ref.groupby(h_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    merged  = pd.merge(itm_tgt, itm_ref, on=h_col, how='outer', suffixes=('_tgt', '_ref')).fillna(0)
    merged['Avg_Price_tgt'] = _safe_price(merged['Value_tgt'], merged['Quantity_tgt'])
    merged['Avg_Price_ref'] = _safe_price(merged['Value_ref'], merged['Quantity_ref'])
    top10 = merged.nlargest(10, 'Value_tgt').sort_values('Value_tgt', ascending=True)

    if top10.empty:
        return _HIDE, "", go.Figure()

    hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=top10[h_col], x=top10['Value_ref'], orientation='h',
        name=f'Base: {lbl_ref}', marker_color='#3498db', opacity=0.6,
        customdata=np.column_stack([top10[f'{vol_col}_ref'], top10['Avg_Price_ref']]),
        hovertemplate=hover_tmpl
    ))
    fig.add_trace(go.Bar(
        y=top10[h_col], x=top10['Value_tgt'], orientation='h',
        name=f'Target: {lbl_tgt}', marker_color='#2ecc71',
        customdata=np.column_stack([top10[f'{vol_col}_tgt'], top10['Avg_Price_tgt']]),
        hovertemplate=hover_tmpl
    ))
    fig.update_layout(title=f"What {customer} Buys — Top Items: {lbl_ref} vs {lbl_tgt}",
                      barmode='group', margin=dict(l=160),
                      plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    return _SHOW, f"Drilldown: {customer}", fig


# ─── DRILLDOWN: SINGLE DEEP DIVE — click item→trend (single) or customer→items (multi) ──

@callback(
    [Output('single-drill-wrap',  'style'),
     Output('single-drill-title', 'children'),
     Output('single-drill-chart', 'figure')],
    [Input('chart-grouped-comparison', 'clickData'),
     Input('single-drill-close',       'n_clicks')],
    [State('single-cust-select',    'value'),
     State('single-month-target',   'value'),
     State('single-month-ref',      'value'),
     State('single-period-mode',    'value'),
     State('cc-hierarchy-select',   'value'),
     State('auth-store',            'data')]
)
def single_drilldown(clk, _close, cust, m_base_val, m_target_val, mode, h_col, auth_data):
    _SHOW = {'display': 'block', 'padding': '20px', 'backgroundColor': 'white',
             'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}',
             'marginTop': '20px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)'}
    _HIDE = {'display': 'none'}

    if ctx.triggered_id == 'single-drill-close' or not clk or not cust:
        return _HIDE, "", go.Figure()

    entity  = clk['points'][0]['y']
    df      = get_secure_cc_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')
    col_map  = {'Weekly': 'Week_Year', 'Monthly': 'Month_Year', 'Quarterly': 'Quarter_Year', 'Yearly': 'Year'}
    col      = col_map.get(mode, 'Month_Year')
    vol_col  = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl    = 'Qty'     if METRIC_MODE == 'qty' else 'Kg'
    lbl_tgt  = _period_label(m_target_val, mode)
    lbl_ref  = _period_label(m_base_val,   mode)

    cust_list = cust if isinstance(cust, list) else [cust]
    is_multi  = len(cust_list) > 1

    if is_multi:
        # entity is a customer name — show their top items (same as portfolio drilldown logic)
        sub_h = next((c for c in ['Items', 'Name', 'Items 2'] if c in df.columns), None)
        if not sub_h:
            return _HIDE, "", go.Figure()
        df_c  = df[df[cust_col].astype(str).str.strip().str.upper() == str(entity).strip().upper()]
        dff_t, dff_r = _get_period_dfs(df_c, col, mode, m_target_val, m_base_val)
        itm_t = dff_t.groupby(sub_h)[['Value', vol_col, 'Quantity']].sum().reset_index()
        itm_r = dff_r.groupby(sub_h)[['Value', vol_col, 'Quantity']].sum().reset_index()
        merged = pd.merge(itm_t, itm_r, on=sub_h, how='outer', suffixes=('_tgt', '_ref')).fillna(0)
        merged['Avg_Price_tgt'] = _safe_price(merged['Value_tgt'], merged['Quantity_tgt'])
        merged['Avg_Price_ref'] = _safe_price(merged['Value_ref'], merged['Quantity_ref'])
        top10 = merged.nlargest(10, 'Value_tgt').sort_values('Value_tgt', ascending=True)

        if top10.empty:
            return _HIDE, "", go.Figure()

        hover_tmpl = (f"<b>%{{y}}</b><br>Rev: Ksh %{{x:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                      f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=top10[sub_h], x=top10['Value_ref'], orientation='h',
            name=f'Base: {lbl_ref}', marker_color='#3498db', opacity=0.6,
            customdata=np.column_stack([top10[f'{vol_col}_ref'], top10['Avg_Price_ref']]),
            hovertemplate=hover_tmpl
        ))
        fig.add_trace(go.Bar(
            y=top10[sub_h], x=top10['Value_tgt'], orientation='h',
            name=f'Target: {lbl_tgt}', marker_color='#2ecc71',
            customdata=np.column_stack([top10[f'{vol_col}_tgt'], top10['Avg_Price_tgt']]),
            hovertemplate=hover_tmpl
        ))
        fig.update_layout(title=f"What {entity} Buys — {lbl_ref} vs {lbl_tgt}",
                          barmode='group', margin=dict(l=160),
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        return _SHOW, f"Product Mix: {entity}", fig

    # ── Single customer: entity is an item — show its period-by-period trend ──
    single_cust = cust_list[0]
    df_c  = df[df[cust_col].astype(str).str.strip().str.upper() == str(single_cust).strip().upper()]
    df_ci = df_c[df_c[h_col].astype(str).str.strip().str.upper() == str(entity).strip().upper()]

    if df_ci.empty:
        return _HIDE, "", go.Figure()

    # Build period column from all available data (not just one window) for trend context
    df_all = df_ci.copy()
    if mode == 'Weekly':
        df_all['_Period'] = df_all['Week_Year']
    elif mode == 'Monthly':
        df_all['_Period'] = df_all['Month_Year']
    elif mode == 'Quarterly':
        df_all['_Period'] = df_all['Quarter_Year'].apply(_quarter_label)
    else:
        df_all['_Period'] = df_all['Year']

    trend = df_all.groupby('_Period')[['Value', vol_col, 'Quantity']].sum().reset_index()
    trend = trend.sort_values('_Period')
    trend['Avg_Price'] = _safe_price(trend['Value'], trend['Quantity'])

    # Highlight the two comparison periods
    target_lbl_raw = _period_label(m_target_val, mode)
    base_lbl_raw   = _period_label(m_base_val,   mode)
    colors = ['#f39c12' if p == target_lbl_raw else ('#3498db' if p == base_lbl_raw else '#bdc3c7')
              for p in trend['_Period']]

    hover_tmpl = (f"<b>%{{x}}</b><br>Rev: Ksh %{{y:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")
    fig = go.Figure(go.Bar(
        x=trend['_Period'], y=trend['Value'],
        marker_color=colors,
        customdata=np.column_stack([trend[vol_col], trend['Avg_Price']]),
        hovertemplate=hover_tmpl
    ))
    fig.update_layout(
        title=f"'{entity}' Trend for {single_cust} — orange={lbl_tgt}, blue={lbl_ref}",
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        xaxis_tickangle=-35
    )
    return _SHOW, f"Item Trend: {entity} ({single_cust})", fig


# ─── DRILLDOWN: PRODUCT — click customer → their period trend for this product ──

@callback(
    [Output('prod-drill-wrap',  'style'),
     Output('prod-drill-title', 'children'),
     Output('prod-drill-chart', 'figure')],
    [Input('chart-prod-customers', 'clickData'),
     Input('prod-drill-close',     'n_clicks')],
    [State('prod-item-select',  'value'),
     State('prod-month-target', 'value'),
     State('prod-month-ref',    'value'),
     State('prod-period-mode',  'value'),
     State('prod-hier-select',  'value'),
     State('auth-store',        'data')]
)
def prod_drilldown(clk, _close, product, m_base_val, m_target_val, mode, h_col, auth_data):
    _SHOW = {'display': 'block', 'padding': '20px', 'backgroundColor': 'white',
             'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}',
             'marginTop': '20px', 'boxShadow': '0 2px 8px rgba(0,0,0,0.08)'}
    _HIDE = {'display': 'none'}

    if ctx.triggered_id == 'prod-drill-close' or not clk or not product:
        return _HIDE, "", go.Figure()

    customer = clk['points'][0]['y']
    df       = get_secure_cc_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')
    vol_col  = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    v_lbl    = 'Qty'     if METRIC_MODE == 'qty' else 'Kg'
    lbl_tgt  = _period_label(m_target_val, mode)
    lbl_ref  = _period_label(m_base_val,   mode)

    # Filter to customer + product(s) across all available periods
    prod_list = product if isinstance(product, list) else [product]
    df_cp = df[
        (df[cust_col].astype(str).str.strip().str.upper() == str(customer).strip().upper()) &
        (df[h_col].astype(str).str.strip().str.upper().isin([str(p).strip().upper() for p in prod_list]))
    ].copy()

    if df_cp.empty:
        return _HIDE, "", go.Figure()

    if mode == 'Weekly':
        df_cp['_Period'] = df_cp['Week_Year']
    elif mode == 'Monthly':
        df_cp['_Period'] = df_cp['Month_Year']
    elif mode == 'Quarterly':
        df_cp['_Period'] = df_cp['Quarter_Year'].apply(_quarter_label)
    else:
        df_cp['_Period'] = df_cp['Year']

    trend = df_cp.groupby('_Period')[['Value', vol_col, 'Quantity']].sum().reset_index()
    trend = trend.sort_values('_Period')
    trend['Avg_Price'] = _safe_price(trend['Value'], trend['Quantity'])

    target_lbl_raw = _period_label(m_target_val, mode)
    base_lbl_raw   = _period_label(m_base_val,   mode)
    colors = ['#f39c12' if p == target_lbl_raw else ('#3498db' if p == base_lbl_raw else '#bdc3c7')
              for p in trend['_Period']]

    hover_tmpl = (f"<b>%{{x}}</b><br>Rev: Ksh %{{y:,.0f}}<br>{v_lbl}: %{{customdata[0]:,.0f}}"
                  f"<br>Avg Price: Ksh %{{customdata[1]:,.2f}}<extra></extra>")
    fig = go.Figure(go.Bar(
        x=trend['_Period'], y=trend['Value'],
        marker_color=colors,
        customdata=np.column_stack([trend[vol_col], trend['Avg_Price']]),
        hovertemplate=hover_tmpl
    ))
    fig.update_layout(
        title=f"{customer}'s '{product}' Purchases — orange={lbl_tgt}, blue={lbl_ref}",
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        xaxis_tickangle=-35
    )
    return _SHOW, f"Purchase Trend: {customer} × {product}", fig


# ─── SELECT ALL / NONE ────────────────────────────────────────────────────────

@callback(
    Output('month-target', 'value', allow_duplicate=True),
    Input('month-target-all',  'n_clicks'),
    Input('month-target-none', 'n_clicks'),
    State('month-target', 'options'),
    prevent_initial_call=True,
)
def month_target_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'month-target-all' else []


@callback(
    Output('month-ref', 'value', allow_duplicate=True),
    Input('month-ref-all',  'n_clicks'),
    Input('month-ref-none', 'n_clicks'),
    State('month-ref', 'options'),
    prevent_initial_call=True,
)
def month_ref_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'month-ref-all' else []


@callback(
    Output('single-cust-select', 'value'),
    Input('single-cust-all',  'n_clicks'),
    Input('single-cust-none', 'n_clicks'),
    State('single-cust-select', 'options'),
    prevent_initial_call=True,
)
def single_cust_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'single-cust-all' else []


@callback(
    Output('single-month-target', 'value', allow_duplicate=True),
    Input('single-mtarget-all',  'n_clicks'),
    Input('single-mtarget-none', 'n_clicks'),
    State('single-month-target', 'options'),
    prevent_initial_call=True,
)
def single_month_target_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'single-mtarget-all' else []


@callback(
    Output('single-month-ref', 'value', allow_duplicate=True),
    Input('single-mref-all',  'n_clicks'),
    Input('single-mref-none', 'n_clicks'),
    State('single-month-ref', 'options'),
    prevent_initial_call=True,
)
def single_month_ref_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'single-mref-all' else []


@callback(
    Output('prod-item-select', 'value', allow_duplicate=True),
    Input('prod-item-all',  'n_clicks'),
    Input('prod-item-none', 'n_clicks'),
    State('prod-item-select', 'options'),
    prevent_initial_call=True,
)
def prod_item_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'prod-item-all' else []


@callback(
    Output('prod-month-target', 'value', allow_duplicate=True),
    Input('prod-mtarget-all',  'n_clicks'),
    Input('prod-mtarget-none', 'n_clicks'),
    State('prod-month-target', 'options'),
    prevent_initial_call=True,
)
def prod_month_target_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'prod-mtarget-all' else []


@callback(
    Output('prod-month-ref', 'value', allow_duplicate=True),
    Input('prod-mref-all',  'n_clicks'),
    Input('prod-mref-none', 'n_clicks'),
    State('prod-month-ref', 'options'),
    prevent_initial_call=True,
)
def prod_month_ref_select_all(_, __, opts):
    return [o['value'] for o in (opts or [])] if ctx.triggered_id == 'prod-mref-all' else []


# ─── SNAPSHOT DOWNLOAD ─────────────────────────────────────────────────────────

@callback(
    Output("customer-comparison-download-snapshot", "data"),
    Input("customer_comparison-btn-snapshot", "n_clicks"),
    [State('store-retention',       'data'),
     State('store-flow',            'data'),
     State('store-top-customers',   'data'),
     State('store-single-scatter',  'data'),
     State('store-single-waterfall','data'),
     State('store-prod-bar',        'data'),
     State('store-prod-scatter',    'data'),
     State('store-prod-waterfall',  'data'),
     State('tabs-nav',              'value')],
    prevent_initial_call=True
)
def download_snapshot(n, fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8, tab):
    if tab == 'tab-portfolio':
        figs_to_print = [fig1, fig3, fig2]
        title = "Portfolio Snapshot"
    elif tab == 'tab-single':
        figs_to_print = [fig4, fig5]
        title = "Deep Dive Snapshot"
    else:
        figs_to_print = [fig6, fig7, fig8]
        title = "Product Comparison Snapshot"

    html_string = f"<html><head><title>{title}</title></head><body><h1 style='text-align:center;'>{title}</h1>"
    for f in figs_to_print:
        if f:
            html_string += pio.to_html(go.Figure(f), full_html=False, include_plotlyjs='cdn') + "<hr>"
    html_string += "</body></html>"
    return dict(content=html_string, filename="comparison_snapshot.html")
