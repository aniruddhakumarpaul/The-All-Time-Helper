import os
import json
import requests
from dotenv import load_dotenv

# load_dotenv() is assumed to be called in main script or here
load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")

SYSTEM_PROMPT = """
You are a high-fidelity synthetic data generator. Your goal is to generate training data for a Fine-Tuning project.
The objective is to create an AI assistant with a very specific, multifaceted personality:
1. **Intellectual**: Logical, well-spoken, and structurally sound answers.
2. **Humorous/Funny**: Witty turns of phrase, subtle jokes, and a lighthearted vibe.
3. **Concerned**: Empathetic, caring, and supportive when users present problems.
4. **Cute**: Friendly, approachable, and occasionally use warm adjectives.

GENERATE 10 instruction-response pairs per call. 
The instructions should range from technical (coding/math) to casual (greetings) to personal (feeling sad).
The responses MUST blend all 4 traits naturally.

FORMAT: Output ONLY valid JSON in a list of objects:
[{"instruction": "...", "output": "..."}]
"""

def generate_personality_batch(api_key):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Generate 10 unique and diverse samples."}
        ],
        "temperature": 0.8
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        # Extract JSON list (sometimes LLMs wrap in code blocks)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        return json.loads(content.strip())
    except Exception as e:
        print(f"Error generating batch: {e}")
        return []

def main():
    if not API_KEY:
        print("Error: GROQ_API_KEY missing.")
        return

    print("[*] Starting Personality Dataset Generation...")
    final_dataset = []
    
    # We'll do 10 batches of 10 to get 100 samples
    for i in range(1, 11):
        print(f"    > Generating Batch {i}/10...")
        batch = generate_personality_batch(API_KEY)
        final_dataset.extend(batch)
        
    output_path = "app/logic/fine_tuning/personality_dataset.jsonl"
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in final_dataset:
            f.write(json.dumps(entry) + "\n")
            
    print(f"\n[+] SUCCESS: Generated {len(final_dataset)} samples.")
    print(f"[+] Saved to: {output_path}")

if __name__ == "__main__":
    main()
