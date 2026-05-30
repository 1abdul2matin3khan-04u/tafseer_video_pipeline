# Tafseer Video Automation Pipeline

An 8-step automated pipeline that transforms raw Quranic tafseer (exegesis) source data into polished, dual-language presentation videos. The pipeline extracts scholar commentaries, synthesises them into unified narratives, generates spoken video scripts, divides content into visual slide cards, integrates TTS audio, and renders final MP4 videos—all with per-ruku resumability and crash-safe tracking.

**Supported language tracks:** English · Roman Urdu (Nastaliq script)

---

## Table of Contents

- [High-Level Data Flow](#high-level-data-flow)
- [Project Structure](#project-structure)
- [Pipeline Steps (0–7)](#pipeline-steps-07)
- [Root Utilities](#root-utilities)
- [Visual Rendering System](#visual-rendering-system)
- [Prerequisites & Setup](#prerequisites--setup)
- [Environment Variables](#environment-variables)
- [CLI Reference](#cli-reference)
- [Known Issues & Flaws](#known-issues--flaws)
- [Context Template for AI Assistants](#context-template-for-ai-assistants)

---

## High-Level Data Flow

```
Step 0 ──► Step 1 ──► Step 2 ──► Step 3 ──► Step 4 ──► Step 5 ──► Step 6 ──► Step 7
extract     summarise   combine    script     visual     audio +    block      ruku
ruku data   per-source  into one   for voice  card div   animation  MP4        MP4
            (×5)        tafseer    -over      + layouts  integrate  render     concat
```

| Step | Script(s) | API / Tool | Input | Output |
|------|-----------|-----------|-------|--------|
| **0** | `extract_ruku.py` | — (local) | Raw source JSONs (ibn-kathir, maarif, tazkir, saadi, bayan-ul-quran, wbw) | Per-ruku source JSONs |
| **1** | `summarize_ruku.py` | Gemini (`flash-lite`) | Step 0 JSONs | Per-source summary `.md` with YAML frontmatter |
| **2** | `generate_combined_tafseer_english.py`, `…_roman-urdu.py` | Gemini (`flash`) | Step 1 summaries | Combined tafseer `.md` + split `block_N.md` files |
| **3** | `generate_video_scripts_english.py`, `…_roman-urdu.py` | Gemini (`flash` / `flash-lite`) | Step 2 block MDs + Step 0 wbw.json | Spoken video script `.md` per block |
| **4** | `generate_visual_directions_division_english.py`, `…_roman_urdu.py`, `process_visual_groups.py` | Gemini (`flash-lite` × 2 calls) | Step 3 scripts | Subblock `.json` files + `subblocks_manifest.json` |
| **5** | `integrate_audio_animation.py` | AWS Polly, Edge-TTS, Gemini (transliteration), everyayah.com | Step 4 subblock JSONs | Subblock JSONs with audio paths, durations, frame counts → `remotion_project/public/` |
| **6** | `assemble_blocks.py` | Remotion CLI, FFmpeg | Step 5 outputs | `block_N.mp4` per block |
| **7** | `assemble_ruku.py` | FFmpeg | Step 6 block MP4s | Final `surah_XXX_ruku_RR_{lang}.mp4` |

> **Ordering constraint:** Step 2 Urdu depends on Step 2 English completing first (reads English block headers for alignment).

---

## Project Structure

```
workspace/
│
├── .env                               # API keys (Gemini, AWS, GitHub PAT) — NOT committed
├── .gitignore
├── README.md
│
├── initialize_tracking.py             # Generates / updates all step tracking JSONs
├── cleaner.py                         # Cleans outputs + cascade-resets tracking states
├── api_logger.py                      # Gemini API key rotation, daily usage logging
│
├── step0__whole-single/               # Data extraction from raw source JSONs
│   ├── input_resources/               #   Raw tafseer sources + rukuDivision.json
│   ├── guiding_resources/             #   extract_ruku.py + tracking JSON
│   └── output_resources/              #   Per-ruku source JSONs
│
├── step1__single-summary/             # Individual source summarisation (Gemini)
│   ├── guiding_resources/             #   summarize_ruku.py + tracking JSON
│   └── output_resources/              #   {source}_summary.md per ruku
│
├── step2__summary-combined/           # Unified tafseer synthesis (Gemini)
│   ├── guiding_resources/             #   EN + UR scripts + tracking JSONs
│   └── output_resources/              #   tafseer_{en,ur}.md + block_N.md splits
│
├── step3__combined-script/            # Voiceover script generation (Gemini)
│   ├── guiding_resources/             #   EN + UR scripts + tracking JSONs
│   └── output_resources/              #   Spoken scripts per block
│
├── step4__script-visual-division/     # Scene breakdown + visual group assignment (Gemini)
│   ├── guiding_resources/             #   EN + UR division scripts, process_visual_groups.py
│   └── output_resources/              #   Subblock JSONs + manifests
│
├── step5__animation-audio-integration/
│   ├── integrate_audio_animation.py   #   TTS generation + layout mappings
│   ├── check_layouts.py               #   Dev diagnostic: manifest dump
│   ├── scan_layouts.py                #   Dev diagnostic: layout validator
│   └── remotion_project/              #   React Remotion video project
│       ├── src/
│       │   ├── Root.tsx               #     Remotion composition entry
│       │   ├── SequencePlayer.tsx      #     Per-scene sequence renderer
│       │   ├── Scene.tsx              #     Scene wrapper (caption + layout)
│       │   ├── CaptionContainer.tsx   #     Caption/title card rendering
│       │   ├── Typography.tsx         #     Font + script detection (Nastaliq/LTR)
│       │   ├── Background.tsx         #     Animated gradient background
│       │   ├── layouts/
│       │   │   ├── LayoutRenderer.tsx  #   Dispatches to correct layout component
│       │   │   ├── BulletsLayout.tsx
│       │   │   ├── TableLayout.tsx
│       │   │   ├── TimelineLayout.tsx
│       │   │   ├── ComparisonLayout.tsx
│       │   │   ├── HierarchyLayout.tsx
│       │   │   └── NarrativeLayout.tsx
│       │   └── index.css
│       └── public/                    #   Rendered assets (audio, JSONs)
│
├── step6__block-assembly/             # Remotion render + FFmpeg stitch → block MP4s
│   └── assemble_blocks.py
│
├── step7__ruku-assembly/              # FFmpeg concat → final ruku MP4
│   └── assemble_ruku.py
│
└── logs/                              # API usage logs (JSON + Markdown reports)
```

---

## Pipeline Steps (0–7)

### Step 0 — Data Extraction (`step0__whole-single`)

Extracts per-ruku verse data from 6 source JSON files (5 tafseer scholars + word-by-word translation). Purely local; no API calls.

- **Script:** `guiding_resources/extract_ruku.py`
- **Sources:** ibn-kathir, maarif, tazkir, saadi, bayan-ul-quran, wbw
- **Output:** `output_resources/surah_XXX/ruku_R_A/{source}.json`
- **Features:** File loading cache for `--all` batch mode, reference integrity checking

### Step 1 — Source Summarisation (`step1__single-summary`)

Summarises each of the 5 tafseer sources individually using Gemini AI. Outputs Markdown with YAML frontmatter. Per-source completion tracking enables crash-safe resumability.

- **Script:** `summarize_ruku.py`
- **Model:** `gemini-3.1-flash-lite`
- **Output:** `{source}_summary.md` per ruku

### Step 2 — Combined Tafseer Synthesis (`step2__summary-combined`)

Synthesises all 5 source summaries into a single structured tafseer document, organised into thematic blocks.

- **Scripts:** `generate_combined_tafseer_english.py`, `generate_combined_tafseer_roman-urdu.py`
- **Model:** `gemini-3.5-flash`
- **Output:** `tafseer_english.md` / `tafseer_urdu.md` + split `block_N.md` files

### Step 3 — Video Script Generation (`step3__combined-script`)

Converts block tafseer content into spoken video scripts with recitation cues, translations, and narrator commentary.

- **Scripts:** `generate_video_scripts_english.py`, `generate_video_scripts_roman-urdu.py`
- **Models:** English: `gemini-3.5-flash`, Urdu: `gemini-3.1-flash-lite`
- **Reads:** Step 0 `wbw.json` for Arabic text + translations

### Step 4 — Visual Card Division (`step4__script-visual-division`)

Two-pass AI pipeline per block: (1) scene breakdown, (2) visual group type assignment (bullets, table, timeline, comparison, hierarchy, narrative). Includes auto-repair logic for common Gemini output quirks.

- **Scripts:** `generate_visual_directions_division_english.py`, `generate_visual_directions_division_roman_urdu.py`, `process_visual_groups.py`
- **Model:** `gemini-3.1-flash-lite` (× 2 calls per block)
- **Output:** `block_N_phase_M_K.json` subblock files + `subblocks_manifest.json`

### Step 5 — Audio & Animation Integration (`step5__animation-audio-integration`)

Generates TTS audio (AWS Polly for English, Edge-TTS for Urdu), downloads Arabic recitations from everyayah.com, and transliterates Roman Urdu to Nastaliq script via Gemini. Outputs enriched subblock JSONs into the Remotion project's `public/` directory.

- **Script:** `integrate_audio_animation.py`
- **Dependencies:** `boto3`, `edge_tts`, `mutagen`, `ffprobe`, Gemini API
- **Features:** Async Edge-TTS, batch transliteration with caching, Polly engine fallback chain (generative → neural → standard), Gemini model fallback on rate limit

### Step 6 — Block Assembly (`step6__block-assembly`)

Renders each subblock via Remotion CLI, then stitches multi-subblock blocks into a single MP4 using FFmpeg concat.

- **Script:** `assemble_blocks.py`
- **Dependencies:** `npx remotion render`, `ffmpeg`
- **Optimisation:** Single-subblock blocks render directly to final path (skip temp + stitch)

### Step 7 — Ruku Assembly (`step7__ruku-assembly`)

Concatenates all block MP4s into a single ruku-level video via FFmpeg's lossless copy-codec concat demuxer.

- **Script:** `assemble_ruku.py`
- **Dependencies:** `ffmpeg`
- **Output:** `surah_XXX/surah_XXX_ruku_RR_{lang}.mp4`

---

## Root Utilities

### `api_logger.py` — API Key Rotation & Usage Logging

Central module imported by all Gemini-calling steps. Features:
- Round-robin key rotation across multiple `PROJECT_N_GEMINI_API_KEY_M` env vars
- Daily usage logging to JSON (`logs/`) with Markdown report generation
- Key exhaustion flag (`GEMINI_DISABLED`) to short-circuit calls when all keys are rate-limited

### `initialize_tracking.py` — Tracking Initialisation

Generates or updates the `tracking.json` files in every step's `guiding_resources/` directory. These JSONs record per-ruku, per-block, and per-language completion states for resumability.

```powershell
python initialize_tracking.py --surah <num> --ruku <abs_ruku>
```

### `cleaner.py` — Pipeline Cleaner

Cleans generated output files and automatically cascade-resets tracking states for the cleaned step and all downstream steps.

```powershell
python cleaner.py --ruku <abs_ruku> [--step <0-7|all>] [--block <num>] [--subblock <id>] [--lang <en|ur|both>]
```

**Examples:**
```powershell
# Clean ruku 1, Urdu track, across all steps
python cleaner.py --ruku 1 --lang ur

# Clean block 5 outputs for steps 5 and 6, Urdu only
python cleaner.py --ruku 1 --block 5 --step 5,6 --lang ur
```

---

## Visual Rendering System

The React Remotion frontend lives in `step5__animation-audio-integration/remotion_project/src/`.

### Layout Types

| Layout | Component | Adaptive Scaling |
|--------|-----------|-----------------|
| Bullets | `BulletsLayout.tsx` | Presets for ≤2, 3, and ≥4 items |
| Table | `TableLayout.tsx` | Header/cell font metrics by row count |
| Timeline | `TimelineLayout.tsx` | Grid spacings by event count |
| Comparison | `ComparisonLayout.tsx` | Column cell scaling by row count |
| Hierarchy | `HierarchyLayout.tsx` | Grid spacing + branch widths by child count |
| Narrative | `NarrativeLayout.tsx` | Plain text fallback |

### Design Principles

- **Cinematic Canvas:** Containers span up to 1150–1200px (~85% of the 16:9 canvas) for a wide, cinematic feel with breathing room.
- **Reading Contrast:** Deep backdrop blur (`blur(20px)`), dark frosted card backing (`rgba(15, 23, 42, 0.65)`), strong text shadows.
- **Nastaliq Script Support:** Auto-detects Arabic/Urdu characters and switches to Nastaliq fonts (`Noto Nastaliq Urdu` / `Amiri`) with RTL direction. Roman Urdu (Latin alphabet) renders LTR in `Outfit` / `Inter`.
- **Remotion-Safe Animations:** No CSS transitions (would break Remotion's pure frame rendering). All animations use Remotion's `spring()` / `interpolate()`.

---

## Prerequisites & Setup

### System Requirements

| Tool | Purpose | Required By |
|------|---------|-------------|
| **Python 3.10+** | Pipeline scripts | All steps |
| **Node.js 18+** | Remotion video rendering | Steps 5–6 |
| **FFmpeg** | Audio/video concat & processing | Steps 5–7 |
| **FFprobe** | Audio duration fallback | Step 5 |

### Python Dependencies

```bash
pip install boto3 botocore edge-tts mutagen python-dotenv
```

### Node.js Setup (Remotion)

```bash
cd step5__animation-audio-integration/remotion_project
npm install
```

### API Accounts Required

- **Google Gemini API** — Content generation (steps 1–5). Multiple keys recommended for rate-limit rotation.
- **AWS** — Polly TTS for English voice (step 5). Requires IAM credentials with `polly:SynthesizeSpeech` permission.

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Gemini API Keys (rotation pool — add as many as needed)
PROJECT_1_GEMINI_API_KEY_1=your_key_here
PROJECT_1_GEMINI_API_KEY_2=your_key_here
PROJECT_2_GEMINI_API_KEY_1=your_key_here
# ... pattern: PROJECT_{N}_GEMINI_API_KEY_{M}

# AWS Credentials (for Polly TTS)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=us-east-1

# GitHub PAT (currently unused in pipeline code)
GITHUB_PAT=your_pat
```

> ⚠️ **Never commit `.env` to version control.** The `.gitignore` already excludes it.

---

## CLI Reference

### Running a Step

Each step is run independently from the workspace root. There is no pipeline orchestrator; steps must be executed in order.

```powershell
# Step 0 — Extract a single ruku
python step0__whole-single/guiding_resources/extract_ruku.py --surah 1 --ruku 1

# Step 0 — Extract all rukus
python step0__whole-single/guiding_resources/extract_ruku.py --all

# Step 1 — Summarise
python step1__single-summary/summarize_ruku.py --surah <num> --ruku <abs_ruku>

# Step 2 — Combine (English first, then Urdu)
python step2__summary-combined/generate_combined_tafseer_english.py --surah <num> --ruku <abs_ruku>
python step2__summary-combined/generate_combined_tafseer_roman-urdu.py --surah <num> --ruku <abs_ruku>

# Steps 3–4 follow the same pattern per language...

# Step 5 — Audio + Animation
python step5__animation-audio-integration/integrate_audio_animation.py --surah <num> --ruku <abs_ruku>
```

### Step 5 Options

| Flag | Description |
|------|-------------|
| `--subblock <id>` | Target a single subblock (e.g. `block_5_phase_3_1`). Skips global tracker update. |
| `--no-audio` / `--layout-only` | Dry-run: bypass TTS + transliteration, use existing files or default 5.0s duration. |
| `--voice-en <name>` | English voice actor override (default: `Matthew`). |
| `--voice-ur <name>` | Urdu voice actor override (default: `ur-PK-AsadNeural`). |

### Cleaner

```powershell
python cleaner.py --ruku <abs_ruku> [--step <0-7|all>] [--block <num>] [--subblock <id>] [--lang <en|ur|both>]
```

Cascade behaviour: cleaning step *S* automatically resets tracking for steps *S* through 7.

---

## Known Issues & Flaws

### 🔴 Critical

| Issue | Details |
|-------|---------|
| **No file locking on shared state** | `api_logger.py` uses a naive retry loop (0.1s × 5) for concurrent JSON writes. No real file lock (`fcntl.flock` / `msvcrt.locking`). Running multiple steps simultaneously can corrupt `key_rotation_state.json` or API logs. |
| **Gemini response not validated for blocked/empty content** | API responses are not checked for empty `candidates`, safety filter blocks, or missing content. Would crash with `KeyError` on unexpected responses. |

### 🟠 Significant

| Issue | Details |
|-------|---------|
| **Massive code duplication** | `call_gemini_api()` is copy-pasted across **7 files** with minor variations. `load_env()` duplicated in 5 files (never called). `strip_markdown_code_blocks()` in 5+ files. Step 4 EN/UR scripts are 99.9% identical (~687 lines each, differing by ~16 bytes). |
| **Dead code** | `load_env()` functions in steps 1–3 (5 copies, never called). `api_key` parameter in `process_track()` / `generate_track()` in steps 2–3 (always passed as `None`, never used). |
| **Prompt typo in Step 1** | `summarize_ruku.py` uses `/n` instead of `\\n` for newlines in the prompt — sends literal `/n` text to the model instead of actual line breaks. |
| **Step 2 Urdu → English implicit dependency** | Urdu track silently depends on English track having completed first (reads English block headers). This is not enforced or documented in the tracking system. |
| **Interactive `input()` in automation script** | Step 2 scripts pause with "Ready to divide?" prompts during processing. Falls back to `True` in non-TTY mode, but is a friction point. |
| **`shell=True` in subprocess calls** | Steps 6–7 use `shell=True` for Remotion and FFmpeg — security risk if paths contain shell metacharacters. |
| **No subprocess timeouts** | Remotion render and FFmpeg commands have no timeout. A hung render blocks indefinitely. |

### 🟡 Minor

| Issue | Details |
|-------|---------|
| **Duplicate `import sys`** | Harmless but present in 3 files (steps 1, 2 EN, 3 UR). |
| **Typo: `surph_num`** | Step 5 parameter should be `surah_num` (works because it's just a logging passthrough). |
| **Step 0 has no tracking JSON** | `initialize_tracking.py` generates tracking for steps 1–7 but not step 0. |
| **No codec/resolution validation before concat** | Step 7 concatenates block MP4s without verifying consistent codecs or resolution. |
| **Hardcoded magic values** | Frame rate `30fps`, default silence `5.0s`, HTTP timeout `180s`, max retries `7`, reciter `Alafasy_128kbps`, max title length `80` chars — none are configurable. |
| **Dev-only diagnostic scripts** | `check_layouts.py` and `scan_layouts.py` are hardcoded to `surah_001/ruku_1_1`. |
| **No type hints or docstrings** | Most functions across the codebase lack type annotations and documentation. |
| **No tests or CI/CD** | Zero test files in the project. No continuous integration configuration. |
| **Inconsistent line endings** | Mix of `\r\n` (Windows) and `\n` (Unix) across files. |
| **`GITHUB_PAT` env var** | Defined in `.env` but never referenced in any Python code. |

---

## Context Template for AI Assistants

When starting a new conversation with an AI coding assistant, paste the block below to establish project context:

```markdown
Hello! We are working on the "Tafseer Video Automation Pipeline" project.

1. **Pipeline**: 8 sequential steps (0–7):
   - Step 0: Extract per-ruku verse data from raw source JSONs (local, no API)
   - Step 1: Summarise each of 5 tafseer sources (Gemini flash-lite)
   - Step 2: Synthesise into combined tafseer with block splits (Gemini flash; Urdu depends on English)
   - Step 3: Generate spoken video scripts with recitation cues (Gemini flash / flash-lite)
   - Step 4: Two-pass visual division → subblock JSONs with layout types (Gemini flash-lite × 2)
   - Step 5: TTS audio (AWS Polly EN, Edge-TTS UR) + Arabic recitation download + Nastaliq transliteration (Gemini) → enriched JSONs into Remotion public/
   - Step 6: Remotion CLI render per subblock + FFmpeg stitch → block MP4s
   - Step 7: FFmpeg concat → final ruku MP4

2. **Root utilities**: `api_logger.py` (key rotation + usage logging), `cleaner.py` (cascade clean + tracking reset), `initialize_tracking.py` (tracking JSON generation)

3. **Visual system** (React Remotion in `step5/remotion_project/src/`):
   - 6 layout types: Bullets, Table, Timeline, Comparison, Hierarchy, Narrative
   - All layouts adaptively scale fonts/gaps/padding based on item count
   - Cinematic 16:9 canvas (containers up to 1150–1200px)
   - High-contrast readability: backdrop blur (20px), dark frosted cards (65% opacity), text shadows
   - Nastaliq auto-detection for Arabic/Urdu script with RTL flip
   - No CSS transitions (Remotion compliance); animations via spring()/interpolate()

4. **Dual language**: English + Roman Urdu (with Nastaliq rendering for actual Urdu script)

5. **Key dependencies**: Python (boto3, edge-tts, mutagen), Node.js (Remotion), FFmpeg, Gemini API, AWS Polly

6. **Known tech debt**: Massive code duplication (call_gemini_api in 7 files, Step 4 EN/UR nearly identical), no file locking on shared state, no tests, dead code (load_env, api_key params)

Please keep these constraints in mind during all modifications.
```
