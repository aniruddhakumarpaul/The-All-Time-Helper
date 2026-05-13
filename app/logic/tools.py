import os
import smtplib
import time
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
from dotenv import load_dotenv

def _build_html_body(personalized_body: str, tone: str) -> str:
    formatted_body = personalized_body.replace('\n', '<br>')
    tone_config = {
        "formal": {"bg": "#f9fafb", "h_bg": "#ffffff", "txt": "#111827", "h_txt": "EXECUTIVE CORRESPONDENCE", "h_sub": "Official Communication Layer", "border": "2px solid #111827"},
        "informal": {"bg": "#fdf2f8", "h_bg": "#fce7f3", "txt": "#be185d", "h_txt": "Hello there!", "h_sub": "A message from your friend's AI", "border": "1px solid #fce7f3"},
        "modern": {"bg": "#ffffff", "h_bg": "linear-gradient(135deg, #6366f1 0%, #a855f7 100%)", "txt": "white", "h_txt": "The All Time Helper", "h_sub": "Your AI Executive Assistant", "border": "1px solid #e5e7eb"}
    }
    t = tone_config.get(tone, tone_config["modern"])
    return f"""
    <html>
        <body style="font-family: 'Inter', Arial, sans-serif; margin: 0; padding: 40px; background-color: {t['bg']};">
            <div style="max-width: 650px; margin: auto; border: {t['border']}; border-radius: 8px; overflow: hidden; background: white; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                <div style="background: {t['h_bg']}; padding: 30px; text-align: center; color: {t['txt']}; border-bottom: 1px solid #e5e7eb;">
                    <h1 style="margin: 0; font-size: 24px; text-transform: uppercase; letter-spacing: 2px;">{t['h_txt']}</h1>
                    <p style="margin: 8px 0 0; opacity: 0.7; font-size: 14px;">{t['h_sub']}</p>
                </div>
                <div style="padding: 40px; color: #1f2937; line-height: 1.8; font-size: 16px; white-space: pre-wrap; height: auto;">{formatted_body}</div>
                <div style="padding: 20px; text-align: center; font-size: 12px; color: #9ca3af; background: #f9fafb;">Dispatched via All Time Helper Secure Swarm</div>
            </div>
        </body>
    </html>
    """

# 2. Email Tool (Live Mode Enforced + HTML Support + Multi-Tone)
@tool("send_email_tool")
def send_email_tool(recipient: str, subject: str, body: str, raw_attachment_text: str = "", attachment_content: str = None, attachment_filename: str = "report.txt", is_html: bool = True, tone: str = "modern") -> str:
    """Useful for sending professional emails. 
    recipient: Destination email(s). Can be a single email or a comma-separated list for broadcasting.
    subject: Professional subject line.
    body: The agent-written message intro/context.
    raw_attachment_text: The VERBATIM technical content or data block provided by the user. Pass it here to avoid truncation.
    OPTIONAL: ONLY provide attachment_content if explicitly requested.
    TONE: 'formal', 'informal', or 'modern'."""
    
    # Secure Auth Check via ContextVar + DB Fallback
    from app.logic.memory import admin_auth_context, user_context
    import sqlite3
    from app.database import DB_FILE
    
    auth_ok = admin_auth_context.get()
    if not auth_ok:
        try:
            with sqlite3.connect(DB_FILE) as conn:
                row = conn.execute(
                    "SELECT admin_authorized FROM users WHERE email=?",
                    (user_context.get(),)
                ).fetchone()
                auth_ok = row and row[0]
        except Exception:
            pass

    if not auth_ok:
        return "ERROR: AUTH_REQUIRED. Please provide your Admin Key in the next message (use the Masked icon) to authorize sending emails."

    load_dotenv()
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    sender_email = os.getenv("SENDER_EMAIL")
    sender_pwd = os.getenv("SENDER_PWD")

    if not all([sender_email, sender_pwd]):
        logger.error("SMTP credentials not configured.")
        return "ERROR: SMTP credentials not configured."

    # Parse recipients (support comma-separated list)
    recipients_raw = recipient
    recipients = [r.strip() for r in recipients_raw.split(',') if '@' in r]
    if not recipients:
        return f"ERROR: No valid recipients found in '{recipient}'"

    # FLAW 2 FIX: Pre-Send Check (Log & Bus)
    from app.logic.bus import tool_result_bus, job_id_context
    from app.logic.memory import user_context
    import sqlite3
    import re
    from app.database import DB_FILE
    
    current_job = job_id_context.get()
    current_user = user_context.get()
    
    # 1. Check shared bus (instant/same-process)
    bus_check = tool_result_bus.get_result(current_job)
    if bus_check:
        logger.info(f"[Tool] Blocking duplicate send for job {current_job} (Bus Match)")
        return bus_check

    # 2. Check DB log (persistent/across-restarts)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT status FROM email_send_log WHERE job_id = ?", (current_job,))
            row = c.fetchone()
            if row:
                logger.info(f"[Tool] Blocking duplicate send for job {current_job} (DB Match)")
                return row[0]
    except Exception as e:
        logger.warning(f"[Tool] DB check failed: {e}")

    try:
        results = []
        # FLAW 3 FIX: Countermeasure 3 — Native Batch SMTP
        # Open one connection and reuse it for all recipients
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(sender_email, sender_pwd)
            
            for target in recipients:
                # BUG 2 FIX: Personalize salutation per recipient
                raw_name = re.split(r'[\._\d]', target.split('@')[0])[0]
                first_name = raw_name.capitalize()
                
                # BUG 1 FIX: Combine agent body with raw verbatim content
                full_message_body = body.strip()
                if raw_attachment_text.strip() and raw_attachment_text.strip() not in body.strip():
                    full_message_body += f"\n\n---\n\n{raw_attachment_text.strip()}"
                
                # Replace any group greetings with personalized one
                personalized_body = re.sub(
                    r'^(hi everyone|dear all|hello everyone|greetings all|hi all|hello all|dear team)[,.]?',
                    f'Dear {first_name},',
                    full_message_body,
                    flags=re.IGNORECASE
                )
                # If no greeting was found at start, prepend one
                if not personalized_body.startswith(f"Dear {first_name}"):
                    personalized_body = f"Dear {first_name},\n\n" + personalized_body

                if attachment_content is not None and len(str(attachment_content).strip()) > 0:
                    msg = MIMEMultipart()
                    msg['Subject'] = subject
                    msg['From'] = f"The All Time Helper <{sender_email}>"
                    msg['To'] = target
                    
                    if is_html:
                        html_content = _build_html_body(personalized_body, tone)
                        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
                    else:
                        msg.attach(MIMEText(personalized_body, 'plain', 'utf-8'))

                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment_content.encode('utf-8'))
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename="{attachment_filename}"')
                    msg.attach(part)
                else:
                    if is_html:
                        msg = MIMEMultipart('alternative')
                        msg['Subject'] = subject
                        msg['From'] = f"The All Time Helper <{sender_email}>"
                        msg['To'] = target
                        msg.attach(MIMEText(personalized_body, 'plain', 'utf-8'))
                        html_content = _build_html_body(personalized_body, tone)
                        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
                    else:
                        msg = MIMEText(personalized_body, 'plain', 'utf-8')
                        msg['Subject'] = subject
                        msg['From'] = f"The All Time Helper <{sender_email}>"
                        msg['To'] = target

                server.send_message(msg)
                results.append(target)
        
        final_res = f"LIVE SUCCESS: Email broadcasted to {', '.join(results)}."
        
        # FLAW 1 & 2 FIX: Log success to Bus and DB
        tool_result_bus.set_result(current_job, final_res)
        try:
            with sqlite3.connect(DB_FILE) as conn:
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO email_send_log (job_id, user_email, recipients, status, timestamp) VALUES (?, ?, ?, ?, ?)",
                          (current_job, current_user, ",".join(results), final_res, time.time()))
        except Exception as e:
            logger.warning(f"[Tool] Persistent logging failed: {e}")

        return final_res

    except Exception as e:
        logger.error(f"SMTP error: {e}", exc_info=True)
        return f"ERROR: Failed to send email. {str(e)}"

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
