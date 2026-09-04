import json
import logging
import os
from typing import Callable

import config as cfg
import httpx
from models import Decision, StrategistOutput

log = logging.getLogger("strategist")

_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prompts", "strategist_system.md"
)

# Fix: Load prompt into memory once at startup rather than hitting the disk on every 15-min call
try:
    with open(_PROMPT_PATH) as f:
        SYSTEM_PROMPT = f.read()
except Exception as e:
    log.error("Failed to load system prompt file at startup: %s", e)
    SYSTEM_PROMPT = "You are a professional trade strategist. Analyze data carefully."


def call_llm(system: str, user: str, schema: dict | None = None,
             model: str | None = None) -> str:
    """Google Gemini generateContent API over raw httpx (no SDK) with native schema validation."""
    model = model or cfg.LLM_MODEL  # env override handled in config.py
    
    # Base generation configuration
    gen_config = {
        "temperature": cfg.LLM_TEMPERATURE,
        "maxOutputTokens": cfg.LLM_MAX_TOKENS,
        "responseMimeType": "application/json",
    }
    
    # Fix: Inject the native Gemini responseSchema to ensure 100% compliant JSON structures
    if schema:
        gen_config["responseSchema"] = schema

    # Inject optional sampling constraints conditionally
    if cfg.LLM_TOP_P is not None:
        gen_config["topP"] = cfg.LLM_TOP_P
    if cfg.LLM_TOP_K is not None:
        gen_config["topK"] = cfg.LLM_TOP_K

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": gen_config,
    }

    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": cfg.GEMINI_API_KEY},
        json=payload,
        timeout=60.0,
    )
    if resp.status_code >= 400:
        # Surface Gemini's error detail (quota name, schema complaint, ...)
        raise ValueError(f"Gemini HTTP {resp.status_code}: {resp.text[:500]}")
    
    # Safe structural traversal of response payload
    resp_json = resp.json()
    try:
        parts = resp_json["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError) as err:
        raise ValueError(f"Malformed API response structure: {resp_json}") from err


# Hand-written mirror of StrategistOutput in Gemini's OpenAPI-subset dialect.
# Pydantic's model_json_schema() emits $defs/$ref, which Gemini rejects, so
# keep this in sync with models.py by hand (chaos_test covers the contract).
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "regime": {
            "type": "STRING",
            "enum": ["uptrend", "downtrend", "rangebound", "high_uncertainty"],
        },
        "market_view": {"type": "STRING"},
        "decisions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING", "enum": ["enter", "abstain"]},
                    "candidate_id": {"type": "STRING", "nullable": True},
                    "qty": {"type": "INTEGER"},
                    "rationale": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["action"],
            },
        },
    },
    "required": ["regime", "market_view", "decisions"],
}


def decide(brief: dict, risk_state: dict, llm: Callable = call_llm) -> StrategistOutput:
    payload = json.dumps({"market_brief": brief, "risk_state": risk_state}, default=str)
    cleaned_schema = _RESPONSE_SCHEMA

    last_err = None
    # Walk the model chain: any failure (quota, 503 high demand, truncated or
    # invalid JSON) moves to the next model instead of retrying the sick one.
    for attempt, model in enumerate(cfg.LLM_MODELS):
        try:
            raw = llm(SYSTEM_PROMPT, payload, schema=cleaned_schema, model=model)
            return StrategistOutput.model_validate_json(raw)
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("strategist attempt %d (%s) failed: %s",
                        attempt + 1, model, e)
            
    # Bulletproof fallback architecture remains intact
    return StrategistOutput(
        regime="high_uncertainty",
        market_view=f"LLM unavailable or invalid output ({last_err}); abstaining.",
        decisions=[Decision(action="abstain", rationale="fail-safe abstain")],
    )
