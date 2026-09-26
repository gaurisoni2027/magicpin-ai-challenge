"""
Vera Bot — Configuration
Reads from environment variables / .env file
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── LLM ────────────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")        # openai | anthropic | gemini | deepseek | groq
LLM_API_KEY: str  = os.getenv("LLM_API_KEY", "")
LLM_MODEL: str    = os.getenv("LLM_MODEL", "")                 # leave empty for per-provider default

# ─── Server ─────────────────────────────────────────────────────────────────
PORT: int = int(os.getenv("PORT", "8080"))
HOST: str = os.getenv("HOST", "0.0.0.0")

# ─── Team metadata ───────────────────────────────────────────────────────────
TEAM_NAME: str         = os.getenv("TEAM_NAME", "Vera-Hybrid")
TEAM_MEMBERS = os.getenv("TEAM_MEMBERS","Gauri Soni").split(",")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL","gauri22102003@gmail.com")
VERSION: str           = "1.0.0"
SUBMITTED_AT: str      = "2026-09-27T00:00:00Z"

# ─── Defaults per provider ───────────────────────────────────────────────────
PROVIDER_DEFAULTS = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "gemini":    "gemini-1.5-flash",
    "deepseek":  "deepseek-chat",
    "groq":      "llama-3.1-70b-versatile",
}
