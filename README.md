# RaspAttendance: Offline Local-Hotspot Face Recognition Attendance System

An enterprise-grade, 100% offline, biometric face recognition attendance logging system designed to run on a Raspberry Pi 4 or Pi 5. The system establishes a local Wi-Fi hotspot where users can clock in/out via their mobile devices or a central terminal, with all logs and mathematical facial vectors cached locally in an SQLite database.

---

## Key Features

- **Split-Execution Architecture**:
  - **Camera Recognition Engine (`attendance_engine.py`)**: Runs continuously, downscaling OpenCV frames by 75% for optimized CPU usage on the Pi, and performs 128-dimensional dlib vector matching with a strict `0.45` accuracy tolerance.
  - **Data Sharing Web Portal (`share_logs.py`)**: Hosts a lightweight Flask server on port `5000` accessible to any client connected to the Pi's hotspot.
- **Biometric Caching**: Scans directory images once, extracts facial vectors, and caches them in SQLite. Subsequent boots bypass calculations and load instantly.
- **User Clock Portal (`/`)**: A sleek, user-facing portal allowing employees to select **Clock In** or **Clock Out** and mark attendance using:
  - **Phone Camera (Selfie)**: Secure face validation using their mobile device browser (requires HTTPS/localhost).
  - **Wall Terminal Camera**: Tapping a button triggers a 20-second active scanning window for the Pi's webcam.
- **Type-Specific Smart Deduplication**: Throttles logs so employees cannot double-clock IN (or OUT) within 20 minutes, while allowing immediate transitions (e.g. clocking IN and then clocking OUT).
- **SQLite Concurrency Protection**: Employs a `30.0` second query busy-timeout, preventing write lockouts when both scripts access the database concurrently.
- **Spreadsheet Log Exporters**: On-the-fly `/download` route compiling database logs into Excel (`.xlsx`) or CSV, downloading directly to mobile devices.
- **Password-Protected Console (`/admin`)**: A secure dashboard to enroll employees (with image face-validation checking), delete profiles, view live logs, and execute system resets.

---

## Project Structure

```
RaspAttendance/
├── Student_Images/          # Folder containing employee photos (Sanitized names, e.g. John_Doe.jpg)
│   └── .gitignore           # Preserves folder in Git without tracking biometric photos
├── attendance_engine.py     # Main face recognition engine & camera loop
├── share_logs.py            # Flask web server, user portal, and admin panel
├── setup.sh                 # Automatic package installer and virtual environment wrapper
├── .gitignore               # Excludes caches, databases, logs, and venv from tracking
└── README.md                # System documentation (this file)
```

---

## Deployment & Setup Guide

### 1. Installation & Environment Setup

Run the automated setup script to install dependencies, initialize a virtual environment, and compile libraries:
```bash
chmod +x setup.sh
./setup.sh
```
The script uses `--system-site-packages` to inherit pre-compiled Debian binary libraries (like OpenCV, Pandas, and Flask), saving hours of compilation time on the Raspberry Pi.

To activate the environment:
```bash
source venv/bin/activate
```

---

### 2. Hardware RTC (DS3231) Configuration

To maintain system time offline:
1. Open the device firmware configuration:
   - On **Bookworm**: `sudo nano /boot/firmware/config.txt`
   - On **Bullseye**: `sudo nano /boot/config.txt`
2. Add this overlay at the bottom:
   ```ini
   dtparam=i2c_arm=on
   dtoverlay=i2c-rtc,ds3231
   ```
3. Purge the fake-hwclock mockup:
   ```bash
   sudo apt-get remove fake-hwclock -y
   sudo dpkg --purge fake-hwclock
   ```
4. Comment out systemd checks in `/lib/udev/hwclock-set`:
   ```bash
   # if [ -e /run/systemd/system ]; then
   #     exit 0
   # fi
   ```
5. Reboot the Pi, verify the address `0x68` shows `UU` via `sudo i2cdetect -y 1`, and sync system time:
   ```bash
   # Write system time to RTC
   sudo hwclock -w
   # Read time from RTC
   sudo hwclock -r
   ```

---

### 3. Persistent Standalone Hotspot Setup

Configure NetworkManager to run a local Wi-Fi hotspot with a built-in DHCP server on the Pi's Wi-Fi card:
```bash
# Create connection
sudo nmcli connection add type wifi ifname wlan0 con-name Hotspot autoconnect yes ssid RaspAttendance
# Set WPA2 Security
sudo nmcli connection modify Hotspot 802-11-wireless.mode ap 802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk "attendance123"
# Allocate IP and share connection (starts local DHCP daemon)
sudo nmcli connection modify Hotspot ipv4.method shared ipv4.addresses 192.168.4.1/24
# Ignore IPv6
sudo nmcli connection modify Hotspot ipv6.method ignore
# Activate
sudo nmcli connection up Hotspot
```

---

### 4. Background Services (Systemd)

To launch both processes automatically at boot:
1. Ensure the execution user is in the `video` group:
   ```bash
   sudo usermod -aG video darkninja
   ```
2. Create `/etc/systemd/system/attendance_engine.service`:
   ```ini
   [Unit]
   Description=RaspAttendance Face Recognition Engine
   After=network.target

   [Service]
   Type=simple
   User=darkninja
   WorkingDirectory=/home/darkninja/RaspAttendance
   ExecStart=/home/darkninja/RaspAttendance/venv/bin/python3 /home/darkninja/RaspAttendance/attendance_engine.py --headless
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
3. Create `/etc/systemd/system/share_logs.service`:
   ```ini
   [Unit]
   Description=RaspAttendance Offline Web Portal
   After=network.target

   [Service]
   Type=simple
   User=darkninja
   WorkingDirectory=/home/darkninja/RaspAttendance
   ExecStart=/home/darkninja/RaspAttendance/venv/bin/python3 /home/darkninja/RaspAttendance/share_logs.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
4. Reload and enable the services:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable attendance_engine.service share_logs.service
   sudo systemctl start attendance_engine.service share_logs.service
   ```

---

## How to Test the Portals

1. Connect your laptop or phone to the Wi-Fi network: **`RaspAttendance`** (Password: `attendance123`).
2. Open a browser and navigate to: **`http://192.168.4.1:5000`** (or `http://localhost:5000` if testing locally on the Pi).
3. To access the admin controls, navigate to: **`http://192.168.4.1:5000/admin`** and enter the default password: **`admin123`**.
