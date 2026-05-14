"""
agent.py — Website Availability Agentic AI
==========================================
Monitors your website every 60 seconds.
When it goes down:
  1. Confirms it's really down (not a blip)
  2. Checks Docker container status
  3. Collects Docker + Apache2 logs
  4. Sends logs to Gemini/Groq for diagnosis
  5. Executes the suggested fix
  6. Verifies recovery
  7. Notifies Slack (or prints to console)
  8. Escalates if it cannot fix automatically

Usage:
  export GEMINI_API_KEY=your-key
  export WEBSITE_URL=http://your-domain.com
  export DOCKER_CONTAINER=your-container-name
  python3 agent.py
"""

import json
import time
import traceback
from datetime import datetime

from config import (
    WEBSITE_URL, DOCKER_CONTAINER, APACHE_LOG,
    CHECK_INTERVAL, CONFIRM_FAILURES,
    VERIFY_WAIT_SECONDS, MAX_FIX_ATTEMPTS
)
from tools import (
    check_website, get_docker_status, get_docker_logs,
    get_apache_logs, restart_container, start_container,
    run_fix_command, check_apache_config,
    get_disk_usage, get_memory_usage, send_slack
)
from llm import call_llm


# ─────────────────────────────────────────────────────────────────────────────
# LLM system prompt
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are an expert DevOps agent. Your job is to diagnose why a website is down and provide the exact shell command to fix it.

Server environment:
  - OS: Ubuntu (AWS EC2)
  - Website URL: {WEBSITE_URL}
  - Docker container name: {DOCKER_CONTAINER}
  - Reverse proxy: Apache2 with ProxyPass
  - Apache error log: {APACHE_LOG}

Common causes and their fixes:
  1. Container stopped/crashed       → docker start {DOCKER_CONTAINER}
  2. Container running but app down  → docker restart {DOCKER_CONTAINER}
  3. OOM (out of memory) kill        → docker restart {DOCKER_CONTAINER}
  4. Port already in use             → fuser -k PORT/tcp && docker start {DOCKER_CONTAINER}
  5. Apache proxy error (502/503)    → sudo systemctl reload apache2
  6. Apache config syntax error      → sudo apache2ctl configtest && sudo systemctl restart apache2
  7. Disk full                       → sudo journalctl --vacuum-size=100M && docker restart {DOCKER_CONTAINER}
  8. App crash loop                  → docker logs to find root cause, then docker restart
  9. Network issue (container)       → docker network inspect bridge && docker restart {DOCKER_CONTAINER}

You MUST respond ONLY with a valid JSON object and nothing else. No explanation outside the JSON. No markdown fences.

Required JSON format:
{{
  "diagnosis": "one sentence describing the root cause",
  "fix_command": "exact shell command to run (single line)",
  "confidence": "high | medium | low",
  "root_cause_category": "container_stopped | app_crash | oom | port_conflict | apache_error | disk_full | config_error | unknown",
  "explanation": "2-3 sentences explaining why this fix works"
}}

If you cannot determine the cause, use fix_command: "docker restart {DOCKER_CONTAINER}" with confidence: "low"."""


# ─────────────────────────────────────────────────────────────────────────────
# Core diagnosis function
# ─────────────────────────────────────────────────────────────────────────────
def diagnose_and_fix(error_info: str, attempt: int = 1) -> dict:
    """
    Full agent loop:
      collect context → ask LLM → execute fix → return outcome dict
    """
    print(f"\n{'='*55}")
    print(f"  AGENT ACTIVATED — attempt {attempt}/{MAX_FIX_ATTEMPTS}")
    print(f"{'='*55}")

    # ── Step 1: Docker status ────────────────────────────────────────────────
    print("\n[1/5] Checking Docker container status...")
    docker_status = get_docker_status(DOCKER_CONTAINER)
    print(f"      Status : {docker_status.get('status')}")
    print(f"      Running: {docker_status.get('running')}")
    print(f"      Restarts: {docker_status.get('restart_count', '?')}")
    print(f"      Exit code: {docker_status.get('exit_code', '?')}")

    # ── Step 2: Fast path — container not running ────────────────────────────
    if not docker_status.get("running"):
        status = docker_status.get("status", "unknown")
        print(f"\n  Container is '{status}'. Starting it directly...")

        if status in ("exited", "created", "dead"):
            result = start_container(DOCKER_CONTAINER)
        else:
            result = restart_container(DOCKER_CONTAINER)

        print(f"  Start result: {result}")
        time.sleep(VERIFY_WAIT_SECONDS)
        verify = check_website(WEBSITE_URL)

        return {
            "diagnosis":   f"Container was '{status}' — started it directly.",
            "fix_command": f"docker start {DOCKER_CONTAINER}",
            "confidence":  "high",
            "fix_result":  result,
            "recovered":   verify["up"],
            "verify":      verify,
            "llm_used":    False
        }

    # ── Step 3: Container running but site down — collect logs ───────────────
    print("\n[2/5] Container is running. Collecting logs...")
    docker_logs = get_docker_logs(DOCKER_CONTAINER, lines=100)
    apache_logs = get_apache_logs(APACHE_LOG, lines=60)
    disk_info   = get_disk_usage()
    mem_info    = get_memory_usage()

    print(f"      Docker logs: {len(docker_logs)} chars")
    print(f"      Apache logs: {len(apache_logs)} chars")

    # ── Step 4: Ask LLM ──────────────────────────────────────────────────────
    print("\n[3/5] Sending diagnostics to LLM...")

    user_prompt = f"""The website {WEBSITE_URL} is DOWN.

Initial HTTP error: {error_info}

=== Docker container status ===
{json.dumps(docker_status, indent=2)}

=== Docker logs (last 100 lines) ===
{docker_logs[-3000:]}

=== Apache2 error.log (last 60 lines) ===
{apache_logs[-2000:]}

=== Disk usage ===
{disk_info}

=== Memory usage ===
{mem_info}

Diagnose the root cause and return the JSON fix."""

    raw_response = call_llm(SYSTEM_PROMPT, user_prompt)

    # ── Step 5: Parse LLM response ───────────────────────────────────────────
    print("\n[4/5] Parsing LLM response...")
    print(f"      Raw: {raw_response[:400]}")

    diagnosis = _parse_llm_response(raw_response)
    print(f"\n  Diagnosis  : {diagnosis.get('diagnosis')}")
    print(f"  Fix command: {diagnosis.get('fix_command')}")
    print(f"  Confidence : {diagnosis.get('confidence')}")
    print(f"  Category   : {diagnosis.get('root_cause_category')}")

    # ── Step 6: Execute fix ───────────────────────────────────────────────────
    print("\n[5/5] Executing fix...")
    fix_cmd = diagnosis.get("fix_command", "").strip()
    fix_result = {"success": False, "output": "no command provided"}

    if fix_cmd:
        fix_result = run_fix_command(fix_cmd)
        print(f"      Success : {fix_result['success']}")
        print(f"      Output  : {fix_result.get('output', '')[:200]}")
    else:
        print("      No fix command provided by LLM.")

    # ── Step 7: Verify ────────────────────────────────────────────────────────
    print(f"\n  Waiting {VERIFY_WAIT_SECONDS}s before verifying...")
    time.sleep(VERIFY_WAIT_SECONDS)
    verify = check_website(WEBSITE_URL)
    print(f"  Verification: {'UP' if verify['up'] else 'STILL DOWN'}")

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
    """Parse JSON from LLM response, handling common formatting issues."""
    # Strip markdown code fences if model added them
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip().strip("```").strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        # Try to find JSON object inside the response
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(clean[start:end])
            except json.JSONDecodeError:
                pass

    # Fallback: safe default
    print("  [WARN] Could not parse LLM JSON. Using safe default.")
    return {
        "diagnosis":           "Could not parse LLM response — using safe default fix",
        "fix_command":         f"docker restart {DOCKER_CONTAINER}",
        "confidence":          "low",
        "root_cause_category": "unknown",
        "explanation":         "Defaulting to container restart as a safe recovery action."
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
    print(f"  URL       : {WEBSITE_URL}")
    print(f"  Container : {DOCKER_CONTAINER}")
    print(f"  Interval  : {CHECK_INTERVAL}s")
    print(f"  LLM       : Gemini 2.0 Flash → Groq fallback")
    print("=" * 55)
    print()

    consecutive_failures = 0
    agent_active         = False   # prevent re-triggering mid-fix
    last_was_down        = False
    outcome              = {}

    while True:
        try:
            now    = datetime.now().strftime("%H:%M:%S")
            result = check_website(WEBSITE_URL)

            # ── Site is UP ───────────────────────────────────────────────────
            if result["up"]:
                if last_was_down:
                    print(f"[{now}] Site is back UP on its own (no agent needed).")
                    last_was_down = False

                consecutive_failures = 0
                agent_active         = False
                print(f"[{now}] UP — {result.get('response_ms')}ms")

            # ── Site is DOWN ─────────────────────────────────────────────────
            else:
                consecutive_failures += 1
                last_was_down         = True
                error_msg             = result.get("error") or f"HTTP {result.get('status_code')}"
                print(f"[{now}] DOWN ({consecutive_failures}/{CONFIRM_FAILURES}) — {error_msg}")

                # Wait for CONFIRM_FAILURES consecutive failures (avoid blips)
                if consecutive_failures >= CONFIRM_FAILURES and not agent_active:
                    agent_active = True
                    _notify_down(error_msg)

                    fixed    = False
                    attempt  = 0
                    outcome  = {}

                    # Retry loop — try up to MAX_FIX_ATTEMPTS times
                    while not fixed and attempt < MAX_FIX_ATTEMPTS:
                        attempt += 1
                        try:
                            outcome = diagnose_and_fix(error_msg, attempt)
                        except Exception:
                            print(f"  [ERROR] Agent crashed on attempt {attempt}:")
                            traceback.print_exc()
                            outcome = {"recovered": False, "diagnosis": "agent error",
                                       "fix_command": ""}

                        if outcome.get("recovered"):
                            fixed = True
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
                        # Keep agent_active=True so we don't spam — reset after 10 min
                        time.sleep(600)
                        agent_active = False

        except KeyboardInterrupt:
            print("\n\n  Agent stopped by user (Ctrl+C).")
            break
        except Exception:
            print(f"  [MONITOR ERROR] Unexpected error in main loop:")
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    monitor()
