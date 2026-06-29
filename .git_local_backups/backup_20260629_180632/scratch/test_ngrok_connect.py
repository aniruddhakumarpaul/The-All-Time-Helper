import os
import time
from pyngrok import ngrok
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("NGROK_TOKEN")
if token:
    try:
        ngrok.set_auth_token(token)
        print("Connecting...")
        tunnel = ngrok.connect(9000)
        print(f"Success! URL: {tunnel.public_url}")
        time.sleep(5)
        ngrok.disconnect(tunnel.public_url)
    except Exception as e:
        print(f"Error: {e}")
else:
    print("NGROK_TOKEN not found in .env")
