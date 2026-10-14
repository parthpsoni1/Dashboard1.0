import dash
from dash import html, dcc, Input, Output, dash_table, ctx, State, callback, no_update
from dash.dash_table.Format import Format, Group, Scheme
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import timedelta
import plotly.io as pio

# --- STATSMODELS ---
try:
    from statsmodels.tsa.seasonal import seasonal_decompose
except ImportError:
    seasonal_decompose = None
    print("Warning: statsmodels not installed. Seasonal decomposition will not work.")

# --- IMPORTS ---
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
from data_loader import df_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security

# --- DATA PREP ---
if df_global is None:
    df = pd.DataFrame()
    available_years = []
else:
    df = df_global.copy()
    available_years = years_global


# --- SECURITY HELPER ---
def get_secure_ea_df(auth_data):
    _df, _, _, _ = get_latest_data()
    df_sec = enforce_row_level_security(_df, auth_data)
    if not df_sec.empty:
        df_sec['Value'] = pd.to_numeric(df_sec['Value'], errors='coerce').fillna(0)
        df_sec['QTY PCS'] = pd.to_numeric(df_sec['QTY PCS'], errors='coerce').fillna(0)
        df_sec['Basket_ID'] = df_sec['Customer Names'] + "_" + df_sec['Date'].astype(str)
    return df_sec


# --- STYLING FOR INSIGHT BOXES ---
INSIGHT_STYLE = {
    'backgroundColor': '#e8f4f8', 'borderLeft': '4px solid #3498db', 'padding': '15px',
    'borderRadius': '4px', 'marginBottom': '20px', 'color': '#2c3e50', 'fontSize': '14px', 'lineHeight': '1.5'
}


# --- HELPER FUNCTIONS ---
def calculate_rfm(df_filtered, snapshot_date):
    if df_filtered.empty: return pd.DataFrame()
    rfm = df_filtered.groupby('Customer Names').agg({
        'Date': lambda x: (snapshot_date - x.max()).days,
        'Value': 'sum',
        'Basket_ID': 'nunique',
        'B2B': 'first'
    }).rename(columns={'Date': 'Recency', 'Value': 'Monetary', 'Basket_ID': 'Frequency'})

    def safe_qcut(series, k, labels):
        try:
            return pd.qcut(series.rank(method='first'), k, labels=labels)
        except ValueError:
            return pd.Series([labels[0]] * len(series), index=series.index)

    rfm['R_Score'] = safe_qcut(rfm['Recency'], 5, labels=[5, 4, 3, 2, 1])
    rfm['F_Score'] = safe_qcut(rfm['Frequency'], 5, labels=[1, 2, 3, 4, 5])
    rfm['M_Score'] = safe_qcut(rfm['Monetary'], 5, labels=[1, 2, 3, 4, 5])
    rfm['RFM_Score'] = rfm[['R_Score', 'F_Score', 'M_Score']].astype(int).sum(axis=1)

    def segment_customer(row):
        score = row['RFM_Score']
        if score >= 14:
            return 'Champions'
        elif score >= 11:
            return 'Loyal Customers'
        elif score >= 8:
            return 'Potential Loyalists'
        elif score >= 6:
            return 'At Risk'
        elif score >= 4:
            return 'Needs Attention'
        else:
            return 'Lost'

    def action_playbook(row):
        seg = row['Segment']
        if seg == 'Champions':
            return "Reward & Request Referrals"
        elif seg == 'Loyal Customers':
            return "Upsell Higher Margin Items"
        elif seg == 'Potential Loyalists':
            return "Offer Loyalty Program / Volume Discount"
        elif seg == 'At Risk':
            return "Send 'We Miss You' Targeted Promotion"
        elif seg == 'Needs Attention':
            return "Reactivation Campaign"
        else:
            return "Do Not Spend Marketing Budget Here"

    rfm['Segment'] = rfm.apply(segment_customer, axis=1)
    rfm['Suggested Action'] = rfm.apply(action_playbook, axis=1)
    rfm.reset_index(inplace=True)
    return rfm


def remove_outliers_iqr(x, y):
    df_temp = pd.DataFrame({'x': x, 'y': y})
    Q1 = df_temp.quantile(0.25);
    Q3 = df_temp.quantile(0.75);
    IQR = Q3 - Q1
    mask = ~((df_temp < (Q1 - 1.5 * IQR)) | (df_temp > (Q3 + 1.5 * IQR))).any(axis=1)
    return x[mask], y[mask], len(x) - mask.sum()


def calc_regression(x, y, mode='linear'):
    if len(x) < 2: return None, None, None, None, None
    if mode == 'linear':
        coeffs = np.polyfit(x, y, 1);
        y_pred = np.poly1d(coeffs)(x)
        m = coeffs[0];
        eq = f"y = {coeffs[0]:.4f}x + {coeffs[1]:.2f}"
    elif mode == 'power':
        mask = (x > 0) & (y > 0)
        x_clean, y_clean = x[mask], y[mask]
        if len(x_clean) < 2: return None, None, None, None, None
        log_x, log_y = np.log(x_clean), np.log(y_clean)
        coeffs = np.polyfit(log_x, log_y, 1)
        b, a = coeffs[0], np.exp(coeffs[1])
        y_pred = a * np.power(x_clean, b);
        m = b;
        eq = f"y = {a:.2f}x^{b:.3f}"
        sort_idx = np.argsort(x_clean)
        return x_clean.iloc[sort_idx], y_pred.iloc[sort_idx], eq, 0, m

    ss_res = np.sum((y - y_pred) ** 2);
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
    return x, y_pred, f"{eq} (R²={r2:.2f})", r2, m


# --- TAB BUILDERS ---
def build_rfm_tab():
    return html.Div([
        html.Div([
            html.Span("FILTER:", style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.RadioItems(id='rfm-segment-filter',
                           options=[{'label': 'All', 'value': 'All'}, {'label': 'B2B', 'value': 'B2B'},
                                    {'label': 'B2C', 'value': 'B2C'}], value='All', inline=True,
                           style={'display': 'inline-block'}),
            html.Button("Reset Table", id='rfm-reset-btn',
                        style={**s.BTN_STYLE, 'marginLeft': 'auto', 'marginRight': '10px'}),
            html.Button("📥 Download Hit List", id='rfm-download-btn',
                        style={'backgroundColor': '#27ae60', 'color': 'white', 'border': 'none', 'padding': '8px 16px',
                               'borderRadius': '4px', 'cursor': 'pointer', 'fontWeight': 'bold'}),
            dcc.Download(id="download-rfm-csv")
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '20px', 'padding': '10px',
                  'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

        html.Div(id='rfm-auto-insight', style=INSIGHT_STYLE),

        html.Div([
            html.Div([dcc.Graph(id='rfm-bar-chart', style={'height': '450px'})],
                     style={'flex': '1', 'minWidth': '0', 'backgroundColor': 'white', 'padding': '10px',
                            'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
                            'border': '1px solid #e0e0e0'}),
            html.Div([dcc.Graph(id='rfm-3d-chart', style={'height': '450px'})],
                     style={'flex': '1', 'minWidth': '0', 'backgroundColor': 'white', 'padding': '10px',
                            'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
                            'border': '1px solid #e0e0e0'}),
        ], style={'display': 'flex', 'gap': '20px', 'width': '100%', 'marginBottom': '20px',
                  'boxSizing': 'border-box'}),

        html.H3("Customer Action Playbook", style={'textAlign': 'center', 'marginTop': '20px'}),
        dash_table.DataTable(id='rfm-table',
                             columns=[
                                 {'name': 'Customer', 'id': 'Customer Names'},
                                 {'name': 'Health Segment', 'id': 'Segment'},
                                 {'name': 'Suggested Action', 'id': 'Suggested Action'},
                                 {'name': 'Total Sales', 'id': 'Monetary', 'type': 'numeric',
                                  'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                                 {'name': 'Days Inactive', 'id': 'Recency'}
                             ],
                             style_table={'height': '400px', 'overflowY': 'auto'}, sort_action='native',
                             filter_action='native',
                             style_header={'backgroundColor': s.COLORS['primary'], 'color': 'white',
                                           'fontWeight': 'bold'},
                             style_cell={'textAlign': 'left', 'padding': '10px'}, style_data_conditional=[
                {'if': {'row_index': 'odd'}, 'backgroundColor': 'rgb(248, 248, 248)'}]),
    ], style=s.SECTION)


def build_basket_tab():
    return html.Div([
        html.Div([
            html.Span("HIERARCHY:", style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.RadioItems(id='basket-hier',
                           options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                           style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Span("TARGET:", style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.Dropdown(id='basket-item-select', placeholder="Select Item/Group...",
                         style={'width': '300px', 'display': 'inline-block'})
        ], style={'marginBottom': '20px', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

        html.Div(id='basket-auto-insight', style=INSIGHT_STYLE),

        html.Div([dcc.Graph(id='basket-chart', style={'height': '60vh'})], style=s.CHART_CONTAINER),

        html.Div(id='basket-comparison-container',
                 style={'display': 'none', 'marginTop': '20px', 'padding': '20px', 'backgroundColor': 'white',
                        'borderRadius': '8px', 'border': '1px solid #e0e0e0'}, children=[
                html.Div([
                    html.H3("Cross-Sell Pitch Opportunity", style={'display': 'inline-block', 'marginRight': '20px'}),
                    html.Button("📥 Download Target List", id='basket-download-btn',
                                style={'backgroundColor': '#27ae60', 'color': 'white', 'border': 'none',
                                       'padding': '8px 16px', 'borderRadius': '4px', 'cursor': 'pointer',
                                       'fontWeight': 'bold'}),
                    dcc.Download(id="download-basket-csv")
                ], style={'marginBottom': '10px'}),
                html.P(id='basket-pitch-insight', style={'color': '#7f8c8d'}),
                dcc.Graph(id='basket-compare-chart')
            ])
    ], style=s.SECTION)


def build_forecast_tab():
    return html.Div([
        html.Div([
            html.Span("CONTEXT:", style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.RadioItems(id='forecast-context',
                           options=[{'label': 'Item', 'value': 'item'}, {'label': 'Customer', 'value': 'cust'},
                                    {'label': 'Sales Rep', 'value': 'rep'}, {'label': 'Segment', 'value': 'seg'}],
                           value='item', inline=True, style={'display': 'inline-block', 'marginRight': '20px'}),
            dcc.Dropdown(id='forecast-target-select', placeholder="Select Target...",
                         style={'width': '250px', 'display': 'inline-block', 'marginRight': '20px'}),
            html.Div([
                html.Label("Target Growth:", style={'fontWeight': 'bold', 'fontSize': '11px', 'marginRight': '5px'}),
                dcc.Input(id='forecast-goal-input', type='number', value=10, min=-50, max=200, step=1,
                          style={'width': '60px'}),
                html.Span("%", style={'fontSize': '12px', 'marginLeft': '2px', 'marginRight': '15px'}),
            ], style={'display': 'inline-block', 'borderLeft': '1px solid #ddd', 'paddingLeft': '15px'})
        ], style={'marginBottom': '20px', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

        html.Div(id='forecast-auto-insight', style=INSIGHT_STYLE),
        html.Div([dcc.Graph(id='forecast-chart', style={'height': '60vh'})], style=s.CHART_CONTAINER),

        html.Div([
            dcc.Checklist(id='advanced-toggle',
                          options=[{'label': ' Show Advanced Data Science Analytics', 'value': 'show'}], value=[],
                          style={'marginTop': '20px', 'fontWeight': 'bold', 'color': '#2c3e50'})
        ]),

        html.Div(id='advanced-ds-container', style={'display': 'none'}, children=[
            html.H3("Seasonality Decomposition (Trend vs Cycles vs Noise)",
                    style={'textAlign': 'center', 'marginTop': '30px'}),
            html.Div([
                html.Span("DECOMPOSITION MODEL: ",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '10px'}),
                dcc.RadioItems(id='decomp-model-toggle', options=[
                    {'label': ' Multiplicative (Best for scaling growth)', 'value': 'multiplicative'},
                    {'label': ' Additive (Best for stable volume)', 'value': 'additive'}], value='multiplicative',
                               inline=True, style={'fontSize': '13px'})
            ], style={'textAlign': 'center', 'marginBottom': '20px', 'padding': '10px', 'backgroundColor': '#f8f9fa',
                      'borderRadius': '8px'}),

            dcc.Graph(id='seasonality-chart', style={'height': '600px'})
        ])
    ], style=s.SECTION)


def build_elasticity_tab():
    return html.Div([
        html.Div([
            html.Span("HIERARCHY:", style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.RadioItems(id='elasticity-hier',
                           options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                           style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Span("ITEM:", style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
            dcc.Dropdown(id='elasticity-item-select', placeholder="Select Item (or ALL)...", value='ALL',
                         style={'width': '250px', 'display': 'inline-block', 'marginRight': '20px'}),

            html.Div(id='simulator-container', children=[
                html.Span("SIMULATE PRICE CHANGE:",
                          style={'fontWeight': 'bold', 'color': '#95a5a6', 'marginRight': '10px'}),
                dcc.Slider(id='price-simulator-slider', min=-30, max=30, step=1, value=-5,
                           marks={-30: '-30%', 0: '0%', 30: '+30%'})
            ], style={'width': '300px', 'display': 'inline-block', 'verticalAlign': 'middle', 'marginLeft': '20px',
                      'borderLeft': '1px solid #ddd', 'paddingLeft': '20px'})
        ], style={'marginBottom': '20px', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px'}),

        html.Div(id='elasticity-auto-insight', style=INSIGHT_STYLE),

        html.Div(id='simulator-cards', children=[
            html.Div(id='sim-vol-card',
                     style={'flex': '1', 'backgroundColor': 'white', 'padding': '20px', 'borderRadius': '8px',
                            'textAlign': 'center', 'border': '1px solid #eee',
                            'boxShadow': '0 2px 4px rgba(0,0,0,0.05)'}),
            html.Div(id='sim-rev-card',
                     style={'flex': '1', 'backgroundColor': 'white', 'padding': '20px', 'borderRadius': '8px',
                            'textAlign': 'center', 'border': '1px solid #eee',
                            'boxShadow': '0 2px 4px rgba(0,0,0,0.05)'})
        ], style={'display': 'flex', 'gap': '20px', 'marginBottom': '20px'}),

        # ADVANCED TOGGLE
        html.Div([
            dcc.Checklist(id='elasticity-advanced-toggle',
                          options=[{'label': ' Show Advanced Chart Controls', 'value': 'show'}], value=[],
                          style={'marginTop': '10px', 'marginBottom': '10px', 'fontWeight': 'bold',
                                 'color': '#2c3e50'}),
            html.Div(id='elasticity-advanced-controls', style={'display': 'none'}, children=[
                html.Span("CLEANING: ",
                          style={'fontWeight': 'bold', 'fontSize': '12px', 'marginRight': '10px', 'color': '#7f8c8d'}),
                dcc.Checklist(id='elasticity-options', options=[{'label': ' Remove Outliers (IQR)', 'value': 'outlier'}, {'label': ' Show Power Law', 'value': 'power'}, {'label': ' Show Linear', 'value': 'linear'}], value=['power'], inline=True, style={'display': 'inline-block', 'marginRight': '20px', 'fontSize': '13px'}),
                html.Span("AXIS SCALE: ",
                          style={'fontWeight': 'bold', 'fontSize': '12px', 'marginRight': '10px', 'color': '#7f8c8d'}),
                dcc.RadioItems(id='elasticity-scale', options=[{'label': ' Logarithmic', 'value': 'log'},
                                                               {'label': ' Linear', 'value': 'linear'}], value='log',
                               inline=True, style={'display': 'inline-block', 'fontSize': '13px'})
            ])
        ]),

        html.Div([dcc.Graph(id='elasticity-chart', style={'height': '60vh'})], style=s.CHART_CONTAINER),

        html.Div(id='elasticity-stats'),
        html.Div(id='elasticity-examples-container', style={'marginTop': '30px'})

    ], style=s.SECTION)


# --- LAYOUT ---
layout = html.Div([
    dcc.Store(id='rfm-data-store'), dcc.Store(id='store-basket-item'),
    dcc.Store(id='snap-rfm-bar'), dcc.Store(id='snap-rfm-3d'), dcc.Store(id='snap-basket-main'),
    dcc.Store(id='snap-basket-comp'),
    dcc.Store(id='snap-forecast'), dcc.Store(id='snap-seasonality'), dcc.Store(id='snap-elasticity'),
    dcc.Download(id="ea-dl"),

    html.Div([
        html.H1("Sales Strategy & Prescriptive Analytics", style=s.HEADER_TITLE),
        html.Div([
            html.Label("Analysis Year:", style={'fontWeight': 'bold', 'marginRight': '10px', 'color': '#7f8c8d'}),
            dcc.Dropdown(id='year-select', options=[{'label': str(y), 'value': y} for y in available_years],
                         value=available_years[0] if available_years else None, clearable=False,
                         style={'width': '100px', 'display': 'inline-block'}),
            html.Button("📸 Snapshot", id="ea-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center', 'marginTop': '10px'}),

        dcc.Tabs(id='analysis-tabs', value='tab-rfm', children=[
            dcc.Tab(label='Customer Health & Actions', value='tab-rfm'),
            dcc.Tab(label='Cross-Selling Opportunities', value='tab-basket'),
            dcc.Tab(label='Sales Trajectory & Goals', value='tab-forecast'),
            dcc.Tab(label='Pricing Power & Simulator', value='tab-elasticity'),
        ], style={'marginTop': '20px', 'fontWeight': 'bold'}),
    ], style=s.SECTION),

    html.Div(id='content-rfm', children=build_rfm_tab(), style={'display': 'block'}),
    html.Div(id='content-basket', children=build_basket_tab(), style={'display': 'none'}),
    html.Div(id='content-forecast', children=build_forecast_tab(), style={'display': 'none'}),
    html.Div(id='content-elasticity', children=build_elasticity_tab(), style={'display': 'none'}),
], style=s.MAIN_CONTAINER)


# --- VISIBILITY CALLBACK ---
@callback(
    [Output('content-rfm', 'style'), Output('content-basket', 'style'),
     Output('content-forecast', 'style'), Output('content-elasticity', 'style')],
    [Input('analysis-tabs', 'value')]
)
def tab_toggle(tab):
    show = {'display': 'block'};
    hide = {'display': 'none'}
    if tab == 'tab-rfm':
        return show, hide, hide, hide
    elif tab == 'tab-basket':
        return hide, show, hide, hide
    elif tab == 'tab-forecast':
        return hide, hide, show, hide
    elif tab == 'tab-elasticity':
        return hide, hide, hide, show
    return show, hide, hide, hide


# --- CALLBACKS: RFM ---
@callback(Output('rfm-data-store', 'data'), [Input('year-select', 'value')], [State('auth-store', 'data')])
def compute_rfm(year, auth_data):
    if not year: return []
    df = get_secure_ea_df(auth_data)
    df_year = df[df['Date'].dt.year == int(year)]
    if df_year.empty: return []
    snapshot = df_year['Date'].max() + timedelta(days=1)
    return calculate_rfm(df_year, snapshot).to_dict('records')


@callback(
    [Output('rfm-bar-chart', 'figure'), Output('rfm-3d-chart', 'figure'), Output('rfm-table', 'data'),
     Output('rfm-auto-insight', 'children'), Output('snap-rfm-bar', 'data'), Output('snap-rfm-3d', 'data')],
    [Input('analysis-tabs', 'value'), Input('rfm-data-store', 'data'), Input('rfm-segment-filter', 'value'),
     Input('rfm-bar-chart', 'clickData'), Input('rfm-reset-btn', 'n_clicks')]
)
def update_rfm(tab, data, seg_filter, click_data, reset_click):
    if tab != 'tab-rfm': return [no_update] * 6
    if not data: return go.Figure(), go.Figure(), [], "No data available.", {}, {}
    rfm_df = pd.DataFrame(data)
    if seg_filter != 'All' and 'B2B' in rfm_df.columns:
        rfm_df = rfm_df[rfm_df['B2B'].astype(str).str.strip().str.upper() == str(seg_filter).strip().upper()]

    segments = ['Champions', 'Loyal Customers', 'Potential Loyalists', 'At Risk', 'Needs Attention', 'Lost']
    seg_counts = rfm_df['Segment'].value_counts().reindex(segments, fill_value=0).reset_index()
    seg_counts.columns = ['Segment', 'Count']
    colors = {"Champions": "#27ae60", "Loyal Customers": "#2ecc71", "Potential Loyalists": "#3498db",
              "At Risk": "#f39c12", "Needs Attention": "#e67e22", "Lost": "#c0392b"}

    fig_bar = px.bar(seg_counts, x='Segment', y='Count', color='Segment', color_discrete_map=colors,
                     title="Customer Segments Distribution")
    fig_bar.update_layout(clickmode='event+select', showlegend=False, margin=dict(l=20, r=20, t=40, b=20),
                          plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    fig_3d = px.scatter_3d(rfm_df, x='Recency', y='Frequency', z='Monetary', color='Segment', color_discrete_map=colors,
                           hover_data=['Customer Names'], title="RFM 3D Cluster View")
    fig_3d.update_layout(margin=dict(l=0, r=0, b=0, t=30), paper_bgcolor='rgba(0,0,0,0)')

    at_risk_count = len(rfm_df[rfm_df['Segment'] == 'At Risk'])
    at_risk_rev = rfm_df[rfm_df['Segment'] == 'At Risk']['Monetary'].sum()
    champ_count = len(rfm_df[rfm_df['Segment'] == 'Champions'])
    insight = html.Div([
        html.Strong("🤖 Auto-Analyst Insight: "),
        html.Span(f"You have "),
        html.Strong(f"{at_risk_count} customers in the 'At Risk' segment", style={'color': '#e74c3c'}),
        html.Span(f" representing "),
        html.Strong(f"Ksh {at_risk_rev:,.0f} in historical value.", style={'color': '#e74c3c'}),
        html.Span(
            f" Meanwhile, your {champ_count} 'Champions' should be targeted for referrals and premium upsells. Click any bar below to filter the Action Playbook table.")
    ])

    ctx_id = ctx.triggered_id
    dff_table = rfm_df.copy()
    if ctx_id == 'rfm-bar-chart' and click_data:
        dff_table = dff_table[dff_table['Segment'] == click_data['points'][0]['x']]

    return fig_bar, fig_3d, dff_table.to_dict('records'), insight, fig_bar, fig_3d


@callback(
    Output("download-rfm-csv", "data"),
    Input("rfm-download-btn", "n_clicks"),
    State("rfm-table", "data"),
    prevent_initial_call=True
)
def download_rfm_hitlist(n_clicks, table_data):
    if not table_data: return dash.no_update
    df_export = pd.DataFrame(table_data)
    return dcc.send_data_frame(df_export.to_csv, "Action_Playbook_Target_List.csv", index=False)


# --- CALLBACKS: BASKET ---
@callback(Output('basket-item-select', 'options'), [Input('basket-hier', 'value')], [State('auth-store', 'data')])
def update_basket_opts(h_col, auth_data):
    df = get_secure_ea_df(auth_data)
    return [{'label': i, 'value': i} for i in sorted(df[h_col].dropna().unique())] if h_col in df.columns else []


@callback(
    [Output('basket-chart', 'figure'), Output('basket-auto-insight', 'children'),
     Output('basket-comparison-container', 'style'), Output('basket-compare-chart', 'figure'),
     Output('basket-pitch-insight', 'children'),
     Output('snap-basket-main', 'data'), Output('snap-basket-comp', 'data')],
    [Input('analysis-tabs', 'value'), Input('basket-item-select', 'value'), Input('year-select', 'value'),
     Input('basket-hier', 'value'), Input('basket-chart', 'clickData')],
    [State('auth-store', 'data')]
)
def update_basket_chart(tab, target, year, h_col, click_data, auth_data):
    if tab != 'tab-basket': return [no_update] * 7
    if not target: return go.Figure(), "Select an item to discover cross-selling opportunities.", {
        'display': 'none'}, go.Figure(), "", {}, {}

    df_year = get_secure_ea_df(auth_data)
    df_year = df_year[df_year['Date'].dt.year == int(year)].copy()

    item_counts = df_year['Basket_ID'].nunique()
    item_support = df_year.groupby(h_col)['Basket_ID'].nunique() / item_counts

    target_baskets = df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(target).strip().upper()][
        'Basket_ID'].unique()
    df_co = df_year[df_year['Basket_ID'].isin(target_baskets)]
    df_co = df_co[df_co[h_col].astype(str).str.strip().str.upper() != str(target).strip().upper()]

    co_counts = df_co.groupby(h_col)['Basket_ID'].nunique().reset_index()
    co_counts.columns = [h_col, 'Freq_AB']
    co_counts = co_counts[co_counts['Freq_AB'] >= 3]

    if co_counts.empty: return go.Figure(
        layout={'title': f"No strong associations"}), "Not enough data to calculate cross-selling lift.", {
        'display': 'none'}, go.Figure(), "", {}, {}

    co_counts = co_counts.set_index(h_col)['Freq_AB']
    confidence = co_counts / len(target_baskets)
    lift = (confidence / item_support).dropna().sort_values(ascending=False).head(15)
    lift_df = lift.reset_index()
    lift_df.columns = ['Item', 'Lift']
    lift_df = lift_df.merge(co_counts.reset_index(), left_on='Item', right_on=h_col, how='left')

    fig = px.bar(lift_df, y='Item', x='Freq_AB', orientation='h', title=f"Highly Associated Items to: {target}",
                 color='Lift', color_continuous_scale='Teal', labels={'Freq_AB': 'Co-occurrence Count'})
    fig.update_traces(hovertemplate="<b>%{y}</b><br>Co-occurrences: %{x}<br>Lift: %{marker.color:.2f}<extra></extra>")
    fig.update_layout(yaxis={'categoryorder': 'total ascending'}, plot_bgcolor='rgba(0,0,0,0)',
                      paper_bgcolor='rgba(0,0,0,0)')

    top_assoc = lift_df.iloc[0]['Item']
    top_lift = lift_df.iloc[0]['Lift']
    insight = html.Div([
        html.Strong("🤖 Auto-Analyst Insight: "),
        html.Span(f"Customers who buy {target} are "),
        html.Strong(f"{top_lift:.1f}x more likely", style={'color': '#27ae60'}),
        html.Span(f" to also buy "), html.Strong(f"{top_assoc}."),
        html.Span(
            f" Click on '{top_assoc}' in the chart below to generate a Hit List of customers you should immediately pitch this combo to.")
    ])

    comp_style = {'display': 'none'};
    fig_comp = go.Figure();
    pitch_insight = ""
    if click_data:
        comp_item = click_data['points'][0]['y']
        comp_style = {'display': 'block', 'marginTop': '20px', 'padding': '20px', 'backgroundColor': 'white',
                      'borderRadius': '8px', 'border': '1px solid #e0e0e0'}

        df_trend = df_year[df_year[h_col].isin([target, comp_item])]
        trend = df_trend.groupby([pd.Grouper(key='Date', freq='ME'), h_col])['Value'].sum().reset_index()
        pivot_trend = trend.pivot(index='Date', columns=h_col, values='Value').fillna(0)
        corr = pivot_trend[target].corr(
            pivot_trend[comp_item]) if target in pivot_trend and comp_item in pivot_trend else 0

        target_cust = set(df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(target).strip().upper()][
                              'Customer Names'])
        comp_cust = set(df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(comp_item).strip().upper()][
                            'Customer Names'])
        missed_opps = len(target_cust - comp_cust)

        pitch_insight = f"There are {missed_opps} unique customers who have purchased '{target}' but NEVER purchased '{comp_item}'. Download the target list above to execute the cross-sell campaign."

        y_log = False
        max_t = trend[trend[h_col] == target]['Value'].max() if not trend[trend[h_col] == target].empty else 0
        max_c = trend[trend[h_col] == comp_item]['Value'].max() if not trend[trend[h_col] == comp_item].empty else 0
        if max_t > 0 and max_c > 0 and max(max_t, max_c) / min(max_t, max_c) > 10: y_log = True

        fig_comp = px.line(trend, x='Date', y='Value', color=h_col, title=f"Trend Comparison: Correlation {corr:.2f}",
                           markers=True, log_y=y_log)
        if y_log: fig_comp.update_layout(yaxis_title="Value (Log Scale)")
        fig_comp.update_layout(hovermode='x unified', plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    return fig, insight, comp_style, fig_comp, pitch_insight, fig, fig_comp


@callback(
    Output("download-basket-csv", "data"),
    Input("basket-download-btn", "n_clicks"),
    [State('basket-item-select', 'value'), State('basket-chart', 'clickData'), State('year-select', 'value'),
     State('basket-hier', 'value'), State('auth-store', 'data')],
    prevent_initial_call=True
)
def download_cross_sell(n_clicks, target, click_data, year, h_col, auth_data):
    if not target or not click_data: return dash.no_update
    comp_item = click_data['points'][0]['y']
    df_year = get_secure_ea_df(auth_data)
    df_year = df_year[df_year['Date'].dt.year == int(year)]

    target_cust = set(
        df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(target).strip().upper()]['Customer Names'])
    comp_cust = set(
        df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(comp_item).strip().upper()]['Customer Names'])

    hit_list = list(target_cust - comp_cust)
    df_export = pd.DataFrame({'Target Customers': hit_list, 'Action': f'Pitch {comp_item}'})
    return dcc.send_data_frame(df_export.to_csv, f"CrossSell_{target}_to_{comp_item}.csv", index=False)


# --- CALLBACKS: FORECAST ---
@callback(Output('forecast-target-select', 'options'), [Input('forecast-context', 'value')],
          [State('auth-store', 'data')])
def update_forecast_opts(ctx, auth_data):
    if ctx == 'seg': return [{'label': 'B2B', 'value': 'B2B'}, {'label': 'B2C', 'value': 'B2C'}]
    df = get_secure_ea_df(auth_data)
    col_map = {'item': 'Name', 'cust': 'Customer Names', 'rep': 'Sales Rep'}
    col = next((c for c in [col_map.get(ctx), 'Items'] if c in df.columns), None)
    if not col: return []
    return [{'label': i, 'value': i} for i in sorted(df[col].dropna().unique())] + [{'label': 'Global', 'value': 'ALL'}]


@callback(Output('advanced-ds-container', 'style'), [Input('advanced-toggle', 'value')])
def toggle_advanced(val):
    return {'display': 'block'} if 'show' in val else {'display': 'none'}


@callback(
    [Output('forecast-chart', 'figure'), Output('forecast-auto-insight', 'children'),
     Output('seasonality-chart', 'figure'), Output('snap-forecast', 'data'), Output('snap-seasonality', 'data')],
    [Input('analysis-tabs', 'value'), Input('forecast-target-select', 'value'), Input('forecast-context', 'value'),
     Input('year-select', 'value'), Input('forecast-goal-input', 'value'), Input('decomp-model-toggle', 'value')],
    [State('auth-store', 'data')]
)
def update_forecast_chart(tab, target, ctx_val, year, growth_target, decomp_model, auth_data):
    if tab != 'tab-forecast': return [no_update] * 5
    if not target: return go.Figure(), "Select a target to run projection.", go.Figure(), {}, {}

    df = get_secure_ea_df(auth_data)
    df_year = df[df['Date'].dt.year == int(year)].copy()

    if target != 'ALL':
        if ctx_val == 'seg' and 'B2B' in df.columns:
            df_year = df_year[df_year['B2B'].astype(str).str.strip().str.upper() == str(target).strip().upper()]
        else:
            col_map = {'item': 'Name', 'cust': 'Customer Names', 'rep': 'Sales Rep'}
            col = next((c for c in [col_map.get(ctx_val), 'Items'] if c in df.columns), None)
            if col: df_year = df_year[df_year[col].astype(str).str.strip().str.upper() == str(target).strip().upper()]

    daily = df_year.groupby('Date')['Value'].sum().reset_index()
    if daily.empty: return go.Figure(layout={'title': "No Data"}), "No data to analyze.", go.Figure(), {}, {}

    idx = pd.date_range(daily['Date'].min(), daily['Date'].max(), freq='D')
    daily = daily.set_index('Date').reindex(idx, fill_value=0).reset_index().rename(
        columns={'index': 'Date'}).set_index('Date')
    daily['MA30'] = daily['Value'].rolling(30).mean()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=daily.index, y=daily['Value'], name='Actual (Gross)', line=dict(color='#bdc3c7', width=1)))
    fig.add_trace(go.Scatter(x=daily.index, y=daily['MA30'], name='30D Trend', line=dict(color='#27ae60', width=3)))

    train = daily.reset_index().tail(60)
    insight_text = "Not enough data to calculate projection."
    if len(train) > 5:
        x = np.arange(len(train));
        z = np.polyfit(x, train['Value'], 1);
        p = np.poly1d(z)
        future_dates = [daily.index.max() + timedelta(days=i) for i in range(1, 31)]
        projected_vals = [max(0, y) for y in p(np.arange(len(train), len(train) + 30))]
        fig.add_trace(go.Scatter(x=future_dates, y=projected_vals, name='30-Day Projection',
                                 line=dict(color='#c0392b', dash='dot', width=3)))

        last_actual = daily['MA30'].iloc[-1] if not np.isnan(daily['MA30'].iloc[-1]) else daily['Value'].iloc[-1]
        projected_end = projected_vals[-1]

        if growth_target is not None:
            target_val = last_actual * (1 + (growth_target / 100))
            fig.add_trace(go.Scatter(x=[daily.index.max(), future_dates[-1]], y=[last_actual, target_val],
                                     name=f'Target (+{growth_target}%)',
                                     line=dict(color='green', dash='longdash', width=2)))

            gap = target_val - projected_end
            if gap > 0:
                insight_text = html.Div([html.Strong("🤖 Auto-Analyst Insight: "), html.Span(
                    f"Based on the current trajectory, you are projected to fall short of your +{growth_target}% goal. You need to average an additional "),
                                         html.Strong(f"Ksh {gap:,.0f} per day", style={'color': '#c0392b'}),
                                         html.Span(" over the next 30 days to hit the target.")])
            else:
                insight_text = html.Div([html.Strong("🤖 Auto-Analyst Insight: "), html.Span(
                    f"Great news. Based on the current trajectory, you are projected to "),
                                         html.Strong(f"beat your +{growth_target}% goal", style={'color': '#27ae60'}),
                                         html.Span(" effortlessly if current volume holds.")])

    fig.update_layout(title=f"Sales Trajectory vs Goals: {target}", hovermode='x unified',
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                      plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    if len(daily) >= 14 and seasonal_decompose:
        try:
            if decomp_model == 'multiplicative':
                safe_vals = daily['Value'].clip(lower=0) + 1
                title_suffix = "Multiplicative"
            else:
                safe_vals = daily['Value'].fillna(0)
                title_suffix = "Additive"

            decomposition = seasonal_decompose(safe_vals, model=decomp_model, period=7)

            fig_season = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                                       subplot_titles=("Trend", f"Seasonal ({title_suffix})", "Residuals"))
            fig_season.add_trace(
                go.Scatter(x=daily.index, y=decomposition.trend, name='Trend', line=dict(color='#2980b9')), row=1,
                col=1)
            fig_season.add_trace(
                go.Scatter(x=daily.index, y=decomposition.seasonal, name='Seasonality', line=dict(color='#27ae60')),
                row=2, col=1)
            fig_season.add_trace(go.Scatter(x=daily.index, y=decomposition.resid, name='Residuals', mode='markers',
                                            marker=dict(color='#7f8c8d', size=3)), row=3, col=1)
            fig_season.update_layout(title=f"Decomposition ({title_suffix}): {target}", showlegend=False,
                                     plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        except Exception as e:
            if decomp_model == 'multiplicative':
                try:
                    decomposition = seasonal_decompose(daily['Value'].fillna(0), model='additive', period=7)
                    fig_season = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.1,
                                               subplot_titles=("Trend", "Seasonal (Additive Fallback)", "Residuals"))
                    fig_season.add_trace(
                        go.Scatter(x=daily.index, y=decomposition.trend, name='Trend', line=dict(color='#2980b9')),
                        row=1, col=1)
                    fig_season.add_trace(go.Scatter(x=daily.index, y=decomposition.seasonal, name='Seasonality',
                                                    line=dict(color='#27ae60')), row=2, col=1)
                    fig_season.add_trace(
                        go.Scatter(x=daily.index, y=decomposition.resid, name='Residuals', mode='markers',
                                   marker=dict(color='#7f8c8d', size=3)), row=3, col=1)
                    fig_season.update_layout(title=f"Decomposition (Additive Fallback): {target}", showlegend=False,
                                             plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                except Exception as e2:
                    fig_season = go.Figure(layout={'title': f"Decomposition Failed: {str(e2)}"})
            else:
                fig_season = go.Figure(layout={'title': f"Decomposition Failed: {str(e)}"})
    else:
        fig_season = go.Figure(layout={'title': "Not enough data (Minimum 14 days required)"})

    return fig, insight_text, fig_season, fig, fig_season


# --- CALLBACKS: ELASTICITY ---
@callback(Output('elasticity-item-select', 'options'), [Input('elasticity-hier', 'value')],
          [State('auth-store', 'data')])
def update_el_opts(h_col, auth_data):
    df = get_secure_ea_df(auth_data)
    return [{'label': i, 'value': i} for i in sorted(df[h_col].dropna().unique())] + [
        {'label': 'Global', 'value': 'ALL'}] if h_col in df.columns else []


@callback(Output('elasticity-advanced-controls', 'style'), [Input('elasticity-advanced-toggle', 'value')])
def toggle_ea_adv(val):
    return {'display': 'block', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '8px',
            'marginBottom': '20px'} if 'show' in val else {'display': 'none'}


@callback(
    [Output('elasticity-chart', 'figure'), Output('elasticity-auto-insight', 'children'),
     Output('sim-vol-card', 'children'), Output('sim-rev-card', 'children'),
     Output('elasticity-examples-container', 'children'),
     Output('simulator-container', 'style'), Output('simulator-cards', 'style'),
     Output('snap-elasticity', 'data')],
    [Input('analysis-tabs', 'value'), Input('elasticity-item-select', 'value'), Input('elasticity-hier', 'value'),
     Input('year-select', 'value'), Input('price-simulator-slider', 'value'),
     Input('elasticity-options', 'value'), Input('elasticity-scale', 'value')],
    [State('auth-store', 'data')]
)
def update_elasticity_chart(tab, target, h_col, year, price_delta_pct, options, scale, auth_data):
    if tab != 'tab-elasticity': return [no_update] * 8
    df = get_secure_ea_df(auth_data)
    df_year = df[df['Date'].dt.year == int(year)]

    hide_sim = {'display': 'none'}
    show_sim_container = {'width': '300px', 'display': 'inline-block', 'verticalAlign': 'middle', 'marginLeft': '20px',
                          'borderLeft': '1px solid #ddd', 'paddingLeft': '20px'}
    show_sim_cards = {'display': 'flex', 'gap': '20px', 'marginBottom': '20px'}

    # GLOBAL 'ALL' VIEW
    if not target or target == 'ALL':
        data = df_year.groupby(h_col).agg({'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
        data = data[(data['QTY PCS'] > 0)];
        data['Unit_Price'] = data['Value'] / data['QTY PCS'];
        data = data[data['Unit_Price'] > 0.01]

        removed_count = 0
        if 'outlier' in options and len(data) > 10:
            x_clean, _, count = remove_outliers_iqr(data['QTY PCS'], data['Unit_Price'])
            data = data.loc[x_clean.index];
            removed_count = count

        is_log = (scale == 'log')
        fig = px.scatter(data, x='QTY PCS', y='Unit_Price', hover_name=h_col, log_x=is_log, log_y=is_log,
                         labels={'QTY PCS': 'Qty', 'Unit_Price': 'Price'}, title=f"Global Pricing Landscape ({h_col})")
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

        insight = html.Div([
            html.Strong("🤖 Auto-Analyst Insight: "),
            html.Span(
                "You are viewing the global price vs. volume landscape. To run the Interactive ROI Simulator or view specific demand curves, please select a specific product from the dropdown above.")
        ])

        # Process top examples for the bottom
        examples_div = html.Div()
        stats_list = []
        top_items = df_year[h_col].value_counts().head(50).index.tolist()
        for item in top_items:
            d_item = df_year[df_year[h_col] == item].groupby('Date').agg(
                {'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
            d_item = d_item[d_item['QTY PCS'] > 0];
            d_item['Unit_Price'] = d_item['Value'] / d_item['QTY PCS']
            if len(d_item) < 5: continue
            _, _, _, r2, m = calc_regression(d_item['QTY PCS'], d_item['Unit_Price'], mode='power')
            # THE FIX: Only valid demand curves (negative slope)
            if m is not None and m < 0:
                e_d = 1 / m
                if e_d >= -15: stats_list.append({'Item': item, 'Elasticity (E_d)': e_d, 'R²': r2})

        if stats_list:
            df_s = pd.DataFrame(stats_list)
            sorted_list = df_s.sort_values('Elasticity (E_d)', ascending=True).to_dict(
                'records')  # Ascending: -5, -4... (Most Elastic)

            if len(sorted_list) >= 2:
                elastic_item = sorted_list[0]['Item']
                inelastic_item = sorted_list[-1]['Item']

                def make_mini_chart(item_name, title, color):
                    di = df_year[df_year[h_col] == item_name].groupby('Date').agg(
                        {'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()
                    di = di[di['QTY PCS'] > 0];
                    di['Unit_Price'] = di['Value'] / di['QTY PCS']
                    f = px.scatter(di, x='QTY PCS', y='Unit_Price', log_x=is_log, log_y=is_log,
                                   title=f"{title}: {item_name}")
                    px_data, py_data, _, _, _ = calc_regression(di['QTY PCS'], di['Unit_Price'], mode='power')
                    if px_data is not None: f.add_trace(
                        go.Scatter(x=px_data, y=py_data, mode='lines', name="Demand Curve",
                                   line=dict(color=color, width=3)))
                    f.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False,
                                    margin=dict(l=0, r=0, t=40, b=0))
                    return html.Div([dcc.Graph(figure=f, style={'height': '350px'})],
                                    style={'flex': '1', 'backgroundColor': 'white', 'padding': '10px',
                                           'borderRadius': '8px', 'boxShadow': '0 2px 4px rgba(0,0,0,0.05)',
                                           'border': '1px solid #e0e0e0'})

                examples_div = html.Div([
                    html.H3("Real-World Examples from your Data",
                            style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '20px'}),
                    html.Div([
                        make_mini_chart(elastic_item, "Most Elastic (Flatter Curve)", "#c0392b"),
                        make_mini_chart(inelastic_item, "Least Elastic (Steeper Curve)", "#27ae60")
                    ], style={'display': 'flex', 'gap': '20px', 'width': '100%'})
                ])

        return fig, insight, html.Div(), html.Div(), examples_div, hide_sim, hide_sim, fig

    # SPECIFIC ITEM VIEW (Simulator Active)
    dff = df_year[df_year[h_col].astype(str).str.strip().str.upper() == str(target).strip().upper()]
    data = dff.groupby('Date').agg({'Value': 'sum', 'QTY PCS': 'sum'}).reset_index()

    data = data[(data['QTY PCS'] > 0)];
    data['Unit_Price'] = data['Value'] / data['QTY PCS'];
    data = data[data['Unit_Price'] > 0.01]

    removed_count = 0
    if 'outlier' in options and len(data) > 10:
        x_clean, _, count = remove_outliers_iqr(data['QTY PCS'], data['Unit_Price'])
        data = data.loc[x_clean.index];
        removed_count = count

    is_log = (scale == 'log')
    title = f"Demand Curve: {target} (Outliers Removed: {removed_count})" if removed_count else f"Demand Curve: {target}"
    fig = px.scatter(data, x='QTY PCS', y='Unit_Price', hover_name='Date', log_x=is_log, log_y=is_log,
                     labels={'QTY PCS': 'Qty', 'Unit_Price': 'Price'}, title=title)

    insight = "Not enough clean data to calculate elasticity."
    vol_card = html.H3("N/A", style={'color': '#7f8c8d'})
    rev_card = html.H3("N/A", style={'color': '#7f8c8d'})

    if 'linear' in options and len(data) > 2:
        lx, ly, leq, _, _ = calc_regression(data['QTY PCS'], data['Unit_Price'], mode='linear')
        if lx is not None: fig.add_trace(
            go.Scatter(x=lx, y=ly, mode='lines', name=f"Linear: {leq}", line=dict(color='green')))

    if len(data) > 2:
        px_data, py_data, peq, _, m = calc_regression(data['QTY PCS'], data['Unit_Price'], mode='power')
        if px_data is not None and m is not None:
            if 'power' in options:
                fig.add_trace(go.Scatter(x=px_data, y=py_data, mode='lines', name=f"Demand Curve",
                                         line=dict(color='red', width=3)))

            # --- PERFECTED ROI SIMULATOR MATH ---
            # m is the exponent 'b' in P = a * Q^b. Thus Elasticity (E_d) = 1/b.
            if m == 0:
                elasticity = 0
            else:
                elasticity = 1 / m
                # Cap extreme noise
                if elasticity > 15: elasticity = 15
                if elasticity < -15: elasticity = -15

            delta_p = price_delta_pct / 100.0
            delta_v = delta_p * elasticity

            baseline_p = data['Unit_Price'].median()
            baseline_v = data['QTY PCS'].median()
            baseline_rev = baseline_p * baseline_v

            new_p = baseline_p * (1 + delta_p)
            new_v = baseline_v * (1 + delta_v)
            new_rev = new_p * new_v

            rev_impact = new_rev - baseline_rev

            # Cards
            vol_color = '#27ae60' if delta_v >= 0 else '#c0392b'
            rev_color = '#27ae60' if rev_impact >= 0 else '#c0392b'

            vol_card = html.Div([
                html.H4("Predicted Volume Impact",
                        style={'color': '#7f8c8d', 'fontSize': '12px', 'textTransform': 'uppercase'}),
                html.H2(f"{'+' if delta_v > 0 else ''}{delta_v * 100:.1f}%",
                        style={'color': vol_color, 'margin': '10px 0'})
            ])

            rev_card = html.Div([
                html.H4("Predicted Revenue Impact (Per Transaction)",
                        style={'color': '#7f8c8d', 'fontSize': '12px', 'textTransform': 'uppercase'}),
                html.H2(f"{'+' if rev_impact > 0 else ''}Ksh {rev_impact:,.0f}",
                        style={'color': rev_color, 'margin': '10px 0'})
            ])

            # Intelligent Auto-Text
            if m > 0:
                e_text = html.Strong("⚠️ Spurious Data (Positive Correlation).", style={'color': '#f39c12'})
                strat = "Data shows higher prices occur alongside higher volumes. This usually indicates that external factors (e.g. seasonality, inflation, aggressive marketing) drove sales, overriding price sensitivity. Simulator results will mathematically follow this anomaly, but should not be used for strategy."
            elif abs(elasticity) > 1:
                e_text = html.Strong("Highly Elastic (Price Sensitive).", style={'color': '#c0392b'})
                strat = "Customers are very sensitive to price. Dropping the price slightly will disproportionately increase volume. Use targeted discounts to drive mass revenue."
            else:
                e_text = html.Strong("Inelastic (Price Insensitive).", style={'color': '#27ae60'})
                strat = "Customers will buy this regardless of small price changes. You can safely maintain or slightly raise prices to pad profit margins without losing much volume."

            insight = html.Div([
                html.Strong("🤖 Auto-Analyst Insight: "),
                html.Span(f"This product is "), e_text, html.Br(),
                html.Span(strat)
            ])

    fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    return fig, insight, vol_card, rev_card, html.Div(), show_sim_container, show_sim_cards, fig


@callback(
    Output("ea-dl", "data"), Input("ea-snap", "n_clicks"),
    [State('snap-rfm-bar', 'data'), State('snap-rfm-3d', 'data'), State('snap-basket-main', 'data'),
     State('snap-basket-comp', 'data'), State('snap-forecast', 'data'), State('snap-seasonality', 'data'),
     State('snap-elasticity', 'data')],
    prevent_initial_call=True
)
def download_ea_snapshot(n, *figs):
    html_content = "<html><head><title>Analysis Snapshot</title></head><body style='font-family: sans-serif; padding: 20px;'><h1 style='text-align:center;'>Experimental Analysis Report</h1>"
    for fig in figs:
        if fig: html_content += f"<div style='margin-bottom: 30px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); padding: 10px; background: white;'>{pio.to_html(go.Figure(fig), full_html=False, include_plotlyjs='cdn')}</div>"
    html_content += "</body></html>"
    return dict(content=html_content, filename=f"experimental_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")