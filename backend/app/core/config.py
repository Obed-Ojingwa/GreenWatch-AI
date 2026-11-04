"""Application settings (environment variables / .env). Provider access remains unverified until Gate 0 passes."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SEGMENT_BODY = '{"model": "{{model}}", "image": "{{image_b64}}", "prompt": "{{prompt}}"}'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"  # "production" turns on fail-fast checks (see production_problems)

    # --- database / storage ---
    database_url: str = ""
    auto_create_tables: bool = False
    storage_backend: str = "supabase"  # local | supabase
    local_storage_dir: str = "./storage_data"
    supabase_url: str = ""
    supabase_service_key: str = ""  # backend only; never ship to the browser
    storage_bucket: str = "greenwatch"
    signed_url_ttl: int = 3600

    # --- demo mode ---
    demo_access_key: str = ""
    frontend_origin: str = (
        "https://greenwatch-ai-theta.vercel.app,https://greenwatch-ai-670g.onrender.com"
    )  # comma-separated allowed origins; Render's FRONTEND_ORIGIN overrides this default

    # --- Model providers ---
    ai_provider: str = "gemini"  # gemini | meta; SAM segmentation remains on Meta
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Meta API: used for SAM segmentation and, when AI_PROVIDER=meta, chat/vision too.
    meta_api_key: str = ""
    meta_diagnostic_key: str = ""
    meta_base_url: str = ""
    meta_auth_header: str = "Authorization"
    meta_auth_scheme: str = "Bearer"  # empty string = send the raw key
    meta_chat_path: str = "chat/completions"
    meta_segment_path: str = ""
    meta_segment_body: str = DEFAULT_SEGMENT_BODY  # JSON template: {{model}} {{prompt}} {{image_b64}} {{image_data_uri}}
    meta_segment_masks_path: str = ""  # where the masks are in the response; end with ".*" to enumerate instances
    meta_sam_mask_encoding: str = "one_bit"  # Responses API metadata.mask_encoding
    sam_model: str = ""
    agent_model: str = ""
    vision_qc_model: str = ""  # defaults to agent_model when empty
    meta_timeout_seconds: float = 120.0
    meta_max_retries: int = 3  # total attempts for 429/5xx/transport errors

    # --- limits ---
    max_upload_mb: int = 15
    max_image_dim: int = 2048  # analysis grid: images are downscaled so the longest side <= this
    max_agent_runs_per_hour: int = 20
    max_tool_calls_per_run: int = 40
    max_qc_retries: int = 1
    stale_run_minutes: int = 10

    # --- alignment thresholds (tune on real imagery during Gate 0) ---
    align_min_inliers: int = 25
    align_min_inlier_ratio: float = 0.35
    align_min_overlap: float = 0.60
    align_max_scale_dev: float = 0.25  # |scale - 1| beyond this needs review
    align_max_rotation_deg: float = 15.0

    # --- QC coverage bounds per category: (min, max) fraction of the image. There is deliberately
    # no universal threshold; tune against your imagery. ---
    qc_bounds: dict[str, tuple[float, float]] = {
        "buildings": (0.001, 0.80),
        "trees": (0.002, 0.90),
        "vegetation": (0.002, 0.95),
    }
    qc_bounds_default: tuple[float, float] = (0.0005, 0.95)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def production_problems(s: Settings) -> list[str]:
    """Reasons the configuration is unsafe for production. Empty in any other environment."""
    if s.environment.lower() != "production":
        return []
    problems: list[str] = []
    if len(s.demo_access_key) < 16 or s.demo_access_key == "change-me-to-a-long-random-string":
        problems.append("DEMO_ACCESS_KEY must be set to a random value of at least 16 characters.")
    if s.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL must point at Postgres (Supabase), not SQLite.")
    if s.storage_backend != "supabase":
        problems.append("STORAGE_BACKEND must be 'supabase' in production.")
    elif not s.supabase_url or not s.supabase_service_key:
        problems.append("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for Supabase storage.")
    if s.supabase_url and (
        not s.supabase_url.startswith("https://")
        or any(char.isspace() for char in s.supabase_url)
        or '"' in s.supabase_url
        or "'" in s.supabase_url
    ):
        problems.append("SUPABASE_URL must be the bare HTTPS project URL without quotes or whitespace.")
    origins = [o.strip() for o in s.frontend_origin.split(",") if o.strip()]
    if not origins or any("localhost" in o or "127.0.0.1" in o for o in origins):
        problems.append("FRONTEND_ORIGIN must list the deployed frontend URL(s), not localhost.")
    provider = s.ai_provider.strip().lower()
    if provider not in {"gemini", "meta"}:
        problems.append("AI_PROVIDER must be either 'gemini' or 'meta'.")
    elif provider == "gemini":
        if not s.gemini_api_key:
            problems.append("GEMINI_API_KEY is required when AI_PROVIDER=gemini.")
        if not s.gemini_base_url.startswith("https://"):
            problems.append("GEMINI_BASE_URL must be an HTTPS URL.")
        if not s.agent_model:
            problems.append("AGENT_MODEL must be set to an available Gemini model.")
    else:
        if not s.meta_api_key:
            problems.append("META_API_KEY is required for Meta chat and SAM segmentation.")
        if not s.meta_base_url or not s.meta_base_url.startswith("https://"):
            problems.append("META_BASE_URL must be set to the HTTPS Meta Model API base URL.")
        if not s.meta_chat_path:
            problems.append("META_CHAT_PATH must be set when AI_PROVIDER=meta.")
        if not s.agent_model:
            problems.append("AGENT_MODEL must be set to an available Meta chat model.")

    # SAM is a distinct segmentation provider regardless of the chat/vision provider.
    if not s.meta_api_key and provider != "meta":
        problems.append("META_API_KEY is required for Meta SAM segmentation.")
    if not s.meta_base_url or not s.meta_base_url.startswith("https://"):
        problems.append("META_BASE_URL must be set to the HTTPS Meta Model API base URL for SAM.")
    for name, value in (
        ("SAM_MODEL", s.sam_model),
        ("META_SEGMENT_PATH", s.meta_segment_path),
    ):
        if not value:
            problems.append(f"{name} must be set for production analysis.")
    return problems
