#!/usr/bin/env python3
"""
pipeline_utils.py
Shared utility functions for the Tafseer Video Automation Pipeline.

Consolidates duplicated logic (API calls, text processing) into a single
importable module used by all pipeline steps.
"""

import os
import sys
import json
import re
import time
import urllib.request
import urllib.error

# Ensure the project root is on sys.path so sibling modules can be imported
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

import api_logger
from config import HTTP_TIMEOUT, MAX_API_RETRIES, RATE_LIMIT_WAIT


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------

class GeminiAPIError(Exception):
    """Base exception for Gemini API errors."""
    pass


class GeminiBlockedError(GeminiAPIError):
    """Raised when the Gemini API blocks the prompt (no candidates returned)."""
    pass


class GeminiSafetyError(GeminiAPIError):
    """Raised when the response is blocked by Gemini safety filters."""
    pass


class GeminiEmptyResponseError(GeminiAPIError):
    """Raised when the Gemini API returns an empty response."""
    pass


# ---------------------------------------------------------------------------
# Response Extraction
# ---------------------------------------------------------------------------

def extract_gemini_text(response_json):
    """
    Safely extract text from a Gemini API response, raising clear errors
    on blocked, safety-filtered, or empty responses.

    Args:
        response_json: Parsed JSON response from the Gemini API.

    Returns:
        Extracted text string, stripped of leading/trailing whitespace.

    Raises:
        GeminiBlockedError: If no candidates were returned.
        GeminiSafetyError: If the response was blocked by safety filters.
        GeminiEmptyResponseError: If the response contained no text.
    """
    candidates = response_json.get("candidates", [])
    if not candidates:
        block_reason = (
            response_json.get("promptFeedback", {}).get("blockReason", "UNKNOWN")
        )
        raise GeminiBlockedError(
            f"No candidates returned. Block reason: {block_reason}"
        )

    finish_reason = candidates[0].get("finishReason", "")
    if finish_reason == "SAFETY":
        ratings = candidates[0].get("safetyRatings", [])
        raise GeminiSafetyError(f"Response blocked by safety filter: {ratings}")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts or "text" not in parts[0]:
        raise GeminiEmptyResponseError("Response contained no text parts")

    return parts[0]["text"].strip()


# ---------------------------------------------------------------------------
# Gemini API Call (Consolidated)
# ---------------------------------------------------------------------------

def call_gemini_api(
    model,
    prompt,
    step_name,
    abs_ruku,
    surah_number,
    surah_name,
    rel_ruku,
    system_instruction=None,
    response_schema=None,
    temperature=None,
):
    """
    Calls the Gemini API with automatic key rotation, retry logic, and
    rate-limit handling (including server-reported retryDelay parsing).

    Args:
        model: Gemini model identifier (e.g. "models/gemini-3.1-flash-lite").
        prompt: The user prompt text.
        step_name: Pipeline step name for logging (e.g. "step1").
        abs_ruku: Absolute ruku index for logging.
        surah_number: Surah number for logging.
        surah_name: Surah name for logging.
        rel_ruku: Relative ruku index for logging.
        system_instruction: Optional system instruction text.
        response_schema: Optional JSON schema dict for structured output.
            When provided, the API response is parsed as JSON and returned
            as a Python dict/list instead of a text string.
        temperature: Optional temperature float for generation config.

    Returns:
        - If response_schema is None: extracted text string, or None on failure.
        - If response_schema is provided: parsed JSON object, or None on failure.
    """
    for attempt in range(1, MAX_API_RETRIES + 1):
        key_name, api_key = api_logger.get_next_api_key(step_name)
        if not api_key:
            print("  Error: No API keys loaded.")
            return None

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"{model}:generateContent?key={api_key}"
        )
        headers = {"Content-Type": "application/json"}

        # Build request payload
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        generation_config = {}
        if response_schema:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema
        if temperature is not None:
            generation_config["temperature"] = temperature
        if generation_config:
            payload["generationConfig"] = generation_config

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
                res_data = json.loads(response.read().decode("utf-8"))

                # Extract and validate response
                try:
                    response_text = extract_gemini_text(res_data)
                except GeminiAPIError as e:
                    print(
                        f"  [Attempt {attempt}/{MAX_API_RETRIES} with {key_name}] {e}"
                    )
                    api_logger.log_api_call(
                        step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                        model, key_name, f"Error: {str(e)[:50]}", None, None,
                    )
                    if attempt < MAX_API_RETRIES:
                        time.sleep(1)
                    continue

                # Log token usage
                input_tokens = None
                output_tokens = None
                if "usageMetadata" in res_data:
                    input_tokens = res_data["usageMetadata"].get("promptTokenCount")
                    output_tokens = res_data["usageMetadata"].get(
                        "candidatesTokenCount"
                    )

                api_logger.log_api_call(
                    step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                    model, key_name, "Success", input_tokens, output_tokens,
                )

                # If structured output requested, parse as JSON
                if response_schema:
                    try:
                        return json.loads(response_text)
                    except json.JSONDecodeError as e:
                        print(
                            f"  [Attempt {attempt}/{MAX_API_RETRIES}] "
                            f"Failed to parse structured JSON response: {e}"
                        )
                        if attempt < MAX_API_RETRIES:
                            time.sleep(1)
                        continue

                return response_text

        except urllib.error.HTTPError as e:
            try:
                err_msg = e.read().decode("utf-8")
            except Exception:
                err_msg = e.reason

            print(
                f"  [Attempt {attempt}/{MAX_API_RETRIES} with {key_name}] "
                f"HTTP Error {e.code}: {e.reason}. Detail: {err_msg}"
            )

            api_logger.log_api_call(
                step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                model, key_name, f"HTTP Error {e.code}", None, None,
            )

            if e.code == 429:
                # Try to parse server-reported retry delay
                wait_time = RATE_LIMIT_WAIT
                try:
                    err_data = json.loads(err_msg)
                    details = err_data.get("error", {}).get("details", [])
                    for detail in details:
                        retry_delay = detail.get("retryDelay", "")
                        if isinstance(retry_delay, str) and retry_delay.endswith("s"):
                            wait_time = max(
                                float(retry_delay[:-1]), RATE_LIMIT_WAIT
                            )
                            break
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
                print(
                    f"  [Rate Limit Active] Rotating key and retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
                continue

        except Exception as e:
            print(
                f"  [Attempt {attempt}/{MAX_API_RETRIES} with {key_name}] Error: {e}"
            )
            api_logger.log_api_call(
                step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                model, key_name, f"Error: {str(e)[:50]}", None, None,
            )

        if attempt < MAX_API_RETRIES:
            time.sleep(1)

    return None


# ---------------------------------------------------------------------------
# Text Processing Utilities
# ---------------------------------------------------------------------------

def strip_markdown_code_blocks(text):
    """
    Removes wrapping markdown code block fences (```lang ... ```) from text.

    Args:
        text: Input text potentially wrapped in code fences.

    Returns:
        Text with outer code fences removed, if present.
    """
    text = text.strip()
    if text.startswith("```"):
        # Remove opening backticks and optional language identifier
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        # Remove closing backticks
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def parse_verse_range(verse_range_str):
    """
    Parses a verse range string like "1-7" or "5" into (start, end) tuple.

    Args:
        verse_range_str: String like "1-7", "5", or "1,3-5".

    Returns:
        Tuple of (start_verse, end_verse) integers, or (None, None) on failure.
    """
    try:
        verse_range_str = verse_range_str.strip()
        if "-" in verse_range_str:
            parts = verse_range_str.split("-")
            return int(parts[0].strip()), int(parts[-1].strip())
        else:
            v = int(verse_range_str.strip())
            return v, v
    except (ValueError, IndexError):
        return None, None
