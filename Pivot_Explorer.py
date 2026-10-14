import sys
import os
import io
import base64

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

import Assets.styles as s
import pandas as pd
import numpy as np
from dash import html, dcc, dash_table, Input, Output, State, ctx, callback, no_update
from dash.dash_table.Format import Format, Scheme, Group
import plotly.graph_objects as go
from data_loader import df_global, years_global, get_latest_data
from users import enforce_row_level_security

try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
DIM_COLS = [
    ('Name',            'Product Name'),
    ('Customer Names',  'Customer'),
    ('Items',           'Product Sub-Group'),
    ('Items 2',         'Product Group'),
    ('Item Category',   'Item Category'),
    ('Sales Rep',       'Sales Rep'),
    ('Rep Name',        'Rep Name'),
    ('Location',        'Location'),
    ('Customer Area',   'Customer Area'),
    ('WarehouseName',   'Warehouse'),
    ('B2B',             'Channel (B2B/B2C)'),
    ('Group',           'Group Code'),
    ('Pack Size Match', 'Pack Size Match'),
    ('Price_Flag',      'Price Flag'),
    ('Tran',            'Transaction Type'),
]

METRIC_COLS = [
    ('Value',                 'Revenue (Ksh)'),
    ('QTY PCS',               'Qty (PCS)'),
    ('Quantity',              'Quantity'),
    ('Weight',                'Weight (kg)'),
    ('Unit Price',            'Unit Price'),
    ('Avg_Price',             'Avg Price'),
    ('Rev_Per_Km',            'Revenue / km'),
    ('Distance from Nairobi', 'Distance (km)'),
    ('Price_Deviation_Pct',   'Price Deviation %'),
    ('Ref_Pct_Increase',      'Ref Price Increase %'),
    ('Weight_Per_Unit',       'Weight / Unit'),
]

MEAN_PREFERRED = {'Unit Price', 'Avg_Price', 'Price_Deviation_Pct',
                  'Ref_Pct_Increase', 'Weight_Per_Unit', 'Rev_Per_Km'}

DIM_OPTIONS    = [{'label': label, 'value': col} for col, label in DIM_COLS]
METRIC_OPTIONS = [{'label': label, 'value': col} for col, label in METRIC_COLS]

AGG_OPTIONS = [
    {'label': 'Sum',    'value': 'sum'},
    {'label': 'Mean',   'value': 'mean'},
    {'label': 'Count',  'value': 'count'},
    {'label': 'Median', 'value': 'median'},
    {'label': 'Max',    'value': 'max'},
    {'label': 'Min',    'value': 'min'},
]

TIME_BUCKET_OPTIONS = [
    {'label': 'None',     'value': 'none'},
    {'label': 'Weekly',   'value': 'W'},
    {'label': 'Monthly',  'value': 'M'},
    {'label': 'Quarterly','value': 'Q'},
    {'label': 'Yearly',   'value': 'Y'},
]

# ── Feature definitions ────────────────────────────────────────────────────────
FEATURE_META = {
    'market_share': {
        'label': '📊 Market Share %',
        'short': "Each row's % of the grand total for the primary metric.",
        'increase': 'This segment is growing in dominance. Protect and invest — it drives the most value.',
        'decrease': 'Share is eroding. Investigate competitive pressure, pricing changes, or lost accounts.',
        'formula':  'row_value ÷ total × 100',
        'col': 'Mkt Share %',
    },
    'cagr': {
        'label': '📈 CAGR %',
        'short': 'Compound Annual Growth Rate — the smoothed year-on-year growth from first to last year in view.',
        'increase': 'Strong multi-year momentum. Reliable growth driver worth further investment.',
        'decrease': 'Multi-year decline — not a blip. Requires root cause analysis (lost customers, pricing, or market shift).',
        'formula':  '(V_last / V_first)^(1 / n_years) − 1 × 100',
        'col': 'CAGR %',
    },
    'velocity': {
        'label': '⚡ Transaction Velocity',
        'short': 'How frequently this row generates transactions — Distinct order days and average orders per month.',
        'increase': 'Regular, sticky buying behaviour. High-value relationship or fast-moving product. Lower fulfilment risk.',
        'decrease': 'Sporadic ordering. Could signal seasonal product, at-risk customer, or substitution by a competitor.',
        'formula':  'Distinct order dates in period; Tx/Month = days ÷ (date_range / 30)',
        'col': 'Tx Days',
    },
    'hhi': {
        'label': '🎯 HHI Concentration',
        'short': 'Herfindahl–Hirschman Index — measures how concentrated revenue is across rows. Range: 0 (perfect spread) → 1 (one player holds all).',
        'increase': 'Revenue is highly concentrated (>0.25 = warning). Losing one top customer/product is catastrophic.',
        'decrease': 'Revenue is well diversified. More resilient to individual losses, but harder to build strategic depth.',
        'formula':  'Σ (share_i)² where share_i = row_value / total',
    },
    'outliers': {
        'label': '🔴 Outlier Flagging',
        'short': 'Flags rows where any selected metric is more than 2 standard deviations from the mean (high or low).',
        'increase': 'Exceptionally high — could be a pricing anomaly, bulk order, or a star performer. Verify it is intentional.',
        'decrease': 'Exceptionally low — structural underperformance, data error, or dormant relationship worth investigating.',
        'formula':  'value > mean + 2σ → "🔴 High Outlier" | value < mean − 2σ → "🔵 Low Outlier"',
        'col': 'Outlier',
    },
    'sparklines': {
        'label': '✨ Trend Sparklines',
        'short': 'Mini 8-period Unicode trend line (last 8 months of the primary metric) appended as a column.',
        'increase': '▁▂▄▆▇█ — Accelerating momentum. Likely to sustain unless disrupted.',
        'decrease': '█▇▅▃▂▁ — Decelerating trend. Early warning — act before it shows in totals.',
        'formula':  'Monthly totals, last 8 months mapped to ▁▂▃▄▅▆▇█ Unicode blocks',
        'col': 'Trend ▸',
    },
    'crosstab': {
        'label': '⊞ Cross-Tab Matrix',
        'short': 'Pivot matrix showing Row Dimension × Sub-Dimension (requires Sub Dim to be set). Reveals intersectional performance.',
        'increase': 'High-value intersection — this combination drives disproportionate value. Double down.',
        'decrease': 'Empty or low cells — unexplored cross-selling territory or territory gaps worth targeting.',
        'formula':  'pivot_table(index=row_dim, columns=sub_dim, values=metric, aggfunc)',
    },
    'waterfall': {
        'label': '🌊 Waterfall Chart',
        'short': 'Visualises the top contributors to change between Period A and Period B in the Variance tab.',
        'increase': 'Long green bars — these are your growth engines. Scale what is working here.',
        'decrease': 'Long red bars — these are the biggest detractors. Immediate diagnostic priority.',
        'formula':  'Period B value − Period A value, sorted descending, top/bottom N shown',
    },
    'time_bucket': {
        'label': '📅 Time Bucketing',
        'short': 'Groups the Date column into Weekly, Monthly, Quarterly, or Yearly periods as an additional pivot dimension.',
        'increase': 'Seasonal peaks visible. Plan procurement and staffing around them.',
        'decrease': 'Troughs reveal quiet periods. Opportunity for promotions or early-order incentives.',
        'formula':  'Date → period string (e.g. 2024-Q1, 2024-Jan)',
    },
    'presets': {
        'label': '💾 Saved Presets',
        'short': 'Save the current pivot configuration (dimensions, metrics, filters) with a name and reload it instantly.',
        'increase': 'N/A — organisational tool. Enables consistent weekly/monthly reporting without manual reconfiguration.',
        'decrease': 'N/A',
        'formula':  'JSON config stored in browser session (dcc.Store)',
    },
}

FEATURE_CHECKLIST_OPTIONS = [
    {'label': FEATURE_META[k]['label'], 'value': k}
    for k in FEATURE_META
]

# Colours
C_DARK   = '#2c3e50'
C_BLUE   = '#3498db'
C_GREEN  = '#27ae60'
C_RED    = '#e74c3c'
C_AMBER  = '#f39c12'
C_BG     = '#f4f6f9'
C_CARD   = '#ffffff'
C_BORDER = '#e0e0e0'

LBL  = {'fontWeight': 'bold', 'fontSize': '11px', 'color': '#7f8c8d',
         'marginRight': '5px', 'textTransform': 'uppercase'}
CTRL = {'display': 'inline-block', 'marginRight': '16px', 'verticalAlign': 'top',
        'marginBottom': '8px'}


# ══════════════════════════════════════════════════════════════════════════════
#  SECURITY HELPER
# ══════════════════════════════════════════════════════════════════════════════
def _get_df(auth_data) -> pd.DataFrame:
    _df, _, _, _ = get_latest_data()
    df = enforce_row_level_security(_df, auth_data)
    df = df.copy()
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    return df.dropna(subset=['Date'])


# ══════════════════════════════════════════════════════════════════════════════
#  ANALYTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def _compute_pivot(df, row_dim, sub_dim, metrics, agg_func, time_bucket='none'):
    dims = [row_dim]
    if sub_dim and sub_dim != row_dim:
        dims.append(sub_dim)
    if time_bucket and time_bucket != 'none':
        df = df.copy()
        df['_Period'] = df['Date'].dt.to_period(time_bucket).astype(str)
        dims.append('_Period')

    agg_map = {}
    for m in metrics:
        if m not in df.columns:
            continue
        if agg_func == 'smart':
            agg_map[m] = 'mean' if m in MEAN_PREFERRED else 'sum'
        else:
            agg_map[m] = agg_func

    if not agg_map:
        return pd.DataFrame()

    return df.groupby(dims, dropna=False).agg(agg_map).reset_index()


def _add_core_analytics(pivot, primary_col):
    """Add Rank, Contribution %, Cumulative %, ABC to any pivot."""
    if primary_col not in pivot.columns or pivot.empty:
        return pivot
    pivot = pivot.copy().sort_values(primary_col, ascending=False).reset_index(drop=True)
    total = pivot[primary_col].sum()
    pivot.insert(0, 'Rank', pivot.index + 1)
    if total:
        pivot['Contribution %'] = (pivot[primary_col] / total * 100).round(2)
        pivot['Cumulative %']   = pivot['Contribution %'].cumsum().round(2)
        pivot['ABC'] = pivot['Cumulative %'].apply(
            lambda x: 'A' if x <= 80 else ('B' if x <= 95 else 'C'))
    return pivot


def _add_market_share(pivot, primary_col):
    total = pivot[primary_col].sum()
    if total:
        pivot['Mkt Share %'] = (pivot[primary_col] / total * 100).round(2)
    return pivot


def _add_outlier_flags(pivot, metrics):
    """Flag rows that are >2σ from the mean on any selected metric."""
    flags = pd.Series([''] * len(pivot))
    for col in metrics:
        if col not in pivot.columns:
            continue
        vals = pd.to_numeric(pivot[col], errors='coerce')
        mean, std = vals.mean(), vals.std()
        if std == 0:
            continue
        high_mask = vals > mean + 2 * std
        low_mask  = vals < mean - 2 * std
        flags[high_mask & (flags == '')] = '🔴 High Outlier'
        flags[low_mask  & (flags == '')] = '🔵 Low Outlier'
    pivot['Outlier'] = flags.values
    return pivot


def _add_sparklines(df_raw, pivot, row_dim, primary_col):
    """Append Unicode sparkline for last 8 calendar months."""
    if primary_col not in df_raw.columns or row_dim not in df_raw.columns:
        return pivot
    blocks = '▁▂▃▄▅▆▇█'
    df_sp = df_raw.copy()
    df_sp['_YM'] = df_sp['Date'].dt.to_period('M')
    monthly = (df_sp.groupby([row_dim, '_YM'])[primary_col]
               .sum().reset_index()
               .sort_values('_YM'))

    def _spark(vals):
        v = list(vals)[-8:]
        lo, hi = min(v), max(v)
        if hi == lo:
            return '▄' * len(v)
        return ''.join(blocks[round((x - lo) / (hi - lo) * 7)] for x in v)

    sparks = (monthly.groupby(row_dim)[primary_col]
              .apply(_spark).reset_index()
              .rename(columns={primary_col: 'Trend ▸'}))
    return pivot.merge(sparks, on=row_dim, how='left')


def _add_cagr(df_raw, pivot, row_dim, primary_col):
    """Compute CAGR % from first to last year available per dimension row."""
    if primary_col not in df_raw.columns or row_dim not in df_raw.columns:
        return pivot
    df_yr = df_raw.copy()
    df_yr['_Year'] = df_yr['Date'].dt.year
    yr_pivot = (df_yr.groupby([row_dim, '_Year'])[primary_col]
                .sum().reset_index()
                .pivot_table(index=row_dim, columns='_Year',
                             values=primary_col, fill_value=0))

    def _cagr(row):
        nonzero = row[row > 0]
        if len(nonzero) < 2:
            return np.nan
        n = nonzero.index[-1] - nonzero.index[0]
        if n == 0:
            return np.nan
        return round(((nonzero.iloc[-1] / nonzero.iloc[0]) ** (1 / n) - 1) * 100, 2)

    cagr_s = yr_pivot.apply(_cagr, axis=1).reset_index(name='CAGR %')
    return pivot.merge(cagr_s, on=row_dim, how='left')


def _add_velocity(df_raw, pivot, row_dim):
    """Add Tx Days and Tx/Month (transaction frequency) columns."""
    if row_dim not in df_raw.columns:
        return pivot
    date_range = (df_raw['Date'].max() - df_raw['Date'].min()).days + 1
    vel = (df_raw.groupby(row_dim)
           .agg(Tx_Days=('Date', lambda x: x.dt.date.nunique()))
           .reset_index())
    vel['Tx/Month'] = (vel['Tx_Days'] / max(date_range / 30, 1)).round(1)
    return pivot.merge(vel, on=row_dim, how='left')


def _compute_hhi(pivot, primary_col) -> float:
    """Herfindahl–Hirschman Index. Returns value 0–1."""
    if primary_col not in pivot.columns:
        return 0.0
    total = pivot[primary_col].sum()
    if total == 0:
        return 0.0
    shares = (pivot[primary_col] / total)
    return round(float((shares ** 2).sum()), 4)


def _compute_crosstab(df_raw, row_dim, sub_dim, primary_col, agg_func):
    """Build a true pivot matrix (rows × columns)."""
    if not sub_dim or sub_dim == row_dim or primary_col not in df_raw.columns:
        return pd.DataFrame()
    agg = agg_func if agg_func != 'smart' else (
        'mean' if primary_col in MEAN_PREFERRED else 'sum')
    try:
        ct = df_raw.pivot_table(
            index=row_dim, columns=sub_dim,
            values=primary_col, aggfunc=agg, fill_value=0
        ).reset_index()
        ct.columns.name = None
        return ct
    except Exception:
        return pd.DataFrame()


def _unicode_sparkline(values, length=8):
    blocks = '▁▂▃▄▅▆▇█'
    v = list(values)[-length:]
    if not v:
        return ''
    lo, hi = min(v), max(v)
    if hi == lo:
        return '▄' * len(v)
    return ''.join(blocks[round((x - lo) / (hi - lo) * 7)] for x in v)


def _descriptive_stats(df, metrics):
    rows = []
    for col in metrics:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors='coerce').dropna()
        if s.empty:
            continue
        rows.append({
            'Metric':   col,
            'Count':    int(s.count()),
            'Sum':      round(s.sum(), 2),
            'Mean':     round(s.mean(), 2),
            'Median':   round(s.median(), 2),
            'Std Dev':  round(s.std(), 2),
            'Min':      round(s.min(), 2),
            'P25':      round(s.quantile(.25), 2),
            'P75':      round(s.quantile(.75), 2),
            'Max':      round(s.max(), 2),
            'IQR':      round(s.quantile(.75) - s.quantile(.25), 2),
            'Skewness': round(s.skew(), 3),
            'CV %':     round(s.std() / s.mean() * 100, 2) if s.mean() != 0 else 0,
        })
    return pd.DataFrame(rows)


def _make_heatmap_styles(df, numeric_cols):
    styles = []
    for col in numeric_cols:
        if col not in df.columns:
            continue
        data = pd.to_numeric(df[col].head(500), errors='coerce').fillna(0)
        lo, hi = data.min(), data.max()
        if hi == lo:
            continue
        for i, val in enumerate(data):
            pct = (val - lo) / (hi - lo)
            r = int(255 - pct * 85)
            g = int(195 + pct * 60)
            b = int(195 - pct * 140)
            styles.append({'if': {'row_index': i, 'column_id': col},
                           'backgroundColor': f'rgb({r},{g},{b})'})
    return styles


def _make_datatable(df, tid, heatmap=False, page_size=25):
    if df is None or df.empty:
        return html.Div("No data to display.",
                        style={'color': '#aaa', 'padding': '20px', 'textAlign': 'center'})

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    cols = []
    for c in df.columns:
        if c in numeric_cols:
            cols.append({'name': c, 'id': c, 'type': 'numeric',
                         'format': Format(group=Group.yes).scheme(Scheme.fixed).precision(2)})
        else:
            cols.append({'name': c, 'id': c})

    cond = [
        {'if': {'row_index': 'odd'}, 'backgroundColor': '#fafbfc'},
        {'if': {'state': 'selected'}, 'backgroundColor': '#d6eaf8',
         'border': f'1px solid {C_BLUE}'},
    ]
    if 'ABC' in df.columns:
        cond += [
            {'if': {'filter_query': '{ABC} = "A"'}, 'backgroundColor': '#eafaf1'},
            {'if': {'filter_query': '{ABC} = "B"'}, 'backgroundColor': '#fef9e7'},
            {'if': {'filter_query': '{ABC} = "C"'}, 'backgroundColor': '#fdedec'},
        ]
    if 'Outlier' in df.columns:
        cond += [
            {'if': {'filter_query': '{Outlier} = "🔴 High Outlier"'},
             'backgroundColor': '#fde8e8', 'fontWeight': 'bold'},
            {'if': {'filter_query': '{Outlier} = "🔵 Low Outlier"'},
             'backgroundColor': '#e8f4fd', 'fontWeight': 'bold'},
        ]
    if heatmap:
        cond += _make_heatmap_styles(df, numeric_cols)

    return dash_table.DataTable(
        id=tid,
        columns=cols,
        data=df.to_dict('records'),
        sort_action='native',
        filter_action='native',
        page_action='native',
        page_size=page_size,
        style_table={'overflowX': 'auto', 'minWidth': '100%'},
        style_header={'backgroundColor': C_DARK, 'color': 'white',
                      'fontWeight': 'bold', 'fontSize': '12px',
                      'textAlign': 'center', 'padding': '10px 8px',
                      'border': f'1px solid {C_BORDER}'},
        style_cell={'fontSize': '12px', 'padding': '7px 10px',
                    'border': f'1px solid {C_BORDER}',
                    'textAlign': 'left', 'minWidth': '70px',
                    'maxWidth': '200px', 'overflow': 'hidden',
                    'textOverflow': 'ellipsis'},
        style_cell_conditional=[
            {'if': {'column_type': 'numeric'}, 'textAlign': 'right'},
        ],
        style_data_conditional=cond,
    )


def _kpi_chip(label, value, color=C_DARK, hint=''):
    return html.Div([
        html.Div(label, style={'fontSize': '11px', 'fontWeight': 'bold',
                               'color': '#7f8c8d', 'textTransform': 'uppercase'}),
        html.Div(str(value), style={'fontSize': '22px', 'fontWeight': 'bold',
                                    'color': color, 'margin': '4px 0'}),
        html.Div(hint, style={'fontSize': '11px', 'color': '#95a5a6'}) if hint else None,
    ], style={'backgroundColor': C_CARD, 'border': f'1px solid {C_BORDER}',
              'borderLeft': f'4px solid {color}',
              'borderRadius': '6px', 'padding': '12px 16px',
              'display': 'inline-block', 'minWidth': '140px', 'marginRight': '12px'})


# ══════════════════════════════════════════════════════════════════════════════
#  EXCEL BUILDER
# ══════════════════════════════════════════════════════════════════════════════
def _build_excel(sheets: dict) -> bytes:
    """sheets = {sheet_name: DataFrame}. Returns formatted .xlsx bytes."""
    buffer = io.BytesIO()

    if not OPENPYXL_OK:
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            for name, df in sheets.items():
                if df is not None and not df.empty:
                    df.to_excel(writer, sheet_name=name[:31], index=False)
        return buffer.getvalue()

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    hdr_fill  = PatternFill('solid', fgColor='2C3E50')
    hdr_font  = Font(bold=True, color='FFFFFF', size=11)
    hdr_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    odd_fill  = PatternFill('solid', fgColor='F2F3F4')
    evn_fill  = PatternFill('solid', fgColor='FFFFFF')
    a_fill    = PatternFill('solid', fgColor='D5F5E3')
    b_fill    = PatternFill('solid', fgColor='FEF9E7')
    c_fill    = PatternFill('solid', fgColor='FDEDEC')
    out_h_fill = PatternFill('solid', fgColor='FDE8E8')
    out_l_fill = PatternFill('solid', fgColor='E8F4FD')
    tot_fill  = PatternFill('solid', fgColor='D6EAF8')
    side      = Side(style='thin', color='E0E0E0')
    border    = Border(left=side, right=side, top=side, bottom=side)

    for raw_name, df in sheets.items():
        name = raw_name[:31]
        if df is None or df.empty:
            ws = wb.create_sheet(name)
            ws.append(['No data'])
            continue

        ws = wb.create_sheet(name)
        ws.append(list(df.columns))
        for cell in ws[1]:
            cell.fill, cell.font, cell.alignment, cell.border = (
                hdr_fill, hdr_font, hdr_align, border)
        ws.freeze_panes = 'A2'
        ws.row_dimensions[1].height = 28

        for r_idx, row in enumerate(df.itertuples(index=False), start=2):
            ws.append(list(row))
            abc = str(getattr(row, 'ABC', '')) if 'ABC' in df.columns else ''
            out = str(getattr(row, 'Outlier', '')) if 'Outlier' in df.columns else ''
            base_fill = odd_fill if r_idx % 2 == 0 else evn_fill

            for c_idx, cell in enumerate(ws[r_idx], start=1):
                col_name = df.columns[c_idx - 1]
                cell.border = border
                # Priority: outlier > ABC > alternating
                if '🔴' in out:    cell.fill = out_h_fill
                elif '🔵' in out:  cell.fill = out_l_fill
                elif abc == 'A':   cell.fill = a_fill
                elif abc == 'B':   cell.fill = b_fill
                elif abc == 'C':   cell.fill = c_fill
                else:              cell.fill = base_fill

                if pd.api.types.is_numeric_dtype(df[col_name]):
                    try:
                        fv = float(cell.value) if cell.value is not None else 0
                        if '%' in col_name or 'Pct' in col_name:
                            cell.number_format = '0.00"%"'
                        elif abs(fv) >= 1000:
                            cell.number_format = '#,##0.00'
                        else:
                            cell.number_format = '0.00'
                    except (TypeError, ValueError):
                        pass

        # Auto column widths
        for col_cells in ws.columns:
            max_len = max(
                (len(str(c.value)) if c.value is not None else 0) for c in col_cells)
            ws.column_dimensions[get_column_letter(
                col_cells[0].column)].width = min(max_len + 4, 42)

        # Color scale on numeric columns
        numeric_idxs = [i + 1 for i, c in enumerate(df.columns)
                        if pd.api.types.is_numeric_dtype(df[c])]
        if len(df) > 1:
            for ci in numeric_idxs:
                cl = get_column_letter(ci)
                ws.conditional_formatting.add(
                    f'{cl}2:{cl}{len(df) + 1}',
                    ColorScaleRule(
                        start_type='min',  start_color='FDEDEC',
                        mid_type='percentile', mid_value=50, mid_color='FFF9C4',
                        end_type='max',    end_color='D5F5E3',
                    ))

        # Grand total footer
        total_row = []
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                total_row.append(round(df[col].sum(), 2))
            elif col == df.columns[0]:
                total_row.append('GRAND TOTAL')
            else:
                total_row.append('')
        ws.append(total_row)
        for cell in ws[ws.max_row]:
            cell.fill   = tot_fill
            cell.font   = Font(bold=True, color='2C3E50')
            cell.border = border

    wb.save(buffer)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  BUSINESS INTELLIGENCE GUIDE COMPONENT
# ══════════════════════════════════════════════════════════════════════════════
def _build_guide():
    """Build the collapsible BI guide table."""
    rows = []
    for k, meta in FEATURE_META.items():
        rows.append(html.Tr([
            html.Td(meta['label'],
                    style={'fontWeight': 'bold', 'fontSize': '12px',
                           'whiteSpace': 'nowrap', 'padding': '8px 12px',
                           'borderBottom': f'1px solid {C_BORDER}'}),
            html.Td(meta['short'],
                    style={'fontSize': '12px', 'padding': '8px 12px',
                           'color': '#555', 'borderBottom': f'1px solid {C_BORDER}'}),
            html.Td(meta.get('formula', ''),
                    style={'fontSize': '11px', 'fontFamily': 'monospace',
                           'color': C_BLUE, 'padding': '8px 12px',
                           'borderBottom': f'1px solid {C_BORDER}'}),
            html.Td([
                html.Span('▲ ', style={'color': C_GREEN, 'fontWeight': 'bold'}),
                meta.get('increase', ''),
            ], style={'fontSize': '12px', 'padding': '8px 12px',
                      'borderBottom': f'1px solid {C_BORDER}',
                      'maxWidth': '280px'}),
            html.Td([
                html.Span('▼ ', style={'color': C_RED, 'fontWeight': 'bold'}),
                meta.get('decrease', ''),
            ], style={'fontSize': '12px', 'padding': '8px 12px',
                      'borderBottom': f'1px solid {C_BORDER}',
                      'maxWidth': '280px'}),
        ]))

    return html.Details([
        html.Summary(
            "📚  Business Intelligence Guide — What each metric means & how to act on it",
            style={'cursor': 'pointer', 'fontWeight': 'bold', 'fontSize': '13px',
                   'color': C_DARK, 'padding': '8px 0', 'userSelect': 'none'}
        ),
        html.Div([
            html.Table([
                html.Thead(html.Tr([
                    html.Th('Feature',  style={'width': '160px'}),
                    html.Th('What it measures'),
                    html.Th('Formula', style={'width': '200px'}),
                    html.Th('▲ Higher means…', style={'width': '280px'}),
                    html.Th('▼ Lower means…', style={'width': '280px'}),
                ], style={'backgroundColor': C_DARK, 'color': 'white',
                          'fontSize': '12px', 'fontWeight': 'bold'})),
                html.Tbody(rows),
            ], style={'width': '100%', 'borderCollapse': 'collapse',
                      'tableLayout': 'fixed'}),
        ], style={'overflowX': 'auto', 'marginTop': '10px',
                  'border': f'1px solid {C_BORDER}', 'borderRadius': '4px'}),
    ], style={'marginTop': '12px'})


# ══════════════════════════════════════════════════════════════════════════════
#  LAYOUT
# ══════════════════════════════════════════════════════════════════════════════
layout = html.Div([

    # Stores & Downloads
    dcc.Store(id='pv-pivot-store'),
    dcc.Store(id='pv-cross-store'),
    dcc.Store(id='pv-presets-store', storage_type='local'),
    dcc.Download(id='pv-dl'),

    # ── Header ──────────────────────────────────────────────────────────────
    html.Div([
        html.Div([
            html.H1("📊 Data Explorer & Pivot Builder",
                    style={**s.HEADER_TITLE, 'marginBottom': '4px'}),
            html.P("Build cross-sectional pivots, apply analytics features, compare periods, "
                   "run ABC/Pareto analysis, and export formatted Excel workbooks.",
                   style={'textAlign': 'center', 'color': '#7f8c8d',
                          'fontSize': '13px', 'margin': '0'}),
        ], style={'flex': 1}),
        html.Button("📥 Download Excel", id='pv-dl-btn',
                    style={**s.BTN_STYLE, 'backgroundColor': C_GREEN,
                           'fontSize': '13px', 'marginLeft': '20px'}),
    ], style={'display': 'flex', 'alignItems': 'center',
              'justifyContent': 'space-between', 'marginBottom': '14px'}),

    # ── Pivot Controls ───────────────────────────────────────────────────────
    html.Div([
        html.H4("⚙️  Pivot Controls",
                style={'color': C_DARK, 'fontSize': '13px',
                       'fontWeight': 'bold', 'marginBottom': '10px'}),
        html.Div([
            html.Div([
                html.Span("Row Dimension", style=LBL),
                dcc.Dropdown(id='pv-row-dim', options=DIM_OPTIONS, value='Items 2',
                             clearable=False,
                             style={'width': '200px', 'fontSize': '13px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Sub Dimension (optional)", style=LBL),
                dcc.Dropdown(id='pv-sub-dim',
                             options=[{'label': 'None', 'value': ''}] + DIM_OPTIONS,
                             value='', clearable=False,
                             style={'width': '200px', 'fontSize': '13px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Metrics", style=LBL),
                dcc.Dropdown(id='pv-metrics', options=METRIC_OPTIONS,
                             value=['Value', 'QTY PCS'], multi=True,
                             style={'width': '300px', 'fontSize': '13px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Aggregation", style=LBL),
                dcc.Dropdown(id='pv-agg', options=AGG_OPTIONS, value='sum',
                             clearable=False,
                             style={'width': '110px', 'fontSize': '13px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Top N Rows", style=LBL),
                dcc.Input(id='pv-top-n', type='number', value=50, min=5, max=1000, step=5,
                          style={'width': '70px', 'fontSize': '13px', 'padding': '5px',
                                 'border': f'1px solid {C_BORDER}',
                                 'borderRadius': '4px'}),
            ], style=CTRL),
        ], style={'display': 'flex', 'flexWrap': 'wrap', 'marginBottom': '10px'}),

        html.Div([
            html.Div([
                html.Span("Date From", style=LBL),
                dcc.DatePickerSingle(id='pv-date-from', display_format='DD/MM/YYYY'),
            ], style=CTRL),
            html.Div([
                html.Span("Date To", style=LBL),
                dcc.DatePickerSingle(id='pv-date-to', display_format='DD/MM/YYYY'),
            ], style=CTRL),
            html.Div([
                html.Span("Year(s)", style=LBL),
                dcc.Dropdown(id='pv-years', options=[], value=[], multi=True,
                             placeholder='All years…',
                             style={'width': '200px', 'fontSize': '13px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Time Bucket", style=LBL),
                dcc.RadioItems(id='pv-time-bucket',
                               options=TIME_BUCKET_OPTIONS, value='none',
                               inline=True,
                               style={'fontSize': '12px', 'marginTop': '4px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Heatmap Cells", style=LBL),
                dcc.RadioItems(id='pv-heatmap',
                               options=[{'label': 'On', 'value': True},
                                        {'label': 'Off', 'value': False}],
                               value=True, inline=True,
                               style={'fontSize': '12px', 'marginTop': '4px'}),
            ], style=CTRL),
        ], style={'display': 'flex', 'flexWrap': 'wrap'}),

    ], style={**s.SECTION, 'marginBottom': '10px'}),

    # ── Dimension Filters ────────────────────────────────────────────────────
    html.Div([
        html.H4("🔍  Dimension Filters",
                style={'color': C_DARK, 'fontSize': '13px',
                       'fontWeight': 'bold', 'marginBottom': '10px'}),
        html.Div([
            html.Div([
                html.Span("Sales Rep", style=LBL),
                dcc.Dropdown(id='pv-f-rep', options=[], multi=True,
                             placeholder='All reps…',
                             style={'width': '190px', 'fontSize': '12px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Customer", style=LBL),
                dcc.Dropdown(id='pv-f-cust', options=[], multi=True,
                             placeholder='All customers…',
                             style={'width': '210px', 'fontSize': '12px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Product Sub-Group", style=LBL),
                dcc.Dropdown(id='pv-f-item', options=[], multi=True,
                             placeholder='All products…',
                             style={'width': '200px', 'fontSize': '12px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Location", style=LBL),
                dcc.Dropdown(id='pv-f-loc', options=[], multi=True,
                             placeholder='All locations…',
                             style={'width': '190px', 'fontSize': '12px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Warehouse", style=LBL),
                dcc.Dropdown(id='pv-f-wh', options=[], multi=True,
                             placeholder='All warehouses…',
                             style={'width': '175px', 'fontSize': '12px'}),
            ], style=CTRL),
            html.Div([
                html.Span("Channel", style=LBL),
                dcc.RadioItems(id='pv-f-b2b',
                               options=[{'label': 'All', 'value': 'All'},
                                        {'label': 'B2B', 'value': 'B2B'},
                                        {'label': 'B2C', 'value': 'B2C'}],
                               value='All', inline=True,
                               style={'fontSize': '12px', 'marginTop': '4px'}),
            ], style=CTRL),
        ], style={'display': 'flex', 'flexWrap': 'wrap'}),
    ], style={**s.SECTION, 'marginBottom': '10px'}),

    # ── Analytics Features ───────────────────────────────────────────────────
    html.Div([
        html.H4("🧪  Analytics Features",
                style={'color': C_DARK, 'fontSize': '13px',
                       'fontWeight': 'bold', 'marginBottom': '8px'}),
        html.P("Toggle the analytical columns and views you want added to the output. "
               "Each selected feature is computed automatically when you click Build Pivot.",
               style={'fontSize': '12px', 'color': '#7f8c8d',
                      'marginBottom': '10px', 'marginTop': '0'}),

        dcc.Checklist(
            id='pv-features',
            options=FEATURE_CHECKLIST_OPTIONS,
            value=['market_share', 'outliers'],
            inline=True,
            inputStyle={'marginRight': '5px'},
            labelStyle={'marginRight': '20px', 'fontSize': '13px',
                        'cursor': 'pointer', 'userSelect': 'none'},
        ),

        # BI Guide (collapsible)
        _build_guide(),

        # ── Saved Presets (shown when 'presets' feature checked) ──
        html.Div(id='pv-presets-panel', children=[
            html.Hr(style={'margin': '12px 0', 'borderColor': C_BORDER}),
            html.H5("💾  Saved Presets",
                    style={'color': C_DARK, 'fontSize': '13px',
                           'fontWeight': 'bold', 'marginBottom': '8px'}),
            html.Div([
                dcc.Input(id='pv-preset-name', type='text',
                          placeholder='Preset name…',
                          style={'fontSize': '13px', 'padding': '6px',
                                 'border': f'1px solid {C_BORDER}',
                                 'borderRadius': '4px', 'marginRight': '8px',
                                 'width': '200px'}),
                html.Button("Save", id='pv-save-preset', n_clicks=0,
                            style={**s.BTN_STYLE, 'fontSize': '12px',
                                   'padding': '6px 14px', 'marginRight': '16px'}),
                dcc.Dropdown(id='pv-load-preset', options=[], value=None,
                             placeholder='Load a preset…',
                             style={'width': '220px', 'fontSize': '13px',
                                    'display': 'inline-block',
                                    'verticalAlign': 'middle'}),
                html.Button("Load", id='pv-load-btn', n_clicks=0,
                            style={**s.BTN_STYLE, 'fontSize': '12px',
                                   'padding': '6px 14px', 'marginLeft': '8px',
                                   'backgroundColor': C_AMBER}),
            ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap'}),
            html.Div(id='pv-preset-status',
                     style={'fontSize': '12px', 'color': C_GREEN,
                            'marginTop': '6px'}),
        ], style={'display': 'none'}),

    ], style={**s.SECTION, 'marginBottom': '10px'}),

    # ── Build Button ─────────────────────────────────────────────────────────
    html.Div([
        html.Button("▶  Build Pivot", id='pv-build-btn', n_clicks=0,
                    style={**s.BTN_STYLE, 'fontSize': '14px',
                           'padding': '10px 32px',
                           'backgroundColor': C_BLUE}),
        html.Span(id='pv-status',
                  style={'marginLeft': '16px', 'fontSize': '13px',
                         'color': '#7f8c8d'}),
    ], style={'textAlign': 'center', 'marginBottom': '14px'}),

    # ── KPI strip (HHI + summary) ─────────────────────────────────────────
    html.Div(id='pv-kpi-strip', style={'marginBottom': '10px'}),

    # ── Output Tabs ──────────────────────────────────────────────────────────
    html.Div([
        dcc.Tabs(id='pv-tabs', value='tab-pivot', children=[
            dcc.Tab(label='📊  Pivot Table',      value='tab-pivot',
                    style={'fontSize': '13px'},
                    selected_style={'fontSize': '13px', 'fontWeight': 'bold'}),
            dcc.Tab(label='📈  Variance / Waterfall', value='tab-variance',
                    style={'fontSize': '13px'},
                    selected_style={'fontSize': '13px', 'fontWeight': 'bold'}),
            dcc.Tab(label='🎯  Pareto / ABC',     value='tab-pareto',
                    style={'fontSize': '13px'},
                    selected_style={'fontSize': '13px', 'fontWeight': 'bold'}),
            dcc.Tab(label='📉  Statistics',       value='tab-stats',
                    style={'fontSize': '13px'},
                    selected_style={'fontSize': '13px', 'fontWeight': 'bold'}),
            dcc.Tab(label='⊞  Cross-Tab Matrix',  value='tab-cross',
                    style={'fontSize': '13px'},
                    selected_style={'fontSize': '13px', 'fontWeight': 'bold'}),
        ]),

        # Tab 1 – Pivot
        html.Div(id='pv-tab-pivot',
                 children=[html.Div(id='pv-pivot-out', style={'marginTop': '10px'})],
                 style={'padding': '10px'}),

        # Tab 2 – Variance + Waterfall
        html.Div(id='pv-tab-variance', children=[
            html.Div([
                html.H4("Compare Two Periods",
                        style={'color': C_DARK, 'fontSize': '13px',
                               'fontWeight': 'bold', 'marginBottom': '10px'}),
                html.Div([
                    html.Div([html.Span("Period A — From", style=LBL),
                              dcc.DatePickerSingle(id='pv-va-from',
                                                   display_format='DD/MM/YYYY')],
                             style=CTRL),
                    html.Div([html.Span("Period A — To", style=LBL),
                              dcc.DatePickerSingle(id='pv-va-to',
                                                   display_format='DD/MM/YYYY')],
                             style=CTRL),
                    html.Div([html.Span("Period B — From", style=LBL),
                              dcc.DatePickerSingle(id='pv-vb-from',
                                                   display_format='DD/MM/YYYY')],
                             style=CTRL),
                    html.Div([html.Span("Period B — To", style=LBL),
                              dcc.DatePickerSingle(id='pv-vb-to',
                                                   display_format='DD/MM/YYYY')],
                             style=CTRL),
                    html.Div([
                        html.Button("⚡ Compare", id='pv-var-btn', n_clicks=0,
                                    style={**s.BTN_STYLE, 'fontSize': '12px',
                                           'marginTop': '18px'})],
                             style=CTRL),
                ], style={'display': 'flex', 'flexWrap': 'wrap'}),
                html.P("Δ Abs = Period B − Period A  |  Δ % = change relative to Period A  |  "
                       "🌊 Waterfall chart shown below when Waterfall feature is enabled.",
                       style={'fontSize': '12px', 'color': '#7f8c8d', 'marginTop': '6px'}),
            ], style={**s.SECTION, 'marginBottom': '10px'}),
            html.Div(id='pv-var-out'),
            dcc.Graph(id='pv-waterfall-fig', config={'displayModeBar': False},
                      style={'height': '440px', 'marginTop': '14px'}),
        ], style={'padding': '10px', 'display': 'none'}),

        # Tab 3 – Pareto / ABC
        html.Div(id='pv-tab-pareto', children=[
            html.Div([
                html.Span("Primary Metric for Pareto", style=LBL),
                dcc.Dropdown(id='pv-pareto-metric', options=METRIC_OPTIONS,
                             value='Value', clearable=False,
                             style={'width': '200px', 'fontSize': '13px',
                                    'display': 'inline-block',
                                    'verticalAlign': 'middle'}),
            ], style={'padding': '10px 10px 0'}),
            dcc.Graph(id='pv-pareto-fig', config={'displayModeBar': False},
                      style={'height': '420px'}),
            html.Div(id='pv-pareto-table-out',
                     style={'marginTop': '10px', 'padding': '0 10px'}),
        ], style={'padding': '6px', 'display': 'none'}),

        # Tab 4 – Statistics
        html.Div(id='pv-tab-stats', children=[
            html.Div(id='pv-stats-out', style={'marginTop': '10px'}),
            dcc.Graph(id='pv-box-fig', config={'displayModeBar': False},
                      style={'height': '380px', 'marginTop': '14px'}),
        ], style={'padding': '10px', 'display': 'none'}),

        # Tab 5 – Cross-Tab Matrix
        html.Div(id='pv-tab-cross', children=[
            html.Div(id='pv-cross-out',
                     children=[
                         html.P("Set a Sub Dimension in Pivot Controls and enable the "
                                "⊞ Cross-Tab Matrix feature, then click Build Pivot.",
                                style={'color': '#aaa', 'textAlign': 'center',
                                       'padding': '30px', 'fontSize': '13px'}),
                     ]),
        ], style={'padding': '10px', 'display': 'none'}),

    ], style={**s.SECTION}),

], style={**s.MAIN_CONTAINER})


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 1 — Initialise dropdowns
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-f-rep',         'options'),
    Output('pv-f-cust',        'options'),
    Output('pv-f-item',        'options'),
    Output('pv-f-loc',         'options'),
    Output('pv-f-wh',          'options'),
    Output('pv-years',         'options'),
    Output('pv-years',         'value'),
    Output('pv-date-from',     'date'),
    Output('pv-date-to',       'date'),
    Output('pv-pareto-metric', 'options'),
    Input('pv-build-btn',      'id'),
    State('auth-store',        'data'),
    prevent_initial_call=False,
)
def _init_filters(_, auth_data):
    df = _get_df(auth_data)
    if df.empty:
        return [], [], [], [], [], [], [], None, None, METRIC_OPTIONS

    def _opts(col):
        if col not in df.columns:
            return []
        vals = sorted(df[col].dropna().astype(str).unique().tolist())
        return [{'label': v, 'value': v} for v in vals if v not in ('nan', '')]

    years  = sorted(df['Date'].dt.year.dropna().unique().astype(int).tolist(), reverse=True)
    y_opts = [{'label': str(y), 'value': y} for y in years]

    avail_metrics = [o for o in METRIC_OPTIONS if o['value'] in df.columns]

    return (
        _opts('Sales Rep'), _opts('Customer Names'), _opts('Items'),
        _opts('Location'), _opts('WarehouseName'),
        y_opts, [years[0]] if years else [],
        df['Date'].min().date().isoformat(),
        df['Date'].max().date().isoformat(),
        avail_metrics,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 2 — Build Pivot  (main workhorse)
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-pivot-out',   'children'),
    Output('pv-status',      'children'),
    Output('pv-pivot-store', 'data'),
    Output('pv-kpi-strip',   'children'),
    Output('pv-cross-out',   'children'),
    Output('pv-cross-store', 'data'),
    Input('pv-build-btn',    'n_clicks'),
    State('auth-store',      'data'),
    State('pv-row-dim',      'value'),
    State('pv-sub-dim',      'value'),
    State('pv-metrics',      'value'),
    State('pv-agg',          'value'),
    State('pv-date-from',    'date'),
    State('pv-date-to',      'date'),
    State('pv-years',        'value'),
    State('pv-top-n',        'value'),
    State('pv-heatmap',      'value'),
    State('pv-time-bucket',  'value'),
    State('pv-features',     'value'),
    State('pv-f-rep',        'value'),
    State('pv-f-cust',       'value'),
    State('pv-f-item',       'value'),
    State('pv-f-loc',        'value'),
    State('pv-f-wh',         'value'),
    State('pv-f-b2b',        'value'),
    prevent_initial_call=True,
)
def build_pivot(n, auth_data, row_dim, sub_dim, metrics, agg_func,
                date_from, date_to, years, top_n, heatmap,
                time_bucket, features,
                f_rep, f_cust, f_item, f_loc, f_wh, f_b2b):

    features = features or []
    df = _get_df(auth_data)
    if df.empty:
        return (html.Div("No data.", style={'color': C_RED}),
                "No data", None, None, None, None)

    # ── Apply filters ──────────────────────────────────────────────────────
    if date_from: df = df[df['Date'] >= pd.to_datetime(date_from)]
    if date_to:   df = df[df['Date'] <= pd.to_datetime(date_to)]
    if years:     df = df[df['Date'].dt.year.isin(years)]

    def _flt(col, vals):
        if vals and col in df.columns:
            return df[df[col].astype(str).isin([str(v) for v in vals])]
        return df

    df = _flt('Sales Rep', f_rep)
    df = _flt('Customer Names', f_cust)
    df = _flt('Items', f_item)
    df = _flt('Location', f_loc)
    df = _flt('WarehouseName', f_wh)
    if f_b2b and f_b2b != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str) == f_b2b]

    if df.empty:
        return (html.Div("No rows match the selected filters.",
                         style={'color': C_AMBER}),
                "0 rows", None, None, None, None)

    if not metrics:
        metrics = ['Value']
    metrics = [m for m in metrics if m in df.columns]
    if not metrics:
        return (html.Div("Selected metrics not found in data.", style={'color': C_RED}),
                "Error", None, None, None, None)

    top_n  = int(top_n) if top_n else 50
    sub    = sub_dim if sub_dim else None
    bucket = time_bucket if time_bucket and time_bucket != 'none' else None

    # ── Build base pivot ───────────────────────────────────────────────────
    pivot = _compute_pivot(df, row_dim, sub, metrics, agg_func,
                           time_bucket=time_bucket or 'none')
    if pivot.empty:
        return (html.Div("Pivot returned no data.", style={'color': C_AMBER}),
                "0 rows", None, None, None, None)

    primary = metrics[0]

    # ── Optional feature columns ───────────────────────────────────────────
    if 'market_share' in features:
        pivot = _add_market_share(pivot, primary)

    if 'cagr' in features:
        pivot = _add_cagr(df, pivot, row_dim, primary)

    if 'velocity' in features:
        pivot = _add_velocity(df, pivot, row_dim)

    if 'sparklines' in features:
        pivot = _add_sparklines(df, pivot, row_dim, primary)

    if 'outliers' in features:
        pivot = _add_outlier_flags(pivot, metrics)

    # Core analytics (rank, contribution, cumulative, ABC) always added
    pivot = _add_core_analytics(pivot, primary)

    # ── Top-N after sorting ────────────────────────────────────────────────
    if 'Rank' in pivot.columns:
        pivot = pivot[pivot['Rank'] <= top_n]

    # Round numerics
    for col in pivot.select_dtypes(include='number').columns:
        pivot[col] = pivot[col].round(2)

    # ── HHI KPI ───────────────────────────────────────────────────────────
    kpi_strip = None
    if 'hhi' in features:
        hhi_val = _compute_hhi(pivot, primary)
        hhi_label = 'Unconcentrated' if hhi_val < 0.15 else (
            'Moderate' if hhi_val < 0.25 else '⚠️ High Risk')
        hhi_color = C_GREEN if hhi_val < 0.15 else (C_AMBER if hhi_val < 0.25 else C_RED)
        n_rows     = len(pivot)
        total_val  = pivot[primary].sum() if primary in pivot.columns else 0
        kpi_strip = html.Div([
            _kpi_chip('HHI Index', hhi_val, hhi_color,
                      f'{hhi_label} · 0=diverse, 1=monopoly'),
            _kpi_chip('Rows in Pivot', f'{n_rows:,}', C_DARK),
            _kpi_chip(f'Total {primary}',
                      f'{total_val:,.0f}' if total_val >= 1000 else f'{total_val:.2f}',
                      C_BLUE),
        ], style={'marginBottom': '10px'})

    # ── Cross-Tab Matrix ───────────────────────────────────────────────────
    cross_children = no_update
    cross_store    = no_update
    if 'crosstab' in features:
        if not sub or sub == row_dim:
            cross_children = html.Div(
                "⊞ Cross-Tab requires a Sub Dimension — set one in Pivot Controls.",
                style={'color': C_AMBER, 'padding': '20px', 'textAlign': 'center'})
        else:
            ct = _compute_crosstab(df, row_dim, sub, primary, agg_func)
            if ct.empty:
                cross_children = html.Div("Cross-tab returned no data.",
                                          style={'color': C_AMBER, 'padding': '20px'})
            else:
                cross_store    = ct.to_json(orient='split')
                cross_children = html.Div([
                    html.P(f"Matrix: {row_dim} (rows) × {sub} (columns) — metric: {primary}  "
                           f"| {len(ct)} rows × {len(ct.columns) - 1} columns",
                           style={'fontSize': '12px', 'color': '#7f8c8d',
                                  'marginBottom': '8px'}),
                    _make_datatable(ct, 'pv-ct-table', heatmap=bool(heatmap),
                                    page_size=20),
                ])

    # ── Build DataTable ────────────────────────────────────────────────────
    table  = _make_datatable(pivot, 'pv-main-table',
                             heatmap=bool(heatmap), page_size=25)
    status = (f"✅  {len(pivot):,} rows · {len(df):,} transactions · "
              f"agg: {agg_func} · features: {', '.join(features) or 'none'}")

    return (table, status,
            pivot.to_json(orient='split', date_format='iso'),
            kpi_strip, cross_children, cross_store)


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 3 — Tab visibility
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-tab-pivot',    'style'),
    Output('pv-tab-variance', 'style'),
    Output('pv-tab-pareto',   'style'),
    Output('pv-tab-stats',    'style'),
    Output('pv-tab-cross',    'style'),
    Input('pv-tabs', 'value'),
)
def _tab_vis(tab):
    show = {'padding': '10px'}
    hide = {'padding': '10px', 'display': 'none'}
    return (
        show if tab == 'tab-pivot'    else hide,
        show if tab == 'tab-variance' else hide,
        show if tab == 'tab-pareto'   else hide,
        show if tab == 'tab-stats'    else hide,
        show if tab == 'tab-cross'    else hide,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 4 — Presets panel visibility
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-presets-panel', 'style'),
    Input('pv-features', 'value'),
)
def _presets_vis(features):
    if features and 'presets' in features:
        return {'display': 'block'}
    return {'display': 'none'}


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 5 — Save preset
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-presets-store',  'data'),
    Output('pv-load-preset',    'options'),
    Output('pv-preset-status',  'children'),
    Input('pv-save-preset',     'n_clicks'),
    State('pv-preset-name',     'value'),
    State('pv-presets-store',   'data'),
    State('pv-row-dim',         'value'),
    State('pv-sub-dim',         'value'),
    State('pv-metrics',         'value'),
    State('pv-agg',             'value'),
    State('pv-time-bucket',     'value'),
    State('pv-features',        'value'),
    State('pv-date-from',       'date'),
    State('pv-date-to',         'date'),
    State('pv-years',           'value'),
    State('pv-f-rep',           'value'),
    State('pv-f-cust',          'value'),
    State('pv-f-item',          'value'),
    State('pv-f-loc',           'value'),
    State('pv-f-wh',            'value'),
    State('pv-f-b2b',           'value'),
    prevent_initial_call=True,
)
def save_preset(n, name, store, row_dim, sub_dim, metrics, agg, bucket,
                features, d_from, d_to, years, f_rep, f_cust, f_item,
                f_loc, f_wh, f_b2b):
    if not n or not name:
        return no_update, no_update, "Enter a preset name first."
    store = store or {}
    store[name] = dict(row_dim=row_dim, sub_dim=sub_dim, metrics=metrics,
                       agg=agg, bucket=bucket, features=features,
                       d_from=d_from, d_to=d_to, years=years,
                       f_rep=f_rep, f_cust=f_cust, f_item=f_item,
                       f_loc=f_loc, f_wh=f_wh, f_b2b=f_b2b)
    opts = [{'label': k, 'value': k} for k in store]
    return store, opts, f"✅ Preset '{name}' saved."


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 6 — Populate preset dropdown on load
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-load-preset', 'options', allow_duplicate=True),
    Input('pv-presets-store', 'data'),
    prevent_initial_call=True,
)
def _refresh_preset_opts(store):
    if not store:
        return []
    return [{'label': k, 'value': k} for k in store]


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 7 — Load preset
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-row-dim',     'value', allow_duplicate=True),
    Output('pv-sub-dim',     'value', allow_duplicate=True),
    Output('pv-metrics',     'value', allow_duplicate=True),
    Output('pv-agg',         'value', allow_duplicate=True),
    Output('pv-time-bucket', 'value', allow_duplicate=True),
    Output('pv-features',    'value', allow_duplicate=True),
    Output('pv-date-from',   'date',  allow_duplicate=True),
    Output('pv-date-to',     'date',  allow_duplicate=True),
    Output('pv-years',       'value', allow_duplicate=True),
    Output('pv-f-rep',       'value', allow_duplicate=True),
    Output('pv-f-cust',      'value', allow_duplicate=True),
    Output('pv-f-item',      'value', allow_duplicate=True),
    Output('pv-f-loc',       'value', allow_duplicate=True),
    Output('pv-f-wh',        'value', allow_duplicate=True),
    Output('pv-f-b2b',       'value', allow_duplicate=True),
    Input('pv-load-btn',     'n_clicks'),
    State('pv-load-preset',  'value'),
    State('pv-presets-store', 'data'),
    prevent_initial_call=True,
)
def load_preset(n, preset_name, store):
    if not n or not preset_name or not store or preset_name not in store:
        return [no_update] * 15
    p = store[preset_name]
    return (p.get('row_dim', 'Items 2'), p.get('sub_dim', ''),
            p.get('metrics', ['Value', 'QTY PCS']), p.get('agg', 'sum'),
            p.get('bucket', 'none'), p.get('features', []),
            p.get('d_from'), p.get('d_to'), p.get('years', []),
            p.get('f_rep', []), p.get('f_cust', []), p.get('f_item', []),
            p.get('f_loc', []), p.get('f_wh', []), p.get('f_b2b', 'All'))


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 8 — Variance + Waterfall
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-var-out',       'children'),
    Output('pv-waterfall-fig', 'figure'),
    Input('pv-var-btn',        'n_clicks'),
    State('auth-store',        'data'),
    State('pv-row-dim',        'value'),
    State('pv-sub-dim',        'value'),
    State('pv-metrics',        'value'),
    State('pv-agg',            'value'),
    State('pv-features',       'value'),
    State('pv-va-from',        'date'),
    State('pv-va-to',          'date'),
    State('pv-vb-from',        'date'),
    State('pv-vb-to',          'date'),
    State('pv-f-rep',          'value'),
    State('pv-f-cust',         'value'),
    State('pv-f-item',         'value'),
    State('pv-f-loc',          'value'),
    State('pv-f-wh',           'value'),
    State('pv-f-b2b',          'value'),
    prevent_initial_call=True,
)
def variance_analysis(n, auth_data, row_dim, sub_dim, metrics, agg_func, features,
                      a_from, a_to, b_from, b_to,
                      f_rep, f_cust, f_item, f_loc, f_wh, f_b2b):
    empty_fig = go.Figure()
    empty_fig.update_layout(paper_bgcolor=C_CARD, plot_bgcolor=C_CARD)

    if not n:
        return no_update, no_update

    features = features or []
    df = _get_df(auth_data)
    if df.empty:
        return html.Div("No data.", style={'color': C_RED}), empty_fig

    def _flt(d, col, vals):
        if vals and col in d.columns:
            return d[d[col].astype(str).isin([str(v) for v in vals])]
        return d

    df = _flt(df, 'Sales Rep', f_rep)
    df = _flt(df, 'Customer Names', f_cust)
    df = _flt(df, 'Items', f_item)
    df = _flt(df, 'Location', f_loc)
    df = _flt(df, 'WarehouseName', f_wh)
    if f_b2b and f_b2b != 'All' and 'B2B' in df.columns:
        df = df[df['B2B'].astype(str) == f_b2b]

    if not metrics:
        metrics = ['Value']
    metrics = [m for m in metrics if m in df.columns]
    sub  = sub_dim if sub_dim else None
    dims = [row_dim] + ([sub] if sub else [])

    def _period(d_from, d_to):
        d = df.copy()
        if d_from: d = d[d['Date'] >= pd.to_datetime(d_from)]
        if d_to:   d = d[d['Date'] <= pd.to_datetime(d_to)]
        return _compute_pivot(d, row_dim, sub, metrics, agg_func)

    pA = _period(a_from, a_to)
    pB = _period(b_from, b_to)

    if pA.empty and pB.empty:
        return (html.Div("Both periods returned no data. Set date ranges above.",
                         style={'color': C_AMBER, 'padding': '16px'}), empty_fig)

    sA, sB = ' [A]', ' [B]'
    if pA.empty:
        merged = pB.rename(columns={m: f'{m}{sB}' for m in metrics}).copy()
        for m in metrics:
            merged[f'{m}{sA}'] = 0
    elif pB.empty:
        merged = pA.rename(columns={m: f'{m}{sA}' for m in metrics}).copy()
        for m in metrics:
            merged[f'{m}{sB}'] = 0
    else:
        merged = (pA.rename(columns={m: f'{m}{sA}' for m in metrics})
                  .merge(pB.rename(columns={m: f'{m}{sB}' for m in metrics}),
                         on=dims, how='outer')
                  .fillna(0))

    for m in metrics:
        ca, cb = f'{m}{sA}', f'{m}{sB}'
        if ca in merged.columns and cb in merged.columns:
            merged[f'{m} Δ Abs'] = (merged[cb] - merged[ca]).round(2)
            merged[f'{m} Δ %']   = np.where(
                merged[ca] != 0,
                ((merged[cb] - merged[ca]) / merged[ca] * 100).round(1),
                np.nan)
            merged[f'{m} Dir']   = merged[f'{m} Δ Abs'].apply(
                lambda x: '▲' if x > 0 else ('▼' if x < 0 else '–'))

    for col in merged.select_dtypes(include='number').columns:
        merged[col] = merged[col].round(2)

    # Variance table styles
    cond = [{'if': {'row_index': 'odd'}, 'backgroundColor': '#fafbfc'}]
    for m in metrics:
        dc = f'{m} Δ %'
        if dc in merged.columns:
            for i, v in enumerate(merged[dc].fillna(0)):
                bg = '#eafaf1' if v > 0 else ('#fdedec' if v < 0 else '#fff')
                cond.append({'if': {'row_index': i, 'column_id': dc},
                             'backgroundColor': bg})

    cols = []
    for c in merged.columns:
        is_num = pd.api.types.is_numeric_dtype(merged[c])
        cols.append({'name': c, 'id': c,
                     'type': 'numeric' if is_num else 'text',
                     'format': Format(group=Group.yes).scheme(Scheme.fixed).precision(2)
                               if is_num else None})

    var_table = dash_table.DataTable(
        columns=cols, data=merged.to_dict('records'),
        sort_action='native', page_action='native', page_size=25,
        style_table={'overflowX': 'auto'},
        style_header={'backgroundColor': C_DARK, 'color': 'white',
                      'fontWeight': 'bold', 'fontSize': '12px', 'textAlign': 'center'},
        style_cell={'fontSize': '12px', 'padding': '7px 10px',
                    'border': f'1px solid {C_BORDER}'},
        style_cell_conditional=[{'if': {'column_type': 'numeric'}, 'textAlign': 'right'}],
        style_data_conditional=cond,
    )

    label_a = f"{a_from or '?'} → {a_to or '?'}"
    label_b = f"{b_from or '?'} → {b_to or '?'}"
    table_out = html.Div([
        html.P(f"Period A: {label_a}  |  Period B: {label_b}",
               style={'fontSize': '12px', 'color': '#7f8c8d',
                      'fontStyle': 'italic', 'marginBottom': '8px'}),
        var_table,
    ])

    # ── Waterfall chart ────────────────────────────────────────────────────
    if 'waterfall' not in features:
        return table_out, empty_fig

    primary = metrics[0]
    delta_col = f'{primary} Δ Abs'
    if delta_col not in merged.columns:
        return table_out, empty_fig

    wf = (merged[[row_dim, delta_col]]
          .dropna()
          .sort_values(delta_col, ascending=False)
          .head(20))

    total_delta = wf[delta_col].sum()
    measures = ['relative'] * len(wf) + ['total']
    x_vals   = wf[row_dim].astype(str).tolist() + ['NET CHANGE']
    y_vals   = wf[delta_col].tolist() + [total_delta]

    colors = ['#27ae60' if v >= 0 else '#e74c3c' for v in y_vals[:-1]]
    colors.append('#3498db')

    wf_fig = go.Figure(go.Waterfall(
        orientation='v',
        measure=measures,
        x=x_vals,
        y=y_vals,
        connector={'line': {'color': '#bdc3c7', 'width': 1}},
        increasing={'marker': {'color': '#27ae60'}},
        decreasing={'marker': {'color': '#e74c3c'}},
        totals={'marker': {'color': '#3498db'}},
        hovertemplate='<b>%{x}</b><br>Δ ' + primary + ': %{y:,.2f}<extra></extra>',
    ))
    wf_fig.update_layout(
        title=f'🌊 Waterfall — Top Contributors to Change in {primary}  '
              f'(A: {label_a}  →  B: {label_b})',
        xaxis={'tickangle': -35, 'tickfont': {'size': 10}},
        yaxis={'title': f'Δ {primary}', 'tickformat': ',.0f'},
        plot_bgcolor=C_CARD, paper_bgcolor=C_CARD,
        height=440, margin={'l': 60, 'r': 20, 't': 70, 'b': 120},
        showlegend=False,
    )
    return table_out, wf_fig


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 9 — Pareto / ABC Chart
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-pareto-fig',       'figure'),
    Output('pv-pareto-table-out', 'children'),
    Input('pv-pareto-metric',     'value'),
    Input('pv-build-btn',         'n_clicks'),
    State('pv-pivot-store',       'data'),
    State('pv-row-dim',           'value'),
    prevent_initial_call=True,
)
def pareto_chart(metric, _n, store_data, row_dim):
    empty = go.Figure()
    empty.update_layout(paper_bgcolor=C_CARD, plot_bgcolor=C_CARD,
                        annotations=[{'text': 'Build the pivot first',
                                       'showarrow': False, 'x': .5, 'y': .5,
                                       'xref': 'paper', 'yref': 'paper',
                                       'font': {'color': '#aaa', 'size': 14}}])
    if not store_data:
        return empty, ''

    try:
        pivot = pd.read_json(io.StringIO(store_data), orient='split')
    except Exception:
        return empty, ''

    if metric not in pivot.columns or row_dim not in pivot.columns:
        return empty, html.Div(f"Metric '{metric}' not in pivot.",
                               style={'color': C_AMBER})

    df_p = (pivot[[row_dim, metric]].dropna()
            .sort_values(metric, ascending=False)
            .head(40).reset_index(drop=True))
    total = df_p[metric].sum()
    if total == 0:
        return empty, ''

    df_p['Contribution %'] = (df_p[metric] / total * 100).round(2)
    df_p['Cumulative %']   = df_p['Contribution %'].cumsum().round(2)
    df_p['ABC'] = df_p['Cumulative %'].apply(
        lambda x: 'A' if x <= 80 else ('B' if x <= 95 else 'C'))

    bar_clr = df_p['ABC'].map({'A': C_GREEN, 'B': C_AMBER, 'C': C_RED})
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_p[row_dim].astype(str), y=df_p[metric],
        name=metric, marker_color=bar_clr, opacity=0.85,
        hovertemplate=(
            '<b>%{x}</b><br>'
            + metric + ': %{y:,.2f}<br>'
            'Contribution: %{customdata[0]:.1f}%<br>'
            'Cumulative: %{customdata[1]:.1f}%<extra></extra>'),
        customdata=df_p[['Contribution %', 'Cumulative %']].values,
    ))
    fig.add_trace(go.Scatter(
        x=df_p[row_dim].astype(str), y=df_p['Cumulative %'],
        name='Cumulative %', mode='lines+markers', yaxis='y2',
        line={'color': C_BLUE, 'width': 2}, marker={'size': 5},
        hovertemplate='Cumulative: %{y:.1f}%<extra></extra>',
    ))
    fig.add_hline(y=80, yref='y2', line_dash='dash', line_color=C_RED,
                  annotation_text='80% threshold', annotation_position='top right')
    fig.update_layout(
        title=f'Pareto Analysis — {metric}  (Green=A: top 80% | Amber=B: 80–95% | Red=C: tail)',
        xaxis={'tickangle': -35, 'tickfont': {'size': 10}},
        yaxis={'title': metric, 'tickformat': ',.0f'},
        yaxis2={'title': 'Cumulative %', 'overlaying': 'y', 'side': 'right',
                'range': [0, 105], 'ticksuffix': '%'},
        legend={'orientation': 'h', 'y': -0.28},
        plot_bgcolor=C_CARD, paper_bgcolor=C_CARD,
        hovermode='x unified', height=420,
        margin={'l': 60, 'r': 60, 't': 60, 'b': 110},
    )

    abc_sum = (df_p.groupby('ABC')
               .agg(Items=('ABC', 'count'),
                    Total_Value=(metric, 'sum'),
                    Share=('Contribution %', 'sum'))
               .reset_index()
               .rename(columns={'Total_Value': f'Total {metric}', 'Share': 'Share %'}))
    abc_sum['Share %'] = abc_sum['Share %'].round(1)

    return fig, html.Div([
        html.H5("ABC Summary",
                style={'color': C_DARK, 'fontSize': '13px',
                       'fontWeight': 'bold', 'marginBottom': '6px'}),
        _make_datatable(abc_sum, 'pv-abc-tbl', heatmap=False, page_size=5),
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 10 — Statistics
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-stats-out', 'children'),
    Output('pv-box-fig',   'figure'),
    Input('pv-build-btn',  'n_clicks'),
    State('pv-pivot-store', 'data'),
    State('pv-metrics',    'value'),
    prevent_initial_call=True,
)
def stats_tab(n, store_data, metrics):
    empty = go.Figure()
    empty.update_layout(paper_bgcolor=C_CARD, plot_bgcolor=C_CARD)
    if not n or not store_data:
        return no_update, no_update

    try:
        pivot = pd.read_json(io.StringIO(store_data), orient='split')
    except Exception:
        return html.Div("Error reading data."), empty

    if not metrics:
        metrics = ['Value']
    metrics = [m for m in metrics if m in pivot.columns]
    stats = _descriptive_stats(pivot, metrics)

    table = _make_datatable(stats, 'pv-stats-tbl', heatmap=False, page_size=20)

    fig = go.Figure()
    palette = [C_BLUE, C_GREEN, C_AMBER, C_RED, '#9b59b6', '#1abc9c',
               '#e67e22', '#34495e', '#2ecc71', '#c0392b']
    for i, m in enumerate(metrics):
        if m not in pivot.columns:
            continue
        col_d = pd.to_numeric(pivot[m], errors='coerce').dropna()
        if col_d.empty:
            continue
        fig.add_trace(go.Box(
            y=col_d, name=m, boxpoints='outliers',
            marker_color=palette[i % len(palette)],
            line_color=C_DARK,
        ))

    fig.update_layout(
        title='Distribution of Selected Metrics — Box Plot (dots = outliers)',
        yaxis={'title': 'Value', 'tickformat': ',.0f'},
        plot_bgcolor=C_CARD, paper_bgcolor=C_CARD,
        height=380, margin={'l': 60, 'r': 20, 't': 50, 'b': 40},
    )
    return table, fig


# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK 11 — Download Excel
# ══════════════════════════════════════════════════════════════════════════════
@callback(
    Output('pv-dl',          'data'),
    Input('pv-dl-btn',       'n_clicks'),
    State('pv-pivot-store',  'data'),
    State('pv-cross-store',  'data'),
    State('pv-metrics',      'value'),
    State('pv-row-dim',      'value'),
    State('pv-features',     'value'),
    prevent_initial_call=True,
)
def download_excel(n, pivot_store, cross_store, metrics, row_dim, features):
    if not n or not pivot_store:
        return no_update

    try:
        pivot = pd.read_json(io.StringIO(pivot_store), orient='split')
    except Exception:
        return no_update

    features = features or []
    if not metrics:
        metrics = ['Value']
    primary = metrics[0]

    # Pareto sheet
    pareto_df = pd.DataFrame()
    if primary and primary in pivot.columns and row_dim in pivot.columns:
        df_p = (pivot[[row_dim, primary]].dropna()
                .sort_values(primary, ascending=False).copy())
        total = df_p[primary].sum()
        if total > 0:
            df_p['Contribution %'] = (df_p[primary] / total * 100).round(2)
            df_p['Cumulative %']   = df_p['Contribution %'].cumsum().round(2)
            df_p['ABC Class']      = df_p['Cumulative %'].apply(
                lambda x: 'A' if x <= 80 else ('B' if x <= 95 else 'C'))
            df_p['ABC'] = df_p['ABC Class']
            pareto_df = df_p

    # Stats sheet
    stats_df = _descriptive_stats(pivot,
                                  [m for m in metrics if m in pivot.columns])

    # Cross-tab sheet
    cross_df = pd.DataFrame()
    if cross_store:
        try:
            cross_df = pd.read_json(io.StringIO(cross_store), orient='split')
        except Exception:
            pass

    sheets = {
        'Pivot Table':      pivot,
        'Pareto ABC':       pareto_df,
        'Statistics':       stats_df,
    }
    if not cross_df.empty:
        sheets['Cross-Tab Matrix'] = cross_df

    xlsx_bytes = _build_excel(sheets)
    b64 = base64.b64encode(xlsx_bytes).decode()

    return {
        'content':  b64,
        'filename': 'pivot_analysis.xlsx',
        'type':     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'base64':   True,
    }
