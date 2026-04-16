import sqlite3
import json
import time
from typing import List, Dict, Optional

class ChatRepository:
    @staticmethod
    def get_chats_for_user(db: sqlite3.Connection, user_email: str) -> List[Dict]:
        c = db.cursor()
        c.execute("SELECT id, title, messages_json FROM chats WHERE user_email=? ORDER BY updated_at ASC", (user_email,))
        rows = c.fetchall()
        chats_array = []
        for r in rows:
            ms = []
            if r['messages_json']:
                try:
                    ms = json.loads(r['messages_json'])
                except:
                    pass
            chats_array.append({
                "id": r['id'],
                "title": r['title'],
                "ms": ms
            })
        return chats_array

    @staticmethod
    def sync_user_chats(db: sqlite3.Connection, user_email: str, chats: List[dict]):
        c = db.cursor()
        c.execute("DELETE FROM chats WHERE user_email=?", (user_email,))
        for chat in chats:
            cid = chat.get('id')
            title = chat.get('title', 'New Chat')
            ms = chat.get('ms', [])
            c.execute("INSERT INTO chats (id, user_email, title, messages_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                      (cid, user_email, title, json.dumps(ms), time.time()))
        db.commit()

class UserRepository:
    @staticmethod
    def get_user_by_email(db: sqlite3.Connection, email: str) -> Optional[sqlite3.Row]:
        c = db.cursor()
        c.execute("SELECT email, hashed_password, verified, name, otp, otp_expiry FROM users WHERE email=?", (email,))
        return c.fetchone()

    @staticmethod
    def create_user(db: sqlite3.Connection, email: str, hashed_pwd: str, name: str, otp: str, expiry: float):
        c = db.cursor()
        c.execute("INSERT INTO users (email, hashed_password, verified, otp, otp_expiry, name) VALUES (?, ?, 0, ?, ?, ?)",
                  (email, hashed_pwd, otp, expiry, name))
        db.commit()

    @staticmethod
    def update_otp(db: sqlite3.Connection, email: str, otp: str, expiry: float):
        c = db.cursor()
        c.execute("UPDATE users SET otp=?, otp_expiry=? WHERE email=?", (otp, expiry, email))
        db.commit()

    @staticmethod
    def verify_user(db: sqlite3.Connection, email: str):
        c = db.cursor()
        c.execute("UPDATE users SET verified=1 WHERE email=?", (email,))
        db.commit()
