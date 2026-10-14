# File: pages/login_page.py
from dash import html, dcc

layout = html.Div([
    html.Div([
        html.H2("Please Log In"),

        # Username Input
        dcc.Input(id='login-username', type='text', placeholder='Username'),

        # Password Input
        dcc.Input(id='login-password', type='password', placeholder='Password'),

        # Login Button
        html.Button('Login', id='login-button', n_clicks=0),

        # Area to show error messages (like "Wrong password")
        html.Div(id='login-output', style={'color': 'red', 'marginTop': '10px'})

    ], style={'textAlign': 'center', 'marginTop': '100px'})  # Simple centering styling
])