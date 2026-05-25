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
GEMINI_MODEL = "models/gemini-2.5-flash"

SYSTEM_PROMPT_URDU = """You are an expert Islamic media producer, scriptwriter, and public speaker fluent in Roman Urdu.
Your task is to take a single Ruku's target functional block Tafseer data (written in Urdu script) and the Arabic verse text as input, and generate a conversational, natural, and human-like spoken video script in Roman Urdu.

=== Script Formatting Sequence ===
The generated script must follow this exact linear sequence:

1. **Title line**:
   "Tafseer [surah_name] Ruku [relative_ruku] Verses [verses] - [title]"
   
2. **Arabic Recitation cues**:
   For each verse in the range, list the recitation cue:
   "[Recite Verse X: Arabic Text]"
   (Omit this section entirely if the block does not cover specific verses (e.g., is a Concept block)).
   
3. **Translation line**:
   "Translation: [combined translation of the block in Roman Urdu]"
   (Omit this section entirely if the block does not cover specific verses).
   
4. **Narrator Commentary (Tafseer)**:
   The spoken narrative in Roman Urdu explaining the verses.

=== Narration Guidelines (Human-Like Speaking Style in Roman Urdu) ===
1. **The Hook**: Start the Narrator Commentary immediately with an attention-grabbing, existential, or relatable question/statement in Roman Urdu connected to the block's theme. Do not start with generic greetings.
2. **Pacing and Pauses**: To give the listener cognitive space to absorb the text, insert explicit pacing cues in square brackets (e.g., `[Pause 2 seconds]`) immediately after:
   - The Translation line.
   - Any main exegesis highlight or deep spiritual reflection.
3. **Conversational Tone**: Write in the tone of an engaging narrator, podcaster, or teacher talking directly to a modern audience in Roman Urdu. Use standard Roman Urdu vocabulary (e.g., "Assalamu Alaikum", "aaj hum baat karenge", "farmaate hain"). Avoid stiff or overly complex Urdu words where simpler words are more conversational.
4. **Vocabulary guidelines**: Use standard Urdu terms (e.g., "Namaz" instead of "Salah", "Aayat" instead of "Verse", "Jannat" instead of "Jannah").
5. **Conversational Attribution**: Introduce the commentators smoothly into the talk without breaking the flow (e.g., "Ibn Kathir farmaate hain...", "Maarif-ul-Quran mein likha hai...").
6. **No Screen Directions**: Write ONLY the clean text that the narrator is meant to speak out loud. Do not include visual cues, scene descriptions, or music instructions (except the [Recite Verse] and [Pause] tags).

=== Output Format ===
Return only the Markdown script. Do not wrap it in code blocks or add any conversational intro/outro text. Start directly with the title line.
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

def parse_verse_range(verses_str):
    verses_str = verses_str.strip()
    if '-' in verses_str:
        parts = verses_str.split('-')
        try:
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            return list(range(start, end + 1))
        except ValueError:
            return []
    else:
        try:
            val = int(verses_str.strip())
            return [val]
        except ValueError:
            return []

def generate_track(api_key, script_dir, root_dir, limit, ruku_filter, force_flag, delay):
    print("\n--- [Phase 2: Generate] Generating Roman Urdu scripts ---")
    todo_path = os.path.join(script_dir, "guiding_resources", "todo_script_urdu.json")
    
    if not os.path.exists(todo_path):
        print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
        return
        
    with open(todo_path, "r", encoding="utf-8") as f:
        todo_list = json.load(f)
        
    processed_rukus = 0
    print(f"  Using model: {GEMINI_MODEL}")
    
    for entry in todo_list:
        if limit is not None and processed_rukus >= limit:
            break
            
        if ruku_filter is not None and entry["absolute_ruku"] != ruku_filter:
            continue
            
        if entry["completed"] and not force_flag:
            continue
            
        abs_ruku = entry["absolute_ruku"]
        surah_num = entry["surah_number"]
        surah_name = entry["surah_name"]
        rel_ruku = entry["relative_ruku"]
        
        block_exegesis_dir = os.path.join(
            root_dir, "step2__summary-combined", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", "ur"
        )
        
        wbw_path = os.path.join(
            root_dir, "step0__whole-single", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", "wbw.json"
        )
        
        if not os.path.exists(block_exegesis_dir):
            print(f"  Error: Urdu block exegesis directory {block_exegesis_dir} does not exist. Run split phase first.")
            continue
            
        # List and sort block files
        block_files = []
        for f in os.listdir(block_exegesis_dir):
            if f.startswith("block_") and f.endswith(".md"):
                match = re.search(r'block_(\d+)\.md', f)
                if match:
                    block_files.append((int(match.group(1)), f))
                    
        if not block_files:
            print(f"  Warning: No block exegesis files found in {block_exegesis_dir}. Skipping Ruku {abs_ruku}.")
            continue
            
        block_files.sort(key=lambda x: x[0])
        
        output_ruku_dir = os.path.join(
            root_dir, "step3__combined-script", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", "ur"
        )
        os.makedirs(output_ruku_dir, exist_ok=True)
        
        wbw_data = {}
        if os.path.exists(wbw_path):
            try:
                with open(wbw_path, "r", encoding="utf-8") as f_wbw:
                    wbw_data = json.load(f_wbw)
            except Exception as e:
                print(f"  Warning: Failed to read wbw.json: {e}")
                
        ruku_success = True
        
        for idx, block_filename in block_files:
            block_path = os.path.join(block_exegesis_dir, block_filename)
            
            try:
                with open(block_path, "r", encoding="utf-8") as f_b:
                    block_content = f_b.read().strip()
            except Exception as e:
                print(f"    Error reading block file {block_path}: {e}")
                ruku_success = False
                break
                
            if not block_content:
                continue
                
            lines = block_content.split("\n", 1)
            header_line = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            
            if body.endswith("---"):
                body = body[:-3].strip()
                
            header_text = re.sub(r'^##\s*(Block|بلاک):\s*', '', header_line, flags=re.IGNORECASE).strip()
            
            if " - " in header_text:
                verses_str, theme = header_text.split(" - ", 1)
            else:
                verses_str = header_text
                theme = ""
            verses_str = verses_str.strip()
            theme = theme.strip()
            
            print(f"    Generating Roman Urdu script for Block {idx} (Verses: {verses_str}, Theme: {theme})")
            
            verse_list = parse_verse_range(verses_str)
            arabic_verses = {}
            translations = []
            
            if verse_list and wbw_data:
                for v in verse_list:
                    v_key = str(v)
                    if v_key in wbw_data:
                        words = wbw_data[v_key].get("w", [])
                        verse_arabic = " ".join([w["c"].strip() for w in words if "c" in w]).strip()
                        arabic_verses[v_key] = verse_arabic
                        
                        g_trans = wbw_data[v_key].get("a", {}).get("g_ur", "").strip()
                        if g_trans:
                            translations.append(f"{v}: {g_trans}")
                            
            combined_translation = " ".join(translations)
            
            block_context = {
                "block_metadata": {
                    "surah_number": surah_num,
                    "surah_name": surah_name,
                    "absolute_ruku": abs_ruku,
                    "relative_ruku": rel_ruku,
                    "verses": verses_str,
                    "theme": theme
                },
                "arabic_verses": arabic_verses,
                "translations": combined_translation,
                "exegesis_content": body
            }
            
            ai_response = call_gemini_api(
                GEMINI_MODEL,
                SYSTEM_PROMPT_URDU,
                json.dumps(block_context, ensure_ascii=False, indent=2),
                "step3", abs_ruku, surah_num, surah_name, rel_ruku
            )
            
            if not ai_response:
                print(f"      Error: Failed to generate script for Block {idx}. Skipping Ruku.")
                ruku_success = False
                break
                
            cleaned_script = strip_markdown_code_blocks(ai_response)
            
            yaml_header = (
                "---\n"
                f"surah_number: {surah_num}\n"
                f"surah_name: {surah_name}\n"
                f"absolute_ruku: {abs_ruku}\n"
                f"relative_ruku: {rel_ruku}\n"
                f"verses: {verses_str}\n"
                f"title: \"{theme}\"\n"
                "---\n"
            )
            final_markdown_script = yaml_header + cleaned_script
            
            out_filepath = os.path.join(output_ruku_dir, f"block_{idx}.md")
            try:
                with open(out_filepath, "w", encoding="utf-8") as f_out:
                    f_out.write(final_markdown_script)
                print(f"      Saved script to {out_filepath}")
            except Exception as e:
                print(f"      Error saving block script to {out_filepath}: {e}")
                ruku_success = False
                break
                
            time.sleep(delay)
            
        if ruku_success:
            entry["completed"] = True
            with open(todo_path, "w", encoding="utf-8") as f_todo:
                json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
            processed_rukus += 1

def main():
    parser = argparse.ArgumentParser(description="Generate conversational Roman Urdu video scripts from block exegesis.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed entries.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between successful API calls.")
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
        
    generate_track(api_key, script_dir, root_dir, args.limit, args.ruku, args.force, args.delay)
    print("\nRoman Urdu Step 3 processing finished.")

if __name__ == "__main__":
    main()
