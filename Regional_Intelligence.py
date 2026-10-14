import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine

import dash
from dash import html, dcc, Input, Output, State, ctx, callback, no_update
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np

from data_loader import df_global, last_date_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security


def get_secure_reg_df(auth_data):
    _df, _, _, _ = get_latest_data()
    df = enforce_row_level_security(_df, auth_data)
    if not df.empty:
        bins = [0, 50, 150, 300, 500, 10000]
        labels = ['Metro (<50km)', 'Short (50-150km)', 'Medium (150-300km)', 'Long (300-500km)', 'Remote (>500km)']
        df['Dist_Bin'] = pd.cut(df['Distance'], bins=bins, labels=labels, right=False).astype(str).replace('nan',
                                                                                                           'Unknown')
    return df


layout = html.Div([
    dcc.Download(id="reg-dl"),
    html.Div([
        html.H1("Regional Logistics Intelligence", style=s.HEADER_TITLE),
        html.P(f"Data as of: {last_date_global if last_date_global else 'N/A'}",
               style={'textAlign': 'center', 'color': '#7f8c8d', 'marginBottom': '20px'}),

        html.Div([
            html.Div([html.Span("HIERARCHY:", style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6',
                                                     'marginRight': '5px'}),
                      dcc.RadioItems(id='reg-hier',
                                     options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                              {'label': 'Item', 'value': 'Name'}], value='Items 2', inline=True,
                                     style={'fontSize': '14px'})],
                     style={'marginRight': '20px', 'display': 'inline-block'}),
            html.Div([html.Span("PRODUCT:", style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6',
                                                   'marginRight': '5px'}),
                      dcc.Dropdown(id='reg-prod', placeholder="All Products", style={'width': '200px'})],
                     style={'marginRight': '20px', 'display': 'inline-block'}),
            html.Div([html.Span("SEGMENT:", style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6',
                                                   'marginRight': '5px'}),
                      dcc.RadioItems(id='reg-segment',
                                     options=[{'label': 'All', 'value': 'All'}, {'label': 'B2B', 'value': 'B2B'},
                                              {'label': 'B2C', 'value': 'B2C'}], value='All', inline=True,
                                     style={'fontSize': '14px'})],
                     style={'marginRight': '20px', 'display': 'inline-block'}),
            html.Div([html.Span("YEAR:", style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6',
                                                'marginRight': '5px'}),
                      dcc.Dropdown(id='reg-year', options=[{'label': str(y), 'value': y} for y in years_global],
                                   value=years_global[0] if years_global else None, clearable=False,
                                   style={'width': '80px'})],
                     style={'marginRight': '20px', 'display': 'inline-block'}),
            html.Div([html.Span("VIEW:", style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6',
                                                'marginRight': '5px'}),
                      dcc.RadioItems(id='reg-view', options=['Week', 'Month', 'Quarter', 'Year'], value='Month', inline=True,
                                     style={'fontSize': '14px'})],
                     style={'marginRight': '20px', 'display': 'inline-block'}),
            html.Button("📸 Snapshot", id="reg-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'backgroundColor': '#f8f9fa',
                  'padding': '15px', 'borderRadius': '8px', 'border': f'1px solid {s.COLORS["border"]}'})
    ], style=s.SECTION),

    html.Div(id='reg-kpis', style={'display': 'flex', 'justifyContent': 'space-around', 'marginBottom': '20px'}),

    html.Div([
        html.H3("Regional Revenue Heatmap", style={'textAlign': 'center', 'marginBottom': '10px'}),
        html.P("Hotspots indicate high revenue density.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px'}),
        dcc.Graph(id='reg-map', style={'height': '75vh'})
    ], style=s.CHART_CONTAINER),

    html.Div([
        html.Div([html.H3("Efficiency: Revenue vs Distance", style={'textAlign': 'center'}),
                  dcc.Graph(id='reg-scatter', style={'height': '55vh'})],
                 style={**s.CHART_CONTAINER, 'width': '49%', 'display': 'inline-block', 'verticalAlign': 'top',
                        'marginRight': '1%'}),
        html.Div([html.H3("Revenue by Logistics Zone", style={'textAlign': 'center'}),
                  dcc.Graph(id='reg-zone-bar', style={'height': '55vh'})],
                 style={**s.CHART_CONTAINER, 'width': '49%', 'display': 'inline-block', 'verticalAlign': 'top'})
    ], style=s.SECTION),

    html.Div([
        dcc.Graph(id='reg-loc-bar', style={'height': '60vh'}), html.Div(id='reg-loc-anno'),
        html.P("Click a location bar above to reveal top customers in that region.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px', 'marginTop': '10px'})
    ], style=s.CHART_CONTAINER),

    html.Div(id='reg-cust-drilldown', style={'display': 'none'}, children=[
        html.H3(id='reg-cust-title', style={'textAlign': 'center', 'marginTop': '30px', 'color': '#2c3e50'}),
        dcc.Graph(id='reg-cust-bar', style={'height': '50vh'})
    ])

], style=s.MAIN_CONTAINER)


@callback(Output('reg-prod', 'options'), [Input('reg-hier', 'value'), Input('reg-year', 'value')],
          [State('auth-store', 'data')])
def update_search(h, y, auth_data):
    df = get_secure_reg_df(auth_data)
    if not df.empty and y:
        dff = df[df['Date'].dt.year == int(y)]
        if h in dff.columns: return [{'label': i, 'value': i} for i in sorted(dff[h].dropna().unique())]
    return []


@callback(
    [Output('reg-kpis', 'children'), Output('reg-map', 'figure'), Output('reg-scatter', 'figure'),
     Output('reg-zone-bar', 'figure'), Output('reg-loc-bar', 'figure'), Output('reg-loc-anno', 'children')],
    [Input('reg-year', 'value'), Input('reg-view', 'value'), Input('reg-segment', 'value'), Input('reg-prod', 'value'),
     Input('reg-hier', 'value')],
    [State('auth-store', 'data')]
)
def update_dash(year, view, seg, prod, h_col, auth_data):
    empty_res = [], go.Figure(), go.Figure(), go.Figure(), go.Figure(), ""
    df = get_secure_reg_df(auth_data)
    if df.empty or not year: return empty_res

    # Filtering Segment & Product first before sending to the engine
    if seg != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == str(seg).strip().upper()]
    if prod and h_col in df.columns:
        df = df[df[h_col].astype(str).str.strip().str.upper() == str(prod).strip().upper()]

    # THE FIX: Rely on Core Engine for time slicing
    df_curr, df_prev, lbl = core_engine.get_time_window(df, year, view)
    if df_curr.empty: return empty_res

    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    locs_c = df_curr['Location'].nunique()
    locs_p = df_prev['Location'].nunique()
    rev_c = df_curr['Value'].sum()
    rev_p = df_prev['Value'].sum()
    dist_c = df_curr['Distance'].sum()
    dist_p = df_prev['Distance'].sum()
    eff_c = rev_c / dist_c if dist_c > 0 else 0
    eff_p = rev_p / dist_p if dist_p > 0 else 0
    avg_d_c = df_curr['Distance'].mean() if not df_curr.empty else 0
    avg_d_p = df_prev['Distance'].mean() if not df_prev.empty else 0

    kpis = [
        s.create_kpi_card("Active Locations", locs_c, locs_p, label=lbl),
        s.create_kpi_card("Total Revenue", rev_c, rev_p, prefix="Ksh ", label=lbl),
        s.create_kpi_card("Efficiency (Rev/KM)", eff_c, eff_p, prefix="Ksh ", label=lbl),
        s.create_kpi_card("Avg Trip Radius", avg_d_c, avg_d_p, suffix=" km", label=lbl)
    ]

    map_df = df_curr.dropna(subset=['Lat', 'Lon'])
    if map_df.empty:
        fig_map = go.Figure(
            layout={'title': "No Coordinates Found", 'plot_bgcolor': 'rgba(0,0,0,0)', 'paper_bgcolor': 'rgba(0,0,0,0)'})
    else:
        map_agg = map_df.groupby(['Location', 'Lat', 'Lon'])['Value'].sum().reset_index()
        fig_map = px.density_mapbox(map_agg, lat='Lat', lon='Lon', z='Value', radius=20,
                                    center=dict(lat=-0.0236, lon=37.9062), zoom=5.5, mapbox_style="carto-positron",
                                    hover_name='Location', hover_data={'Lat': False, 'Lon': False, 'Value': ':,.0f'})
        fig_map.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0}, plot_bgcolor='rgba(0,0,0,0)',
                              paper_bgcolor='rgba(0,0,0,0)')

    loc_sum = df_curr.groupby('Location').agg(Rev=('Value', 'sum'), Dist=('Distance', 'mean'),
                                              Count=('Date', 'count')).reset_index()
    fig_scat = px.scatter(loc_sum, x='Dist', y='Rev', size='Count', hover_name='Location', color='Dist',
                          color_continuous_scale='Bluered')
    fig_scat.update_layout(margin=dict(l=0, r=0, t=20, b=0), plot_bgcolor='rgba(0,0,0,0)',
                           paper_bgcolor='rgba(0,0,0,0)', xaxis_title="Distance (km)", yaxis_title="Revenue (Ksh)")

    zone_sum = df_curr.groupby('Dist_Bin')[['Value', vol_col, 'Quantity']].sum().reset_index()
    zone_sum['Avg_Price'] = (zone_sum['Value'] / zone_sum['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)
    order = ['Metro (<50km)', 'Short (50-150km)', 'Medium (150-300km)', 'Long (300-500km)', 'Remote (>500km)']
    fig_zone = s.create_bar_chart(zone_sum, 'Dist_Bin', 'Value', "", vol_col, METRIC_MODE)
    fig_zone.update_traces(marker_color='#3498db')
    fig_zone.update_xaxes(categoryorder='array', categoryarray=order)
    fig_zone.update_layout(margin=dict(l=0, r=0, t=20, b=0))

    top_loc = core_engine.get_top_performers(df_curr, 'Location', vol_col, top_n=20)
    title_loc = s.get_chart_title(f"Top 20 Locations ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_bar = s.create_bar_chart(top_loc, 'Location', 'Value', title_loc, vol_col, METRIC_MODE, is_horizontal=True,
                                 use_z_colors=True)
    anno_bar = s.get_storytelling_anno(top_loc, 'Location', 'Value', vol_col, METRIC_MODE)

    return kpis, fig_map, fig_scat, fig_zone, fig_bar, anno_bar


@callback(
    [Output('reg-cust-drilldown', 'style'), Output('reg-cust-title', 'children'), Output('reg-cust-bar', 'figure')],
    [Input('reg-loc-bar', 'clickData'), Input('reg-year', 'value'), Input('reg-view', 'value'),
     Input('reg-segment', 'value'), Input('reg-prod', 'value'), Input('reg-hier', 'value')],
    [State('auth-store', 'data')]
)
def drill_location_customers(clk, year, view, seg, prod, h_col, auth_data):
    if not clk: return {'display': 'none'}, "", go.Figure()

    loc = clk['points'][0]['y']
    df = get_secure_reg_df(auth_data)

    if seg != 'All' and 'B2B' in df.columns: df = df[
        df['B2B'].astype(str).str.strip().str.upper() == str(seg).strip().upper()]
    if prod and h_col in df.columns: df = df[df[h_col].astype(str).str.strip().str.upper() == str(prod).strip().upper()]

    # THE FIX: Bulletproof String Match
    df = df[df['Location'].astype(str).str.strip().str.upper() == str(loc).strip().upper()]

    df_curr, df_prev, lbl = core_engine.get_time_window(df, year, view)

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), None)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    if df_curr.empty or not cust_col: return {'display': 'none'}, "", go.Figure()

    top_cust = core_engine.get_top_performers(df_curr, cust_col, vol_col, top_n=15)
    header = s.get_chart_title(f"Top Buyers in {loc} ({lbl})", df_curr, df_prev, METRIC_MODE)
    fig_cust = s.create_bar_chart(top_cust, cust_col, 'Value', header, vol_col, METRIC_MODE, is_horizontal=True)
    fig_cust.update_traces(marker_color='#2ecc71')

    return {'display': 'block', 'padding': '20px', 'backgroundColor': 'white',
            'border': f'1px solid {s.COLORS["border"]}', 'borderRadius': '8px',
            'marginTop': '20px'}, f"Customer Drilldown: {loc}", fig_cust


@callback(Output("reg-dl", "data"), Input("reg-snap", "n_clicks"),
          [State('reg-map', 'figure'), State('reg-scatter', 'figure'), State('reg-zone-bar', 'figure'),
           State('reg-loc-bar', 'figure')], prevent_initial_call=True)
def download(n, f1, f2, f3, f4):
    import plotly.io as pio
    html = "<html><body><h1 style='text-align:center;'>Regional Snapshot</h1>"
    for f in [f1, f2, f3, f4]:
        if f: html += f"<div style='margin-bottom:20px;'>{pio.to_html(go.Figure(f), full_html=False, include_plotlyjs='cdn')}</div>"
    return dict(content=html + "</body></html>", filename="regional_snapshot.html")