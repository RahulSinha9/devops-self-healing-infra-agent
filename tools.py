"""
tools.py — every action the agent can perform on the Ubuntu server.
All functions return dicts or strings so results can be fed directly to the LLM.
"""

import subprocess
import requests
import json
import os
import time

from config import (
    WEBSITE_URL, DOCKER_CONTAINER, APACHE_LOG, REQUEST_TIMEOUT
)

# ─── Blocked commands (safety guard) ─────────────────────────────────────────
BLOCKED_PATTERNS = [
    "rm -rf /", "mkfs", "dd if=", "shutdown", "reboot",
    "> /dev/sda", ":(){ :|:& };:", "chmod -R 777 /",
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Website health check
# ─────────────────────────────────────────────────────────────────────────────
def check_website(url: str = WEBSITE_URL) -> dict:
    """
    HTTP GET the website.
    Returns: { up, status_code, response_ms, error }
    """
    try:
        start = time.time()
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        ms = int((time.time() - start) * 1000)
        return {
            "up": r.status_code == 200,
            "status_code": r.status_code,
            "response_ms": ms,
            "error": None
        }
    except requests.exceptions.ConnectionError:
        return {"up": False, "status_code": None,
                "response_ms": None, "error": "Connection refused — server not responding"}
    except requests.exceptions.Timeout:
        return {"up": False, "status_code": None,
                "response_ms": None, "error": f"Timeout after {REQUEST_TIMEOUT}s"}
    except Exception as e:
        return {"up": False, "status_code": None,
                "response_ms": None, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Docker tools
# ─────────────────────────────────────────────────────────────────────────────
def get_docker_status(container: str = DOCKER_CONTAINER) -> dict:
    """
    Returns: { container, status, running, restart_count, exit_code }
    """
    try:
        fmt = "{{.State.Status}}|{{.RestartCount}}|{{.State.ExitCode}}"
        result = subprocess.run(
            ["docker", "inspect", "--format", fmt, container],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"container": container, "status": "not_found",
                    "running": False, "error": result.stderr.strip()}

        parts = result.stdout.strip().split("|")
        status = parts[0] if len(parts) > 0 else "unknown"
        return {
            "container":     container,
            "status":        status,
            "running":       status == "running",
            "restart_count": parts[1] if len(parts) > 1 else "?",
            "exit_code":     parts[2] if len(parts) > 2 else "?",
        }
    except Exception as e:
        return {"container": container, "status": "error",
                "running": False, "error": str(e)}


def get_docker_logs(container: str = DOCKER_CONTAINER, lines: int = 100) -> str:
    """Fetch last N lines from docker logs (stdout + stderr)."""
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), container],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no logs found)"
    except Exception as e:
        return f"Error fetching docker logs: {e}"


def restart_container(container: str = DOCKER_CONTAINER) -> dict:
    """docker restart — sends SIGTERM, waits 10s, then SIGKILL."""
    try:
        result = subprocess.run(
            ["docker", "restart", container],
            capture_output=True, text=True, timeout=30
        )
        return {
            "success": result.returncode == 0,
            "output":  (result.stdout + result.stderr).strip()
        }
    except Exception as e:
        return {"success": False, "output": str(e)}


def start_container(container: str = DOCKER_CONTAINER) -> dict:
    """docker start — use when container is fully stopped."""
    try:
        result = subprocess.run(
            ["docker", "start", container],
            capture_output=True, text=True, timeout=20
        )
        return {
            "success": result.returncode == 0,
            "output":  (result.stdout + result.stderr).strip()
        }
    except Exception as e:
        return {"success": False, "output": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Apache2 tools
# ─────────────────────────────────────────────────────────────────────────────
def get_apache_logs(log_path: str = APACHE_LOG, lines: int = 50) -> str:
    """Fetch last N lines of Apache2 error.log."""
    try:
        if not os.path.exists(log_path):
            return f"Log file not found: {log_path}"
        result = subprocess.run(
            ["tail", "-n", str(lines), log_path],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.stdout.strip() else "(log is empty)"
    except Exception as e:
        return f"Error reading apache log: {e}"


def check_apache_config() -> dict:
    """Run apache2ctl configtest and return result."""
    try:
        result = subprocess.run(
            ["sudo", "apache2ctl", "configtest"],
            capture_output=True, text=True, timeout=15
        )
        return {
            "valid":  result.returncode == 0,
            "output": (result.stdout + result.stderr).strip()
        }
    except Exception as e:
        return {"valid": False, "output": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 4. System tools
# ─────────────────────────────────────────────────────────────────────────────
def get_disk_usage() -> str:
    """df -h / — check if disk is full."""
    try:
        result = subprocess.run(
            ["df", "-h", "/"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def get_memory_usage() -> str:
    """free -h — check available memory."""
    try:
        result = subprocess.run(
            ["free", "-h"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {e}"


def get_port_usage(port: int = 80) -> str:
    """Check what process is using a port (ss -tlnp)."""
    try:
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or f"Nothing listening on port {port}"
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Fix executor (with safety guard)
# ─────────────────────────────────────────────────────────────────────────────
def run_fix_command(command: str) -> dict:
    """
    Execute a shell command suggested by the LLM.
    Blocks known dangerous commands before running.
    """
    # Safety check
    for pattern in BLOCKED_PATTERNS:
        if pattern in command:
            return {
                "success": False,
                "output":  f"BLOCKED: command contains unsafe pattern '{pattern}'",
                "blocked": True
            }

    try:
        result = subprocess.run(
            command, shell=True,
            capture_output=True, text=True, timeout=30
        )
        return {
            "success":     result.returncode == 0,
            "output":      (result.stdout + result.stderr).strip(),
            "returncode":  result.returncode,
            "blocked":     False
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "Command timed out after 30s", "blocked": False}
    except Exception as e:
        return {"success": False, "output": str(e), "blocked": False}


# ─────────────────────────────────────────────────────────────────────────────
# 6. Slack notifier
# ─────────────────────────────────────────────────────────────────────────────
def send_slack(message: str, webhook: str = "") -> bool:
    """Post a message to Slack webhook. Prints to console if no webhook set."""
    from config import SLACK_WEBHOOK as cfg_webhook
    hook = webhook or cfg_webhook
    if not hook or not hook.startswith("http"):
        print(f"[NOTIFY] {message}")
        return False
    try:
        r = requests.post(hook, json={"text": message}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False
