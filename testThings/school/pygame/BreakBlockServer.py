#!/usr/bin/env python3
from flask import Flask, jsonify, request
import sqlite3
import random
import string

app = Flask(__name__)

app_version = "0.1.0"

# Global DB connection (shared)
conn = None

def ensure_conn():
    """Ensure module-level sqlite3 connection exists and return it."""
    global conn
    if conn is None:
        # check_same_thread False to allow usage across Flask threads in dev
        conn = sqlite3.connect('breakblock.db', check_same_thread=False)
    return conn

def init_db():
    conn = ensure_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL UNIQUE,
                  token TEXT NOT NULL)
                  ''')
    # Leaderboard table (stores the best score for each user)
    c.execute('''CREATE TABLE IF NOT EXISTS leaderboard
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL UNIQUE,
                  score INTEGER NOT NULL,
                  win BOOLEAN NOT NULL DEFAULT 0,
                  app_version TEXT NOT NULL DEFAULT '1.0')
                  ''')
    # History table (stores all scores)
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  score INTEGER NOT NULL,
                  win BOOLEAN NOT NULL DEFAULT 0,
                  app_version TEXT NOT NULL DEFAULT '1.0',
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)
                    ''')
    conn.commit()

def insert_score(user_id, score, win=False, app_version=app_version):
    conn = ensure_conn()
    c = conn.cursor()
    # Insert into history
    c.execute("INSERT INTO history (user_id, score, win, app_version) VALUES (?, ?, ?, ?)", (user_id, score, win, app_version))
    
    # Update leaderboard
    # Check if user is already in leaderboard
    c.execute("SELECT score FROM leaderboard WHERE user_id = ?", (user_id,))
    current_leaderboard_entry = c.fetchone()
    
    if current_leaderboard_entry:
        # If current score is higher, update it
        if score > current_leaderboard_entry[0]:
            c.execute("UPDATE leaderboard SET score = ?, win = ?, app_version = ? WHERE user_id = ?", (score, win, app_version, user_id))
    else:
        # User not in leaderboard, insert them
        c.execute("INSERT INTO leaderboard (user_id, score, win, app_version) VALUES (?, ?, ?, ?)", (user_id, score, win, app_version))
        
    conn.commit()

def get_leaderboard():
    conn = ensure_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, score, win, app_version FROM leaderboard ORDER BY score DESC LIMIT 10")
    rows = c.fetchall()
    return [{"user_id": row[0], "name": get_user_by_id(row[0])["name"], "score": row[1], "win": row[2], "app_version": row[3]} for row in rows]

def create_user(name):
    conn = ensure_conn()
    c = conn.cursor()
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    # check if name already exists
    c.execute("SELECT id FROM users WHERE name = ?", (name,))
    if c.fetchone():
        name += '_' + ''.join(random.choices(string.digits, k=4))
    c.execute("INSERT INTO users (name, token) VALUES (?, ?)", (name, token))
    conn.commit()
    return token

def get_user_by_token(token):
    conn = ensure_conn()
    c = conn.cursor()
    c.execute("SELECT id, name FROM users WHERE token = ?", (token,))
    row = c.fetchone()
    if row:
        return {"id": row[0], "name": row[1]}
    return None

def get_user_by_id(user_id):
    conn = ensure_conn()
    c = conn.cursor()
    c.execute("SELECT id, name FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    if row:
        return {"id": row[0], "name": row[1]}
    return None

def delete_user(token):
    conn = ensure_conn()
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE token = ?", (token,))
    conn.commit()

def edit_user_name(token, new_name):
    conn = ensure_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET name = ? WHERE token = ?", (new_name, token))
    conn.commit()

def get_history_by_name(name):
    conn = ensure_conn()
    c = conn.cursor()
    # Get user id from name
    c.execute("SELECT id FROM users WHERE name = ?", (name,))
    user_row = c.fetchone()
    if not user_row:
        return None
    user_id = user_row[0]
    
    # Query history table
    c.execute("SELECT score, win, app_version, timestamp FROM history WHERE user_id = ? ORDER BY timestamp DESC", (user_id,))
    rows = c.fetchall()
    return [{"score": row[0], "win": row[1], "app_version": row[2], "timestamp": row[3]} for row in rows]

@app.route('/')
def index():
    return '''
<!DOCTYPE html>
<html>
<head>
    <title>打磚塊排行榜</title>
    <script>
        async function fetchLeaderboard() {
            const response = await fetch('/api/leaderboard');
            const data = await response.json();
            const leaderboardDiv = document.getElementById('leaderboard');
            leaderboardDiv.innerHTML = '<h2>排行榜</h2>';
            data.forEach((entry, index) => {
                leaderboardDiv.innerHTML += `<p>${index + 1}. ${entry.name} - ${entry.score} - ${entry.win ? '勝利' : '失敗'} - ${entry.app_version}</p>`;
            });
        }
        window.onload = fetchLeaderboard;
        function goToUserHistory() {
            const name = document.getElementById('name').value;
            window.location.href = `/history?name=${name}`;
        }
    </script>
</head>
<body>
    <h1>打磚塊</h1>
    <div id="leaderboard"></div>
    <div id="goToUserHistory">
        <h2>歷史紀錄</h2>
        <input type="text" id="name" placeholder="輸入名字">
        <button onclick="goToUserHistory()">查看</button>
    </div>
</body>
'''

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard_route():
    leaderboard = get_leaderboard()
    return jsonify(leaderboard)

@app.route('/api/submit_score', methods=['POST'])
def submit_score():
    data = request.json
    token = data.get('token')
    user = get_user_by_token(token)
    score = data.get('score')
    win = data.get('win')
    app_version = data.get('app_version')
    if user is None or score is None or win is None:
        return jsonify({"error": "Missing user or score"}), 400
    insert_score(user["id"], score, win, app_version)
    print(f"Received score submission: {user['name']} - {score} - {win} - {app_version}")
    return jsonify({"status": "success"}), 201

@app.route('/api/create_user')
def create_user_route():
    name = request.args.get('name')
    if not name:
        return jsonify({"error": "Missing name"}), 400
    token = create_user(name)
    return jsonify({"status": "success", "token": token}), 201

@app.route('/api/app_version', methods=['GET'])
def get_app_version():
    return jsonify({"app_version": app_version})

@app.route('/api/delete_user', methods=['POST'])
def delete_user_route():
    data = request.json
    token = data.get('token')
    if not token:
        return jsonify({"error": "Missing token"}), 400
    delete_user(token)
    return jsonify({"status": "success"}), 200

@app.route('/api/get_user', methods=['GET'])
def get_user_route():
    token = request.args.get('token')
    if not token:
        return jsonify({"error": "Missing token"}), 400
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "Invalid token"}), 404
    return jsonify({"status": "success", "user": user}), 200

@app.route('/api/edit_user_name', methods=['POST'])
def edit_user_name_route():
    data = request.json
    token = data.get('token')
    new_name = data.get('new_name')
    if not token or not new_name:
        return jsonify({"error": "Missing token or new_name"}), 400
    edit_user_name(token, new_name)
    return jsonify({"status": "success"}), 200

@app.route('/api/history', methods=['GET'])
def get_history_route():
    name = request.args.get('name')
    if not name:
        return jsonify({"error": "Missing name"}), 400
    history = get_history_by_name(name)
    if history is None:
        return jsonify({"error": "User not found"}), 404
    return jsonify(history)

@app.route('/history')
def history_page():
    name = request.args.get('name', '')
    return f'''
<!DOCTYPE html>
<html>
<head>
    <title>歷史成績查詢</title>
    <style>
        body {{ font-family: sans-serif; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
    <script>
        async function fetchHistory() {{
            const name = "{name}";
            if (!name) return;
            
            const response = await fetch(`/api/history?name=${{name}}`);
            const historyDiv = document.getElementById('history');
            
            if (response.status === 404) {{
                historyDiv.innerHTML = '<p>找不到該用戶。</p>';
                return;
            }}
            
            const data = await response.json();
            
            if (data.length === 0) {{
                historyDiv.innerHTML = '<p>該用戶尚無成績記錄。</p>';
                return;
            }}

            let html = `<h2>${{name}} 的歷史成績</h2>`;
            html += '<table><tr><th>分數</th><th>結果</th><th>版本</th><th>時間</th></tr>';
            data.forEach(entry => {{
                html += `<tr>
                    <td>${{entry.score}}</td>
                    <td>${{entry.win ? '勝利' : '失敗'}}</td>
                    <td>${{entry.app_version}}</td>
                    <td>${{entry.timestamp}}</td>
                </tr>`;
            }});
            html += '</table>';
            historyDiv.innerHTML = html;
        }}
        
        function search() {{
            const nameInput = document.getElementById('nameInput').value;
            window.location.href = `/history?name=${{nameInput}}`;
        }}

        window.onload = fetchHistory;
    </script>
</head>
<body>
    <h1>查詢歷史成績</h1>
    <div>
        <input type="text" id="nameInput" placeholder="輸入用戶名稱" value="{name}">
        <button onclick="search()">查詢</button>
    </div>
    <div id="history"></div>
</body>
</html>
'''

if __name__ == '__main__':
    init_db()
    app.run()