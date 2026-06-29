import os
from pyngrok import ngrok
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("NGROK_TOKEN")
if token:
    ngrok.set_auth_token(token)
    tunnels = ngrok.get_tunnels()
    print(f"Active Tunnels: {len(tunnels)}")
    for t in tunnels:
        print(f"URL: {t.public_url} -> {t.config['addr']}")
else:
    print("NGROK_TOKEN not found in .env")
