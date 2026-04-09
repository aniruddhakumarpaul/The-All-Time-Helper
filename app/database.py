import sqlite3
import os

# Calculate absolute path to the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Hardcode absolute path regardless of .env config to prevent ghost relative databases
DB_FILE = os.path.join(BASE_DIR, "users.db")

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Create the modern users table with hashed_password and otp_expiry
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    hashed_password TEXT,
                    verified INTEGER DEFAULT 0,
                    otp TEXT,
                    otp_expiry REAL,
                    name TEXT
                 )''')
                 
    # Create a table for storing chat history
    c.execute('''CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    user_email TEXT,
                    title TEXT,
                    messages_json TEXT,
                    updated_at REAL,
                    FOREIGN KEY(user_email) REFERENCES users(email)
                 )''')
    conn.commit()
    conn.close()
