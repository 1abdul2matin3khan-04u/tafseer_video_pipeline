#!/usr/bin/env python3
import os
import sys
import json
import re
import time
import urllib.request
import urllib.error
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_logger
from pipeline_utils import call_gemini_api, strip_markdown_code_blocks

# Configurable Gemini Model (using flash-lite to prevent free-tier rate limits)
GEMINI_MODEL = "models/gemini-3.5-flash"

SYSTEM_PROMPT_ROMAN_URDU_COMBINE = """You are a comparative Islamic exegetical analyst writing in Roman Urdu.
Synthesize the provided summaries for Surah {surah_name} (Surah {surah_num}) Ruku {relative_ruku} (Verses {verses}) from 5 sources into a single Combined Tafseer in Markdown, organized block by block in Roman Urdu.

<block_structure>
Divide the Ruku into logical thematic blocks in sequential order:
1. Start with: ## Block: Concept - Ruku Overview
   - Provide an academic overview and introduction of the Ruku's themes in Roman Urdu.
2. Verse blocks: ## Block: [Verse Range] - [Theme in Roman Urdu]  (e.g., ## Block: 1-3 - Tauheed)
   - Blocks must follow the exact sequential order of the content as it appears in the sources. Do not reorder or extract content out of its original position.
   - Do not skip any verse.
3. End with: ## Block: Concept - Ruku Summary
   - Provide a concise summary, key lessons, and takeaways of the Ruku in Roman Urdu.
</block_structure>

<under_each_block>
### Core Tafseer (Ibn Kathir)
Write a complete account of Ibn Kathir's explanation. Preserve every hadith, narration, historical detail, and theological conclusion. Do not compress.

### Additional Interpretations
List only what each source adds beyond Ibn Kathir:
- **Maarif-ul-Quran**: [unique addition]
- **Tazkir-ul-Quran**: [unique addition]
- **Tafsir As-Saadi**: [unique addition]
- **Tafsir Bayan-ul-Quran**: [unique addition]

Rules:
- Include only points not already covered by Ibn Kathir.
- Omit an author's line entirely if they add nothing unique.
- If two authors share the same unique point, attribute both in one line.
- If a source contradicts Ibn Kathir, note it explicitly: "[Author] differs here — [their position]."
</under_each_block>

<language_rules>
Write entirely in Roman Urdu using the Latin alphabet.
Example register: "Allah farmaate hain", "Ibn Kathir kehte hain", "insani fitrat", "Namaz", "Jannat", "Aayat".
Do NOT use Urdu script or Arabic script characters anywhere in the commentary text. Arabic terms may appear only inside direct quotations of verse text if needed.
</language_rules>

<output_format>
Return only the Markdown document. Do not wrap in code blocks. No preamble or closing remarks. Start directly with the first block header.
</output_format>
"""

SYSTEM_PROMPT_SURAH_OVERVIEW_ROMAN_URDU = """You are a comparative Islamic exegetical analyst writing in Roman Urdu.
Synthesize the provided Ruku-level overview blocks of Surah {surah_name} (Surah {surah_num}) into a Surah Overview in Markdown format, organized into one or more thematic blocks in Roman Urdu.
=== Rules ===
1. THEMATIC GROUPING: Organize the Overview into one or more thematic blocks.
   - Demarcate each block with a level-2 header in the format: ## Block: Concept - [Theme in Roman Urdu] (e.g. ## Block: Concept - Shaan-e-Nuzool, ## Block: Concept - Markazi Mazameen).
   - In each block, discuss the Surah's historical context, period of revelation, core themes, or structural division in Roman Urdu.
2. LANGUAGE: Write entirely in Roman Urdu using the Latin alphabet. Do not use Urdu or Arabic script characters.
3. OUTPUT FORMAT: Return only the synthesized Markdown document. Do not wrap it in markdown code blocks or add introductory/concluding conversational filler. Start directly with the first block header.
"""

SYSTEM_PROMPT_SURAH_SUMMARY_ROMAN_URDU = """You are a comparative Islamic exegetical analyst writing in Roman Urdu.
Synthesize the provided Ruku-level summary blocks of Surah {surah_name} (Surah {surah_num}) into a Surah Summary in Markdown format, organized into one or more thematic blocks in Roman Urdu.
=== Rules ===
1. THEMATIC GROUPING: Organize the Summary into one or more thematic blocks.
   - Demarcate each block with a level-2 header in the format: ## Block: Concept - [Theme in Roman Urdu] (e.g. ## Block: Concept - Khasosi Asbaq, ## Block: Concept - Ahem Hidayat).
   - In each block, detail core theological lessons, practical takeaways, and key messages in Roman Urdu.
2. LANGUAGE: Write entirely in Roman Urdu using the Latin alphabet. Do not use Urdu or Arabic script characters.
3. OUTPUT FORMAT: Return only the synthesized Markdown document. Do not wrap it in markdown code blocks or add introductory/concluding conversational filler. Start directly with the first block header.
"""

def parse_markdown_summary(filepath):
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
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
    print("\n===== Starting Step 2 (ROMAN URDU Track) =====")
    todo_path = os.path.join(script_dir, "guiding_resources", "todo_tafseer_urdu.json")

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
        "tafsir-bayan-ul-quran": "bayan_ul_quran_summary.md",
    }

    for entry in todo_list:
        if limit is not None and processed_rukus >= limit:
            print(
                f"Reached processing limit of {limit} Rukus for ROMAN URDU track. Stopping."
            )
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

        print(
            f"\n>>> [ROMAN URDU] Processing {'Virtual ' if is_virtual else ''}Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})"
        )

        # Directories
        output_ruku_dir = os.path.join(
            root_dir,
            "step2__summary-combined",
            "output_resources",
            f"surah_{surah_num:03d}",
            f"ruku_{rel_ruku}_{abs_ruku}",
        )

        if is_virtual:
            # Pre-flight dependency check: make sure all standard Rukus for this Surah are completed in Step 2
            standard_entries = [e for e in todo_list if e["surah_number"] == surah_num and 0 < e["relative_ruku"] <= total_rukus]
            standard_entries.sort(key=lambda x: x["relative_ruku"])
            
            missing_rukus = []
            for sr in standard_entries:
                s_rel = sr["relative_ruku"]
                s_abs = sr["absolute_ruku"]
                s_out_dir = os.path.join(
                    root_dir, "step2__summary-combined", "output_resources", f"surah_{surah_num:03d}", f"ruku_{s_rel}_{s_abs}"
                )
                tafseer_file = os.path.join(s_out_dir, "tafseer_roman-urdu.md")
                ur_dir = os.path.join(s_out_dir, "ur")
                
                # Check for Step 2 files
                if not os.path.exists(tafseer_file) or not os.path.exists(ur_dir):
                    missing_rukus.append(s_abs)
                    continue
                
                block_files = [f for f in os.listdir(ur_dir) if f.startswith("block_") and f.endswith(".md")]
                if not block_files:
                    missing_rukus.append(s_abs)
                    continue
                
                if rel_ruku == 0:
                    # Overview needs block_1.md (Ruku Overview)
                    if not os.path.exists(os.path.join(ur_dir, "block_1.md")):
                        missing_rukus.append(s_abs)
            
            if missing_rukus:
                print(f"  [Warning/Dependency] Missing Step 2 standard Ruku Tafseer/Blocks for Surah {surah_num}: {missing_rukus}. Cannot generate virtual Ruku {abs_ruku} yet. Skipping.")
                continue
                
            # Load blocks based on type (Overview or Summary)
            if rel_ruku == 0:
                # Load all ruku overview blocks (block_1.md)
                ruku_overview_blocks = {}
                for sr in standard_entries:
                    s_rel = sr["relative_ruku"]
                    s_abs = sr["absolute_ruku"]
                    block_1_path = os.path.join(
                        root_dir, "step2__summary-combined", "output_resources", f"surah_{surah_num:03d}", f"ruku_{s_rel}_{s_abs}", "ur", "block_1.md"
                    )
                    if os.path.exists(block_1_path):
                        try:
                            with open(block_1_path, "r", encoding="utf-8") as f_in:
                                content = f_in.read().strip()
                            ruku_overview_blocks[f"ruku_{s_rel}"] = content
                        except Exception as e:
                            print(f"  Warning: Failed to read {block_1_path}: {e}")
                
                input_context = {
                    "surah_metadata": {
                        "surah_number": surah_num,
                        "surah_name": surah_name,
                        "total_rukus": total_rukus,
                        "type": "Overview"
                    },
                    "ruku_overview_blocks": ruku_overview_blocks
                }
                sys_prompt = SYSTEM_PROMPT_SURAH_OVERVIEW_ROMAN_URDU.format(surah_name=surah_name, surah_num=surah_num)
            else:
                # Load all ruku summary blocks (final block_K.md)
                ruku_summary_blocks = {}
                for sr in standard_entries:
                    s_rel = sr["relative_ruku"]
                    s_abs = sr["absolute_ruku"]
                    ur_dir = os.path.join(
                        root_dir, "step2__summary-combined", "output_resources", f"surah_{surah_num:03d}", f"ruku_{s_rel}_{s_abs}", "ur"
                    )
                    if os.path.exists(ur_dir):
                        b_files = []
                        for f in os.listdir(ur_dir):
                            if f.startswith("block_") and f.endswith(".md"):
                                match = re.search(r'block_(\d+)\.md', f)
                                if match:
                                    b_files.append((int(match.group(1)), f))
                        if b_files:
                            b_files.sort(key=lambda x: x[0])
                            final_block_filename = b_files[-1][1]
                            final_block_path = os.path.join(ur_dir, final_block_filename)
                            try:
                                with open(final_block_path, "r", encoding="utf-8") as f_in:
                                    content = f_in.read().strip()
                                ruku_summary_blocks[f"ruku_{s_rel}"] = content
                            except Exception as e:
                                print(f"  Warning: Failed to read {final_block_path}: {e}")
                
                input_context = {
                    "surah_metadata": {
                        "surah_number": surah_num,
                        "surah_name": surah_name,
                        "total_rukus": total_rukus,
                        "type": "Summary"
                    },
                    "ruku_summary_blocks": ruku_summary_blocks
                }
                sys_prompt = SYSTEM_PROMPT_SURAH_SUMMARY_ROMAN_URDU.format(surah_name=surah_name, surah_num=surah_num)
        else:
            # Standard Ruku flow
            step1_ruku_dir = os.path.join(
                root_dir,
                "step1__single-summary",
                "output_resources",
                f"surah_{surah_num:03d}",
                f"ruku_{rel_ruku}_{abs_ruku}",
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
                    "verse_range": verse_range,
                },
                "sources": sources_data,
            }
            sys_prompt = SYSTEM_PROMPT_ROMAN_URDU_COMBINE.format(
                surah_name=surah_name,
                surah_num=surah_num,
                relative_ruku=rel_ruku,
                verses=verse_range
            )

        print(
            f"  Querying Gemini API ({GEMINI_MODEL}) for combined Roman Urdu Tafseer..."
        )
        ai_response = call_gemini_api(
            GEMINI_MODEL,
            json.dumps(input_context, ensure_ascii=False),
            "step2",
            abs_ruku,
            surah_num,
            surah_name,
            rel_ruku,
            system_instruction=sys_prompt,
        )

        if not ai_response:
            print(
                f"  Error: Failed to get AI response for combining Ruku {abs_ruku}. Skipping."
            )
            continue

        cleaned_markdown = strip_markdown_code_blocks(ai_response)

        # Ensure YAML frontmatter is present at the beginning of output
        if not cleaned_markdown.startswith("---"):
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
        else:
            final_output_content = cleaned_markdown

        out_filename = "tafseer_urdu.md"
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
                user_input = (
                    input(
                        f"\n[?] Ready to divide the Urdu Tafseer into blocks? Press Enter or type 'yes' to proceed: "
                    )
                    .strip()
                    .lower()
                )
                should_split = user_input == "" or user_input.startswith("y")
            except (EOFError, KeyboardInterrupt):
                should_split = True

        if should_split:
            block_regex = r"^## (بلاک|Block):\s*"
            blocks_parts = re.split(
                block_regex, cleaned_markdown, flags=re.MULTILINE | re.IGNORECASE
            )

            if len(blocks_parts) <= 1:
                print(f"  Warning: No block headers found in Tafseer. Cannot split.")
            else:
                block_exegesis_dir = os.path.join(output_ruku_dir, "ur")
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
                        body_text = blocks_parts[i + 1].strip()

                        block_markdown = f"## {keyword}: {body_text}\n"
                        block_file_path = os.path.join(
                            block_exegesis_dir, f"block_{blocks_count}.md"
                        )

                        try:
                            with open(block_file_path, "w", encoding="utf-8") as f_block:
                                f_block.write(block_markdown)
                            print(f"    Saved block: block_{blocks_count}.md")
                        except Exception as e:
                            print(
                                f"    Error saving block exegesis to {block_file_path}: {e}"
                            )
                    i += 2
                print(
                    f"  Successfully divided into {blocks_count} block files in {block_exegesis_dir}"
                )

        # Update progress tracking
        entry["completed"] = True
        with open(todo_path, "w", encoding="utf-8") as f_todo:
            json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)

        processed_rukus += 1
        time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(
        description="Generate combined structured Tafseers directly in Roman Urdu."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit the number of Rukus to process."
    )
    parser.add_argument(
        "--ruku", type=int, default=None, help="Process a specific absolute Ruku index."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reprocessing of already completed entries.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between successful API calls (default: 1.0).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Ask for confirmation before dividing Tafseer into blocks.",
    )
    args = parser.parse_args()

    # Determine directories
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)

    # Load environment variables via api_logger
    keys = api_logger.load_env_keys()
    if not keys:
        print(
            "Error: No GEMINI_API_KEY_1 through GEMINI_API_KEY_7 found in .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    process_track(
        script_dir, root_dir, args.limit, args.ruku, args.force, args.delay, args.interactive
    )
    print("\nUrdu Step 2 processing finished.")


if __name__ == "__main__":
    main()
