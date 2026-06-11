#!/bin/bash
# RaspAttendance Local Testing Setup Script (setup.sh)
#
# This script installs system dependencies, creates a Python virtual
# environment inheriting system packages, and installs face-recognition.
#
# Author: Embedded Systems & Computer Vision Specialist

set -e

# Colors for terminal output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=====================================================${NC}"
echo -e "${BLUE}       RaspAttendance Offline System Setup           ${NC}"
echo -e "${BLUE}=====================================================${NC}"

# Check for apt-get package manager
if [ -x "$(command -v apt-get)" ]; then
    echo -e "${YELLOW}[1/4] Installing system dependencies via APT (requires sudo)...${NC}"
    # Install dependencies. OpenCV, Pandas, NumPy, Flask, OpenPyXL are installed via apt
    # to avoid slow source compilations on the Raspberry Pi / Debian systems.
    sudo apt-get update
    sudo apt-get install -y \
        python3-flask \
        python3-pandas \
        python3-openpyxl \
        python3-opencv \
        python3-numpy \
        python3-venv \
        python3-pip \
        i2c-tools \
        cmake \
        libboost-all-dev \
        libx11-dev \
        libatlas-base-dev \
        libgtk-3-dev
else
    echo -e "${RED}Error: apt-get package manager not found. Please install dependencies manually.${NC}"
    exit 1
fi

echo -e "${GREEN}System dependencies installed successfully.${NC}\n"

echo -e "${YELLOW}[2/4] Initializing Python Virtual Environment...${NC}"
# Use system site packages so we inherit pre-compiled opencv/numpy/pandas/flask
python3 -m venv --system-site-packages venv
echo -e "${GREEN}Virtual environment created in /home/darkninja/RaspAttendance/venv${NC}\n"

echo -e "${YELLOW}[3/4] Activating Virtual Environment & Installing Face Recognition...${NC}"
source venv/bin/activate

# Install face_recognition library inside the virtual environment
echo -e "${BLUE}Running pip install face-recognition...${NC}"
pip install --upgrade pip
pip install face-recognition

echo -e "${GREEN}Python dependencies compiled and installed successfully.${NC}\n"

echo -e "${YELLOW}[4/4] Setting Up Test Directories...${NC}"
# Setup default image paths for testing
mkdir -p /home/darkninja/RaspAttendance/Student_Images
echo -e "${GREEN}Local fallback test image directory created.${NC}\n"

echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}        SETUP COMPLETE - READY FOR TESTING           ${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo -e "To start testing, follow these commands:"
echo -e ""
echo -e "  1. Activate the environment:"
echo -e "     ${BLUE}source venv/bin/activate${NC}"
echo -e ""
echo -e "  2. Start the Data Sharing and Admin web server:"
echo -e "     ${BLUE}python share_logs.py${NC}"
echo -e "     (Open your browser and navigate to: http://localhost:5000)"
echo -e ""
echo -e "  3. (Optional) Start the recognition engine (requires webcam connected):"
echo -e "     ${BLUE}python attendance_engine.py${NC}"
echo -e ""
echo -e "${YELLOW}Note: You can test the Web Admin Portal dashboard immediately in your browser"
echo -e "even if you do not have a physical webcam or DS3231 RTC hardware connected.${NC}"
