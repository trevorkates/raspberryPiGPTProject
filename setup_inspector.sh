#!/bin/bash

set -e

echo "👤 Creating 'keyence' user if needed..."
if id "keyence" &>/dev/null; then
    echo "✅ User 'keyence' already exists."
else
    sudo useradd -m -s /bin/bash keyence
    echo "keyence:iv3pass" | sudo chpasswd
    echo "🔑 Set password to 'iv3pass'"
fi

echo "🔒 Adding 'keyence' to system groups..."
sudo usermod -aG sudo,gpio,video,dialout,plugdev,i2c,spi keyence

echo "📦 Installing system packages..."
sudo apt update && sudo apt install -y \
    python3-pip python3-tk libatlas-base-dev \
    git curl unzip

echo "🐍 Installing Python packages..."
pip3 install openai python-dotenv pillow pymodbus

echo "📁 Cloning GitHub repo into /home/keyence/inspector..."
sudo -u keyence git clone https://github.com/trevorkates/raspberryPiGPTProject.git /home/keyence/inspector

echo "🔁 Renaming watcher_ui.py for consistency (if needed)..."
sudo -u keyence mv /home/keyence/inspector/watcher_ui.py /home/keyence/inspector/watcher_ui.py 2>/dev/null || true

echo "🧹 Ensuring image and result folders exist..."
sudo mkdir -p /home/keyence/{iv3_images,results}
sudo chown -R keyence:keyence /home/keyence/

echo "🧠 Creating placeholder .env if missing..."
if [ ! -f /home/keyence/inspector/.env ]; then
  echo "OPENAI_API_KEY=your-api-key-here" | sudo tee /home/keyence/inspector/.env >/dev/null
  sudo chown keyence:keyence /home/keyence/inspector/.env
fi

echo "🖥️ Setting up desktop autostart..."
AUTOSTART_DIR="/home/keyence/.config/autostart"
sudo -u keyence mkdir -p "$AUTOSTART_DIR"
sudo -u keyence cp /home/keyence/inspector/cm1_lid_inspector.desktop "$AUTOSTART_DIR/"

echo "🔐 Granting Modbus port 502 access to Python..."
PYTHON_BIN=$(which python3)
sudo setcap 'cap_net_bind_service=+ep' "$PYTHON_BIN"

echo "✅ Setup complete!"
echo "🚪 You can now switch to the 'keyence' user and restart or run manually:"
echo "    su - keyence"
echo "    bash /home/keyence/run_ui.sh"
