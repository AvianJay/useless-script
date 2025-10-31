#!/usr/bin/env python3
from flask import Flask, jsonify, request
import sqlite3
import random
import string
app = Flask(__name__)

app_version = "0.1.0"

def init_db():
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL UNIQUE,
                  token TEXT NOT NULL)
                  ''')
    c.execute('''CREATE TABLE IF NOT EXISTS leaderboard
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  score INTEGER NOT NULL,
                  win BOOLEAN NOT NULL DEFAULT 0,
                  app_version TEXT NOT NULL DEFAULT '1.0')
                  ''')
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  score INTEGER NOT NULL,
                  win BOOLEAN NOT NULL DEFAULT 0,
                  app_version TEXT NOT NULL DEFAULT '1.0')
                    ''')
    conn.commit()
    conn.close()

def insert_score(name, score, win=False, app_version=app_version):
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute("INSERT INTO leaderboard (name, score, win, app_version) VALUES (?, ?, ?, ?)", (name, score, win, app_version))
    conn.commit()
    conn.close()

def get_leaderboard():
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute("SELECT user_id, score, win, app_version FROM leaderboard ORDER BY score DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    return [{"user_id": row[0], "name": get_user_by_id(row[0])["name"], "score": row[1], "win": row[2], "app_version": row[3]} for row in rows]

def create_user(name):
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
    c.execute("INSERT INTO users (name, token) VALUES (?, ?)", (name, token))
    conn.commit()
    conn.close()
    return token

def get_user_by_token(token):
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute("SELECT id, name FROM users WHERE token = ?", (token,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "name": row[1]}
    return None

def get_user_by_id(user_id):
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute("SELECT id, name FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "name": row[1]}
    return None

def delete_user(token):
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def edit_user_name(token, new_name):
    conn = sqlite3.connect('breakblock.db')
    c = conn.cursor()
    c.execute("UPDATE users SET name = ? WHERE token = ?", (new_name, token))
    conn.commit()
    conn.close()

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
    </script>
</head>
<body>
    <div id="leaderboard"></div>
</body>
'''


@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard_route():
    leaderboard = get_leaderboard()
    return jsonify(leaderboard)


@app.route('/api/submit_score', methods=['POST'])
def submit_score():
    data = request.json
    name = data.get('name')
    score = data.get('score')
    win = data.get('win')
    app_version = data.get('app_version')
    if name is None or score is None or win is None:
        return jsonify({"error": "Missing name or score"}), 400
    # Here you would normally save the score to your database.
    insert_score(name, score, win, app_version)
    print(f"Received score submission: {name} - {score} - {win} - {app_version}")
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

if __name__ == '__main__':
    init_db()
    app.run()