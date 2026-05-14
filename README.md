# DevOps Self-Healing Infrastructure Agent

An agentic AI that monitors your website 24/7, diagnoses failures from Docker and Apache2 logs using a free LLM, and auto-heals without human intervention.

> Your website goes down at 3 AM — this agent fixes it before you wake up.

---

## Architecture

```
Website Check (every 60s)
        │
        ▼
   Site DOWN?
        │
        ▼
Check All Containers
  ├── Both down     → docker compose up -d
  ├── DB down       → start DB → restart App
  ├── App down      → start App
  └── Both running  → collect logs → ask LLM → execute fix
        │
        ▼
  Verify Recovery
  ├── Fixed   → notify Slack
  └── Failed  → retry (max 3x) → escalate
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3 |
| Primary LLM | Google Gemini 2.0 Flash (free) |
| Fallback LLM | Groq Llama 3.3 70B (free) |
| App Container | Docker |
| DB Container | Docker |
| Reverse Proxy | Apache2 |
| Process Manager | PM2 |
| Cloud | AWS EC2 Ubuntu |

---

## Project Structure

```
devops-self-healing-infra-agent/
├── agent.py            ← main monitor loop + agentic diagnosis
├── tools.py            ← docker, apache2, system actions
├── llm.py              ← Gemini + Groq client with fallback
├── config.py           ← settings (reads from env variables)
├── requirements.txt    ← Python dependencies
└── README.md
```

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/your-username/devops-self-healing-infra-agent.git
cd devops-self-healing-infra-agent
```

### 2. Install dependencies

```bash
pip3 install requests --break-system-packages
npm install -g pm2
```

### 3. Get free API keys

| Provider | Link | Free Limit |
|---|---|---|
| Google Gemini | https://aistudio.google.com/apikey | 1,500 req/day |
| Groq | https://console.groq.com | 14,400 req/day |

### 4. Set environment variables

```bash
export WEBSITE_URL=https://your-domain.com
export DOCKER_CONTAINER=your-app-container
export DB_CONTAINER=your-db-container
export COMPOSE_DIR=/path/to/your/docker-compose
export APACHE_LOG=/var/log/apache2/error.log
export GEMINI_API_KEY=your-gemini-key-here
export GROQ_API_KEY=your-groq-key-here
```

To make them permanent:
```bash
echo 'export WEBSITE_URL=https://your-domain.com' >> ~/.bashrc
echo 'export DOCKER_CONTAINER=your-app-container' >> ~/.bashrc
echo 'export DB_CONTAINER=your-db-container' >> ~/.bashrc
echo 'export COMPOSE_DIR=/path/to/your/docker-compose' >> ~/.bashrc
echo 'export APACHE_LOG=/var/log/apache2/error.log' >> ~/.bashrc
echo 'export GEMINI_API_KEY=your-gemini-key-here' >> ~/.bashrc
echo 'export GROQ_API_KEY=your-groq-key-here' >> ~/.bashrc
source ~/.bashrc
```

### 5. Start with PM2

```bash
pm2 start agent.py --name "website-agent" --interpreter python3
pm2 save
pm2 startup
```

---

## What Happens During an Incident

```
[03:14:22] UP  — 243ms
[03:14:52] DOWN (1/2) — Connection refused
[03:15:22] DOWN (2/2) — Connection refused

[NOTIFY] Website DOWN — agent investigating...

=======================================================
  AGENT ACTIVATED — attempt 1/3
=======================================================

[1/6] Checking all containers...
      App (your-app-container): exited | running=False
      DB  (your-db-container):  running | running=True

  App container is 'exited'. Starting it...
  Start result: {'success': True}

[6/6] Waiting 15s then verifying...
      Result: UP

  Site is back UP after 1 attempt(s).
[NOTIFY] RECOVERED — docker start your-app-container
```

---

## How the Agent Thinks

### Fast path (no LLM needed)
| Situation | Action |
|---|---|
| Both containers down | `docker compose up -d` |
| DB container down | Start DB → wait 15s → restart App |
| App container down | `docker start your-app-container` |

### LLM path (both running but site down)
1. Collects Docker logs (last 100 lines)
2. Collects Apache2 error.log (last 60 lines)
3. Checks disk usage and memory
4. Sends everything to Gemini / Groq
5. LLM returns exact fix command as JSON
6. Agent executes the command
7. Verifies site is back up

### LLM response format
```json
{
  "diagnosis": "App running out of memory — OOM kill",
  "fix_command": "docker restart your-app-container",
  "confidence": "high",
  "root_cause_category": "oom",
  "explanation": "Container exceeded memory limits and was killed by the OOM killer."
}
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `WEBSITE_URL` | Yes | — | Full URL of your website |
| `DOCKER_CONTAINER` | Yes | — | App container name (`docker ps`) |
| `DB_CONTAINER` | No | — | Database container name |
| `COMPOSE_DIR` | No | — | Path to docker-compose directory |
| `APACHE_LOG` | No | /var/log/apache2/error.log | Apache2 error log path |
| `GEMINI_API_KEY` | Yes* | — | From aistudio.google.com |
| `GROQ_API_KEY` | No | — | From console.groq.com (fallback) |
| `SLACK_WEBHOOK` | No | — | Slack incoming webhook URL |
| `CHECK_INTERVAL` | No | 60 | Seconds between health checks |

*Groq key can be used as primary if Gemini quota is exhausted.

---

## PM2 Commands

```bash
pm2 status                         # check if agent is running
pm2 logs website-agent             # live logs
pm2 logs website-agent --lines 50  # last 50 lines
pm2 restart website-agent          # restart agent
pm2 stop website-agent             # stop agent
pm2 delete website-agent           # remove from pm2
pm2 monit                          # live dashboard
```

---

## Testing the Agent

### Test 1 — Stop app container
```bash
docker stop your-app-container
# Agent detects → starts container → verifies → recovered
```

### Test 2 — Stop database container
```bash
docker stop your-db-container
# Agent detects → starts DB → waits 15s → restarts app → recovered
```

### Test 3 — Stop both containers
```bash
docker compose down
# Agent detects → docker compose up -d → recovered
```

---

## Security Best Practices

- Never hardcode API keys in any file
- Store keys as environment variables only
- Add `.env` to `.gitignore`
- Use `chmod 600` on any file containing secrets
- Rotate API keys regularly

```bash
echo ".env" >> .gitignore
echo "__pycache__/" >> .gitignore
echo "*.pyc" >> .gitignore
```

---

## Free LLM Limits

| Provider | Model | Free Limit | Speed |
|---|---|---|---|
| Google Gemini | gemini-2.0-flash | 1,500 req/day | Fast |
| Groq | llama-3.3-70b | 14,400 req/day | Very fast |

Since the LLM is only called when the site goes down (not every 60s), you will rarely hit rate limits in normal operation.

---

## Agentic AI Concepts Used

| Concept | Where Used |
|---|---|
| Tool use | docker, apache2, bash commands |
| Multi-step reasoning | check containers → collect logs → diagnose → fix → verify |
| Conditional planning | different fix path based on which container is down |
| Reflection | verify fix worked, retry if not |
| Human-in-the-loop | escalate to human after 3 failed attempts |
| Fallback chain | Gemini → Groq → safe default command |

---

## License

MIT License — free to use, modify, and distribute.
