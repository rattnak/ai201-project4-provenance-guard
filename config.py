import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LLM_MODEL = "llama-3.3-70b-versatile"
LOG_FILE = "logs/audit.jsonl"

# Confidence scoring thresholds (see planning.md, section 2)
AI_THRESHOLD = 0.70
HUMAN_THRESHOLD = 0.30

# Ensemble weights
WEIGHT_LLM = 0.5
WEIGHT_STYLOMETRY = 0.3
WEIGHT_REPETITION = 0.2

# Rate limits for POST /submit (see README "Rate limiting" section)
RATE_LIMITS = ["10 per minute", "100 per day"]
