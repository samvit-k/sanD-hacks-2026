"""Runtime settings, read from the project-root .env (see .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# The backend directory is located from this file, not from the repository root: on Vercel the backend service is
# deployed with backend/ as its root, so schema.sql and fixtures must be found relative to the code itself.
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
load_dotenv(ROOT / ".env")  # local development only; on Vercel the variables come from the project settings

class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        self.embed_model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
        self.embed_dim = 768  # must match vector(768) in schema.sql
        self.gemini_timeout_s = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))  # per request

        # Optional demo/testing default only. The active question is set by the user (PUT /api/workspace)
        # and stored in the `workspace` table, which always takes precedence. Empty -> no default.
        self.research_question = " ".join(os.getenv("RESEARCH_QUESTION", "").split()) or None

        # Coverage limits: start small, raise only after Checkpoint 2 passes.
        self.max_claims = int(os.getenv("MAX_CLAIMS", "3"))
        self.top_k = int(os.getenv("TOP_K", "2"))
        self.sim_threshold = float(os.getenv("SIM_THRESHOLD", "0.6"))
        self.max_prompt_sentences = int(os.getenv("MAX_PROMPT_SENTENCES", "1200"))
        self.llm_workers = int(os.getenv("LLM_WORKERS", "4"))

        # Bright Data Google Scholar Scraper API (dedicated scraper, async dataset API).
        self.brightdata_token = os.getenv("BRIGHTDATA_API_TOKEN", "")
        self.brightdata_dataset_id = os.getenv("BRIGHTDATA_SCHOLAR_DATASET_ID", "")
        # JSON input object for one Scholar query; "{query}" is substituted.
        # VERIFY the input field name against the Scholar scraper's page in the Scraper Library.
        self.brightdata_input_template = os.getenv("BRIGHTDATA_SCHOLAR_INPUT", '{"keyword": "{query}"}')
        self.brightdata_live_wait = int(os.getenv("BRIGHTDATA_LIVE_WAIT_SECONDS", "45"))
        self.discovery_limit = int(os.getenv("DISCOVERY_LIMIT", "8"))

    @property
    def brightdata_configured(self) -> bool:
        return bool(self.brightdata_token and self.brightdata_dataset_id)

settings = Settings()