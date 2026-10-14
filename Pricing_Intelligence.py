import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine

import dash
from dash import html, dcc, dash_table, Input, Output, State, ctx, callback, no_update
from dash.dash_table.Format import Format, Scheme, Group
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import plotly.io as pio

from data_loader import df_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security


# --- SECURITY HELPER ---
def get_secure_pi_df(auth_data):
    _df, _, _, _ = get_latest_data()
    return enforce_row_level_security(_df, auth_data)


# --- LAYOUT ---
layout = html.Div([
    dcc.Download(id="price-dl"),

    html.Div([
        html.H1("Pricing Intelligence & Audit", style=s.HEADER_TITLE),
        html.P("Analyze exact price points and detect unauthorized discounts or pricing anomalies.",
               style={'textAlign': 'center', 'color': '#7f8c8d', 'marginBottom': '20px'}),

        html.Div([
            html.Div([
                html.Span("HIERARCHY:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.RadioItems(id='price-hier',
                               options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                        {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                               style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'})
            ], style={'display': 'inline-block'}),

            html.Div([
                html.Span("TARGET:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.Dropdown(id='price-item-select', placeholder="Select Item/Group...",
                             style={'width': '250px', 'fontSize': '13px'})
            ], style={'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("SEGMENT:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='price-segment',
                           options=[{'label': 'All', 'value': 'All'}, {'label': 'B2B', 'value': 'B2B'},
                                    {'label': 'B2C', 'value': 'B2C'}], value='All', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("YEAR:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.Dropdown(id='price-year', options=[{'label': str(y), 'value': y} for y in years_global],
                         value=years_global[0] if years_global else None, clearable=False,
                         style={'width': '80px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("VIEW:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='price-view', options=['Week', 'Month', 'Quarter', 'Year'], value='Year', inline=True,
                           style={'fontSize': '14px', 'marginRight': '20px', 'display': 'inline-block'}),

            html.Button("📸 Snapshot", id="price-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'justifyContent': 'center',
                  'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '8px',
                  'border': f'1px solid {s.COLORS["border"]}'})
    ], style=s.SECTION),

    html.Div([
        html.H3("Exact Price Distribution", style={'textAlign': 'center', 'marginBottom': '10px'}),
        html.P("Click on any specific price point below to see exactly which customers purchased at that rate.",
               style={'textAlign': 'center', 'color': '#95a5a6', 'fontSize': '12px'}),
        dcc.Graph(id='price-histogram', style={'height': '55vh'})
    ], style=s.CHART_CONTAINER),

    html.Div(id='price-drilldown-container', style={**s.SECTION, 'display': 'none', 'marginTop': '20px'}, children=[
        html.H3(id='price-table-title', style={'textAlign': 'center', 'color': '#2c3e50', 'marginBottom': '20px'}),
        dash_table.DataTable(
            id='price-table',
            columns=[
                {'name': 'Customer', 'id': 'Customer Names'},
                {'name': 'Specific Item', 'id': 'Specific Item'},  # THE FIX: ADDED SPECIFIC ITEM
                {'name': 'Sales Rep(s)', 'id': 'Sales Rep'},
                {'name': 'Total Vol at this Price', 'id': 'Volume', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'Total Rev at this Price', 'id': 'Revenue', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
                {'name': 'Last Purchase Date', 'id': 'Last_Date'},
                {'name': 'Last Purchase Price', 'id': 'Last_Price', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
                {'name': 'Their Overall Avg Price', 'id': 'Overall_Avg', 'type': 'numeric',
                 'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)}
            ],
            style_table={'overflowX': 'auto', 'height': '400px', 'overflowY': 'auto'},
            style_header={'backgroundColor': s.COLORS['primary'], 'color': 'white', 'fontWeight': 'bold',
                          'textAlign': 'center'},
            style_cell={'textAlign': 'center', 'padding': '10px', 'fontFamily': 'Segoe UI', 'fontSize': '13px'},
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}],
            sort_action='native'
        )
    ]),

    html.Div(id='inconsistency-container', style={**s.SECTION, 'display': 'none', 'marginTop': '20px'}, children=[
        html.H3("🚨 Pricing Inconsistency Audit",
                style={'textAlign': 'center', 'color': s.COLORS.get('danger', '#e74c3c'), 'marginBottom': '5px'}),
        html.P(id='inconsistency-subtitle',
               style={'textAlign': 'center', 'color': '#7f8c8d', 'fontSize': '13px', 'marginBottom': '20px'}),
        html.Div(id='inconsistency-content')
    ])

], style=s.MAIN_CONTAINER)


# --- CALLBACKS ---

@callback(Output('price-item-select', 'options'), [Input('price-hier', 'value'), Input('price-segment', 'value')],
          [State('auth-store', 'data')])
def update_search_options(h_col, segment, auth_data):
    df = get_secure_pi_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    if h_col in df.columns: return [{'label': i, 'value': i} for i in sorted(df[h_col].dropna().unique())]
    return []


def get_exact_pricing_data(df_curr, h_col, target, vol_col):
    df_item = df_curr[df_curr[h_col].astype(str).str.strip().str.upper() == str(target).strip().upper()].copy()
    df_item = df_item[df_item[vol_col] > 0]

    if df_item.empty: return df_item

    df_item['Unit_Price'] = (df_item['Value'] / df_item[vol_col]).round(2)
    df_item['Price_Label'] = df_item['Unit_Price'].apply(lambda x: f"Ksh {x:,.2f}")

    return df_item


@callback(
    Output('price-histogram', 'figure'),
    [Input('price-item-select', 'value'), Input('price-hier', 'value'), Input('price-year', 'value'),
     Input('price-view', 'value'), Input('price-segment', 'value')],
    [State('auth-store', 'data')]
)
def update_histogram(target, h_col, year, view, segment, auth_data):
    if not target: return go.Figure().update_layout(title="Select an item above to load pricing distribution",
                                                    plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    df = get_secure_pi_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    df_curr, df_prev, lbl = core_engine.get_time_window(df, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_exact = get_exact_pricing_data(df_curr, h_col, target, vol_col)
    if df_exact.empty: return go.Figure().update_layout(title="No data found for this selection",
                                                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    hist_data = df_exact.groupby(['Price_Label', 'Unit_Price']).agg(
        Customers=('Customer Names', 'nunique'),
        Volume=(vol_col, 'sum'),
        Revenue=('Value', 'sum')
    ).reset_index().sort_values('Unit_Price', ascending=True)

    fig = px.bar(hist_data, x='Price_Label', y='Customers', title=f"Exact Price Distribution: {target} ({lbl})",
                 labels={'Price_Label': 'Exact Price Point', 'Customers': 'Unique Customers'}, text_auto=True,
                 custom_data=['Volume', 'Revenue'])

    fig.update_traces(marker_color='#3498db',
                      hovertemplate="<b>Price: %{x}</b><br>Customers: %{y}<br>Total Vol: %{customdata[0]:,.0f}<br>Total Rev: Ksh %{customdata[1]:,.0f}<extra></extra>")
    fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', xaxis_tickangle=-45,
                      xaxis_type='category')

    return fig


@callback(
    [Output('price-drilldown-container', 'style'), Output('price-table-title', 'children'),
     Output('price-table', 'data')],
    [Input('price-histogram', 'clickData'), Input('price-item-select', 'value'), Input('price-hier', 'value'),
     Input('price-year', 'value'), Input('price-view', 'value'), Input('price-segment', 'value')],
    [State('auth-store', 'data')]
)
def update_table(click_data, target, h_col, year, view, segment, auth_data):
    trigger = ctx.triggered_id
    if not click_data or trigger != 'price-histogram': return {'display': 'none'}, "", []

    clicked_label = click_data['points'][0]['x']

    df = get_secure_pi_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    df_curr, _, lbl = core_engine.get_time_window(df, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_exact = get_exact_pricing_data(df_curr, h_col, target, vol_col)
    df_clicked = df_exact[df_exact['Price_Label'] == clicked_label]

    if df_clicked.empty: return {'display': 'none'}, "", []

    # THE FIX: Always break down by the lowest SKU level to prevent blended averages
    item_col_base = 'Name' if 'Name' in df_curr.columns else h_col
    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Rep'] if c in df_curr.columns), None)

    # 1. Aggregate Specific Bucket Data by Customer AND Specific Item
    #    Volume / Revenue stay scoped to the clicked price point.
    table_raw = df_clicked.groupby(['Customer Names', item_col_base]).agg(
        Volume=(vol_col, 'sum'),
        Revenue=('Value', 'sum')
    ).reset_index()

    # 1b. Last actual purchase for this customer + item across ALL price points,
    #     so the row shows what they most recently paid (date + price), not just
    #     the clicked bar. Parse dates explicitly (dd/mm/yyyy) so 'latest' is a real
    #     chronological max rather than a lexicographic one.
    df_last = df_exact.copy()
    df_last['_dt'] = pd.to_datetime(df_last['Date'], errors='coerce', dayfirst=True)
    df_last = df_last.dropna(subset=['_dt']).sort_values('_dt')
    last_purchase = df_last.groupby(['Customer Names', item_col_base]).agg(
        Last_Date=('_dt', 'max'),
        Last_Price=('Unit_Price', 'last')  # price on the most recent date (frame is date-sorted)
    ).reset_index()
    table_raw = pd.merge(table_raw, last_purchase, on=['Customer Names', item_col_base], how='left')

    if rep_col:
        reps = df_clicked.groupby(['Customer Names', item_col_base])[rep_col].unique().apply(
            lambda x: ", ".join(x)).reset_index()
        table_raw = pd.merge(table_raw, reps, on=['Customer Names', item_col_base], how='left')
    else:
        table_raw['Sales Rep'] = "N/A"

    table_raw['Last_Date'] = pd.to_datetime(table_raw['Last_Date']).dt.strftime('%d-%b-%Y')

    # 2. Calculate Context Average for that specific Customer AND Specific Item
    context_agg = df_exact.groupby(['Customer Names', item_col_base]).agg(
        All_Rev=('Value', 'sum'),
        All_Vol=(vol_col, 'sum')
    ).reset_index()
    context_agg['Overall_Avg'] = (context_agg['All_Rev'] / context_agg['All_Vol']).replace([np.inf, -np.inf], np.nan).fillna(0)

    # Merge Context into the Table
    final_table = pd.merge(table_raw, context_agg[['Customer Names', item_col_base, 'Overall_Avg']],
                           on=['Customer Names', item_col_base], how='left')
    final_table.rename(columns={item_col_base: 'Specific Item'}, inplace=True)
    final_table = final_table.sort_values('Volume', ascending=False)

    title = f"Audit Report: Customers buying {target} exactly at {clicked_label} ({lbl})"
    return {**s.SECTION, 'display': 'block', 'marginTop': '20px'}, title, final_table.to_dict('records')


# --- CALLBACK: INCONSISTENCY AUDIT ---
@callback(
    [Output('inconsistency-container', 'style'), Output('inconsistency-subtitle', 'children'),
     Output('inconsistency-content', 'children')],
    [Input('price-item-select', 'value'), Input('price-hier', 'value'), Input('price-year', 'value'),
     Input('price-view', 'value'), Input('price-segment', 'value')],
    [State('auth-store', 'data')]
)
def update_inconsistency_audit(target, h_col, year, view, segment, auth_data):
    if not target: return {'display': 'none'}, "", html.Div()

    df = get_secure_pi_df(auth_data)
    if segment != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == segment.upper()]

    df_curr, _, lbl = core_engine.get_time_window(df, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_item = get_exact_pricing_data(df_curr, h_col, target, vol_col)
    if df_item.empty: return {'display': 'none'}, "", html.Div()

    # THE FIX: Ensure we only flag inconsistencies on the exact same SKU, not just the group
    item_col_base = 'Name' if 'Name' in df_item.columns else h_col

    # Find combinations of Customer + Specific Item with multiple price points
    price_counts = df_item.groupby(['Customer Names', item_col_base])['Unit_Price'].nunique().reset_index()
    inconsistent_combos = price_counts[price_counts['Unit_Price'] > 1]

    if inconsistent_combos.empty:
        success_msg = html.Div([
            html.H4("✅ 100% Pricing Consistency", style={'color': '#27ae60', 'textAlign': 'center'}),
            html.P(
                f"No customers were charged multiple different prices for the same specific item in {target} during {lbl}.",
                style={'textAlign': 'center'})
        ], style={'padding': '20px', 'backgroundColor': '#e8f8f5', 'borderRadius': '8px',
                  'border': '1px solid #27ae60'})
        return {**s.SECTION, 'display': 'block',
                'marginTop': '20px'}, f"Scanning {lbl} for internal price variances...", success_msg

    # Filter df_item to only include the specific rule-breaking Customer + Item combinations
    df_inconsistent = pd.merge(df_item, inconsistent_combos[['Customer Names', item_col_base]],
                               on=['Customer Names', item_col_base], how='inner')
    rep_col = next((c for c in ['Sales Rep', 'Salesman', 'Rep'] if c in df_item.columns), None)

    audit_raw = df_inconsistent.groupby(['Customer Names', item_col_base, 'Unit_Price']).agg(
        Volume=(vol_col, 'sum'),
        Revenue=('Value', 'sum'),
        Last_Date=('Date', 'max')
    ).reset_index()

    if rep_col:
        reps = df_inconsistent.groupby(['Customer Names', item_col_base, 'Unit_Price'])[rep_col].unique().apply(
            lambda x: ", ".join(x)).reset_index()
        audit_raw = pd.merge(audit_raw, reps, on=['Customer Names', item_col_base, 'Unit_Price'], how='left')
    else:
        audit_raw['Sales Rep'] = "N/A"

    audit_raw['Last_Date'] = pd.to_datetime(audit_raw['Last_Date']).dt.strftime('%d-%b-%Y')
    audit_raw.rename(columns={item_col_base: 'Specific Item', 'Sales Rep': 'Sales Rep(s)'}, inplace=True)

    # Sort so the same customer's items appear together
    audit_raw = audit_raw.sort_values(['Customer Names', 'Specific Item', 'Volume'], ascending=[True, True, False])

    table = dash_table.DataTable(
        data=audit_raw.to_dict('records'),
        columns=[
            {'name': 'Customer', 'id': 'Customer Names'},
            {'name': 'Specific Item', 'id': 'Specific Item'},
            {'name': 'Exact Price Point (Ksh)', 'id': 'Unit_Price', 'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=2, group=Group.yes)},
            {'name': 'Volume at this Price', 'id': 'Volume', 'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
            {'name': 'Revenue Generated', 'id': 'Revenue', 'type': 'numeric',
             'format': Format(scheme=Scheme.fixed, precision=0, group=Group.yes)},
            {'name': 'Last Date at Price', 'id': 'Last_Date'},
            {'name': 'Sales Rep(s)', 'id': 'Sales Rep(s)'}
        ],
        style_table={'overflowX': 'auto', 'maxHeight': '500px', 'overflowY': 'auto'},
        style_header={'backgroundColor': s.COLORS.get('danger', '#e74c3c'), 'color': 'white', 'fontWeight': 'bold',
                      'textAlign': 'center'},
        style_cell={'textAlign': 'center', 'padding': '10px', 'fontFamily': 'Segoe UI', 'fontSize': '13px'},
        style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}],
        sort_action='native'
    )

    unique_custs_flagged = audit_raw['Customer Names'].nunique()
    subtitle = f"Found {unique_custs_flagged} customers who were charged multiple different prices for the exact same item during {lbl}."
    return {**s.SECTION, 'display': 'block', 'marginTop': '20px'}, subtitle, table


@callback(
    Output("price-dl", "data"), Input("price-snap", "n_clicks"),
    [State('price-histogram', 'figure')],
    prevent_initial_call=True
)
def download_snapshot(n, fig):
    html_content = f"""
    <html>
    <head>
        <title>Pricing Intelligence Snapshot</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; padding: 20px; background-color: #f4f4f4; }}
            .chart-container {{ background: white; margin-bottom: 20px; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ text-align: center; color: #2c3e50; }}
            .timestamp {{ text-align: center; color: #7f8c8d; font-size: 0.9em; margin-bottom: 30px; }}
        </style>
    </head>
    <body>
        <h1>Pricing Intelligence Report</h1>
        <p class="timestamp">Generated on {pd.Timestamp.now().strftime('%d-%b-%Y %H:%M')}</p>
        <div class="chart-container">{pio.to_html(go.Figure(fig), full_html=False, include_plotlyjs="cdn")}</div>
    </body>
    </html>
    """
    return dict(content=html_content, filename=f"pricing_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")