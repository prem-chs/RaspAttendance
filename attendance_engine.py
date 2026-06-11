#!/usr/bin/env python3
"""
Raspberry Pi Offline Face Recognition Attendance Engine (attendance_engine.py)

This script runs continuously to capture video frames, detect/recognize faces,
and log attendance records. It reads system_state to determine if the next
log should be marked as "IN" (Clock In) or "OUT" (Clock Out).

Author: Embedded Systems & Computer Vision Specialist
"""

import os
import sys
import time
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("attendance_engine.log", mode="a")
    ]
)
logger = logging.getLogger("AttendanceEngine")

# Try importing critical libraries and report missing dependencies
try:
    import cv2
except ImportError:
    logger.error("OpenCV is not installed. Please run: pip install opencv-python")
    sys.exit(1)

try:
    import face_recognition
except ImportError:
    logger.error("face_recognition is not installed. Please run: pip install face-recognition")
    sys.exit(1)


# Default Configurations
DEFAULT_DB_PATH = "/home/darkninja/RaspAttendance/attendance_system.db"
DEFAULT_IMAGE_DIR = "/home/pi/Student_Images/"
TOLERANCE_THRESHOLD = 0.45  # Stricter matching threshold to prevent false positives
DEDUPLICATION_MINUTES = 20  # Log attendance once every 20 minutes per student/employee
SYNC_INTERVAL_SECONDS = 10  # Sync with image directory every 10 seconds


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Raspberry Pi Face Recognition Attendance Engine")
    parser.add_argument(
        "--db", 
        type=str, 
        default=DEFAULT_DB_PATH, 
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--images", 
        type=str, 
        default=DEFAULT_IMAGE_DIR, 
        help=f"Path to student/employee images folder (default: {DEFAULT_IMAGE_DIR})"
    )
    parser.add_argument(
        "--tolerance", 
        type=float, 
        default=TOLERANCE_THRESHOLD, 
        help=f"Face recognition tolerance (default: {TOLERANCE_THRESHOLD})"
    )
    parser.add_argument(
        "--headless", 
        action="store_true", 
        default="DISPLAY" not in os.environ,
        help="Run without displaying OpenCV GUI window (auto-detected if DISPLAY is missing)"
    )
    return parser.parse_args()


def init_database(db_path):
    """Initialize SQLite database and ensure tables exist."""
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info(f"Created directory for database: {db_dir}")

    # Use check_same_thread=False to prevent errors if we access it across threads,
    # and use a 30-second timeout to handle database locking queueing gracefully.
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    cursor = conn.cursor()

    # Table for caching face encodings
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            name TEXT PRIMARY KEY,
            encoding_blob BLOB NOT NULL,
            last_modified REAL NOT NULL
        )
    """)

    # Table for check-in logs
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

    # Table for system state (syncing terminal modes)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def sync_student_dataset(db_conn, image_dir):
    """
    Synchronizes the database cache with the local image directory.
    Calculates facial vectors for new/modified images and removes stale records.
    """
    if not os.path.exists(image_dir):
        os.makedirs(image_dir, exist_ok=True)
        logger.warning(f"Images directory '{image_dir}' did not exist. Created it. Please place employee images here.")
        return [], []

    valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp')
    try:
        image_files = [f for f in os.listdir(image_dir) if f.lower().endswith(valid_extensions)]
    except Exception as e:
        logger.error(f"Error scanning directory '{image_dir}': {e}")
        return [], []
    
    directory_students = {}
    for f in image_files:
        name_key = os.path.splitext(f)[0].replace("_", " ").strip()
        full_path = os.path.join(image_dir, f)
        try:
            mod_time = os.path.getmtime(full_path)
            directory_students[name_key] = {
                'path': full_path,
                'mod_time': mod_time,
                'filename': f
            }
        except Exception as e:
            logger.error(f"Error reading modification time of file '{f}': {e}")

    # Fetch currently cached students from SQLite
    cursor = db_conn.cursor()
    cursor.execute("SELECT name, last_modified FROM students")
    cached_records = {row[0]: row[1] for row in cursor.fetchall()}

    # 1. Identify and delete stale database records
    stale_names = [name for name in cached_records if name not in directory_students]
    if stale_names:
        for name in stale_names:
            cursor.execute("DELETE FROM students WHERE name = ?", (name,))
            logger.info(f"Removed student/employee '{name}' from database cache (image deleted from directory)")
        db_conn.commit()

    # 2. Process new or modified images
    for name, info in directory_students.items():
        needs_update = False
        if name not in cached_records:
            needs_update = True
            logger.info(f"New student/employee image detected: '{info['filename']}'. Calculating facial vector...")
        elif info['mod_time'] > cached_records[name]:
            needs_update = True
            logger.info(f"Modified image detected for student/employee '{name}'. Updating facial vector...")

        if needs_update:
            try:
                image = face_recognition.load_image_file(info['path'])
                encodings = face_recognition.face_encodings(image)
                
                if len(encodings) > 0:
                    encoding = encodings[0]
                    encoding_blob = encoding.tobytes()
                    cursor.execute(
                        "INSERT OR REPLACE INTO students (name, encoding_blob, last_modified) VALUES (?, ?, ?)",
                        (name, encoding_blob, info['mod_time'])
                    )
                    db_conn.commit()
                    logger.info(f"Successfully cached vector for '{name}'")
                else:
                    logger.warning(f"No face detected in image '{info['filename']}'. Skipping registration.")
            except Exception as e:
                logger.error(f"Error processing image '{info['filename']}': {e}")

    # 3. Load all active student records from DB to memory for rapid recognition
    cursor.execute("SELECT name, encoding_blob FROM students")
    rows = cursor.fetchall()
    
    known_names = []
    known_encodings = []
    for row in rows:
        name = row[0]
        encoding = np.frombuffer(row[1], dtype=np.float64)
        known_names.append(name)
        known_encodings.append(encoding)
        
    return known_names, known_encodings


def get_terminal_mode(db_conn):
    """
    Checks if the user selected a specific mode (IN/OUT) on the portal.
    Mode expires after 20 seconds of inactivity, reverting to default 'IN'.
    """
    try:
        cursor = db_conn.cursor()
        cursor.execute("SELECT value, updated_at FROM system_state WHERE key = 'terminal_mode'")
        row = cursor.fetchone()
        if row:
            mode, updated_at = row[0], row[1]
            if time.time() - updated_at <= 20.0:
                return mode
    except Exception:
        pass
    return "IN"


def check_and_log_attendance(db_conn, name, last_logged_cache, log_type):
    """
    Implements smart deduplication logging.
    Logs check-in or check-out if the user has not logged in with
    the SAME status (IN/OUT) within the last 20 minutes on the current day.
    """
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")
    current_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    # Phase 1: Check in-memory cache
    cache_key = f"{name}_{log_type}"
    if cache_key in last_logged_cache:
        time_elapsed = now - last_logged_cache[cache_key]
        if time_elapsed < timedelta(minutes=DEDUPLICATION_MINUTES):
            return False

    # Phase 2: Query database to verify last entry for this type today is too recent
    cursor = db_conn.cursor()
    cursor.execute(
        "SELECT timestamp FROM logs WHERE name = ? AND date = ? AND type = ? ORDER BY timestamp DESC LIMIT 1",
        (name, current_date, log_type)
    )
    row = cursor.fetchone()

    if row:
        try:
            last_db_time = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            time_elapsed = now - last_db_time
            if time_elapsed < timedelta(minutes=DEDUPLICATION_MINUTES):
                last_logged_cache[cache_key] = last_db_time
                return False
        except ValueError:
            pass

    # Phase 3: Insert log entry
    try:
        cursor.execute(
            "INSERT INTO logs (name, date, time, type, timestamp) VALUES (?, ?, ?, ?, ?)",
            (name, current_date, current_time, log_type, current_timestamp)
        )
        db_conn.commit()
        last_logged_cache[cache_key] = now
        logger.info(f"[TERMINAL CLOCK SUCCESS] Employee: {name} clocked {log_type} at {current_time}")
        return True
    except sqlite3.OperationalError as e:
        logger.error(f"Database error writing log for {name}: {e}. Will retry.")
        return False


def run_recognition_engine(args):
    """Main recognition loop."""
    logger.info(f"Initializing database at: {args.db}")
    db_conn = init_database(args.db)

    logger.info(f"Syncing image directory: {args.images}")
    known_names, known_encodings = sync_student_dataset(db_conn, args.images)
    logger.info(f"Dataset Pre-loading Complete: {len(known_names)} active models loaded.")

    # Dictionary to cache the last logged datetime of students to prevent DB thrashing
    last_logged_cache = {}

    # Setup periodic directory sync variables
    last_sync_time = time.time()

    # Initialize Camera Capture
    logger.info("Initializing camera module...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logger.error("Could not open webcam/camera. Verify connections and permissions.")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    time.sleep(1.0)
    logger.info(f"Recognition engine running. Mode: {'Headless' if args.headless else 'GUI Windows enabled'}")

    try:
        while True:
            # 1. Periodically sync image directory (every 10 seconds)
            current_time = time.time()
            if current_time - last_sync_time > SYNC_INTERVAL_SECONDS:
                prev_count = len(known_names)
                known_names, known_encodings = sync_student_dataset(db_conn, args.images)
                new_count = len(known_names)
                if prev_count != new_count:
                    logger.info(f"Directory synced. Enrolled database count changed: {prev_count} -> {new_count}")
                last_sync_time = current_time

            # 2. Get active terminal clocking type (IN or OUT)
            log_type = get_terminal_mode(db_conn)

            # 3. Read frame
            ret, frame = cap.read()
            if not ret:
                logger.warning("Failed to grab video frame. Retrying...")
                time.sleep(0.1)
                continue

            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

            # Detect and calculate face vectors
            face_locations = face_recognition.face_locations(rgb_small_frame)
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)

            # Evaluate matches
            for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
                name = "Unknown"
                color = (0, 0, 255) # Red for unknown
                distance = 1.0

                if known_encodings:
                    face_distances = face_recognition.face_distance(known_encodings, face_encoding)
                    best_match_idx = np.argmin(face_distances)
                    
                    if face_distances[best_match_idx] <= args.tolerance:
                        name = known_names[best_match_idx]
                        distance = face_distances[best_match_idx]
                        color = (0, 255, 0) # Green for recognized student

                # Scale coordinates back
                top *= 4
                right *= 4
                bottom *= 4
                left *= 4

                # Draw bounding box
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

                # Draw label with name and active marking type
                label_text = f"{name} ({log_type})" if name != "Unknown" else name
                cv2.rectangle(frame, (left, bottom - 30), (right, bottom), color, cv2.FILLED)
                cv2.putText(
                    frame, 
                    label_text, 
                    (left + 6, bottom - 8), 
                    cv2.FONT_HERSHEY_SIMPLEX, 
                    0.6, 
                    (255, 255, 255), 
                    1,
                    cv2.LINE_AA
                )

                # Log check-in/out
                if name != "Unknown":
                    check_and_log_attendance(db_conn, name, last_logged_cache, log_type)

            # Display GUI window if not in headless mode
            if not args.headless:
                cv2.imshow("RaspAttendance - Recognition Engine", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info("Exit command received from GUI.")
                    break
            else:
                time.sleep(0.01)

    except KeyboardInterrupt:
        logger.info("Recognition engine interrupted.")
    finally:
        cap.release()
        if not args.headless:
            cv2.destroyAllWindows()
        db_conn.close()
        logger.info("Recognition engine stopped. Resources released.")


if __name__ == "__main__":
    startup_time = datetime.now()
    logger.info(f"System Startup Time: {startup_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    args = parse_arguments()
    run_recognition_engine(args)
