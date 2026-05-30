#!/usr/bin/env python3
"""
config.py
Centralised configuration constants for the Tafseer Video Automation Pipeline.

Edit values here instead of hunting through individual step scripts.
All pipeline scripts import from this file rather than using inline literals.
"""

# ---------------------------------------------------------------------------
# Video Rendering
# ---------------------------------------------------------------------------
FRAME_RATE = 30                           # Video frames per second
DEFAULT_SILENCE_DURATION = 5.0            # Seconds (used in --no-audio mode)

# ---------------------------------------------------------------------------
# Gemini API Settings
# ---------------------------------------------------------------------------
HTTP_TIMEOUT = 180                        # Request timeout in seconds
MAX_API_RETRIES = 7                       # Max retry attempts per API call
RATE_LIMIT_WAIT = 2                       # Base wait seconds on 429 rate limit

# ---------------------------------------------------------------------------
# Content / Layout Settings
# ---------------------------------------------------------------------------
QURAN_RECITER = "Alafasy_128kbps"         # everyayah.com reciter folder name
MAX_TITLE_CHARS = 80                      # Max characters for visual card titles
DEFAULT_MAX_NARRATIVE_SCENES = 6          # Max narrative scenes per subblock

# ---------------------------------------------------------------------------
# Voice Defaults (TTS)
# ---------------------------------------------------------------------------
DEFAULT_VOICE_EN = "Matthew"              # AWS Polly voice for English
DEFAULT_VOICE_UR = "ur-PK-AsadNeural"     # Edge-TTS voice for Urdu

# ---------------------------------------------------------------------------
# AWS Defaults
# ---------------------------------------------------------------------------
DEFAULT_AWS_REGION = "us-east-1"          # Default AWS region for Polly
