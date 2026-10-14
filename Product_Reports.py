import dash
from dash import html, dcc, Input, Output, State, ctx, callback
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import sys

# --- Data Loading ---
file_path = r'C:\Users\perth.soni\PycharmProjects\DashBoard\Raws\Transactions from Jan to Sep 25.xlsx'
try:
    df = pd.read_excel(file_path, sheet_name='Reporter')
    df.columns = df.columns.str.strip()
    df['Date'] = pd.to_datetime(df['Date'])

    # --- UPDATE 1: Load Hierarchy Columns and Bundles ---
    # Ensure standard columns exist, handle missing ones gracefully
    if 'Items' not in df.columns: df['Items'] = 'Unknown'
    if 'Name' not in df.columns: df['Name'] = df['Items']  # Fallback
    if 'Items 2' not in df.columns: df['Items 2'] = 'Unknown'  # Fallback
    if 'Quantity' not in df.columns: df['Quantity'] = 0  # Fallback for Bundles

    df['Customer Names'] = df['Customer Names'].astype(str).replace('nan', 'Unknown')

    # Hierarchy Columns Cleaning
    df['Items'] = df['Items'].astype(str).replace('nan', 'Unknown')
    df['Name'] = df['Name'].astype(str).replace('nan', 'Unknown')
    df['Items 2'] = df['Items 2'].astype(str).replace('nan', 'Unknown')

    # Bundles Column (Ensure Numeric)
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce').fillna(0)

    # Handle Variant Column (Legacy logic preserved for Item Name display)
    if 'Name' in df.columns:
        df['Item Name'] = df['Name']
    elif 'Item Name' in df.columns:
        df['Item Name'] = df['Item Name'].astype(str).replace('nan', 'Unknown')
    else:
        df['Item Name'] = df['Items']

    # Load Inventory (QIH)
    df_soh_raw = pd.read_excel(file_path, sheet_name='QIH')
    df_soh_raw.columns = df_soh_raw.columns.str.strip()

    # Map Inventory to Hierarchy for aggregation
    # We create a mapping dataframe to link SKU (Items) to Name and Items 2
    product_mapping = df[['Items', 'Name', 'Items 2']].drop_duplicates()

    # Clean SOH Item Name
    if 'Item Description' in df_soh_raw.columns:
        df_soh_raw['Item Name'] = df_soh_raw['Item Description'].astype(str).replace('nan', 'Unknown')
    elif 'Name' in df_soh_raw.columns:
        df_soh_raw['Item Name'] = df_soh_raw['Name'].astype(str).replace('nan', 'Unknown')
    elif 'Item Name' in df_soh_raw.columns:
        df_soh_raw['Item Name'] = df_soh_raw['Item Name'].astype(str).replace('nan', 'Unknown')
    else:
        df_soh_raw['Item Name'] = df_soh_raw['Items']  # Fallback

    df_soh_raw = pd.merge(df_soh_raw, product_mapping, on='Items', how='left')

    # Base SKU level inventory (for Dead Stock chart)
    df_soh = df_soh_raw.groupby('Items')['Qty On Hand'].sum().reset_index()

    if 'B2B' not in df.columns: df['B2B'] = 'All'
    df['B2B'] = df['B2B'].astype(str).str.strip()
    last_trans_date = df['Date'].max().strftime('%d %b %Y')

    available_years = sorted(df['Date'].dt.year.unique(), reverse=True)

except Exception as e:
    print(f"Error Loading Data: {e}")
    sys.exit(1)

# --- STYLE CONSTANTS ---
SECTION_STYLE = {
    'minHeight': '100vh',
    'width': '100%',
    'scrollSnapAlign': 'start',
    'display': 'flex',
    'flexDirection': 'column',
    'justifyContent': 'center',
    'padding': '40px',
    'boxSizing': 'border-box',
    'borderBottom': '1px solid #eee'
}

CHART_STYLE = {'height': '75vh'}


# --- UPDATE 2: Stats Helper with Bundles ---
def calculate_stats(subset_df, full_df, target_year, target_month, view_mode, col_name='Items'):
    if subset_df.empty: return ""

    # Context handling
    if col_name == 'Global':
        history_df = full_df
    else:
        target_ids = subset_df[col_name].unique()
        history_df = full_df[full_df[col_name].isin(target_ids)]

    prev_year = target_year - 1

    if view_mode == 'Month to Date':
        c_mask = (history_df['Date'].dt.year == target_year) & (history_df['Date'].dt.month == target_month)
        p_mask = (history_df['Date'].dt.year == prev_year) & (history_df['Date'].dt.month == target_month)
    else:
        c_mask = (history_df['Date'].dt.year == target_year)
        p_mask = (history_df['Date'].dt.year == prev_year)

    # Metrics: Current
    vc = history_df[c_mask]['Value'].sum()
    qc = history_df[c_mask]['QTY PCS'].sum()
    bc = history_df[c_mask]['Quantity'].sum()  # Bundles Current
    ac = vc / qc if qc > 0 else 0

    # Metrics: Previous
    vp = history_df[p_mask]['Value'].sum()
    qp = history_df[p_mask]['QTY PCS'].sum()
    bp = history_df[p_mask]['Quantity'].sum()  # Bundles Previous
    ap = vp / qp if qp > 0 else 0

    # Growth Calculations
    g_val = ((vc - vp) / vp) * 100 if vp > 0 else 0
    d_val = "▲" if g_val >= 0 else "▼"
    c_val = "green" if g_val >= 0 else "red"

    g_qty = ((qc - qp) / qp) * 100 if qp > 0 else 0
    d_qty = "▲" if g_qty >= 0 else "▼"
    c_qty = "green" if g_qty >= 0 else "red"

    g_bnd = ((bc - bp) / bp) * 100 if bp > 0 else 0
    d_bnd = "▲" if g_bnd >= 0 else "▼"
    c_bnd = "green" if g_bnd >= 0 else "red"

    g_avg = ((ac - ap) / ap) * 100 if ap > 0 else 0
    d_avg = "▲" if g_avg >= 0 else "▼"
    c_avg = "green" if g_avg >= 0 else "red"

    return (f"<span style='font-size:14px'>"
            f"Revenue: {vc:,.0f} (LY: {vp:,.0f}, <span style='color:{c_val}'>{d_val} {abs(g_val):.1f}%</span>) | "
            f"Pcs: {qc:,.0f} (LY: {qp:,.0f}, <span style='color:{c_qty}'>{d_qty} {abs(g_qty):.1f}%</span>) | "
            f"Bundles: {bc:,.0f} (LY: {bp:,.0f}, <span style='color:{c_bnd}'>{d_bnd} {abs(g_bnd):.1f}%</span>) | "
            f"Avg: {ac:,.2f} (LY: {ap:,.2f}, <span style='color:{c_avg}'>{d_avg} {abs(g_avg):.1f}%</span>)"
            f"</span>")


app = dash.Dash(__name__, title="Product Intelligence")

app.layout = html.Div([
    dcc.Store(id='selected-item-store'),
    dcc.Store(id='selected-time-store'),
    dcc.Store(id='selected-customer-store'),
    dcc.Store(id='selected-month-store'),
    dcc.Store(id='dead-stock-item-store'),

    dcc.Download(id="download-snapshot"),

    # 1. HEADER SECTION
    html.Div([
        html.H1("Product Intelligence Portal", style={'textAlign': 'center', 'color': '#2c3e50'}),
        html.P(f"Data as of: {last_trans_date}",
               style={'textAlign': 'center', 'color': 'gray', 'marginBottom': '30px'}),

        # Control Panel Box
        html.Div([
            html.Label("Search:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.Dropdown(id='product-search', placeholder="Select...",
                         style={'width': '200px', 'marginRight': '15px'}),

            # --- UPDATE 3: Hierarchy Buttons ---
            html.Label("Hierarchy:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='hierarchy-select',
                           options=[
                               {'label': ' Group', 'value': 'Items'},  # Group -> Item
                               {'label': ' Sub-Group', 'value': 'Items 2'},  # Sub-Group -> Items 2
                               {'label': ' Item', 'value': 'Name'}  # Item -> Name
                           ],
                           value='Items',  # Default
                           inline=True,
                           style={'marginRight': '15px'}),

            html.Label("Segment:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.RadioItems(id='segment-filter', options=['All', 'B2B', 'B2C'], value='All', inline=True,
                           style={'marginRight': '15px'}),

            html.Label("Year:", style={'fontWeight': 'bold', 'marginRight': '5px'}),
            dcc.Dropdown(
                id='year-select',
                options=[{'label': str(y), 'value': y} for y in available_years],
                value=available_years[0],
                clearable=False,
                style={'width': '80px', 'marginRight': '15px'}
            ),

            dcc.RadioItems(id='time-view', options=['Month to Date', 'Year to Date'], value='Month to Date',
                           inline=True),

            html.Button("📸 Save Snapshot", id="btn-snapshot",
                        style={'marginLeft': '20px', 'backgroundColor': '#2c3e50', 'color': 'white',
                               'fontWeight': 'bold'})

        ], style={
            'display': 'flex', 'justifyContent': 'center', 'alignItems': 'center', 'flexWrap': 'wrap',
            'padding': '15px', 'backgroundColor': '#f9f9f9', 'borderRadius': '10px',
            'border': '1px solid #e0e0e0'
        })
    ], style={'padding': '40px', 'borderBottom': '1px solid #eee', 'scrollSnapAlign': 'start'}),

    # 2. TOP 20 ITEMS
    html.Div([
        html.H2("Ranked 1-20 (Click Bar to Select)", style={'textAlign': 'center', 'color': '#2980b9'}),
        dcc.Graph(id='items-1-20-bar', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 3. RANKS 21-40
    html.Div([
        html.H3("Ranked 21-40", style={'textAlign': 'center', 'color': '#7f8c8d'}),
        dcc.Graph(id='items-21-40-bar', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 4. RANKS 41-60
    html.Div([
        html.H3("Ranked 41-60", style={'textAlign': 'center', 'color': '#7f8c8d'}),
        dcc.Graph(id='items-41-60-bar', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 5. VARIANT BREAKDOWN (Note: Variants usually only apply to SKU level, but we will adapt)
    html.Div([
        html.H2(id='variant-title', children="Breakdown", style={'textAlign': 'center', 'color': '#8e44ad'}),
        dcc.Graph(id='variant-bar-chart', style=CHART_STYLE, )
    ], style=SECTION_STYLE),

    # 6. TREND ANALYSIS
    html.Div([
        html.H2(id='item-trend-title', children="Sales Trend", style={'textAlign': 'center', 'color': '#27ae60'}),
        html.P("Click a bar (Month) to filter the Daily Breakdown below.",
               style={'textAlign': 'center', 'color': 'gray'}),
        dcc.Graph(id='item-trend-bar', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 7. DAILY BREAKDOWN
    html.Div([
        html.H2(id='l4-title', children="Daily Breakdown (Global or Customer Specific)",
                style={'textAlign': 'center', 'color': '#16a085'}),
        html.P("Shows total daily sales. Click a Customer below to filter this chart.",
               style={'textAlign': 'center', 'color': 'gray'}),
        dcc.Graph(id='level-4-drilldown', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 8. CUSTOMER PARETO
    html.Div([
        html.H2(id='pareto-title', children="Customer Mix", style={'textAlign': 'center', 'color': '#d35400'}),
        html.P("Click a Customer bar to filter the Daily Chart above & History Chart below.",
               style={'textAlign': 'center', 'color': 'gray'}),
        html.Div([
            html.Label("Show Top:", style={'marginRight': '10px'}),
            dcc.RadioItems(id='pareto-threshold',
                           options=[{'label': 'Top 5', 'value': 5},
                                    {'label': 'Top 10', 'value': 10},
                                    {'label': 'Top 20', 'value': 20}],
                           value=10, inline=True)
        ], style={'textAlign': 'center', 'marginBottom': '10px'}),
        dcc.Graph(id='item-customer-pareto', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 9. CUSTOMER HISTORY
    html.Div([
        html.H2(id='final-drilldown-title', children="Customer Specific History (Monthly)",
                style={'textAlign': 'center', 'color': '#c0392b'}),
        html.P("Click a Month bar to filter the Daily Chart to that month.",
               style={'textAlign': 'center', 'color': 'gray'}),
        dcc.Graph(id='customer-item-trend', style=CHART_STYLE)
    ], style=SECTION_STYLE),

    # 10. DEAD STOCK SECTION (Always SKU based)
    html.Div([
        html.H2("Dormant Inventory (Dead Stock - SKU Level)", style={'textAlign': 'center', 'color': '#c0392b'}),
        dcc.Graph(id='dead-stock-chart', style={'height': '45vh', 'marginBottom': '20px'}),
        html.Div([
            html.Div([
                html.H4(id='dead-var-title', children="1. Inventory Variants (Colors/Sizes)",
                        style={'textAlign': 'center'}),
                dcc.Graph(id='dead-variant-chart', style={'height': '45vh'})
            ], style={'width': '48%', 'display': 'inline-block'}),
            html.Div([
                html.H4(id='dead-cust-title', children="2. Historical Buyers (Leads)", style={'textAlign': 'center'}),
                dcc.Graph(id='dead-customer-chart', style={'height': '45vh'})
            ], style={'width': '48%', 'display': 'inline-block', 'marginLeft': '4%'})
        ], style={'width': '100%'})
    ], style=SECTION_STYLE),

    # 11. METHODOLOGY
    html.Div([
        html.H2("Methodology & User Guide", style={'color': '#2c3e50', 'textAlign': 'left', 'marginBottom': '30px'}),
        html.Div([dcc.Markdown("Standard methodology...")], style={'maxWidth': '1000px', 'margin': '0 auto'})
    ], style={'minHeight': '50vh', 'width': '100%', 'scrollSnapAlign': 'start', 'padding': '60px',
              'backgroundColor': '#f9fbfb', 'display': 'flex', 'flexDirection': 'column',
              'justifyContent': 'flex-start'})

], style={'height': '100vh', 'overflowY': 'scroll', 'scrollSnapType': 'y proximity'})


# --- CALLBACKS ---

@callback(Output('product-search', 'options'),
              [Input('segment-filter', 'value'), Input('hierarchy-select', 'value')])
def update_search_options(segment, h_col):
    # Search options update based on hierarchy selection
    dff = df if segment == 'All' else df[df['B2B'] == segment]
    return [{'label': i, 'value': i} for i in sorted(dff[h_col].unique())]


@callback(
    [Output('items-1-20-bar', 'figure'), Output('items-21-40-bar', 'figure'), Output('items-41-60-bar', 'figure'),
     Output('dead-stock-chart', 'figure')],
    [Input('time-view', 'value'), Input('segment-filter', 'value'), Input('year-select', 'value'),
     Input('hierarchy-select', 'value')]
)
def update_rankings(view, segment, year, h_col):
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    max_date = df_filtered['Date'].max()
    target_year = int(year)

    today = pd.Timestamp.now()
    if view == 'Month to Date':
        current_df = df_filtered[(df_filtered['Date'].dt.year == target_year) & (
                df_filtered['Date'].dt.month == max_date.month)]
        label = max_date.strftime('%B %Y')
        if max_date.year == today.year and max_date.month == today.month:
            label += ' (MTD)'
    else:
        current_df = df_filtered[df_filtered['Date'].dt.year == target_year]
        label = str(target_year)
        if target_year == today.year:
            label += ' (YTD)'

    # Group by the Selected Hierarchy Column
    item_perf = current_df.groupby(h_col)[['Value', 'QTY PCS', 'Quantity']].sum().reset_index().sort_values('Value',
                                                                                                            ascending=False)

    def create_bar(d, title):
        if d.empty: return go.Figure(layout={'title': "No Data"})
        # Update Stats to use h_col
        stats = calculate_stats(d, df_filtered, target_year, max_date.month, view, col_name=h_col)
        d['Avg_Price'] = (d['Value'] / d['Quantity']).fillna(0)
        fig = px.bar(d, x='Value', y=h_col, orientation='h',
                     title=f"{title}<br><span style='font-size: 14px; color: gray'>{stats}</span>", text_auto='.2s')
        fig.update_traces(
            hovertemplate="<b>%{y}</b><br>Rev: %{x:,.2f}<br>Qty: %{customdata[0]:,.0f}<br>Bundles: %{customdata[1]:,.0f}<br>Avg Price: %{customdata[2]:,.2f}<extra></extra>",
            customdata=d[['QTY PCS', 'Quantity', 'Avg_Price']])
        fig.update_layout(yaxis={'categoryorder': 'total ascending', 'dtick': 1}, clickmode='event+select',
                          autosize=True)
        return fig

    # --- Dead Stock Logic (Kept at SKU 'Items' level for accuracy) ---
    last_sales = df_filtered.groupby('Items')['Date'].max().reset_index()
    item_pricing = df_filtered.groupby('Items').agg(Total_Val=('Value', 'sum'),
                                                    Total_Qty=('Quantity', 'sum')).reset_index()
    item_pricing['Avg_Price'] = (item_pricing['Total_Val'] / item_pricing['Total_Qty']).fillna(0)
    df_dead = pd.merge(df_soh, last_sales, on='Items', how='left')
    df_dead = pd.merge(df_dead, item_pricing[['Items', 'Avg_Price']], on='Items', how='left')
    df_dead['Days_Since_Sale'] = (max_date - df_dead['Date']).dt.days.fillna(999)
    df_dead['Value_Locked'] = df_dead['Qty On Hand'] * df_dead['Avg_Price']

    dead_stock_df = df_dead[(df_dead['Days_Since_Sale'] > 180) & (df_dead['Value_Locked'] > 0)].sort_values(
        'Value_Locked', ascending=False).head(10)

    fig_dead = px.bar(dead_stock_df, x='Value_Locked', y='Items', orientation='h', color='Days_Since_Sale',
                      color_continuous_scale='Tealgrn', title=f"Top 10 Dormant Items (> 6 Months) - {segment}",
                      text_auto='.2s')
    fig_dead.update_traces(
        hovertemplate="<b>%{y}</b><br>Value Locked: %{x:,.2f}<br>Stock: %{customdata[0]:,.0f}<br>Days: %{marker.color:.0f}<extra></extra>",
        customdata=dead_stock_df[['Qty On Hand']])
    fig_dead.update_layout(yaxis={'categoryorder': 'total ascending', 'dtick': 1}, clickmode='event+select',
                           autosize=True)

    return create_bar(item_perf.iloc[0:20], f"Ranks 1-20 ({label})"), create_bar(item_perf.iloc[20:40],
                                                                                 f"Ranks 21-40"), create_bar(
        item_perf.iloc[40:60], f"Ranks 41-60"), fig_dead


@callback(Output('dead-stock-item-store', 'data'), Input('dead-stock-chart', 'clickData'))
def store_dead_item(clickData):
    if clickData: return clickData['points'][0]['label']
    return None


@callback(
    [Output('dead-variant-chart', 'figure'), Output('dead-var-title', 'children'),
     Output('dead-customer-chart', 'figure'), Output('dead-cust-title', 'children')],
    [Input('dead-stock-item-store', 'data'), Input('segment-filter', 'value')]
)
def update_dead_stock_drilldown(item, segment):
    if not item:
        return (go.Figure(layout={'title': "Select Item Above"}), "Inventory Variants",
                go.Figure(layout={'title': "Select Item Above"}), "Historical Buyers")

    soh_variants = df_soh_raw[df_soh_raw['Items'] == item]
    if soh_variants.empty:
        fig_var = go.Figure(layout={'title': "No Inventory Data"})
    else:
        var_grp = soh_variants.groupby('Item Name')['Qty On Hand'].sum().reset_index().sort_values('Qty On Hand',
                                                                                                   ascending=False)
        sales_data = df[df['Items'] == item]

        def get_variant_metrics(row):
            v_name = row['Item Name'];
            v_sales = sales_data[sales_data['Item Name'] == v_name]
            if v_sales.empty: return pd.Series([0, 'Never'])
            avg_p = v_sales['Value'].sum() / v_sales['QTY PCS'].sum() if v_sales['QTY PCS'].sum() > 0 else 0
            return pd.Series([row['Qty On Hand'] * avg_p, v_sales['Date'].max().strftime('%d %b %Y')])

        var_grp[['Stock_Value', 'Last_Sold']] = var_grp.apply(get_variant_metrics, axis=1)

        fig_var = px.bar(var_grp, x='Qty On Hand', y='Item Name', orientation='h',
                         title=f"Colors/Size Breakdown: {item}", text_auto='.0f')
        fig_var.update_traces(
            marker_color='#e74c3c',
            hovertemplate="<b>%{y}</b><br>Stock Value: %{customdata[0]:,.2f}<br>Qty Hand: %{x:,.0f}<br>Last Sold: %{customdata[1]}<extra></extra>",
            customdata=var_grp[['Stock_Value', 'Last_Sold']]
        )
        fig_var.update_layout(yaxis={'categoryorder': 'total ascending'}, autosize=True)

    df_sales = df if segment == 'All' else df[df['B2B'] == segment]
    sales_hist = df_sales[df_sales['Items'] == item]
    if sales_hist.empty:
        fig_cust = go.Figure(layout={'title': "No History Found"})
    else:
        cust_grp = sales_hist.groupby('Customer Names').agg(
            Value=('Value', 'sum'),
            Qty=('Quantity', 'sum'),
            Last_Sold=('Date', 'max')
        ).reset_index().sort_values('Value', ascending=False).head(10)

        cust_grp['Avg_Price'] = (cust_grp['Value'] / cust_grp['Qty']).fillna(0)
        cust_grp['Last_Sold_Str'] = cust_grp['Last_Sold'].dt.strftime('%d %b %Y')

        fig_cust = px.bar(cust_grp, x='Value', y='Customer Names', orientation='h',
                          title=f"Top Past Buyers (Leads): {item}", text_auto='.2s')
        fig_cust.update_traces(
            marker_color='#3498db',
            hovertemplate="<b>%{y}</b><br>Rev: %{x:,.2f}<br>Qty Sold: %{customdata[0]:,.0f}<br>Avg Price: %{customdata[1]:,.2f}<br>Last Sold To: %{customdata[2]}<extra></extra>",
            customdata=cust_grp[['Qty', 'Avg_Price', 'Last_Sold_Str']]
        )
        fig_cust.update_layout(yaxis={'categoryorder': 'total ascending'}, autosize=True)

    return fig_var, f"Variants: {item}", fig_cust, f"Top Leads: {item}"


@callback([Output('variant-bar-chart', 'figure'), Output('variant-title', 'children')],
              [Input('selected-item-store', 'data'), Input('time-view', 'value'), Input('segment-filter', 'value'),
               Input('year-select', 'value'), Input('hierarchy-select', 'value')])
def update_variant(item, view, segment, year, h_col):
    if not item: return go.Figure(), "Breakdown (Select Item)"
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    max_date = df_filtered['Date'].max()
    target_year = int(year)

    if view == 'Month to Date':
        dff = df_filtered[
            (df_filtered['Date'].dt.year == target_year) & (df_filtered['Date'].dt.month == max_date.month)]
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == target_year]

    # Filter by the selected hierarchy column
    variant_df = dff[dff[h_col] == item]

    if variant_df.empty: return go.Figure(layout={'title': "No Sales"}), f"Breakdown: {item}"

    # We always break down by 'Item Name' (SKU description) as the next level of detail
    perf = variant_df.groupby('Item Name')[['Value', 'QTY PCS', 'Quantity']].sum().reset_index().sort_values('Value',
                                                                                                             ascending=False)
    perf['Avg_Price'] = (perf['Value'] / perf['Quantity']).fillna(0)

    # Calculate stats for the selected item in the selected hierarchy
    stats = calculate_stats(pd.DataFrame({h_col: [item]}), df_filtered, target_year, max_date.month, view,
                            col_name=h_col)

    fig = px.bar(perf, x='Value', y='Item Name', orientation='h', title=f"Mix: {item}<br>{stats}", text_auto='.2s')
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>Rev: %{x:,.2f}<br>Qty: %{customdata[0]:,.0f}<br>Bundles: %{customdata[1]:,.0f}<br>Avg: %{customdata[2]:,.2f}<extra></extra>",
        customdata=perf[['QTY PCS', 'Quantity', 'Avg_Price']])
    return fig, f"Breakdown: {item}"


@callback(
    [Output('selected-item-store', 'data'), Output('items-1-20-bar', 'selectedData'),
     Output('items-21-40-bar', 'selectedData'), Output('items-41-60-bar', 'selectedData'),
     Output('product-search', 'value')],
    [Input('items-1-20-bar', 'selectedData'), Input('items-21-40-bar', 'selectedData'),
     Input('items-41-60-bar', 'selectedData'), Input('product-search', 'value')])
def store_item(sel1, sel2, sel3, search_val):
    if ctx.triggered_id == 'product-search': return search_val, None, None, None, dash.no_update
    if ctx.triggered_id == 'items-1-20-bar' and sel1: return sel1['points'][0][
        'label'], dash.no_update, None, None, None
    if ctx.triggered_id == 'items-21-40-bar' and sel2: return sel2['points'][0][
        'label'], None, dash.no_update, None, None
    if ctx.triggered_id == 'items-41-60-bar' and sel3: return sel3['points'][0][
        'label'], None, None, dash.no_update, None
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update


@callback(Output('selected-time-store', 'data'),
              [Input('item-trend-bar', 'clickData'), Input('selected-item-store', 'data')],
              State('selected-time-store', 'data'))
def store_time(clickData, item_change, current_time):
    if ctx.triggered_id == 'selected-item-store': return None
    if clickData: return clickData['points'][0]['x']
    return None


@callback([Output('item-trend-bar', 'figure'), Output('item-trend-title', 'children')],
              [Input('selected-item-store', 'data'), Input('time-view', 'value'),
               Input('segment-filter', 'value'), Input('year-select', 'value'), Input('hierarchy-select', 'value')])
def update_trend(item, view, segment, year, h_col):
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    if not item: return go.Figure(), "Sales Trend (Select Item)"
    max_date = df_filtered['Date'].max()
    target_year = int(year)
    if view == 'Month to Date':
        dff = df_filtered[(df_filtered['Date'].dt.year == target_year) & (
                df_filtered['Date'].dt.month == max_date.month)];
        rule = 'D';
        xfmt = "%d %b"
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == target_year];
        rule = 'MS';
        xfmt = "%b %Y"

    # Filter by selected hierarchy column
    dff_item = dff[dff[h_col] == item]
    if dff_item.empty: return go.Figure(), f"Trend: {item}"

    stats = calculate_stats(pd.DataFrame({h_col: [item]}), df_filtered, target_year, max_date.month, view,
                            col_name=h_col)
    trend = dff_item.set_index('Date').resample(rule)[['Value', 'QTY PCS', 'Quantity']].sum().reset_index()
    trend['Avg_Price'] = (trend['Value'] / trend['Quantity']).fillna(0)

    fig = px.bar(trend, x='Date', y='Value', title=f"Trend: {item}<br>{stats}", text_auto='.2s')
    fig.update_traces(
        hovertemplate="<b>%{x}</b><br>Rev: %{y:,.2f}<br>Qty: %{customdata[0]:,.0f}<br>Bundles: %{customdata[1]:,.0f}<br>Avg: %{customdata[2]:,.2f}<extra></extra>",
        customdata=trend[['QTY PCS', 'Quantity', 'Avg_Price']])
    fig.update_xaxes(tickformat=xfmt, dtick="M1" if rule == 'MS' else 86400000.0)
    fig.update_layout(clickmode='event+select')
    return fig, f"Analysis: {item}"


@callback([Output('item-customer-pareto', 'figure'), Output('pareto-title', 'children')],
              [Input('selected-item-store', 'data'), Input('selected-time-store', 'data'), Input('time-view', 'value'),
               Input('segment-filter', 'value'), Input('pareto-threshold', 'value'), Input('year-select', 'value'),
               Input('hierarchy-select', 'value')])
def update_mix(item, date, view, segment, thres, year, h_col):
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    if not item: return go.Figure(), "Customer Mix (Select Item)"

    max_date_global = df_filtered['Date'].max()
    target_year = int(year)
    calc_month = max_date_global.month

    # 1. Base Filter (Year)
    if view == 'Month to Date':
        dff = df_filtered[
            (df_filtered['Date'].dt.year == target_year) & (df_filtered['Date'].dt.month == max_date_global.month)]
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == target_year]

    dff_item = dff[dff[h_col] == item]

    # 2. Date Click Filter
    if date:
        dt_sel = pd.to_datetime(date)
        calc_month = dt_sel.month
        if view == 'Month to Date':
            dff_item = dff_item[dff_item['Date'] == dt_sel]
        else:
            dff_item = dff_item[
                (dff_item['Date'].dt.year == dt_sel.year) & (dff_item['Date'].dt.month == dt_sel.month)]

    if dff_item.empty: return go.Figure(layout={'title': "No Sales"}), "Mix"

    grp = dff_item.groupby('Customer Names')[['Value', 'QTY PCS', 'Quantity']].sum().sort_values('Value',
                                                                                                 ascending=False).reset_index()

    # Top N Logic
    limit = int(thres) if thres else 10
    if len(grp) <= limit:
        final = grp
    else:
        top_n = grp.iloc[:limit].copy()
        others_val = grp.iloc[limit:]['Value'].sum()
        others_qty = grp.iloc[limit:]['QTY PCS'].sum()
        others_bnd = grp.iloc[limit:]['Quantity'].sum()

        others_row = pd.DataFrame({
            'Customer Names': ['Others'],
            'Value': [others_val],
            'QTY PCS': [others_qty],
            'Quantity': [others_bnd]
        })
        final = pd.concat([top_n, others_row], ignore_index=True)

    final['Avg_Price'] = (final['Value'] / final['Quantity']).fillna(0)

    # Stats Calc
    if date:
        item_spec = dff_item
        stats_view = 'Month to Date'
    else:
        item_spec = df_filtered[df_filtered[h_col] == item]
        if view == 'Month to Date':
            item_spec = item_spec[
                (item_spec['Date'].dt.year == target_year) & (item_spec['Date'].dt.month == max_date_global.month)]
        else:
            item_spec = item_spec[item_spec['Date'].dt.year == target_year]
        stats_view = view

    stats = calculate_stats(pd.DataFrame(), item_spec, target_year, calc_month, stats_view, col_name='Global')

    chart_title = f"Mix: {item}<br><span style='font-size: 12px; color: gray;'>{stats}</span>"

    fig = px.bar(final, x='Value', y='Customer Names', orientation='h', title=chart_title,
                 text_auto='.2s')

    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>Rev: %{x:,.2f}<br>Qty: %{customdata[0]:,.0f}<br>Bundles: %{customdata[1]:,.0f}<br>Avg Price: %{customdata[2]:,.2f}<extra></extra>",
        customdata=final[['QTY PCS', 'Quantity', 'Avg_Price']])

    fig.update_layout(yaxis={'categoryorder': 'total ascending'}, clickmode='event+select')

    return fig, "Customer Mix"


@callback(Output('selected-customer-store', 'data'),
              [Input('item-customer-pareto', 'selectedData'), Input('selected-item-store', 'data')])
def store_cust(sel, item):
    if ctx.triggered_id == 'selected-item-store': return None
    if sel: return sel['points'][0]['label']
    return None


@callback([Output('customer-item-trend', 'figure'), Output('final-drilldown-title', 'children')],
              [Input('selected-customer-store', 'data'), State('selected-item-store', 'data'),
               Input('time-view', 'value'), Input('segment-filter', 'value'), Input('year-select', 'value'),
               Input('hierarchy-select', 'value')])
def update_hist(cust, item, view, segment, year, h_col):
    if not cust or not item: return go.Figure(), "Customer Specific History (Monthly)"
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    max_date = df_filtered['Date'].max()
    target_year = int(year)
    if view == 'Month to Date':
        dff = df_filtered[(df_filtered['Date'].dt.year == target_year) & (
                df_filtered['Date'].dt.month == max_date.month)];
        rule = 'D';
        xfmt = "%d %b";
        dtick_val = 86400000.0
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == target_year];
        rule = 'MS';
        xfmt = "%b %Y";
        dtick_val = "M1"

    dff_spec = dff[(dff[h_col] == item) & (dff['Customer Names'] == cust)]
    if dff_spec.empty: return go.Figure(), "History"

    item_cust_hist = df_filtered[(df_filtered[h_col] == item) & (df_filtered['Customer Names'] == cust)]
    stats = calculate_stats(pd.DataFrame({h_col: [item]}), item_cust_hist, target_year, max_date.month, view,
                            col_name=h_col)

    trend = dff_spec.set_index('Date').resample(rule)[['Value', 'QTY PCS', 'Quantity']].sum().reset_index()
    trend['Avg_Price'] = (trend['Value'] / trend['Quantity']).fillna(0)

    fig = px.bar(trend, x='Date', y='Value', title=f"{item} -> {cust}<br>{stats}", text_auto='.2s')
    fig.update_traces(
        hovertemplate="<b>%{x}</b><br>Rev: %{y:,.2f}<br>Qty: %{customdata[0]:,.0f}<br>Bundles: %{customdata[1]:,.0f}<br>Avg: %{customdata[2]:,.2f}<extra></extra>",
        customdata=trend[['QTY PCS', 'Quantity', 'Avg_Price']])
    fig.update_xaxes(tickformat=xfmt, dtick=dtick_val)
    fig.update_layout(clickmode='event+select')
    return fig, "History"


@callback(Output('selected-month-store', 'data'),
              [Input('customer-item-trend', 'clickData'), Input('selected-customer-store', 'data')],
              State('time-view', 'value'))
def store_month(clickData, cust, view):
    if ctx.triggered_id == 'selected-customer-store': return None
    if clickData: return clickData['points'][0]['x']
    return None


@callback([Output('level-4-drilldown', 'figure'), Output('l4-title', 'children')],
              [Input('selected-item-store', 'data'),
               Input('selected-customer-store', 'data'),
               Input('selected-time-store', 'data'),
               Input('selected-month-store', 'data'),
               Input('segment-filter', 'value'),
               Input('time-view', 'value'),
               Input('year-select', 'value'),
               Input('hierarchy-select', 'value')])
def update_daily(item, cust, time_store, month_store, segment, view, year, h_col):
    if not item: return go.Figure(), "Daily Breakdown"

    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    target_year = int(year)
    max_date_global = df_filtered['Date'].max()

    # 1. Logic Split
    # FILTER 1: Customer (Global vs Specific)
    if cust:
        dff = df_filtered[(df_filtered[h_col] == item) & (df_filtered['Customer Names'] == cust)]
        df_context = dff
        title_suffix = f"{cust}"
        color_seq = ['#e74c3c']
    else:
        dff = df_filtered[df_filtered[h_col] == item]
        df_context = dff
        title_suffix = "All Customers"
        color_seq = ['#16a085']

    # FILTER 2: Time
    active_date = None
    if month_store:
        active_date = pd.to_datetime(month_store)
    elif time_store:
        active_date = pd.to_datetime(time_store)

    if active_date:
        current_data = dff[
            (dff['Date'].dt.year == active_date.year) &
            (dff['Date'].dt.month == active_date.month)
            ]
        calc_month = active_date.month
        calc_year = active_date.year
        date_title = active_date.strftime('%B %Y')
        x_dtick = 86400000.0

    elif view == 'Month to Date':
        current_data = dff[
            (dff['Date'].dt.year == target_year) &
            (dff['Date'].dt.month == max_date_global.month)
            ]
        calc_month = max_date_global.month
        calc_year = target_year
        date_title = f"{max_date_global.strftime('%B')} {target_year}"
        x_dtick = 86400000.0

    else:
        current_data = dff[dff['Date'].dt.year == target_year]
        calc_month = max_date_global.month
        calc_year = target_year
        date_title = str(target_year)
        x_dtick = None

    if current_data.empty: return go.Figure(layout={'title': "No Data"}), f"Daily: {title_suffix}"

    vc = current_data['Value'].sum();
    qc = current_data['QTY PCS'].sum();
    ac = vc / qc if qc > 0 else 0

    if view == 'Month to Date' or active_date:
        prev_data = df_context[
            (df_context['Date'].dt.year == calc_year - 1) &
            (df_context['Date'].dt.month == calc_month)
            ]
    else:
        prev_data = df_context[df_context['Date'].dt.year == calc_year - 1]

    vp = prev_data['Value'].sum();
    qp = prev_data['QTY PCS'].sum();
    ap = vp / qp if qp > 0 else 0

    g_val = ((vc - vp) / vp) * 100 if vp > 0 else 0
    d_val = "▲" if g_val >= 0 else "▼"
    c_val = "green" if g_val >= 0 else "red"

    g_qty = ((qc - qp) / qp) * 100 if qp > 0 else 0
    d_qty = "▲" if g_qty >= 0 else "▼"
    c_qty = "green" if g_qty >= 0 else "red"

    g_avg = ((ac - ap) / ap) * 100 if ap > 0 else 0
    d_avg = "▲" if g_avg >= 0 else "▼"
    c_avg = "green" if g_avg >= 0 else "red"

    stats = (f"Revenue: {vc:,.0f} (LY: {vp:,.0f}, <span style='color:{c_val}'>{d_val} {abs(g_val):.1f}%</span>) | "
             f"Qty: {qc:,.0f} (LY: {qp:,.0f}, <span style='color:{c_qty}'>{d_qty} {abs(g_qty):.1f}%</span>) | "
             f"Avg Price: {ac:,.2f} (LY: {ap:,.2f}, <span style='color:{c_avg}'>{d_avg} {abs(g_avg):.1f}%</span>)")

    trend = current_data.groupby('Date')[['Value', 'QTY PCS', 'Quantity']].sum().reset_index()
    trend['Avg_Price'] = (trend['Value'] / trend['Quantity']).fillna(0)

    fig = px.bar(trend, x='Date', y='Value',
                 title=f"Daily Sales: {title_suffix} ({date_title})<br><span style='font-size:12px; color:gray'>{stats}</span>",
                 text_auto='.2s', color_discrete_sequence=color_seq)

    fig.update_traces(
        hovertemplate="<b>%{x}</b><br>Rev: %{y:,.2f}<br>Qty: %{customdata[0]:,.0f}<br>Bundles: %{customdata[1]:,.0f}<br>Avg Price: %{customdata[2]:,.2f}<extra></extra>",
        customdata=trend[['QTY PCS', 'Quantity', 'Avg_Price']]
    )

    if x_dtick:
        fig.update_xaxes(tickformat="%d %b", dtick=x_dtick)
    else:
        fig.update_xaxes(tickformat="%d %b")

    return fig, "Daily Breakdown"


# --- SNAPSHOT CALLBACK ---
@callback(
    Output("product-reports-download-snapshot", "data"),
    Input("product-reports-btn-snapshot", "n_clicks"),
    [State('items-1-20-bar', 'figure'),
     State('variant-bar-chart', 'figure'),
     State('item-trend-bar', 'figure'),
     State('level-4-drilldown', 'figure'),
     State('item-customer-pareto', 'figure'),
     State('customer-item-trend', 'figure'),
     State('dead-stock-chart', 'figure'),
     State('dead-variant-chart', 'figure')],
    prevent_initial_call=True
)
def download_snapshot(n_clicks, fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8):
    if not n_clicks: return dash.no_update

    html_string = "<html><head><title>Product Intelligence Snapshot</title></head><body>"
    html_string += "<h1 style='text-align:center; font-family:sans-serif;'>Product Intelligence Snapshot</h1>"

    import plotly.io as pio
    for fig_data in [fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8]:
        if fig_data and 'data' in fig_data:
            fig = go.Figure(fig_data)
            html_string += pio.to_html(fig, full_html=False, include_plotlyjs='cdn')
            html_string += "<hr>"

    html_string += "</body></html>"
    return dict(content=html_string, filename="product_snapshot.html")


if __name__ == '__main__': app.run(debug=True, port=8056)