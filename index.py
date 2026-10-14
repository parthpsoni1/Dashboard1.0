import dash
from dash import dcc, html, Input, Output, State, ctx
from app import app

from loguru import logger
import sys
# --- LOGURU CONFIGURATION ---
logger.remove() # Remove default generic logger
# 1. Terminal Output (Colorful & Readable)
logger.add(sys.stdout, colorize=True, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>")
# 2. File Output (Auto-rotates at 10MB, keeps for 30 days)
logger.add("logs/dashboard_{time}.log", rotation="10 MB", retention="30 days", level="INFO")

logger.info("🟢 Server initializing... Loading modules.")


import data_loader
from Pages import V1, Rep_Dashboard, Product_Intelligence, login_page, Customer_Intelligence, \
    Customer_Comparision, Regional_Intelligence, Stacked_Intelligence, Pricing_Intelligence, InvoiceChecker, \
    Experimental_Customer_Comparison, Pivot_Explorer, Admin_Panel, Data_Export


# 1. IMPORT YOUR NEW IAM MODULE
from users import USER_DATABASE


# --- CONFIGURATION ---
PAGE_REGISTRY = {
    '/home': {'layout': V1.layout, 'name': 'Executive Dashboard'},
    '/sales_reps': {'layout': Rep_Dashboard.layout, 'name': 'Sales Reps'},
    '/products': {'layout': Product_Intelligence.layout, 'name': 'Product Intelligence'},
    '/customers': {'layout': Customer_Intelligence.layout, 'name': 'Customer Intelligence'},
    '/customers_comparison': {'layout': Customer_Comparision.layout, 'name': 'Customer Comparison'},
    '/regional_intelligence': {'layout': Regional_Intelligence.layout, 'name': 'Regional Logistics'},
    '/stacked': {'layout': Stacked_Intelligence.layout, 'name': 'Stacked Analysis'},
    '/pricing_intelligence': {'name': 'Pricing Intelligence', 'layout': Pricing_Intelligence.layout},
    '/Invoice_checker': {'name': 'Invoice Checking', 'layout': InvoiceChecker.layout},
    '/experimental_customer_comparison': {'name': 'Advanced Analytics', 'layout': Experimental_Customer_Comparison.layout},
    '/pivot_explorer': {'name': 'Pivot Explorer', 'layout': Pivot_Explorer.layout},
    '/data_export': {'name': 'Data Export', 'layout': Data_Export.layout},
    # Hidden from sidebar — admin only
    '/admin_panel': {'name': 'Admin Panel', 'layout': Admin_Panel.layout, 'hidden': True, 'admin_only': True},
}

# --- MAIN LAYOUT ---
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='auth-store', storage_type='session'),
    html.Div(id='page-content', style={'width': '100%', 'height': '100vh', 'fontFamily': 'Segoe UI, sans-serif'})
])


@app.callback(
    Output('page-content', 'children'),
    [Input('url', 'pathname'), Input('auth-store', 'data')]
)
def display_page(pathname, auth_data):
    # 1. UNAUTHENTICATED ATTEMPTS
    if not auth_data:
        if pathname not in ['/', '/login']:
            logger.warning(f"🛡️ ACCESS DENIED: Unauthenticated user tried to access '{pathname}'")
        return login_page.layout

    username = auth_data.get('username')
    user_config = USER_DATABASE.get(username)
    if not user_config: return login_page.layout

    allowed_pages = user_config['allowed_pages']

    # 2. TRACK SUCCESSFUL PAGE VISITS
    # We skip logging '/' and '/login' so we don't spam the logs during the redirect phase
    if pathname not in ['/', '/login']:
        # Look up the readable name of the page from your registry (e.g., "Executive Dashboard")
        page_name = PAGE_REGISTRY.get(pathname, {}).get('name', 'Unknown Page')
        logger.info(f"👁️ PAGE VISIT: User '{username}' is viewing [{page_name}] ({pathname})")

    if pathname == '/' or pathname == '/login':
        if allowed_pages == 'all': return dcc.Location(pathname='/home', id='redirect-home')
        if isinstance(allowed_pages, list) and len(allowed_pages) > 0:
            return dcc.Location(pathname=allowed_pages[0], id='redirect-user')
        return html.Div("Error: No pages assigned.")

    has_access = (allowed_pages == 'all') or (pathname in allowed_pages)

    if pathname in PAGE_REGISTRY:
        if has_access:
            sidebar_links = []
            for path, config in PAGE_REGISTRY.items():
                if config.get('hidden'):
                    continue
                if allowed_pages == 'all' or path in allowed_pages:
                    style = {'display': 'block', 'padding': '10px 15px', 'textDecoration': 'none', 'color': '#34495e',
                             'fontSize': '13px', 'borderBottom': '1px solid #eee'}
                    if path == pathname:
                        style.update({'backgroundColor': '#f0f2f5', 'fontWeight': 'bold', 'color': '#2c3e50',
                                      'borderLeft': '4px solid #2c3e50'})
                    sidebar_links.append(dcc.Link(config['name'], href=path, style=style))

            # Add Logout Button at the bottom of the links
            logout_style = {'display': 'block', 'padding': '10px 15px', 'textDecoration': 'none', 'color': '#e74c3c',
                            'fontSize': '13px', 'borderTop': '1px solid #eee', 'fontWeight': 'bold',
                            'cursor': 'pointer'}
            sidebar_links.append(html.Div(
                html.A("Log Out", id='logout-link', style=logout_style),
                style={'marginTop': 'auto', 'position': 'absolute', 'bottom': '20px', 'width': '100%'}
            ))

            # Admin-only pages: check role
            page_cfg = PAGE_REGISTRY[pathname]
            if page_cfg.get('admin_only') and user_config.get('role') != 'admin':
                return html.Div([html.H3("403 - Admin Access Required"),
                                 dcc.Link("Go Back", href='/home')])

            return html.Div([
                # Sidebar — hidden pages are not shown in nav links
                html.Div(sidebar_links, style={'width': '14%', 'position': 'fixed', 'top': 0, 'left': 0, 'bottom': 0,
                                               'backgroundColor': 'white', 'borderRight': '1px solid #ddd',
                                               'paddingTop': '15px', 'zIndex': 1000}),
                # Content Area
                html.Div(page_cfg['layout'],
                         style={'marginLeft': '14%', 'padding': '0', 'backgroundColor': '#fdfdfd'})
            ])
        else:
            # 3. TRACK UNAUTHORIZED ROLE ATTEMPTS
            logger.warning(
                f"🚫 PERMISSION DENIED: User '{username}' tried to access '{pathname}' but lacks role permissions.")
            return html.Div([html.H3("403 - Access Denied"), dcc.Link("Go Back", href='/home')])

    return html.Div([html.H3("404 - Page not found"), dcc.Link("Go Back", href="/")])


# --- AUTHENTICATION CALLBACKS ---

@app.callback(
    [Output('auth-store', 'data', allow_duplicate=True), Output('login-output', 'children')],
    [Input('login-button', 'n_clicks')],
    [State('login-username', 'value'), State('login-password', 'value')],
    prevent_initial_call=True
)
def login_auth(n_clicks, username, password):
    if not n_clicks: return None, ""

    user = USER_DATABASE.get(username)
    if user and user['password'] == password:
        session_data = {
            'username': username,
            'role': user.get('role', 'rep'),
            'rep_name': user.get('rep_name', ''),
            'ui_permissions': user.get('ui_permissions', {})
        }
        # <-- LOG SUCCESSFUL LOGIN -->
        logger.success(f"🔓 LOGIN SUCCESS: '{username}' logged in. (Role: {session_data['role']})")
        return session_data, ""

    # <-- LOG FAILED LOGIN ATTEMPT -->
    logger.warning(f"❌ LOGIN FAILED: Invalid attempt for username '{username}'")
    return None, "Invalid credentials"


# LOGOUT CALLBACK
@app.callback(
    Output('auth-store', 'data', allow_duplicate=True),
    Input('logout-link', 'n_clicks'),
    State('auth-store', 'data'),  # <-- Added State to know WHO logged out
    prevent_initial_call=True
)
def process_logout(n_clicks, auth_data):
    if n_clicks:
        username = auth_data.get('username', 'Unknown') if auth_data else 'Unknown'
        logger.info(f"🔒 LOGOUT: '{username}' logged out.")
        return None
    return dash.no_update

server = app.server

# Register the hidden admin upload endpoint
from upload_route import register_upload_route
register_upload_route(server)

if __name__ == '__main__':
    app.run(debug=False, port=8090, host='127.0.0.1')