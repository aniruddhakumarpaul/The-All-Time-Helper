from fastapi import APIRouter, Depends, HTTPException, status
import sqlite3
import random
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.security import get_password_hash, verify_password, create_access_token
import time

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import os

router = APIRouter()

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PWD = os.getenv("SENDER_PWD")

def send_otp_email(target_email, otp):
    if not SENDER_EMAIL or not SENDER_PWD:
        print(f"SMTP Credentials missing. DEBUG: OTP for {target_email} is {otp}")
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
            print(f"SMTP Error: {e}")
            print(f"FALLBACK: OTP for {target_email} is {otp}")
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
    c = db.cursor()
    c.execute("SELECT email FROM users WHERE email=?", (req.email,))
    if c.fetchone():
        return {"success": False, "error": "User exists"}
    
    hashed_pwd = get_password_hash(req.pwd)
    otp = str(random.randint(100000, 999999))
    expiry = time.time() + 300 # 5 minutes
    
    try:
        c.execute("INSERT INTO users (email, hashed_password, verified, otp, otp_expiry, name) VALUES (?, ?, 0, ?, ?, ?)",
                  (req.email, hashed_pwd, otp, expiry, req.name))
        db.commit()
    except sqlite3.IntegrityError:
        return {"success": False, "error": "Database error"}
        
    send_otp_email(req.email, otp)
    return {"success": True}

@router.post("/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    c = db.cursor()
    c.execute("SELECT hashed_password, verified, name FROM users WHERE email=?", (req.email,))
    user = c.fetchone()
    
    if not user or not verify_password(req.pwd, user['hashed_password']):
        return {"success": False, "error": "Bad login"}
        
    if user['verified'] == 0:
        otp = str(random.randint(100000, 999999))
        expiry = time.time() + 300
        c.execute("UPDATE users SET otp=?, otp_expiry=? WHERE email=?", (otp, expiry, req.email))
        db.commit()
        send_otp_email(req.email, otp)
        return {"success": True, "unverified": True}
        
    # Generate JWT
    token = create_access_token(data={"sub": req.email})
    return {"success": True, "user": {"email": req.email, "name": user['name']}, "token": token}

@router.post("/verify")
def verify(req: VerifyRequest, db: sqlite3.Connection = Depends(get_db)):
    c = db.cursor()
    c.execute("SELECT name, otp_expiry FROM users WHERE email=? AND otp=?", (req.email, req.otp))
    user = c.fetchone()
    
    if not user:
        return {"success": False, "error": "Bad OTP"}
        
    if time.time() > user['otp_expiry']:
        return {"success": False, "error": "OTP Expired. Please log in again to request a new one."}
        
    c.execute("UPDATE users SET verified=1 WHERE email=?", (req.email,))
    db.commit()
    
    # Generate JWT after verification
    token = create_access_token(data={"sub": req.email})
    return {"success": True, "user": {"email": req.email, "name": user['name']}, "token": token}
