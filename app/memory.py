import sqlite3
import json
import os

STATE_DB = "data/state.db"

def init_memory_db():
    """Initialize tables for sessions and user profiles."""
    conn = sqlite3.connect(STATE_DB)
    c = conn.cursor()
    # Profiles for long-term memory
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id TEXT PRIMARY KEY, profile_data TEXT)''')
    # Conversations for short-term memory
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def get_user_profile(user_id: str) -> dict:
    """Return the stored profile for a user."""
    conn = sqlite3.connect(STATE_DB)
    c = conn.cursor()
    c.execute("SELECT profile_data FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return json.loads(row[0]) if row else {}

def update_user_profile(user_id: str, new_data: dict):
    """Update the stored profile with new user preferences."""
    profile = get_user_profile(user_id)
    profile.update(new_data)
    
    conn = sqlite3.connect(STATE_DB)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, profile_data) VALUES (?, ?)", 
              (user_id, json.dumps(profile)))
    conn.commit()
    conn.close()

def get_session_history(session_id: str, limit: int = 15) -> list:
    """Retrieves the last N messages for short-term conversational context."""
    conn = sqlite3.connect(STATE_DB)
    c = conn.cursor()
    c.execute("SELECT role, content FROM sessions WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?", (session_id, limit))
    rows = c.fetchall()
    conn.close()
    # Fetch newest messages first, then restore chronological order for the model.
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def append_message(session_id: str, role: str, content: str):
    """Logs a single message to the active session."""
    conn = sqlite3.connect(STATE_DB)
    c = conn.cursor()
    c.execute("INSERT INTO sessions (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_memory_db()
    print("Memory database initialized successfully.")
