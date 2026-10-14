import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import core_engine

import dash
from dash import html, dcc, Input, Output, State, ctx, no_update, callback
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np

from data_loader import df_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security


def get_secure_si_df(auth_data):
    _df, _, _, _ = get_latest_data()
    df = enforce_row_level_security(_df, auth_data)
    if not df.empty:
        if 'Sales Rep' not in df.columns:
            df['Sales Rep'] = df['Sales Rep Name'] if 'Sales Rep Name' in df.columns else 'Unknown'
        df['Sales Rep'] = df['Sales Rep'].astype(str).replace('nan', 'Unknown').str.strip()
    return df


SECTION_STYLE = {
    'minHeight': '100vh', 'width': '100%', 'scrollSnapAlign': 'start',
    'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center',
    'padding': '40px', 'boxSizing': 'border-box', 'borderBottom': '1px solid #eee'
}

SHOW_TAB = {'display': 'block', **SECTION_STYLE}
HIDE_TAB = {'display': 'none'}


def apply_pareto(df, group_col, value_col, top_n=10):
    totals = df.groupby(group_col)[value_col].sum().sort_values(ascending=False)
    top_items = totals.head(top_n).index.tolist()
    df['Pareto_Group'] = df[group_col].apply(lambda x: x if x in top_items else 'Others')
    return df


def color_others_grey(fig):
    fig.for_each_trace(lambda t: t.update(marker_color='#d3d3d3') if t.name == 'Others' else None)
    return fig


# THE FIX: Upgraded to use core_engine to prevent Month Bleed bugs
def get_split_movers_data(df_full, target_year, view_mode, group_col):
    df_curr, df_prev, period_label = core_engine.get_time_window(df_full, target_year, view_mode)
    if df_curr.empty and df_prev.empty: return pd.DataFrame(), pd.DataFrame(), "No Data"

    curr_data = df_curr.groupby(group_col)['Value'].sum()
    prev_data = df_prev.groupby(group_col)['Value'].sum()
    diff = curr_data.sub(prev_data, fill_value=0)

    top_g = diff[diff > 0].sort_values(ascending=False).head(10)
    top_l = diff[diff < 0].sort_values(ascending=True).head(10)

    def format_stack(series, is_growth):
        res = []
        for item, change in series.items():
            p = prev_data.get(item, 0);
            c = curr_data.get(item, 0);
            abs_change = abs(change)
            if is_growth:
                res.append({group_col: item, 'Type': 'Base', 'Value': p, 'SortVal': change, 'Tip_Base': p,
                            'Tip_Delta': abs_change, 'Tip_Total': c})
                res.append({group_col: item, 'Type': 'Growth', 'Value': abs_change, 'SortVal': change, 'Tip_Base': p,
                            'Tip_Delta': abs_change, 'Tip_Total': c})
            else:
                res.append({group_col: item, 'Type': 'Retained', 'Value': c, 'SortVal': change, 'Tip_Base': c,
                            'Tip_Delta': abs_change, 'Tip_Total': p})
                res.append({group_col: item, 'Type': 'Decline', 'Value': abs_change, 'SortVal': change, 'Tip_Base': c,
                            'Tip_Delta': abs_change, 'Tip_Total': p})
        return pd.DataFrame(res)

    df_gainers = format_stack(top_g, True)
    df_decliners = format_stack(top_l, False)
    if not df_gainers.empty: df_gainers = df_gainers.sort_values('SortVal', ascending=True)
    if not df_decliners.empty: df_decliners = df_decliners.sort_values('SortVal', ascending=False)

    return df_gainers, df_decliners, period_label


layout = html.Div([
    dcc.Download(id="stacked-intelligence-download-snapshot"),
    html.Div([
        html.H1("Composition & Stacked Analysis", style={'textAlign': 'center', 'color': '#2c3e50'}),
        html.Div([
            html.Label("Hierarchy:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='si-hierarchy-select',
                           options=[{'label': 'Group', 'value': 'Items 2'}, {'label': 'Sub-Group', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items 2', inline=True,
                           style={'marginRight': '20px'}),
            html.Label("Segment:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='si-segment-filter', options=['All', 'B2B', 'B2C'], value='All', inline=True,
                           style={'marginRight': '20px'}),
            html.Label("Year:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.Dropdown(id='si-year-select', options=[{'label': y, 'value': y} for y in years_global],
                         value=years_global[0] if years_global else None, clearable=False,
                         style={'width': '80px', 'marginRight': '15px'}),
            html.Label("Compare:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='si-time-view',
                           options=[{'label': 'Week', 'value': 'Week'}, {'label': 'Month', 'value': 'Month'},
                                    {'label': 'Quarter', 'value': 'Quarter'}, {'label': 'Year', 'value': 'Year'}], value='Month', inline=True,
                           style={'marginRight': '15px'}),
            html.Label("Top:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='si-top-n-selector', options=[{'label': '5', 'value': 5}, {'label': '10', 'value': 10},
                                                            {'label': '20', 'value': 20}], value=10, inline=True,
                           style={'marginRight': '15px'}),
            html.Label("Stack:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='si-stack-n-selector', options=[{'label': '3', 'value': 3}, {'label': '5', 'value': 5},
                                                              {'label': '10', 'value': 10}], value=5, inline=True,
                           style={'marginRight': '15px'}),
            html.Button("📸 Snapshot", id="stacked-intelligence-btn-snapshot",
                        style={'marginLeft': '20px', 'backgroundColor': '#2c3e50', 'color': 'white'})
        ], style={'display': 'flex', 'justifyContent': 'center', 'alignItems': 'center', 'padding': '20px',
                  'backgroundColor': '#f8f9fa', 'flexWrap': 'wrap'})
    ]),
    dcc.Tabs(id='si-stack-tabs', value='tab-exec', children=[
        dcc.Tab(label='Executive (Trends)', value='tab-exec'), dcc.Tab(label='Customer Mix', value='tab-cust'),
        dcc.Tab(label='Product Concentration', value='tab-prod'), dcc.Tab(label='Sales Rep Portfolio', value='tab-rep'),
    ], style={'fontWeight': 'bold', 'fontSize': '18px'}),

    html.Div(id='content-exec', style=SHOW_TAB, children=[
        html.H2("Revenue Trend Stacked by Hierarchy", style={'textAlign': 'center', 'color': '#2980b9'}),
        dcc.Graph(id='exec-stack-chart', style={'height': '70vh'}),
        html.Div([
            html.Div([html.H3("🚀 Top Hierarchy Growers", style={'textAlign': 'center', 'color': '#27ae60'}),
                      dcc.Graph(id='exec-grower-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block'}),
            html.Div([html.H3("⚠️ Top Hierarchy Decliners", style={'textAlign': 'center', 'color': '#c0392b'}),
                      dcc.Graph(id='exec-decline-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block', 'float': 'right'})
        ])
    ]),

    html.Div(id='content-cust', style=HIDE_TAB, children=[
        html.H2("Top Customers: Product Mix", style={'textAlign': 'center', 'color': '#27ae60'}),
        dcc.Graph(id='cust-stack-chart', style={'height': '70vh'}),
        html.Div([
            html.Div([html.H3("🚀 Top Customer Growers", style={'textAlign': 'center', 'color': '#27ae60'}),
                      dcc.Graph(id='cust-grower-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block'}),
            html.Div([html.H3("⚠️ Top Customer Decliners", style={'textAlign': 'center', 'color': '#c0392b'}),
                      dcc.Graph(id='cust-decline-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block', 'float': 'right'})
        ])
    ]),

    html.Div(id='content-prod', style=HIDE_TAB, children=[
        html.H2("Top Products: Customer Concentration", style={'textAlign': 'center', 'color': '#8e44ad'}),
        dcc.Graph(id='prod-stack-chart', style={'height': '70vh'}),
        html.Div([
            html.Div([html.H3("🚀 Top Product Growers", style={'textAlign': 'center', 'color': '#27ae60'}),
                      dcc.Graph(id='prod-grower-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block'}),
            html.Div([html.H3("⚠️ Top Product Decliners", style={'textAlign': 'center', 'color': '#c0392b'}),
                      dcc.Graph(id='prod-decline-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block', 'float': 'right'})
        ])
    ]),

    html.Div(id='content-rep', style=HIDE_TAB, children=[
        html.H2("Top Sales Reps: Customer Portfolio", style={'textAlign': 'center', 'color': '#d35400'}),
        dcc.Graph(id='rep-stack-chart', style={'height': '70vh'}),
        html.Div([
            html.Div([html.H3("🚀 Top Rep Growers", style={'textAlign': 'center', 'color': '#27ae60'}),
                      dcc.Graph(id='rep-grower-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block'}),
            html.Div([html.H3("⚠️ Top Rep Decliners", style={'textAlign': 'center', 'color': '#c0392b'}),
                      dcc.Graph(id='rep-decline-chart', style={'height': '65vh'})],
                     style={'width': '49%', 'display': 'inline-block', 'float': 'right'})
        ])
    ]),

    html.Div(id='drilldown-container',
             style={'display': 'none', 'padding': '40px', 'backgroundColor': '#f1f1f1', 'borderTop': '2px solid #ccc'},
             children=[
                 html.H2(id='drilldown-title', style={'textAlign': 'center', 'color': '#2c3e50'}),
                 html.Button("X Close Drilldown", id='btn-close-drill',
                             style={'display': 'block', 'margin': '0 auto 20px auto', 'cursor': 'pointer',
                                    'padding': '8px 16px', 'backgroundColor': '#e74c3c', 'color': 'white',
                                    'border': 'none', 'borderRadius': '4px'}),
                 dcc.Graph(id='drilldown-chart', style={'height': '60vh'})
             ])
])


@callback([Output('content-exec', 'style'), Output('content-cust', 'style'), Output('content-prod', 'style'),
           Output('content-rep', 'style')], [Input('si-stack-tabs', 'value')])
def render_content(tab):
    res = [HIDE_TAB] * 4
    if tab == 'tab-exec':
        res[0] = SHOW_TAB
    elif tab == 'tab-cust':
        res[1] = SHOW_TAB
    elif tab == 'tab-prod':
        res[2] = SHOW_TAB
    elif tab == 'tab-rep':
        res[3] = SHOW_TAB
    return res


@callback(
    [Output('exec-stack-chart', 'figure'), Output('exec-grower-chart', 'figure'),
     Output('exec-decline-chart', 'figure')],
    [Input('si-stack-tabs', 'value'), Input('si-year-select', 'value'), Input('si-time-view', 'value'),
     Input('si-stack-n-selector', 'value'), Input('si-hierarchy-select', 'value'), Input('si-segment-filter', 'value')],
    [State('auth-store', 'data')]
)
def update_exec_tab(tab, year, view, stack_n, h_col, segment, auth_data):
    if tab != 'tab-exec': return [no_update] * 3
    df_sec = get_secure_si_df(auth_data)
    empty = go.Figure(layout={'title': "No Data", 'plot_bgcolor': 'rgba(0,0,0,0)', 'paper_bgcolor': 'rgba(0,0,0,0)'})
    if df_sec.empty or not year: return [empty] * 3

    if segment != 'All' and 'B2B' in df_sec.columns:
        df_filtered = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    else:
        df_filtered = df_sec

    df_curr, df_prev, lbl = core_engine.get_time_window(df_filtered, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    # The Stacked Trend ALWAYS requests YTD context from the engine
    df_ytd, _, _ = core_engine.get_time_window(df_filtered, year, 'Year')
    df_exec = apply_pareto(df_ytd.copy(), h_col, 'Value', top_n=int(stack_n))

    exec_data, xfmt, dtick = core_engine.get_trend_data(df_exec, view, vol_col)

    # We must manually re-group the pareto category because get_trend_data groups everything into one date
    if view == 'Week':
        df_exec['Period'] = df_exec['Date'] - pd.to_timedelta(df_exec['Date'].dt.dayofweek, unit='D')
        df_exec['Period'] = df_exec['Period'].dt.strftime('%Y-%m-%d')
    elif view == 'Month':
        df_exec['Period'] = df_exec['Date'].dt.to_period('M').dt.to_timestamp().dt.strftime('%Y-%m-%d')
    elif view == 'Quarter':
        df_exec['Period'] = 'Q' + df_exec['Date'].dt.quarter.astype(str) + ' ' + df_exec['Date'].dt.year.astype(str)
    else:
        df_exec['Period'] = df_exec['Date'].dt.to_period('Y').dt.to_timestamp().dt.strftime('%Y-%m-%d')

    exec_data = df_exec.groupby(['Period', 'Pareto_Group'])[['Value', vol_col, 'Quantity']].sum().reset_index()
    exec_data['Avg_Price'] = (exec_data['Value'] / exec_data['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)

    stats_header = s.get_chart_title(f"Revenue Trend YTD", df_ytd, df_prev, METRIC_MODE)
    fig_exec = px.bar(exec_data, x='Period', y='Value', color='Pareto_Group', title=stats_header, text_auto='.2s',
                      custom_data=[vol_col, 'Avg_Price'])
    fig_exec.update_traces(
        hovertemplate="<b>%{data.name}</b><br>Rev: %{y:,.0f}<br>Vol: %{customdata[0]:,.0f}<br>Avg: %{customdata[1]:,.2f}<extra></extra>")
    fig_exec.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
    if not exec_data.empty: fig_exec.update_xaxes(dtick=dtick, tickformat=xfmt)
    fig_exec = color_others_grey(fig_exec)

    eg_df, ed_df, e_per = get_split_movers_data(df_filtered, year, view, h_col)
    fig_eg = px.bar(eg_df, y=h_col, x='Value', color='Type', orientation='h', title=f"Top {h_col} Growth ({e_per})",
                    text_auto='.2s', color_discrete_map={'Base': '#bdc3c7', 'Growth': '#2ecc71'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not eg_df.empty else empty
    if not eg_df.empty: fig_eg.update_traces(
        hovertemplate="<b>%{y}</b><br>Base: %{customdata[0]:,.0f}<br>Growth: %{customdata[1]:,.0f}<br>Total: %{customdata[2]:,.0f}<extra></extra>"); fig_eg.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    fig_ed = px.bar(ed_df, y=h_col, x='Value', color='Type', orientation='h', title=f"Top {h_col} Decline ({e_per})",
                    text_auto='.2s', color_discrete_map={'Retained': '#bdc3c7', 'Decline': '#c0392b'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not ed_df.empty else empty
    if not ed_df.empty: fig_ed.update_traces(
        hovertemplate="<b>%{y}</b><br>Retained: %{customdata[0]:,.0f}<br>Lost: %{customdata[1]:,.0f}<br>Prev Total: %{customdata[2]:,.0f}<extra></extra>"); fig_ed.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    return fig_exec, fig_eg, fig_ed


@callback(
    [Output('cust-stack-chart', 'figure'), Output('cust-grower-chart', 'figure'),
     Output('cust-decline-chart', 'figure')],
    [Input('si-stack-tabs', 'value'), Input('si-year-select', 'value'), Input('si-time-view', 'value'),
     Input('si-top-n-selector', 'value'), Input('si-stack-n-selector', 'value'), Input('si-hierarchy-select', 'value'),
     Input('si-segment-filter', 'value')],
    [State('auth-store', 'data')]
)
def update_cust_tab(tab, year, view, top_n, stack_n, h_col, segment, auth_data):
    if tab != 'tab-cust': return [no_update] * 3
    df_sec = get_secure_si_df(auth_data)
    empty = go.Figure(layout={'title': "No Data", 'plot_bgcolor': 'rgba(0,0,0,0)', 'paper_bgcolor': 'rgba(0,0,0,0)'})
    if df_sec.empty or not year: return [empty] * 3

    if segment != 'All' and 'B2B' in df_sec.columns:
        df_filtered = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    else:
        df_filtered = df_sec

    # ENGINE
    df_curr, df_prev, lbl = core_engine.get_time_window(df_filtered, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df_sec.columns), None)
    if not cust_col: return [empty] * 3

    top_cust = df_curr.groupby(cust_col)['Value'].sum().nlargest(int(top_n)).index.tolist()
    cust_plot = [apply_pareto(df_curr[df_curr[cust_col] == c].copy(), h_col, 'Value', top_n=int(stack_n)).groupby(
        [cust_col, 'Pareto_Group'])[['Value', vol_col, 'Quantity']].sum().reset_index() for c in top_cust]
    cust_final = pd.concat(cust_plot) if cust_plot else pd.DataFrame()

    if not cust_final.empty:
        cust_final['Avg_Price'] = (cust_final['Value'] / cust_final['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)
        fig_cust = px.bar(cust_final, y=cust_col, x='Value', color='Pareto_Group', orientation='h',
                          title=s.get_chart_title(f"Top {top_n} Customers: {h_col} Mix ({lbl})", df_curr, df_prev,
                                                  METRIC_MODE),
                          text_auto='.2s', category_orders={cust_col: top_cust}, custom_data=[vol_col, 'Avg_Price'])
        fig_cust.update_traces(
            hovertemplate="<b>%{data.name}</b><br>Val: %{x:,.0f}<br>Vol: %{customdata[0]:,.0f}<br>Avg: %{customdata[1]:,.2f}<extra></extra>")
        fig_cust.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                               yaxis={'categoryorder': 'total ascending'})
        fig_cust = color_others_grey(fig_cust)
    else:
        fig_cust = empty

    cg_df, cd_df, c_per = get_split_movers_data(df_filtered, year, view, cust_col)
    fig_cg = px.bar(cg_df, y=cust_col, x='Value', color='Type', orientation='h', title=f"Top Customer Growth ({c_per})",
                    text_auto='.2s', color_discrete_map={'Base': '#bdc3c7', 'Growth': '#2ecc71'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not cg_df.empty else empty
    if not cg_df.empty: fig_cg.update_traces(
        hovertemplate="<b>%{y}</b><br>Base: %{customdata[0]:,.0f}<br>Growth: %{customdata[1]:,.0f}<br>Total: %{customdata[2]:,.0f}<extra></extra>"); fig_cg.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    fig_cd = px.bar(cd_df, y=cust_col, x='Value', color='Type', orientation='h',
                    title=f"Top Customer Decline ({c_per})", text_auto='.2s',
                    color_discrete_map={'Retained': '#bdc3c7', 'Decline': '#c0392b'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not cd_df.empty else empty
    if not cd_df.empty: fig_cd.update_traces(
        hovertemplate="<b>%{y}</b><br>Retained: %{customdata[0]:,.0f}<br>Lost: %{customdata[1]:,.0f}<br>Prev Total: %{customdata[2]:,.0f}<extra></extra>"); fig_cd.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    return fig_cust, fig_cg, fig_cd


@callback(
    [Output('prod-stack-chart', 'figure'), Output('prod-grower-chart', 'figure'),
     Output('prod-decline-chart', 'figure')],
    [Input('si-stack-tabs', 'value'), Input('si-year-select', 'value'), Input('si-time-view', 'value'),
     Input('si-top-n-selector', 'value'), Input('si-stack-n-selector', 'value'), Input('si-hierarchy-select', 'value'),
     Input('si-segment-filter', 'value')],
    [State('auth-store', 'data')]
)
def update_prod_tab(tab, year, view, top_n, stack_n, h_col, segment, auth_data):
    if tab != 'tab-prod': return [no_update] * 3
    df_sec = get_secure_si_df(auth_data)
    empty = go.Figure(layout={'title': "No Data", 'plot_bgcolor': 'rgba(0,0,0,0)', 'paper_bgcolor': 'rgba(0,0,0,0)'})
    if df_sec.empty or not year: return [empty] * 3

    if segment != 'All' and 'B2B' in df_sec.columns:
        df_filtered = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    else:
        df_filtered = df_sec

    # ENGINE
    df_curr, df_prev, lbl = core_engine.get_time_window(df_filtered, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df_sec.columns), None)

    top_items = df_curr.groupby(h_col)['Value'].sum().nlargest(int(top_n)).index.tolist()
    prod_plot = [apply_pareto(df_curr[df_curr[h_col] == i].copy(), cust_col, 'Value', top_n=int(stack_n)).groupby(
        [h_col, 'Pareto_Group'])[['Value', vol_col, 'Quantity']].sum().reset_index() for i in top_items if cust_col]
    prod_final = pd.concat(prod_plot) if prod_plot else pd.DataFrame()

    if not prod_final.empty:
        prod_final['Avg_Price'] = (prod_final['Value'] / prod_final['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)
        fig_prod = px.bar(prod_final, y=h_col, x='Value', color='Pareto_Group', orientation='h',
                          title=s.get_chart_title(f"Top {top_n} {h_col}s: Customer Concentration ({lbl})", df_curr,
                                                  df_prev, METRIC_MODE), text_auto='.2s',
                          category_orders={h_col: top_items},
                          custom_data=[vol_col, 'Avg_Price'])
        fig_prod.update_traces(
            hovertemplate="<b>%{data.name}</b><br>Val: %{x:,.0f}<br>Vol: %{customdata[0]:,.0f}<br>Avg: %{customdata[1]:,.2f}<extra></extra>")
        fig_prod.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False,
                               yaxis={'categoryorder': 'total ascending'})
        fig_prod = color_others_grey(fig_prod)
    else:
        fig_prod = empty

    pg_df, pd_df, p_per = get_split_movers_data(df_filtered, year, view, h_col)
    fig_pg = px.bar(pg_df, y=h_col, x='Value', color='Type', orientation='h', title=f"Top {h_col} Growth ({p_per})",
                    text_auto='.2s', color_discrete_map={'Base': '#bdc3c7', 'Growth': '#2ecc71'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not pg_df.empty else empty
    if not pg_df.empty: fig_pg.update_traces(
        hovertemplate="<b>%{y}</b><br>Base: %{customdata[0]:,.0f}<br>Growth: %{customdata[1]:,.0f}<br>Total: %{customdata[2]:,.0f}<extra></extra>"); fig_pg.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    fig_pd = px.bar(pd_df, y=h_col, x='Value', color='Type', orientation='h', title=f"Top {h_col} Decline ({p_per})",
                    text_auto='.2s', color_discrete_map={'Retained': '#bdc3c7', 'Decline': '#c0392b'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not pd_df.empty else empty
    if not pd_df.empty: fig_pd.update_traces(
        hovertemplate="<b>%{y}</b><br>Retained: %{customdata[0]:,.0f}<br>Lost: %{customdata[1]:,.0f}<br>Prev Total: %{customdata[2]:,.0f}<extra></extra>"); fig_pd.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    return fig_prod, fig_pg, fig_pd


@callback(
    [Output('rep-stack-chart', 'figure'), Output('rep-grower-chart', 'figure'), Output('rep-decline-chart', 'figure')],
    [Input('si-stack-tabs', 'value'), Input('si-year-select', 'value'), Input('si-time-view', 'value'),
     Input('si-top-n-selector', 'value'), Input('si-stack-n-selector', 'value'), Input('si-hierarchy-select', 'value'),
     Input('si-segment-filter', 'value')],
    [State('auth-store', 'data')]
)
def update_rep_tab(tab, year, view, top_n, stack_n, h_col, segment, auth_data):
    if tab != 'tab-rep': return [no_update] * 3
    df_sec = get_secure_si_df(auth_data)
    empty = go.Figure(layout={'title': "No Data", 'plot_bgcolor': 'rgba(0,0,0,0)', 'paper_bgcolor': 'rgba(0,0,0,0)'})
    if df_sec.empty or not year: return [empty] * 3

    if segment != 'All' and 'B2B' in df_sec.columns:
        df_filtered = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    else:
        df_filtered = df_sec

    # ENGINE
    df_curr, df_prev, lbl = core_engine.get_time_window(df_filtered, year, view)
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df_sec.columns), None)

    rep_col = 'Sales Rep'
    top_reps = df_curr.groupby(rep_col)['Value'].sum().nlargest(int(top_n)).index.tolist()
    rep_plot = [apply_pareto(df_curr[df_curr[rep_col] == r].copy(), cust_col, 'Value', top_n=int(stack_n)).groupby(
        [rep_col, 'Pareto_Group'])[['Value', vol_col, 'Quantity']].sum().reset_index() for r in top_reps if cust_col]
    rep_final = pd.concat(rep_plot) if rep_plot else pd.DataFrame()

    if not rep_final.empty:
        rep_final['Avg_Price'] = (rep_final['Value'] / rep_final['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)
        fig_rep = px.bar(rep_final, y=rep_col, x='Value', color='Pareto_Group', orientation='h',
                         title=s.get_chart_title(f"Top {top_n} Reps: Portfolio ({lbl})", df_curr, df_prev, METRIC_MODE),
                         text_auto='.2s', category_orders={rep_col: top_reps}, custom_data=[vol_col, 'Avg_Price'])
        fig_rep.update_traces(
            hovertemplate="<b>%{data.name}</b><br>Val: %{x:,.0f}<br>Vol: %{customdata[0]:,.0f}<br>Avg: %{customdata[1]:,.2f}<extra></extra>")
        fig_rep.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', showlegend=False,
                              yaxis={'categoryorder': 'total ascending'})
        fig_rep = color_others_grey(fig_rep)
    else:
        fig_rep = empty

    rg_df, rd_df, r_per = get_split_movers_data(df_filtered, year, view, rep_col)
    fig_rg = px.bar(rg_df, y=rep_col, x='Value', color='Type', orientation='h', title=f"Top Rep Growth ({r_per})",
                    text_auto='.2s', color_discrete_map={'Base': '#bdc3c7', 'Growth': '#2ecc71'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not rg_df.empty else empty
    if not rg_df.empty: fig_rg.update_traces(
        hovertemplate="<b>%{y}</b><br>Base: %{customdata[0]:,.0f}<br>Growth: %{customdata[1]:,.0f}<br>Total: %{customdata[2]:,.0f}<extra></extra>"); fig_rg.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    fig_rd = px.bar(rd_df, y=rep_col, x='Value', color='Type', orientation='h', title=f"Top Rep Decline ({r_per})",
                    text_auto='.2s', color_discrete_map={'Retained': '#bdc3c7', 'Decline': '#c0392b'},
                    custom_data=['Tip_Base', 'Tip_Delta', 'Tip_Total']).update_layout(plot_bgcolor='rgba(0,0,0,0)',
                                                                                      paper_bgcolor='rgba(0,0,0,0)') if not rd_df.empty else empty
    if not rd_df.empty: fig_rd.update_traces(
        hovertemplate="<b>%{y}</b><br>Retained: %{customdata[0]:,.0f}<br>Lost: %{customdata[1]:,.0f}<br>Prev Total: %{customdata[2]:,.0f}<extra></extra>"); fig_rd.update_layout(
        yaxis={'categoryorder': 'total ascending'})

    return fig_rep, fig_rg, fig_rd


@callback(
    [Output('drilldown-chart', 'figure'), Output('drilldown-container', 'style'),
     Output('drilldown-title', 'children')],
    [Input('exec-stack-chart', 'clickData'), Input('cust-stack-chart', 'clickData'),
     Input('prod-stack-chart', 'clickData'), Input('rep-stack-chart', 'clickData'),
     Input('exec-grower-chart', 'clickData'), Input('exec-decline-chart', 'clickData'),
     Input('cust-grower-chart', 'clickData'), Input('cust-decline-chart', 'clickData'),
     Input('prod-grower-chart', 'clickData'), Input('prod-decline-chart', 'clickData'),
     Input('rep-grower-chart', 'clickData'), Input('rep-decline-chart', 'clickData'),
     Input('btn-close-drill', 'n_clicks')],
    [State('si-year-select', 'value'), State('si-time-view', 'value'), State('si-hierarchy-select', 'value'),
     State('si-segment-filter', 'value'), State('auth-store', 'data')]
)
def update_drilldown(exec_clk, cust_clk, prod_clk, rep_clk, eg_clk, ed_clk, cg_clk, cd_clk, pg_clk, pd_clk, rg_clk,
                     rd_clk, close_clk, year, view, h_col, segment, auth_data):
    trigger = ctx.triggered_id
    if not trigger or trigger == 'btn-close-drill': return go.Figure(), {'display': 'none'}, ""

    click_map = {
        'exec-stack-chart': exec_clk, 'cust-stack-chart': cust_clk, 'prod-stack-chart': prod_clk,
        'rep-stack-chart': rep_clk,
        'exec-grower-chart': eg_clk, 'exec-decline-chart': ed_clk, 'cust-grower-chart': cg_clk,
        'cust-decline-chart': cd_clk,
        'prod-grower-chart': pg_clk, 'prod-decline-chart': pd_clk, 'rep-grower-chart': rg_clk,
        'rep-decline-chart': rd_clk
    }
    active_clk = click_map.get(trigger)
    if not active_clk: return go.Figure(), {'display': 'none'}, ""

    entity = None;
    context = "Total";
    drill_type = ""

    if trigger == 'exec-stack-chart':
        entity = active_clk['points'][0]['x']
        drill_type = "ExecTrend"
    elif trigger in ['exec-grower-chart', 'exec-decline-chart', 'prod-stack-chart', 'prod-grower-chart',
                     'prod-decline-chart']:
        entity = active_clk['points'][0]['y'];
        drill_type = "Product"
    elif trigger in ['cust-stack-chart', 'cust-grower-chart', 'cust-decline-chart']:
        entity = active_clk['points'][0]['y'];
        drill_type = "Customer"
    elif trigger in ['rep-stack-chart', 'rep-grower-chart', 'rep-decline-chart']:
        entity = active_clk['points'][0]['y'];
        drill_type = "Rep"

    if not entity or entity == 'OTHERS': return go.Figure(), {'display': 'none'}, ""

    df_sec = get_secure_si_df(auth_data)
    if segment != 'All' and 'B2B' in df_sec.columns:
        df_filtered = df_sec[df_sec['B2B'].astype(str).str.strip().str.upper() == segment.upper()]
    else:
        df_filtered = df_sec

    # THE FIX: Core Engine applies the correct context
    df_curr, df_prev, lbl = core_engine.get_time_window(df_filtered, year, view)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df_sec.columns), 'Customer Names')

    if drill_type == "ExecTrend":
        dt_click = pd.to_datetime(entity)
        if view == 'Month':
            dff_c = df_curr[df_curr['Date'].dt.month == dt_click.month];
            dff_p = df_prev[df_prev['Date'].dt.month == dt_click.month]
        else:
            dff_c = df_curr;
            dff_p = df_prev
    elif drill_type == "Customer":
        dff_c = df_curr[df_curr[cust_col].astype(str).str.strip().str.upper() == str(entity).strip().upper()]
        dff_p = df_prev[df_prev[cust_col].astype(str).str.strip().str.upper() == str(entity).strip().upper()]
    elif drill_type == "Product":
        dff_c = df_curr[df_curr[h_col].astype(str).str.strip().str.upper() == str(entity).strip().upper()]
        dff_p = df_prev[df_prev[h_col].astype(str).str.strip().str.upper() == str(entity).strip().upper()]
    elif drill_type == "Rep":
        dff_c = df_curr[df_curr['Sales Rep'].astype(str).str.strip().str.upper() == str(entity).strip().upper()]
        dff_p = df_prev[df_prev['Sales Rep'].astype(str).str.strip().str.upper() == str(entity).strip().upper()]

    if view == 'Week':
        curr_trend = dff_c.groupby(dff_c['Date'].dt.dayofweek)['Value'].sum()
        prev_trend = dff_p.groupby(dff_p['Date'].dt.dayofweek)['Value'].sum()
        x_vals = range(7);
        axis_lbl = "DayOfWeek"
    elif view == 'Month':
        curr_trend = dff_c.groupby(dff_c['Date'].dt.day)['Value'].sum()
        prev_trend = dff_p.groupby(dff_p['Date'].dt.day)['Value'].sum()
        x_vals = range(1, 32);
        axis_lbl = "Day"
    else:
        curr_trend = dff_c.groupby(dff_c['Date'].dt.month)['Value'].sum()
        prev_trend = dff_p.groupby(dff_p['Date'].dt.month)['Value'].sum()
        x_vals = range(1, 13);
        axis_lbl = "Month"

    data = []
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for x in x_vals:
        val_c = curr_trend.get(x, 0);
        val_p = prev_trend.get(x, 0)
        base = min(val_c, val_p);
        growth = max(0, val_c - val_p);
        decline = max(0, val_p - val_c)
        if axis_lbl == "Month":
            label = pd.to_datetime(f"2020-{x}-01").strftime('%b')
        elif axis_lbl == "DayOfWeek":
            label = days[x]
        else:
            label = str(x)

        if base > 0: data.append({'Time': label, 'Type': 'Base', 'Value': base, 'Order': 1})
        if growth > 0: data.append({'Time': label, 'Type': 'Growth', 'Value': growth, 'Order': 2})
        if decline > 0: data.append({'Time': label, 'Type': 'Decline', 'Value': decline, 'Order': 3})

    df_drill = pd.DataFrame(data)
    if df_drill.empty: return go.Figure(layout={'title': "No Data"}), {'display': 'block'}, "No Data"

    drill_title = f"Overall Trend Variance ({lbl})" if drill_type == "ExecTrend" else f"Variance Waterfall: {entity} ({lbl})"
    fig = px.bar(df_drill, x='Time', y='Value', color='Type', title=drill_title,
                 color_discrete_map={'Base': '#95a5a6', 'Growth': '#2ecc71', 'Decline': '#e74c3c'}, text_auto='.2s',
                 category_orders={'Order': [1, 2, 3]})
    fig.update_layout(barmode='stack', plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')

    return fig, {'display': 'block', 'padding': '40px', 'backgroundColor': '#fff',
                 'borderTop': '2px solid #ccc'}, f"Deep Dive: {drill_title}"


@callback(
    Output("stacked-intelligence-download-snapshot", "data"), Input("stacked-intelligence-btn-snapshot", "n_clicks"),
    [State('exec-stack-chart', 'figure'), State('cust-stack-chart', 'figure'), State('prod-stack-chart', 'figure'),
     State('rep-stack-chart', 'figure'), State('exec-grower-chart', 'figure'), State('exec-decline-chart', 'figure'),
     State('cust-grower-chart', 'figure'), State('cust-decline-chart', 'figure'), State('prod-grower-chart', 'figure'),
     State('prod-decline-chart', 'figure'), State('rep-grower-chart', 'figure'), State('rep-decline-chart', 'figure')],
    prevent_initial_call=True
)
def download_snapshot(n_clicks, *figures):
    html_string = f"<html><head><title>Stacked Analysis Snapshot</title></head><body style='font-family: sans-serif; padding: 20px;'><h1 style='text-align:center;'>Stacked Analysis Snapshot</h1>"
    import plotly.io as pio
    for fig_data in figures:
        if fig_data and isinstance(fig_data, dict) and fig_data.get('data'):
            fig = go.Figure(fig_data).update_layout(width=None, height=500, autosize=True)
            html_string += f"<div style='margin-bottom: 30px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); padding: 10px; background: white;'>{pio.to_html(fig, full_html=False, include_plotlyjs='cdn')}</div>"
    html_string += "</body></html>"
    return dict(content=html_string, filename="stacked_analysis_snapshot.html")