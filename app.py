import dash
from dash import html

app = dash.Dash(__name__)

# ... your callbacks and layout routing here ...

# Expose the underlying Flask server for the WSGI to hook into
server = app.server

if __name__ == '__main__':
    # This block will ONLY run if you execute this file directly in an IDE or terminal for testing.
    # It will NOT run when Waitress serves the app in production.
    app.run(debug=False, host='127.0.0.1', port=8050)