#!/usr/bin/env python3
import os
import sys
import json
import time
import re
import urllib.request
import urllib.error
import argparse
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_logger
from pipeline_utils import call_gemini_api, strip_markdown_code_blocks, parse_verse_range

# Configurable Gemini Model
GEMINI_MODEL = "models/gemini-3.1-flash-lite"

SYSTEM_PROMPT_URDU = """You are an Islamic video scriptwriter writing in Roman Urdu for a 
modern audience.

Your input is a single Ruku block containing its tafseer content, 
Arabic verse text, and translation. Convert it into a conversational 
spoken video script in Roman Urdu.

=== Output Sequence ===
The script must follow this exact order:

1. Title line:
   "Tafseer [surah_name] Ruku [relative_ruku] Verses [verses] - [title]"

2. Recitation cues — one line per verse:
   "[Recite Verse X: Arabic Text]"
   Omit this section entirely for Concept blocks.

3. Translation line:
   "Translation: [combined translation in Roman Urdu]"
   Omit this section entirely for Concept blocks.
   Follow immediately with: [Pause 2 seconds]

4. Narrator Commentary:
   The spoken explanation of the block's tafseer content.

=== Commentary Rules ===

1. Hook: Open with a single attention-grabbing question or statement 
   directly connected to the block's theme.
   Do NOT open with "Assalamu Alaikum", "Bhai Saab", "Dosto", or any 
   greeting or salutation.
   For Concept blocks (no verses): begin commentary directly with the hook —
   no recitation or translation lines.

2. Coverage: Cover every point from the tafseer content — every hadith, 
   every scholar attribution, every historical detail, every lesson. 
   Omitting content is not permitted.

3. Attributions: Introduce scholars naturally into the flow.
   Write: "Ibn Kathir farmaate hain..." or "Maarif-ul-Quran mein 
   likha hai..." — never break the narrative to announce a source.

4. Pauses: Insert [Pause 2 seconds] after any major theological point 
   or spiritual reflection that needs listener absorption time.

5. Length: Proportional to content depth. A light block: 300–400 words. 
   A dense block with multiple hadith or a historical narrative: 
   600–900 words.

6. Vocabulary: Use conversational Urdu register throughout.
   Namaz, not Salah.
   Aayat, not Verse.
   Jannat, not Jannah.
   Roza, not Sawm.

=== Output Format ===
Return only the script text.
Do not wrap in code blocks.
Do not add any intro or closing remark outside the script.
Start directly with the title line.
"""



def generate_track(script_dir, root_dir, limit, ruku_filter, force_flag, delay):
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
                json.dumps(block_context, ensure_ascii=False),
                "step3", abs_ruku, surah_num, surah_name, rel_ruku,
                system_instruction=SYSTEM_PROMPT_URDU
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
    generate_track(script_dir, root_dir, args.limit, args.ruku, args.force, args.delay)
    print("\nRoman Urdu Step 3 processing finished.")

if __name__ == "__main__":
    main()
