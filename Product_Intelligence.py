import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine

import dash
from dash import html, dcc, Input, Output, State, ctx, callback, no_update, dash_table
from dash.dash_table.Format import Format, Scheme, Group
import plotly.graph_objects as go
import pandas as pd
import plotly.io as pio
from data_loader import df_global, df_soh_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security
import numpy as np
# --- STYLING HELPERS ---
SCROLL_CONTAINER_STYLE = {
    'height': '83vh', 'overflowY': 'scroll', 'overflowX': 'hidden',
    'backgroundColor': s.COLORS.get('card', 'white'),
    'border': f"1px solid {s.COLORS.get('border', '#e0e0e0')}",
    'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)', 'padding': '10px'
}

DEBUG_STYLE = {
    'width': '100%', 'backgroundColor': '#2c3e50', 'color': '#2ecc71', 'padding': '15px',
    'fontFamily': 'monospace', 'fontSize': '13px', 'maxHeight': '200px',
    'overflowY': 'scroll', 'marginBottom': '20px', 'borderRadius': '5px',
    'border': '1px solid #34495e', 'lineHeight': '1.5'
}


def get_secure_pi_df(auth_data):
    _df, _, _, _ = get_latest_data()
    return enforce_row_level_security(_df, auth_data)


# --- LAYOUT ---
layout = html.Div([
    dcc.Store(id='pi-item'), dcc.Store(id='pi-drill-item'),
    dcc.Store(id='pi-customer'), dcc.Store(id='pi-cust-date'),
    dcc.Store(id='pi-rep'), dcc.Store(id='pi-rep-date'),
    dcc.Store(id='pi-dead-item'), dcc.Store(id='pi-trend-date'),
    dcc.Download(id="pi-dl"),

    html.Div([
        html.H1("Product Intelligence", style=s.HEADER_TITLE),
        html.Div(id='pi-debug-console', style=DEBUG_STYLE, children="Initializing System..."),

        html.Div([
            html.Div([
                html.Span("SEARCH:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.Dropdown(id='pi-search', placeholder="Search Item...", style={'width': '200px', 'fontSize': '13px'},
                             optionHeight=30),
            ], style={'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("LEVEL:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='pi-hier',
                           options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),

            # Locate this section in the layout and replace with:
            html.Div(id='pi-segment-container', children=[
                html.Span("SEGMENT:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.RadioItems(id='pi-segment-filter',
                               options=[{'label': 'All', 'value': 'All'}, {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', inline=True,
                               style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),
            ], style={'display': 'inline-block'}),

            html.Span("YEAR:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.Dropdown(id='pi-year', options=[{'label': str(y), 'value': y} for y in years_global],
                         value=years_global[0] if years_global else None, clearable=False,
                         style={'width': '80px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("VIEW:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='pi-view', options=['Week', 'Month', 'Quarter', 'Year'], value='Year', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Button("📸 Snapshot", id="pi-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'justifyContent': 'center',
                  'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '8px',
                  'border': f'1px solid {s.COLORS["border"]}'})
    ], style=s.SECTION),

    html.Div([
        html.H3("Top 60 Items", style={'textAlign': 'center', 'marginBottom': '10px'}),
        html.Div([dcc.Graph(id='pi-top', style={'height': '2000px'}), html.Div(id='pi-top-anno')],
                 style=SCROLL_CONTAINER_STYLE)
    ], style=s.SECTION),

    html.Div(id='breakdown-wrapper', style=s.SECTION, children=[
        dcc.Graph(id='pi-breakdown-chart', style=s.CHART_CONTAINER),
        html.Div(id='pi-breakdown-anno'),
        html.Div([
            html.Span("Click bar to filter. Click again to reset. ", style={'color': '#95a5a6', 'fontSize': '12px'}),
            html.Button("Reset Variant", id='btn-reset-variant',
                        style={'fontSize': '10px', 'padding': '2px 5px', 'cursor': 'pointer'})
        ], style={'textAlign': 'center', 'marginTop': '5px'})
    ]),

    html.H3("Trend Analysis",
            style={'textAlign': 'center', 'marginTop': '30px', 'borderTop': '1px solid #eee', 'paddingTop': '20px'}),
    html.Div([
        html.Div([html.Button("Reset Trend Selection", id='btn-reset-trend',
                              style={'fontSize': '11px', 'padding': '4px 8px', 'cursor': 'pointer',
                                     'backgroundColor': '#e74c3c', 'color': 'white', 'border': 'none',
                                     'borderRadius': '4px'})], style={'textAlign': 'right', 'marginBottom': '5px'}),
        dcc.Graph(id='pi-trend', style=s.CHART_CONTAINER), html.Div(id='pi-trend-anno')
    ], style=s.SECTION),

    html.Div([dcc.Graph(id='pi-daily', style=s.CHART_CONTAINER), html.Div(id='pi-daily-anno')], style=s.SECTION),

    html.Div(id='top-cust-wrapper', style=s.SECTION, children=[
        dcc.Graph(id='pi-top-cust', style=s.CHART_CONTAINER), html.Div(id='pi-top-cust-anno'),
        html.Div("Click a customer to open Deep Dive below",
                 style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginTop': '5px'})
    ]),

    html.Div(id='customer-section', style={'display': 'none'}, children=[
        html.H3(id='cust-section-title',
                style={'textAlign': 'center', 'marginTop': '30px', 'color': s.COLORS['primary'],
                       'borderTop': '1px solid #eee', 'paddingTop': '20px'}),
        html.Div([dcc.Graph(id='pi-cust-trend', style=s.CHART_CONTAINER), html.Div(id='pi-cust-trend-anno')],
                 style=s.SECTION),
        html.Div([dcc.Graph(id='pi-cust-daily', style=s.CHART_CONTAINER), html.Div(id='pi-cust-daily-anno')],
                 style=s.SECTION)
    ]),

    html.Div(id='sales-rep-wrapper', style=s.SECTION, children=[
        html.H3("Sales Representative Breakdown",
                style={'textAlign': 'center', 'marginTop': '30px', 'borderTop': '1px solid #eee',
                       'paddingTop': '20px'}),
        dcc.Graph(id='pi-rep-chart', style=s.CHART_CONTAINER), html.Div(id='pi-rep-chart-anno'),
        html.Div("Click a Sales Rep to see their performance history",
                 style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginTop': '5px'})
    ]),

    html.Div(id='rep-section', style={'display': 'none'}, children=[
        html.H3(id='rep-section-title',
                style={'textAlign': 'center', 'marginTop': '30px', 'color': '#e67e22', 'borderTop': '1px solid #eee',
                       'paddingTop': '20px'}),
        html.Div([dcc.Graph(id='pi-rep-trend', style=s.CHART_CONTAINER), html.Div(id='pi-rep-trend-anno')],
                 style=s.SECTION),
        html.Div([dcc.Graph(id='pi-rep-daily', style=s.CHART_CONTAINER), html.Div(id='pi-rep-daily-anno')],
                 style=s.SECTION)
    ]),

    html.Div([
        html.H3("Slow Moving Inventory (Aging Report)",
                style={'textAlign': 'center', 'color': '#e67e22', 'marginBottom': '15px'}),

        # Search Bar
        html.Div([
            html.Span("SEARCH SUB-CATEGORY:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.Dropdown(id='pi-aging-subcat-filter', placeholder="Select or type a Sub-Category...", clearable=True,
                         style={'width': '350px', 'display': 'inline-block', 'fontSize': '13px'})
        ], style={'textAlign': 'center', 'marginBottom': '15px', 'display': 'flex', 'justifyContent': 'center',
                  'alignItems': 'center'}),

        dcc.Store(id='pi-aging-data-store'),  # Browser cache for fast filtering

        dash_table.DataTable(
            id='pi-slow-moving-table',
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
            filter_action='native',
            sort_by=[{'column_id': '12 Month', 'direction': 'desc'}],
            page_size=10,
            style_table={'overflowX': 'auto', 'minWidth': '100%'},
            style_header={'backgroundColor': '#e67e22', 'color': 'white', 'fontWeight': 'bold', 'textAlign': 'center',
                          'fontSize': '12px'},
            style_cell={'textAlign': 'left', 'padding': '8px', 'fontFamily': 'Segoe UI', 'fontSize': '11px'},
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}]
        )
    ], style=s.SECTION)])


# --- CALLBACKS ---

@callback(Output('pi-search', 'options'),
          [Input('pi-hier', 'value'), Input('pi-segment-filter', 'value'), State('auth-store', 'data')])
def update_search_options(h_col, segment, auth_data):
    df = get_secure_pi_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    if h_col in df.columns: return [{'label': i, 'value': i} for i in sorted(df[h_col].dropna().unique())]
    return []


@callback(
    [Output('pi-top', 'figure'), Output('pi-top-anno', 'children'),
     Output('pi-aging-data-store', 'data'), Output('pi-debug-console', 'children')],
    [Input('pi-year', 'value'), Input('pi-view', 'value'), Input('pi-hier', 'value'),
     Input('pi-segment-filter', 'value')],
    [State('auth-store', 'data')]
)
def update_rank_and_debug(year, view, h_col, segment, auth_data):
    df = get_secure_pi_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    df_curr, df_prev, lbl = core_engine.get_time_window(df, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    debug_msg = [
        html.Strong("🔍 ON-SCREEN DIAGNOSTICS"), html.Br(),
        f"Total Allowed Rows: {len(df):,}", html.Br(),
        f"Rows Active in Timeframe ({lbl}): {len(df_curr):,}", html.Br()
    ]

    top = core_engine.get_top_performers(df_curr, h_col, vol_col, top_n=60)
    header = s.get_chart_title(f"Top 60 Items ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_top = s.create_bar_chart(top, h_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
    anno_top = s.get_storytelling_anno(top, h_col, 'Value', vol_col, METRIC_MODE)

    # --- ROBUST AGING LOGIC (Ported from V1) ---
    _, _soh_live, _, _ = get_latest_data()
    df_soh_local = _soh_live.copy() if not _soh_live.empty else pd.DataFrame()
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

    target_year = int(year)
    df_target_yr = df[df['Date'].dt.year == target_year]
    anchor_d = df_target_yr['Date'].max() if not df_target_yr.empty else pd.Timestamp(f'{target_year}-12-31')

    slow_data = []
    if not df.empty and 'Item Code' in df.columns and 'Name' in df.columns:
        hier_df = df[['Items 2', 'Items', 'Name', 'Item Code', 'Date']].copy()
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

    return fig_top, anno_top, slow_data, debug_msg


@callback(
    [Output('pi-item', 'data'), Output('pi-drill-item', 'data'),
     Output('pi-customer', 'data'), Output('pi-cust-date', 'data'),
     Output('pi-trend-date', 'data'), Output('pi-rep', 'data'), Output('pi-rep-date', 'data')],
    [Input('pi-top', 'clickData'), Input('pi-search', 'value'),
     Input('pi-breakdown-chart', 'clickData'), Input('btn-reset-variant', 'n_clicks'),
     Input('pi-top-cust', 'clickData'), Input('pi-cust-trend', 'clickData'),
     Input('pi-trend', 'clickData'), Input('btn-reset-trend', 'n_clicks'),
     Input('pi-rep-chart', 'clickData'), Input('pi-rep-trend', 'clickData')],
    [State('pi-item', 'data'), State('pi-drill-item', 'data')]
)
def manage_stores(top_clk, search_val, drill_clk, reset_btn, cust_clk, cust_date_clk, trend_clk, reset_trend,
                  rep_clk, rep_date_clk, current_item, current_drill):
    trig = ctx.triggered_id
    item, drill_item, cust, c_date, trend_date, rep, rep_date = (no_update,) * 7

    if trig == 'pi-top':
        item = top_clk['points'][0]['y']; drill_item = cust = c_date = trend_date = rep = rep_date = None
    elif trig == 'pi-search':
        item = search_val; drill_item = cust = c_date = trend_date = rep = rep_date = None
    elif trig == 'pi-breakdown-chart':
        drill_item = None if drill_clk['points'][0]['y'] == current_drill else drill_clk['points'][0]['y']
    elif trig == 'btn-reset-variant':
        drill_item = None
    elif trig == 'pi-top-cust':
        cust = cust_clk['points'][0]['y']; c_date = None
    elif trig == 'pi-cust-trend':
        c_date = cust_date_clk['points'][0]['x']
    elif trig == 'pi-trend':
        trend_date = trend_clk['points'][0]['x']
    elif trig == 'btn-reset-trend':
        trend_date = None
    elif trig == 'pi-rep-chart':
        rep = rep_clk['points'][0]['y']; rep_date = None
    elif trig == 'pi-rep-trend':
        rep_date = rep_date_clk['points'][0]['x']

    return item, drill_item, cust, c_date, trend_date, rep, rep_date


# --- THE CONNECTED RANKING CHARTS ---

@callback(
    [Output('pi-breakdown-chart', 'figure'), Output('pi-breakdown-anno', 'children'),
     Output('breakdown-wrapper', 'style')],
    [Input('pi-item', 'data'), Input('pi-year', 'value'), Input('pi-view', 'value'), Input('pi-hier', 'value'),
     Input('pi-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_variant_breakdown(item, year, view, h_col, trend_date, auth_data):
    if not item: return go.Figure().update_layout(title="Select an item above"), "", {'display': 'none'}

    df = get_secure_pi_df(auth_data)
    df_base = df[df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()]

    # Engine calculates precise window based on trend click
    df_curr, df_prev, lbl = core_engine.get_time_window(df_base, year, view, trend_date)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)

    if drill_col and drill_col in df.columns:
        drill_df = core_engine.get_top_performers(df_curr, drill_col, vol_col, top_n=10)
        header = s.get_chart_title(f"Variant Breakdown: {item} ({lbl})", df_curr, df_prev, METRIC_MODE)
        fig_drill = s.create_bar_chart(drill_df, drill_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
        return fig_drill, s.get_storytelling_anno(drill_df, drill_col, 'Value', vol_col, METRIC_MODE), s.SECTION

    return go.Figure().update_layout(title="No deeper hierarchy available"), "", {'display': 'none'}


@callback(
    [Output('pi-top-cust', 'figure'), Output('pi-top-cust-anno', 'children'), Output('top-cust-wrapper', 'style')],
    [Input('pi-item', 'data'), Input('pi-drill-item', 'data'), Input('pi-year', 'value'), Input('pi-view', 'value'),
     Input('pi-hier', 'value'), Input('pi-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_top_customers(item, drill_item, year, view, h_col, trend_date, auth_data):
    if not item: return go.Figure().update_layout(title="Select Item to see Customers"), "", {'display': 'none'}

    df = get_secure_pi_df(auth_data)
    df_base = df[df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()]

    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)
    if drill_item and drill_col and drill_col in df_base.columns:
        df_base = df_base[df_base[drill_col].astype(str).str.strip().str.upper() == str(drill_item).strip().upper()]

    # Engine calculates precise window based on trend click
    df_curr, df_prev, lbl = core_engine.get_time_window(df_base, year, view, trend_date)

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Account', 'Client'] if c in df.columns), None)
    if not cust_col: return go.Figure().update_layout(title="No Customer Column Found"), "", s.SECTION

    cust_df = core_engine.get_top_performers(df_curr, cust_col, vol_col, top_n=20)
    title_suffix = f" ({drill_item})" if drill_item else ""
    header = s.get_chart_title(f"Top 20 Customers: {item}{title_suffix} ({lbl})", df_curr, df_prev, METRIC_MODE)

    fig_cust = s.create_bar_chart(cust_df, cust_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
    fig_cust.update_traces(marker_color='#2ecc71')
    return fig_cust, s.get_storytelling_anno(cust_df, cust_col, 'Value', vol_col, METRIC_MODE), s.SECTION


@callback(
    [Output('pi-rep-chart', 'figure'), Output('pi-rep-chart-anno', 'children'), Output('sales-rep-wrapper', 'style')],
    [Input('pi-item', 'data'), Input('pi-drill-item', 'data'), Input('pi-year', 'value'), Input('pi-view', 'value'),
     Input('pi-hier', 'value'), Input('pi-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_sales_reps(item, drill_item, year, view, h_col, trend_date, auth_data):
    if not item: return go.Figure().update_layout(title="Select Item to see Reps"), "", {'display': 'none'}

    df = get_secure_pi_df(auth_data)
    df_base = df[df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()]

    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)
    if drill_item and drill_col and drill_col in df_base.columns:
        df_base = df_base[df_base[drill_col].astype(str).str.strip().str.upper() == str(drill_item).strip().upper()]

    # Engine calculates precise window based on trend click
    df_curr, df_prev, lbl = core_engine.get_time_window(df_base, year, view, trend_date)

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Salesperson', 'Rep'] if c in df.columns), None)
    if not rep_col: return go.Figure().update_layout(title="No Sales Rep Column Found"), "", s.SECTION

    rep_df = core_engine.get_top_performers(df_curr, rep_col, vol_col, top_n=20)
    title_suffix = f" ({drill_item})" if drill_item else ""
    header = s.get_chart_title(f"Sales Reps: {item}{title_suffix} ({lbl})", df_curr, df_prev, METRIC_MODE)

    fig_rep = s.create_bar_chart(rep_df, rep_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
    fig_rep.update_traces(marker_color='#e67e22')
    return fig_rep, s.get_storytelling_anno(rep_df, rep_col, 'Value', vol_col, METRIC_MODE), s.SECTION


# --- THE TREND & DAILY CHARTS ---

@callback(
    [Output('pi-trend', 'figure'), Output('pi-trend-anno', 'children')],
    [Input('pi-item', 'data'), Input('pi-drill-item', 'data'), Input('pi-year', 'value'), Input('pi-view', 'value'),
     Input('pi-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_sales_trend(item, drill_item, year, view, h_col, auth_data):
    if not item: return go.Figure(), ""

    df = get_secure_pi_df(auth_data)
    df_item = df[df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()]

    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)
    title_suffix = ""
    if drill_item and drill_col and drill_col in df_item.columns:
        df_item = df_item[df_item[drill_col].astype(str).str.strip().str.upper() == str(drill_item).strip().upper()]
        title_suffix = f" ({drill_item})"

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    # The Trend Chart ALWAYS requests 'Year' from the engine to show the YTD context
    df_curr, df_prev, lbl = core_engine.get_time_window(df_item, year, 'Year')
    trend, xfmt, dtick = core_engine.get_trend_data(df_curr, view, vol_col)

    header = s.get_chart_title(f"Sales Trend: {item}{title_suffix} (YTD Context)", df_curr, df_prev, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    if not trend.empty: fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    return fig_trend, s.get_storytelling_anno(trend, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('pi-daily', 'figure'), Output('pi-daily-anno', 'children')],
    [Input('pi-item', 'data'), Input('pi-drill-item', 'data'), Input('pi-year', 'value'), Input('pi-view', 'value'),
     Input('pi-hier', 'value'), Input('pi-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_daily_breakdown(item, drill_item, year, view, h_col, trend_date, auth_data):
    if not item: return go.Figure(), ""

    df = get_secure_pi_df(auth_data)
    df_item = df[df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper()]

    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)
    title_suffix = ""
    if drill_item and drill_col and drill_col in df_item.columns:
        df_item = df_item[df_item[drill_col].astype(str).str.strip().str.upper() == str(drill_item).strip().upper()]
        title_suffix = f" ({drill_item})"

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    # Engine calculates precise window based on trend click
    df_curr, df_prev, lbl = core_engine.get_time_window(df_item, year, view, trend_date)
    daily = core_engine.get_drilldown_data(df_curr, vol_col)

    header = s.get_chart_title(f"Daily Breakdown: {lbl}{title_suffix}", df_curr, df_prev, METRIC_MODE)
    fig_daily = s.create_bar_chart(daily, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    if not daily.empty: fig_daily.update_xaxes(tickformat="%d %b", dtick=86400000.0)

    return fig_daily, s.get_storytelling_anno(daily, 'Date', 'Value', vol_col, METRIC_MODE)


# --- THE DEEP DIVES ---

@callback(
    [Output('customer-section', 'style'), Output('cust-section-title', 'children'),
     Output('pi-cust-trend', 'figure'), Output('pi-cust-trend-anno', 'children'),
     Output('pi-cust-daily', 'figure'), Output('pi-cust-daily-anno', 'children')],
    [Input('pi-customer', 'data'), Input('pi-cust-date', 'data'), State('pi-item', 'data'), Input('pi-year', 'value'),
     Input('pi-view', 'value'), State('pi-drill-item', 'data'), State('pi-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_customer_deep_dive(cust, date_click, item, year, view, drill_item, h_col, auth_data):
    empty_res = {'display': 'none'}, "", go.Figure(), "", go.Figure(), ""
    if not cust or not item: return empty_res

    df = get_secure_pi_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Account', 'Client'] if c in df.columns), None)
    if not cust_col: return empty_res

    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)
    base_mask = (df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()) & (
                df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper())
    if drill_item and drill_col and drill_col in df.columns:
        base_mask &= (df[drill_col].astype(str).str.strip().str.upper() == str(drill_item).strip().upper())

    df_base = df[base_mask]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    # Deep Dive Trend always asks for 'Year' to show context
    df_curr_trend, df_prev_trend, lbl_trend = core_engine.get_time_window(df_base, year, 'Year')
    trend, xfmt, dtick = core_engine.get_trend_data(df_curr_trend, view, vol_col)
    trend_header = s.get_chart_title(f"Trend: {cust} (YTD)", df_curr_trend, df_prev_trend, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend, 'Date', 'Value', trend_header, vol_col, METRIC_MODE, use_z_colors=True)
    if not trend.empty: fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    # Deep Dive Daily obeys the date click
    df_curr_daily, df_prev_daily, lbl_daily = core_engine.get_time_window(df_base, year, view, date_click)
    daily = core_engine.get_drilldown_data(df_curr_daily, vol_col)
    daily_header = s.get_chart_title(f"Daily Activity: {lbl_daily}", df_curr_daily, df_prev_daily, METRIC_MODE)
    fig_daily = s.create_bar_chart(daily, 'Date', 'Value', daily_header, vol_col, METRIC_MODE, use_z_colors=True)
    if not daily.empty: fig_daily.update_xaxes(tickformat="%d %b", dtick=86400000.0)

    return {'display': 'block'}, f"Deep Dive: {cust} buying {item}", fig_trend, s.get_storytelling_anno(trend, 'Date',
                                                                                                        'Value',
                                                                                                        vol_col,
                                                                                                        METRIC_MODE), fig_daily, s.get_storytelling_anno(
        daily, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('rep-section', 'style'), Output('rep-section-title', 'children'),
     Output('pi-rep-trend', 'figure'), Output('pi-rep-trend-anno', 'children'),
     Output('pi-rep-daily', 'figure'), Output('pi-rep-daily-anno', 'children')],
    [Input('pi-rep', 'data'), Input('pi-rep-date', 'data'), State('pi-item', 'data'), Input('pi-year', 'value'),
     Input('pi-view', 'value'), State('pi-drill-item', 'data'), State('pi-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_rep_deep_dive(rep, date_click, item, year, view, drill_item, h_col, auth_data):
    empty_res = {'display': 'none'}, "", go.Figure(), "", go.Figure(), ""
    if not rep or not item: return empty_res

    df = get_secure_pi_df(auth_data)
    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Salesperson', 'Rep'] if c in df.columns), None)
    if not rep_col: return empty_res

    drill_col = 'Items' if h_col == 'Items 2' else ('Name' if h_col == 'Items' else None)
    base_mask = (df[rep_col].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
                df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper())
    if drill_item and drill_col and drill_col in df.columns:
        base_mask &= (df[drill_col].astype(str).str.strip().str.upper() == str(drill_item).strip().upper())

    df_base = df[base_mask]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_curr_trend, df_prev_trend, lbl_trend = core_engine.get_time_window(df_base, year, 'Year')
    trend, xfmt, dtick = core_engine.get_trend_data(df_curr_trend, view, vol_col)
    trend_header = s.get_chart_title(f"Sales by {rep} (YTD)", df_curr_trend, df_prev_trend, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend, 'Date', 'Value', trend_header, vol_col, METRIC_MODE)
    fig_trend.update_traces(marker_color='#e67e22')
    if not trend.empty: fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    df_curr_daily, df_prev_daily, lbl_daily = core_engine.get_time_window(df_base, year, view, date_click)
    daily = core_engine.get_drilldown_data(df_curr_daily, vol_col)
    daily_header = s.get_chart_title(f"Daily Sales: {lbl_daily}", df_curr_daily, df_prev_daily, METRIC_MODE)
    fig_daily = s.create_bar_chart(daily, 'Date', 'Value', daily_header, vol_col, METRIC_MODE)
    fig_daily.update_traces(marker_color='#e67e22')
    if not daily.empty: fig_daily.update_xaxes(tickformat="%d %b", dtick=86400000.0)

    return {'display': 'block'}, f"Deep Dive: {rep}", fig_trend, s.get_storytelling_anno(trend, 'Date', 'Value',
                                                                                         vol_col,
                                                                                         METRIC_MODE), fig_daily, s.get_storytelling_anno(
        daily, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('dormant-drill-section', 'style'), Output('dormant-drill-title', 'children'),
     Output('pi-dead-cust', 'figure'), Output('pi-dead-cust-anno', 'children'),
     Output('pi-dead-var', 'figure'), Output('pi-dead-var-anno', 'children')],
    [Input('pi-dead-item', 'data'), Input('pi-year', 'value')],
    [State('auth-store', 'data')]
)
def update_dormant_drill(dead_item, year, auth_data):
    empty_res = {'display': 'none'}, "", go.Figure(), "", go.Figure(), ""
    if not dead_item: return empty_res

    df = get_secure_pi_df(auth_data)
    df_dead = df[df['Items'].astype(str).str.strip().str.upper() == str(dead_item).strip().upper()]

    df_curr = df_dead[df_dead['Date'].dt.year == int(year)]
    df_prev = df_dead[df_dead['Date'].dt.year == int(year) - 1]
    df_hist = df_dead.copy()

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Account'] if c in df.columns), None)

    fig_cust, anno_cust = go.Figure().update_layout(title="No Historical Sales Found"), ""
    if cust_col and not df_hist.empty:
        cust_hist = core_engine.get_top_performers(df_hist, cust_col, vol_col, top_n=10)
        header = s.get_chart_title(f"Previous Buyers: {dead_item}", df_curr, df_prev, METRIC_MODE)
        fig_cust = s.create_bar_chart(cust_hist, cust_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
        fig_cust.update_traces(marker_color='#95a5a6')
        anno_cust = s.get_storytelling_anno(cust_hist, cust_col, 'Value', vol_col, METRIC_MODE)

    fig_var, anno_var = go.Figure().update_layout(title="No Variants Found"), ""
    if 'Name' in df_hist.columns and not df_hist.empty:
        var_df = core_engine.get_top_performers(df_hist, 'Name', vol_col, top_n=10)
        header_var = s.get_chart_title(f"Variant Split: {dead_item}", df_curr, df_prev, METRIC_MODE)
        fig_var = s.create_bar_chart(var_df, 'Name', 'Value', header_var, vol_col, METRIC_MODE, is_horizontal=True)
        anno_var = s.get_storytelling_anno(var_df, 'Name', 'Value', vol_col, METRIC_MODE)

    return {'display': 'block'}, f"Analysis: {dead_item}", fig_cust, anno_cust, fig_var, anno_var


@callback(
    Output("pi-dl", "data"), Input("pi-snap", "n_clicks"),
    [State('pi-top', 'figure'), State('pi-breakdown-chart', 'figure'), State('pi-trend', 'figure'),
     State('pi-daily', 'figure'), State('pi-top-cust', 'figure'), State('pi-cust-trend', 'figure'),
     State('pi-cust-daily', 'figure'), State('pi-rep-chart', 'figure'), State('pi-rep-trend', 'figure'),
     State('pi-rep-daily', 'figure')],
    prevent_initial_call=True
)
def download_snapshot(n, *figures):
    html_content = f"""
    <html>
    <head>
        <title>Product Intelligence Snapshot</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; padding: 20px; background-color: #f4f4f4; }}
            .chart-container {{ background: white; margin-bottom: 20px; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ text-align: center; color: #2c3e50; }}
            .timestamp {{ text-align: center; color: #7f8c8d; font-size: 0.9em; margin-bottom: 30px; }}
        </style>
    </head>
    <body>
        <h1>Product Intelligence Report</h1>
        <p class="timestamp">Generated on {pd.Timestamp.now().strftime('%d-%b-%Y %H:%M')}</p>
    """
    for fig in figures:
        if fig and isinstance(fig, dict) and 'data' in fig and len(fig['data']) > 0:
            html_content += f'<div class="chart-container">{pio.to_html(go.Figure(fig), full_html=False, include_plotlyjs="cdn")}</div>'

    html_content += "</body></html>"
    return dict(content=html_content, filename=f"pi_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")
@callback(
    Output('pi-segment-container', 'style'),
    [Input('auth-store', 'data')]
)
def setup_pi_permissions(auth_data):
    ui_rules = auth_data.get('ui_permissions', {}) if auth_data else {}
    if ui_rules.get('show_segment_filters', True):
        return {'display': 'inline-block'}
    return {'display': 'none'}

@callback(
    [Output('pi-slow-moving-table', 'data'), Output('pi-aging-subcat-filter', 'options')],
    [Input('pi-aging-subcat-filter', 'value'), Input('pi-aging-data-store', 'data')]
)
def filter_pi_aging_report(selected_subcat, store_data):
    if not store_data: return [], []
    df_aging = pd.DataFrame(store_data)
    available_subcats = sorted(df_aging['Items'].dropna().unique())
    options = [{'label': i, 'value': i} for i in available_subcats]
    if selected_subcat:
        df_aging = df_aging[df_aging['Items'] == selected_subcat]
    return df_aging.to_dict('records'), options