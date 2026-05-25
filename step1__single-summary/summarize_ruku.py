#!/usr/bin/env python3
import os
import sys
import json
import re
import time
import urllib.request
import urllib.error
import argparse
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_logger

# Configurable Gemini Model
# Popular Gemini Models:
# 1. "models/gemini-3.5-flash" (Default: Latest generation Flash model)
# 2. "models/gemini-3.1-flash-lite" (Recommended for strict free-tier rate limits)
# 3. "models/gemini-3-flash-preview" (Gemini 3 Flash Preview)
# 4. "models/gemini-2.5-flash" (Reliable previous generation Flash model)
# 5. "models/gemini-2.5-flash-lite" (Lightweight previous generation Flash model)
GEMINI_MODEL = "models/gemini-3.1-flash-lite"

SOURCE_MAP = {
    "ibn_kathir": {
        "input_name": "tafseer-ibn-kathir",
        "output_name": "ibn_kathir_summary.md",
        "language": "en",
        "lang_name": "English"
    },
    "maarif": {
        "input_name": "maarif-ul-quran",
        "output_name": "maarif_summary.md",
        "language": "en",
        "lang_name": "English"
    },
    "tazkir": {
        "input_name": "tazkir-ul-quran",
        "output_name": "tazkir_summary.md",
        "language": "en",
        "lang_name": "English"
    },
    "saadi": {
        "input_name": "tafsir-as-saadi",
        "output_name": "saadi_summary.md",
        "language": "ur",
        "lang_name": "Urdu"
    },
    "bayan_ul_quran": {
        "input_name": "tafsir-bayan-ul-quran",
        "output_name": "bayan_ul_quran_summary.md",
        "language": "ur",
        "lang_name": "Urdu"
    }
}

SUMMARY_SOURCES = list(SOURCE_MAP.keys())

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

def strip_html(text):
    if not isinstance(text, str):
        return text
    # Simple regex to strip HTML tags
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

def clean_html_structure(obj):
    if isinstance(obj, dict):
        return {k: clean_html_structure(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_html_structure(x) for x in obj]
    elif isinstance(obj, str):
        return strip_html(obj)
    return obj

def strip_markdown_code_blocks(text):
    text = text.strip()
    if text.startswith("```"):
        # Remove opening backticks and language indicator
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        # Remove closing backticks
        if text.endswith("```"):
            text = text[:-3].strip()
    return text

def call_gemini_api(model, prompt, step_name, abs_ruku, surah_number, surah_name, rel_ruku):
    max_retries = 7  # Allow rotating through all 7 keys
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
                        {"text": prompt}
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

def main():
    parser = argparse.ArgumentParser(description="Summarize Quran Tafseers per Ruku using Gemini API.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed sources.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between successful API calls (default: 1.0).")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Language filter: 'both' for all sources (default), 'en' for English sources only, 'ur' for Urdu sources only.")
    args = parser.parse_args()

    # Determine directories
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    # Load environment variables via api_logger
    keys = api_logger.load_env_keys()
    if not keys:
        print("Error: No GEMINI_API_KEY_1 through GEMINI_API_KEY_7 found in .env file.", file=sys.stderr)
        sys.exit(1)

    todo_path = os.path.join(script_dir, "guiding_resources", "todo_summary.json")
    if not os.path.exists(todo_path):
        print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
        sys.exit(1)
        
    with open(todo_path, "r", encoding="utf-8") as f:
        todo_list = json.load(f)
        
    processed_rukus = 0
    
    # Filter sources based on language choice
    active_sources = [
        src_key for src_key in SUMMARY_SOURCES
        if args.lang == "both" or SOURCE_MAP[src_key]["language"] == args.lang
    ]
    
    for entry in todo_list:
        # Check limit
        if args.limit is not None and processed_rukus >= args.limit:
            print(f"\nReached processing limit of {args.limit} Rukus. Stopping.")
            break
            
        # Check absolute Ruku filter
        if args.ruku is not None and entry["absolute_ruku"] != args.ruku:
            continue
            
        # Check if all active sources for this Ruku are completed, skip if so
        sources_completed = entry.get("sources_completed", [])
        all_active_completed = all(s in sources_completed for s in active_sources)
        if all_active_completed and not args.force:
            continue
            
        # Skip completed if not forced
        if entry["completed"] and not args.force:
            continue
            
        abs_ruku = entry["absolute_ruku"]
        surah_num = entry["surah_number"]
        surah_name = entry["surah_name"]
        rel_ruku = entry["relative_ruku"]
        verse_range = entry["verse_range"]
        
        print(f"\n>>> Processing Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku}, Verses: {verse_range})")
        
        # Paths
        input_ruku_dir = os.path.join(
            root_dir, "step0__whole-single", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}"
        )
        output_ruku_dir = os.path.join(
            script_dir, "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}"
        )
        
        sources_completed = entry.get("sources_completed", [])
        if args.force:
            sources_completed = []
            
        ruku_changed = False
        
        for src_key in active_sources:
            if src_key in sources_completed and not args.force:
                continue
                
            src_info = SOURCE_MAP[src_key]
            input_file = os.path.join(input_ruku_dir, f"{src_info['input_name']}.json")
            
            if not os.path.exists(input_file):
                print(f"  Warning: Input file {input_file} does not exist. Marking as completed/skipped.")
                sources_completed.append(src_key)
                entry["sources_completed"] = sources_completed
                ruku_changed = True
                continue
                
            print(f"  Summarizing {src_key} in {src_info['lang_name']}...")
            
            # Load input JSON (retaining raw HTML tags)
            try:
                with open(input_file, "r", encoding="utf-8") as f_in:
                    raw_data = json.load(f_in)
            except Exception as e:
                print(f"  Error reading/parsing {input_file}: {e}. Skipping source.")
                continue
                
            # Construct Prompt: data + simple instruction
            json_str = json.dumps(raw_data, ensure_ascii=False, indent=2)
            prompt = f"{json_str}\n\nwrite in an organized manner. donot skip anything. Write in {src_info['lang_name']}."
            
            # Call API via call_gemini_api
            response_text = call_gemini_api(GEMINI_MODEL, prompt, "step1", abs_ruku, surah_num, surah_name, rel_ruku)
            
            if response_text is None:
                print(f"  Error: Failed to obtain summary for {src_key} from Gemini API. Skipping.")
                continue
                
            # Clean response
            response_text = strip_markdown_code_blocks(response_text)
            
            # Prepend YAML frontmatter
            yaml_header = (
                "---\n"
                f"source: {src_info['input_name']}\n"
                f"language: {src_info['language']}\n"
                f"surah_number: {surah_num}\n"
                f"surah_name: {surah_name}\n"
                f"absolute_ruku: {abs_ruku}\n"
                f"relative_ruku: {rel_ruku}\n"
                f"verse_range: {verse_range}\n"
                "---\n"
            )
            full_markdown = yaml_header + response_text
            
            # Save file
            os.makedirs(output_ruku_dir, exist_ok=True)
            output_file = os.path.join(output_ruku_dir, src_info['output_name'])
            try:
                with open(output_file, "w", encoding="utf-8") as f_out:
                    f_out.write(full_markdown)
                print(f"  Saved summary to {output_file}")
            except Exception as e:
                print(f"  Error saving to {output_file}: {e}")
                continue
                
            # Update tracking list
            sources_completed.append(src_key)
            entry["sources_completed"] = sources_completed
            ruku_changed = True
            
            # Save todo list progress immediately to ensure we can resume on interruption
            with open(todo_path, "w", encoding="utf-8") as f_todo:
                json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
                
            # Delay to respect free tier rate limit
            time.sleep(args.delay)
            
        # If all 5 sources completed, mark entry as completed
        all_completed = all(s in sources_completed for s in SUMMARY_SOURCES)
        if all_completed and not entry["completed"]:
            entry["completed"] = True
            ruku_changed = True
            
        if ruku_changed:
            with open(todo_path, "w", encoding="utf-8") as f_todo:
                json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
                
        processed_rukus += 1
        
    print("\nProcessing finished.")

if __name__ == "__main__":
    main()
