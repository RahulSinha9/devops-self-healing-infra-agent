import os

# ─── Website & Server Settings ────────────────────────────────────────────────
WEBSITE_URL       = os.environ.get("WEBSITE_URL", "http://your-domain.com")
DOCKER_CONTAINER  = os.environ.get("DOCKER_CONTAINER", "your-container-name")
APACHE_LOG        = os.environ.get("APACHE_LOG", "/var/log/apache2/error.log")
SLACK_WEBHOOK     = os.environ.get("SLACK_WEBHOOK", "")

# ─── Free LLM API Keys (set via environment variables — never hardcode) ───────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")

# ─── Agent Behaviour ──────────────────────────────────────────────────────────
CHECK_INTERVAL        = int(os.environ.get("CHECK_INTERVAL", 60))   # seconds between checks
CONFIRM_FAILURES      = 2     # how many consecutive failures before acting
VERIFY_WAIT_SECONDS   = 15    # wait this long after fix before re-checking
MAX_FIX_ATTEMPTS      = 3     # give up and escalate after this many attempts
REQUEST_TIMEOUT       = 10    # HTTP timeout for website check

# ─── Startup validation ───────────────────────────────────────────────────────
if not GEMINI_API_KEY:
    raise EnvironmentError(
        "\n\n  GEMINI_API_KEY not set!\n"
        "  Run this on your server:\n"
        "    export GEMINI_API_KEY=your-key-here\n"
        "  Or add it to ~/.bashrc for persistence.\n"
    )
