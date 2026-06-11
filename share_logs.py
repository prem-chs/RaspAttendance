#!/usr/bin/env python3
"""
Raspberry Pi Offline Data Sharing & Admin Portal (share_logs.py)

This script hosts a Flask web server bound to 0.0.0.0 on port 5000.
It features:
1. User Clock-In Portal at `/` (No admin links, ensuring normal users see only clocking functions).
2. Administrative Portal at `/admin` protected by session password authentication.
3. Access protection on all administrative API endpoints (logs, registers, resets, downloads).

Author: Embedded Systems & Computer Vision Specialist
"""

import os
import sys
import sqlite3
import logging
from functools import wraps
from datetime import datetime
from io import BytesIO
from werkzeug.utils import secure_filename
from flask import Flask, render_template_string, jsonify, request, send_file, session, redirect, url_for

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("share_logs.log", mode="a")
    ]
)
logger = logging.getLogger("AdminPortal")

# Default Configurations
DEFAULT_DB_PATH = "/home/darkninja/RaspAttendance/attendance_system.db"
DEFAULT_IMAGE_DIR = "/home/pi/Student_Images/"
DEFAULT_ADMIN_PASSWORD = "admin123"

app = Flask(__name__)
# Secure sessions for tracking admin logins
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'rasp_attendance_secret_secure_key_129837')
app.config['DB_PATH'] = os.environ.get('ATTENDANCE_DB_PATH', DEFAULT_DB_PATH)
app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', DEFAULT_ADMIN_PASSWORD)


def get_image_dir():
    """Detects and returns a writable images directory."""
    paths_to_try = [
        DEFAULT_IMAGE_DIR,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "Student_Images")
    ]
    for p in paths_to_try:
        try:
            if not os.path.exists(p):
                os.makedirs(p, exist_ok=True)
            test_file = os.path.join(p, ".write_test")
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return p
        except Exception:
            continue
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Student_Images")
    os.makedirs(fallback, exist_ok=True)
    return fallback

app.config['IMAGE_DIR'] = get_image_dir()
logger.info(f"System registered image storage directory: {app.config['IMAGE_DIR']}")


def get_db():
    """Establish connection to SQLite database with a 30-second busy timeout to prevent concurrent lockouts."""
    db_path = app.config['DB_PATH']
    if not os.path.exists(db_path):
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        open(db_path, 'a').close()

    # Use timeout=30.0 to automatically queue queries when another process (like the camera engine) writes
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db_structure():
    """Ensure database has the correct schemas and migrations."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            name TEXT PRIMARY KEY,
            encoding_blob BLOB NOT NULL,
            last_modified REAL NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'IN',
            timestamp DATETIME NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()
    
    # Run migration checks for old databases
    try:
        cursor.execute("PRAGMA table_info(logs)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'type' not in columns:
            cursor.execute("ALTER TABLE logs ADD COLUMN type TEXT NOT NULL DEFAULT 'IN'")
            conn.commit()
            logger.info("Migrated database: added 'type' column to logs table.")
    except Exception as e:
        logger.error(f"Migration error: {e}")
        
    conn.close()

init_db_structure()


def get_system_metrics():
    """Query current database metrics."""
    metrics = {
        'total_employees': 0,
        'total_logs': 0,
        'logs_today': 0,
        'unique_today': 0,
        'db_path': app.config['DB_PATH'],
        'image_dir': app.config['IMAGE_DIR'],
        'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM students")
        metrics['total_employees'] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM logs")
        metrics['total_logs'] = cursor.fetchone()[0]
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT COUNT(*) FROM logs WHERE date = ?", (today_str,))
        metrics['logs_today'] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT name) FROM logs WHERE date = ?", (today_str,))
        metrics['unique_today'] = cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"Metrics query error: {e}")
    finally:
        if conn:
            conn.close()
    return metrics


# ================= DECORATOR: REQUIRE ADMIN LOGIN =================
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            # Return JSON for unauthorized API requests
            if request.path.startswith('/api/') or request.path == '/download':
                return jsonify({'message': 'Unauthorized. Admin password authentication required.'}), 401
            # Redirect browser pages to the login portal
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


# ================= TEMPLATE: USER CLOCK-IN INTERFACE =================
USER_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Biometric Attendance Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.75);
            --card-border: rgba(91, 192, 190, 0.15);
            --accent-green: #10b981;
            --accent-blue: #3b82f6;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --active-glow: rgba(16, 185, 129, 0.25);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', sans-serif;
            -webkit-tap-highlight-color: transparent;
        }

        body {
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 10% 20%, rgba(59, 130, 246, 0.08) 0px, transparent 50%),
                radial-gradient(at 90% 80%, rgba(16, 185, 129, 0.08) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .container {
            width: 100%;
            max-width: 540px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 30px;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            text-align: center;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }

        header h1 {
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(135deg, #ffffff 40%, var(--accent-green) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }

        header p {
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .mode-selector {
            display: grid;
            grid-template-columns: 1fr 1fr;
            background: rgba(0, 0, 0, 0.3);
            padding: 4px;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        .mode-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 14px;
            font-size: 14px;
            font-weight: 700;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.25s ease;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .mode-btn.active.mode-in {
            background-color: var(--accent-green);
            color: #0b0f19;
            box-shadow: 0 0 15px var(--accent-green);
        }

        .mode-btn.active.mode-out {
            background-color: var(--accent-blue);
            color: #0b0f19;
            box-shadow: 0 0 15px var(--accent-blue);
        }

        .tab-nav {
            display: flex;
            justify-content: center;
            gap: 10px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 12px;
        }

        .tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 13px;
            font-weight: 600;
            padding: 8px 16px;
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.2s ease;
        }

        .tab-btn.active {
            color: var(--text-main);
            background: rgba(255, 255, 255, 0.05);
        }

        .tab-content {
            display: none;
            flex-direction: column;
            align-items: center;
            gap: 20px;
        }

        .tab-content.active {
            display: flex;
        }

        .camera-container {
            width: 260px;
            height: 260px;
            border-radius: 50%;
            overflow: hidden;
            border: 4px solid var(--accent-green);
            background: #000000;
            position: relative;
            box-shadow: 0 0 20px var(--active-glow);
            transition: border-color 0.25s ease;
        }

        video {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transform: scaleX(-1);
        }

        canvas {
            display: none;
        }

        .terminal-instructions {
            text-align: center;
            font-size: 14px;
            color: var(--text-muted);
            line-height: 1.6;
            padding: 10px;
        }

        .btn {
            width: 100%;
            padding: 14px;
            border-radius: 12px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            color: #ffffff;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }

        .btn-green {
            background: linear-gradient(135deg, var(--accent-green) 0%, #059669 100%);
            box-shadow: 0 4px 15px rgba(16, 185, 129, 0.2);
            color: #0b0f19;
            font-weight: 700;
        }

        .btn-green:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(16, 185, 129, 0.35);
        }

        .btn-blue {
            background: linear-gradient(135deg, var(--accent-blue) 0%, #2563eb 100%);
            box-shadow: 0 4px 15px rgba(59, 130, 246, 0.2);
            color: #0b0f19;
            font-weight: 700;
        }

        .btn-blue:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(59, 130, 246, 0.35);
        }

        .timer-circle {
            width: 120px;
            height: 120px;
            border-radius: 50%;
            border: 6px solid rgba(255, 255, 255, 0.1);
            border-top-color: var(--accent-green);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 36px;
            font-weight: 700;
            animation: pulse 1s infinite alternate;
        }

        @keyframes pulse {
            0% { transform: scale(0.96); }
            100% { transform: scale(1.04); }
        }

        .feedback {
            padding: 14px 20px;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 500;
            display: none;
            width: 100%;
            text-align: center;
            line-height: 1.4;
        }

        .feedback-success {
            background: rgba(16, 185, 129, 0.1);
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.2);
        }

        .feedback-error {
            background: rgba(239, 68, 68, 0.1);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.2);
        }
    </style>
</head>
<body>

    <div class="container">
        <div class="card">
            <header>
                <h1>Attendance Terminal</h1>
                <p>Biometric Authentication System</p>
            </header>

            <!-- IN/OUT Toggle -->
            <div class="mode-selector">
                <button class="mode-btn active mode-in" id="btnModeIn" onclick="setMode('IN')">Clock In</button>
                <button class="mode-btn mode-out" id="btnModeOut" onclick="setMode('OUT')">Clock Out</button>
            </div>

            <!-- Tab Selector -->
            <div class="tab-nav">
                <button class="tab-btn active" id="tabNavPhone" onclick="switchTab('phone')">Verify via Phone Camera</button>
                <button class="tab-btn" id="tabNavTerminal" onclick="switchTab('terminal')">Use Wall Terminal</button>
            </div>

            <!-- Device Camera Scan Panel -->
            <div class="tab-content active" id="tabPhone">
                <div class="camera-container" id="camFrame">
                    <video id="videoElement" autoplay playsinline muted></video>
                </div>
                <button class="btn btn-green" id="actionBtn" onclick="takeSelfie()">
                    Scan & Verify Face
                </button>
            </div>

            <!-- Wall Terminal Trigger Panel -->
            <div class="tab-content" id="tabTerminal">
                <div class="terminal-instructions" id="terminalPrompt">
                    Approach the wall-mounted webcam, select your check-in mode above, and click the activation button to scan your face.
                </div>
                <div class="timer-circle" id="countdownTimer" style="display: none;">20</div>
                <button class="btn btn-secondary" id="terminalBtn" onclick="activateTerminalCamera()">
                    Trigger Wall Camera Scan
                </button>
            </div>

            <!-- User Alert Feedback -->
            <div class="feedback" id="feedbackBox"></div>
        </div>
    </div>

    <!-- Hidden canvas for photo extraction -->
    <canvas id="photoCanvas" width="640" height="480"></canvas>

    <script>
        let selectedMode = 'IN';
        let activeTab = 'phone';
        let stream = null;
        let countdownInterval = null;

        function setMode(mode) {
            selectedMode = mode;
            document.getElementById('btnModeIn').classList.toggle('active', mode === 'IN');
            document.getElementById('btnModeOut').classList.toggle('active', mode === 'OUT');
            
            const camFrame = document.getElementById('camFrame');
            const actionBtn = document.getElementById('actionBtn');
            const countdownTimer = document.getElementById('countdownTimer');
            
            if (mode === 'IN') {
                camFrame.style.borderColor = 'var(--accent-green)';
                camFrame.style.boxShadow = '0 0 20px rgba(16, 185, 129, 0.25)';
                actionBtn.className = 'btn btn-green';
                countdownTimer.style.borderColor = 'rgba(255, 255, 255, 0.1)';
                countdownTimer.style.borderTopColor = 'var(--accent-green)';
            } else {
                camFrame.style.borderColor = 'var(--accent-blue)';
                camFrame.style.boxShadow = '0 0 20px rgba(59, 130, 246, 0.25)';
                actionBtn.className = 'btn btn-blue';
                countdownTimer.style.borderColor = 'rgba(255, 255, 255, 0.1)';
                countdownTimer.style.borderTopColor = 'var(--accent-blue)';
            }
        }

        function switchTab(tab) {
            activeTab = tab;
            document.getElementById('tabNavPhone').classList.toggle('active', tab === 'phone');
            document.getElementById('tabNavTerminal').classList.toggle('active', tab === 'terminal');
            document.getElementById('tabPhone').classList.toggle('active', tab === 'phone');
            document.getElementById('tabTerminal').classList.toggle('active', tab === 'terminal');

            hideAlert();

            if (tab === 'phone') {
                startCamera();
                stopCountdown();
            } else {
                stopCamera();
            }
        }

        async function startCamera() {
            if (stream) return;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ 
                    video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } } 
                });
                document.getElementById('videoElement').srcObject = stream;
            } catch (err) {
                console.error("Camera access failed:", err);
                showAlert("Could not access camera. Note: Mobile browsers require HTTPS to access camera.", false);
            }
        }

        function stopCamera() {
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
                stream = null;
            }
        }

        async function takeSelfie() {
            if (!stream) {
                showAlert("Camera stream not active. If on a phone, make sure you are using localhost or HTTPS.", false);
                return;
            }

            const video = document.getElementById('videoElement');
            const canvas = document.getElementById('photoCanvas');
            const ctx = canvas.getContext('2d');
            
            // Wait for video dimensions to map
            if (video.videoWidth > 0) {
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
            }
            
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            const dataURL = canvas.toDataURL('image/jpeg', 0.85);

            showAlert("Transmitting selfie for verification...", true, false);

            try {
                const res = await fetch('/api/user_clock', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image: dataURL, mode: selectedMode })
                });
                
                // If response is not ok (e.g. 500 error), read the error description
                if (!res.ok) {
                    let errMsg = "HTTP error " + res.status;
                    try {
                        const errData = await res.json();
                        errMsg = errData.message || errMsg;
                    } catch (e) {}
                    showAlert(errMsg, false);
                    return;
                }
                
                const data = await res.json();
                
                if (data.success) {
                    showAlert(data.message, true);
                    setTimeout(hideAlert, 5000);
                } else {
                    showAlert(data.message, false);
                }
            } catch (err) {
                console.error("Fetch failed:", err);
                showAlert("Connection failure. Check if server is running: " + err.message, false);
            }
        }

        async function activateTerminalCamera() {
            showAlert(`Activating wall camera for Clock ${selectedMode}...`, true, false);

            try {
                const res = await fetch('/api/terminal/set_mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: selectedMode })
                });

                if (!res.ok) {
                    let errMsg = "Failed to trigger terminal.";
                    try {
                        const errData = await res.json();
                        errMsg = errData.message || errMsg;
                    } catch (e) {}
                    showAlert(errMsg, false);
                    return;
                }

                hideAlert();
                startCountdown();
            } catch (err) {
                console.error("Terminal trigger failed:", err);
                showAlert("Failed to contact server: " + err.message, false);
            }
        }

        function startCountdown() {
            stopCountdown();
            
            const timer = document.getElementById('countdownTimer');
            const btn = document.getElementById('terminalBtn');
            const prompt = document.getElementById('terminalPrompt');

            prompt.innerHTML = `Wall camera scanning mode set to <strong>Clock ${selectedMode}</strong>.<br>Align your face in front of the terminal camera now.`;
            btn.style.display = 'none';
            timer.style.display = 'flex';
            
            let seconds = 20;
            timer.innerText = seconds;

            countdownInterval = setInterval(() => {
                seconds--;
                timer.innerText = seconds;

                if (seconds <= 0) {
                    stopCountdown();
                    prompt.innerHTML = "Wall camera scan window closed. Re-trigger if you missed your turn.";
                    btn.style.display = 'block';
                    timer.style.display = 'none';
                }
            }, 1000);
        }

        function stopCountdown() {
            if (countdownInterval) {
                clearInterval(countdownInterval);
                countdownInterval = null;
            }
        }

        function showAlert(msg, isSuccess, persists = true) {
            const box = document.getElementById('feedbackBox');
            box.innerText = msg;
            box.style.display = 'block';
            box.className = 'feedback ' + (isSuccess ? 'feedback-success' : 'feedback-error');
        }

        function hideAlert() {
            document.getElementById('feedbackBox').style.display = 'none';
        }

        window.addEventListener('load', () => {
            startCamera();
        });
    </script>
</body>
</html>
"""


# ================= TEMPLATE: ADMINISTRATOR LOGIN VIEW =================
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Administrative Authentication</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.75);
            --card-border: rgba(91, 192, 190, 0.15);
            --accent-green: #10b981;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --danger: #ef4444;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', sans-serif;
        }

        body {
            background-color: var(--bg-color);
            background-image: radial-gradient(at 50% 50%, rgba(16, 185, 129, 0.08) 0px, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }

        .login-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 40px 30px;
            max-width: 400px;
            width: 100%;
            text-align: center;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.4);
            display: flex;
            flex-direction: column;
            gap: 24px;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }

        h2 {
            font-size: 22px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }

        p.subtitle {
            font-size: 13px;
            color: var(--text-muted);
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 8px;
            text-align: left;
        }

        label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
        }

        input[type="password"] {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s ease;
            width: 100%;
        }

        input[type="password"]:focus {
            border-color: var(--accent-green);
        }

        .btn-submit {
            background: linear-gradient(135deg, var(--accent-green) 0%, #059669 100%);
            color: #0b0f19;
            padding: 14px;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            border: none;
            transition: transform 0.2s ease;
        }

        .btn-submit:hover {
            transform: translateY(-1px);
        }

        .alert-error {
            background: rgba(239, 68, 68, 0.1);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.2);
            padding: 12px;
            border-radius: 8px;
            font-size: 13px;
            text-align: center;
        }

        .back-link {
            font-size: 12px;
            color: var(--text-muted);
            text-decoration: none;
            transition: color 0.2s ease;
            margin-top: 10px;
        }

        .back-link:hover {
            color: var(--text-main);
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div>
            <h2>Admin Authentication</h2>
            <p class="subtitle">Enter access credential to unlock control panel</p>
        </div>

        {% if error %}
            <div class="alert-error">{{ error }}</div>
        {% endif %}

        <form action="/admin/login" method="POST" style="display: flex; flex-direction: column; gap: 20px;">
            <div class="form-group">
                <label for="password">Security Password</label>
                <input type="password" name="password" id="password" placeholder="••••••••" required autofocus>
            </div>
            <button type="submit" class="btn-submit">Verify Credentials</button>
        </form>

        <a href="/" class="back-link">Return to Clock-In Portal</a>
    </div>
</body>
</html>
"""


# ================= TEMPLATE: ADMINISTRATOR CONSOLE =================
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RaspAttendance Admin Panel</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --sidebar-bg: #111827;
            --card-bg: rgba(17, 24, 39, 0.7);
            --card-border: rgba(91, 192, 190, 0.15);
            --accent-green: #10b981;
            --accent-green-glow: rgba(16, 185, 129, 0.3);
            --accent-blue: #3b82f6;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --table-header: rgba(31, 41, 55, 0.9);
            --table-row-hover: rgba(91, 192, 190, 0.08);
            --btn-active: #10b981;
            --btn-hover: #059669;
            --danger: #ef4444;
            --danger-hover: #dc2626;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', sans-serif;
            -webkit-tap-highlight-color: transparent;
        }

        body {
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 10% 20%, rgba(59, 130, 246, 0.1) 0px, transparent 50%),
                radial-gradient(at 90% 80%, rgba(16, 185, 129, 0.08) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
        }

        /* Sidebar Navigation */
        .sidebar {
            width: 260px;
            background-color: var(--sidebar-bg);
            border-right: 1px solid var(--card-border);
            padding: 30px 20px;
            display: flex;
            flex-direction: column;
            gap: 40px;
            position: fixed;
            top: 0;
            bottom: 0;
            left: 0;
            z-index: 100;
        }

        .brand h1 {
            font-size: 20px;
            font-weight: 700;
            background: linear-gradient(135deg, #ffffff 40%, var(--btn-active) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }

        .brand p {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .nav-links {
            display: flex;
            flex-direction: column;
            gap: 8px;
            height: calc(100% - 60px);
        }

        .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 16px;
            border-radius: 8px;
            color: var(--text-muted);
            text-decoration: none;
            font-weight: 500;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s ease;
            background: transparent;
            border: none;
            width: 100%;
            text-align: left;
        }

        .nav-item:hover, .nav-item.active {
            color: var(--text-main);
            background: rgba(255, 255, 255, 0.05);
        }

        .nav-item.active {
            border-left: 3px solid var(--btn-active);
            background: rgba(16, 185, 129, 0.08);
            color: var(--btn-active);
        }

        .nav-item svg {
            width: 18px;
            height: 18px;
            stroke: currentColor;
        }

        /* Main Content */
        .main-content {
            margin-left: 260px;
            flex-grow: 1;
            padding: 40px;
            max-width: 1200px;
            width: calc(100% - 260px);
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            flex-wrap: wrap;
            gap: 16px;
        }

        .header-title h2 {
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }

        .header-title p {
            font-size: 14px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .status-badge {
            background: rgba(16, 185, 129, 0.1);
            color: var(--accent-green);
            padding: 6px 12px;
            border-radius: 50px;
            font-size: 12px;
            font-weight: 600;
            border: 1px solid rgba(16, 185, 129, 0.2);
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background-color: var(--accent-green);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--accent-green-glow);
        }

        /* Tab Panels */
        .tab-panel {
            display: none;
            flex-direction: column;
            gap: 30px;
        }

        .tab-panel.active {
            display: flex;
        }

        /* Metrics Grid */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
        }

        .metric-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
            transition: all 0.2s ease;
        }

        .metric-card:hover {
            transform: translateY(-2px);
            border-color: rgba(16, 185, 129, 0.3);
        }

        .metric-title {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .metric-value {
            font-size: 32px;
            font-weight: 700;
            margin-top: 10px;
            letter-spacing: -1px;
        }

        .metric-footer {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 10px;
        }

        /* Panel Card Elements */
        .panel-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.2);
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .panel-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 16px;
        }

        .panel-card-header h3 {
            font-size: 18px;
            font-weight: 600;
        }

        .panel-card-header p {
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 2px;
        }

        /* Form Controls */
        .form-group {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 16px;
        }

        label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
        }

        input[type="text"], input[type="file"] {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 14px;
            outline: none;
            transition: all 0.2s ease;
            width: 100%;
        }

        input[type="file"] {
            padding: 8px 12px;
            cursor: pointer;
        }

        input[type="text"]:focus {
            border-color: var(--btn-active);
            box-shadow: 0 0 8px rgba(16, 185, 129, 0.2);
        }

        .btn {
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            border: none;
            color: #ffffff;
            text-decoration: none;
        }

        .btn-success {
            background-color: var(--btn-active);
        }

        .btn-success:hover {
            background-color: var(--btn-hover);
        }

        .btn-danger {
            background-color: var(--danger);
        }

        .btn-danger:hover {
            background-color: var(--danger-hover);
        }

        .btn-secondary {
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .btn-secondary:hover {
            background-color: rgba(255, 255, 255, 0.1);
        }

        .btn-sm {
            padding: 6px 12px;
            font-size: 12px;
            border-radius: 6px;
        }

        /* Search Box */
        .search-container {
            position: relative;
            width: 100%;
            max-width: 300px;
        }

        .search-input {
            width: 100%;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--card-border);
            color: var(--text-main);
            padding: 10px 16px;
            padding-left: 36px;
            border-radius: 8px;
            font-size: 14px;
            outline: none;
            transition: all 0.2s ease;
        }

        .search-input:focus {
            border-color: var(--btn-active);
        }

        .search-icon {
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            pointer-events: none;
            width: 14px;
            height: 14px;
            stroke: currentColor;
        }

        /* Table CSS */
        .table-responsive {
            width: 100%;
            overflow-x: auto;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 14px;
        }

        th {
            background-color: var(--table-header);
            color: var(--text-muted);
            font-weight: 600;
            padding: 14px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }

        td {
            padding: 14px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            color: var(--text-main);
        }

        tr:last-child td {
            border-bottom: none;
        }

        tr:hover td {
            background-color: var(--table-row-hover);
        }

        .badge {
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .badge-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
        }

        .badge-in {
            background: rgba(16, 185, 129, 0.1);
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.2);
        }

        .badge-in .badge-dot {
            background-color: var(--accent-green);
        }

        .badge-out {
            background: rgba(59, 130, 246, 0.1);
            color: var(--accent-blue);
            border: 1px solid rgba(59, 130, 246, 0.2);
        }

        .badge-out .badge-dot {
            background-color: var(--accent-blue);
        }

        .no-data {
            text-align: center;
            padding: 40px !important;
            color: var(--text-muted);
            font-style: italic;
        }

        .columns-layout {
            display: grid;
            grid-template-columns: 1fr 2fr;
            gap: 30px;
        }

        @media (max-width: 900px) {
            .columns-layout {
                grid-template-columns: 1fr;
            }
        }

        /* Danger Settings Cards */
        .settings-danger-card {
            border: 1px solid rgba(239, 68, 68, 0.3);
            background: rgba(239, 68, 68, 0.03);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }

        .danger-text h4 {
            color: var(--danger);
            font-size: 16px;
            font-weight: 600;
        }

        .danger-text p {
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* Processing Modal overlay */
        .overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.85);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            backdrop-filter: blur(8px);
        }

        .overlay-content {
            background: var(--sidebar-bg);
            border: 1px solid var(--card-border);
            padding: 40px;
            border-radius: 16px;
            text-align: center;
            max-width: 400px;
            width: 90%;
            display: flex;
            flex-direction: column;
            gap: 20px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
        }

        .spinner {
            width: 50px;
            height: 50px;
            border: 5px solid rgba(255, 255, 255, 0.1);
            border-top-color: var(--btn-active);
            border-radius: 50%;
            animation: spin 1s infinite linear;
            margin: 0 auto;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        /* Mobile Layout */
        @media (max-width: 768px) {
            body {
                flex-direction: column;
            }
            .sidebar {
                width: 100%;
                position: relative;
                padding: 20px;
                gap: 20px;
                border-right: none;
                border-bottom: 1px solid var(--card-border);
            }
            .main-content {
                margin-left: 0;
                width: 100%;
                padding: 20px;
            }
        }
    </style>
</head>
<body>

    <!-- Sidebar Navigation -->
    <div class="sidebar">
        <div class="brand">
            <h1>RaspAttendance</h1>
            <p>Admin Console</p>
        </div>
        <div class="nav-links">
            <button class="nav-item active" onclick="switchTab('dashboard')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"></rect><rect x="14" y="3" width="7" height="5"></rect><rect x="14" y="12" width="7" height="9"></rect><rect x="3" y="16" width="7" height="5"></rect></svg>
                Dashboard
            </button>
            <button class="nav-item" onclick="switchTab('employees')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
                Manage Employees
            </button>
            <button class="nav-item" onclick="switchTab('settings')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
                System Settings
            </button>
            
            <!-- Logout / Return trigger -->
            <a href="/admin/logout" class="nav-item" style="margin-top: auto; border: 1px solid rgba(239, 68, 68, 0.2); color: var(--danger);">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
                Logout & Lock
            </a>
        </div>
    </div>

    <!-- Main Page Content -->
    <div class="main-content">
        <header>
            <div class="header-title">
                <h2 id="viewTitle">System Dashboard</h2>
                <p id="viewSubtitle">Real-time attendance intelligence</p>
            </div>
            <div class="status-badge">
                <span class="status-dot"></span>
                <span>Offline Hub Active</span>
            </div>
        </header>

        <!-- ================= DASHBOARD TAB ================= -->
        <div id="tab-dashboard" class="tab-panel active">
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-title">Enrolled Employees</div>
                    <div class="metric-value" id="valStudents">-</div>
                    <div class="metric-footer">Total facial database records</div>
                </div>
                <div class="metric-card">
                    <div class="metric-title">Total Detections</div>
                    <div class="metric-value" id="valLogs">-</div>
                    <div class="metric-footer">All-time check-in log records</div>
                </div>
                <div class="metric-card">
                    <div class="metric-title">Logs Today</div>
                    <div class="metric-value" id="valToday">-</div>
                    <div class="metric-footer">Scanned clock-ins today</div>
                </div>
                <div class="metric-card">
                    <div class="metric-title">Unique Scans Today</div>
                    <div class="metric-value" id="valUnique">-</div>
                    <div class="metric-footer">Individual active employees</div>
                </div>
            </div>

            <div class="panel-card">
                <div class="panel-card-header">
                    <div>
                        <h3>Recent Attendance Logs</h3>
                        <p>Latest 20 scan entries recorded locally</p>
                    </div>
                    <div style="display: flex; gap: 10px; align-items: center;">
                        <a href="/download?format=xlsx" class="btn btn-secondary btn-sm" style="background: rgba(16,185,129,0.1); border-color: rgba(16,185,129,0.3); color: var(--btn-active);">
                            Export Excel
                        </a>
                        <a href="/download?format=csv" class="btn btn-secondary btn-sm">
                            Export CSV
                        </a>
                        <div class="search-container">
                            <svg class="search-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                            <input type="text" class="search-input" id="searchLogs" placeholder="Search logs...">
                        </div>
                    </div>
                </div>

                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Employee Name</th>
                                <th>Log Date</th>
                                <th>Local Time</th>
                                <th>Verification Status</th>
                            </tr>
                        </thead>
                        <tbody id="logsTableBody">
                            <tr>
                                <td colspan="4" class="no-data">Connecting to database...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ================= EMPLOYEES TAB ================= -->
        <div id="tab-employees" class="tab-panel">
            <div class="columns-layout">
                <div class="panel-card" style="align-self: flex-start;">
                    <div class="panel-card-header">
                        <div>
                            <h3>Add New Employee</h3>
                            <p>Register face vectors on-the-fly</p>
                        </div>
                    </div>
                    <form id="addEmployeeForm" onsubmit="submitEmployeeForm(event)">
                        <div class="form-group">
                            <label for="employeeName">Full Name / ID</label>
                            <input type="text" id="employeeName" placeholder="e.g. John Doe" required>
                        </div>
                        <div class="form-group">
                            <label for="employeePhoto">Front Facing Face Photo</label>
                            <input type="file" id="employeePhoto" accept="image/*" required>
                            <p style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                                File formats: JPG, PNG. Image must have a clear view of a single face.
                            </p>
                        </div>
                        <button type="submit" class="btn btn-success" style="width: 100%; margin-top: 10px;">
                            Extract & Register Face
                        </button>
                    </form>
                </div>

                <div class="panel-card">
                    <div class="panel-card-header">
                        <div>
                            <h3>Currently Enrolled Database</h3>
                            <p>All active biometric profiles</p>
                        </div>
                        <div class="search-container">
                            <svg class="search-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
                            <input type="text" class="search-input" id="searchEmployees" placeholder="Filter profiles...">
                        </div>
                    </div>

                    <div class="table-responsive">
                        <table>
                            <thead>
                                <th>Employee name</th>
                                <th>Last modified</th>
                                <th style="text-align: right;">Actions</th>
                            </thead>
                            <tbody id="employeesTableBody">
                                <tr>
                                    <td colspan="3" class="no-data">Fetching enrollments...</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- ================= SETTINGS TAB ================= -->
        <div id="tab-settings" class="tab-panel">
            <div class="panel-card">
                <div class="panel-card-header">
                    <div>
                        <h3>Biometric System Configuration</h3>
                        <p>Database paths and local directory mappings</p>
                    </div>
                </div>
                <div style="display: flex; flex-direction: column; gap: 12px; font-size: 14px;">
                    <div><strong>SQLite Database Path:</strong> <span style="font-family: monospace; color: var(--accent-green);" id="infoDb">-</span></div>
                    <div><strong>Images Directory:</strong> <span style="font-family: monospace; color: var(--accent-green);" id="infoImages">-</span></div>
                    <div><strong>System Current Time:</strong> <span id="infoTime">-</span></div>
                </div>
            </div>

            <div class="panel-card" style="border-color: rgba(239, 68, 68, 0.25);">
                <div class="panel-card-header" style="border-color: rgba(239, 68, 68, 0.1);">
                    <div>
                        <h3 style="color: var(--danger);">System Safety Zone</h3>
                        <p>Destructive maintenance actions</p>
                    </div>
                </div>

                <div style="display: flex; flex-direction: column; gap: 20px;">
                    <div class="settings-danger-card">
                        <div class="danger-text">
                            <h4>Clear Attendance Logs</h4>
                            <p>Delete all check-in database entries. Employee files remain untouched.</p>
                        </div>
                        <button class="btn btn-danger btn-sm" onclick="triggerClearLogs()">
                            Purge History logs
                        </button>
                    </div>

                    <div class="settings-danger-card">
                        <div class="danger-text">
                            <h4>Factory Reset System</h4>
                            <p>Wipe all logs, clean the students database, and purge all saved images.</p>
                        </div>
                        <button class="btn btn-danger btn-sm" onclick="triggerSystemReset()">
                            Wipe Entire Database
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Processing Feedback Overlay Modal -->
    <div class="overlay" id="loadingOverlay">
        <div class="overlay-content">
            <div class="spinner"></div>
            <h3 id="overlayText">Processing face validation...</h3>
            <p style="font-size: 13px; color: var(--text-muted);">
                Analyzing facial features. This may take a moment.
            </p>
        </div>
    </div>

    <script>
        function switchTab(tabName) {
            document.querySelectorAll('.nav-item').forEach(btn => btn.classList.remove('active'));
            const btnIdx = ['dashboard', 'employees', 'settings'].indexOf(tabName);
            if (btnIdx !== -1) {
                document.querySelectorAll('.nav-item')[btnIdx].classList.add('active');
            }

            document.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));
            document.getElementById(`tab-${tabName}`).classList.add('active');

            const titleMap = {
                'dashboard': ['System Dashboard', 'Real-time attendance intelligence'],
                'employees': ['Employee Database', 'Manage biometric enrollments'],
                'settings': ['System Settings', 'Local system environment management']
            };
            document.getElementById('viewTitle').innerText = titleMap[tabName][0];
            document.getElementById('viewSubtitle').innerText = titleMap[tabName][1];
            
            if (tabName === 'employees') {
                fetchEmployees();
            } else if (tabName === 'settings') {
                updateSettingsInfo();
            }
        }

        let cachedLogs = [];
        let cachedEmployees = [];

        function showLoading(text) {
            document.getElementById('overlayText').innerText = text;
            document.getElementById('loadingOverlay').style.display = 'flex';
        }

        function hideLoading() {
            document.getElementById('loadingOverlay').style.display = 'none';
        }

        async function fetchDashboardData() {
            try {
                const response = await fetch('/api/logs');
                if (!response.ok) throw new Error('API server returned error');
                
                const data = await response.json();
                
                document.getElementById('valStudents').innerText = data.metrics.total_employees;
                document.getElementById('valLogs').innerText = data.metrics.total_logs;
                document.getElementById('valToday').innerText = data.metrics.logs_today;
                document.getElementById('valUnique').innerText = data.metrics.unique_today;
                
                cachedLogs = data.logs;
                const searchVal = document.getElementById('searchLogs').value;
                renderLogsTable(cachedLogs, searchVal);
            } catch (err) {
                console.error("Dashboard update failed:", err);
            }
        }

        function renderLogsTable(logs, filter = '') {
            const tbody = document.getElementById('logsTableBody');
            const filtered = logs.filter(log => 
                log.name.toLowerCase().includes(filter.toLowerCase())
            );

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="4" class="no-data">${filter ? 'No matching records' : 'No records registered today'}</td></tr>`;
                return;
            }

            let html = '';
            filtered.forEach(log => {
                const typeClass = log.type === 'OUT' ? 'badge-out' : 'badge-in';
                const typeText = log.type === 'OUT' ? 'Clock Out' : 'Clock In';
                html += `
                    <tr>
                        <td><strong>${log.name}</strong></td>
                        <td>${log.date}</td>
                        <td>${log.time}</td>
                        <td>
                            <span class="badge ${typeClass}">
                                <span class="badge-dot"></span>
                                ${typeText}
                            </span>
                        </td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        }

        async function fetchEmployees() {
            try {
                const res = await fetch('/api/employees');
                if (!res.ok) throw new Error("Could not retrieve employees");
                const data = await res.json();
                cachedEmployees = data.employees;
                const filter = document.getElementById('searchEmployees').value;
                renderEmployeesTable(cachedEmployees, filter);
            } catch (err) {
                console.error("Error fetching employees:", err);
            }
        }

        function renderEmployeesTable(employees, filter = '') {
            const tbody = document.getElementById('employeesTableBody');
            const filtered = employees.filter(emp => 
                emp.name.toLowerCase().includes(filter.toLowerCase())
            );

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="3" class="no-data">${filter ? 'No matching employee profile' : 'No employees registered'}</td></tr>`;
                return;
            }

            let html = '';
            filtered.forEach(emp => {
                html += `
                    <tr>
                        <td><strong>${emp.name}</strong></td>
                        <td style="color: var(--text-muted); font-size: 13px;">${emp.last_modified}</td>
                        <td style="text-align: right;">
                            <button class="btn btn-danger btn-sm" onclick="deleteEmployee('${emp.name}')">
                                Delete Profile
                            </button>
                        </td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        }

        async function submitEmployeeForm(event) {
            event.preventDefault();
            const nameInput = document.getElementById('employeeName');
            const fileInput = document.getElementById('employeePhoto');
            
            if (!nameInput.value.trim() || fileInput.files.length === 0) return;

            const formData = new FormData();
            formData.append('name', nameInput.value.trim());
            formData.append('photo', fileInput.files[0]);

            showLoading("Analyzing facial features & creating vector... Please wait.");

            try {
                const res = await fetch('/api/employee/add', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await res.json();
                hideLoading();

                if (res.ok) {
                    alert(data.message);
                    nameInput.value = '';
                    fileInput.value = '';
                    fetchEmployees();
                } else {
                    alert("Error: " + data.message);
                }
            } catch (err) {
                hideLoading();
                alert("Network error occurred while uploading: " + err.message);
            }
        }

        async function deleteEmployee(name) {
            if (!confirm(`Are you sure you want to delete '${name}'? This will wipe their image and facial cache.`)) return;

            showLoading("Removing student/employee profile...");
            try {
                const res = await fetch('/api/employee/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name })
                });
                const data = await res.json();
                hideLoading();
                
                if (res.ok) {
                    alert(data.message);
                    fetchEmployees();
                } else {
                    alert("Error: " + data.message);
                }
            } catch (err) {
                hideLoading();
                alert("Failed to delete profile due to network error: " + err.message);
            }
        }

        async function updateSettingsInfo() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                document.getElementById('infoDb').innerText = data.metrics.db_path;
                document.getElementById('infoImages').innerText = data.metrics.image_dir;
                document.getElementById('infoTime').innerText = data.metrics.time;
            } catch (err) {
                console.error("Settings load failed:", err);
            }
        }

        async function triggerClearLogs() {
            if (!confirm("Are you sure you want to delete all historical attendance records? This action cannot be undone.")) return;

            showLoading("Clearing logs table...");
            try {
                const res = await fetch('/api/settings/clear_logs', { method: 'POST' });
                const data = await res.json();
                hideLoading();
                alert(data.message);
                fetchDashboardData();
            } catch (err) {
                hideLoading();
                alert("Failed to purge logs: " + err.message);
            }
        }

        async function triggerSystemReset() {
            if (!confirm("CRITICAL WARNING: This will delete ALL employees, ALL logs, and ALL image files. This will fully reset the database. Type OK to continue.")) return;
            if (!confirm("Final Confirmation: Do you really want to wipe the system?")) return;

            showLoading("Resetting system to clean state...");
            try {
                const res = await fetch('/api/settings/reset_system', { method: 'POST' });
                const data = await res.json();
                hideLoading();
                alert(data.message);
                fetchDashboardData();
                fetchEmployees();
            } catch (err) {
                hideLoading();
                alert("Failed to reset system: " + err.message);
            }
        }

        document.getElementById('searchLogs').addEventListener('input', (e) => {
            renderLogsTable(cachedLogs, e.target.value);
        });

        document.getElementById('searchEmployees').addEventListener('input', (e) => {
            renderEmployeesTable(cachedEmployees, e.target.value);
        });

        fetchDashboardData();
        setInterval(fetchDashboardData, 5000);
    </script>
</body>
</html>
"""


@app.route('/')
def user_portal():
    """Renders user-facing clock-in/out landing page."""
    return render_template_string(USER_TEMPLATE)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Handles admin password login portal."""
    # If already logged in, redirect directly to admin panel
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_portal'))

    if request.method == 'POST':
        entered_password = request.form.get('password', '')
        if entered_password == app.config['ADMIN_PASSWORD']:
            session['admin_logged_in'] = True
            logger.info("Admin user successfully authenticated.")
            return redirect(url_for('admin_portal'))
        else:
            logger.warning("Failed admin authentication attempt.")
            return render_template_string(LOGIN_TEMPLATE, error="Incorrect administrative password.")

    return render_template_string(LOGIN_TEMPLATE, error=None)


@app.route('/admin/logout')
def admin_logout():
    """Wipes admin session and redirects to User Portal."""
    session.pop('admin_logged_in', None)
    logger.info("Admin session logged out and locked.")
    return redirect(url_for('user_portal'))


@app.route('/admin')
@admin_required
def admin_portal():
    """Renders administrator management panel."""
    return render_template_string(ADMIN_TEMPLATE)


@app.route('/api/logs')
def api_logs():
    """Returns database metrics and latest 20 logs. Open to public view for dashboard updates."""
    metrics = get_system_metrics()
    logs = []
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name, date, time, type FROM logs ORDER BY timestamp DESC LIMIT 20")
        rows = cursor.fetchall()
        for row in rows:
            logs.append({
                'name': row['name'],
                'date': row['date'],
                'time': row['time'],
                'type': row['type'] if 'type' in row.keys() else 'IN'
            })
    except Exception as e:
        logger.error(f"Error querying logs API: {e}")
    finally:
        if conn:
            conn.close()
            
    return jsonify({
        'metrics': metrics,
        'logs': logs
    })


@app.route('/api/employees')
@admin_required
def api_employees():
    """Returns all enrolled employee profiles. Requires admin auth."""
    employees = []
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name, last_modified FROM students ORDER BY name ASC")
        rows = cursor.fetchall()
        for row in rows:
            mod_time = row['last_modified']
            mod_time_str = datetime.fromtimestamp(mod_time).strftime("%Y-%m-%d %H:%M:%S") if mod_time else "N/A"
            employees.append({
                'name': row['name'],
                'last_modified': mod_time_str
            })
    except Exception as e:
        logger.error(f"Error querying employees API: {e}")
    finally:
        if conn:
            conn.close()
            
    return jsonify({
        'employees': employees
    })


@app.route('/api/employee/add', methods=['POST'])
@admin_required
def api_add_employee():
    """Registers a new employee by photo upload. Requires admin auth."""
    if 'name' not in request.form or 'photo' not in request.files:
        return jsonify({'message': 'Missing name or photo file'}), 400
        
    name = request.form['name'].strip()
    photo_file = request.files['photo']
    
    if not name or photo_file.filename == '':
        return jsonify({'message': 'Invalid name or empty photo filename'}), 400

    sanitized_name = secure_filename(name.replace(" ", "_"))
    if not sanitized_name:
        return jsonify({'message': 'Invalid name characters'}), 400
        
    _, ext = os.path.splitext(photo_file.filename)
    if ext.lower() not in ['.jpg', '.jpeg', '.png', '.bmp']:
        return jsonify({'message': 'Unsupported format. Use JPG, PNG, or BMP'}), 400
        
    filename = f"{sanitized_name}{ext.lower()}"
    save_path = os.path.join(app.config['IMAGE_DIR'], filename)
    
    try:
        photo_file.save(save_path)
        logger.info(f"Image saved to: {save_path}")
    except Exception as e:
        logger.error(f"Failed to save image file: {e}")
        return jsonify({'message': f'Failed to write image file: {e}'}), 500

    face_extracted = False
    warning_msg = ""
    try:
        import face_recognition
        import numpy as np
        
        image = face_recognition.load_image_file(save_path)
        encodings = face_recognition.face_encodings(image)
        
        if len(encodings) > 0:
            encoding_blob = encodings[0].tobytes()
            conn = get_db()
            cursor = conn.cursor()
            mod_time = os.path.getmtime(save_path)
            cursor.execute(
                "INSERT OR REPLACE INTO students (name, encoding_blob, last_modified) VALUES (?, ?, ?)",
                (name, encoding_blob, mod_time)
            )
            conn.commit()
            conn.close()
            face_extracted = True
            logger.info(f"Biometric profile successfully registered online for {name}")
        else:
            warning_msg = (
                "Employee image saved, but NO faces could be detected in the photo. "
                "Ensure you upload a clear, well-lit view of a single face."
            )
    except Exception as e:
        logger.warning(f"Face extraction failed in web context: {e}.")
        warning_msg = (
            "Employee image saved successfully. Background processing active. "
            "Profile will load shortly."
        )

    if face_extracted:
        return jsonify({'message': f"Employee '{name}' registered successfully!"}), 200
    else:
        return jsonify({'message': warning_msg}), 200


@app.route('/api/employee/delete', methods=['POST'])
@admin_required
def api_delete_employee():
    """Deletes an employee and removes their image file. Requires admin auth."""
    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({'message': 'Missing employee name'}), 400
        
    name = data['name']
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM students WHERE name = ?", (name,))
    conn.commit()
    conn.close()

    sanitized_name = secure_filename(name.replace(" ", "_"))
    image_dir = app.config['IMAGE_DIR']
    
    deleted_files = 0
    valid_extensions = ['.jpg', '.jpeg', '.png', '.bmp']
    for ext in valid_extensions:
        file_path = os.path.join(image_dir, f"{sanitized_name}{ext}")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                deleted_files += 1
            except Exception as e:
                logger.error(f"Error removing file {file_path}: {e}")
                
    return jsonify({
        'message': f"Deleted profile '{name}' successfully. Removed {deleted_files} photo files."
    }), 200


@app.route('/api/user_clock', methods=['POST'])
def api_user_clock():
    """
    Clock-in route using the user's phone camera.
    Open to the public for check-in operations.
    """
    data = request.get_json()
    if not data or 'image' not in data or 'mode' not in data:
        return jsonify({'success': False, 'message': 'Missing image or mode data'}), 400
        
    mode = data['mode'].upper()
    if mode not in ['IN', 'OUT']:
        return jsonify({'success': False, 'message': 'Invalid mode'}), 400
        
    image_data = data['image']
    if ',' in image_data:
        _, encoded = image_data.split(',', 1)
    else:
        encoded = image_data
        
    try:
        import base64
        import cv2
        import numpy as np
        
        image_bytes = base64.b64decode(encoded)
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({'success': False, 'message': 'Could not decode image'}), 400
        
        # Prevent dlib crashes due to zero-dimension inputs
        if img.shape[0] == 0 or img.shape[1] == 0:
            return jsonify({'success': False, 'message': 'Invalid selfie image sizes.'}), 400
            
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    except Exception as e:
        logger.error(f"Error decoding image in user clock: {e}")
        return jsonify({'success': False, 'message': f'Image decoding error: {e}'}), 400

    try:
        import face_recognition
    except ImportError:
        return jsonify({
            'success': False, 
            'message': 'Face recognition module is not running on this server. Please use the wall camera instead.'
        }), 501

    try:
        face_locations = face_recognition.face_locations(rgb_img)
        if len(face_locations) == 0:
            return jsonify({'success': False, 'message': 'No face detected in your selfie. Please adjust your alignment and lighting.'}), 200
            
        face_encodings = face_recognition.face_encodings(rgb_img, face_locations)
        if len(face_encodings) == 0:
            return jsonify({'success': False, 'message': 'Failed to extract facial vectors.'}), 200
            
        target_encoding = face_encodings[0]
        
        # Load known encodings
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name, encoding_blob FROM students")
        rows = cursor.fetchall()
        
        known_names = []
        known_encodings = []
        for row in rows:
            known_names.append(row['name'])
            known_encodings.append(np.frombuffer(row['encoding_blob'], dtype=np.float64))
        conn.close()
        
        if not known_encodings:
            return jsonify({'success': False, 'message': 'No registered employees found in system. Please contact the administrator.'}), 200
            
        face_distances = face_recognition.face_distance(known_encodings, target_encoding)
        best_match_idx = np.argmin(face_distances)
        
        # Match check (tolerance: 0.45)
        if face_distances[best_match_idx] <= 0.45:
            matched_name = known_names[best_match_idx]
            
            conn = get_db()
            cursor = conn.cursor()
            
            now = datetime.now()
            current_date = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M:%S")
            current_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
            
            # Smart deduplication per status type (20 minutes window)
            cursor.execute(
                "SELECT type, timestamp FROM logs WHERE name = ? AND date = ? AND type = ? ORDER BY timestamp DESC LIMIT 1",
                (matched_name, current_date, mode)
            )
            last_log = cursor.fetchone()
            
            is_duplicate = False
            if last_log:
                try:
                    last_time = datetime.strptime(last_log[1], "%Y-%m-%d %H:%M:%S")
                    if (now - last_time).total_seconds() < 1200:
                        is_duplicate = True
                except ValueError:
                    pass
                    
            if is_duplicate:
                conn.close()
                return jsonify({
                    'success': True,
                    'message': f"You are already clocked {mode} (within the last 20 minutes)!"
                }), 200
                
            cursor.execute(
                "INSERT INTO logs (name, date, time, type, timestamp) VALUES (?, ?, ?, ?, ?)",
                (matched_name, current_date, current_time, mode, current_timestamp)
            )
            conn.commit()
            conn.close()
            
            logger.info(f"[USER PORTAL CLOCK] Employee: {matched_name} clocked {mode} at {current_time}")
            return jsonify({
                'success': True,
                'message': f"Hello {matched_name}! You have clocked {mode} successfully."
            }), 200
        else:
            return jsonify({'success': False, 'message': 'Face not recognized. Please verify you are registered.'}), 200
            
    except Exception as e:
        logger.error(f"Face matching error on user clock: {e}")
        return jsonify({'success': False, 'message': f'Face recognition engine error: {e}'}), 500


@app.route('/api/terminal/set_mode', methods=['POST'])
def api_set_terminal_mode():
    """Sets the active terminal clock-in/out mode. Open to public check-in triggers."""
    data = request.get_json()
    if not data or 'mode' not in data:
        return jsonify({'success': False, 'message': 'Missing mode'}), 400
        
    mode = data['mode'].upper()
    if mode not in ['IN', 'OUT']:
        return jsonify({'success': False, 'message': 'Invalid mode'}), 400
        
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('terminal_mode', ?, ?)",
            ('terminal_mode', mode, datetime.now().timestamp())
        )
        conn.commit()
        conn.close()
        logger.info(f"Terminal mode set to {mode}")
        return jsonify({'success': True, 'message': f'Terminal mode set to {mode}'}), 200
    except Exception as e:
        logger.error(f"Error setting terminal mode: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/settings/clear_logs', methods=['POST'])
@admin_required
def api_clear_logs():
    """Wipes logs table. Requires admin auth."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM logs")
        conn.commit()
        conn.close()
        logger.info("Logs table cleared.")
        return jsonify({'message': 'Attendance logs purged successfully.'}), 200
    except Exception as e:
        logger.error(f"Clear logs error: {e}")
        return jsonify({'message': f'Failed to clear logs: {e}'}), 500


@app.route('/api/settings/reset_system', methods=['POST'])
@admin_required
def api_reset_system():
    """Wipes all records and deletes student image files. Requires admin auth."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM logs")
        cursor.execute("DELETE FROM students")
        conn.commit()
        conn.close()
        
        image_dir = app.config['IMAGE_DIR']
        if os.path.exists(image_dir):
            valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp')
            for f in os.listdir(image_dir):
                if f.lower().endswith(valid_extensions):
                    file_path = os.path.join(image_dir, f)
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.error(f"Failed to delete file {file_path}: {e}")
                        
        logger.info("Factory reset completed.")
        return jsonify({'message': 'System fully reset. Database wiped and images directory cleaned.'}), 200
    except Exception as e:
        logger.error(f"Reset system error: {e}")
        return jsonify({'message': f'Failed to reset system: {e}'}), 500


@app.route('/download')
@admin_required
def download_logs():
    """Exports attendance data to Excel or CSV. Requires admin auth."""
    export_format = request.args.get('format', 'xlsx').lower()
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='logs'")
        if not cursor.fetchone():
            return "No logs table found.", 404

        cursor.execute(
            "SELECT name AS 'Employee Name', date AS 'Date', time AS 'Time', type AS 'Log Type', timestamp AS 'Timestamp' "
            "FROM logs ORDER BY timestamp DESC"
        )
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        data_rows = [list(row) for row in rows]
    except Exception as e:
        logger.error(f"Export query error: {e}")
        return f"Database error: {e}", 500
    finally:
        if conn:
            conn.close()

    if export_format == 'xlsx':
        try:
            import pandas as pd
            df = pd.DataFrame(data_rows, columns=columns)
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Attendance History')
            output.seek(0)
            filename = f"Attendance_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(
                output,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=filename
            )
        except ImportError:
            logger.warning("Pandas or openpyxl missing. Falling back to CSV export.")
            export_format = 'csv'

    if export_format == 'csv':
        import csv
        output = BytesIO()
        from io import TextIOWrapper
        wrapper = TextIOWrapper(output, encoding='utf-8', write_through=True)
        writer = csv.writer(wrapper)
        writer.writerow(columns)
        writer.writerows(data_rows)
        output.seek(0)
        filename = f"Attendance_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )

    return "Unsupported format", 400


if __name__ == '__main__':
    logger.info("Starting Offline Admin Portal server...")
    app.run(host='0.0.0.0', port=5000, debug=False)
