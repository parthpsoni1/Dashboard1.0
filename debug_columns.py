import pandas as pd

# Update this path if needed
file_path = r'C:\Users\perth.soni\PycharmProjects\DashBoard\Raws\Transactions from Jan to Sep 25.xlsx'

try:
    print("--- Loading Excel File ---")
    df = pd.read_excel(file_path, sheet_name='Sheet 2')

    print("\n--- Column Names Found ---")
    print(df.columns.tolist())

    if 'B2B' in df.columns:
        print("\n--- Unique Values in 'B2B' Column ---")
        unique_vals = df['B2B'].unique()
        print(unique_vals)

        print("\n--- Value Counts ---")
        print(df['B2B'].value_counts())
    else:
        print("\nERROR: 'B2B' column NOT found!")
        # Print first 5 rows to visually check headers
        print("\n--- First 5 Rows ---")
        print(df.head())

except Exception as e:
    print(f"Error: {e}")


"""
# Product_Intelligence.py (Fixed Dormant Items Chart)
import dash
from dash import html, dcc, Input, Output, State, ctx
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import sys

# --- Data Loading ---
file_path = r'C:\Users\perth.soni\PycharmProjects\DashBoard\Raws\Transactions from Jan to Sep 25.xlsx'
try:
    df = pd.read_excel(file_path, sheet_name='Sheet 2')
    df['Date'] = pd.to_datetime(df['Date'])
    df['Customer Names'] = df['Customer Names'].astype(str).replace('nan', 'Unknown')
    df['Items'] = df['Items'].astype(str).replace('nan', 'Unknown')

    df_soh_raw = pd.read_excel(file_path, sheet_name='QIH')
    df_soh = df_soh_raw.groupby('Items')['Qty On Hand'].sum().reset_index()

    if 'B2B' not in df.columns: df['B2B'] = 'All'
    df['B2B'] = df['B2B'].astype(str).str.strip()

    last_trans_date = df['Date'].max().strftime('%d %b %Y')
except Exception:
    sys.exit(1)

SECTION_STYLE = {
    'minHeight': '100vh', 'width': '100%', 'scrollSnapAlign': 'start',
    'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center',
    'padding': '40px', 'boxSizing': 'border-box', 'borderBottom': '1px solid #eee'
}

app = dash.Dash(__name__, title="Product Intelligence")

app.layout = html.Div([
    dcc.Store(id='selected-item-store'),
    dcc.Store(id='selected-time-store'),
    dcc.Store(id='selected-customer-store'),
    dcc.Store(id='selected-month-store'),

    # SECTION 1: CONTROLS
    html.Div([
        html.H1("Product Intelligence Portal", style={'textAlign': 'center', 'marginBottom': '0'}),
        html.P(f"Data current as of: {last_trans_date}",
               style={'textAlign': 'center', 'color': 'gray', 'marginTop': '5px', 'marginBottom': '30px'}),

        html.Div([
            html.Label("Search Item:", style={'fontWeight': 'bold', 'marginRight': '10px', 'color': '#2c3e50'}),
            dcc.Dropdown(
                id='product-search',
                options=[{'label': i, 'value': i} for i in sorted(df['Items'].unique())],
                placeholder="Search for any item...",
                style={'width': '300px', 'textAlign': 'left'},
                clearable=True
            ),
            html.Label("Segment:",
                       style={'fontWeight': 'bold', 'marginRight': '10px', 'marginLeft': '20px', 'color': '#2c3e50'}),
            dcc.RadioItems(id='segment-filter', options=['All', 'B2B', 'B2C'], value='All', inline=True,
                           style={'marginRight': '30px'}),
            html.A("← Executive", href="http://127.0.0.1:8050", style={'marginRight': '20px', 'fontSize': '18px'}),
            dcc.RadioItems(id='time-view', options=['Month to Date', 'Year to Date'], value='Month to Date',
                           inline=True, style={'fontSize': '18px'}),
        ], style={'display': 'flex', 'justifyContent': 'center', 'alignItems': 'center', 'padding': '20px',
                  'backgroundColor': '#f9f9f9', 'borderRadius': '10px'}),

        html.H4("⬇️ Scroll Down for Item Rankings",
                style={'textAlign': 'center', 'color': 'gray', 'marginTop': '50px', 'animation': 'bounce 2s infinite'})
    ], style=SECTION_STYLE),

    # RANKINGS
    html.Div([html.H3("Items Ranked 1 - 20 (Best Sellers)",
                      style={'textAlign': 'center', 'fontSize': '24px', 'color': 'green'}),
              html.P("Select a bar to view Item Analysis below", style={'textAlign': 'center', 'color': 'gray'}),
              dcc.Graph(id='items-1-20-bar', style={'height': '80vh'})], style=SECTION_STYLE),
    html.Div([html.H3("Items Ranked 21 - 40", style={'textAlign': 'center', 'fontSize': '24px'}),
              dcc.Graph(id='items-21-40-bar', style={'height': '80vh'})], style=SECTION_STYLE),
    html.Div([html.H3("Items Ranked 41 - 60", style={'textAlign': 'center', 'fontSize': '24px'}),
              dcc.Graph(id='items-41-60-bar', style={'height': '80vh'})], style=SECTION_STYLE),

    # TREND & MIX
    html.Div([html.H2(id='item-trend-title', children="Item Analysis: Trend",
                      style={'textAlign': 'center', 'marginBottom': '10px'}),
              html.P("Click a Time Bar below to see WHO bought during that period. Click again to Reset.",
                     style={'textAlign': 'center', 'color': '#e67e22', 'fontWeight': 'bold'}),
              dcc.Graph(id='item-trend-bar', style={'height': '80vh'})], style=SECTION_STYLE),
    html.Div([html.H2(id='pareto-title', children="Who buys this? (Customer Mix)",
                      style={'textAlign': 'center', 'marginBottom': '10px'}), html.Div(
        [html.Label("Top Customers to Show:", style={'fontWeight': 'bold', 'marginRight': '10px'}),
         dcc.RadioItems(id='pareto-threshold',
                        options=[{'label': 'Top 5', 'value': 5}, {'label': 'Top 10', 'value': 10},
                                 {'label': 'Top 20', 'value': 20}], value=10, inline=True)],
        style={'textAlign': 'center', 'marginBottom': '10px'}), html.P("Select a Customer to drill further",
                                                                       style={'textAlign': 'center', 'fontSize': '14px',
                                                                              'color': 'gray', 'marginBottom': '20px'}),
              dcc.Graph(id='item-customer-pareto', style={'height': '75vh'})], style=SECTION_STYLE),

    # HISTORY & DAILY
    html.Div([html.H2(id='final-drilldown-title', children="Customer Specific History",
                      style={'textAlign': 'center', 'color': '#2980b9'}),
              html.P(id='l3-subtitle', children="Click a Month bar to see daily breakdown (Year View Only)",
                     style={'textAlign': 'center', 'color': 'orange', 'fontWeight': 'bold'}),
              dcc.Graph(id='customer-item-trend', style={'height': '80vh'})], style=SECTION_STYLE),
    html.Div([html.H2(id='l4-title', children="Daily Breakdown (Specific Month)",
                      style={'textAlign': 'center', 'color': '#8e44ad'}),
              dcc.Graph(id='level-4-drilldown', style={'height': '80vh'})], style=SECTION_STYLE),
    html.Div(
        [html.H2("⚠️ Dormant Inventory Risk (> 6 Months No Sales)", style={'textAlign': 'center', 'color': '#c0392b'}),
         dcc.Graph(id='dead-stock-chart', style={'height': '80vh'})], style=SECTION_STYLE),

    # FOOTER
    html.Div([
        html.H2("Methodology & User Guide", style={'color': '#2c3e50'}),
        dcc.Markdown('''
        ### Analysis Workflow (Drill-Down)
        1.  **Search or Select:** Use the search bar or click a ranking bar to select an Item. **Selection is mutually exclusive.**
        2.  **Item Trend:** Shows sales history. **Click a bar** to filter the Customer Mix chart. **Click again to reset.**
        3.  **Customer Mix:** Shows top buyers. Click a customer bar to drill down.
        4.  **Customer History:** Shows when that customer bought the item.
        5.  **Daily Breakdown:** In Year View, click a month to see daily transactions.
        ''', style={'fontSize': '16px', 'lineHeight': '1.6'})
    ], style={'minHeight': '50vh', 'padding': '50px', 'backgroundColor': '#f4f6f6', 'display': 'flex',
              'flexDirection': 'column', 'justifyContent': 'center'})

], style={'height': '100vh', 'overflowY': 'scroll', 'scrollSnapType': 'y proximity'})


# --- CALLBACKS ---
@app.callback(
    [Output('items-1-20-bar', 'figure'), Output('items-21-40-bar', 'figure'), Output('items-41-60-bar', 'figure'),
     Output('dead-stock-chart', 'figure')], [Input('time-view', 'value'), Input('segment-filter', 'value')])
def update_rankings(view, segment):
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    if df_filtered.empty: return [go.Figure(layout={'title': "No Data"})] * 4
    max_date = df_filtered['Date'].max()
    if pd.isnull(max_date): return [go.Figure(layout={'title': "No Data"})] * 4

    if view == 'Month to Date':
        current_df = df_filtered[
            (df_filtered['Date'].dt.year == max_date.year) & (df_filtered['Date'].dt.month == max_date.month)]
        label = max_date.strftime('%B %Y')
    else:
        current_df = df_filtered[df_filtered['Date'].dt.year == max_date.year]
        label = str(max_date.year)

    item_perf = current_df.groupby('Items')['Value'].sum().reset_index().sort_values('Value', ascending=False)

    def create_bar(d, title):
        fig = px.bar(d, x='Value', y='Items', orientation='h', title=title, text_auto='.2s')
        fig.update_layout(yaxis={'categoryorder': 'total ascending'}, clickmode='event+select', autosize=True)
        return fig

    # FIXED: DEAD STOCK CALCULATION
    last_sales = df_filtered.groupby('Items')['Date'].max().reset_index()
    item_pricing = df_filtered.groupby('Items').agg(Total_Val=('Value', 'sum'),
                                                    Total_Qty=('QTY PCS', 'sum')).reset_index()
    item_pricing['Avg_Price'] = item_pricing['Total_Val'] / item_pricing['Total_Qty']

    df_dead = pd.merge(df_soh, last_sales, on='Items', how='left')
    df_dead = pd.merge(df_dead, item_pricing[['Items', 'Avg_Price']], on='Items', how='left')
    df_dead['Days_Since_Sale'] = (max_date - df_dead['Date']).dt.days.fillna(999)
    df_dead['Value_Locked'] = df_dead['Qty On Hand'] * df_dead['Avg_Price']

    # FILTER: ONLY SHOW ITEMS WITH VALUE > 0 TO AVOID EMPTY BARS
    dead_stock_df = df_dead[(df_dead['Days_Since_Sale'] > 180) & (df_dead['Value_Locked'] > 0)].sort_values(
        'Value_Locked', ascending=False).head(20)

    fig_dead = px.bar(dead_stock_df, x='Value_Locked', y='Items', orientation='h', color='Days_Since_Sale',
                      color_continuous_scale='Reds', title=f"Top 20 Dormant Items ({segment})",
                      text_auto='.2s') if not dead_stock_df.empty else go.Figure(
        layout={'title': "No Dead Stock Found"})
    fig_dead.update_layout(yaxis={'categoryorder': 'total ascending'}, autosize=True)

    return create_bar(item_perf.iloc[0:20], f"Ranks 1-20 ({label})"), create_bar(item_perf.iloc[20:40],
                                                                                 f"Ranks 21-40 ({label})"), create_bar(
        item_perf.iloc[40:60], f"Ranks 41-60 ({label})"), fig_dead


# --- 1. SELECTION LOGIC ---
@app.callback(
    [Output('selected-item-store', 'data'),
     Output('items-1-20-bar', 'selectedData'),
     Output('items-21-40-bar', 'selectedData'),
     Output('items-41-60-bar', 'selectedData'),
     Output('product-search', 'value')],
    [Input('items-1-20-bar', 'selectedData'),
     Input('items-21-40-bar', 'selectedData'),
     Input('items-41-60-bar', 'selectedData'),
     Input('product-search', 'value')]
)
def store_selected_item(sel1, sel2, sel3, search_val):
    trigger = ctx.triggered_id
    if not trigger: return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    if trigger == 'product-search': return search_val, None, None, None, dash.no_update

    if trigger == 'items-1-20-bar':
        if sel1 and sel1['points']:
            return sel1['points'][0]['label'], dash.no_update, None, None, None
        elif not sel2 and not sel3:
            return None, dash.no_update, None, None, None
    elif trigger == 'items-21-40-bar':
        if sel2 and sel2['points']:
            return sel2['points'][0]['label'], None, dash.no_update, None, None
        elif not sel1 and not sel3:
            return None, None, dash.no_update, None, None
    elif trigger == 'items-41-60-bar':
        if sel3 and sel3['points']:
            return sel3['points'][0]['label'], None, None, dash.no_update, None
        elif not sel1 and not sel2:
            return None, None, None, dash.no_update, None

    return dash.no_update, dash.no_update, dash.no_update, dash.no_update, dash.no_update


# --- 2. TIME SELECTION ---
@app.callback(Output('selected-time-store', 'data'),
              [Input('item-trend-bar', 'selectedData'), Input('selected-item-store', 'data')],
              State('selected-time-store', 'data'))
def store_time_selection(selectedData, item_change, current_time):
    if ctx.triggered_id == 'selected-item-store': return None
    if selectedData and selectedData['points']: return selectedData['points'][0]['x']
    return None


@app.callback([Output('item-trend-bar', 'figure'), Output('item-trend-title', 'children')],
              [Input('selected-item-store', 'data'), Input('time-view', 'value'), Input('segment-filter', 'value')])
def update_trend_chart(selected_item, view, segment):
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    if not selected_item: return go.Figure(layout={'title': "Select an Item above or Search"}), "Item Analysis"
    max_date = df_filtered['Date'].max()
    if pd.isnull(max_date): return go.Figure(), "No Data"

    if view == 'Month to Date':
        dff = df_filtered[
            (df_filtered['Date'].dt.year == max_date.year) & (df_filtered['Date'].dt.month == max_date.month)]
        rule = 'D';
        x_fmt = "%d %b"
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == max_date.year]
        rule = 'MS';
        x_fmt = "%b %Y"

    dff_item = dff[dff['Items'] == selected_item]
    if dff_item.empty: return go.Figure(layout={'title': "No Sales"}), f"Analysis: {selected_item}"

    df_trend = dff_item.set_index('Date').resample(rule)['Value'].sum().reset_index()
    fig_trend = px.bar(df_trend, x='Date', y='Value',
                       title=f"Sales Trend: {selected_item} (Click Bar to Filter Customers)", text_auto='.2s')
    fig_trend.update_xaxes(tickformat=x_fmt);
    fig_trend.update_layout(clickmode='event+select', autosize=True)
    return fig_trend, f"Analysis: {selected_item}"


@app.callback([Output('item-customer-pareto', 'figure'), Output('pareto-title', 'children')],
              [Input('selected-item-store', 'data'), Input('selected-time-store', 'data'), Input('time-view', 'value'),
               Input('segment-filter', 'value'), Input('pareto-threshold', 'value')])
def update_pareto(selected_item, selected_date, view, segment, threshold):
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    if not selected_item: return go.Figure(layout={'title': "Select an Item above or Search"}), "Who buys this?"
    max_date = df_filtered['Date'].max()
    if pd.isnull(max_date): return go.Figure(), "Who buys this?"

    if view == 'Month to Date':
        dff = df_filtered[
            (df_filtered['Date'].dt.year == max_date.year) & (df_filtered['Date'].dt.month == max_date.month)]
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == max_date.year]

    dff_item = dff[dff['Items'] == selected_item]
    title_suffix = "(Global)"
    if selected_date:
        sel_dt = pd.to_datetime(selected_date)
        if view == 'Daily Breakdown' or view == 'Month to Date':
            dff_item = dff_item[dff_item['Date'] == sel_dt]
            title_suffix = f"({sel_dt.strftime('%d %b')})"
        else:
            dff_item = dff_item[(dff_item['Date'].dt.year == sel_dt.year) & (dff_item['Date'].dt.month == sel_dt.month)]
            title_suffix = f"({sel_dt.strftime('%b %Y')})"

    if dff_item.empty: return go.Figure(
        layout={'title': "No Sales in selected period"}), f"Who bought {selected_item} {title_suffix}?"

    cust_group = dff_item.groupby('Customer Names')['Value'].sum().sort_values(ascending=False).reset_index()
    limit = int(threshold)
    if len(cust_group) <= limit:
        final_cust_df = cust_group
        chart_title = f"All Customers: {selected_item}"
    else:
        top_n = cust_group.iloc[:limit]
        others_val = cust_group.iloc[limit:]['Value'].sum()
        others_row = pd.DataFrame({'Customer Names': ['Others (Rest)'], 'Value': [others_val]})
        final_cust_df = pd.concat([top_n, others_row])
        chart_title = f"Top {limit} Customers + Others: {selected_item}"

    fig_pareto = px.bar(final_cust_df, x='Value', y='Customer Names', orientation='h',
                        title=f"{chart_title} {title_suffix}", text_auto='.2s')
    fig_pareto.update_layout(yaxis={'categoryorder': 'total ascending'}, clickmode='event+select', autosize=True)
    return fig_pareto, f"Who bought {selected_item} {title_suffix}?"


@app.callback(Output('selected-customer-store', 'data'),
              [Input('item-customer-pareto', 'selectedData'), Input('selected-item-store', 'data')])
def store_selected_customer(selectedData, item_changed):
    if ctx.triggered_id == 'selected-item-store': return None
    if selectedData and selectedData['points']: return selectedData['points'][0]['label']
    return None


@app.callback([Output('customer-item-trend', 'figure'), Output('final-drilldown-title', 'children'),
               Output('l3-subtitle', 'children')],
              [Input('selected-customer-store', 'data'), State('selected-item-store', 'data'),
               Input('time-view', 'value'), Input('segment-filter', 'value')])
def update_specific_history(customer, item, view, segment):
    if not customer or not item: return go.Figure(
        layout={'title': "Select a Customer above"}), "Customer Specific History", ""
    if 'Others' in customer: return go.Figure(layout={'title': "Cannot drill into Others"}), "Aggregate Data", ""
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    max_date = df_filtered['Date'].max()
    if view == 'Month to Date':
        dff = df_filtered[
            (df_filtered['Date'].dt.year == max_date.year) & (df_filtered['Date'].dt.month == max_date.month)]
        rule = 'D';
        x_fmt = "%d %b";
        subtitle = "Daily view already active"
    else:
        dff = df_filtered[df_filtered['Date'].dt.year == max_date.year]
        rule = 'MS';
        x_fmt = "%b %Y";
        subtitle = "Click a Month bar below to see Daily Breakdown"
    dff_specific = dff[(dff['Items'] == item) & (dff['Customer Names'] == customer)]
    if dff_specific.empty: return go.Figure(layout={'title': "No Data"}), f"{customer} buying {item}", ""
    df_trend = dff_specific.set_index('Date').resample(rule)['Value'].sum().reset_index()
    fig = px.bar(df_trend, x='Date', y='Value', title=f"Purchases of {item} by {customer}", text_auto='.2s')
    fig.update_xaxes(tickformat=x_fmt);
    fig.update_layout(clickmode='event+select', autosize=True)
    return fig, f"History: {customer} buying {item}", subtitle


@app.callback(Output('selected-month-store', 'data'),
              [Input('customer-item-trend', 'selectedData'), Input('selected-customer-store', 'data')],
              State('time-view', 'value'))
def store_month(selectedData, customer_change, view):
    if ctx.triggered_id == 'selected-customer-store': return None
    if view == 'Year to Date' and selectedData and selectedData['points']: return selectedData['points'][0]['x']
    return None


@app.callback([Output('level-4-drilldown', 'figure'), Output('l4-title', 'children')],
              [Input('selected-month-store', 'data'), State('selected-customer-store', 'data'),
               State('selected-item-store', 'data'), Input('segment-filter', 'value')])
def update_level4(month_date_str, customer, item, segment):
    if not month_date_str or not customer or not item: return go.Figure(
        layout={'title': "Drilldown inactive (Select a Month above)"}), "Daily Breakdown"
    target_month_dt = pd.to_datetime(month_date_str)
    df_filtered = df if segment == 'All' else df[df['B2B'] == segment]
    dff_daily = df_filtered[(df_filtered['Items'] == item) & (df_filtered['Customer Names'] == customer) & (
                df_filtered['Date'].dt.year == target_month_dt.year) & (
                                        df_filtered['Date'].dt.month == target_month_dt.month)]
    if dff_daily.empty: return go.Figure(layout={'title': "No Daily Data"}), "Daily Breakdown"
    df_trend = dff_daily.set_index('Date').resample('D')['Value'].sum().reset_index()
    month_name = target_month_dt.strftime('%B %Y')
    fig = px.bar(df_trend, x='Date', y='Value', title=f"Daily Purchases: {month_name}", text_auto='.2s')
    fig.update_xaxes(tickformat="%d %b");
    fig.update_traces(marker_color='#8e44ad');
    fig.update_layout(autosize=True)
    return fig, f"Daily Breakdown: {month_name}"


if __name__ == '__main__':
    app.run(debug=True, port=8052)
"""