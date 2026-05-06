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
    if not SENDER_EMAIL or not SENDER_PWD:
        logger.warning(f"SMTP Credentials missing. DEBUG: OTP for {target_email} is {otp}")
        return False
    def _send():
        try:
            msg = MIMEMultipart()
            msg['From'] = SENDER_EMAIL
            msg['To'] = target_email
            msg['Subject'] = "Your AI Assistant Verification Code"
            body = f"Your 6-digit verification code is: {otp}. Enter this in the app to proceed."
            msg.attach(MIMEText(body, 'plain'))
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PWD)
                server.send_message(msg)
        except Exception as e:
            logger.error(f"SMTP Error: {e}")
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
