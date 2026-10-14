import calendar
import pandas as pd
import numpy as np


def get_time_window(df, target_year, view_mode, clicked_date_str=None):
    """
    Centralized time-filtering engine.
    Filters any raw dataframe to the exact requested time window.
    Returns: df_curr, df_prev, time_label (e.g., 'Oct 2025')
    """
    target_year = int(target_year)
    df_y = df[df['Date'].dt.year == target_year]
    df_p = df[df['Date'].dt.year == target_year - 1]

    if df_y.empty:
        return df_y, df_p, str(target_year)

    # 1. Determine the Anchor Date FIRST (Clicked vs Latest Available)
    if clicked_date_str:
        try:
            anchor_date = pd.to_datetime(clicked_date_str)
        except Exception:
            # Handle quarter-format labels like "Q2 2025" from quarterly bar clicks
            qs = str(clicked_date_str)
            if qs.startswith('Q') and ' ' in qs:
                try:
                    q_num = int(qs.split('Q')[1].split(' ')[0])
                    yr_num = int(qs.split(' ')[-1])
                    end_month = q_num * 3
                    last_day = calendar.monthrange(yr_num, end_month)[1]
                    anchor_date = pd.Timestamp(yr_num, end_month, last_day)
                except Exception:
                    anchor_date = df_y['Date'].max()
            else:
                anchor_date = df_y['Date'].max()
    else:
        anchor_date = df_y['Date'].max()

    if pd.isna(anchor_date):
        return df_y, df_p, str(target_year)

    today = pd.Timestamp.now().normalize()

    # 2. Year View (Now heavily restricted to strict Year-to-Date)
    if view_mode == 'Year':
        # Create a mask to slice df_p to the exact month and day of our anchor
        ly_mask = (df_p['Date'].dt.month < anchor_date.month) | \
                  ((df_p['Date'].dt.month == anchor_date.month) & (df_p['Date'].dt.day <= anchor_date.day))

        df_p_ytd = df_p[ly_mask]
        lbl = str(target_year)
        if target_year == today.year:
            lbl += ' (YTD)'
        return df_y, df_p_ytd, lbl

    # 2.5 Quarter View - YTD window with quarterly grouping; clicking a bar shows that quarter
    if view_mode == 'Quarter':
        if clicked_date_str:
            # Show only the clicked quarter vs same quarter last year
            q = anchor_date.quarter
            df_q  = df_y[(df_y['Date'].dt.quarter == q)]
            df_pq = df_p[(df_p['Date'].dt.quarter == q)]
            lbl = f"Q{q} {target_year}"
            if target_year == today.year and q == today.quarter:
                # QTD parity: cap prev year to the same month+day as our latest data
                df_pq = df_pq[
                    (df_pq['Date'].dt.month < anchor_date.month) |
                    ((df_pq['Date'].dt.month == anchor_date.month) &
                     (df_pq['Date'].dt.day <= anchor_date.day))
                ]
                lbl += ' (QTD)'
            return df_q, df_pq, lbl
        else:
            ly_mask = (df_p['Date'].dt.month < anchor_date.month) | \
                      ((df_p['Date'].dt.month == anchor_date.month) & (df_p['Date'].dt.day <= anchor_date.day))
            df_p_ytd = df_p[ly_mask]
            lbl = f"Q{anchor_date.quarter} {target_year}"
            if target_year == today.year and anchor_date.quarter == today.quarter:
                lbl += ' (QTD)'
            return df_y, df_p_ytd, lbl

    # 3. Month View Filtering
    if view_mode == 'Month':
        df_curr = df_y[df_y['Date'].dt.month == anchor_date.month]
        df_prev = df_p[df_p['Date'].dt.month == anchor_date.month]
        lbl = anchor_date.strftime('%b %Y')
        if target_year == today.year and anchor_date.month == today.month:
            # MTD parity: cap prev year to the same day of month as our latest data
            df_prev = df_prev[df_prev['Date'].dt.day <= anchor_date.day]
            lbl += ' (MTD)'
        return df_curr, df_prev, lbl

    # 4. Week View Filtering
    if view_mode == 'Week':
        if clicked_date_str:
            # If clicked, show that specific 7-day week
            end_date = anchor_date + pd.Timedelta(days=6)
            df_curr = df_y[(df_y['Date'] >= anchor_date) & (df_y['Date'] <= end_date)]
            df_prev = df_p[(df_p['Date'] >= anchor_date - pd.DateOffset(years=1)) & (
                    df_p['Date'] <= end_date - pd.DateOffset(years=1))]
            lbl = f"Wk of {anchor_date.strftime('%d %b %Y')}"
            if anchor_date <= today <= end_date:
                lbl += ' (WTD)'
            return df_curr, df_prev, lbl
        else:
            # Default if no click: Show the last 4 weeks leading up to the max date
            start_date = anchor_date - pd.Timedelta(days=28)
            df_curr = df_y[(df_y['Date'] > start_date) & (df_y['Date'] <= anchor_date)]
            df_prev = df_p[(df_p['Date'] > start_date - pd.DateOffset(years=1)) & (
                    df_p['Date'] <= anchor_date - pd.DateOffset(years=1))]
            lbl = f"Last 4 Wks ({anchor_date.strftime('%b')})"
            if target_year == today.year:
                lbl += ' (WTD)'
            return df_curr, df_prev, lbl

    return df_y, df_p, str(target_year)


def get_trend_data(df, view_mode, vol_col):
    """Groups pre-filtered data by Week, Month, or Year. Formats explicitly to prevent Plotly continuous axis spillover."""
    if df.empty: return pd.DataFrame(), None, None
    df_temp = df.copy()

    # Create a clean categorical string for Plotly, while maintaining chronological Sort_Date
    if view_mode == 'Week':
        df_temp['Sort_Date'] = df_temp['Date'] - pd.to_timedelta(df_temp['Date'].dt.dayofweek, unit='D')
        df_temp['Period'] = df_temp['Sort_Date'].dt.strftime('%d %b %Y')
    elif view_mode == 'Month':
        df_temp['Sort_Date'] = df_temp['Date'].dt.to_period('M').dt.to_timestamp()
        df_temp['Period'] = df_temp['Sort_Date'].dt.strftime('%b %Y')
    elif view_mode == 'Quarter':
        df_temp['Sort_Date'] = df_temp['Date'].dt.to_period('Q').dt.to_timestamp()
        df_temp['Period'] = 'Q' + df_temp['Date'].dt.quarter.astype(str) + ' ' + df_temp['Date'].dt.year.astype(str)
    else:
        df_temp['Sort_Date'] = df_temp['Date'].dt.to_period('Y').dt.to_timestamp()
        df_temp['Period'] = df_temp['Sort_Date'].dt.strftime('%Y')

    trend = df_temp.groupby(['Sort_Date', 'Period'])[['Value', vol_col, 'Quantity']].sum().reset_index()
    trend = trend.sort_values('Sort_Date')

    # Rename Period to Date so the visual functions natively map it to the X-Axis
    trend.rename(columns={'Period': 'Date'}, inplace=True)
    trend['Avg_Price'] = (trend['Value'] / trend['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)

    # Return None for dtick/xfmt since the categorical strings handle it perfectly
    return trend, None, None


def get_drilldown_data(df, vol_col):
    """Groups pre-filtered data by exact Day."""
    if df.empty: return pd.DataFrame()
    df_temp = df.copy()

    # Force to Date object so Plotly can auto-optimize tick spacing for dense ranges
    df_temp['Date'] = df_temp['Date'].dt.date
    daily_df = df_temp.groupby('Date')[['Value', vol_col, 'Quantity']].sum().reset_index()
    daily_df['Avg_Price'] = (daily_df['Value'] / daily_df['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)
    return daily_df


def get_top_performers(df, group_col, vol_col, top_n=10):
    if df.empty or group_col not in df.columns: return pd.DataFrame()
    top = df.groupby(group_col)[['Value', vol_col, 'Quantity']].sum().nlargest(top_n, 'Value').sort_values('Value',
                                                                                               ascending=True).reset_index()
    top['Avg_Price'] = (top['Value'] / top['Quantity']).replace([np.inf, -np.inf], np.nan).fillna(0)
    return top


# --- RESTORED FUNCTIONS FOR V1 TABLE ---

def calculate_kpi_metrics(df_curr, df_prev, vol_col):
    """Safely calculates Revenue, Volume, Price, and handles zero-division."""
    res = {}
    res['rev_c'] = df_curr['Value'].sum() if not df_curr.empty else 0
    res['rev_p'] = df_prev['Value'].sum() if not df_prev.empty else 0
    res['vol_c'] = df_curr[vol_col].sum() if not df_curr.empty else 0
    res['vol_p'] = df_prev[vol_col].sum() if not df_prev.empty else 0
    res['price_c'] = res['rev_c'] / res['vol_c'] if res['vol_c'] > 0 else 0
    res['price_p'] = res['rev_p'] / res['vol_p'] if res['vol_p'] > 0 else 0
    return res


def get_performance_table(df_curr, df_prev, group_col, vol_col):
    """Generates the data dictionary required for Dash DataTables."""
    if df_curr.empty or group_col not in df_curr.columns: return []

    c_agg = df_curr.groupby(group_col)[['Value', vol_col, 'Quantity']].sum().reset_index()
    p_agg = df_prev.groupby(group_col)[['Value', vol_col, 'Quantity']].sum().reset_index() if not df_prev.empty else pd.DataFrame(
        columns=[group_col, 'Value', vol_col, 'Quantity'])

    merged = pd.merge(c_agg, p_agg, on=group_col, how='left', suffixes=('_curr', '_prev')).fillna(0)
    safe_prev = merged['Value_prev'].replace(0, np.nan)
    merged['Growth'] = ((merged['Value_curr'] - merged['Value_prev']) / safe_prev * 100).fillna(0)
    merged['Avg_Price'] = np.where(merged['Quantity_curr'] > 0, merged['Value_curr'] / merged['Quantity_curr'], 0)

    merged = merged.sort_values('Value_curr', ascending=False).head(60)

    records = []
    for _, row in merged.iterrows():
        records.append({
            'Entity': row[group_col], 'Revenue': row['Value_curr'], 'LY_Revenue': row['Value_prev'],
            'Growth': row['Growth'], 'Volume': row[f'{vol_col}_curr'], 'Avg_Price': row['Avg_Price']
        })
    return records