import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine

import dash
from dash import html, dcc, Input, Output, State, ctx, callback, no_update
import plotly.graph_objects as go
import pandas as pd
import plotly.io as pio
from data_loader import df_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security

# --- STYLING HELPERS ---
SCROLL_CONTAINER_STYLE = {
    'height': '83vh', 'overflowY': 'scroll', 'overflowX': 'hidden',
    'backgroundColor': s.COLORS.get('card', 'white'), 'border': f"1px solid {s.COLORS.get('border', '#e0e0e0')}",
    'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)', 'padding': '10px'
}

DEBUG_STYLE = {
    'width': '100%', 'backgroundColor': '#2c3e50', 'color': '#2ecc71', 'padding': '15px',
    'fontFamily': 'monospace', 'fontSize': '13px', 'maxHeight': '200px',
    'overflowY': 'scroll', 'marginBottom': '20px', 'borderRadius': '5px',
    'border': '1px solid #34495e', 'lineHeight': '1.5'
}


def get_secure_ci_df(auth_data):
    _df, _, _, _ = get_latest_data()
    return enforce_row_level_security(_df, auth_data)


# --- LAYOUT ---
layout = html.Div([
    dcc.Store(id='ci-cust'), dcc.Store(id='ci-trend-date'), dcc.Store(id='ci-prod'),
    dcc.Store(id='ci-prod-date'), dcc.Store(id='ci-rep-selected'), dcc.Store(id='ci-rep-date'),
    dcc.Store(id='ci-loss-item'), dcc.Download(id="ci-dl"),

    html.Div([
        html.H1("Customer Intelligence", style=s.HEADER_TITLE),
        html.Div(id='ci-debug-console', style=DEBUG_STYLE, children="Initializing System..."),

        html.Div([
            html.Div([
                html.Span("SEARCH:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.Dropdown(id='ci-search', placeholder="Search Customer...",
                             style={'width': '250px', 'fontSize': '13px'}, optionHeight=30),
            ], style={'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("LEVEL:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='ci-hier',
                           options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),

            # Locate this section in the layout and replace with:
            html.Div(id='ci-segment-container', children=[
                html.Span("SEGMENT:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.RadioItems(id='ci-segment',
                               options=[{'label': 'All', 'value': 'All'}, {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', inline=True,
                               style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),
            ], style={'display': 'inline-block'}),

            html.Span("YEAR:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.Dropdown(id='ci-year', options=[{'label': str(y), 'value': y} for y in years_global],
                         value=years_global[0] if years_global else None, clearable=False,
                         style={'width': '80px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("VIEW:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='ci-view', options=['Week', 'Month', 'Quarter', 'Year'], value='Year', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Button("📸 Snapshot", id="ci-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'justifyContent': 'center',
                  'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '8px',
                  'border': f'1px solid {s.COLORS["border"]}'})
    ], style=s.SECTION),

    html.Div([
        html.H3("Top Customers Ranking", style={'textAlign': 'center', 'marginBottom': '10px'}),
        html.Div([dcc.Graph(id='ci-top', style={'height': '2000px'}), html.Div(id='ci-top-anno')],
                 style=SCROLL_CONTAINER_STYLE)
    ], style=s.SECTION),

    html.H3("Overall Customer Analysis",
            style={'textAlign': 'center', 'marginTop': '30px', 'borderTop': '1px solid #eee', 'paddingTop': '20px'}),
    html.Div([
        html.Div([html.Button("Reset Trend Selection", id='btn-reset-ci-trend',
                              style={'fontSize': '11px', 'padding': '4px 8px', 'cursor': 'pointer',
                                     'backgroundColor': '#e74c3c', 'color': 'white', 'border': 'none',
                                     'borderRadius': '4px'})], style={'textAlign': 'right', 'marginBottom': '5px'}),
        dcc.Graph(id='ci-trend', style=s.CHART_CONTAINER), html.Div(id='ci-trend-anno')
    ], style=s.SECTION),
    html.Div([dcc.Graph(id='ci-daily', style=s.CHART_CONTAINER), html.Div(id='ci-daily-anno')], style=s.SECTION),

    html.Div([
        html.H3("Product Mix Analysis",
                style={'textAlign': 'center', 'marginTop': '30px', 'borderTop': '1px solid #eee',
                       'paddingTop': '20px'}),
        html.Div([dcc.Graph(id='ci-mix', style=s.CHART_CONTAINER), html.Div(id='ci-mix-anno')], style=s.SECTION),
        html.Div("Click a product above to see its sales history for this customer",
                 style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px'})
    ], style=s.SECTION),

    html.Div(id='product-deep-dive', style={'display': 'none'}, children=[
        html.H4(id='ci-prod-title', style={'textAlign': 'center', 'color': '#3498db'}),
        html.Div([dcc.Graph(id='ci-prod-trend', style=s.CHART_CONTAINER), html.Div(id='ci-prod-trend-anno')],
                 style=s.SECTION),
        html.Div([dcc.Graph(id='ci-prod-daily', style=s.CHART_CONTAINER), html.Div(id='ci-prod-daily-anno')],
                 style=s.SECTION)
    ]),

    html.Div([
        html.H3("Servicing Sales Reps",
                style={'textAlign': 'center', 'marginTop': '30px', 'borderTop': '1px solid #eee',
                       'paddingTop': '20px'}),
        html.Div([dcc.Graph(id='ci-rep', style=s.CHART_CONTAINER), html.Div(id='ci-rep-anno')], style=s.SECTION),
        html.Div("Click a Rep above to see their service history for this customer",
                 style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px'})
    ], style=s.SECTION),

    html.Div(id='rep-deep-dive', style={'display': 'none'}, children=[
        html.H4(id='ci-rep-title', style={'textAlign': 'center', 'color': '#e67e22'}),
        html.Div([dcc.Graph(id='ci-rep-trend', style=s.CHART_CONTAINER), html.Div(id='ci-rep-trend-anno')],
                 style=s.SECTION),
        html.Div([dcc.Graph(id='ci-rep-daily', style=s.CHART_CONTAINER), html.Div(id='ci-rep-daily-anno')],
                 style=s.SECTION)
    ]),

    html.Div([
        html.H3("Dropped Items (Churn Risk)",
                style={'textAlign': 'center', 'color': s.COLORS.get('danger', '#e74c3c'), 'marginTop': '30px',
                       'borderTop': '1px solid #eee', 'paddingTop': '20px'}),
        html.P("Items bought previously but NOT bought in the current selected period.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px'}),
        html.Div([dcc.Graph(id='ci-dropped', style=s.CHART_CONTAINER), html.Div(id='ci-dropped-anno')],
                 style=s.SECTION),
        html.Div("Click a dropped item to analyze other buyers and variants",
                 style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px'})
    ], style=s.SECTION),

    html.Div(id='loss-deep-dive', style={'display': 'none'}, children=[
        html.H4(id='ci-loss-title', style={'textAlign': 'center', 'color': '#c0392b'}),
        html.Div([
            html.Div([dcc.Graph(id='ci-loss-other', style=s.CHART_CONTAINER), html.Div(id='ci-loss-other-anno')],
                     style=s.SECTION),
            html.Div([dcc.Graph(id='ci-loss-var', style=s.CHART_CONTAINER), html.Div(id='ci-loss-var-anno')],
                     style=s.SECTION)
        ])
    ]),
], style=s.MAIN_CONTAINER)


# --- CALLBACKS ---

@callback(Output('ci-search', 'options'), [Input('ci-segment', 'value'), State('auth-store', 'data')])
def update_search_options(segment, auth_data):
    df = get_secure_ci_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    if cust_col: return [{'label': i, 'value': i} for i in sorted(df[cust_col].dropna().unique())]
    return []


@callback(
    [Output('ci-top', 'figure'), Output('ci-top-anno', 'children'), Output('ci-debug-console', 'children')],
    [Input('ci-year', 'value'), Input('ci-view', 'value'), Input('ci-segment', 'value')],
    [State('auth-store', 'data')]
)
def update_ranking(year, view, segment, auth_data):
    df = get_secure_ci_df(auth_data)
    target_year = int(year)

    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    # Engine cleanly handles the window slicing
    df_curr, df_prev, lbl = core_engine.get_time_window(df, target_year, view)

    debug_msg = [
        html.Strong("🔍 ON-SCREEN DIAGNOSTICS (Powered by Core Engine)"), html.Br(),
        f"Total Allowed Rows for User: {len(df):,}", html.Br(),
        f"Rows Active in Selected Timeframe ({lbl}): {len(df_curr):,}", html.Br(),
        f"Selected Year: {target_year} | Selected View: {view} | Segment: {segment}"
    ]

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    top = core_engine.get_top_performers(df_curr, cust_col, vol_col, top_n=60)
    header = s.get_chart_title(f"Top Customers ({lbl})", df_curr, df_prev, METRIC_MODE)

    fig = s.create_bar_chart(top, cust_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
    anno = s.get_storytelling_anno(top, cust_col, 'Value', vol_col, METRIC_MODE)
    return fig, anno, debug_msg


@callback(
    [Output('ci-cust', 'data'), Output('ci-trend-date', 'data'),
     Output('ci-prod', 'data'), Output('ci-prod-date', 'data'),
     Output('ci-rep-selected', 'data'), Output('ci-rep-date', 'data'), Output('ci-loss-item', 'data')],
    [Input('ci-top', 'clickData'), Input('ci-search', 'value'), Input('ci-trend', 'clickData'),
     Input('btn-reset-ci-trend', 'n_clicks'),
     Input('ci-mix', 'clickData'), Input('ci-prod-trend', 'clickData'),
     Input('ci-rep', 'clickData'), Input('ci-rep-trend', 'clickData'), Input('ci-dropped', 'clickData')]
)
def manage_stores(clk_top, search_val, clk_trend, reset_trend, clk_mix, clk_prod_t, clk_rep, clk_rep_t, clk_loss):
    trig = ctx.triggered_id
    cust, trend_date, prod, prod_date, rep, rep_date, loss_item = (no_update,) * 7

    if trig == 'ci-top':
        cust = clk_top['points'][0]['y']; trend_date = prod = prod_date = rep = rep_date = loss_item = None
    elif trig == 'ci-search':
        cust = search_val; trend_date = prod = prod_date = rep = rep_date = loss_item = None
    elif trig == 'ci-trend':
        trend_date = clk_trend['points'][0]['x']
    elif trig == 'btn-reset-ci-trend':
        trend_date = None
    elif trig == 'ci-mix':
        prod = clk_mix['points'][0]['y']; prod_date = None
    elif trig == 'ci-prod-trend':
        prod_date = clk_prod_t['points'][0]['x']
    elif trig == 'ci-rep':
        rep = clk_rep['points'][0]['y']; rep_date = None
    elif trig == 'ci-rep-trend':
        rep_date = clk_rep_t['points'][0]['x']
    elif trig == 'ci-dropped':
        loss_item = clk_loss['points'][0]['y']

    return cust, trend_date, prod, prod_date, rep, rep_date, loss_item


@callback(
    [Output('ci-trend', 'figure'), Output('ci-trend-anno', 'children')],
    [Input('ci-cust', 'data'), Input('ci-year', 'value'), Input('ci-view', 'value')],
    [State('auth-store', 'data')]
)
def update_trend(cust, year, view, auth_data):
    if not cust: return go.Figure().update_layout(title="Select a Customer"), ""

    df = get_secure_ci_df(auth_data)
    target_year = int(year)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_cust = df[df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()]

    # Trend ALWAYS shows YTD context
    df_curr_trend, df_prev_trend, _ = core_engine.get_time_window(df_cust, target_year, 'Year')
    trend_raw, xfmt, dtick = core_engine.get_trend_data(df_curr_trend, view, vol_col)

    header = s.get_chart_title(f"Sales Trend: {cust} (YTD)", df_curr_trend, df_prev_trend, METRIC_MODE)
    fig = s.create_bar_chart(trend_raw, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    if not trend_raw.empty: fig.update_xaxes(tickformat=xfmt, dtick=dtick)

    return fig, s.get_storytelling_anno(trend_raw, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('ci-daily', 'figure'), Output('ci-daily-anno', 'children')],
    [Input('ci-cust', 'data'), Input('ci-year', 'value'), Input('ci-view', 'value'), Input('ci-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_daily(cust, year, view, trend_date, auth_data):
    if not cust: return go.Figure(), ""

    df = get_secure_ci_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    df_cust = df[df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_curr_daily, df_prev_daily, lbl = core_engine.get_time_window(df_cust, year, view, trend_date)
    daily = core_engine.get_drilldown_data(df_curr_daily, vol_col)

    header = s.get_chart_title(f"Daily Activity: {lbl}", df_curr_daily, df_prev_daily, METRIC_MODE)
    fig = s.create_bar_chart(daily, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    if not daily.empty: fig.update_xaxes(tickformat="%d %b", dtick=86400000.0)

    return fig, s.get_storytelling_anno(daily, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('ci-mix', 'figure'), Output('ci-mix-anno', 'children'),
     Output('ci-rep', 'figure'), Output('ci-rep-anno', 'children')],
    [Input('ci-cust', 'data'), Input('ci-year', 'value'), Input('ci-view', 'value'), Input('ci-hier', 'value'),
     Input('ci-trend-date', 'data')],
    [State('auth-store', 'data')]
)
def update_context_charts(cust, year, view, h_col, trend_date, auth_data):
    empty_res = go.Figure(), "", go.Figure(), ""
    if not cust: return empty_res

    df = get_secure_ci_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_cust = df[df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()]

    # Engine perfectly slices the window based on the trend chart click
    df_curr, df_prev, lbl = core_engine.get_time_window(df_cust, year, view, trend_date)

    # Product Mix
    mix = core_engine.get_top_performers(df_curr, h_col, vol_col, top_n=10)
    header_mix = s.get_chart_title(f"Top {h_col}: {cust} ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_mix = s.create_bar_chart(mix, h_col, 'Value', header_mix, vol_col, METRIC_MODE, is_horizontal=True)

    # Reps
    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Rep'] if c in df.columns), None)
    if rep_col:
        reps = core_engine.get_top_performers(df_curr, rep_col, vol_col, top_n=10)
        header_rep = s.get_chart_title(f"Servicing Reps ({lbl})", df_curr, df_prev, METRIC_MODE)
        fig_rep = s.create_bar_chart(reps, rep_col, 'Value', header_rep, vol_col, METRIC_MODE, is_horizontal=True)
        fig_rep.update_traces(marker_color='#e67e22')
        anno_rep = s.get_storytelling_anno(reps, rep_col, 'Value', vol_col, METRIC_MODE)
    else:
        fig_rep, anno_rep = go.Figure(layout={'title': "No Rep Data"}), ""

    return fig_mix, s.get_storytelling_anno(mix, h_col, 'Value', vol_col, METRIC_MODE), fig_rep, anno_rep


@callback(
    [Output('product-deep-dive', 'style'), Output('ci-prod-title', 'children'),
     Output('ci-prod-trend', 'figure'), Output('ci-prod-trend-anno', 'children'),
     Output('ci-prod-daily', 'figure'), Output('ci-prod-daily-anno', 'children')],
    [Input('ci-prod', 'data'), Input('ci-prod-date', 'data'),
     State('ci-cust', 'data'), Input('ci-year', 'value'), Input('ci-view', 'value'), State('ci-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_prod_deep_dive(prod, date_click, cust, year, view, h_col, auth_data):
    empty_res = {'display': 'none'}, "", go.Figure(), "", go.Figure(), ""
    if not prod: return empty_res

    df = get_secure_ci_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)

    df_base = df[(df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()) & (
                df[h_col].astype(str).str.strip().str.upper() == str(prod).strip().upper())]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_curr_trend, df_prev_trend, _ = core_engine.get_time_window(df_base, year, 'Year')
    trend, xfmt, dtick = core_engine.get_trend_data(df_curr_trend, view, vol_col)
    header = s.get_chart_title(f"Trend: {prod} (YTD)", df_curr_trend, df_prev_trend, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    if not trend.empty: fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    df_curr_daily, df_prev_daily, lbl = core_engine.get_time_window(df_base, year, view, date_click)
    daily = core_engine.get_drilldown_data(df_curr_daily, vol_col)
    header_d = s.get_chart_title(f"Daily: {lbl}", df_curr_daily, df_prev_daily, METRIC_MODE)
    fig_daily = s.create_bar_chart(daily, 'Date', 'Value', header_d, vol_col, METRIC_MODE, use_z_colors=True)
    if not daily.empty: fig_daily.update_xaxes(tickformat="%d %b", dtick=86400000.0)

    return {'display': 'block'}, f"Deep Dive: {prod}", fig_trend, s.get_storytelling_anno(trend, 'Date', 'Value',
                                                                                          vol_col,
                                                                                          METRIC_MODE), fig_daily, s.get_storytelling_anno(
        daily, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('rep-deep-dive', 'style'), Output('ci-rep-title', 'children'),
     Output('ci-rep-trend', 'figure'), Output('ci-rep-trend-anno', 'children'),
     Output('ci-rep-daily', 'figure'), Output('ci-rep-daily-anno', 'children')],
    [Input('ci-rep-selected', 'data'), Input('ci-rep-date', 'data'),
     State('ci-cust', 'data'), Input('ci-year', 'value'), Input('ci-view', 'value')],
    [State('auth-store', 'data')]
)
def update_rep_deep_dive(rep, date_click, cust, year, view, auth_data):
    empty_res = {'display': 'none'}, "", go.Figure(), "", go.Figure(), ""
    if not rep: return empty_res

    df = get_secure_ci_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Rep'] if c in df.columns), None)

    df_base = df[(df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()) & (
                df[rep_col].astype(str).str.strip().str.upper() == str(rep).strip().upper())]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_curr_trend, df_prev_trend, _ = core_engine.get_time_window(df_base, year, 'Year')
    trend, xfmt, dtick = core_engine.get_trend_data(df_curr_trend, view, vol_col)
    header = s.get_chart_title(f"Sales Trend by {rep} (YTD)", df_curr_trend, df_prev_trend, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=False)
    fig_trend.update_traces(marker_color='#e67e22')
    if not trend.empty: fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    df_curr_daily, df_prev_daily, lbl = core_engine.get_time_window(df_base, year, view, date_click)
    daily = core_engine.get_drilldown_data(df_curr_daily, vol_col)
    header_d = s.get_chart_title(f"Daily: {lbl}", df_curr_daily, df_prev_daily, METRIC_MODE)
    fig_daily = s.create_bar_chart(daily, 'Date', 'Value', header_d, vol_col, METRIC_MODE, use_z_colors=False)
    fig_daily.update_traces(marker_color='#e67e22')
    if not daily.empty: fig_daily.update_xaxes(tickformat="%d %b", dtick=86400000.0)

    return {'display': 'block'}, f"Rep Performance: {rep}", fig_trend, s.get_storytelling_anno(trend, 'Date', 'Value',
                                                                                               vol_col,
                                                                                               METRIC_MODE), fig_daily, s.get_storytelling_anno(
        daily, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('ci-dropped', 'figure'), Output('ci-dropped-anno', 'children')],
    [Input('ci-cust', 'data'), Input('ci-year', 'value'), Input('ci-view', 'value'), Input('ci-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_dropped(cust, year, view, h_col, auth_data):
    if not cust: return go.Figure(), ""

    df = get_secure_ci_df(auth_data)
    target_year = int(year)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    df_cust = df[df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper()]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_curr_yr = df_cust[df_cust['Date'].dt.year == target_year]
    df_prev_yr = df_cust[df_cust['Date'].dt.year == target_year - 1]

    target_col = h_col if h_col in df.columns else 'Items'
    dropped_items = list(set(df_prev_yr[target_col].unique()) - set(df_curr_yr[target_col].unique()))
    if not dropped_items: return go.Figure(layout={'title': "No Dropped Items Found (Good Retention!)"}), ""

    df_lost = df_prev_yr[df_prev_yr[target_col].isin(dropped_items)]
    lost_rank = core_engine.get_top_performers(df_lost, target_col, vol_col, top_n=15)

    title = f"Potential Loss: {target_col} Bought in {target_year - 1} but NOT in {target_year}"
    fig = s.create_bar_chart(lost_rank, target_col, 'Value', title, vol_col, METRIC_MODE, is_horizontal=True)
    fig.update_traces(marker_color='#e74c3c')

    return fig, s.get_storytelling_anno(lost_rank, target_col, 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('loss-deep-dive', 'style'), Output('ci-loss-title', 'children'),
     Output('ci-loss-other', 'figure'), Output('ci-loss-other-anno', 'children'),
     Output('ci-loss-var', 'figure'), Output('ci-loss-var-anno', 'children')],
    [Input('ci-loss-item', 'data'), Input('ci-year', 'value'), State('ci-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_loss_drill(loss_item, year, h_col, auth_data):
    empty_res = {'display': 'none'}, "", go.Figure(), "", go.Figure(), ""
    if not loss_item: return empty_res

    df = get_secure_ci_df(auth_data)
    target_year = int(year)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    target_col = h_col if h_col in df.columns else 'Items'

    df_curr = df[(df[target_col].astype(str).str.strip().str.upper() == str(loss_item).strip().upper()) & (
                df['Date'].dt.year == target_year)]
    df_prev = df[(df[target_col].astype(str).str.strip().str.upper() == str(loss_item).strip().upper()) & (
                df['Date'].dt.year == target_year - 1)]

    buyers = core_engine.get_top_performers(df_curr, cust_col, vol_col, top_n=10)
    header_buyers = s.get_chart_title(f"Other Customers Buying {loss_item} ({target_year})", df_curr, df_prev,
                                      METRIC_MODE)
    fig_buyers = s.create_bar_chart(buyers, cust_col, 'Value', header_buyers, vol_col, METRIC_MODE, is_horizontal=True)
    fig_buyers.update_traces(marker_color='#95a5a6')

    fig_var, anno_var = go.Figure(layout={'title': "No Variant Data"}), ""
    if 'Name' in df.columns:
        variants = core_engine.get_top_performers(df_curr, 'Name', vol_col, top_n=10)
        header_var = s.get_chart_title(f"Variant Split: {loss_item}", df_curr, df_prev, METRIC_MODE)
        fig_var = s.create_bar_chart(variants, 'Name', 'Value', header_var, vol_col, METRIC_MODE, is_horizontal=True)
        anno_var = s.get_storytelling_anno(variants, 'Name', 'Value', vol_col, METRIC_MODE)

    return {'display': 'block'}, f"Why did we lose {loss_item}?", fig_buyers, s.get_storytelling_anno(buyers, cust_col,
                                                                                                      'Value', vol_col,
                                                                                                      METRIC_MODE), fig_var, anno_var


@callback(
    Output("ci-dl", "data"), Input("ci-snap", "n_clicks"),
    [State('ci-top', 'figure'), State('ci-trend', 'figure'), State('ci-daily', 'figure'),
     State('ci-mix', 'figure'), State('ci-prod-trend', 'figure'), State('ci-prod-daily', 'figure'),
     State('ci-rep', 'figure'), State('ci-rep-trend', 'figure'), State('ci-rep-daily', 'figure'),
     State('ci-dropped', 'figure'), State('ci-loss-other', 'figure'), State('ci-loss-var', 'figure')],
    prevent_initial_call=True
)
def download_snapshot(n, *figures):
    html_content = f"""
    <html>
    <head>
        <title>Customer Intelligence Snapshot</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; padding: 20px; background-color: #f4f4f4; }}
            .chart-container {{ background: white; margin-bottom: 20px; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ text-align: center; color: #2c3e50; }}
            .timestamp {{ text-align: center; color: #7f8c8d; font-size: 0.9em; margin-bottom: 30px; }}
        </style>
    </head>
    <body>
        <h1>Customer Intelligence Report</h1>
        <p class="timestamp">Generated on {pd.Timestamp.now().strftime('%d-%b-%Y %H:%M')}</p>
    """
    for fig in figures:
        if fig and isinstance(fig, dict) and 'data' in fig and len(fig['data']) > 0:
            html_content += f'<div class="chart-container">{pio.to_html(go.Figure(fig), full_html=False, include_plotlyjs="cdn")}</div>'

    html_content += "</body></html>"
    return dict(content=html_content, filename=f"ci_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")
@callback(
    Output('ci-segment-container', 'style'),
    [Input('auth-store', 'data')]
)
def setup_ci_permissions(auth_data):
    ui_rules = auth_data.get('ui_permissions', {}) if auth_data else {}
    if ui_rules.get('show_segment_filters', True):
        return {'display': 'inline-block'}
    return {'display': 'none'}