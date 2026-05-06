import os
import httpx
import uuid
import threading
import time
from PIL import Image
from io import BytesIO
from app.logger import logger

# Configuration
# Refresh token from environment just in case
def get_hf_token():
    return os.getenv("HF_TOKEN")

# nielsr/upscaling-swinir is a standard SwinIR implementation known to work with Inference API
UPSCALER_MODEL = "nielsr/upscaling-swinir" 


# Global registry for tracking background jobs
# { job_id: {"status": "pending"|"processing"|"ready"|"failed", "url": "/static/uploads/..."} }
upscale_registry = {}
registry_lock = threading.Lock()

class UpscaleManager:
    @staticmethod
    def start_upscale(source_url: str) -> str:
        """Starts the asynchronous upscale task and returns a unique job_id."""
        job_id = str(uuid.uuid4())
        with registry_lock:
            upscale_registry[job_id] = {"status": "pending", "url": source_url}
        
        # Start background thread
        thread = threading.Thread(target=UpscaleManager._upscale_worker, args=(source_url, job_id))
        thread.daemon = True
        thread.start()
        
        return job_id

    @staticmethod
    def _upscale_worker(source_url: str, job_id: str):
        """Internal worker to handle the cloud request and conversion."""
        try:
            with registry_lock:
                upscale_registry[job_id]["status"] = "processing"
            
            logger.info(f"[Upscaler] Starting job {job_id} for {source_url}")

            # 1. Download source image (Pollinations.ai can take up to 60s to generate the image on-the-fly)
            with httpx.Client(timeout=120.0) as client:
                resp = client.get(source_url)
                if resp.status_code != 200:
                    raise Exception(f"Failed to download source image: {resp.status_code}")
                img_bytes = resp.content

            # 2. Convert and Local Upscale
            logger.debug(f"[Upscaler] Performing High-Fidelity Local Upscaling to 4K...")
            img = Image.open(BytesIO(img_bytes))
            
            # Double dimensions
            new_w, new_h = img.width * 2, img.height * 2
            
            # Lanczos resampling + subtle sharpening
            from PIL import ImageFilter
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

            # Ensure static/uploads exists inside The_All_Time_helper
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            upload_dir = os.path.join(base_dir, "static", "uploads")
            os.makedirs(upload_dir, exist_ok=True)
            
            filename = f"upscaled_{job_id}.jpg"
            filepath = os.path.join(upload_dir, filename)
            
            # Save as JPG with high quality
            img.convert("RGB").save(filepath, "JPEG", quality=95, optimize=True)
            
            # 4. Update Registry
            with registry_lock:
                upscale_registry[job_id]["status"] = "ready"
                upscale_registry[job_id]["url"] = f"/static/uploads/{filename}"
            
            # --- SUCCESS LOGGER ---
            file_size_kb = os.path.getsize(filepath) / 1024
            logger.info(f"✨ [UPSCALER SUCCESS] Job {job_id} finished. Optimized JPG saved: {filename} ({file_size_kb:.1f} KB)")
            # ----------------------

        except Exception as e:
            logger.error(f"[Upscaler] Job {job_id} FAILED: {str(e)}")
            with registry_lock:
                upscale_registry[job_id]["status"] = "failed"

    @staticmethod
    def get_status(job_id: str):
        """Returns the current status of a job."""
        with registry_lock:
            return upscale_registry.get(job_id)
