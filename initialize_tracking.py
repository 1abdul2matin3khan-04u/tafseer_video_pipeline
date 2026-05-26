#!/usr/bin/env python3
"""
initialize_tracking.py
Generates all pipeline tracking JSON files in their correct step guiding_resources/ folders.
Run from the workspace root: python initialize_tracking.py

Tracking files generated:
  step1__single-summary/guiding_resources/todo_summary.json
  step2__summary-combined/guiding_resources/todo_tafseer_english.json
  step2__summary-combined/guiding_resources/todo_tafseer_urdu.json
  step3__combined-script/guiding_resources/todo_script_english.json
  step3__combined-script/guiding_resources/todo_script_urdu.json
  step4__script-visual-division/guiding_resources/todo_visuals_english.json
  step4__script-visual-division/guiding_resources/todo_visuals_urdu.json
"""
import os
import json
import re

# Sources summarized in step1 (wbw provides translation, not tafseer, so not summarized)
SUMMARY_SOURCES = ["ibn_kathir", "maarif", "tazkir", "saadi", "bayan_ul_quran"]

def load_ruku_list(mapping_path):
    with open(mapping_path, 'r', encoding='utf-8') as f:
        surahs = json.load(f)

    ruku_list = []
    abs_idx = 1
    for surah in surahs:
        surah_num = surah.get('surah_number')
        surah_name = surah.get('surah_name', '')
        verse_ranges = surah.get('verse_ranges', [])
        for rel_idx, range_str in enumerate(verse_ranges):
            ruku_list.append({
                "absolute_ruku": abs_idx,
                "surah_number": surah_num,
                "surah_name": surah_name,
                "relative_ruku": rel_idx + 1,
                "verse_range": range_str,
            })
            abs_idx += 1
    return ruku_list

def get_block_entries(root, ruku_list, lang):
    """
    Scans the Step 3 combined-script output resources directory to find
    how many blocks were generated for each Ruku, and returns a list of
    block-level entries.
    """
    step3_out_dir = os.path.join(root, "step3__combined-script", "output_resources")
    block_entries = []
    
    # Create a lookup for quick Ruku metadata
    ruku_lookup = {r["absolute_ruku"]: r for r in ruku_list}
    
    if os.path.exists(step3_out_dir):
        for surah_dir in os.listdir(step3_out_dir):
            surah_path = os.path.join(step3_out_dir, surah_dir)
            if not os.path.isdir(surah_path):
                continue
            for ruku_dir in os.listdir(surah_path):
                match = re.search(r'ruku_(\d+)_(\d+)', ruku_dir)
                if not match:
                    continue
                rel_ruku = int(match.group(1))
                abs_ruku = int(match.group(2))
                
                lang_path = os.path.join(surah_path, ruku_dir, lang)
                if not os.path.exists(lang_path) or not os.path.isdir(lang_path):
                    continue
                
                # Find all block md files
                block_nums = []
                for fname in os.listdir(lang_path):
                    if fname.startswith("block_") and fname.endswith(".md"):
                        m_blk = re.search(r'block_(\d+)\.md', fname)
                        if m_blk:
                            block_nums.append(int(m_blk.group(1)))
                
                block_nums.sort()
                
                r_meta = ruku_lookup.get(abs_ruku, {
                    "absolute_ruku": abs_ruku,
                    "surah_number": int(surah_dir.split("_")[-1]) if "_" in surah_dir else 1,
                    "surah_name": surah_dir.split("_")[-1] if "_" in surah_dir else "Surah",
                    "relative_ruku": rel_ruku,
                    "verse_range": ""
                })
                
                for blk_no in block_nums:
                    block_entries.append({
                        "absolute_ruku": abs_ruku,
                        "surah_number": r_meta["surah_number"],
                        "surah_name": r_meta["surah_name"],
                        "relative_ruku": rel_ruku,
                        "verse_range": r_meta["verse_range"],
                        "block_no": blk_no,
                        "completed": False
                    })
                    
    block_entries.sort(key=lambda x: (x["absolute_ruku"], x["block_no"]))
    return block_entries

def write_json(path, data):
    completed_map = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                for entry in old_data:
                    abs_ruku = entry.get("absolute_ruku")
                    if abs_ruku is not None:
                        completed_map[abs_ruku] = entry.get("completed", False)
                        if "sources_completed" in entry:
                            completed_map[f"{abs_ruku}_sources"] = entry["sources_completed"]
        except Exception as e:
            print(f"  Warning: Could not parse existing tracking file {path}: {e}")

    for entry in data:
        abs_ruku = entry.get("absolute_ruku")
        if abs_ruku in completed_map:
            entry["completed"] = completed_map[abs_ruku]
        if f"{abs_ruku}_sources" in completed_map and "sources_completed" in entry:
            entry["sources_completed"] = completed_map[f"{abs_ruku}_sources"]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Written: {path}")

def write_block_json(path, data):
    completed_map = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                for entry in old_data:
                    abs_ruku = entry.get("absolute_ruku")
                    block_no = entry.get("block_no")
                    if abs_ruku is not None and block_no is not None:
                        completed_map[(abs_ruku, block_no)] = entry.get("completed", False)
                    elif abs_ruku is not None:
                        completed_map[abs_ruku] = entry.get("completed", False)
        except Exception as e:
            print(f"  Warning: Could not parse existing tracking file {path}: {e}")

    for entry in data:
        abs_ruku = entry.get("absolute_ruku")
        block_no = entry.get("block_no")
        if (abs_ruku, block_no) in completed_map:
            entry["completed"] = completed_map[(abs_ruku, block_no)]
        elif abs_ruku in completed_map:
            entry["completed"] = completed_map[abs_ruku]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Written: {path}")



def main():
    root = os.path.dirname(os.path.abspath(__file__))
    mapping_path = os.path.join(root, "step0__whole-single", "input_resources", "rukuDivision.json")

    if not os.path.exists(mapping_path):
        print(f"Error: rukuDivision.json not found at {mapping_path}")
        return

    ruku_list = load_ruku_list(mapping_path)
    print(f"Loaded {len(ruku_list)} Rukus from rukuDivision.json.\n")

    # ── step1: todo_summary.json (per-source tracking inside each Ruku entry) ──
    print("Generating step1 tracking file...")
    summary_entries = [
        {**r, "sources_completed": [], "completed": False}
        for r in ruku_list
    ]
    write_json(
        os.path.join(root, "step1__single-summary", "guiding_resources", "todo_summary.json"),
        summary_entries
    )

    # ── step2: todo_tafseer_english.json + todo_tafseer_urdu.json ──
    print("\nGenerating step2 tracking files...")
    step2_entries = [
        {**r, "completed": False}
        for r in ruku_list
    ]
    for fname in ["todo_tafseer_english.json", "todo_tafseer_urdu.json"]:
        write_json(
            os.path.join(root, "step2__summary-combined", "guiding_resources", fname),
            step2_entries
        )

    # ── step3: todo_script_english.json + todo_script_urdu.json ──
    print("\nGenerating step3 tracking files...")
    step3_entries = [
        {**r, "completed": False}
        for r in ruku_list
    ]
    for fname in ["todo_script_english.json", "todo_script_urdu.json"]:
        write_json(
            os.path.join(root, "step3__combined-script", "guiding_resources", fname),
            step3_entries
        )

    # ── step4: todo_visuals_english.json + todo_visuals_urdu.json ──
    print("\nGenerating step4 tracking files...")
    step4_entries_en = get_block_entries(root, ruku_list, "en")
    step4_entries_ur = get_block_entries(root, ruku_list, "ur")
    
    write_block_json(
        os.path.join(root, "step4__script-visual-division", "guiding_resources", "todo_visuals_english.json"),
        step4_entries_en
    )
    write_block_json(
        os.path.join(root, "step4__script-visual-division", "guiding_resources", "todo_visuals_urdu.json"),
        step4_entries_ur
    )


    # ── step5: todo_integration_english.json + todo_integration_urdu.json ──
    print("\nGenerating step5 tracking files...")
    write_block_json(
        os.path.join(root, "step5__animation-audio-integration", "guiding_resources", "todo_integration_english.json"),
        step4_entries_en
    )
    write_block_json(
        os.path.join(root, "step5__animation-audio-integration", "guiding_resources", "todo_integration_urdu.json"),
        step4_entries_ur
    )

    # ── step6: todo_assembly_english.json + todo_assembly_urdu.json ──
    print("\nGenerating step6 tracking files...")
    write_block_json(
        os.path.join(root, "step6__block-assembly", "guiding_resources", "todo_assembly_english.json"),
        step4_entries_en
    )
    write_block_json(
        os.path.join(root, "step6__block-assembly", "guiding_resources", "todo_assembly_urdu.json"),
        step4_entries_ur
    )

    # ── step7: todo_ruku_english.json + todo_ruku_urdu.json ──
    print("\nGenerating step7 tracking files...")
    step7_entries = [
        {**r, "completed": False}
        for r in ruku_list
    ]
    for fname in ["todo_ruku_english.json", "todo_ruku_urdu.json"]:
        write_json(
            os.path.join(root, "step7__ruku-assembly", "guiding_resources", fname),
            step7_entries
        )

    print(f"\nAll tracking files generated/updated. Total Rukus in base list: {len(ruku_list)}")

if __name__ == "__main__":
    main()
