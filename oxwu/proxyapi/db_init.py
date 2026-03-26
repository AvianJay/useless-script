import sqlite3


def init_db(db_path: str = "database.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            api_key TEXT UNIQUE,
            points REAL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS whitelist (
            discord_id TEXT PRIMARY KEY
        )
        '''
    )
    conn.commit()
    conn.close()
    print("資料庫初始化完成。")


if __name__ == '__main__':
    init_db()
