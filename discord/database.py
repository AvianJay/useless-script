import sqlite3
import json
import os
from typing import Any, Dict, Optional

DB_PATH = 'data.db'

# Default server configuration
DEFAULT_SERVER_CONFIG = {
    "REPORT_CHANNEL_ID": None,
    "MODERATION_MESSAGE_CHANNEL_ID": None,
    "REPORTED_MESSAGE": "感謝您的檢舉，我們會盡快處理您的檢舉。",
    "REPORT_BLACKLIST": [],
    "REPORT_RATE_LIMIT": 300,
    "REPORT_MESSAGE": "@Admin",
    "notify_user_on_mute": True,
    "notify_user_on_kick": True,
    "notify_user_on_ban": True,
    "dsize_max": 30,
    "dsize_surgery_percent": 2,
}

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create server_configs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS server_configs (
                    guild_id INTEGER NOT NULL,
                    config_key TEXT NOT NULL,
                    config_value TEXT NOT NULL,
                    UNIQUE(guild_id, config_key)
                )
            ''')
            
            # Create global_config table for backward compatibility
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS global_config (
                    config_key TEXT PRIMARY KEY,
                    config_value TEXT NOT NULL
                )
            ''')
            
            # Create user_data table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_data (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER, -- Nullable for global data
                    data_key TEXT NOT NULL,
                    data_value TEXT NOT NULL,
                    UNIQUE(user_id, guild_id, data_key)
                )
            ''')

            conn.commit()
    
    def get_server_config(self, guild_id: int, key: str, default: Any = None) -> Any:
        """Get a configuration value for a specific server"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT config_value FROM server_configs WHERE guild_id = ? AND config_key = ?',
                (guild_id, key)
            )
            result = cursor.fetchone()
            
            if result:
                try:
                    return json.loads(result[0])
                except (json.JSONDecodeError, TypeError):
                    # Try to convert to int if it looks like a number
                    try:
                        return int(result[0])
                    except (ValueError, TypeError):
                        return result[0]
            
            return default if default is not None else DEFAULT_SERVER_CONFIG.get(key)
    
    def set_server_config(self, guild_id: int, key: str, value: Any) -> bool:
        """Set a configuration value for a specific server"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Convert value to JSON if it's a complex type, otherwise keep as string
                if isinstance(value, (dict, list)):
                    json_value = json.dumps(value)
                elif isinstance(value, int):
                    json_value = str(value)  # Store integers as strings for consistency
                else:
                    json_value = str(value)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO server_configs (guild_id, config_key, config_value)
                    VALUES (?, ?, ?)
                ''', (guild_id, key, json_value))
                
                conn.commit()
                return True
        except Exception as e:
            print(f"Error setting server config: {e}")
            return False
    
    def get_all_server_config(self, guild_id: int) -> Dict[str, Any]:
        """Get all configuration values for a specific server"""
        config = DEFAULT_SERVER_CONFIG.copy()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT config_key, config_value FROM server_configs WHERE guild_id = ?',
                (guild_id,)
            )
            results = cursor.fetchall()
            
            for key, value in results:
                try:
                    config[key] = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    # Try to convert to int if it looks like a number
                    try:
                        config[key] = int(value)
                    except (ValueError, TypeError):
                        config[key] = value
        
        return config
    
    def get_global_config(self, key: str, default: Any = None) -> Any:
        """Get a global configuration value (for backward compatibility)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT config_value FROM global_config WHERE config_key = ?',
                (key,)
            )
            result = cursor.fetchone()
            
            if result:
                try:
                    return json.loads(result[0])
                except (json.JSONDecodeError, TypeError):
                    return result[0]
            
            return default
    
    def set_global_config(self, key: str, value: Any) -> bool:
        """Set a global configuration value (for backward compatibility)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Convert value to JSON if it's a complex type
                if isinstance(value, (dict, list)):
                    json_value = json.dumps(value)
                else:
                    json_value = str(value)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO global_config (config_key, config_value)
                    VALUES (?, ?)
                ''', (key, json_value))
                
                conn.commit()
                return True
        except Exception as e:
            print(f"Error setting global config: {e}")
            return False
    
    def get_user_data(self, user_id: int, guild_id: Optional[int], key: str, default: Any = None) -> Any:
        """Get user-specific data, optionally scoped to a guild"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT data_value FROM user_data WHERE user_id = ? AND guild_id IS ? AND data_key = ?',
                (user_id, guild_id, key)
            )
            result = cursor.fetchone()
            
            if result:
                try:
                    return json.loads(result[0])
                except (json.JSONDecodeError, TypeError):
                    return result[0]
            
            return default
    
    def set_user_data(self, user_id: int, guild_id: Optional[int], key: str, value: Any) -> bool:
        """Set user-specific data, optionally scoped to a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Convert value to JSON if it's a complex type
                if isinstance(value, (dict, list)):
                    json_value = json.dumps(value)
                else:
                    json_value = str(value)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO user_data (user_id, guild_id, data_key, data_value)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, guild_id, key, json_value))
                
                conn.commit()
                return True
        except Exception as e:
            print(f"Error setting user data: {e}")
            return False
    
    def get_all_user_data(self, guild_id: Optional[int] = None, key: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
        """Get all user data, optionally filtered by guild and/or key"""
        data = {}
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query = 'SELECT user_id, data_key, data_value FROM user_data WHERE 1=1'
            params = []
            
            if guild_id is not None:
                query += ' AND guild_id IS ?'
                params.append(guild_id)
            if key is not None:
                query += ' AND data_key = ?'
                params.append(key)
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            for user_id, data_key, data_value in results:
                if user_id not in data:
                    data[user_id] = {}
                try:
                    data[user_id][data_key] = json.loads(data_value)
                except (json.JSONDecodeError, TypeError):
                    data[user_id][data_key] = data_value
        
        return data

# Global database instance
db = Database()