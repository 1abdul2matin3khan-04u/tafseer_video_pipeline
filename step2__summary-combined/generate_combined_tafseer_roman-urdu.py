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

You will receive compressed summaries of a single Ruku from 5 Tafseer sources:
- Ibn Kathir (English)
- Maarif-ul-Quran (English)
- Tazkir-ul-Quran (English)
- Tafsir As-Saadi (Urdu script)
- Tafsir Bayan-ul-Quran (Urdu script)

Synthesize them into one combined Tafseer in Roman Urdu, divided into 
thematic blocks.

=== Block Structure ===
Divide the Ruku into logical thematic blocks. Each block covers a 
dominant theme and, where applicable, a verse range.

Blocks must follow the exact sequential order of the content as it 
appears in the sources. Do not reorganize, reorder, or extract content 
out of its original position. If an overview appears before verse 
explanation, it is Block 1. If a story appears at the end, it stays 
at the end.

Demarcate each block with:
## Block: [N] - [Verse Range] - [Theme in Roman Urdu]

If a block has no specific verse range (e.g. an overview, a concluding 
story, or a conceptual discussion), use:
## Block: [N] - Concept - [Theme in Roman Urdu]

=== Under Each Block ===

### Core Tafseer (Ibn Kathir)
Write a complete account of Ibn Kathir's explanation for these verses.
Preserve every hadith, narration, historical detail, and theological 
conclusion from his summary. Do not compress here.

### Additional Interpretations
List only what each of the remaining 4 sources adds beyond Ibn Kathir.

Format:
- **Maarif-ul-Quran**: [unique addition, or omit this line entirely]
- **Tazkir-ul-Quran**: [unique addition, or omit this line entirely]
- **Tafsir As-Saadi**: [unique addition, or omit this line entirely]
- **Tafsir Bayan-ul-Quran**: [unique addition, or omit this line entirely]

Rules for Additional Interpretations:
- A point already present in Ibn Kathir's section must not appear again 
  under any author.
- If an author's entire contribution for this block is already covered by 
  Ibn Kathir, omit that author's line completely.
- If two authors make the same unique point, attribute it to both in one 
  line rather than repeating it.
- If sources contradict Ibn Kathir on a point, note the disagreement 
  explicitly: "[Author] differs here — [their position]."

=== Language ===
Write entirely in Roman Urdu using the Latin alphabet.
Example register: "Allah farmaate hain", "Ibn Kathir kehte hain", 
"insani fitrat", "Namaz", "Jannat", "Aayat".
Do not use Urdu script or Arabic script characters anywhere in the 
commentary text. Arabic terms may appear only inside direct quotations 
of verse text if needed.

=== Output Format ===
Return only the Markdown document.
Do not wrap in code blocks.
Do not add any introduction or closing remark.
Start directly with the first block header."""

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

        print(
            f"\n>>> [ROMAN URDU] Processing Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})"
        )

        # Directories
        output_ruku_dir = os.path.join(
            root_dir,
            "step2__summary-combined",
            "output_resources",
            f"surah_{surah_num:03d}",
            f"ruku_{rel_ruku}_{abs_ruku}",
        )

        step1_ruku_dir = os.path.join(
            root_dir,
            "step1__single-summary",
            "output_resources",
            f"surah_{surah_num:03d}",
            f"ruku_{rel_ruku}_{abs_ruku}",
        )

        # Extract Block Alignment Headers from English Track
        en_tafseer_path = os.path.join(output_ruku_dir, "tafseer_english.md")
        if not os.path.exists(en_tafseer_path):
            print(
                f"  Error: English combined Tafseer not found at {en_tafseer_path}. Cannot align blocks. Run English track first."
            )
            continue

        try:
            with open(en_tafseer_path, "r", encoding="utf-8") as f_en:
                en_content = f_en.read()
        except Exception as e:
            print(f"  Error reading {en_tafseer_path}: {e}. Skipping Ruku.")
            continue

        block_headers = []
        for line in en_content.splitlines():
            if line.strip().startswith("## Block:"):
                block_headers.append(line.strip())

        if not block_headers:
            print(
                f"  Warning: No block headers found in {en_tafseer_path}. Using default grouping."
            )
            block_headers_instr = ""
        else:
            block_headers_str = "\n".join(f"- {h}" for h in block_headers)
            block_headers_instr = (
                f"\n=== Block Structure Alignment ===\n"
                f"You MUST organize the Roman Urdu Tafseer into the exact same blocks as the English track. "
                f"Here are the exact block headers you must use (translate the theme at the end of the header to Roman Urdu, "
                f"but keep the verse range/structure identical, e.g. '## Block: 1 - Basmalah'):\n"
                f"{block_headers_str}\n"
            )

        # Load summaries from Step 1
        sources_data = {}
        source_keys = {
            "tafseer-ibn-kathir": "ibn_kathir_summary.md",
            "maarif-ul-quran": "maarif_summary.md",
            "tazkir-ul-quran": "tazkir_summary.md",
            "tafsir-as-saadi": "saadi_summary.md",
            "tafsir-bayan-ul-quran": "bayan_ul_quran_summary.md",
        }

        for k, fname in source_keys.items():
            fpath = os.path.join(step1_ruku_dir, fname)
            summary_content = parse_markdown_summary(fpath)
            if summary_content:
                sources_data[k] = summary_content
            else:
                print(f"  Warning: Summary file {fname} not found or empty.")

        # Build simplified user context for AI containing all 5 summaries
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
            system_instruction=SYSTEM_PROMPT_ROMAN_URDU_COMBINE + block_headers_instr,
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

    # English dependency check (Urdu depends on English block headers)
    if args.ruku is not None:
        todo_path = os.path.join(script_dir, "guiding_resources", "todo_tafseer_urdu.json")
        if os.path.exists(todo_path):
            try:
                with open(todo_path, "r", encoding="utf-8") as f:
                    todo_list = json.load(f)
                target_entry = next((e for e in todo_list if e["absolute_ruku"] == args.ruku), None)
                if target_entry:
                    surah_num = target_entry["surah_number"]
                    rel_ruku = target_entry["relative_ruku"]
                    en_tafseer_path = os.path.join(
                        root_dir, "step2__summary-combined", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{args.ruku}", "tafseer_english.md"
                    )
                    if not os.path.exists(en_tafseer_path):
                        print(f"Error: English tafseer must be generated before Urdu.", file=sys.stderr)
                        print(f"Run generate_combined_tafseer_english.py first.", file=sys.stderr)
                        print(f"Expected file: {en_tafseer_path}", file=sys.stderr)
                        sys.exit(1)
            except Exception as e:
                print(f"Warning: English dependency check failed: {e}", file=sys.stderr)

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
