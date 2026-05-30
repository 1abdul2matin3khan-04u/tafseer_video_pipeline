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
from pipeline_utils import call_gemini_api, strip_markdown_code_blocks
# Configurable Gemini Model
GEMINI_MODEL = "models/gemini-3.5-flash"
SYSTEM_PROMPT_ENGLISH = """You are an expert academic Islamic scholar and comparative exegetical analyst.
Read the provided Ruku exegesis summaries from 5 different sources and synthesize them into a single, comprehensive "Combined Tafseer" in Markdown format, organized block by block.
=== Rules ===
1. THEMATIC GROUPING: Divide the Ruku into logical thematic blocks.
   - You MUST start the Ruku with a Ruku Overview block: `## Block: Concept - Ruku Overview`. In this block, provide an academic overview/introduction of the Ruku's themes.
   - Then, divide the actual verses into logical thematic blocks. Demarcate each block with a level-2 header in the format: `## Block: [Verse Range] - [Theme]` (e.g., `## Block: 1-3 - The Oneness of Allah`).
   - You MUST end the Ruku with a Ruku Summary block: `## Block: Concept - Ruku Summary`. In this block, provide a concise summary, key lessons, and takeaways of the Ruku.
   - Blocks can cover a group of adjacent verses. Multiple blocks can cover the same verse range if they discuss different themes.
   - Do not skip any verses in the Ruku.
2. CORE TAFSEER: Under each block, write a section:
   ### Core Tafseer (Ibn Kathir)
   Provide a detailed, comprehensive summary of Tafseer Ibn Kathir for the verses/concepts in this block. Do not omit any key historical details or theological explanations.
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

SYSTEM_PROMPT_SURAH_OVERVIEW = """You are an expert academic Islamic scholar and media producer.
Synthesize the provided Ruku-level summaries of Surah {surah_name} (Surah {surah_num}) into a Surah Overview in Markdown format, organized into one or more thematic blocks.
=== Rules ===
1. THEMATIC GROUPING: Organize the Overview into one or more thematic blocks.
   - Demarcate each block with a level-2 header in the format: ## Block: Concept - [Theme] (e.g. ## Block: Concept - Historical Context, ## Block: Concept - Period of Revelation).
   - In each block, discuss the Surah's historical context, period of revelation, core themes, or structural division.
2. LANGUAGE: The output text must be in clear, formal English.
3. OUTPUT FORMAT: Return only the synthesized Markdown document. Do not wrap it in markdown code blocks (e.g. do not wrap in ```markdown ... ```) or add introductory/concluding conversational filler. Start directly with the first block header.
"""

SYSTEM_PROMPT_SURAH_SUMMARY = """You are an expert academic Islamic scholar and media producer.
Synthesize the provided Ruku-level summaries of Surah {surah_name} (Surah {surah_num}) into a Surah Summary in Markdown format, organized into one or more thematic blocks.
=== Rules ===
1. THEMATIC GROUPING: Organize the Summary into one or more thematic blocks.
   - Demarcate each block with a level-2 header in the format: ## Block: Concept - [Theme] (e.g. ## Block: Concept - Theological Lessons, ## Block: Concept - Key Messages).
   - In each block, detail core theological lessons, practical takeaways, and key messages.
2. LANGUAGE: The output text must be in clear, formal English.
3. OUTPUT FORMAT: Return only the synthesized Markdown document. Do not wrap it in markdown code blocks (e.g. do not wrap in ```markdown ... ```) or add introductory/concluding conversational filler. Start directly with the first block header.
"""

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
def process_track(script_dir, root_dir, limit, ruku_filter, force_flag, delay, interactive=False):
    print("\n===== Starting Step 2 (ENGLISH Track) =====")
    todo_path = os.path.join(script_dir, "guiding_resources", "todo_tafseer_english.json")
    
    if not os.path.exists(todo_path):
        print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
        return
        
    with open(todo_path, "r", encoding="utf-8") as f:
        todo_list = json.load(f)
        
    processed_rukus = 0
    
    # Load rukuDivision mapping to check total standard rukus per surah
    mapping_path = os.path.join(root_dir, "step0__whole-single", "input_resources", "rukuDivision.json")
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path, 'r', encoding='utf-8') as f:
                surahs_mapping = {s["surah_number"]: s for s in json.load(f)}
        except Exception as e:
            print(f"Warning: Failed to load rukuDivision.json: {e}")
            surahs_mapping = {}
    else:
        surahs_mapping = {}
    
    source_keys = {
        "tafseer-ibn-kathir": "ibn_kathir_summary.md",
        "maarif-ul-quran": "maarif_summary.md",
        "tazkir-ul-quran": "tazkir_summary.md",
        "tafsir-as-saadi": "saadi_summary.md",
        "tafsir-bayan-ul-quran": "bayan_ul_quran_summary.md"
    }

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
        
        # Determine total standard rukus for current surah
        surah_info = surahs_mapping.get(surah_num, {})
        total_rukus = len(surah_info.get("verse_ranges", []))
        is_virtual = (rel_ruku == 0 or rel_ruku > total_rukus)
        
        print(f"\n>>> [ENGLISH] Processing {'Virtual ' if is_virtual else ''}Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})")
        
        # Directories
        output_ruku_dir = os.path.join(
            root_dir, "step2__summary-combined", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}"
        )
        
        if is_virtual:
            # Pre-flight dependency check: make sure all standard Rukus for this Surah are completed in Step 1
            standard_entries = [e for e in todo_list if e["surah_number"] == surah_num and 0 < e["relative_ruku"] <= total_rukus]
            standard_entries.sort(key=lambda x: x["relative_ruku"])
            
            missing_rukus = []
            for sr in standard_entries:
                s_rel = sr["relative_ruku"]
                s_abs = sr["absolute_ruku"]
                step1_dir = os.path.join(
                    root_dir, "step1__single-summary", "output_resources", f"surah_{surah_num:03d}", f"ruku_{s_rel}_{s_abs}"
                )
                main_summary_file = os.path.join(step1_dir, "ibn_kathir_summary.md")
                if not os.path.exists(main_summary_file):
                    missing_rukus.append(s_abs)
                    
            if missing_rukus:
                print(f"  [Warning/Dependency] Missing Step 1 summaries for standard Rukus of Surah {surah_num}: {missing_rukus}. Cannot generate virtual Ruku {abs_ruku} yet. Skipping.")
                continue
                
            # Load all standard Ruku summaries
            all_rukus_summaries = {}
            for sr in standard_entries:
                s_rel = sr["relative_ruku"]
                s_abs = sr["absolute_ruku"]
                step1_dir = os.path.join(
                    root_dir, "step1__single-summary", "output_resources", f"surah_{surah_num:03d}", f"ruku_{s_rel}_{s_abs}"
                )
                ruku_summaries = {}
                for k, fname in source_keys.items():
                    fpath = os.path.join(step1_dir, fname)
                    if os.path.exists(fpath):
                        content = parse_markdown_summary(fpath)
                        if content:
                            ruku_summaries[k] = content
                all_rukus_summaries[f"ruku_{s_rel}"] = {
                    "metadata": sr,
                    "summaries": ruku_summaries
                }
                
            input_context = {
                "surah_metadata": {
                    "surah_number": surah_num,
                    "surah_name": surah_name,
                    "total_rukus": total_rukus,
                    "type": "Overview" if rel_ruku == 0 else "Summary"
                },
                "ruku_summaries": all_rukus_summaries
            }
            
            if rel_ruku == 0:
                sys_prompt = SYSTEM_PROMPT_SURAH_OVERVIEW.format(surah_name=surah_name, surah_num=surah_num)
            else:
                sys_prompt = SYSTEM_PROMPT_SURAH_SUMMARY.format(surah_name=surah_name, surah_num=surah_num)
        else:
            # Standard Ruku flow
            step1_ruku_dir = os.path.join(
                root_dir, "step1__single-summary", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}"
            )
            sources_data = {}
            for k, fname in source_keys.items():
                fpath = os.path.join(step1_ruku_dir, fname)
                summary_content = parse_markdown_summary(fpath)
                if summary_content:
                    sources_data[k] = summary_content
                else:
                    print(f"  Warning: Summary file {fname} not found or empty.")
                    
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
            sys_prompt = SYSTEM_PROMPT_ENGLISH
            
        # Call Gemini API
        print(f"  Querying Gemini API ({GEMINI_MODEL}) for combined Markdown exegesis...")
        ai_response = call_gemini_api(
            GEMINI_MODEL,
            json.dumps(input_context, ensure_ascii=False),
            "step2", abs_ruku, surah_num, surah_name, rel_ruku,
            system_instruction=sys_prompt
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
        if interactive and sys.stdin.isatty():
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
    parser.add_argument("--interactive", action="store_true", help="Ask for confirmation before dividing Tafseer into blocks.")
    args = parser.parse_args()
    
    # Determine directories
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    # Load environment variables via api_logger
    keys = api_logger.load_env_keys()
    if not keys:
        print("Error: No GEMINI_API_KEY_1 through GEMINI_API_KEY_7 found in .env file.", file=sys.stderr)
        sys.exit(1)
        
    process_track(script_dir, root_dir, args.limit, args.ruku, args.force, args.delay, args.interactive)
    print("\nEnglish Step 2 processing finished.")
if __name__ == "__main__":
    main()