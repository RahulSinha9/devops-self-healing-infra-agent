"""
agent.py — Website Availability Agentic AI
==========================================
Monitors your website every 60 seconds.
Handles both app container and database container failures.

When site goes down:
  1. Confirms it is really down (not a blip)
  2. Checks ALL containers (app + db)
  3. If DB is down → restarts DB first, waits, then checks app
  4. If app is down → restarts app container
  5. If both running → collects logs and asks LLM to diagnose
  6. Executes the suggested fix
  7. Verifies recovery
  8. Notifies Slack or prints to console
  9. Escalates if cannot fix automatically

Usage:
  export GROQ_API_KEY=gsk_your_key
  export WEBSITE_URL=https://devopslabx.com
  export DOCKER_CONTAINER=devopslabx-app
  export DB_CONTAINER=devopslabx-db
  export COMPOSE_DIR=/home/ubuntu/DevOpsLabX-New
  python3 agent.py
"""

import json
import os
import time
import traceback
import subprocess
from datetime import datetime

from config import (
    WEBSITE_URL, DOCKER_CONTAINER, APACHE_LOG,
    CHECK_INTERVAL, CONFIRM_FAILURES,
    VERIFY_WAIT_SECONDS, MAX_FIX_ATTEMPTS
)
from tools import (
    check_website, get_docker_status, get_docker_logs,
    get_apache_logs, restart_container, start_container,
    run_fix_command, get_disk_usage, get_memory_usage, send_slack
)
from llm import call_llm

# ─── Extra config from environment ───────────────────────────────────────────
DB_CONTAINER = os.environ.get("DB_CONTAINER", "devopslabx-db")
COMPOSE_DIR  = os.environ.get("COMPOSE_DIR", "/home/ubuntu/DevOpsLabX-New")


# ─────────────────────────────────────────────────────────────────────────────
# Helper — get all containers status
# ─────────────────────────────────────────────────────────────────────────────
def get_all_containers() -> dict:
    """Returns status of all running/stopped containers."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"],
            capture_output=True, text=True, timeout=10
        )
        containers = {}
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                parts = line.split("|")
                if len(parts) == 3:
                    containers[parts[0]] = {
                        "status":  parts[1],
                        "image":   parts[2],
                        "running": parts[1].startswith("Up")
                    }
        return containers
    except Exception as e:
        return {"error": str(e)}


def restart_with_compose(compose_dir: str = COMPOSE_DIR) -> dict:
    """Restart all services using docker compose up -d."""
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            capture_output=True, text=True,
            cwd=compose_dir, timeout=60
        )
        return {
            "success": result.returncode == 0,
            "output":  (result.stdout + result.stderr).strip()
        }
    except Exception as e:
        return {"success": False, "output": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# LLM system prompt
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are an expert DevOps agent. Your job is to diagnose why a website is down and provide the exact shell command to fix it.

Server environment:
  - OS: Ubuntu (AWS EC2)
  - Website URL: {WEBSITE_URL}
  - App container: {DOCKER_CONTAINER} (Next.js on port 3000)
  - DB container: {DB_CONTAINER} (MariaDB on port 3306)
  - Reverse proxy: Apache2 with ProxyPass
  - Apache error log: {APACHE_LOG}
  - Docker Compose directory: {COMPOSE_DIR}

Common causes and their fixes:
  1. App container stopped              -> docker start {DOCKER_CONTAINER}
  2. App container crashed/OOM          -> docker restart {DOCKER_CONTAINER}
  3. DB container stopped               -> docker start {DB_CONTAINER}
  4. DB container crashed               -> docker restart {DB_CONTAINER}
  5. Both containers down               -> cd {COMPOSE_DIR} && docker compose up -d
  6. DB connection refused from app     -> docker restart {DB_CONTAINER} && sleep 10 && docker restart {DOCKER_CONTAINER}
  7. Port 3000 already in use           -> fuser -k 3000/tcp && docker start {DOCKER_CONTAINER}
  8. Apache proxy error (502/503)       -> sudo systemctl reload apache2
  9. Apache config syntax error         -> sudo apache2ctl configtest && sudo systemctl restart apache2
  10. Disk full                         -> sudo journalctl --vacuum-size=100M && docker restart {DOCKER_CONTAINER}
  11. App crash loop                    -> docker restart {DOCKER_CONTAINER}
  12. DB auth error                     -> docker restart {DB_CONTAINER}

You MUST respond ONLY with a valid JSON object. No text outside the JSON. No markdown fences.

Required JSON format:
{{
  "diagnosis": "one sentence describing the root cause",
  "fix_command": "exact single-line shell command to fix it",
  "confidence": "high | medium | low",
  "root_cause_category": "app_container | db_container | both_containers | apache_error | port_conflict | disk_full | oom | db_connection | config_error | unknown",
  "explanation": "2-3 sentences explaining why this fix works"
}}

If you cannot determine cause, use:
  fix_command: "cd {COMPOSE_DIR} && docker compose up -d"
  confidence: "low"
"""


# ─────────────────────────────────────────────────────────────────────────────
# Core diagnosis function
# ─────────────────────────────────────────────────────────────────────────────
def diagnose_and_fix(error_info: str, attempt: int = 1) -> dict:
    print(f"\n{'='*55}")
    print(f"  AGENT ACTIVATED — attempt {attempt}/{MAX_FIX_ATTEMPTS}")
    print(f"{'='*55}")

    # ── Step 1: Check ALL containers ─────────────────────────────────────────
    print("\n[1/6] Checking all containers...")
    all_containers = get_all_containers()
    app_status     = get_docker_status(DOCKER_CONTAINER)
    db_status      = get_docker_status(DB_CONTAINER)

    app_running = app_status.get("running", False)
    db_running  = db_status.get("running",  False)

    print(f"      App ({DOCKER_CONTAINER}): {app_status.get('status')} | running={app_running} | restarts={app_status.get('restart_count')} | exit={app_status.get('exit_code')}")
    print(f"      DB  ({DB_CONTAINER}): {db_status.get('status')} | running={db_running} | restarts={db_status.get('restart_count')} | exit={db_status.get('exit_code')}")

    # ── Step 2: Fast path — BOTH containers down ──────────────────────────────
    if not app_running and not db_running:
        print("\n  Both containers are down! Restarting with docker compose...")
        result = restart_with_compose(COMPOSE_DIR)
        print(f"  Compose result: {result}")
        time.sleep(VERIFY_WAIT_SECONDS)
        verify = check_website(WEBSITE_URL)
        return {
            "diagnosis":   "Both app and database containers were down.",
            "fix_command": f"cd {COMPOSE_DIR} && docker compose up -d",
            "confidence":  "high",
            "category":    "both_containers",
            "fix_result":  result,
            "recovered":   verify["up"],
            "verify":      verify,
            "llm_used":    False
        }

    # ── Step 3: Fast path — DB container down ────────────────────────────────
    if not db_running:
        print(f"\n  DB container '{DB_CONTAINER}' is down! Starting it first...")
        db_result = start_container(DB_CONTAINER)
        print(f"  DB start result: {db_result}")
        print(f"  Waiting 15s for DB to be ready...")
        time.sleep(15)
        print(f"  Restarting app to reconnect to DB...")
        app_result = restart_container(DOCKER_CONTAINER)
        print(f"  App restart result: {app_result}")
        time.sleep(VERIFY_WAIT_SECONDS)
        verify = check_website(WEBSITE_URL)
        return {
            "diagnosis":   f"Database container '{DB_CONTAINER}' was down — app lost DB connection.",
            "fix_command": f"docker start {DB_CONTAINER} && sleep 15 && docker restart {DOCKER_CONTAINER}",
            "confidence":  "high",
            "category":    "db_container",
            "fix_result":  {"db": db_result, "app": app_result},
            "recovered":   verify["up"],
            "verify":      verify,
            "llm_used":    False
        }

    # ── Step 4: Fast path — App container down ────────────────────────────────
    if not app_running:
        print(f"\n  App container '{DOCKER_CONTAINER}' is down! Starting it...")
        status = app_status.get("status", "unknown")
        if status in ("exited", "created", "dead"):
            result = start_container(DOCKER_CONTAINER)
        else:
            result = restart_container(DOCKER_CONTAINER)
        print(f"  App start result: {result}")
        time.sleep(VERIFY_WAIT_SECONDS)
        verify = check_website(WEBSITE_URL)
        return {
            "diagnosis":   f"App container '{DOCKER_CONTAINER}' was '{status}'.",
            "fix_command": f"docker start {DOCKER_CONTAINER}",
            "confidence":  "high",
            "category":    "app_container",
            "fix_result":  result,
            "recovered":   verify["up"],
            "verify":      verify,
            "llm_used":    False
        }

    # ── Step 5: Both running but site down — collect logs ─────────────────────
    print("\n[2/6] Both containers running. Collecting logs...")
    app_logs    = get_docker_logs(DOCKER_CONTAINER, lines=100)
    db_logs     = get_docker_logs(DB_CONTAINER, lines=50)
    apache_logs = get_apache_logs(APACHE_LOG, lines=60)
    disk_info   = get_disk_usage()
    mem_info    = get_memory_usage()

    print(f"      App logs : {len(app_logs)} chars")
    print(f"      DB logs  : {len(db_logs)} chars")
    print(f"      Apache   : {len(apache_logs)} chars")

    # ── Step 6: Ask LLM ───────────────────────────────────────────────────────
    print("\n[3/6] Sending diagnostics to LLM...")
    user_prompt = f"""Website {WEBSITE_URL} is DOWN.
Initial error: {error_info}

=== All containers ===
{json.dumps(all_containers, indent=2)}

=== App container status ===
{json.dumps(app_status, indent=2)}

=== DB container status ===
{json.dumps(db_status, indent=2)}

=== App Docker logs (last 100 lines) ===
{app_logs[-3000:]}

=== DB Docker logs (last 50 lines) ===
{db_logs[-1500:]}

=== Apache2 error.log (last 60 lines) ===
{apache_logs[-2000:]}

=== Disk usage ===
{disk_info}

=== Memory usage ===
{mem_info}

Both containers are running but site returns: {error_info}
Diagnose and return JSON fix."""

    raw_response = call_llm(SYSTEM_PROMPT, user_prompt)

    # ── Step 7: Parse LLM response ────────────────────────────────────────────
    print("\n[4/6] Parsing LLM response...")
    print(f"      Raw: {raw_response[:400]}")

    diagnosis = _parse_llm_response(raw_response)
    print(f"\n  Diagnosis  : {diagnosis.get('diagnosis')}")
    print(f"  Fix command: {diagnosis.get('fix_command')}")
    print(f"  Confidence : {diagnosis.get('confidence')}")
    print(f"  Category   : {diagnosis.get('root_cause_category')}")

    # ── Step 8: Execute fix ───────────────────────────────────────────────────
    print("\n[5/6] Executing fix...")
    fix_cmd    = diagnosis.get("fix_command", "").strip()
    fix_result = {"success": False, "output": "no command provided"}

    if fix_cmd:
        fix_result = run_fix_command(fix_cmd)
        print(f"      Success : {fix_result['success']}")
        print(f"      Output  : {fix_result.get('output', '')[:200]}")
    else:
        print("      No fix command from LLM.")

    # ── Step 9: Verify ────────────────────────────────────────────────────────
    print(f"\n[6/6] Waiting {VERIFY_WAIT_SECONDS}s then verifying...")
    time.sleep(VERIFY_WAIT_SECONDS)
    verify = check_website(WEBSITE_URL)
    print(f"      Result: {'UP' if verify['up'] else 'STILL DOWN'}")

    return {
        "diagnosis":   diagnosis.get("diagnosis", "unknown"),
        "fix_command": fix_cmd,
        "confidence":  diagnosis.get("confidence", "low"),
        "category":    diagnosis.get("root_cause_category", "unknown"),
        "explanation": diagnosis.get("explanation", ""),
        "fix_result":  fix_result,
        "recovered":   verify["up"],
        "verify":      verify,
        "llm_used":    True
    }


def _parse_llm_response(raw: str) -> dict:
    """Parse JSON from LLM response, handles markdown fences."""
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip().strip("```").strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(clean[start:end])
            except json.JSONDecodeError:
                pass

    print("  [WARN] Could not parse LLM JSON. Using safe default.")
    return {
        "diagnosis":           "Could not parse LLM response — using safe default",
        "fix_command":         f"cd {COMPOSE_DIR} && docker compose up -d",
        "confidence":          "low",
        "root_cause_category": "unknown",
        "explanation":         "Defaulting to docker compose up as safe recovery."
    }


# ─────────────────────────────────────────────────────────────────────────────
# Notification helpers
# ─────────────────────────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _notify_down(error: str):
    send_slack(
        f":red_circle: *Website DOWN* — `{WEBSITE_URL}`\n"
        f"Error: `{error}`\n"
        f"Time: {_ts()}\n"
        f"Agent is investigating..."
    )


def _notify_recovered(outcome: dict, attempt: int):
    send_slack(
        f":green_circle: *Website RECOVERED* — `{WEBSITE_URL}`\n"
        f"*Root cause:* {outcome.get('diagnosis')}\n"
        f"*Category:* {outcome.get('category')}\n"
        f"*Fix applied:* `{outcome.get('fix_command')}`\n"
        f"*Confidence:* {outcome.get('confidence')}\n"
        f"*Attempts:* {attempt}\n"
        f"*Time:* {_ts()}"
    )


def _notify_escalate(outcome: dict, attempt: int):
    send_slack(
        f":sos: *Website STILL DOWN — manual intervention needed*\n"
        f"URL: `{WEBSITE_URL}`\n"
        f"Last diagnosis: {outcome.get('diagnosis')}\n"
        f"Last fix tried: `{outcome.get('fix_command')}`\n"
        f"Attempts made: {attempt}\n"
        f"Time: {_ts()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main monitor loop
# ─────────────────────────────────────────────────────────────────────────────
def monitor():
    print("=" * 55)
    print("  Website Availability Agent")
    print("=" * 55)
    print(f"  URL        : {WEBSITE_URL}")
    print(f"  App        : {DOCKER_CONTAINER}")
    print(f"  Database   : {DB_CONTAINER}")
    print(f"  Compose    : {COMPOSE_DIR}")
    print(f"  Interval   : {CHECK_INTERVAL}s")
    print(f"  LLM        : Gemini 2.0 Flash -> Groq fallback")
    print("=" * 55)
    print()

    consecutive_failures = 0
    agent_active         = False
    last_was_down        = False
    outcome              = {}

    while True:
        try:
            now    = datetime.now().strftime("%H:%M:%S")
            result = check_website(WEBSITE_URL)

            if result["up"]:
                if last_was_down:
                    print(f"[{now}] Site is back UP.")
                    last_was_down = False
                consecutive_failures = 0
                agent_active         = False
                print(f"[{now}] UP — {result.get('response_ms')}ms")

            else:
                consecutive_failures += 1
                last_was_down         = True
                error_msg             = result.get("error") or f"HTTP {result.get('status_code')}"
                print(f"[{now}] DOWN ({consecutive_failures}/{CONFIRM_FAILURES}) — {error_msg}")

                if consecutive_failures >= CONFIRM_FAILURES and not agent_active:
                    agent_active = True
                    _notify_down(error_msg)

                    fixed   = False
                    attempt = 0
                    outcome = {}

                    while not fixed and attempt < MAX_FIX_ATTEMPTS:
                        attempt += 1
                        try:
                            outcome = diagnose_and_fix(error_msg, attempt)
                        except Exception:
                            print(f"  [ERROR] Agent crashed on attempt {attempt}:")
                            traceback.print_exc()
                            outcome = {
                                "recovered":   False,
                                "diagnosis":   "agent error",
                                "fix_command": "",
                                "category":    "unknown"
                            }

                        if outcome.get("recovered"):
                            fixed                = True
                            consecutive_failures = 0
                            agent_active         = False
                            print(f"\n  Site is back UP after {attempt} attempt(s).")
                            _notify_recovered(outcome, attempt)
                        else:
                            if attempt < MAX_FIX_ATTEMPTS:
                                print(f"\n  Fix did not work. Waiting 30s before attempt {attempt+1}...")
                                time.sleep(30)

                    if not fixed:
                        print(f"\n  Could not fix after {MAX_FIX_ATTEMPTS} attempts. Escalating.")
                        _notify_escalate(outcome, attempt)
                        time.sleep(600)
                        agent_active = False

        except KeyboardInterrupt:
            print("\n\n  Agent stopped (Ctrl+C).")
            break
        except Exception:
            print("  [MONITOR ERROR]")
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    monitor()
