import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine
import dash
from dash import html, dcc, dash_table, Input, Output, State, ctx, callback, no_update, ALL
from dash.dash_table.Format import Format, Scheme, Group
import plotly.graph_objects as go
import plotly.io as pio
import pandas as pd
import numpy as np

# Import the live-reloading function
from data_loader import get_latest_data, METRIC_MODE
from users import enforce_row_level_security

# Prime layout variables
_, _, last_date_v1, years_v1 = get_latest_data()


def get_secure_v1_df(auth_data):
    df_fresh, _, _, _ = get_latest_data()
    return enforce_row_level_security(df_fresh, auth_data)


# --- CUSTOM KPI BUILDERS ---
def create_v1_kpi(title, value, border_color, is_currency=True):
    val_str = f"KES {value:,.0f}" if is_currency else f"{value:,.0f}"
    return html.Div([
        html.Div(style={'height': '5px', 'backgroundColor': border_color, 'borderTopLeftRadius': '8px',
                        'borderTopRightRadius': '8px'}),
        html.Div([
            html.H4(title, style={'color': '#95a5a6', 'fontSize': '13px', 'fontWeight': 'bold', 'textAlign': 'center',
                                  'margin': '15px 0 5px 0'}),
            html.H2(val_str,
                    style={'margin': '0 0 15px 0', 'color': '#2c3e50', 'fontSize': '28px', 'textAlign': 'center'})
        ], style={'padding': '10px'})
    ], style={'backgroundColor': 'white', 'borderRadius': '8px', 'flex': '1', 'margin': '0 10px',
              'boxShadow': '0 2px 4px rgba(0,0,0,0.05)', 'border': '1px solid #eee'})


def create_kpi_pct(title, val, border_color):
    if val == 0:
        val_str, color = "-", "#7f8c8d"
    else:
        color = '#2ecc71' if val >= 0 else '#e74c3c'
        arrow = '▲' if val >= 0 else '▼'
        val_str = f"{arrow} {abs(val):.1f}%"

    return html.Div([
        html.Div(style={'height': '5px', 'backgroundColor': border_color, 'borderTopLeftRadius': '8px',
                        'borderTopRightRadius': '8px'}),
        html.Div([
            html.H4(title, style={'color': '#95a5a6', 'fontSize': '13px', 'fontWeight': 'bold', 'textAlign': 'center',
                                  'margin': '15px 0 5px 0'}),
            html.H2(val_str, style={'margin': '0 0 15px 0', 'color': color, 'fontSize': '28px', 'textAlign': 'center'})
        ], style={'padding': '10px'})
    ], style={'backgroundColor': 'white', 'borderRadius': '8px', 'flex': '1', 'margin': '0 10px',
              'boxShadow': '0 2px 4px rgba(0,0,0,0.05)', 'border': '1px solid #eee'})


# --- LAYOUT ---
layout = html.Div([
    dcc.Store(id='v1-trend-date'), dcc.Store(id='v1-daily-date'),
    dcc.Store(id='kpi-data-store'),  # Browser Cache for Level 3 Math
    dcc.Download(id="v1-dl"),

    html.Div([
        html.H1("Executive Dashboard", style=s.HEADER_TITLE),
        html.P(f"Data as of: {last_date_v1 if last_date_v1 else 'N/A'}",
               style={'textAlign': 'center', 'color': '#7f8c8d', 'marginBottom': '20px'}),

        html.Div([
            html.Div(id='v1-segment-container', children=[
                html.Span("SEGMENT:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.RadioItems(id='v1-segment', options=['All', 'B2B', 'B2C'], value='All', inline=True,
                               style={'fontSize': '14px', 'marginRight': '20px'})
            ], style={'display': 'inline-block'}),

            html.Span("YEAR:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.Dropdown(id='v1-year', options=[{'label': str(y), 'value': y} for y in years_v1] if years_v1 else [],
                         value=years_v1[0] if years_v1 else None, clearable=False,
                         style={'width': '80px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("VIEW:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='v1-view', options=['Week', 'Month', 'Quarter', 'Year'], value='Month', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px'}),

            html.Span("LEVEL:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='v1-hier',
                           options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                           style={'display': 'inline-block', 'marginRight': '20px'}),

            html.Button("📸 Snapshot", id="v1-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'backgroundColor': '#f8f9fa',
                  'padding': '15px', 'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}'})
    ], style=s.SECTION),

    # --- KPI DRILLDOWN ARCHITECTURE (YTD & MTD SPLIT) ---
    html.Div([
        # --- YTD SECTION ---
        html.H3("Year-to-Date (YTD) Cumulative Performance",
                style={'textAlign': 'center', 'color': s.COLORS['primary'], 'margin': '0 0 15px 0'}),
        html.Div(id='ytd-kpi-l1', style={'display': 'flex', 'justifyContent': 'space-between', 'marginBottom': '15px'}),
        html.Div(
            html.Button("▼ Show YTD Monthly Progression", id='btn-ytd-toggle',
                        style={'fontSize': '12px', 'padding': '6px 15px', 'cursor': 'pointer',
                               'backgroundColor': '#ecf0f1', 'color': '#2c3e50', 'border': '1px solid #bdc3c7',
                               'borderRadius': '4px', 'fontWeight': 'bold'}),
            style={'textAlign': 'center', 'marginBottom': '10px'}
        ),
        html.Div(id='ytd-kpi-l2', style={'display': 'none', 'justifyContent': 'space-between', 'gap': '20px'}),
        html.Div(id='ytd-kpi-l3', style={'display': 'none'}),

        html.Div(style={'height': '1px', 'backgroundColor': '#e0e0e0', 'margin': '30px 0'}),

        # --- MTD SECTION ---
        html.H3("Month-to-Date (MTD) Isolated Performance",
                style={'textAlign': 'center', 'color': s.COLORS['primary'], 'margin': '0 0 15px 0'}),
        html.Div(id='mtd-kpi-l1', style={'display': 'flex', 'justifyContent': 'space-between', 'marginBottom': '15px'}),
        html.Div(
            html.Button("▼ Show MTD Monthly Comparison", id='btn-mtd-toggle',
                        style={'fontSize': '12px', 'padding': '6px 15px', 'cursor': 'pointer',
                               'backgroundColor': '#ecf0f1', 'color': '#2c3e50', 'border': '1px solid #bdc3c7',
                               'borderRadius': '4px', 'fontWeight': 'bold'}),
            style={'textAlign': 'center', 'marginBottom': '10px'}
        ),
        html.Div(id='mtd-kpi-l2', style={'display': 'none', 'justifyContent': 'space-between', 'gap': '20px'}),
        html.Div(id='mtd-kpi-l3', style={'display': 'none'})

    ], style=s.SECTION),

    html.Div([
        html.Div([html.Button("Reset Trend Filters", id='btn-reset-v1-trends',
                              style={'fontSize': '11px', 'padding': '4px 8px', 'cursor': 'pointer',
                                     'backgroundColor': '#e74c3c', 'color': 'white', 'border': 'none',
                                     'borderRadius': '4px'})], style={'textAlign': 'right', 'marginBottom': '5px'}),
        dcc.Graph(id='v1-trend', style=s.CHART_CONTAINER), html.Div(id='v1-trend-anno'),
        dcc.Graph(id='v1-daily-trend', style={**s.CHART_CONTAINER, 'marginTop': '20px'}), html.Div(id='v1-daily-anno')
    ], style=s.SECTION),

    html.Div([
        html.Div([dcc.Graph(id='v1-top-products', style=s.CHART_CONTAINER), html.Div(id='v1-prod-anno')]),
        html.Div(id='v1-drilldown-container', style={'display': 'none', 'marginTop': '20px'},
                 children=[dcc.Graph(id='v1-prod-drilldown', style=s.CHART_CONTAINER),
                           html.Div(id='v1-prod-drilldown-anno')])
    ], style=s.SECTION),

    html.Div([dcc.Graph(id='v1-top-customers', style=s.CHART_CONTAINER), html.Div(id='v1-cust-anno')], style=s.SECTION),
    html.Div([dcc.Graph(id='v1-top-reps', style=s.CHART_CONTAINER), html.Div(id='v1-rep-anno')], style=s.SECTION),

    html.Div([
        html.H2("Inventory Risk Management",
                style={'textAlign': 'center', 'color': s.COLORS.get('danger', '#e74c3c'), 'marginBottom': '20px',
                       'fontSize': '18px'}),
        html.Div([
            html.Div([
                html.H3("🟣 Overstocked", style={'textAlign': 'center', 'color': 'purple', 'fontSize': '14px'}),
                dash_table.DataTable(id='overstock-table', columns=[
                    {'name': 'Item', 'id': 'Name'},
                    {'name': 'QIH', 'id': 'Qty On Hand', 'type': 'numeric',
                     'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
                    {'name': 'Max', 'id': 'Max_Stock', 'type': 'numeric',
                     'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)}
                ], page_size=10, page_action='native',
                                     style_header={'backgroundColor': 'purple', 'color': 'white', 'fontWeight': 'bold',
                                                   'fontSize': '11px'},
                                     style_cell={'fontSize': '11px', 'textAlign': 'center', 'padding': '5px',
                                                 'fontFamily': 'Segoe UI'})
            ], style={'width': '49%', 'display': 'inline-block', 'verticalAlign': 'top'}),
            html.Div([
                html.H3("🚨 Re-order",
                        style={'textAlign': 'center', 'color': s.COLORS.get('danger', '#e74c3c'), 'fontSize': '14px'}),
                dash_table.DataTable(id='understock-table', columns=[
                    {'name': 'Item', 'id': 'Name'},
                    {'name': 'QIH', 'id': 'Qty On Hand', 'type': 'numeric',
                     'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
                    {'name': 'ROL', 'id': 'ROL', 'type': 'numeric',
                     'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)}
                ], page_size=10, page_action='native',
                                     style_header={'backgroundColor': s.COLORS.get('danger', '#e74c3c'),
                                                   'color': 'white', 'fontWeight': 'bold', 'fontSize': '11px'},
                                     style_cell={'fontSize': '11px', 'textAlign': 'center', 'padding': '5px',
                                                 'fontFamily': 'Segoe UI'})
            ], style={'width': '49%', 'display': 'inline-block', 'verticalAlign': 'top', 'marginLeft': '2%'})
        ])
    ], style=s.SECTION),

    html.Div([
        html.H3("Top 60 Products Detailed Performance", style={'textAlign': 'center', 'color': s.COLORS['primary']}),
        dash_table.DataTable(
            id='v1-table',
            columns=[
                {'name': 'Product Category', 'id': 'Entity'},
                {'name': 'Revenue (Ksh)', 'id': 'Revenue', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'LY Revenue (Ksh)', 'id': 'LY_Revenue', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'YoY Growth %', 'id': 'Growth', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=1)},
                {'name': 'Volume', 'id': 'Volume', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'Avg Price (Ksh)', 'id': 'Avg_Price', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)}
            ],
            style_table={'overflowX': 'auto', 'height': '400px', 'overflowY': 'auto'},
            style_header={'backgroundColor': s.COLORS['primary'], 'color': 'white', 'fontWeight': 'bold',
                          'textAlign': 'center'},
            style_cell={'textAlign': 'left', 'padding': '10px', 'fontFamily': 'Segoe UI'},
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'},
                                    {'if': {'filter_query': '{Growth} < 0', 'column_id': 'Growth'}, 'color': '#e74c3c',
                                     'fontWeight': 'bold'},
                                    {'if': {'filter_query': '{Growth} >= 0', 'column_id': 'Growth'}, 'color': '#2ecc71',
                                     'fontWeight': 'bold'}],
            sort_action='native'
        )
    ], style=s.SECTION),

    html.Div([
        html.H3("Slow Moving Inventory (Aging Report)",
                style={'textAlign': 'center', 'color': '#e67e22', 'marginBottom': '15px'}),

        # --- NEW SEARCH BAR ---
        html.Div([
            html.Span("SEARCH SUB-CATEGORY:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.Dropdown(id='aging-subcat-filter', placeholder="Select or type a Sub-Category...", clearable=True,
                         style={'width': '350px', 'display': 'inline-block', 'fontSize': '13px'})
        ], style={'textAlign': 'center', 'marginBottom': '15px', 'display': 'flex', 'justifyContent': 'center',
                  'alignItems': 'center'}),

        dcc.Store(id='aging-data-store'),  # Hidden browser cache for the aging data

        dash_table.DataTable(
            id='slow-moving-table',
            columns=[
                {'name': 'Category', 'id': 'Items 2'},
                {'name': 'Sub-Category', 'id': 'Items'},
                {'name': 'Item', 'id': 'Name'},
                {'name': '12 Month', 'id': '12 Month', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': '6 Month', 'id': '6 Month', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': '3 Month', 'id': '3 Month', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)}
            ],
            sort_action='native',
            filter_action='native',  # Allows users to also type directly into column headers to search
            sort_by=[{'column_id': '12 Month', 'direction': 'desc'}],
            page_size=10,
            style_table={'overflowX': 'auto', 'minWidth': '100%'},
            style_header={'backgroundColor': '#e67e22', 'color': 'white', 'fontWeight': 'bold', 'textAlign': 'center',
                          'fontSize': '12px'},
            style_cell={'textAlign': 'left', 'padding': '8px', 'fontFamily': 'Segoe UI', 'fontSize': '11px'},
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}]
        )
    ], style=s.SECTION)

], style=s.MAIN_CONTAINER)


# --- CALLBACKS ---
@callback(Output('v1-segment-container', 'style'), [Input('auth-store', 'data')])
def setup_permissions_v1(auth_data):
    ui_rules = auth_data.get('ui_permissions', {}) if auth_data else {}
    return {'display': 'inline-block'} if ui_rules.get('show_segment_filters', True) else {'display': 'none'}


@callback(
    [Output('v1-trend-date', 'data'), Output('v1-daily-date', 'data')],
    [Input('v1-trend', 'clickData'), Input('v1-daily-trend', 'clickData'), Input('btn-reset-v1-trends', 'n_clicks')]
)
def manage_v1_clicks(trend_clk, daily_clk, reset_btn):
    trig = ctx.triggered_id
    if trig == 'btn-reset-v1-trends': return None, None
    if trig == 'v1-trend': return trend_clk['points'][0]['x'], None
    if trig == 'v1-daily-trend': return no_update, daily_clk['points'][0]['x']
    return no_update, no_update


@callback(
    [Output('v1-daily-trend', 'figure'), Output('v1-daily-anno', 'children')],
    [Input('v1-year', 'value'), Input('v1-view', 'value'), Input('v1-segment', 'value'),
     Input('v1-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_daily_trend(year, view, segment, trend_date, auth_data):
    if not year: return go.Figure(), ""
    df_sec = get_secure_v1_df(auth_data)
    if segment != 'All' and 'B2B' in df_sec.columns: df_sec = df_sec[
        df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    df_curr, df_prev, lbl = core_engine.get_time_window(df_sec, year, view, trend_date)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    daily_df = core_engine.get_drilldown_data(df_curr, vol_col)
    header = s.get_chart_title(f"Daily Breakdown ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig = s.create_bar_chart(daily_df, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    if not daily_df.empty:
        if view == 'Year' and not trend_date:
            fig.update_xaxes(dtick=None, tickformat="%d %b")
        else:
            fig.update_xaxes(dtick=86400000.0, tickformat="%d %b")
    return fig, s.get_storytelling_anno(daily_df, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('ytd-kpi-l1', 'children'), Output('mtd-kpi-l1', 'children'),
     Output('ytd-kpi-l2', 'children'), Output('mtd-kpi-l2', 'children'),
     Output('kpi-data-store', 'data'),
     Output('v1-trend', 'figure'), Output('v1-trend-anno', 'children'),
     Output('v1-top-products', 'figure'), Output('v1-prod-anno', 'children'),
     Output('aging-data-store', 'data'),
     Output('v1-top-customers', 'figure'), Output('v1-cust-anno', 'children'),
     Output('v1-top-reps', 'figure'), Output('v1-rep-anno', 'children'),
     Output('overstock-table', 'data'), Output('understock-table', 'data'),
     Output('v1-table', 'data')],
    [Input('v1-year', 'value'), Input('v1-view', 'value'), Input('v1-hier', 'value'), Input('v1-segment', 'value'),
     Input('v1-trend-date', 'data'), Input('v1-daily-date', 'data')],
    [State('auth-store', 'data')]
)
def update_main_v1(year, view, h_col, segment, trend_date, daily_date, auth_data):
    # 17 Expected Outputs
    empty_res = ([], [], [], [], {}, go.Figure(), "", go.Figure(), "", [], go.Figure(), "", go.Figure(), "", [], [], [])
    if not year: return empty_res

    df_sec = get_secure_v1_df(auth_data)
    if df_sec.empty: return empty_res

    target_year = int(year)
    if segment != 'All' and 'B2B' in df_sec.columns:
        df_sec = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    # Quantity basis for the monthly progression QTY / Avg Price metrics.
    # Use the raw 'Quantity' column (units) — not 'QTY PCS' (pieces) — by default.
    qty_col = 'Quantity' if 'Quantity' in df_sec.columns else vol_col

    df_target_yr = df_sec[df_sec['Date'].dt.year == target_year]
    anchor_d = df_target_yr['Date'].max() if not df_target_yr.empty else pd.Timestamp(f'{target_year}-12-31')

    # --- LEVEL 1 KPI MATH ---
    # YTD Calcs
    ytd_cy = df_target_yr['Value'].sum()
    ytd_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & ((df_sec['Date'].dt.month < anchor_d.month) | (
                (df_sec['Date'].dt.month == anchor_d.month) & (df_sec['Date'].dt.day <= anchor_d.day)))
    ytd_ly = df_sec[ytd_ly_mask]['Value'].sum()
    ytd_var = ytd_cy - ytd_ly
    ytd_pct = (ytd_var / ytd_ly * 100) if ytd_ly > 0 else 0

    ytd_l1 = [
        create_v1_kpi(f"YTD Revenue ({target_year})", ytd_cy, "#3498db"),
        create_v1_kpi(f"YTD Revenue ({target_year - 1})", ytd_ly, "#9b59b6"),
        create_v1_kpi("YTD Variance", ytd_var, "#e67e22"),
        create_kpi_pct("YTD % Change", ytd_pct, "#f1c40f")
    ]

    # MTD Calcs (Strictly capped to current day for accurate LY comparison)
    mtd_cy = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month == anchor_d.month)][
        'Value'].sum()
    current_year = pd.Timestamp.now().year

    if target_year == current_year:
        mtd_ly = df_sec[(df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == anchor_d.month) & (
                    df_sec['Date'].dt.day <= anchor_d.day)]['Value'].sum()
    else:
        mtd_ly = df_sec[(df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == anchor_d.month)][
            'Value'].sum()

    mtd_var = mtd_cy - mtd_ly
    mtd_pct = (mtd_var / mtd_ly * 100) if mtd_ly > 0 else 0

    # When the anchor month is in progress, MTD is a to-date window (both years
    # capped to the anchor day) — spell out the range so the partial LY is clear.
    if target_year == current_year:
        mtd_period_lbl = f"{anchor_d.strftime('%b')} 1–{anchor_d.day}, {anchor_d.year}"
    else:
        mtd_period_lbl = anchor_d.strftime('%b %Y')

    mtd_l1 = [
        create_v1_kpi(f"MTD Revenue ({mtd_period_lbl})", mtd_cy, "#3498db"),
        create_v1_kpi(f"MTD Revenue (LY, {mtd_period_lbl.replace(str(anchor_d.year), str(anchor_d.year - 1))})", mtd_ly, "#9b59b6"),
        create_v1_kpi("MTD Variance", mtd_var, "#e67e22"),
        create_kpi_pct("MTD % Change", mtd_pct, "#f1c40f")
    ]

    # --- LEVEL 2 & 3 KPI MATH (Browser Cache Generation) ---
    kpi_store_data = {}
    # Anchor context so the drill-down can label the in-progress month's to-date window.
    kpi_store_data['_meta'] = {'anchor_month': int(anchor_d.month), 'anchor_day': int(anchor_d.day),
                               'is_current': target_year == current_year}
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ytd_left, ytd_right = [], []
    mtd_left, mtd_right = [], []

    max_month = anchor_d.month if target_year == current_year else 12

    # --- Monthly progression card builder (label + Sales/QTY/Avg Price mini-table) ---
    qty_label = "QTY"

    def fmt_arrow(v, size='11px'):
        if v == 0:
            return html.Span("-", style={'color': '#bdc3c7', 'fontSize': size})
        return html.Span(f"{'▲' if v > 0 else '▼'} {abs(v):.1f}%",
                         style={'color': '#2ecc71' if v > 0 else '#e74c3c',
                                'fontWeight': 'bold', 'fontSize': size})

    _th_style = {'textAlign': 'right', 'fontWeight': 'normal', 'fontSize': '10px',
                 'color': '#95a5a6', 'padding': '1px 5px'}
    _td_name = {'textAlign': 'left', 'padding': '4px 5px', 'fontSize': '11px',
                'color': '#2c3e50', 'fontWeight': 'bold', 'borderTop': '1px solid #f0f0f0'}
    _td_val = {'textAlign': 'right', 'padding': '4px 5px', 'fontSize': '11px',
               'color': '#7f8c8d', 'borderTop': '1px solid #f0f0f0'}
    _td_pct = {'textAlign': 'right', 'padding': '4px 5px', 'borderTop': '1px solid #f0f0f0'}

    def _metric_row(name, cy, ly, pct, decimals=0):
        return html.Tr([
            html.Td(name, style=_td_name),
            html.Td(f"{cy:,.{decimals}f}", style=_td_val),
            html.Td(f"{ly:,.{decimals}f}", style=_td_val),
            html.Td(fmt_arrow(pct), style=_td_pct)
        ])

    def build_progression_card(label, card_type, m, sales, qty, avgp):
        # sales / qty / avgp are (cy, ly, pct) tuples
        return html.Div([
            html.Div(label, style={'minWidth': '72px', 'fontSize': '13px',
                                   'fontWeight': 'bold', 'color': '#2c3e50'}),
            html.Table([
                html.Thead(html.Tr([
                    html.Th("", style=_th_style), html.Th("CY", style=_th_style),
                    html.Th("LY", style=_th_style), html.Th("%", style=_th_style)
                ])),
                html.Tbody([
                    _metric_row("Sales", sales[0], sales[1], sales[2], 0),
                    _metric_row(qty_label, qty[0], qty[1], qty[2], 0),
                    _metric_row("Avg Price", avgp[0], avgp[1], avgp[2], 2),
                ])
            ], style={'width': '100%', 'borderCollapse': 'collapse'})
        ], id={'type': card_type, 'index': m}, n_clicks=0,
            style={'border': '1px solid #e0e0e0', 'borderRadius': '6px', 'padding': '10px',
                   'backgroundColor': 'white', 'cursor': 'pointer',
                   'boxShadow': '0 1px 3px rgba(0,0,0,0.05)', 'transition': '0.2s',
                   'display': 'flex', 'alignItems': 'center', 'gap': '12px'})

    for m in range(1, max_month + 1):
        # --- BASE YTD MATH (Sales + Volume + Avg Price) ---
        y_cy_mask = (df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month <= m)
        if m == anchor_d.month and target_year == current_year:
            y_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month <= m) & (
                        (df_sec['Date'].dt.month < m) | (df_sec['Date'].dt.day <= anchor_d.day))
        else:
            y_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month <= m)
        y_cy = df_sec[y_cy_mask]['Value'].sum()
        y_ly = df_sec[y_ly_mask]['Value'].sum()
        y_pct = ((y_cy - y_ly) / y_ly * 100) if y_ly > 0 else 0
        y_qty_cy = df_sec[y_cy_mask][qty_col].sum() if qty_col in df_sec.columns else 0
        y_qty_ly = df_sec[y_ly_mask][qty_col].sum() if qty_col in df_sec.columns else 0
        y_qty_pct = ((y_qty_cy - y_qty_ly) / y_qty_ly * 100) if y_qty_ly > 0 else 0
        y_ap_cy = (y_cy / y_qty_cy) if y_qty_cy > 0 else 0
        y_ap_ly = (y_ly / y_qty_ly) if y_qty_ly > 0 else 0
        y_ap_pct = ((y_ap_cy - y_ap_ly) / y_ap_ly * 100) if y_ap_ly > 0 else 0

        # --- BASE MTD MATH (Sales + Volume + Avg Price) ---
        m_cy_mask = (df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month == m)
        if m == anchor_d.month and target_year == current_year:
            m_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == m) & (
                        df_sec['Date'].dt.day <= anchor_d.day)
        else:
            m_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == m)
        m_cy = df_sec[m_cy_mask]['Value'].sum()
        m_ly = df_sec[m_ly_mask]['Value'].sum()
        m_pct = ((m_cy - m_ly) / m_ly * 100) if m_ly > 0 else 0
        m_qty_cy = df_sec[m_cy_mask][qty_col].sum() if qty_col in df_sec.columns else 0
        m_qty_ly = df_sec[m_ly_mask][qty_col].sum() if qty_col in df_sec.columns else 0
        m_qty_pct = ((m_qty_cy - m_qty_ly) / m_qty_ly * 100) if m_qty_ly > 0 else 0
        m_ap_cy = (m_cy / m_qty_cy) if m_qty_cy > 0 else 0
        m_ap_ly = (m_ly / m_qty_ly) if m_qty_ly > 0 else 0
        m_ap_pct = ((m_ap_cy - m_ap_ly) / m_ap_ly * 100) if m_ap_ly > 0 else 0

        # --- SEGMENT YoY MATH ---
        y_b2b_cy, y_b2b_ly, y_b2b_pct = 0, 0, 0
        y_b2c_cy, y_b2c_ly, y_b2c_pct = 0, 0, 0
        m_b2b_cy, m_b2b_ly, m_b2b_pct = 0, 0, 0
        m_b2c_cy, m_b2c_ly, m_b2c_pct = 0, 0, 0

        if 'B2B' in df_sec.columns:
            y_b2b_mask = (df_sec['B2B'].astype(str).str.strip().str.upper() == 'B2B')
            y_b2c_mask = (df_sec['B2B'].astype(str).str.strip().str.upper() == 'B2C')

            # YTD / MTD current — straight sums, no cap needed on current year
            y_b2b_cy = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month <= m) & y_b2b_mask]['Value'].sum()
            y_b2c_cy = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month <= m) & y_b2c_mask]['Value'].sum()
            m_b2b_cy = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month == m) & y_b2b_mask]['Value'].sum()
            m_b2c_cy = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month == m) & y_b2c_mask]['Value'].sum()

            if m == anchor_d.month and target_year == current_year:
                # Cap each segment's LY to its own latest date so a segment with fewer
                # data days isn't penalised by the other segment's later anchor.
                _b2b_last = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month == m) & y_b2b_mask]['Date'].max()
                _b2c_last = df_sec[(df_sec['Date'].dt.year == target_year) & (df_sec['Date'].dt.month == m) & y_b2c_mask]['Date'].max()
                b2b_day = _b2b_last.day if pd.notna(_b2b_last) else anchor_d.day
                b2c_day = _b2c_last.day if pd.notna(_b2c_last) else anchor_d.day

                y_b2b_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month <= m) & (
                    (df_sec['Date'].dt.month < m) | (df_sec['Date'].dt.day <= b2b_day))
                y_b2c_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month <= m) & (
                    (df_sec['Date'].dt.month < m) | (df_sec['Date'].dt.day <= b2c_day))
                m_b2b_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == m) & (
                    df_sec['Date'].dt.day <= b2b_day)
                m_b2c_ly_mask = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == m) & (
                    df_sec['Date'].dt.day <= b2c_day)
            else:
                _base_ytd = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month <= m)
                _base_mtd = (df_sec['Date'].dt.year == target_year - 1) & (df_sec['Date'].dt.month == m)
                y_b2b_ly_mask = y_b2c_ly_mask = _base_ytd
                m_b2b_ly_mask = m_b2c_ly_mask = _base_mtd

            y_b2b_ly = df_sec[y_b2b_ly_mask & y_b2b_mask]['Value'].sum()
            y_b2c_ly = df_sec[y_b2c_ly_mask & y_b2c_mask]['Value'].sum()
            m_b2b_ly = df_sec[m_b2b_ly_mask & y_b2b_mask]['Value'].sum()
            m_b2c_ly = df_sec[m_b2c_ly_mask & y_b2c_mask]['Value'].sum()

            y_b2b_pct = ((y_b2b_cy - y_b2b_ly) / y_b2b_ly * 100) if y_b2b_ly > 0 else 0
            y_b2c_pct = ((y_b2c_cy - y_b2c_ly) / y_b2c_ly * 100) if y_b2c_ly > 0 else 0
            m_b2b_pct = ((m_b2b_cy - m_b2b_ly) / m_b2b_ly * 100) if m_b2b_ly > 0 else 0
            m_b2c_pct = ((m_b2c_cy - m_b2c_ly) / m_b2c_ly * 100) if m_b2c_ly > 0 else 0

        # Pack Store
        kpi_store_data[str(m)] = {
            'ytd': {'cy': y_cy, 'ly': y_ly, 'pct': y_pct, 'b2b_cy': y_b2b_cy, 'b2b_ly': y_b2b_ly, 'b2b_pct': y_b2b_pct,
                    'b2c_cy': y_b2c_cy, 'b2c_ly': y_b2c_ly, 'b2c_pct': y_b2c_pct},
            'mtd': {'cy': m_cy, 'ly': m_ly, 'pct': m_pct, 'b2b_cy': m_b2b_cy, 'b2b_ly': m_b2b_ly, 'b2b_pct': m_b2b_pct,
                    'b2c_cy': m_b2c_cy, 'b2c_ly': m_b2c_ly, 'b2c_pct': m_b2c_pct}
        }

        # Build UI Cards (label + Sales/QTY/Avg Price mini-table)
        ytd_card = build_progression_card(
            f"Jan - {month_names[m - 1]}", 'ytd-m-card', m,
            (y_cy, y_ly, y_pct), (y_qty_cy, y_qty_ly, y_qty_pct), (y_ap_cy, y_ap_ly, y_ap_pct))

        # In-progress month is a to-date window (CY & LY both capped to anchor day),
        # so label it with the day range to make the partial period explicit.
        if m == anchor_d.month and target_year == current_year:
            mtd_label = f"{month_names[m - 1]} 1–{anchor_d.day}"
        else:
            mtd_label = f"{month_names[m - 1]} Only"
        mtd_card = build_progression_card(
            mtd_label, 'mtd-m-card', m,
            (m_cy, m_ly, m_pct), (m_qty_cy, m_qty_ly, m_qty_pct), (m_ap_cy, m_ap_ly, m_ap_pct))

        if m <= 6:
            ytd_left.append(ytd_card)
            mtd_left.append(mtd_card)
        else:
            ytd_right.append(ytd_card)
            mtd_right.append(mtd_card)

    ytd_l2 = [html.Div(ytd_left, style={'width': '48%', 'display': 'flex', 'flexDirection': 'column', 'gap': '10px'}),
              html.Div(ytd_right, style={'width': '48%', 'display': 'flex', 'flexDirection': 'column', 'gap': '10px'})]
    mtd_l2 = [html.Div(mtd_left, style={'width': '48%', 'display': 'flex', 'flexDirection': 'column', 'gap': '10px'}),
              html.Div(mtd_right, style={'width': '48%', 'display': 'flex', 'flexDirection': 'column', 'gap': '10px'})]

    # --- CHARTS & TRENDS ---
    df_ytd, df_lytd, lbl_ytd = core_engine.get_time_window(df_sec, target_year, 'Year')
    trend_df, _, _ = core_engine.get_trend_data(df_ytd, view, vol_col)
    title_trend = s.get_chart_title(f"Revenue Trend (YTD Context)", df_ytd, df_lytd, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend_df, 'Date', 'Value', title_trend, vol_col, METRIC_MODE, use_z_colors=True)
    anno_trend = s.get_storytelling_anno(trend_df, 'Date', 'Value', vol_col, METRIC_MODE)

    df_curr, df_prev, lbl = core_engine.get_time_window(df_sec, target_year, view, trend_date)

    if daily_date:
        try:
            # .split('T')[0] ensures any weird trailing time artifacts are stripped
            exact_ts = pd.to_datetime(str(daily_date).split('T')[0].split(' ')[0])
            exact_d = exact_ts.date()
            prev_d = (exact_ts - pd.DateOffset(years=1)).date()
            df_curr = df_curr[df_curr['Date'].dt.date == exact_d]
            df_prev = df_prev[df_prev['Date'].dt.date == prev_d]
            lbl = exact_d.strftime('%d %b %Y')
        except Exception:
            pass  # Failsafe back to the trend_date if the click data is utterly broken

    top_p = core_engine.get_top_performers(df_curr, h_col, vol_col, top_n=15)
    title_p = s.get_chart_title(f"Top Product Categories ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_p = s.create_bar_chart(top_p, h_col, 'Value', title_p, vol_col, METRIC_MODE, is_horizontal=True)
    anno_p = s.get_storytelling_anno(top_p, h_col, 'Value', vol_col, METRIC_MODE)

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df_curr.columns), None)
    top_c = core_engine.get_top_performers(df_curr, cust_col, vol_col)
    title_c = s.get_chart_title(f"Top Customers ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_c = s.create_bar_chart(top_c, cust_col, 'Value', title_c, vol_col, METRIC_MODE, is_horizontal=True)
    fig_c.update_traces(marker_color='#2ecc71')
    anno_c = s.get_storytelling_anno(top_c, cust_col, 'Value', vol_col, METRIC_MODE)

    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Rep'] if c in df_curr.columns), None)
    top_r = core_engine.get_top_performers(df_curr, rep_col, vol_col) if rep_col else pd.DataFrame()
    title_r = s.get_chart_title(f"Top Sales Reps ({lbl})", df_curr, df_prev, METRIC_MODE) if rep_col else "No Rep Data"
    fig_r = s.create_bar_chart(top_r, rep_col, 'Value', title_r, vol_col, METRIC_MODE,
                               is_horizontal=True) if not top_r.empty else go.Figure()
    fig_r.update_traces(marker_color='#e67e22')
    anno_r = s.get_storytelling_anno(top_r, rep_col, 'Value', vol_col, METRIC_MODE)

    # --- INVENTORY & AGING ---
    _, df_soh_fresh, _, _ = get_latest_data()
    df_soh_local = df_soh_fresh.copy() if not df_soh_fresh.empty else pd.DataFrame()
    df_soh_local.columns = [str(c).strip() for c in df_soh_local.columns]

    if 'Items' in df_soh_local.columns:
        df_soh_local['Join_Key_SOH'] = df_soh_local['Items'].astype(str).str.strip().str.upper()
    else:
        df_soh_local['Join_Key_SOH'] = 'MISSING'

    if 'Qty On Hand' not in df_soh_local.columns:
        qih_c = next(
            (c for c in df_soh_local.columns if 'qih' in c.lower() or 'qty' in c.lower() or 'stock' in c.lower()), None)
        if qih_c:
            df_soh_local.rename(columns={qih_c: 'Qty On Hand'}, inplace=True)
        else:
            df_soh_local['Qty On Hand'] = 0

    if not df_sec.empty and 'Item Code' in df_sec.columns and 'Name' in df_sec.columns:
        hier_df = df_sec[['Items 2', 'Items', 'Name', 'Item Code', 'Date']].copy()
        last_sales = hier_df.groupby(['Items 2', 'Items', 'Name', 'Item Code'])['Date'].max().reset_index()
        last_sales['Days_Since'] = (anchor_d - last_sales['Date']).dt.days
        last_sales['Join_Key_Sales'] = last_sales['Item Code'].astype(str).str.strip().str.upper()

        aging_df = pd.merge(last_sales, df_soh_local[['Join_Key_SOH', 'Qty On Hand']], left_on='Join_Key_Sales',
                            right_on='Join_Key_SOH', how='left')
        aging_df['Qty On Hand'] = aging_df['Qty On Hand'].fillna(0)
        aging_df = aging_df[aging_df['Qty On Hand'] > 0].copy()

        cond_12 = aging_df['Days_Since'] >= 365
        cond_6 = (aging_df['Days_Since'] >= 180) & (aging_df['Days_Since'] < 365)
        cond_3 = (aging_df['Days_Since'] >= 90) & (aging_df['Days_Since'] < 180)

        aging_df['12 Month'] = np.where(cond_12, aging_df['Qty On Hand'], None)
        aging_df['6 Month'] = np.where(cond_6, aging_df['Qty On Hand'], None)
        aging_df['3 Month'] = np.where(cond_3, aging_df['Qty On Hand'], None)

        slow_data = aging_df[cond_12 | cond_6 | cond_3].to_dict('records')
    else:
        slow_data = []

    days = (df_curr['Date'].max() - df_curr['Date'].min()).days + 1 if not df_curr.empty else 1
    if days < 1: days = 1

    if not df_curr.empty and 'Item Code' in df_curr.columns and 'Name' in df_curr.columns:
        static = df_curr.groupby(['Item Code', 'Name']).agg(Trans_Count=(vol_col, 'count'),
                                                            Qty=(vol_col, 'sum')).reset_index()
        static['Daily_Sales'] = static['Qty'] / days
        static['Pct'] = static['Trans_Count'].rank(pct=True)
        static['Days_Cover'] = np.select([(static['Pct'] > 0.9), (static['Pct'] > 0.8)], [12, 8], default=6)
        static['Max_Stock'] = np.ceil((static['Daily_Sales'] * static['Days_Cover']) / 50) * 50
        static['ROL'] = np.ceil(static['Max_Stock'] / 2)
        static['Join_Key_Sales'] = static['Item Code'].astype(str).str.strip().str.upper()
    else:
        static = pd.DataFrame(columns=['Item Code', 'Name', 'Daily_Sales', 'Max_Stock', 'ROL', 'Join_Key_Sales'])

    df_inv = pd.merge(static, df_soh_local[['Join_Key_SOH', 'Qty On Hand']], left_on='Join_Key_Sales',
                      right_on='Join_Key_SOH', how='right').fillna(0)
    df_inv = df_inv[df_inv['Qty On Hand'] > 0].copy()
    df_inv['Status'] = np.where(df_inv['Qty On Hand'] < df_inv['ROL'], 'Understocked',
                                np.where(df_inv['Qty On Hand'] > df_inv['Max_Stock'], 'Overstocked', 'Healthy'))

    over = df_inv[df_inv['Status'] == 'Overstocked'].sort_values('Qty On Hand', ascending=False)
    under = df_inv[df_inv['Status'] == 'Understocked'].sort_values('ROL', ascending=False)
    table_data = core_engine.get_performance_table(df_curr, df_prev, h_col, vol_col)

    # --- THE INFINITY SCRUBBER ---
    # Intercepts the table data and neutralizes Infinity/NaN before it crashes the Dash UI
    if isinstance(table_data, pd.DataFrame):
        table_data = table_data.replace([np.inf, -np.inf], np.nan).where(pd.notnull(table_data), None).to_dict(
            'records')
    elif isinstance(table_data, list):
        for row in table_data:
            for k, v in row.items():
                if isinstance(v, float) and (pd.isna(v) or np.isinf(v)):
                    row[k] = None

    return (ytd_l1, mtd_l1, ytd_l2, mtd_l2, kpi_store_data, fig_trend, anno_trend, fig_p, anno_p, slow_data,
            fig_c, anno_c, fig_r, anno_r, over.to_dict('records'), under.to_dict('records'), table_data)


# --- KPI INTERACTIVITY CALLBACK (Level 2 & 3 Drilldowns) ---
@callback(
    [Output('ytd-kpi-l2', 'style'), Output('ytd-kpi-l3', 'style'), Output('ytd-kpi-l3', 'children'),
     Output('btn-ytd-toggle', 'children'),
     Output('mtd-kpi-l2', 'style'), Output('mtd-kpi-l3', 'style'), Output('mtd-kpi-l3', 'children'),
     Output('btn-mtd-toggle', 'children')],
    [Input('btn-ytd-toggle', 'n_clicks'), Input('btn-mtd-toggle', 'n_clicks'),
     Input({'type': 'ytd-m-card', 'index': ALL}, 'n_clicks'), Input({'type': 'mtd-m-card', 'index': ALL}, 'n_clicks')],
    [State('kpi-data-store', 'data'), State('ytd-kpi-l2', 'style'), State('mtd-kpi-l2', 'style')]
)
def handle_kpi_interactivity(ytd_toggle, mtd_toggle, ytd_clicks, mtd_clicks, kpi_data, ytd_l2_style, mtd_l2_style):
    if not ctx.triggered or not ctx.triggered[0]['value']: return dash.no_update

    trig = ctx.triggered_id

    # Init Defaults
    o_y_l2 = ytd_l2_style or {'display': 'none'}
    o_y_l3_style, o_y_l3_child, y_btn = {'display': 'none'}, "", "▼ Show YTD Monthly Progression"
    o_m_l2 = mtd_l2_style or {'display': 'none'}
    o_m_l3_style, o_m_l3_child, m_btn = {'display': 'none'}, "", "▼ Show MTD Monthly Comparison"

    # Restore button states based on current visibility
    if o_y_l2.get('display') != 'none': y_btn = "▲ Hide YTD Monthly Progression"
    if o_m_l2.get('display') != 'none': m_btn = "▲ Hide MTD Monthly Comparison"

    # Handle Toggles
    if trig == 'btn-ytd-toggle':
        if o_y_l2.get('display') == 'none':
            o_y_l2, y_btn = {'display': 'flex', 'justifyContent': 'space-between',
                             'gap': '20px'}, "▲ Hide YTD Monthly Progression"
        else:
            o_y_l2, y_btn = {'display': 'none'}, "▼ Show YTD Monthly Progression"
    elif trig == 'btn-mtd-toggle':
        if o_m_l2.get('display') == 'none':
            o_m_l2, m_btn = {'display': 'flex', 'justifyContent': 'space-between',
                             'gap': '20px'}, "▲ Hide MTD Monthly Comparison"
        else:
            o_m_l2, m_btn = {'display': 'none'}, "▼ Show MTD Monthly Comparison"

    # Handle Drilldowns
    elif isinstance(trig, dict):
        m_index = str(trig.get('index'))
        card_type = trig.get('type')
        data = kpi_data.get(m_index, {})
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        m_name = month_names[int(m_index) - 1]

        def build_l3_ui(title, subset):
            # --- Formatters for the Overview Cards ---
            def m_kpi(t, v1, v2, is_pct=False):
                c = '#2ecc71' if v1 >= 0 else '#e74c3c'
                a = '▲' if v1 >= 0 else '▼'
                v_str = f"{a} {abs(v1):.1f}%" if is_pct and v1 != 0 else (f"KES {v1:,.0f}" if not is_pct else "-")
                return html.Div([
                    html.Div(t, style={'fontSize': '11px', 'color': '#7f8c8d', 'fontWeight': 'bold',
                                       'textTransform': 'uppercase'}),
                    html.Div(v_str,
                             style={'fontSize': '16px', 'color': c if is_pct else '#2c3e50', 'fontWeight': 'bold',
                                    'marginTop': '5px'}),
                    html.Div(f"LY: {v2:,.0f}" if not is_pct else "", style={'fontSize': '10px', 'color': '#95a5a6'})
                ], style={'textAlign': 'center', 'flex': '1'})

            # --- Formatters for the Segment Table ---
            def fmt_cur(v):
                return f"KES {v:,.0f}"

            def fmt_var(v):
                if v == 0: return html.Span("-", style={'color': '#bdc3c7'})
                color = '#2ecc71' if v > 0 else '#e74c3c'
                sign = '+' if v > 0 else ''
                return html.Span(f"{sign}{v:,.0f}", style={'color': color, 'fontWeight': 'bold'})

            def fmt_pct(v):
                if v == 0: return html.Span("-", style={'color': '#bdc3c7'})
                color = '#2ecc71' if v > 0 else '#e74c3c'
                arrow = '▲' if v > 0 else '▼'
                return html.Span(f"{arrow} {abs(v):.1f}%", style={'color': color, 'fontWeight': 'bold'})

            b2b_var = subset['b2b_cy'] - subset['b2b_ly']
            b2c_var = subset['b2c_cy'] - subset['b2c_ly']

            th_style = {'padding': '8px 10px', 'borderBottom': '2px solid #bdc3c7', 'textAlign': 'right',
                        'color': '#7f8c8d', 'fontSize': '11px', 'textTransform': 'uppercase'}
            td_style = {'padding': '10px', 'borderBottom': '1px solid #eee', 'textAlign': 'right', 'fontSize': '14px',
                        'color': '#2c3e50', 'fontWeight': 'bold'}
            td_left = {**td_style, 'textAlign': 'left', 'color': '#95a5a6', 'width': '15%'}

            segment_table = html.Table([
                html.Thead(html.Tr([
                    html.Th("Segment", style={**th_style, 'textAlign': 'left'}),
                    html.Th("Current", style=th_style),
                    html.Th("Previous (LY)", style=th_style),
                    html.Th("Variance", style=th_style),
                    html.Th("Growth", style=th_style)
                ])),
                html.Tbody([
                    html.Tr([
                        html.Td("B2B", style=td_left),
                        html.Td(fmt_cur(subset['b2b_cy']), style=td_style),
                        html.Td(fmt_cur(subset['b2b_ly']), style=td_style),
                        html.Td(fmt_var(b2b_var), style=td_style),
                        html.Td(fmt_pct(subset['b2b_pct']), style=td_style)
                    ]),
                    html.Tr([
                        html.Td("B2C", style=td_left),
                        html.Td(fmt_cur(subset['b2c_cy']), style=td_style),
                        html.Td(fmt_cur(subset['b2c_ly']), style=td_style),
                        html.Td(fmt_var(b2c_var), style=td_style),
                        html.Td(fmt_pct(subset['b2c_pct']), style=td_style)
                    ], style={'backgroundColor': '#fcfcfc'})
                ])
            ], style={'width': '100%', 'borderCollapse': 'collapse', 'marginTop': '5px'})

            return html.Div([
                html.H3(title,
                        style={'textAlign': 'center', 'color': '#2c3e50', 'margin': '0 0 15px 0', 'fontSize': '16px',
                               'fontWeight': 'bold'}),

                html.H4("Total Revenue Overview",
                        style={'textAlign': 'center', 'color': '#7f8c8d', 'fontSize': '12px', 'margin': '0 0 10px 0',
                               'textTransform': 'uppercase'}),
                html.Div([m_kpi("Current", subset['cy'], subset['ly']), m_kpi("Previous", subset['ly'], subset['ly']),
                          m_kpi("Total Growth", subset['pct'], 0, True)],
                         style={'display': 'flex', 'justifyContent': 'space-around', 'marginBottom': '20px',
                                'borderBottom': '1px solid #eee', 'paddingBottom': '15px'}),

                html.H4("Segment Breakdowns",
                        style={'textAlign': 'center', 'color': '#7f8c8d', 'fontSize': '12px', 'margin': '0 0 5px 0',
                               'textTransform': 'uppercase'}),
                segment_table
            ])

        if card_type == 'ytd-m-card' and 'ytd' in data:
            o_y_l2 = {'display': 'flex', 'justifyContent': 'space-between', 'gap': '20px'}
            o_y_l3_style = {'display': 'block', 'marginTop': '20px', 'padding': '15px', 'backgroundColor': '#f9f9f9',
                            'border': '1px solid #ddd', 'borderRadius': '8px',
                            'boxShadow': 'inset 0 2px 4px rgba(0,0,0,0.03)'}
            o_y_l3_child = build_l3_ui(f"YTD Breakdown: Jan - {m_name}", data['ytd'])

        elif card_type == 'mtd-m-card' and 'mtd' in data:
            o_m_l2 = {'display': 'flex', 'justifyContent': 'space-between', 'gap': '20px'}
            o_m_l3_style = {'display': 'block', 'marginTop': '20px', 'padding': '15px', 'backgroundColor': '#f9f9f9',
                            'border': '1px solid #ddd', 'borderRadius': '8px',
                            'boxShadow': 'inset 0 2px 4px rgba(0,0,0,0.03)'}
            meta = kpi_data.get('_meta', {})
            if meta.get('is_current') and int(m_index) == meta.get('anchor_month'):
                mtd_l3_title = f"MTD Breakdown: {m_name} 1–{meta.get('anchor_day')}"
            else:
                mtd_l3_title = f"MTD Breakdown: {m_name} Only"
            o_m_l3_child = build_l3_ui(mtd_l3_title, data['mtd'])

    return o_y_l2, o_y_l3_style, o_y_l3_child, y_btn, o_m_l2, o_m_l3_style, o_m_l3_child, m_btn


# ... (The drill_top_product and download_snapshot callbacks remain identical) ...

@callback(
    [Output('v1-drilldown-container', 'style'), Output('v1-prod-drilldown', 'figure'),
     Output('v1-prod-drilldown-anno', 'children')],
    [Input('v1-top-products', 'clickData'), Input('v1-year', 'value'), Input('v1-view', 'value'),
     Input('v1-hier', 'value'), Input('v1-segment', 'value')],
    [State('auth-store', 'data')]
)
def drill_top_product(clk, year, view, h_col, segment, auth_data):
    if not clk or not year: return {'display': 'none'}, go.Figure(), ""
    item = clk['points'][0]['y']
    df_sec = get_secure_v1_df(auth_data)
    if df_sec.empty: return {'display': 'none'}, go.Figure(), ""
    target_year = int(year)
    if segment != 'All' and 'B2B' in df_sec.columns: df_sec = df_sec[
        df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    df_item = df_sec[df_sec[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()]
    if df_item.empty: return {'display': 'none'}, go.Figure(), ""
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    df_ytd, df_lytd, lbl_ytd = core_engine.get_time_window(df_item, target_year, 'Year')
    trend_df, _, _ = core_engine.get_trend_data(df_ytd, view, vol_col)
    header = s.get_chart_title(f"Sales Trend: {item} (YTD Context)", df_ytd, df_lytd, METRIC_MODE)
    fig = s.create_bar_chart(trend_df, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    return {'display': 'block', 'marginTop': '20px'}, fig, s.get_storytelling_anno(trend_df, 'Date', 'Value', vol_col,
                                                                                   METRIC_MODE)


# --- ITEM DRILL-DOWN: re-scope Top Customers + Top Reps to the clicked product ---
# Fires only on a product-bar click (not on year/view/segment changes), so it never
# races update_main_v1 — which still resets these charts to all-items whenever the
# year, period, hierarchy or segment changes. The item filter is applied on top of
# the SAME period window that produced the Top Products chart.
@callback(
    [Output('v1-top-customers', 'figure', allow_duplicate=True),
     Output('v1-cust-anno', 'children', allow_duplicate=True),
     Output('v1-top-reps', 'figure', allow_duplicate=True),
     Output('v1-rep-anno', 'children', allow_duplicate=True)],
    [Input('v1-top-products', 'clickData')],
    [State('v1-year', 'value'), State('v1-view', 'value'), State('v1-hier', 'value'),
     State('v1-segment', 'value'), State('v1-trend-date', 'data'), State('v1-daily-date', 'data'),
     State('auth-store', 'data')],
    prevent_initial_call=True,
)
def drill_item_customers_reps(clk, year, view, h_col, segment, trend_date, daily_date, auth_data):
    if not clk or not year:
        return no_update, no_update, no_update, no_update
    item = clk['points'][0]['y']

    df_sec = get_secure_v1_df(auth_data)
    if df_sec.empty:
        return no_update, no_update, no_update, no_update
    target_year = int(year)
    if segment != 'All' and 'B2B' in df_sec.columns:
        df_sec = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    # Same period window the Top Products chart used (month -> day drill).
    df_curr, df_prev, lbl = core_engine.get_time_window(df_sec, target_year, view, trend_date)
    if daily_date:
        try:
            exact_ts = pd.to_datetime(str(daily_date).split('T')[0].split(' ')[0])
            exact_d = exact_ts.date()
            prev_d = (exact_ts - pd.DateOffset(years=1)).date()
            df_curr = df_curr[df_curr['Date'].dt.date == exact_d]
            df_prev = df_prev[df_prev['Date'].dt.date == prev_d]
            lbl = exact_d.strftime('%d %b %Y')
        except Exception:
            pass

    # Filter both windows to the clicked product (same hierarchy column as the chart).
    if h_col in df_curr.columns:
        _mask_c = df_curr[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()
        _mask_p = df_prev[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()
        df_curr = df_curr[_mask_c]
        df_prev = df_prev[_mask_p]

    if df_curr.empty:
        return no_update, no_update, no_update, no_update

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df_curr.columns), None)
    top_c = core_engine.get_top_performers(df_curr, cust_col, vol_col)
    title_c = s.get_chart_title(f"Top Customers — {item} ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_c = s.create_bar_chart(top_c, cust_col, 'Value', title_c, vol_col, METRIC_MODE, is_horizontal=True)
    fig_c.update_traces(marker_color='#2ecc71')
    anno_c = s.get_storytelling_anno(top_c, cust_col, 'Value', vol_col, METRIC_MODE)

    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Rep'] if c in df_curr.columns), None)
    top_r = core_engine.get_top_performers(df_curr, rep_col, vol_col) if rep_col else pd.DataFrame()
    title_r = s.get_chart_title(f"Top Sales Reps — {item} ({lbl})", df_curr, df_prev, METRIC_MODE) if rep_col else "No Rep Data"
    fig_r = s.create_bar_chart(top_r, rep_col, 'Value', title_r, vol_col, METRIC_MODE,
                               is_horizontal=True) if not top_r.empty else go.Figure()
    fig_r.update_traces(marker_color='#e67e22')
    anno_r = s.get_storytelling_anno(top_r, rep_col, 'Value', vol_col, METRIC_MODE)

    return fig_c, anno_c, fig_r, anno_r


@callback(
    Output("v1-dl", "data"), Input("v1-snap", "n_clicks"),
    [State('v1-trend', 'figure'), State('v1-daily-trend', 'figure'),
     State('v1-top-products', 'figure'), State('v1-prod-drilldown', 'figure'),
     State('v1-top-customers', 'figure'), State('v1-top-reps', 'figure'),
     State('aging-data-store', 'data'), State('overstock-table', 'data'), State('understock-table', 'data')],
    prevent_initial_call=True
)
def download_snapshot(n, fig_t, fig_dt, fig_i, fig_d, fig_c, fig_r, slow_data, o_data, u_data):
    c_config = {'full_html': False, 'include_plotlyjs': 'cdn'}

    html_charts = [
        pio.to_html(go.Figure(fig_t), **c_config) if fig_t else "<div>No Trend Data</div>",
        pio.to_html(go.Figure(fig_dt), **c_config) if fig_dt else "<div>No Daily Drilldown Data</div>",
        pio.to_html(go.Figure(fig_i), **c_config) if fig_i else "<div>No Top Items Data</div>",
        pio.to_html(go.Figure(fig_d), **c_config) if fig_d else "<div>No Drilldown Data Selected</div>",
        pio.to_html(go.Figure(fig_c), **c_config) if fig_c else "<div>No Customer Data</div>",
        pio.to_html(go.Figure(fig_r), **c_config) if fig_r else "<div>No Rep Data</div>"
    ]

    def clean_table(data, cols_map):
        if not data: return None
        df = pd.DataFrame(data)
        available_cols = [c for c in cols_map.keys() if c in df.columns]
        if not available_cols: return None
        df = df[available_cols].rename(columns=cols_map)
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].apply(lambda x: f"{x:,.0f}" if pd.notnull(x) else "-")
        return df

    df_slow = clean_table(slow_data,
                          {'Items 2': 'Category', 'Items': 'Sub-Category', 'Name': 'Item', '12 Month': '12 Month',
                           '6 Month': '6 Month', '3 Month': '3 Month'})
    df_over = clean_table(o_data, {'Name': 'Item', 'Qty On Hand': 'QIH', 'Max_Stock': 'Max'})
    df_under = clean_table(u_data, {'Name': 'Item', 'Qty On Hand': 'QIH', 'ROL': 'ROL'})

    def df_to_html(df, title, color):
        if df is None or df.empty:
            return f"<div style='margin-bottom:20px; padding:10px; border-top: 4px solid {color}'><h3>{title}</h3><p>No Data</p></div>"
        return f"""
        <div style='margin-bottom: 20px; border-top: 4px solid {color}; background: white; padding: 15px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);'>
            <h3 style='color: {color}; text-align: center; margin-top: 0;'>{title}</h3>
            {df.to_html(index=False, border=0, classes='styled-table', justify='center')}
        </div>
        """

    html_tables = [
        df_to_html(df_slow, "⏳ Slow Moving Inventory", "#e67e22"),
        df_to_html(df_over, "🟣 Overstocked", "purple"),
        df_to_html(df_under, "🚨 Re-order Needed", "#d32f2f")
    ]

    full_html = f"""
    <html>
    <head>
        <title>Executive Snapshot</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background-color: #f4f4f4; padding: 20px; }}
            .chart-container {{ background: white; margin-bottom: 20px; padding: 15px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            .styled-table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; margin: 0 auto; }}
            .styled-table thead tr {{ background-color: #f8f9fa; border-bottom: 2px solid #ddd; }}
            .styled-table th {{ padding: 10px; text-align: center; color: #333; font-weight: bold; }}
            .styled-table td {{ padding: 8px; text-align: center; border-bottom: 1px solid #eee; color: #555; }}
            .styled-table tr:nth-of-type(even) {{ background-color: #fcfcfc; }}
            h1 {{ text-align: center; color: #2c3e50; }}
            .timestamp {{ text-align: center; color: #7f8c8d; font-size: 0.9em; margin-bottom: 30px; }}
            .inventory-row {{ display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap; }}
            .inventory-col {{ flex: 1; min-width: 300px; }}
            .full-width-col {{ flex: 100%; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <h1>Executive Dashboard Snapshot</h1>
        <p class="timestamp">Generated on {pd.Timestamp.now().strftime('%d-%b-%Y %H:%M')}</p>
        <div class="chart-container">{html_charts[0]}</div>
        <div class="chart-container">{html_charts[1]}</div>
        <div class="chart-container">{html_charts[2]}</div>
        <div class="chart-container">{html_charts[3]}</div>
        <div class="chart-container">{html_charts[4]}</div>
        <div class="chart-container">{html_charts[5]}</div>

        <h2 style="text-align:center; color:#c0392b; margin-top:40px;">Inventory Risk Management</h2>

        <div class="inventory-row">
            <div class="full-width-col">{html_tables[0]}</div>
        </div>

        <div class="inventory-row">
            <div class="inventory-col">{html_tables[1]}</div>
            <div class="inventory-col">{html_tables[2]}</div>
        </div>
    </body>
    </html>
    """
    return dict(content=full_html, filename=f"v1_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")


# --- AGING REPORT FAST-FILTER CALLBACK ---
@callback(
    [Output('slow-moving-table', 'data'), Output('aging-subcat-filter', 'options')],
    [Input('aging-subcat-filter', 'value'), Input('aging-data-store', 'data')]
)
def filter_aging_report(selected_subcat, store_data):
    if not store_data:
        return [], []

    df_aging = pd.DataFrame(store_data)

    # Automatically generate the dropdown options based on whatever is actually in the aging report
    available_subcats = sorted(df_aging['Items'].dropna().unique())
    options = [{'label': i, 'value': i} for i in available_subcats]

    # Apply the filter if the user selected one
    if selected_subcat:
        df_aging = df_aging[df_aging['Items'] == selected_subcat]

    return df_aging.to_dict('records'), options