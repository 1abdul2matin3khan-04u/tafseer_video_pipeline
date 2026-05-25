#!/usr/bin/env python3
import os
import sys
import json
import time
import re
import urllib.request
import urllib.error
import argparse
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_logger
# Configurable Gemini Model
GEMINI_MODEL = "models/gemini-3.5-flash"
SYSTEM_PROMPT_ENGLISH = """You are an expert academic Islamic scholar and comparative exegetical analyst.
Read the provided Ruku exegesis summaries from 5 different sources and synthesize them into a single, comprehensive "Combined Tafseer" in Markdown format, organized block by block.
=== Rules ===
1. THEMATIC GROUPING: Divide the Ruku into logical thematic blocks.
   - Demarcate each block with a level-2 header in the format: `## Block: [Verse Range] - [Theme]` (e.g., `## Block: 1-3 - The Oneness of Allah`).
   - If a block is conceptual and does not correspond to specific verses (e.g., introductory concepts before verses start, or concluding remarks after the ruku ends), format the header as: `## Block: Concept - [Theme]`.
   - Blocks can cover a group of adjacent verses. Multiple blocks can cover the same verse range if they discuss different themes.
   - Do not skip any verses in the Ruku.
2. CORE TAFSEER: Under each block, write a section:
   ### Core Tafseer (Ibn Kathir)
   Provide a detailed, comprehensive summary of Tafseer Ibn Kathir for the verses in this block. Do not omit any key historical details or theological explanations.
3. COMPARATIVE ANALYSIS (NO REPETITION): Compare the other 4 Tafseers (Maarif-ul-Quran, Tazkir-ul-Quran, Tafsir As-Saadi, Tafsir Bayan-ul-Quran) against Ibn Kathir.
   - Under each block, write a section:
     ### Additional Interpretations
     - **Maarif-ul-Quran**: [Unique insights, or omit if none]
     - **Tazkir-ul-Quran**: [Unique insights, or omit if none]
     - **Tafsir As-Saadi**: [Unique insights translated to English, or omit if none]
     - **Tafsir Bayan-ul-Quran**: [Unique insights translated to English, or omit if none]
   - For each author, extract only unique insights (historical contexts, linguistic notes, spiritual lessons) not covered by Ibn Kathir. Do not repeat any points.
   - Omit the author completely from this list if they add nothing unique for this block.
4. LANGUAGE: The output text must be in clear, formal English. Translate any Urdu insights from Tafsir As-Saadi and Bayan-ul-Quran into English.
5. OUTPUT FORMAT: Return only the synthesized Markdown document. Do not wrap it in markdown code blocks (e.g. do not wrap in ```markdown ... ```) or add introductory/concluding conversational filler. Start directly with the first block header.
"""
def load_env(filepath):
    if not os.path.exists(filepath):
        return {}
    env_vars = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    env_vars[key.strip()] = val.strip()
    except Exception as e:
        print(f"Warning: Failed to parse .env file: {e}")
    return env_vars
def parse_markdown_summary(filepath):
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        # Strip YAML frontmatter delimited by ---
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
        return content.strip()
    except Exception as e:
        print(f"Warning: Failed to read/parse markdown summary {filepath}: {e}")
        return ""
def strip_markdown_code_blocks(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        if text.endswith("```"):
            text = text[:-3].strip()
    return text
def call_gemini_api(model, system_prompt, user_content, step_name, abs_ruku, surah_number, surah_name, rel_ruku):
    max_retries = 7
    for attempt in range(1, max_retries + 1):
        key_name, api_key = api_logger.get_next_api_key(step_name)
        if not api_key:
            print("  Error: No API keys loaded.")
            return None
            
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{system_prompt}\n\nInput Context:\n{user_content}"}
                    ]
                }
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                response_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                
                input_tokens = None
                output_tokens = None
                if "usageMetadata" in res_data:
                    input_tokens = res_data["usageMetadata"].get("promptTokenCount")
                    output_tokens = res_data["usageMetadata"].get("candidatesTokenCount")
                    
                api_logger.log_api_call(
                    step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                    model, key_name, "Success", input_tokens, output_tokens
                )
                return response_text
        except urllib.error.HTTPError as e:
            try:
                err_msg = e.read().decode('utf-8')
            except Exception:
                err_msg = e.reason
            print(f"  [Attempt {attempt}/{max_retries} with {key_name}] HTTP Error {e.code}: {e.reason}. Detail: {err_msg}")
            
            api_logger.log_api_call(
                step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                model, key_name, f"HTTP Error {e.code}", None, None
            )
            
            if e.code == 429:
                print(f"  [Rate Limit Active] Rotating key and retrying...")
                time.sleep(2)
                continue
        except Exception as e:
            print(f"  [Attempt {attempt}/{max_retries} with {key_name}] Error: {e}")
            api_logger.log_api_call(
                step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                model, key_name, f"Error: {str(e)[:50]}", None, None
            )
            
        if attempt < max_retries:
            time.sleep(1)
            
    return None
def process_track(api_key, script_dir, root_dir, limit, ruku_filter, force_flag, delay):
    print("\n===== Starting Step 2 (ENGLISH Track) =====")
    todo_path = os.path.join(script_dir, "guiding_resources", "todo_tafseer_english.json")
    
    if not os.path.exists(todo_path):
        print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
        return
        
    with open(todo_path, "r", encoding="utf-8") as f:
        todo_list = json.load(f)
        
    processed_rukus = 0
    
    for entry in todo_list:
        if limit is not None and processed_rukus >= limit:
            print(f"Reached processing limit of {limit} Rukus for ENGLISH track. Stopping.")
            break
            
        if ruku_filter is not None and entry["absolute_ruku"] != ruku_filter:
            continue
            
        if entry["completed"] and not force_flag:
            continue
            
        abs_ruku = entry["absolute_ruku"]
        surah_num = entry["surah_number"]
        surah_name = entry["surah_name"]
        rel_ruku = entry["relative_ruku"]
        verse_range = entry["verse_range"]
        
        print(f"\n>>> [ENGLISH] Processing Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})")
        
        # Directories
        output_ruku_dir = os.path.join(
            root_dir, "step2__summary-combined", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}"
        )
        
        step1_ruku_dir = os.path.join(
            root_dir, "step1__single-summary", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}"
        )
        
        # Load summaries
        sources_data = {}
        source_keys = {
            "tafseer-ibn-kathir": "ibn_kathir_summary.md",
            "maarif-ul-quran": "maarif_summary.md",
            "tazkir-ul-quran": "tazkir_summary.md",
            "tafsir-as-saadi": "saadi_summary.md",
            "tafsir-bayan-ul-quran": "bayan_ul_quran_summary.md"
        }
        
        for k, fname in source_keys.items():
            fpath = os.path.join(step1_ruku_dir, fname)
            summary_content = parse_markdown_summary(fpath)
            if summary_content:
                sources_data[k] = summary_content
            else:
                print(f"  Warning: Summary file {fname} not found or empty.")
                
        # Build simplified user context for AI
        input_context = {
            "ruku_metadata": {
                "surah_number": surah_num,
                "surah_name": surah_name,
                "absolute_ruku": abs_ruku,
                "relative_ruku": rel_ruku,
                "verse_range": verse_range
            },
            "sources": sources_data
        }
        
        # Call Gemini API
        print(f"  Querying Gemini API ({GEMINI_MODEL}) for combined Markdown exegesis...")
        ai_response = call_gemini_api(
            GEMINI_MODEL,
            SYSTEM_PROMPT_ENGLISH,
            json.dumps(input_context, ensure_ascii=False, indent=2),
            "step2", abs_ruku, surah_num, surah_name, rel_ruku
        )
        
        if not ai_response:
            print(f"  Error: Failed to get AI response for Ruku {abs_ruku}. Skipping.")
            continue
            
        cleaned_markdown = strip_markdown_code_blocks(ai_response)
        
        # Prepend YAML frontmatter
        yaml_header = (
            "---\n"
            f"surah_number: {surah_num}\n"
            f"surah_name: {surah_name}\n"
            f"absolute_ruku: {abs_ruku}\n"
            f"relative_ruku: {rel_ruku}\n"
            f"verse_range: {verse_range}\n"
            "---\n"
        )
        final_output_content = yaml_header + cleaned_markdown
        
        out_filename = "tafseer_english.md"
        out_filepath = os.path.join(output_ruku_dir, out_filename)
        
        # Write output file
        os.makedirs(output_ruku_dir, exist_ok=True)
        try:
            with open(out_filepath, "w", encoding="utf-8") as f_out:
                f_out.write(final_output_content)
            print(f"  Saved combined Markdown Tafseer to {out_filepath}")
        except Exception as e:
            print(f"  Error saving final Tafseer to {out_filepath}: {e}")
            continue

        # Robust interactive block splitting prompt
        should_split = True
        if sys.stdin.isatty():
            try:
                user_input = input(f"\n[?] Ready to divide the Tafseer into blocks? Press Enter or type 'yes' to proceed: ").strip().lower()
                should_split = (user_input == "" or user_input.startswith("y"))
            except (EOFError, KeyboardInterrupt):
                # Fallback to splitting if closed or cancelled
                should_split = True
        
        if should_split:
            block_regex = r'^## (Block):\s*'
            blocks_parts = re.split(block_regex, cleaned_markdown, flags=re.MULTILINE | re.IGNORECASE)
            
            if len(blocks_parts) <= 1:
                print(f"  Warning: No block headers found in Tafseer. Cannot split.")
            else:
                block_exegesis_dir = os.path.join(output_ruku_dir, "en")
                os.makedirs(block_exegesis_dir, exist_ok=True)
                
                # Clear old block files
                for f_old in os.listdir(block_exegesis_dir):
                    if f_old.startswith("block_") and f_old.endswith(".md"):
                        try:
                            os.remove(os.path.join(block_exegesis_dir, f_old))
                        except Exception:
                            pass
                            
                blocks_count = 0
                i = 1
                while i < len(blocks_parts):
                    if i + 1 < len(blocks_parts):
                        blocks_count += 1
                        keyword = blocks_parts[i]
                        body_text = blocks_parts[i+1].strip()
                        
                        block_markdown = f"## Block: {body_text}\n"
                        block_file_path = os.path.join(block_exegesis_dir, f"block_{blocks_count}.md")
                        
                        try:
                            with open(block_file_path, "w", encoding="utf-8") as f_block:
                                f_block.write(block_markdown)
                            print(f"    Saved block: block_{blocks_count}.md")
                        except Exception as e:
                            print(f"    Error saving block exegesis to {block_file_path}: {e}")
                    i += 2
                print(f"  Successfully divided into {blocks_count} block files in {block_exegesis_dir}")
            
        # Update progress tracking
        entry["completed"] = True
        with open(todo_path, "w", encoding="utf-8") as f_todo:
            json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
            
        processed_rukus += 1
        time.sleep(delay)
def main():
    parser = argparse.ArgumentParser(description="Synthesize per-source summaries into combined structured English Tafseers.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed entries.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between successful API calls (default: 1.0).")
    args = parser.parse_args()
    
    # Determine directories
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    # Load environment variables via api_logger
    keys = api_logger.load_env_keys()
    if not keys:
        print("Error: No GEMINI_API_KEY_1 through GEMINI_API_KEY_7 found in .env file.", file=sys.stderr)
        sys.exit(1)
    api_key = None
        
    process_track(api_key, script_dir, root_dir, args.limit, args.ruku, args.force, args.delay)
    print("\nEnglish Step 2 processing finished.")
if __name__ == "__main__":
    main()