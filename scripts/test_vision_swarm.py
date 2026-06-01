import os
import base64
import sys

# Inject project path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.agents import run_helper_agent

def test_vision():
    image_path = os.path.join("static", "img", "bot.png")
    if not os.path.exists(image_path):
        print(f"Error: {image_path} not found")
        return

    with open(image_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode("utf-8")

    prompt = "What is this image about? Give me detailed context and search if needed."
    print(f"--- TESTING VISION SWARM ---")
    print(f"Prompt: {prompt}")
    
    # Test with cloud model (agentic-pro) to see the deep analysis
    result = run_helper_agent(prompt, img_data=img_data, target_model="agentic-pro")
    
    print("\n--- AI RESPONSE ---")
    print(result)
    print("\n--- TEST COMPLETE ---")

if __name__ == "__main__":
    test_vision()
