#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Website Availability Agent — Ubuntu Setup Script
# Run this once on your AWS Ubuntu instance:
#   chmod +x setup.sh && ./setup.sh
# ─────────────────────────────────────────────────────────────────

set -e
AGENT_DIR="$HOME/website-agent"

echo "======================================================"
echo "  Website Availability Agent — Setup"
echo "======================================================"

# ── 1. Install Python requests ────────────────────────────────
echo ""
echo "[1/4] Installing dependencies..."
pip3 install requests --quiet
echo "      Done."

# ── 2. Prompt for config values ───────────────────────────────
echo ""
echo "[2/4] Configuration"
echo "------------------------------------------------------"

read -p "  Your website URL (e.g. http://example.com): " WEBSITE_URL
read -p "  Docker container name (run 'docker ps' to check): " DOCKER_CONTAINER
read -p "  Gemini API key (from aistudio.google.com): " GEMINI_API_KEY
read -p "  Groq API key (optional, press Enter to skip): " GROQ_API_KEY
read -p "  Slack webhook URL (optional, press Enter to skip): " SLACK_WEBHOOK

# ── 3. Write environment variables ────────────────────────────
echo ""
echo "[3/4] Saving environment variables..."

ENV_FILE="$HOME/.website-agent-env"
cat > "$ENV_FILE" << EOF
export WEBSITE_URL="$WEBSITE_URL"
export DOCKER_CONTAINER="$DOCKER_CONTAINER"
export GEMINI_API_KEY="$GEMINI_API_KEY"
export GROQ_API_KEY="$GROQ_API_KEY"
export SLACK_WEBHOOK="$SLACK_WEBHOOK"
EOF
chmod 600 "$ENV_FILE"

# Source it in bashrc if not already there
if ! grep -q "website-agent-env" "$HOME/.bashrc"; then
    echo "source $ENV_FILE" >> "$HOME/.bashrc"
fi
source "$ENV_FILE"
echo "      Saved to $ENV_FILE (chmod 600 — only you can read it)"

# ── 4. Create systemd service ─────────────────────────────────
echo ""
echo "[4/4] Creating systemd service..."

PYTHON_PATH=$(which python3)

sudo tee /etc/systemd/system/website-agent.service > /dev/null << EOF
[Unit]
Description=Website Availability Agentic AI
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$AGENT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$PYTHON_PATH $AGENT_DIR/agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable website-agent
echo "      Service created."

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "======================================================"
echo "  Setup complete!"
echo "======================================================"
echo ""
echo "  To start the agent now:"
echo "    sudo systemctl start website-agent"
echo ""
echo "  To watch live logs:"
echo "    sudo journalctl -u website-agent -f"
echo ""
echo "  To stop:"
echo "    sudo systemctl stop website-agent"
echo ""
echo "  To test manually (without systemd):"
echo "    source ~/.website-agent-env && python3 agent.py"
echo "======================================================"
