from fastapi import APIRouter, Depends, HTTPException, status
import sqlite3
import random
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.repository import UserRepository
from app.security import get_password_hash, verify_password, create_access_token
import time
from app.logger import logger

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import os

router = APIRouter()

# FIX #8: OTP Rate Limiting — prevent brute-force attacks
_otp_attempts = {}  # {email: [timestamp, ...]}
OTP_MAX_ATTEMPTS = 5
OTP_WINDOW_SECONDS = 300  # 5 minutes

def _check_otp_rate_limit(email: str) -> bool:
    """Returns True if the request should be BLOCKED."""
    now = time.time()
    attempts = _otp_attempts.get(email, [])
    # Prune old attempts outside the window
    attempts = [t for t in attempts if now - t < OTP_WINDOW_SECONDS]
    _otp_attempts[email] = attempts
    if len(attempts) >= OTP_MAX_ATTEMPTS:
        return True  # BLOCKED
    _otp_attempts[email].append(now)
    return False

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PWD = os.getenv("SENDER_PWD")

def send_otp_email(target_email, otp):
    email_mode = os.getenv("EMAIL_MODE", "SIMULATE").upper()
    is_live = (email_mode == "LIVE") and all([SENDER_EMAIL, SENDER_PWD])

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@400;500;600;700&display=swap');
    </style>
</head>
<body style="font-family: 'Outfit', 'Inter', sans-serif; margin: 0; padding: 40px; background-color: #f3f4f6; -webkit-font-smoothing: antialiased;">
    <div style="max-width: 500px; margin: auto; border: 1px solid #e5e7eb; border-radius: 16px; overflow: hidden; background: #ffffff; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.05);">
        <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); padding: 35px 30px; text-align: center; color: #ffffff;">
            <h1 style="margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;">THE ALL TIME HELPER</h1>
            <p style="margin: 8px 0 0; opacity: 0.85; font-size: 14px; font-weight: 400;">Security Verification</p>
        </div>
        <div style="padding: 40px 30px; text-align: center; color: #374151;">
            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Hello, please use the following security verification code to complete your login or registration.
            </p>
            <div style="letter-spacing: 6px; font-size: 32px; font-weight: 700; color: #6366f1; background-color: #f8fafc; padding: 16px 30px; border-radius: 12px; display: inline-block; border: 1px solid #e2e8f0; margin-bottom: 24px;">
                {otp}
            </div>
            <p style="margin: 0; font-size: 13px; line-height: 1.5; color: #9ca3af;">
                This code is valid for 5 minutes.<br>If you did not request this code, please secure your account immediately.
            </p>
        </div>
        <div style="padding: 20px 30px; text-align: center; font-size: 11px; color: #9ca3af; background: #f9fafb; border-top: 1px solid #e5e7eb;">
            Secure Authentication Layer &bull; OTP Verification
        </div>
    </div>
</body>
</html>"""

    if not is_live:
        log_entry = f"""
========================================================================
[SIMULATED EMAIL DISPATCH]
Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}
From: The All Time Helper (Security OTP Simulation)
To: {target_email}
Subject: Your AI Assistant Verification Code
OTP: {otp}
------------------------------------------------------------------------
HTML CONTENT PREVIEW:
{html_content}
========================================================================
"""
        try:
            log_file = "simulated_emails.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
            logger.info(f"OTP email simulated. Logged to simulated_emails.log (OTP: {otp})")
        except Exception as e:
            logger.error(f"Error writing simulated OTP email: {e}")
        return False

    def _send():
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = f"The All Time Helper <{SENDER_EMAIL}>"
            msg['To'] = target_email
            msg['Subject'] = "Your AI Assistant Verification Code"
            
            # Plain text fallback
            body = f"Your 6-digit verification code is: {otp}. Enter this in the app to proceed."
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PWD)
                server.send_message(msg)
            logger.info(f"OTP email sent successfully to {target_email}")
        except Exception as e:
            logger.error(f"SMTP Error sending OTP to {target_email}: {e}")
            logger.info(f"FALLBACK: OTP for {target_email} is {otp}")
            
    threading.Thread(target=_send, daemon=True).start()
    return True

class SignupRequest(BaseModel):
    email: str
    pwd: str
    name: str

class LoginRequest(BaseModel):
    email: str
    pwd: str

class VerifyRequest(BaseModel):
    email: str
    otp: str

@router.post("/signup")
def signup(req: SignupRequest, db: sqlite3.Connection = Depends(get_db)):
    if UserRepository.get_user_by_email(db, req.email):
        return {"success": False, "error": "User exists"}
    
    hashed_pwd = get_password_hash(req.pwd)
    otp = str(random.randint(100000, 999999))
    expiry = time.time() + 300 # 5 minutes
    
    try:
        UserRepository.create_user(db, req.email, hashed_pwd, req.name, otp, expiry)
    except sqlite3.IntegrityError:
        return {"success": False, "error": "Database error"}
        
    send_otp_email(req.email, otp)
    return {"success": True}

@router.post("/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    user = UserRepository.get_user_by_email(db, req.email)
    
    if not user or not verify_password(req.pwd, user['hashed_password']):
        return {"success": False, "error": "Bad login"}
        
    if user['verified'] == 0:
        otp = str(random.randint(100000, 999999))
        expiry = time.time() + 300
        UserRepository.update_otp(db, req.email, otp, expiry)
        send_otp_email(req.email, otp)
        return {"success": True, "unverified": True}
        
    # Generate JWT
    token = create_access_token(data={"sub": req.email})
    return {"success": True, "user": {"email": req.email, "name": user['name']}, "token": token}

@router.post("/verify")
def verify(req: VerifyRequest, db: sqlite3.Connection = Depends(get_db)):
    # FIX #8: Rate limit OTP verification attempts
    if _check_otp_rate_limit(req.email):
        return {"success": False, "error": "Too many attempts. Please wait 5 minutes."}
    
    user = UserRepository.get_user_by_email(db, req.email)
    
    if not user or user['otp'] != req.otp:
        return {"success": False, "error": "Bad OTP"}
        
    if time.time() > user['otp_expiry']:
        return {"success": False, "error": "OTP Expired. Please log in again to request a new one."}
        
    UserRepository.verify_user(db, req.email)
    
    # Generate JWT after verification
    token = create_access_token(data={"sub": req.email})
    return {"success": True, "user": {"email": req.email, "name": user['name']}, "token": token}
