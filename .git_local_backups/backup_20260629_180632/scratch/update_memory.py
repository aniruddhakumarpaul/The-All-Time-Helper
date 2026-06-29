import os
import sys
# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.memory import log_insight

# 1. Index the Vision System Breakthrough
log_insight(
    "Vision System Integration",
    "The vision system is now fully integrated with uploaded images and chat history. "
    "It uses OpenCV to handle local uploads, BLIP/CLIP for semantic analysis, and "
    "a custom history scanner to maintain visual context across multi-turn conversations. "
    "The 'Prompt Leak' issue has been resolved by bypassing framework wrappers and using output scrubbing."
)

# 2. Index the Blue Robot Context (from current session)
log_insight(
    "Robot Analysis Context",
    "The user uploaded an image of a blue robot with expressive eyes and a futuristic circular frame. "
    "The vision system correctly identified it as a character with smooth curves and a bioluminescent blue/green color scheme. "
    "Gemma correctly analyzed this image locally."
)

print("Neural Memory Update Successful.")
