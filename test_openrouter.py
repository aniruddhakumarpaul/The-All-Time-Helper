import os
import certifi

_BUNDLED_CA_FILE = certifi.where()
for _ca_env_name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
    os.environ.setdefault(_ca_env_name, _BUNDLED_CA_FILE)

import litellm
from dotenv import load_dotenv

load_dotenv()

print("Calling litellm.completion with the configured application model...")
try:
    response = litellm.completion(
        model="openrouter/google/gemma-4-26b-a4b-it:free",
        messages=[{"role": "user", "content": "Say 'hello world' and nothing else."}],
        api_key=os.getenv("OPENROUTER_API_KEY"),
        max_tokens=20
    )
    print("\nSuccess! Model responded:")
    print(response.choices[0].message.content)
except Exception as e:
    print(f"\nlitellm failed: {type(e).__name__} - {e}")
