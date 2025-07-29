#!/bin/bash

set -e

echo "👤 Checking for 'keyence' user..."
if id "keyence" &>/dev/null; then
    echo "✅ 'keyence' user already exists."
else
    echo "🧑‍💻 Creating 'keyence' user..."
    sudo useradd -m -s /bin/bash keyence
    echo "🔑 Setting password to 'iv3pass'..."
    echo "keyence:iv3pass" | sudo chpasswd
fi

# --- PERMISSIONS ---
echo "🔒 Adding 'keyence' to system groups..."
sudo usermod -aG sudo,gpio,video,dialout,plugdev,i2c,spi keyence

# --- SYSTEM PACKAGES ---
echo "📦 Updating system and installing dependencies..."
sudo apt update && sudo apt install -y \
  python3-pip python3-tk libatlas-base-dev \
  git curl unzip

# --- PYTHON PACKAGES ---
echo "🐍 Installing Python dependencies..."
pip3 install openai python-dotenv pillow pymodbus

# --- FOLDER SETUP ---
echo "📁 Creating folders under /home/keyence/..."
sudo mkdir -p /home/keyence/{iv3_images,results,inspector}
sudo chown -R keyence:keyence /home/keyence

# --- PLACEHOLDER ENV FILE ---
if [ ! -f /home/keyence/inspector/.env ]; then
  echo "📝 Creating placeholder .env file..."
  echo "OPENAI_API_KEY=your-api-key-here" | sudo tee /home/keyence/inspector/.env >/dev/null
  sudo chown keyence:keyence /home/keyence/inspector/.env
fi

# --- MODBUS TCP PERMISSION ---
echo "🔐 Granting Python permission to use port 502..."
PYTHON_BIN=$(which python3)
sudo setcap 'cap_net_bind_service=+ep' "$PYTHON_BIN"

echo "✅ Setup complete!"
echo "🔁 You can now switch to the 'keyence' user and run the inspector:"
echo "    su - keyence"
echo "    bash /home/keyence/run_ui.sh"
