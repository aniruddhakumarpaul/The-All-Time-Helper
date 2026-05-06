import sys
import os

# Mock the environment so we can import agents
os.environ["GROQ_API_KEY"] = "mock_key"
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.logic.agents import _reconstruct_contextual_prompt, _detect_intent

def test_context_resolution():
    print("--- Testing Context Resolution ---")
    
    # Test Case 1: Numbered Selection
    history_1 = [
        {"role": "user", "content": "give me a professional shot xtream 125r"},
        {"role": "assistant", "content": "Are you looking for:\n1. Images/Visuals\n2. Specifications\n3. Review\n4. 3D Model"}
    ]
    prompt_1 = "1."
    resolved_1 = _reconstruct_contextual_prompt(prompt_1, history_1)
    print(f"Test 1 (Numeric): '{prompt_1}' -> '{resolved_1}'")
    assert "Selection: Images/Visuals" in resolved_1
    
    # Test Case 2: Intent Detection for 'shot'
    intent_2 = _detect_intent("give me a professional shot", "agentic-pro")
    print(f"Test 2 (Keyword 'shot'): requires_tools={intent_2['requires_tools']}")
    assert intent_2['requires_tools'] == True
    
    # Test Case 3: Ambiguous Pronoun
    history_3 = [
        {"role": "user", "content": "Search for BMW M8 Competition news"},
        {"role": "assistant", "content": "Here are the results for BMW M8..."}
    ]
    prompt_3 = "show it to me"
    resolved_3 = _reconstruct_contextual_prompt(prompt_3, history_3)
    print(f"Test 3 (Pronoun): '{prompt_3}' -> '{resolved_3}'")
    assert "target: bmw m8 competition" in resolved_3.lower()

    # Test Case 4: Unnumbered list with colons (Xstream 125R case)
    history_4 = [
        {"role": "user", "content": "give me a professional shot xtream 125r"},
        {"role": "assistant", "content": "Are you looking for one of the following?\n\nImages/Visuals: Are you looking for professional photographs...\nSpecifications: Do you need detailed technical specs...\nReview/Content: Are you looking for a description..."}
    ]
    prompt_4 = "1."
    resolved_4 = _reconstruct_contextual_prompt(prompt_4, history_4)
    print(f"Test 4 (Unnumbered): '{prompt_4}' -> '{resolved_4}'")
    assert "Selection: Images/Visuals" in resolved_4

    # Test Case 5: Frontend style keys (r/c)
    history_5 = [
        {"r": "u", "c": "search for audi r8"},
        {"r": "b", "c": "1. Images, 2. Specs"}
    ]
    prompt_5 = "1"
    resolved_5 = _reconstruct_contextual_prompt(prompt_5, history_5)
    print(f"Test 5 (Frontend Keys): '{prompt_5}' -> '{resolved_5}'")
    assert "Selection: Images" in resolved_5

    print("\n✅ All logic tests passed!")

if __name__ == "__main__":
    test_context_resolution()
