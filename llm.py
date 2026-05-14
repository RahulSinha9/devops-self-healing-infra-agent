"""
llm.py — Free LLM client with automatic fallback.

Priority:
  1. Google Gemini 2.0 Flash  — 1,500 req/day free, no credit card
  2. Groq  Llama 3.3 70B      — 14,400 req/day free, ultra-fast
  3. Plain error message       — so agent never crashes silently
"""

import requests
import json


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────
def call_llm(system: str, user: str) -> str:
    """
    Send a system + user message.
    Returns the model's text response as a plain string.
    Tries Gemini first, Groq second.
    """
    print("  [LLM] Calling Gemini 2.0 Flash...")
    response = _gemini(system, user)
    if response:
        print("  [LLM] Gemini responded.")
        return response

    print("  [LLM] Gemini failed — trying Groq Llama 3.3 70B fallback...")
    response = _groq(system, user)
    if response:
        print("  [LLM] Groq responded.")
        return response

    print("  [LLM] Both providers failed.")
    return json.dumps({
        "diagnosis": "LLM unavailable — both Gemini and Groq failed",
        "fix_command": "",
        "confidence": "low",
        "explanation": "Check your API keys or network connectivity"
    })


# ─────────────────────────────────────────────────────────────────────────────
# Provider 1 — Google Gemini 2.0 Flash (free tier)
# ─────────────────────────────────────────────────────────────────────────────
def _gemini(system: str, user: str) -> str | None:
    from config import GEMINI_API_KEY
    if not GEMINI_API_KEY:
        return None
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{
                "parts": [{"text": f"{system}\n\n{user}"}]
            }],
            "generationConfig": {
                "maxOutputTokens": 1024,
                "temperature": 0.1     # low temp = deterministic, factual fixes
            }
        }
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except requests.exceptions.HTTPError as e:
        # 429 = quota exceeded → fall through to Groq
        status = e.response.status_code if e.response else "?"
        print(f"  [Gemini] HTTP {status}: {e}")
        return None
    except Exception as e:
        print(f"  [Gemini] Error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Provider 2 — Groq Llama 3.3 70B (free tier, OpenAI-compatible)
# ─────────────────────────────────────────────────────────────────────────────
def _groq(system: str, user: str) -> str | None:
    from config import GROQ_API_KEY
    if not GROQ_API_KEY:
        print("  [Groq] No GROQ_API_KEY set, skipping.")
        return None
    try:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user}
            ],
            "max_tokens": 1024,
            "temperature": 0.1
        }
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers, json=payload, timeout=30
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else "?"
        print(f"  [Groq] HTTP {status}: {e}")
        return None
    except Exception as e:
        print(f"  [Groq] Error: {e}")
        return None
