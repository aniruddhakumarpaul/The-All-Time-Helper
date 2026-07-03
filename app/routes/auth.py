from fastapi import APIRouter, Depends
import sqlite3
import secrets
from pydantic import BaseModel
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

# OTP rate limiting. This remains process-local; Codex/local follow-up should move it to SQLite or Redis.
_otp_verify_attempts = {}  # {email: [timestamp, ...]}
_otp_request_attempts = {}  # {email: [timestamp, ...]}
OTP_MAX_VERIFY_ATTEMPTS = 5
OTP_MAX_REQUEST_ATTEMPTS = 3
OTP_WINDOW_SECONDS = 300  # 5 minutes


def _normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def _check_rate_limit(bucket: dict, key: str, max_attempts: int, window_seconds: int) -> bool:
    """Returns True if the request should be blocked."""
    now = time.time()
    attempts = [t for t in bucket.get(key, []) if now - t < window_seconds]
    bucket[key] = attempts
    if len(attempts) >= max_attempts:
        return True
    attempts.append(now)
    return False


def _check_otp_verify_rate_limit(email: str) -> bool:
    return _check_rate_limit(_otp_verify_attempts, email, OTP_MAX_VERIFY_ATTEMPTS, OTP_WINDOW_SECONDS)


def _check_otp_request_rate_limit(email: str) -> bool:
    return _check_rate_limit(_otp_request_attempts, email, OTP_MAX_REQUEST_ATTEMPTS, OTP_WINDOW_SECONDS)


def _new_otp() -> str:
    # cryptographically strong 6-digit OTP, zero-padded
    return f"{secrets.randbelow(1_000_000):06d}"


SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PWD = os.getenv("SENDER_PWD")


def send_otp_email(target_email, otp):
    if os.getenv("EMAIL_MODE", "").upper() == "SIMULATE":
        logger.info(f"[SIMULATED EMAIL] To: {target_email} | OTP: {otp}")
        return True

    if not SENDER_EMAIL or not SENDER_PWD:
        logger.warning("SMTP credentials missing; OTP email was not sent.")
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
            logger.error(f"SMTP Error while sending OTP: {e}")

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
    email = _normalize_email(req.email)
    name = str(req.name or "").strip()[:120]
    if not email or "@" not in email:
        return {"success": False, "error": "Invalid email"}
    if len(str(req.pwd or "")) < 8:
        return {"success": False, "error": "Password must be at least 8 characters"}
    if _check_otp_request_rate_limit(email):
        return {"success": False, "error": "Too many verification requests. Please wait 5 minutes."}
    if UserRepository.get_user_by_email(db, email):
        return {"success": False, "error": "User exists"}

    hashed_pwd = get_password_hash(req.pwd)
    otp = _new_otp()
    expiry = time.time() + 300  # 5 minutes

    try:
        UserRepository.create_user(db, email, hashed_pwd, name, otp, expiry)
    except sqlite3.IntegrityError:
        return {"success": False, "error": "Database error"}

    if not send_otp_email(email, otp):
        return {"success": False, "error": "Email verification is not configured"}
    return {"success": True}


@router.post("/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    email = _normalize_email(req.email)
    user = UserRepository.get_user_by_email(db, email)

    if not user or not verify_password(req.pwd, user['hashed_password']):
        return {"success": False, "error": "Bad login"}

    if user['verified'] == 0:
        if _check_otp_request_rate_limit(email):
            return {"success": False, "error": "Too many verification requests. Please wait 5 minutes."}
        otp = _new_otp()
        expiry = time.time() + 300
        UserRepository.update_otp(db, email, otp, expiry)
        if not send_otp_email(email, otp):
            return {"success": False, "error": "Email verification is not configured"}
        return {"success": True, "unverified": True}

    token = create_access_token(data={"sub": email})
    return {"success": True, "user": {"email": email, "name": user['name']}, "token": token}


@router.post("/verify")
def verify(req: VerifyRequest, db: sqlite3.Connection = Depends(get_db)):
    email = _normalize_email(req.email)
    otp = str(req.otp or "").strip()
    if _check_otp_verify_rate_limit(email):
        return {"success": False, "error": "Too many attempts. Please wait 5 minutes."}

    user = UserRepository.get_user_by_email(db, email)

    if not user or user['otp'] != otp:
        return {"success": False, "error": "Bad OTP"}

    if time.time() > user['otp_expiry']:
        return {"success": False, "error": "OTP Expired. Please log in again to request a new one."}

    UserRepository.verify_user(db, email)

    token = create_access_token(data={"sub": email})
    return {"success": True, "user": {"email": email, "name": user['name']}, "token": token}
