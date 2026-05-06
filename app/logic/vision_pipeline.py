import os
import requests
import base64
import json
import time
from PIL import Image
import io

class VisionPipeline:
    """
    Gemma 4 Native Vision Orchestrator.
    Replaces legacy BLIP/CLIP hybrid with direct native multimodal analysis.
    """
    def __init__(self):
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model_name = "gemma4:e2b"
        print(f"[Vision] Native Gemma 4 Engine Initialized (Model: {self.model_name})")

    def _encode_image(self, img_source):
        """Standardize image to base64 for Ollama."""
        try:
            if isinstance(img_source, str) and (img_source.startswith("http") or os.path.exists(img_source)):
                # If URL, download; if path, read
                if img_source.startswith("http"):
                    # Use proxy or direct download
                    res = requests.get(img_source, timeout=10)
                    img_bytes = res.content
                else:
                    with open(img_source, "rb") as f:
                        img_bytes = f.read()
            elif isinstance(img_source, bytes):
                img_bytes = img_source
            else:
                return None

            # Optimization: Resize large images to prevent OOM on local hardware
            img = Image.open(io.BytesIO(img_bytes))
            if max(img.size) > 1024:
                img.thumbnail((1024, 1024))
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG")
                img_bytes = buffered.getvalue()

            return base64.b64encode(img_bytes).decode('utf-8')
        except Exception as e:
            print(f"[Vision] Encoding error: {e}")
            return None

    def analyze_chat_images(self, urls, user_prompt):
        """
        Stage 1: Multi-Image Perception using Gemma 4.
        """
        if not urls:
            return None

        # Gemma 4 handles the most recent contextually relevant image
        selected_url = urls[0]
        b64_image = self._encode_image(selected_url)
        
        if not b64_image:
            return None

        print(f"[Vision] Direct Gemma 4 Analysis started for: {selected_url}")
        
        # Native Multimodal Prompt
        system_msg = (
            "You are 'The All Time Helper's Vision Module (Gemma 4)'. "
            "Analyze the provided image in detail. "
            "CRITICAL: Provide a detailed description first, then a 'KEYWORDS: ' section with 15 concepts."
        )
        
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                {
                    "role": "user", 
                    "content": f"User Query: {user_prompt}\n\nPlease describe what you see in this image in high fidelity.",
                    "images": [b64_image]
                }
            ],
            "stream": False,
            "options": {
                "num_predict": 512,
                "temperature": 0.2
            }
        }

        try:
            # EXTENDED TIMEOUT: 120s for Native VLM processing
            start_time = time.time()
            response = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=120)
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                description = response.json().get("message", {}).get("content", "Vision analysis failed.")
                print(f"[Vision] Gemma 4 success in {elapsed:.1f}s")
                return {
                    "url": selected_url,
                    "description": description
                }
            else:
                print(f"[Vision] Gemma 4 Error {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[Vision] Gemma 4 Timeout/Error: {e}")

        return None

# Global Instance
vision_sys = VisionPipeline()
