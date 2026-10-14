import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import datetime

import Assets.styles as s
import core_engine
import dash
from dash import html, dcc, dash_table, Input, Output, State, ctx, callback, no_update
from dash.dash_table.Format import Format, Scheme, Group
import plotly.graph_objects as go
import pandas as pd
import numpy as np

from data_loader import df_global, df_soh_global, years_global, METRIC_MODE, get_latest_data
from users import enforce_row_level_security

_TARGETS_PATH = os.path.join(root_dir, 'Raws', 'Rep Targets.xlsx')


def _load_rep_target(rep: str, year: int, view: str, selected_month_date=None) -> tuple:
    """
    Load the sales target for a rep from Rep Targets.xlsx.

    The file has a 'Rep Name' column and one column per month (date header = first day of month).
    New month columns are added by the user each month — no code change needed.

    Returns (target_value: float | None, label: str)
      - target_value is already scaled to the requested view period
      - label describes the source (e.g. "Jul 2026 target", "2026 annual target")
    """
    if not os.path.exists(_TARGETS_PATH):
        return None, ''
    try:
        df_t = pd.read_excel(_TARGETS_PATH)
        df_t.columns = df_t.columns.tolist()

        if 'Rep Name' not in df_t.columns:
            return None, ''

        rep_row = df_t[
            df_t['Rep Name'].astype(str).str.strip().str.upper() == str(rep).strip().upper()
        ]
        if rep_row.empty:
            return None, ''
        rep_row = rep_row.iloc[0]

        # Collect month columns (datetime / Timestamp headers) and sort chronologically
        month_cols = sorted(
            [c for c in df_t.columns if isinstance(c, (pd.Timestamp, datetime.datetime))],
            key=lambda x: pd.Timestamp(x)
        )
        if not month_cols:
            return None, ''

        def _month_val(col):
            v = pd.to_numeric(rep_row[col], errors='coerce')
            return float(v) if pd.notna(v) else None

        def _target_month_ts(sel):
            """Resolve which month to use from selected_month_date or fall back to today."""
            if sel:
                try:
                    dt = pd.Timestamp(sel)
                    return pd.Timestamp(year=dt.year, month=dt.month, day=1)
                except Exception:
                    pass
            return pd.Timestamp(year=year, month=pd.Timestamp.now().month, day=1)

        def _find_col_for_month(target_ts):
            """Return (col, ts) for the requested month, or nearest earlier month."""
            # Exact match
            for c in month_cols:
                ts = pd.Timestamp(c)
                if ts.year == target_ts.year and ts.month == target_ts.month:
                    return c, ts
            # Nearest earlier match within the same year
            earlier = [(c, pd.Timestamp(c)) for c in month_cols
                       if pd.Timestamp(c) < target_ts and pd.Timestamp(c).year == year]
            if earlier:
                return max(earlier, key=lambda x: x[1])
            # Any available (most recent overall)
            return month_cols[-1], pd.Timestamp(month_cols[-1])

        if view == 'Month':
            tgt_ts = _target_month_ts(selected_month_date)
            col, ts = _find_col_for_month(tgt_ts)
            val = _month_val(col)
            if val is not None:
                return val, f"{ts.strftime('%b %Y')} target"

        elif view == 'Week':
            tgt_ts = _target_month_ts(selected_month_date)
            col, ts = _find_col_for_month(tgt_ts)
            val = _month_val(col)
            if val is not None:
                # 4.33 = average weeks per month
                return val / 4.33, f"weekly target ({ts.strftime('%b %Y')})"

        elif view == 'Quarter':
            tgt_ts = _target_month_ts(selected_month_date)
            q_start = ((tgt_ts.month - 1) // 3) * 3 + 1
            q_months = {q_start, q_start + 1, q_start + 2}
            vals, labels = [], []
            for c in month_cols:
                ts = pd.Timestamp(c)
                if ts.year == year and ts.month in q_months:
                    v = _month_val(c)
                    if v is not None:
                        vals.append(v)
                        labels.append(ts.strftime('%b'))
            if vals:
                # If fewer than 3 months present, scale proportionally
                total = sum(vals) * (3 / len(vals))
                return total, f"Q target ({'/'.join(labels)})"

        elif view == 'Year':
            vals, found_months = [], []
            for c in month_cols:
                ts = pd.Timestamp(c)
                if ts.year == year:
                    v = _month_val(c)
                    if v is not None:
                        vals.append(v)
                        found_months.append(ts.month)
            if vals:
                # Scale to full year if not all 12 months present
                annual = sum(vals) * (12 / len(vals))
                return annual, f"{year} annual target ({len(vals)}/12 months)"

        return None, ''
    except Exception:
        return None, ''


# --- SECURITY HELPER ---
def get_secure_df(auth_data):
    df_fresh, _, _, _ = get_latest_data()
    return enforce_row_level_security(df_fresh, auth_data)


# --- LAYOUT ---
layout = html.Div([
    dcc.Store(id='rep-store'), dcc.Store(id='store-trend-month'), dcc.Download(id="rep-dl"),

    html.Div([
        html.H1("Sales Rep Portal", style=s.HEADER_TITLE),
        html.Div([
            html.Div(id='segment-container-rep', children=[
                html.Span("SEGMENT:",
                          style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
                dcc.RadioItems(id='segment-filter-rep', options=['All', 'B2B', 'B2C'], value='All', inline=True,
                               style={'marginRight': '20px', 'display': 'inline-block'})
            ], style={'display': 'inline-block'}),

            html.Span("YEAR:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.Dropdown(id='year-rep', options=[{'label': str(y), 'value': y} for y in years_global],
                         value=years_global[0] if years_global else None, clearable=False,
                         style={'width': '80px', 'display': 'inline-block', 'marginRight': '20px'}),

            html.Span("REP:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.Dropdown(id='rep-dd', placeholder="Select Rep...",
                         style={'width': '220px', 'display': 'inline-block', 'marginRight': '20px'}),

            html.Span("VIEW:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='view-rep', options=['Week', 'Month', 'Quarter', 'Year'], value='Month', inline=True,
                           style={'marginRight': '20px', 'display': 'inline-block'}),

            html.Span("LEVEL:",
                      style={'fontWeight': 'bold', 'fontSize': '11px', 'color': '#95a5a6', 'marginRight': '5px'}),
            dcc.RadioItems(id='hier-rep',
                           options=[{'label': 'Grp', 'value': 'Items 2'}, {'label': 'Sub', 'value': 'Items'},
                                    {'label': 'Item', 'value': 'Name'}], value='Items', inline=True,
                           style={'display': 'inline-block'}),

            html.Button("📸 SNAPSHOT", id="rep-snap", style=s.BTN_STYLE)
        ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'justifyContent': 'center',
                  'backgroundColor': '#f8f9fa', 'padding': '15px', 'borderRadius': '8px',
                  'border': f'1px solid {s.COLORS["border"]}'}),
        html.Div(id='header-rev-display',
                 style={'textAlign': 'center', 'color': s.COLORS['success'], 'marginTop': '20px', 'fontWeight': '300',
                        'fontSize': '20px'})
    ], style=s.SECTION),

    html.Div(id='team-chart-section', children=[dcc.Graph(id='team-chart', style=s.CHART_CONTAINER)], style=s.SECTION),
    html.Div([dcc.Graph(id='target-meter', style=s.CHART_CONTAINER)], style=s.SECTION),

    html.Div([
        html.Div([html.H3("1. Sales Trend Analysis", style={'display': 'inline-block', 'marginRight': '20px'}),
                  html.Button("Reset Trend", id='reset-trend', style=s.BTN_STYLE)], style={'marginBottom': '10px'}),
        dcc.Graph(id='rep-trend', style=s.CHART_CONTAINER), html.Div(id='rep-trend-anno'),
        dcc.Graph(id='rep-trend-daily', style=s.CHART_CONTAINER), html.Div(id='rep-trend-daily-anno')
    ], style=s.SECTION),

    html.Div([
        html.Div([html.H3("2. Product Portfolio", style={'display': 'inline-block', 'marginRight': '20px'}),
                  html.Button("Reset Products", id='reset-prod', style=s.BTN_STYLE)], style={'marginBottom': '10px'}),
        dcc.Graph(id='rep-prod', style=s.CHART_CONTAINER), html.Div(id='rep-prod-anno'),
        dcc.Graph(id='rep-prod-monthly', style=s.CHART_CONTAINER), html.Div(id='rep-prod-monthly-anno'),
        dcc.Graph(id='rep-prod-daily', style=s.CHART_CONTAINER), html.Div(id='rep-prod-daily-anno'),
        dcc.Graph(id='rep-prod-variants', style={**s.CHART_CONTAINER, 'marginTop': '20px'}),
        html.Div(id='rep-prod-variants-anno')
    ], style=s.SECTION),

    html.Div([
        html.Div([html.H3("3. Customer Portfolio", style={'display': 'inline-block', 'marginRight': '20px'}),
                  html.Button("Reset Customers", id='reset-cust', style=s.BTN_STYLE)], style={'marginBottom': '10px'}),
        dcc.Graph(id='rep-cust', style=s.CHART_CONTAINER), html.Div(id='rep-cust-anno'),
        dcc.Graph(id='rep-cust-monthly', style=s.CHART_CONTAINER), html.Div(id='rep-cust-monthly-anno'),
        dcc.Graph(id='rep-cust-daily', style=s.CHART_CONTAINER), html.Div(id='rep-cust-daily-anno'),
    ], style=s.SECTION),

    html.Div([
        html.H3("4. Deep Dive (Contextual)", style={'textAlign': 'center', 'color': '#2c3e50'}),
        dcc.Graph(id='cust-trend-chart', style=s.CHART_CONTAINER), html.Div(id='cust-trend-chart-anno'),
        html.Hr(style={'margin': '40px 0', 'borderTop': '1px dashed #ccc'}),
        dcc.Graph(id='cust-top-items', style=s.CHART_CONTAINER), html.Div(id='cust-top-items-anno'),
        dcc.Graph(id='cust-item-monthly', style=s.CHART_CONTAINER), html.Div(id='cust-item-monthly-anno'),
        dcc.Graph(id='cust-item-daily', style=s.CHART_CONTAINER), html.Div(id='cust-item-daily-anno'),

        html.Hr(style={'margin': '40px 0', 'borderTop': '2px solid #2ecc71'}),
        html.Div([
            html.H3(id='cust-whitespace-title',
                    style={'color': '#27ae60', 'textAlign': 'center', 'marginBottom': '5px'}),
            html.P(
                "These items were purchased historically but dropped recently.",
                style={'textAlign': 'center', 'color': '#7f8c8d', 'fontSize': '13px'}),
            dash_table.DataTable(id='cust-whitespace-table', columns=[{'name': 'Item Name', 'id': 'Item'},
                                                                      {'name': 'Historical Qty', 'id': 'Hist_Qty',
                                                                       'type': 'numeric', 'format': Format(precision=1,
                                                                                                           scheme=Scheme.fixed,
                                                                                                           group=Group.yes)},
                                                                      {'name': 'Days Since Last Buy',
                                                                       'id': 'Days_Since'},
                                                                      {'name': 'Current Stock Available',
                                                                       'id': 'Qty_Hand', 'type': 'numeric',
                                                                       'format': Format(precision=0,
                                                                                        scheme=Scheme.fixed,
                                                                                        group=Group.yes)}],
                                 style_table={'height': '400px', 'overflowY': 'auto'},
                                 style_header={'backgroundColor': '#27ae60', 'color': 'white', 'fontWeight': 'bold'},
                                 style_cell={'textAlign': 'center', 'padding': '10px', 'fontFamily': 'Segoe UI'},
                                 style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f8f9fa'}])
        ])
    ], style=s.SECTION),
], style=s.MAIN_CONTAINER)


# --- CALLBACKS ---

@callback(Output('rep-dd', 'value', allow_duplicate=True), [Input('team-chart', 'clickData')],
          prevent_initial_call=True)
def sync_rep(clk): return clk['points'][0]['y'] if clk else dash.no_update


@callback(
    [Output('rep-dd', 'options'), Output('rep-dd', 'value'), Output('rep-dd', 'disabled'),
     Output('segment-container-rep', 'style'), Output('team-chart-section', 'style')],
    [Input('year-rep', 'value'), Input('auth-store', 'data')]
)
def setup_permissions(y, auth_data):
    if not auth_data: return [], None, True, {'display': 'none'}, {'display': 'none'}
    df, _, _, _ = get_latest_data()  # fresh (not the boot-frozen df_global)
    all_reps = sorted(df[df['Date'].dt.year == int(y)]['Sales Rep'].dropna().unique())
    ui_rules = auth_data.get('ui_permissions', {})

    rep_opts = [{'label': auth_data.get('rep_name', ''), 'value': auth_data.get('rep_name', '')}] if ui_rules.get(
        'lock_rep_dropdown', True) else [{'label': r, 'value': r} for r in all_reps]
    rep_val = auth_data.get('rep_name', '') if ui_rules.get('lock_rep_dropdown', True) else (
        all_reps[0] if all_reps else None)
    seg_style = {'display': 'inline-block'} if ui_rules.get('show_segment_filters', False) else {'display': 'none'}
    team_style = s.SECTION if ui_rules.get('show_team_charts', False) else {'display': 'none'}

    return rep_opts, rep_val, ui_rules.get('lock_rep_dropdown', True), seg_style, team_style


@callback(
    Output('store-trend-month', 'data'),
    [Input('rep-trend', 'clickData'), Input('cust-trend-chart', 'clickData'), Input('reset-trend', 'n_clicks')],
    [State('store-trend-month', 'data')]
)
def manage_trend_sync(clk_main, clk_deep, reset, current_month):
    trig = ctx.triggered_id
    if trig == 'reset-trend': return None
    if trig == 'rep-trend' and clk_main: return clk_main['points'][0]['x']
    if trig == 'cust-trend-chart' and clk_deep: return clk_deep['points'][0]['x']
    return current_month


# MAIN DASHBOARD UPDATE
@callback(
    [Output('header-rev-display', 'children'), Output('team-chart', 'figure'), Output('target-meter', 'figure'),
     Output('rep-trend', 'figure'), Output('rep-trend-anno', 'children'),
     Output('rep-prod', 'figure'), Output('rep-prod-anno', 'children'),
     Output('rep-cust', 'figure'), Output('rep-cust-anno', 'children')],
    [Input('year-rep', 'value'), Input('view-rep', 'value'), Input('hier-rep', 'value'), Input('rep-dd', 'value'),
     Input('segment-filter-rep', 'value'), Input('store-trend-month', 'data')],
    [State('auth-store', 'data')]
)
def update_main_dash(year, view, h_col, rep, seg, selected_month_date, auth_data):
    empty_res = "Loading...", go.Figure(), go.Figure(), go.Figure(), "", go.Figure(), "", go.Figure(), ""
    if not year: return empty_res

    df = get_secure_df(auth_data)
    if df.empty: return empty_res

    target_year = int(year)
    if seg != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str).str.strip().str.upper() == seg.upper()]

    ui_rules = auth_data.get('ui_permissions', {}) if auth_data else {}
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    # ENGINE UPGRADE: Centralized Global Timing
    df_curr, df_prev, lbl = core_engine.get_time_window(df, target_year, view, selected_month_date)

    _C_CREDIT_SEL = '#3498db'   # selected rep — credit
    _C_CREDIT_DIM = '#bdc3c7'   # other reps  — credit
    _C_CASH_SEL   = '#E67E22'   # selected rep — cash
    _C_CASH_DIM   = '#F5CBA7'   # other reps  — cash

    fig_team = go.Figure()
    if ui_rules.get('show_team_charts', False) and not df_curr.empty:
        selected_upper = str(rep).strip().upper() if rep else ''
        has_sale_type = 'Sale_Type' in df_curr.columns

        # Aggregate credit and cash separately per rep
        if has_sale_type:
            _cr = (df_curr[df_curr['Sale_Type'] == 'Credit']
                   .groupby('Sales Rep')['Value'].sum())
            _cs = (df_curr[df_curr['Sale_Type'] == 'Cash']
                   .groupby('Sales Rep')['Value'].sum())
        else:
            _cr = df_curr.groupby('Sales Rep')['Value'].sum()
            _cs = pd.Series(dtype=float)

        # Sort reps by total descending
        _total = _cr.add(_cs, fill_value=0).sort_values(ascending=True)
        _reps  = _total.index.tolist()
        _cr_v  = _cr.reindex(_reps).fillna(0).values
        _cs_v  = _cs.reindex(_reps).fillna(0).values if not _cs.empty else np.zeros(len(_reps))

        _cr_colors = [_C_CREDIT_SEL if str(r).strip().upper() == selected_upper
                      else _C_CREDIT_DIM for r in _reps]
        _cs_colors = [_C_CASH_SEL if str(r).strip().upper() == selected_upper
                      else _C_CASH_DIM for r in _reps]

        title_team = s.get_chart_title(f"Team Performance ({lbl})", df_curr, df_prev, METRIC_MODE)
        fig_team.add_trace(go.Bar(
            name='Credit', x=_cr_v, y=_reps, orientation='h',
            marker_color=_cr_colors,
            hovertemplate='<b>%{y}</b><br>Credit: Ksh %{x:,.0f}<extra></extra>'
        ))
        if _cs_v.any():
            fig_team.add_trace(go.Bar(
                name='Cash', x=_cs_v, y=_reps, orientation='h',
                marker_color=_cs_colors,
                hovertemplate='<b>%{y}</b><br>Cash: Ksh %{x:,.0f}<extra></extra>'
            ))
        fig_team.update_layout(
            barmode='stack', title={'text': title_team},
            xaxis_title='Revenue (Ksh)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            margin=dict(l=10, r=10, t=60, b=10),
            height=max(300, len(_reps) * 28 + 80),
        )

    if not rep: return "Select Rep", fig_team, go.Figure(), go.Figure(), "", go.Figure(), "", go.Figure(), ""

    # Bulletproof Rep Matching
    df_rep_all = df[df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()]
    df_rep_curr, df_rep_prev, lbl_rep = core_engine.get_time_window(df_rep_all, target_year, view, selected_month_date)

    # Meter Logic — use Rep Targets.xlsx; fall back to LY×1.15
    rep_ly = df_rep_all[(df_rep_all['Date'].dt.year == (target_year - 1))]['Value'].sum()
    ly_val = rep_ly / 12 if view == 'Month' else (rep_ly / 52 if view == 'Week' else rep_ly)

    xlsx_target, xlsx_label = _load_rep_target(rep, target_year, view, selected_month_date)
    if xlsx_target is not None and xlsx_target > 0:
        target = xlsx_target
        target_label = xlsx_label
    else:
        target = rep_ly * 1.15 if rep_ly > 0 else df_rep_curr['Value'].sum() * 1.2
        if view == 'Month': target /= 12
        if view == 'Week': target /= 52
        target_label = f"LY×1.15 (LY: {ly_val:,.0f})"

    _total_val = df_rep_curr['Value'].sum()
    _cash_val  = (df_rep_curr[df_rep_curr['Sale_Type'] == 'Cash']['Value'].sum()
                  if 'Sale_Type' in df_rep_curr.columns else 0)
    _cash_pct  = (_cash_val / _total_val * 100) if _total_val > 0 else 0
    _cred_pct  = 100 - _cash_pct

    fig_meter = go.Figure(
        go.Indicator(mode="gauge+number+delta", value=_total_val, delta={'reference': target},
                     gauge={'axis': {'range': [None, max(target * 1.3, _total_val * 1.1)]},
                            'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': target}}))
    fig_meter.update_layout(
        title={'text': (f"Target: {target:,.0f} ({target_label})<br>"
                        f"<span style='font-size:12px;color:#E67E22'>Cash {_cash_pct:.0f}%</span>"
                        f"<span style='font-size:12px;color:#7f8c8d'> | </span>"
                        f"<span style='font-size:12px;color:#3498db'>Credit {_cred_pct:.0f}%</span>")},
        margin=dict(t=100, b=0)
    )

    # Trend Logic (Always asks for Year to show YTD Context)
    df_rep_ytd, df_rep_lytd, _ = core_engine.get_time_window(df_rep_all, target_year, 'Year')
    trend_raw, xfmt, dtick = core_engine.get_trend_data(df_rep_ytd, view, vol_col)
    header_trend = s.get_chart_title(f"Sales Trend (YTD Context)", df_rep_ytd, df_rep_lytd, METRIC_MODE)

    if 'Sale_Type' in df_rep_ytd.columns and df_rep_ytd['Sale_Type'].nunique() > 1:
        _tr_cr, _, _ = core_engine.get_trend_data(
            df_rep_ytd[df_rep_ytd['Sale_Type'] == 'Credit'], view, vol_col)
        _tr_cs, _, _ = core_engine.get_trend_data(
            df_rep_ytd[df_rep_ytd['Sale_Type'] == 'Cash'], view, vol_col)
        fig_trend = go.Figure()
        if not _tr_cr.empty:
            fig_trend.add_trace(go.Bar(
                name='Credit', x=_tr_cr['Date'], y=_tr_cr['Value'],
                marker_color=_C_CREDIT_SEL,
                hovertemplate='%{x}: Ksh %{y:,.0f}<extra>Credit</extra>'
            ))
        if not _tr_cs.empty:
            fig_trend.add_trace(go.Bar(
                name='Cash', x=_tr_cs['Date'], y=_tr_cs['Value'],
                marker_color=_C_CASH_SEL,
                hovertemplate='%{x}: Ksh %{y:,.0f}<extra>Cash</extra>'
            ))
        fig_trend.update_layout(
            barmode='stack', title={'text': header_trend},
            xaxis_tickformat=xfmt, xaxis_dtick=dtick,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1)
        )
    else:
        fig_trend = s.create_bar_chart(trend_raw, 'Date', 'Value', header_trend, vol_col, METRIC_MODE, use_z_colors=True)
        if not trend_raw.empty:
            fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    anno_trend = s.get_storytelling_anno(trend_raw, 'Date', 'Value', vol_col, METRIC_MODE)

    # Top Items
    items = core_engine.get_top_performers(df_rep_curr, h_col, vol_col, top_n=20)
    header_prod = s.get_chart_title(f"Top Items ({lbl_rep})", df_rep_curr, df_rep_prev, METRIC_MODE)
    fig_prod = s.create_bar_chart(items, h_col, 'Value', header_prod, vol_col, METRIC_MODE, is_horizontal=True)
    anno_prod = s.get_storytelling_anno(items, h_col, 'Value', vol_col, METRIC_MODE)

    # Top Customers
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')
    cust = core_engine.get_top_performers(df_rep_curr, cust_col, vol_col, top_n=20)
    header_cust = s.get_chart_title(f"Top Customers ({lbl_rep})", df_rep_curr, df_rep_prev, METRIC_MODE)
    fig_cust = s.create_bar_chart(cust, cust_col, 'Value', header_cust, vol_col, METRIC_MODE, is_horizontal=True)
    fig_cust.update_traces(marker_color='#2ecc71')
    anno_cust = s.get_storytelling_anno(cust, cust_col, 'Value', vol_col, METRIC_MODE)

    return f"{rep}: Ksh {df_rep_curr['Value'].sum():,.0f}", fig_team, fig_meter, fig_trend, anno_trend, fig_prod, anno_prod, fig_cust, anno_cust


@callback(
    [Output('rep-trend-daily', 'figure'), Output('rep-trend-daily-anno', 'children')],
    [Input('store-trend-month', 'data'), Input('rep-dd', 'value'), Input('year-rep', 'value'),
     Input('reset-trend', 'n_clicks'), Input('view-rep', 'value')],
    [State('auth-store', 'data')]
)
def drill_trend_daily(date_str, rep, year, reset, view, auth_data):
    if not rep or not date_str: return go.Figure(), ""
    df = get_secure_df(auth_data)
    df_rep = df[df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_c, df_p, lbl = core_engine.get_time_window(df_rep, year, view, date_str)
    daily_data = core_engine.get_drilldown_data(df_c, vol_col)

    header = s.get_chart_title(f"Daily Breakdown: {lbl}", df_c, df_p, METRIC_MODE)
    fig = s.create_bar_chart(daily_data, 'Date', 'Value', header, vol_col, METRIC_MODE, use_z_colors=True)
    return fig, s.get_storytelling_anno(daily_data, 'Date', 'Value', vol_col, METRIC_MODE)


@callback(
    [Output('rep-prod-monthly', 'figure'), Output('rep-prod-monthly-anno', 'children'),
     Output('rep-prod-daily', 'figure'), Output('rep-prod-daily-anno', 'children'),
     Output('rep-prod-variants', 'figure'), Output('rep-prod-variants-anno', 'children')],
    [Input('rep-prod', 'clickData'), Input('rep-prod-monthly', 'clickData'), Input('rep-dd', 'value'),
     Input('year-rep', 'value'), Input('view-rep', 'value'), Input('hier-rep', 'value'),
     Input('reset-prod', 'n_clicks')],
    [State('auth-store', 'data')]
)
def drill_product(clk_top, clk_month, rep, year, view, h_col, reset, auth_data):
    empty_res = go.Figure(), "", go.Figure(), "", go.Figure(), ""
    if ctx.triggered_id == 'reset-prod' or not rep or not clk_top: return empty_res

    item = clk_top['points'][0]['y']
    df = get_secure_df(auth_data)
    df_base = df[(df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
                df[h_col].astype(str).str.strip().str.upper() == str(item).strip().upper())]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_cy, df_py, lbl = core_engine.get_time_window(df_base, year, 'Year')
    if df_cy.empty: return empty_res

    monthly, xfmt, dtick = core_engine.get_trend_data(df_cy, view, vol_col)
    header_m = s.get_chart_title(f"Trend: {item} (YTD)", df_cy, df_py, METRIC_MODE)
    fig_m = s.create_bar_chart(monthly, 'Date', 'Value', header_m, vol_col, METRIC_MODE, use_z_colors=True)
    if not monthly.empty: fig_m.update_xaxes(tickformat=xfmt, dtick=dtick)

    fig_d, anno_d = go.Figure(), ""
    if clk_month:
        clk_d_str = clk_month['points'][0]['x']
        df_mc, df_mp, lbl_d = core_engine.get_time_window(df_base, year, view, clk_d_str)
        daily = core_engine.get_drilldown_data(df_mc, vol_col)

        header_d = s.get_chart_title(f"Daily: {lbl_d}", df_mc, df_mp, METRIC_MODE)
        fig_d = s.create_bar_chart(daily, 'Date', 'Value', header_d, vol_col, METRIC_MODE, use_z_colors=True)
        anno_d = s.get_storytelling_anno(daily, 'Date', 'Value', vol_col, METRIC_MODE)

    fig_v, anno_v = go.Figure(), ""
    if h_col != 'Name':
        var = core_engine.get_top_performers(df_cy, 'Name', vol_col)
        header_v = s.get_chart_title(f"Variants: {item}", df_cy, df_py, METRIC_MODE)
        fig_v = s.create_bar_chart(var, 'Name', 'Value', header_v, vol_col, METRIC_MODE, is_horizontal=True)
        anno_v = s.get_storytelling_anno(var, 'Name', 'Value', vol_col, METRIC_MODE)

    return fig_m, s.get_storytelling_anno(monthly, 'Date', 'Value', vol_col, METRIC_MODE), fig_d, anno_d, fig_v, anno_v


@callback(
    [Output('rep-cust-monthly', 'figure'), Output('rep-cust-monthly-anno', 'children'),
     Output('rep-cust-daily', 'figure'), Output('rep-cust-daily-anno', 'children')],
    [Input('rep-cust', 'clickData'), Input('rep-cust-monthly', 'clickData'), Input('rep-dd', 'value'),
     Input('year-rep', 'value'), Input('view-rep', 'value'), Input('reset-cust', 'n_clicks')],
    [State('auth-store', 'data')]
)
def drill_customer(clk_top, clk_month, rep, year, view, reset, auth_data):
    empty_res = go.Figure(), "", go.Figure(), ""
    if ctx.triggered_id == 'reset-cust' or not rep or not clk_top: return empty_res

    cust = clk_top['points'][0]['y']
    df = get_secure_df(auth_data)
    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')

    df_base = df[(df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
                df[cust_col].astype(str).str.strip().str.upper() == str(cust).strip().upper())]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_cy, df_py, lbl = core_engine.get_time_window(df_base, year, 'Year')
    if df_cy.empty: return empty_res

    monthly, xfmt, dtick = core_engine.get_trend_data(df_cy, view, vol_col)
    header_m = s.get_chart_title(f"Trend: {cust} (YTD)", df_cy, df_py, METRIC_MODE)
    fig_m = s.create_bar_chart(monthly, 'Date', 'Value', header_m, vol_col, METRIC_MODE, use_z_colors=True)
    if not monthly.empty: fig_m.update_xaxes(tickformat=xfmt, dtick=dtick)

    fig_d, anno_d = go.Figure(), ""
    if clk_month:
        clk_d_str = clk_month['points'][0]['x']
        df_mc, df_mp, lbl_d = core_engine.get_time_window(df_base, year, view, clk_d_str)
        daily = core_engine.get_drilldown_data(df_mc, vol_col)

        header_d = s.get_chart_title(f"Daily: {lbl_d}", df_mc, df_mp, METRIC_MODE)
        fig_d = s.create_bar_chart(daily, 'Date', 'Value', header_d, vol_col, METRIC_MODE, use_z_colors=True)
        anno_d = s.get_storytelling_anno(daily, 'Date', 'Value', vol_col, METRIC_MODE)

    return fig_m, s.get_storytelling_anno(monthly, 'Date', 'Value', vol_col, METRIC_MODE), fig_d, anno_d


# --- THE PERFECT UPSELL LOGIC ---
# --- THE PERFECT UPSELL LOGIC ---
@callback(
    [Output('cust-trend-chart', 'figure'), Output('cust-trend-chart-anno', 'children'),
     Output('cust-top-items', 'figure'), Output('cust-top-items-anno', 'children'),
     Output('cust-whitespace-table', 'data'), Output('cust-whitespace-title', 'children')],
    [Input('rep-cust', 'clickData'), Input('store-trend-month', 'data'), Input('rep-dd', 'value'),
     Input('year-rep', 'value'), Input('view-rep', 'value'), Input('hier-rep', 'value')],
    [State('auth-store', 'data')]
)
def update_deep_dive_main(clk_cust, trend_month, rep, year, view, h_col, auth_data):
    empty_res = go.Figure(), "", go.Figure(), "", [], ""
    df = get_secure_df(auth_data)
    if not rep or df.empty: return empty_res

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')

    # Get Customer ID
    if clk_cust:
        cust_id = clk_cust['points'][0]['y']
    else:
        try:
            cust_id = df[(df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
                    df['Date'].dt.year == int(year))].groupby(cust_col)['Value'].sum().nlargest(1).index[0]
        except:
            return empty_res

    df_c_all = df[(df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
            df[cust_col].astype(str).str.strip().str.upper() == str(cust_id).strip().upper())]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_cy, df_py, _ = core_engine.get_time_window(df_c_all, year, 'Year')
    if df_cy.empty: return empty_res

    trend, xfmt, dtick = core_engine.get_trend_data(df_cy, view, vol_col)
    header_trend = s.get_chart_title(f"Overview: {cust_id} (YTD)", df_cy, df_py, METRIC_MODE)
    fig_trend = s.create_bar_chart(trend, 'Date', 'Value', header_trend, vol_col, METRIC_MODE, use_z_colors=True)
    if not trend.empty: fig_trend.update_xaxes(tickformat=xfmt, dtick=dtick)

    # Engine perfectly extracts the targeted items timeframe based on trend click
    df_items, df_items_p, t_lbl = core_engine.get_time_window(df_c_all, year, view, trend_month)

    target_item_col = h_col if h_col in df.columns else 'Items'
    items = core_engine.get_top_performers(df_items, target_item_col, vol_col)
    header_items = s.get_chart_title(f"Top {target_item_col}: {t_lbl}", df_items, df_items_p, METRIC_MODE)
    fig_items = s.create_bar_chart(items, target_item_col, 'Value', header_items, vol_col, METRIC_MODE,
                                   is_horizontal=True)

    # --- NEXT BEST ACTION (UPSELL) WITH ROBUST DATA ARMOR ---
    last_date = df_items['Date'].max() if not df_items.empty else df_cy['Date'].max()
    if pd.isna(last_date): last_date = df['Date'].max()

    threshold_date = last_date - pd.Timedelta(days=90)
    df_hist = df_c_all[df_c_all['Date'] < threshold_date]
    df_recent = df_c_all[df_c_all['Date'] >= threshold_date]

    # Force upsell to look at specific items/names, not high-level categories
    upsell_col = 'Name' if 'Name' in df.columns else target_item_col

    dropped = set(df_hist[upsell_col].unique()) - set(df_recent[upsell_col].unique())
    table_data = []

    if dropped and not df_hist.empty:
        ws_df = df_hist[df_hist[upsell_col].isin(dropped)].groupby(upsell_col).agg(
            Last=('Date', 'max'),
            Hist_Qty=(vol_col, 'sum')
        ).reset_index()

        ws_df['Days_Since'] = (last_date - ws_df['Last']).dt.days

        # --- ROBUST INVENTORY MATCHING ---
        df_soh_local = df_soh_global.copy() if df_soh_global is not None else pd.DataFrame()
        df_soh_local.columns = [str(c).strip() for c in df_soh_local.columns]

        if 'Items' in df_soh_local.columns:
            df_soh_local['Join_Key_SOH'] = df_soh_local['Items'].astype(str).str.strip().str.upper()
        else:
            df_soh_local['Join_Key_SOH'] = 'MISSING'

        if 'Qty On Hand' not in df_soh_local.columns:
            qih_c = next(
                (c for c in df_soh_local.columns if 'qih' in c.lower() or 'qty' in c.lower() or 'stock' in c.lower()),
                None)
            if qih_c:
                df_soh_local.rename(columns={qih_c: 'Qty On Hand'}, inplace=True)
            else:
                df_soh_local['Qty On Hand'] = 0

        # Create a map from Name -> Item Code to ensure flawless SOH joining
        if 'Item Code' in df_hist.columns:
            item_map = df_hist.drop_duplicates(subset=[upsell_col]).set_index(upsell_col)['Item Code'].to_dict()
            ws_df['Item Code'] = ws_df[upsell_col].map(item_map)
            ws_df['Join_Key_Sales'] = ws_df['Item Code'].astype(str).str.strip().str.upper()
        else:
            ws_df['Join_Key_Sales'] = ws_df[upsell_col].astype(str).str.strip().str.upper()

        # Group SOH to prevent duplicates inflating the dataset
        soh_grouped = df_soh_local.groupby('Join_Key_SOH')['Qty On Hand'].sum().reset_index()

        ws_df = pd.merge(ws_df, soh_grouped, left_on='Join_Key_Sales', right_on='Join_Key_SOH', how='left')
        ws_df['Qty On Hand'] = ws_df['Qty On Hand'].fillna(0)

        # Only show dropped items that are ACTUALLY IN STOCK right now
        ws_df = ws_df[ws_df['Qty On Hand'] > 0]
        ws_df = ws_df.sort_values('Hist_Qty', ascending=False)

        # Prepare for table output
        ws_df = ws_df.rename(columns={upsell_col: 'Item', 'Qty On Hand': 'Qty_Hand'})
        table_data = ws_df.head(20).to_dict('records')

    return fig_trend, s.get_storytelling_anno(trend, 'Date', 'Value', vol_col, METRIC_MODE), \
        fig_items, s.get_storytelling_anno(items, target_item_col, 'Value', vol_col, METRIC_MODE), \
        table_data, f"Next Best Action: Upsell Targets for {cust_id}"

@callback(
    [Output('cust-item-monthly', 'figure'), Output('cust-item-monthly-anno', 'children'),
     Output('cust-item-daily', 'figure'), Output('cust-item-daily-anno', 'children')],
    [Input('cust-top-items', 'clickData'), Input('cust-item-monthly', 'clickData'), Input('rep-cust', 'clickData'),
     Input('rep-dd', 'value'), Input('year-rep', 'value'), Input('view-rep', 'value'), Input('hier-rep', 'value')],
    [State('auth-store', 'data')]
)
def drill_deep_dive_item(clk_item, clk_month, clk_cust, rep, year, view, h_col, auth_data):
    empty_res = go.Figure(), "", go.Figure(), ""
    df = get_secure_df(auth_data)
    if not rep or not clk_item: return empty_res

    cust_col = next((c for c in ['Customer Names', 'Customer', 'Client'] if c in df.columns), 'Customer Names')
    target_item_col = h_col if h_col in df.columns else 'Items'

    item_id = clk_item['points'][0]['y']
    cust_id = clk_cust['points'][0]['y'] if clk_cust else df[
        (df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
                    df['Date'].dt.year == int(year))].groupby(cust_col)['Value'].sum().nlargest(1).index[0]

    df_base = df[(df['Sales Rep'].astype(str).str.strip().str.upper() == str(rep).strip().upper()) & (
                df[cust_col].astype(str).str.strip().str.upper() == str(cust_id).strip().upper()) & (
                             df[target_item_col].astype(str).str.strip().str.upper() == str(item_id).strip().upper())]
    vol_col = 'QTY PCS' if METRIC_MODE == 'qty' else 'Weight'

    df_base_c, df_base_p, lbl = core_engine.get_time_window(df_base, year, 'Year')
    if df_base_c.empty: return empty_res

    monthly, xfmt, dtick = core_engine.get_trend_data(df_base_c, view, vol_col)
    header_m = s.get_chart_title(f"Trend: {item_id} (YTD)", df_base_c, df_base_p, METRIC_MODE)
    fig_m = s.create_bar_chart(monthly, 'Date', 'Value', header_m, vol_col, METRIC_MODE, use_z_colors=True)
    if not monthly.empty: fig_m.update_xaxes(tickformat=xfmt, dtick=dtick)

    fig_d, anno_d = go.Figure(), ""
    if clk_month:
        clk_d_str = clk_month['points'][0]['x']
        df_mc, df_mp, lbl_d = core_engine.get_time_window(df_base, year, view, clk_d_str)
        daily = core_engine.get_drilldown_data(df_mc, vol_col)

        header_d = s.get_chart_title(f"Daily: {lbl_d}", df_mc, df_mp, METRIC_MODE)
        fig_d = s.create_bar_chart(daily, 'Date', 'Value', header_d, vol_col, METRIC_MODE, use_z_colors=True)
        anno_d = s.get_storytelling_anno(daily, 'Date', 'Value', vol_col, METRIC_MODE)

    return fig_m, s.get_storytelling_anno(monthly, 'Date', 'Value', vol_col, METRIC_MODE), fig_d, anno_d


# --- (Keep your existing download_snapshot callback here at the bottom, no changes needed) ---
@callback(
    Output("rep-dl", "data"),
    Input("rep-snap", "n_clicks"),
    [State('team-chart', 'figure'), State('target-meter', 'figure'),
     State('rep-trend', 'figure'), State('rep-trend-daily', 'figure'),
     State('rep-prod', 'figure'), State('rep-prod-monthly', 'figure'), State('rep-prod-daily', 'figure'),
     State('rep-prod-variants', 'figure'),
     State('rep-cust', 'figure'), State('rep-cust-monthly', 'figure'), State('rep-cust-daily', 'figure'),
     State('cust-trend-chart', 'figure'), State('cust-top-items', 'figure'), State('cust-item-monthly', 'figure'),
     State('cust-item-daily', 'figure')],
    prevent_initial_call=True
)
def download_snapshot(n, *figures):
    import plotly.io as pio
    html_content = f"""
    <html>
    <head>
        <title>Sales Rep Snapshot</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; padding: 20px; background-color: #f4f4f4; }}
            .chart-container {{ background: white; margin-bottom: 20px; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            h1 {{ text-align: center; color: #2c3e50; }}
            .timestamp {{ text-align: center; color: #7f8c8d; font-size: 0.9em; margin-bottom: 30px; }}
        </style>
    </head>
    <body>
        <h1>Sales Rep Performance Report</h1>
        <p class="timestamp">Generated on {pd.Timestamp.now().strftime('%d-%b-%Y %H:%M')}</p>
    """
    for fig in figures:
        if fig and isinstance(fig, dict) and 'data' in fig and len(fig['data']) > 0:
            html_content += f'<div class="chart-container">{pio.to_html(go.Figure(fig), full_html=False, include_plotlyjs="cdn")}</div>'

    html_content += "</body></html>"
    return dict(content=html_content, filename=f"rep_snapshot_{pd.Timestamp.now().strftime('%Y%m%d')}.html")