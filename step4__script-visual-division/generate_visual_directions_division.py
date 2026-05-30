#!/usr/bin/env python3
"""
generate_visual_directions_division.py
Unified script to generate scene-by-scene visual directions with structured visual groups.
Supports English and Roman Urdu tracks via the --lang argument.
"""

import os
import sys
import json
import time
import re
import argparse
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_logger
from pipeline_utils import call_gemini_api
from process_visual_groups import flatten_visual_groups, subdivide_by_visual_groups

# Configurable Gemini Model
GEMINI_MODEL = "models/gemini-3.1-flash-lite"

SCENE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "scenes": {
            "type": "ARRAY",
            "description": "List of scene rows in sequential order.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "scene_no": {
                        "type": "INTEGER",
                        "description": "The sequential scene index starting from 1."
                    },
                    "script": {
                        "type": "STRING",
                        "description": "The exact spoken voiceover text or recitation action for this specific scene."
                    },
                    "remarks": {
                        "type": "STRING",
                        "description": "Notes on delivery tone, pacing, emphasis, and sound effects cues in English."
                    }
                },
                "required": ["scene_no", "script", "remarks"]
            }
        }
    },
    "required": ["scenes"]
}

VISUAL_GROUPS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "visual_groups": {
            "type": "ARRAY",
            "description": "Structured visual aids for commentary scenes. Each group spans consecutive scenes and progressively reveals content.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "group_id": {
                        "type": "STRING",
                        "description": "Unique identifier like vg_1, vg_2, etc."
                    },
                    "type": {
                        "type": "STRING",
                        "description": "Visual type: bullets, table, timeline, comparison, or hierarchy."
                    },
                    "theme": {
                        "type": "STRING",
                        "description": "Color theme: warning, mercy, historical, or default."
                    },
                    "title": {
                        "type": "STRING",
                        "description": "Short title for the visual (max 60 chars)."
                    },
                    "scene_range": {
                        "type": "ARRAY",
                        "description": "Two integers [start_scene_no, end_scene_no].",
                        "items": {"type": "INTEGER"}
                    },
                    "reveals": {
                        "type": "ARRAY",
                        "description": "Cumulative reveal counts, one per scene in the range.",
                        "items": {"type": "INTEGER"}
                    },
                    "items": {
                        "type": "ARRAY",
                        "description": "For bullets type: list of bullet point strings.",
                        "items": {"type": "STRING"}
                    },
                    "headers": {
                        "type": "ARRAY",
                        "description": "For table type: column header strings.",
                        "items": {"type": "STRING"}
                    },
                    "rows": {
                        "type": "ARRAY",
                        "description": "For table type: array of row arrays.",
                        "items": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"}
                        }
                    },
                    "events": {
                        "type": "ARRAY",
                        "description": "For timeline type: array of event objects.",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "label": {"type": "STRING"},
                                "description": {"type": "STRING"}
                            },
                            "required": ["label", "description"]
                        }
                    },
                    "left_label": {
                        "type": "STRING",
                        "description": "For comparison type: left column label."
                    },
                    "right_label": {
                        "type": "STRING",
                        "description": "For comparison type: right column label."
                    },
                    "points": {
                        "type": "ARRAY",
                        "description": "For comparison type: comparison point pairs.",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "left": {"type": "STRING"},
                                "right": {"type": "STRING"}
                            },
                            "required": ["left", "right"]
                        }
                    },
                    "root": {
                        "type": "STRING",
                        "description": "For hierarchy type: root node label."
                    },
                    "children": {
                        "type": "ARRAY",
                        "description": "For hierarchy type: child nodes with optional grandchildren.",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "label": {"type": "STRING"},
                                "children": {
                                     "type": "ARRAY",
                                     "items": {"type": "STRING"}
                                }
                            },
                            "required": ["label", "children"]
                        }
                    }
                },
                "required": [
                    "group_id", "type", "theme", "title", "scene_range", "reveals",
                    "items", "headers", "rows", "events", "left_label", "right_label",
                    "points", "root", "children"
                ]
            }
        }
    },
    "required": ["visual_groups"]
}


def parse_markdown_with_yaml(filepath):
    if not os.path.exists(filepath):
        return None, None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()

        yaml_metadata = {}
        script_text = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                yaml_block = parts[1].strip()
                script_text = parts[2].strip()

                # Parse simple YAML key-values
                for line in yaml_block.split("\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip()
                        v = v.strip()
                        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                            v = v[1:-1]
                        yaml_metadata[k] = v
        return yaml_metadata, script_text
    except Exception as e:
        print(f"Error parsing script file {filepath}: {e}")
        return None, None


def sync_block_todo_list(todo_path, root_dir, lang):
    """
    Reads the todo list, expands Ruku-level entries into block-level entries
    by scanning the Step 3 output directories, and saves the updated list.
    """
    if not os.path.exists(todo_path):
        return []

    try:
        with open(todo_path, "r", encoding="utf-8") as f:
            raw_entries = json.load(f)
    except Exception as e:
        print(f"Error reading todo file {todo_path}: {e}")
        return []

    # Map of ruku-level metadata (absolute_ruku -> entry metadata)
    ruku_meta = {}
    block_entries = []

    # Parse existing entries
    for entry in raw_entries:
        abs_ruku = entry.get("absolute_ruku")
        if abs_ruku is None:
            continue
        if "block_no" in entry:
            block_entries.append(entry)
        else:
            ruku_meta[abs_ruku] = entry

    # Scan step 3 combined-script output directory for block files
    step3_out_dir = os.path.join(root_dir, "step3__combined-script", "output_resources")
    
    if os.path.exists(step3_out_dir):
        for surah_dir in os.listdir(step3_out_dir):
            surah_path = os.path.join(step3_out_dir, surah_dir)
            if not os.path.isdir(surah_path):
                continue
            for ruku_dir in os.listdir(surah_path):
                match_ruku = re.search(r'ruku_(\d+)_(\d+)', ruku_dir)
                if not match_ruku:
                    continue
                rel_ruku = int(match_ruku.group(1))
                abs_ruku = int(match_ruku.group(2))
                
                lang_path = os.path.join(surah_path, ruku_dir, lang)
                if not os.path.exists(lang_path) or not os.path.isdir(lang_path):
                    continue
                
                for f_name in os.listdir(lang_path):
                    if f_name.startswith("block_") and f_name.endswith(".md"):
                        match_blk = re.search(r'block_(\d+)\.md', f_name)
                        if match_blk:
                            blk_no = int(match_blk.group(1))
                            
                            exists = any(
                                e["absolute_ruku"] == abs_ruku and e["block_no"] == blk_no
                                for e in block_entries
                            )
                            if not exists:
                                r_meta = ruku_meta.get(abs_ruku, {})
                                surah_num = r_meta.get("surah_number", int(surah_dir.split("_")[-1]) if "_" in surah_dir else 1)
                                surah_name = r_meta.get("surah_name", surah_dir.split("_")[-1] if "_" in surah_dir else "Surah")
                                verse_range = r_meta.get("verse_range", "")
                                completed_status = r_meta.get("completed", False)
                                
                                block_entries.append({
                                    "absolute_ruku": abs_ruku,
                                    "surah_number": surah_num,
                                    "surah_name": surah_name,
                                    "relative_ruku": rel_ruku,
                                    "verse_range": verse_range,
                                    "block_no": blk_no,
                                    "completed": completed_status
                                })

    block_entries.sort(key=lambda x: (x["absolute_ruku"], x["block_no"]))

    try:
        with open(todo_path, "w", encoding="utf-8") as f:
            json.dump(block_entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving updated todo file {todo_path}: {e}")

    return block_entries


def process_track(lang, script_dir, root_dir, limit, ruku_filter, block_filter, force_flag, delay, max_narrative_scenes):
    lang_label = "English" if lang == "en" else "Roman Urdu"
    print(f"\n===== Starting Step 4 ({lang_label.upper()} Track - Visual Groups) =====")
    todo_file = f"todo_visuals_english.json" if lang == "en" else f"todo_visuals_urdu.json"
    todo_path = os.path.join(script_dir, "guiding_resources", todo_file)

    if not os.path.exists(todo_path):
        print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
        return

    # Prompts tailored for the specific language track
    prompt_scene_breakdown = f"""You are a video script editor breaking a {lang_label} Tafseer block into individual spoken scenes.

=== Scene Rules ===

1. Scene 1 — Title:
   Contains only the title line from the script.

2. Recitation and Translation scenes:
   Each [Recite Verse X: ...] tag becomes its own scene.
   The Translation line becomes its own scene.
   Preserve the exact bracketed tag and prefix in the script field.
   Do not alter, clean, or paraphrase them.

3. Commentary scenes:
   Break narrator commentary into individual spoken phrases of 10–18 
   words each (approximately 5–10 seconds of speech).
   One natural sentence or clause per scene.

4. remarks field:
   Write delivery notes in English — tone, pacing, emphasis, 
   sound effect cues.
"""

    prompt_visual_groups = f"""You are a motion graphics director designing visual aids for a {lang_label} Tafseer video.

You will receive a block script and its scene breakdown. Your task is to assign structured visual groups to commentary scenes only.

=== What Visual Groups Are ===
Graphics that occupy the top 75% of screen while the narrator speaks.
They do not apply to title, recitation, or translation scenes.

=== Decision Process ===
Read all commentary scenes first. Identify segments where a visual 
would genuinely aid comprehension. Not every scene needs one — 
reflective or transitional statements stay narrative-only.

=== Available Types ===
bullets — key points, characteristics, or lessons as a list.
table — columnar data, attribute comparisons, structured categories.
timeline — chronological sequence of events or prophetic narrative.
comparison — side-by-side contrast of two groups, states, or concepts.
hierarchy — a tree of categories or branches of a concept.

=== Themes ===
warning — punishment, hellfire, theological consequences.
mercy — guidance, blessings, forgiveness, paradise.
historical — prophetic stories, historical events, narrative passages.
default — general discussion, neutral content.

=== Progressive Reveal ===
Each group spans multiple consecutive commentary scenes.
The graphic builds gradually across its scene range.
reveals is an array of cumulative integers, one per scene in the range.
Values may repeat (hold) or jump by more than one.

=== Constraints ===
reveals length must equal (scene_range[1] - scene_range[0] + 1).
Maximum reveal value must not exceed item count of the visual type.
Scene ranges must not overlap between groups.
group_id must be unique: vg_1, vg_2, etc.
For unused fields output empty arrays [] or empty strings "".
Visual titles must be in English.
"""

    # Expand Ruku todo list into block todo list dynamically
    todo_list = sync_block_todo_list(todo_path, root_dir, lang)

    processed_blocks = 0

    for entry in todo_list:
        if limit is not None and processed_blocks >= limit:
            print(f"Reached processing limit of {limit} Blocks for {lang_label} track. Stopping.")
            break

        if ruku_filter is not None and entry["absolute_ruku"] != ruku_filter:
            continue

        if block_filter is not None and entry["block_no"] != block_filter:
            continue

        if entry["completed"] and not force_flag:
            continue

        abs_ruku = entry["absolute_ruku"]
        surah_num = entry["surah_number"]
        surah_name = entry["surah_name"]
        rel_ruku = entry["relative_ruku"]
        idx = entry["block_no"]

        print(f"\n>>> [{lang_label.upper()}] Generating Visual Groups for Ruku {abs_ruku} Block {idx} (Surah {surah_num:03d} {surah_name})")

        input_script_dir = os.path.join(
            root_dir, "step3__combined-script", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", lang
        )

        filename = f"block_{idx}.md"
        script_filepath = os.path.join(input_script_dir, filename)

        if not os.path.exists(script_filepath):
            print(f"  Warning: Script block file not found: {script_filepath}. Skipping.")
            continue

        output_dir = os.path.join(
            root_dir, "step4__script-visual-division", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", lang
        )
        os.makedirs(output_dir, exist_ok=True)

        yaml_meta, script_text = parse_markdown_with_yaml(script_filepath)

        if not script_text:
            print(f"    Error: Failed to parse content from {script_filepath}. Skipping block.")
            continue

        verses = yaml_meta.get("verses", "Concept")
        title = yaml_meta.get("title", "")
        # Compact plain text representation of the input payload
        input_payload_str = f"Block: {verses} - {title}\n\n{script_text}"

        raw_filename = f"block_{idx}_raw.json"
        raw_filepath = os.path.join(output_dir, raw_filename)
        json_data = None

        if os.path.exists(raw_filepath) and not force_flag:
            try:
                with open(raw_filepath, "r", encoding="utf-8") as f_raw:
                    json_data = json.load(f_raw)
                print(f"    Reusing cached raw output from {raw_filepath}")
            except Exception as e:
                print(f"    Error reading cached raw output from {raw_filepath}: {e}, calling API...")

        if json_data is None:
            # Call 1: Scene Breakdown
            print(f"    [Call 1] Generating scene breakdown for Block {idx}...")
            ai_response_scenes = call_gemini_api(
                GEMINI_MODEL,
                input_payload_str,
                "step4_scenes", abs_ruku, surah_num, surah_name, rel_ruku,
                system_instruction=prompt_scene_breakdown,
                response_schema=SCENE_SCHEMA
            )

            if not ai_response_scenes:
                print(f"    Error: Failed to generate scenes breakdown for Block {idx}. Skipping.")
                continue

            try:
                scenes_data = json.loads(ai_response_scenes)
                scenes = scenes_data.get("scenes", [])
            except Exception as e:
                print(f"    Error parsing scenes response JSON for Block {idx}: {e}")
                continue

            # Call 2: Visual Groups
            print(f"    [Call 2] Designing progressive visual groups for Block {idx}...")
            vg_input_context = f"Block Script:\n{input_payload_str}\n\nScene Breakdown:\n{json.dumps(scenes, ensure_ascii=False)}"
            ai_response_vg = call_gemini_api(
                GEMINI_MODEL,
                vg_input_context,
                "step4_visuals", abs_ruku, surah_num, surah_name, rel_ruku,
                system_instruction=prompt_visual_groups,
                response_schema=VISUAL_GROUPS_SCHEMA
            )

            if not ai_response_vg:
                print(f"    Error: Failed to generate visual groups for Block {idx}. Skipping.")
                continue

            try:
                vg_data = json.loads(ai_response_vg)
                visual_groups = vg_data.get("visual_groups", [])
            except Exception as e:
                print(f"    Error parsing visual groups response JSON for Block {idx}: {e}")
                continue

            # Combine output to keep backward compatibility
            json_data = {
                "scenes": scenes,
                "visual_groups": visual_groups
            }

            # Save raw Gemini output
            try:
                with open(raw_filepath, "w", encoding="utf-8") as f_raw:
                    json.dump(json_data, f_raw, ensure_ascii=False, indent=2)
                print(f"    Saved raw output to {raw_filepath}")
            except Exception as e:
                print(f"    Error writing raw output to {raw_filepath}: {e}")
                continue

        # --- Process visual_groups: flatten into per-scene layouts ---
        scenes = json_data.get("scenes", [])
        visual_groups = json_data.get("visual_groups", [])
        print(f"    Block {idx}: {len(scenes)} scenes, {len(visual_groups)} visual groups")

        scenes = flatten_visual_groups(scenes, visual_groups)

        # --- Subdivide by visual_group boundaries ---
        block_metadata = {
            "surah_number": surah_num,
            "surah_name": surah_name,
            "absolute_ruku": abs_ruku,
            "relative_ruku": rel_ruku,
            "verses": yaml_meta.get("verses", "Concept"),
            "ruku_heading": yaml_meta.get("title", ""),
            "block_no": idx
        }

        subblocks = subdivide_by_visual_groups(
            scenes, visual_groups, block_metadata, max_narrative_scenes
        )

        # Clean up existing subblock phase JSON files for this block to avoid leaving stale/leftover files
        if os.path.exists(output_dir):
            for fname in os.listdir(output_dir):
                if fname.startswith(f"block_{idx}_phase_") and fname.endswith(".json"):
                    try:
                        os.remove(os.path.join(output_dir, fname))
                    except Exception as e:
                        print(f"    Warning: Could not remove old file {fname}: {e}")

        subblocks_manifest = []
        # Save subblocks to output dir
        subblocks_success = True
        for sb in subblocks:
            sb_id = sb["subblock_id"]
            sb_filename = f"{sb_id}.json"
            sb_filepath = os.path.join(output_dir, sb_filename)

            try:
                with open(sb_filepath, "w", encoding="utf-8") as f_sb:
                    json.dump(sb, f_sb, ensure_ascii=False, indent=2)
                print(f"    Generated subdivided phase: {sb_filename}")
            except Exception as e:
                print(f"    Error writing subblock file {sb_filepath}: {e}")
                subblocks_success = False
                break

            # Add to manifest
            subblocks_manifest.append({
                "block_no": idx,
                "subblock_id": sb_id,
                "subblock_type": sb["subblock_type"],
                "filename": sb_filename,
                "scene_count": len(sb["scenes"])
            })

        if not subblocks_success:
            continue

        # Update manifest file (merge with existing block entries)
        manifest_path = os.path.join(output_dir, "subblocks_manifest.json")
        existing_manifest = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f_manifest:
                    existing_manifest = json.load(f_manifest)
            except Exception as e:
                print(f"    Warning: Could not read existing manifest: {e}")

        # Remove old entries for this block and merge
        existing_manifest = [m for m in existing_manifest if m.get("block_no") != idx]
        combined_manifest = existing_manifest + subblocks_manifest
        combined_manifest.sort(key=lambda x: (x.get("block_no", 0), x.get("subblock_id", "")))

        try:
            with open(manifest_path, "w", encoding="utf-8") as f_manifest:
                json.dump(combined_manifest, f_manifest, ensure_ascii=False, indent=2)
            print(f"    Updated subblock manifest: {manifest_path}")
        except Exception as e:
            print(f"    Error writing manifest file {manifest_path}: {e}")
            continue

        # Mark block completed
        entry["completed"] = True
        with open(todo_path, "w", encoding="utf-8") as f_todo:
            json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
        processed_blocks += 1

        time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="Generate scene-by-scene visual directions with structured visual groups.")
    parser.add_argument("--lang", choices=["en", "ur"], required=True, help="Language track: 'en' for English, 'ur' for Roman Urdu.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of blocks to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--block", type=int, default=None, help="Process a specific block index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed entries.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between successful API calls.")
    parser.add_argument("--max-narrative-scenes", type=int, default=6, help="Max scenes per narrative-only subblock.")
    args = parser.parse_args()

    # Determine directories
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)

    # Load environment variables via api_logger
    keys = api_logger.load_env_keys()
    if not keys:
        print("Error: No GEMINI_API_KEY_1 through GEMINI_API_KEY_7 found in .env file.", file=sys.stderr)
        sys.exit(1)

    process_track(args.lang, script_dir, root_dir, args.limit, args.ruku, args.block, args.force, args.delay, args.max_narrative_scenes)
    print(f"\n{args.lang.upper()} Step 4 processing finished.")


if __name__ == "__main__":
    main()
