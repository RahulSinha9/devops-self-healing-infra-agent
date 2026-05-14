# Website Availability Agentic AI

Monitors your website every 60 seconds. When it goes down, it automatically investigates Docker and Apache2 logs, asks an LLM to diagnose the root cause, executes the fix, verifies recovery, and notifies you on Slack.

## Free LLMs used
- **Gemini 2.0 Flash** (primary) — 1,500 req/day, no credit card
- **Groq Llama 3.3 70B** (fallback) — fast, free tier

## Files

```
website-agent/
├── agent.py          ← main loop + agentic diagnosis
├── tools.py          ← docker, apache2, system actions
├── llm.py            ← Gemini + Groq client with fallback
├── config.py         ← settings (reads from env vars)
├── setup.sh          ← one-command Ubuntu setup
└── requirements.txt
```

## Quick start

```bash
# 1. Upload files to your Ubuntu server
scp -r website-agent/ ubuntu@your-server:~/

# 2. Run setup (interactive — asks for your config)
cd ~/website-agent
chmod +x setup.sh
./setup.sh

# 3. Start the agent
sudo systemctl start website-agent

# 4. Watch it work
sudo journalctl -u website-agent -f
```

## Manual start (without systemd)

```bash
export GEMINI_API_KEY=your-key-here
export WEBSITE_URL=http://your-domain.com
export DOCKER_CONTAINER=your-container-name
python3 agent.py
```

## What it does on a real incident

```
[03:14:22] UP  — 243ms
[03:14:52] DOWN (1/2) — Connection refused
[03:15:22] DOWN (2/2) — Connection refused
[NOTIFY]   Site DOWN — agent investigating...

=======================================================
  AGENT ACTIVATED — attempt 1/3
=======================================================

[1/5] Checking Docker container status...
      Status : running
      Running: True
      Restarts: 5
      Exit code: 0

[2/5] Container is running. Collecting logs...
      Docker logs: 4821 chars
      Apache logs: 1203 chars

[3/5] Sending diagnostics to LLM...
  [LLM] Calling Gemini 2.0 Flash...
  [LLM] Gemini responded.

[4/5] Parsing LLM response...
  Diagnosis  : App running out of memory — Java heap space error
  Fix command: docker restart my-app
  Confidence : high
  Category   : oom

[5/5] Executing fix...
      Success : True

  Waiting 15s before verifying...
  Verification: UP

  Site is back UP after 1 attempt(s).
[NOTIFY]   RECOVERED — docker restart my-app
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | From aistudio.google.com |
| `GROQ_API_KEY` | No | From console.groq.com (fallback) |
| `WEBSITE_URL` | Yes | Full URL of your site |
| `DOCKER_CONTAINER` | Yes | Container name from `docker ps` |
| `APACHE_LOG` | No | Default: /var/log/apache2/error.log |
| `SLACK_WEBHOOK` | No | Slack incoming webhook URL |
| `CHECK_INTERVAL` | No | Default: 60 seconds |

## Systemd commands

```bash
sudo systemctl start   website-agent   # start
sudo systemctl stop    website-agent   # stop
sudo systemctl restart website-agent   # restart
sudo systemctl status  website-agent   # check status
sudo journalctl -u website-agent -f    # live logs
sudo journalctl -u website-agent -n 100  # last 100 lines
```
# devops-self-healing-infra-agent
