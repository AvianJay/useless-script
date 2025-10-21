import os
import json
import sqlite3
import selfcord

DB_PATH = 'flagged_data.db'

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    if not os.path.exists(DB_PATH):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS guilds (
                    id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    UNIQUE(id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS flagged_users (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER, -- Nullable for global data
                    flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    flagged_role BOOLEAN DEFAULT 0,
                    UNIQUE(user_id, guild_id)
                )
            ''')
            conn.commit()
            # conn.close()
            
def update_guild(conn, guild: selfcord.Guild):
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO guilds (id, name)
        VALUES (?, ?)
    ''', (guild.id, guild.name))
    conn.commit()

def add_flagged_user(conn, user_id: int, guild_id: int = None, flagged_role: bool = False):
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO flagged_users (user_id, guild_id, flagged_role)
        VALUES (?, ?, ?)
    ''', (user_id, guild_id, int(flagged_role)))
    conn.commit()

def get_flagged_user(conn, user_id: int, guild_id: int):
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, flagged_at, flagged_role FROM flagged_users
        WHERE user_id = ? AND guild_id = ?
    ''', (user_id, guild_id))
    return cursor.fetchall()