import os
import smtplib
from email.mime.text import MIMEText
from crewai.tools import tool
from duckduckgo_search import DDGS

# 1. Custom DuckDuckGo Search Tool
@tool("search_tool")
def search_tool(query: str) -> str:
    """Useful for searching the web for real-time information, news, and technical questions."""
    try:
        results = DDGS().text(query, max_results=5)
        if not results:
            return "No reliable results found on the web."
        
        output = []
        for r in results:
            output.append(f"Title: {r['title']}\nSnippet: {r['body']}\nURL: {r['href']}\n")
        return "\n---\n".join(output)
    except Exception as e:
        return f"Search Error: {str(e)}"

# 2. Email Tool (Hybrid Mode)
@tool("send_email_tool")
def send_email_tool(recipient: str, subject: str, body: str) -> str:
    """Useful for sending professional emails to clients or friends.
    Provide recipient email, a clear subject, and the message content."""
    sender_email = os.getenv("SENDER_EMAIL")
    sender_pwd = os.getenv("SENDER_PWD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    
    # HYBRID LOGIC
    email_mode = os.getenv("EMAIL_MODE", "SIMULATE").upper()

    if email_mode == "SIMULATE":
        log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "simulated_emails.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- SIMULATED EMAIL ---\nTo: {recipient}\nSubject: {subject}\nBody: {body}\n-----------------------\n")
        return f"SIMULATED SUCCESS: Email logged (Mode: {email_mode})"

    if not all([sender_email, sender_pwd]):
        return "Error: SMTP credentials not configured for LIVE mode."

    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = recipient

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_pwd)
            server.send_message(msg)
        return f"LIVE SUCCESS: Sent email to {recipient}"
    except Exception as e:
        return f"Failed to send email: {str(e)}"

# 3. Astrology Tool
@tool("calculate_horoscope")
def calculate_horoscope(sign: str) -> str:
    """Provides a daily horoscope reading based on the user's zodiac sign."""
    readings = {
        "aries": "A day of bold action. Your energy is contagious.",
        "taurus": "Patience and persistence bring rewards today.",
        "gemini": "Communication is your strength. Share your ideas.",
        "cancer": "Trust your intuition. Home and family bring comfort.",
        "leo": "Creativity is your superpower today. Express yourself.",
        "virgo": "Attention to detail leads to excellence.",
        "libra": "Balance and harmony guide your decisions.",
        "scorpio": "Intuition is high. Trust your gut feelings.",
        "sagittarius": "Adventure calls. Embrace new perspectives.",
        "capricorn": "Stamina and focus will lead to major progress.",
        "aquarius": "Innovation sparks big breakthroughs today.",
        "pisces": "Empathy and creativity flow naturally for you."
    }
    sign = sign.lower().strip()
    return readings.get(sign, "The stars are in flux for you today. Stay open to new possibilities and be kind to yourself.")

# 4. Palm Reading Tool
@tool("analyze_palm_lines")
def analyze_palm_lines(palm_description: str) -> str:
    """Analyzes a description of palm lines (Life, Heart, Head) to provide a fate reading."""
    return f"Based on your palm: '{palm_description}', I see a path of great resilience and emotional depth. Your lines suggest a major turning point approaching that will lead to spiritual growth."

# 5. Symbolic Image Tool (Pollinations API)
# BUG FIX: The tool now returns ONLY the raw markdown image tag.
# Previously it returned a conversational wrapper ("I have visualized...") which
# caused the LLM to re-summarise and strip the critical '!' character,
# converting the image into a broken hyperlink.
@tool("generate_visionary_image")
def generate_visionary_image(description: str) -> str:
    """Generates a high-definition AI image based on the artistic description provided.
    IMPORTANT: Return the EXACT output of this tool to the user. Do not paraphrase or modify it."""
    import urllib.parse
    import time
    
    # 1. Clean and encode the prompt
    clean_desc = description.strip().replace('\n', ' ')
    encoded = urllib.parse.quote(clean_desc)
    
    # 2. Dynamic Seed and HD Parameters
    # We use a mix of prompt hash and timestamp to ensure high entropy for the seed.
    seed = (abs(hash(clean_desc)) + int(time.time())) % 1000000
    
    # 3. Construct the HD Art Engine URL
    # model=flux : High fidelity model
    # width/height : HD Resolution
    # nologo=true : Clean output
    image_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width=1280&height=720&nologo=true&seed={seed}"
    
    print(f"[ART ENGINE] Generated HD URL: {image_url}")
    
    # 4. Return ONLY the raw markdown tag.
    return f"![{clean_desc}]({image_url})"

