import os
import sys

# Ensure project root is in path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.logic.vision_pipeline import vision_sys
import re

def test_vision_pipeline():
    # Simulate history with the "c" (short content) key
    history = [
        {"r": "u", "c": "Look at this cat: ![Cat](https://image.pollinations.ai/prompt/a%20cute%20orange%20cat?model=turbo&width=1024&height=1024&nologo=true&seed=42)"},
        {"r": "b", "c": "That is indeed a cute cat!"}
    ]
    
    user_prompt = "What color is the cat?"
    
    print(f"DEBUG: Testing with prompt: '{user_prompt}'")
    
    # 1. Simulate the extraction logic from agents.py
    all_img_urls = []
    for msg in history:
        content = msg.get("content", msg.get("c", ""))
        print(f"DEBUG: Checking message content: {content[:50]}...")
        matches = re.findall(r'!\[.*?\]\((https?://.*?|/static/.*?|/api/image_proxy.*?)\)', content)
        if matches:
            print(f"DEBUG: Found image URLs: {matches}")
            all_img_urls.extend(matches)
    
    if not all_img_urls:
        print("FAIL: No images found in history!")
        return

    # 2. Run the vision pipeline
    print("DEBUG: Calling vision_sys.analyze_chat_images...")
    result = vision_sys.analyze_chat_images(all_img_urls, user_prompt)
    
    if result:
        print("\n--- VISION SCANNER SUCCESS ---")
        print(f"Identified Image: {result['url']}")
        print(f"Description: {result['description']}")
        print(f"Confidence: {result['confidence']:.2f}")
    else:
        print("\n--- VISION SCANNER FAILED ---")

if __name__ == "__main__":
    test_vision_pipeline()
