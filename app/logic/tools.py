import os
import smtplib
from email.mime.text import MIMEText
from crewai.tools import tool
from app.logic.memory import query_memory, log_insight
from app.logger import logger
from app.logic.upscaler import UpscaleManager

# 1. Custom DuckDuckGo TEXT Search Tool (using modern ddgs library)
@tool("web_search_text")
def search_tool(query: str) -> str:
    """Useful for searching the web for TEXT-BASED information, news, and technical questions. 
    STRICT RULE: This tool returns ONLY text descriptions and snippets. It CANNOT provide images."""
    try:
        from ddgs import DDGS
        import time
        
        query = query.strip()
        logger.info(f"DEBUG: [Search] Query (ddgs v9): {query}")
        
        results = []
        ddgs = DDGS()
        
        # Strategy 1: News (for recent/political queries)
        if any(kw in query.lower() for kw in ["recent", "news", "latest", "events", "politics"]):
            try:
                results.extend(ddgs.news(query, max_results=5))
                logger.info(f"DEBUG: [Search] News returned {len(results)} results")
            except Exception as ne:
                logger.error(f"DEBUG: [Search] News failed: {ne}")
                time.sleep(1)
        
        # Strategy 2: General web search (supplement or fallback)
        if len(results) < 3:
            try:
                web_results = ddgs.text(query, max_results=5)
                results.extend(web_results)
                logger.info(f"DEBUG: [Search] Web returned {len(web_results)} results")
            except Exception as te:
                logger.error(f"DEBUG: [Search] Web failed: {te}")
        
        if not results:
            return "No reliable results found. The search engine may be temporarily unavailable."
        
        output = []
        for r in results[:8]:
            title = r.get('title', 'N/A')
            snippet = r.get('body', r.get('snippet', r.get('description', 'N/A')))
            url = r.get('href', r.get('link', r.get('url', 'N/A')))
            output.append(f"Title: {title}\nSnippet: {snippet}\nURL: {url}\n")
        return "\n---\n".join(output)
    except Exception as e:
        logger.error(f"DEBUG: [Search] Global Failure: {str(e)}")
        return f"Search Error: {str(e)}"

from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

# 2. Email Tool (Live Mode Enforced + Attachments Support)
@tool("send_email_tool")
def send_email_tool(recipient: str, subject: str, body: str, attachment_content: str = None, attachment_filename: str = "report.txt") -> str:
    """Useful for sending professional emails. Provide recipient, subject, and body. 
    OPTIONAL: Provide attachment_content and attachment_filename (e.g. 'summary.txt') for large reports.
    The system will automatically check for authorization."""
    
    # Secure Auth Check via ContextVar (LLM is blind to the actual key)
    from app.logic.memory import admin_auth_context
    provided_key = admin_auth_context.get()
    expected_key = os.getenv("ADMIN_KEY")
    
    if not provided_key or provided_key != expected_key:
        logger.warning("Email tool access denied: Invalid or missing Admin Key.")
        return "ERROR: AUTH_REQUIRED. The Admin Key is missing or incorrect. INSTRUCTION: Tell the user 'Please provide your Admin Key in the next message to authorize this action.' and explain that they can type it directly or use the 'Masked' feature for privacy."
        
    sender_email = os.getenv("SENDER_EMAIL")
    sender_pwd = os.getenv("SENDER_PWD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    if not all([sender_email, sender_pwd]):
        logger.error("SMTP credentials not configured.")
        return "ERROR: SMTP credentials not configured for LIVE mode. Please ask the administrator to configure SENDER_EMAIL and SENDER_PWD."

    try:
        # Use MIMEMultipart to support attachments
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = recipient

        # Attach the body text
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # Handle optional attachment
        if attachment_content:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment_content.encode('utf-8'))
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{attachment_filename}"')
            msg.attach(part)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_pwd)
            server.send_message(msg)
            
        # Hardened, structured success message
        logger.info(f"Email successfully sent to {recipient}")
        return f"LIVE SUCCESS: Email securely dispatched to {recipient}. {'(With Attachment)' if attachment_content else ''} Message ID: {hash(subject+body)}"
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")
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
@tool("image_generate_tool")
def image_generate_tool(description: str) -> str:
    """AI IMAGE GENERATOR: Generates a high-definition AI image based on an artistic description.
    Use this for: Fictional things, concept art, fantasy, and creative requests."""
    import urllib.parse
    import time
    
    # 1. Clean and encode the prompt
    clean_desc = description.strip().replace('\n', ' ')
    encoded = urllib.parse.quote(clean_desc)
    
    # 2. Dynamic Seed and HD Parameters
    # We use a mix of prompt hash and timestamp to ensure high entropy for the seed.
    seed = (abs(hash(clean_desc)) + int(time.time())) % 1000000
    
    # TRIGGER: Start the background upscale task
    try:
        # We start the upscale task using the base url.
        # Then we append &uid=... to the URL so the frontend can extract the job ID
        # without the LLM stripping it off!
        base_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width=1024&height=1024&nologo=true&seed={seed}"
        upscale_id = UpscaleManager.start_upscale(base_url)
        
        image_url_with_uid = f"{base_url}&uid={upscale_id}"
        logger.info(f"[ART ENGINE] Generated HD URL with UID: {image_url_with_uid}")
        
        return f"![{clean_desc}]({image_url_with_uid})"
    except Exception as e:
        logger.error(f"[ART ENGINE] Failed to trigger upscale: {e}")
        base_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width=1024&height=1024&nologo=true&seed={seed}"
        return f"![{clean_desc}]({base_url})"


# 5.5 Real World Image Search Tool (using modern ddgs library)
@tool("image_search_tool")
def image_search_tool(query: str) -> str:
    """WEB IMAGE SEARCH: Useful for searching for REAL-WORLD images of products, people, places, vehicles, etc.
    Use this when the user wants to see a photo of something that actually exists in the real world."""
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        results = ddgs.images(query, max_results=1)
        
        if not results:
            return "No reliable image results found."
        
        img_url = results[0]['image']
        return f"![{query}]({img_url})"
    except Exception as e:
        logger.error(f"Image Search Error: {str(e)}")
        return f"Image Search Error: {str(e)}"


# 6. Neural Memory Tools
@tool("recall_memory")
def recall_memory(query: str) -> str:
    """Semantically searches the project's 'neural memory' for code snippets, architectural decisions, and previous activity.
    Use this to understand how a feature is implemented or what was decided in the past without reading all files."""
    try:
        results = query_memory(query)
        if not results:
            return "No relevant memories found for this query."
        
        output = []
        for r in results:
            source = r['metadata'].get('file', 'Unknown')
            output.append(f"--- Memory from {source} ---\n{r['content']}\n")
        return "\n".join(output)
    except Exception as e:
        logger.error(f"Memory Retrieval Error: {str(e)}")
        return f"Memory Retrieval Error: {str(e)}"

@tool("archive_insight")
def archive_insight(title: str, body: str) -> str:
    """Permanently saves an architectural decision, user preference, or project milestone to the 'neural memory'.
    Use this to ensure critical context is preserved for future sessions."""
    try:
        log_insight(title, body)
        return f"Successfully archived insight: '{title}' to Neural Memory."
    except Exception as e:
        return f"Memory Archive Error: {str(e)}"
