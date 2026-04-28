import torch
from PIL import Image
import requests
from transformers import BlipProcessor, BlipForConditionalGeneration, CLIPProcessor, CLIPModel
import os
from io import BytesIO
import base64

class VisionPipeline:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.caption_model_name = "Salesforce/blip-image-captioning-base"
        self.clip_model_name = "openai/clip-vit-base-patch32"
        
        self.caption_processor = None
        self.caption_model = None
        self.clip_processor = None
        self.clip_model = None

    def _load_models(self):
        """Lazy load models to save resources until needed."""
        if self.caption_model is None:
            print(f"[Vision] Loading Captioning Model ({self.caption_model_name})...")
            self.caption_processor = BlipProcessor.from_pretrained(self.caption_model_name)
            self.caption_model = BlipForConditionalGeneration.from_pretrained(self.caption_model_name).to(self.device)
        if self.clip_model is None:
            print(f"[Vision] Loading Intent Matching Model ({self.clip_model_name})...")
            self.clip_processor = CLIPProcessor.from_pretrained(self.clip_model_name)
            self.clip_model = CLIPModel.from_pretrained(self.clip_model_name).to(self.device)

    def analyze_chat_images(self, image_urls, user_prompt):
        """
        Analyzes a list of image URLs to find the one most relevant to the user's prompt 
        and generates a detailed semantic description for it.
        """
        if not image_urls:
            return None

        try:
            self._load_models()
            
            images = []
            valid_urls = []
            
            for url in image_urls:
                try:
                    # CASE 1: Base64 Data (Uploaded Files)
                    if url.startswith('data:image/') or (len(url) > 100 and ',' not in url and not url.startswith('http')):
                        # Handle potential raw base64 or data URI
                        b64_data = url.split(',')[1] if ',' in url else url
                        img = Image.open(BytesIO(base64.b64decode(b64_data))).convert('RGB')
                        images.append(img)
                        valid_urls.append("Uploaded Image")
                        continue

                    # CASE 2: Proxy URLs
                    if '/api/image_proxy' in url:
                        from urllib.parse import urlparse, parse_qs
                        parsed = urlparse(url)
                        real_url = parse_qs(parsed.query).get('url', [None])[0]
                        if real_url:
                            url = real_url

                    # CASE 3: Local Static Files
                    if url.startswith('/static/'):
                        base_path = os.getcwd()
                        clean_url = url.split('?')[0].lstrip('/')
                        path = os.path.join(base_path, clean_url)
                        
                        if os.path.exists(path):
                            img = Image.open(path).convert('RGB')
                            images.append(img)
                            valid_urls.append(url)
                    # CASE 4: Remote URLs
                    else:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
                        }
                        response = requests.get(url, headers=headers, timeout=10)
                        img = Image.open(BytesIO(response.content)).convert('RGB')
                        images.append(img)
                        valid_urls.append(url)
                except Exception as e:
                    print(f"[Vision] Error loading {url}: {e}")

            if not images:
                return None

            # 1. Referential Resolution: Match Prompt to Image using CLIP
            inputs = self.clip_processor(text=[user_prompt], images=images, return_tensors="pt", padding=True).to(self.device)
            with torch.no_grad():
                outputs = self.clip_model(**inputs)
            
            # Find the image with the highest similarity to the text prompt
            logits_per_image = outputs.logits_per_image
            best_img_idx = logits_per_image.argmax().item()
            
            selected_img = images[best_img_idx]
            selected_url = valid_urls[best_img_idx]

            # 2. Detailed Interpretation: Generate Caption using BLIP
            # We use the user's prompt as a 'conditional' to guide the captioning
            inputs = self.caption_processor(selected_img, user_prompt, return_tensors="pt").to(self.device)
            with torch.no_grad():
                out = self.caption_model.generate(**inputs, max_new_tokens=50)
            
            description = self.caption_processor.decode(out[0], skip_special_tokens=True)

            print(f"[Vision] Identified Image: {selected_url}")
            print(f"[Vision] Interpretation: {description}")

            return {
                "url": selected_url,
                "description": description,
                "index": best_img_idx,
                "confidence": float(torch.softmax(logits_per_image.flatten(), dim=0)[best_img_idx])
            }
        except Exception as e:
            print(f"[Vision] Pipeline Failure: {e}")
            return None

# Singleton for application-wide use
vision_sys = VisionPipeline()
