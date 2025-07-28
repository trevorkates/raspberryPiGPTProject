#!/bin/bash

# ----- SYSTEM PREP -----
echo "📦 Updating system and installing dependencies..."
sudo apt update && sudo apt install -y \
  python3-pip python3-tk libatlas-base-dev \
  git curl unzip

echo "📦 Installing Python packages..."
pip3 install openai python-dotenv pillow pymodbus

# ----- FOLDER SETUP -----
echo "📁 Creating project folders..."
mkdir -p /home/pi/iv3_images
mkdir -p /home/pi/inspector

# ----- PLACEHOLDER .env -----
if [ ! -f /home/pi/inspector/.env ]; then
  echo "📝 Creating .env placeholder (edit to add your API key)..."
  echo "OPENAI_API_KEY=your-api-key-here" > /home/pi/inspector/.env
fi

# ----- DOWNLOAD OR MOVE YOUR SCRIPT -----
echo "📄 Make sure your script is saved as /home/pi/inspector/watcher_ui.py"
echo "If not, copy or move it there before running."

# ----- OPTIONAL: GRANT MODBUS PORT ACCESS -----
echo "🔐 Allowing Python to use port 502 for Modbus..."
PYTHON_BIN=$(which python3)
sudo setcap 'cap_net_bind_service=+ep' "$PYTHON_BIN"

# ----- DONE -----
echo "✅ Setup complete. Add your OpenAI API key to /home/pi/inspector/.env"
echo "Then run: python3 /home/pi/inspector/watcher_ui.py"
