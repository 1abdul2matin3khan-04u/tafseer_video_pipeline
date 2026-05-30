#!/usr/bin/env python3
import os
import sys
import json
import re
import shutil
import argparse

# Ensure UTF-8 output on Windows terminal
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# List of pipeline steps
STEPS = {
    0: {"name": "step0__whole-single", "path_type": "standard"},
    1: {"name": "step1__single-summary", "path_type": "standard"},
    2: {"name": "step2__summary-combined", "path_type": "standard"},
    3: {"name": "step3__combined-script", "path_type": "lang_based"},
    4: {"name": "step4__script-visual-division", "path_type": "lang_based"},
    5: {"name": "step5__animation-audio-integration", "path_type": "remotion"},
    6: {"name": "step6__block-assembly", "path_type": "lang_based"},
    7: {"name": "step7__ruku-assembly", "path_type": "lang_based"}
}

def load_ruku_list(mapping_path):
    if not os.path.exists(mapping_path):
        print(f"Error: rukuDivision.json not found at {mapping_path}")
        sys.exit(1)
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

def get_target_rukus(ruku_list, args):
    if args.ruku is not None:
        target = [r for r in ruku_list if r["absolute_ruku"] == args.ruku]
        if not target:
            print(f"Error: Absolute Ruku {args.ruku} not found.")
            sys.exit(1)
        return target
    elif args.surah is not None:
        if args.rel_ruku is not None:
            target = [r for r in ruku_list if r["surah_number"] == args.surah and r["relative_ruku"] == args.rel_ruku]
        else:
            target = [r for r in ruku_list if r["surah_number"] == args.surah]
        if not target:
            print(f"Error: Surah {args.surah} (relative ruku {args.rel_ruku}) not found.")
            sys.exit(1)
        return target
    else:
        # If block or subblock is specified, a Ruku context must be provided.
        if args.block is not None or args.subblock is not None:
            print("Error: You must specify a Ruku filter (--ruku or --surah) to clean a specific block or subblock.")
            sys.exit(1)
        # Clean all rukus
        return ruku_list

def update_manifest_file(manifest_path, block_no=None, subblock_id=None):
    if not os.path.exists(manifest_path):
        return False
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        original_len = len(data)
        if subblock_id is not None:
            data = [m for m in data if m.get("subblock_id") != subblock_id]
        elif block_no is not None:
            data = [m for m in data if m.get("block_no") != block_no]
            
        if len(data) < original_len:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        print(f"Error updating manifest {manifest_path}: {e}")
    return False

def reset_tracking_file(path, abs_ruku, block_no=None, is_summary_step=False):
    if not os.path.exists(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        modified = False
        for entry in data:
            if entry.get("absolute_ruku") == abs_ruku:
                if "block_no" in entry:
                    if block_no is not None:
                        if entry.get("block_no") == block_no:
                            if entry.get("completed") != False:
                                entry["completed"] = False
                                modified = True
                    else:
                        if entry.get("completed") != False:
                            entry["completed"] = False
                            modified = True
                else:
                    # Ruku-based tracking file (any block/subblock change invalidates the ruku)
                    if entry.get("completed") != False:
                        entry["completed"] = False
                        modified = True
                    if is_summary_step and entry.get("sources_completed") != []:
                        entry["sources_completed"] = []
                        modified = True
        
        if modified:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception as e:
        print(f"Error resetting tracking file {path}: {e}")
    return False

def get_tracking_paths(root, step_idx, lang):
    """
    Returns a list of tracking files for a step and language.
    """
    paths = []
    step_name = STEPS[step_idx]["name"]
    if step_idx == 1:
        paths.append((os.path.join(root, step_name, "guiding_resources", "todo_summary.json"), True))
    elif step_idx == 2:
        if lang in ["en", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_tafseer_english.json"), False))
        if lang in ["ur", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_tafseer_urdu.json"), False))
    elif step_idx == 3:
        if lang in ["en", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_script_english.json"), False))
        if lang in ["ur", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_script_urdu.json"), False))
    elif step_idx == 4:
        if lang in ["en", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_visuals_english.json"), False))
        if lang in ["ur", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_visuals_urdu.json"), False))
    elif step_idx == 5:
        if lang in ["en", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_integration_english.json"), False))
        if lang in ["ur", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_integration_urdu.json"), False))
    elif step_idx == 6:
        if lang in ["en", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_assembly_english.json"), False))
        if lang in ["ur", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_assembly_urdu.json"), False))
    elif step_idx == 7:
        if lang in ["en", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_ruku_english.json"), False))
        if lang in ["ur", "both"]:
            paths.append((os.path.join(root, step_name, "guiding_resources", "todo_ruku_urdu.json"), False))
    return paths

def main():
    root = os.path.dirname(os.path.abspath(__file__))
    mapping_path = os.path.join(root, "step0__whole-single", "input_resources", "rukuDivision.json")
    ruku_list = load_ruku_list(mapping_path)

    parser = argparse.ArgumentParser(description="Utility cleaner script for Tafseer video pipeline.")
    parser.add_argument("--ruku", type=int, default=None, help="Target absolute Ruku index (1-558).")
    parser.add_argument("--surah", type=int, default=None, help="Target Surah number.")
    parser.add_argument("--rel-ruku", type=int, default=None, help="Target relative Ruku index inside the Surah.")
    parser.add_argument("--step", type=str, default="all", help="Target step(s) to clean (0-7, comma-separated, or 'all').")
    parser.add_argument("--block", type=int, default=None, help="Target specific block index.")
    parser.add_argument("--subblock", type=str, default=None, help="Target specific subblock ID (e.g. block_5_phase_3_1).")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Target language track (en, ur, both).")
    parser.add_argument("-y", "--yes", action="store_true", help="Bypass interactive confirmation prompt.")
    args = parser.parse_args()

    # Determine steps to clean
    steps_to_clean = []
    if args.step == "all":
        steps_to_clean = list(range(8))
    else:
        try:
            steps_to_clean = [int(s.strip()) for s in args.step.split(",") if s.strip().isdigit()]
            steps_to_clean = [s for s in steps_to_clean if s in range(8)]
        except Exception:
            print("Error: Invalid step specification. Use numbers 0-7 or 'all'.")
            sys.exit(1)

    if not steps_to_clean:
        print("Error: No valid steps selected to clean.")
        sys.exit(1)

    # Determine languages
    langs = []
    if args.lang == "both":
        langs = ["en", "ur"]
    else:
        langs = [args.lang]

    # Resolve target rukus
    target_rukus = get_target_rukus(ruku_list, args)

    # If subblock is specified, auto-resolve target block
    target_block = args.block
    if args.subblock:
        match = re.search(r"block_(\d+)", args.subblock)
        if match:
            target_block = int(match.group(1))

    # Collect operations to perform
    deletions_files = []
    deletions_dirs = []
    manifest_updates = []  # tuple of (manifest_path, block_no, subblock_id)
    tracking_resets = []   # tuple of (tracking_path, abs_ruku, block_no, is_summary)

    for ruku in target_rukus:
        surah = ruku["surah_number"]
        rel = ruku["relative_ruku"]
        abs_ruku = ruku["absolute_ruku"]

        # Step 0
        if 0 in steps_to_clean and not args.block and not args.subblock:
            step0_dir = os.path.join(root, "step0__whole-single", "output_resources", f"surah_{surah:03d}", f"ruku_{rel}_{abs_ruku}")
            if os.path.exists(step0_dir):
                deletions_dirs.append(step0_dir)

        # Step 1
        if 1 in steps_to_clean and not args.block and not args.subblock:
            step1_dir = os.path.join(root, "step1__single-summary", "output_resources", f"surah_{surah:03d}", f"ruku_{rel}_{abs_ruku}")
            if os.path.exists(step1_dir):
                deletions_dirs.append(step1_dir)
            
            # Step 1 has a single shared tracking file
            todo_path = os.path.join(root, "step1__single-summary", "guiding_resources", "todo_summary.json")
            if os.path.exists(todo_path):
                tracking_resets.append((todo_path, abs_ruku, None, True))

        # Step 2
        if 2 in steps_to_clean and not args.block and not args.subblock:
            step2_dir = os.path.join(root, "step2__summary-combined", "output_resources", f"surah_{surah:03d}", f"ruku_{rel}_{abs_ruku}")
            if os.path.exists(step2_dir):
                if args.lang == "both":
                    deletions_dirs.append(step2_dir)
                else:
                    filename = "combined_tafseer_english.json" if args.lang == "en" else "combined_tafseer_roman-urdu.json"
                    file_path = os.path.join(step2_dir, filename)
                    if os.path.exists(file_path):
                        deletions_files.append(file_path)
            
            for l in langs:
                paths = get_tracking_paths(root, 2, l)
                for p, is_sum in paths:
                    tracking_resets.append((p, abs_ruku, None, is_sum))

        # Steps 3 to 7
        for step in [s for s in steps_to_clean if s >= 3]:
            step_name = STEPS[step]["name"]
            
            # Resolve base Ruku output directory for this step
            if step == 5:
                ruku_dir = os.path.join(root, "step5__animation-audio-integration", "remotion_project", "public", "output_resources", f"surah_{surah:03d}", f"ruku_{rel}_{abs_ruku}")
            else:
                ruku_dir = os.path.join(root, step_name, "output_resources", f"surah_{surah:03d}", f"ruku_{rel}_{abs_ruku}")

            for l in langs:
                lang_dir = os.path.join(ruku_dir, l)
                if not os.path.exists(lang_dir):
                    continue

                # 1. Ruku-level clean
                if not target_block and not args.subblock:
                    deletions_dirs.append(lang_dir)
                    
                    # For Step 6, also clean temp chunks matching this Ruku
                    if step == 6:
                        temp_chunks_dir = os.path.join(root, "step6__block-assembly", "temp_chunks")
                        if os.path.exists(temp_chunks_dir):
                            for item in os.listdir(temp_chunks_dir):
                                if item.startswith(f"ruku_{abs_ruku}_"):
                                    deletions_dirs.append(os.path.join(temp_chunks_dir, item))
                
                # 2. Block-level clean
                elif target_block and not args.subblock:
                    if step == 3:
                        blk_file = os.path.join(lang_dir, f"block_{target_block}.md")
                        if os.path.exists(blk_file):
                            deletions_files.append(blk_file)
                    elif step == 4:
                        raw_file = os.path.join(lang_dir, f"block_{target_block}_raw.json")
                        if os.path.exists(raw_file):
                            deletions_files.append(raw_file)
                        for item in os.listdir(lang_dir):
                            if item.startswith(f"block_{target_block}_phase_") and item.endswith(".json"):
                                deletions_files.append(os.path.join(lang_dir, item))
                        
                        manifest_path = os.path.join(lang_dir, "subblocks_manifest.json")
                        if os.path.exists(manifest_path):
                            manifest_updates.append((manifest_path, target_block, None))
                    elif step == 5:
                        for item in os.listdir(lang_dir):
                            if item.startswith(f"block_{target_block}_phase_") and item.endswith(".json"):
                                deletions_files.append(os.path.join(lang_dir, item))
                        
                        audio_dir = os.path.join(lang_dir, "audio")
                        if os.path.exists(audio_dir):
                            for item in os.listdir(audio_dir):
                                if item.startswith(f"block_{target_block}_phase_"):
                                    deletions_dirs.append(os.path.join(audio_dir, item))
                        
                        manifest_path = os.path.join(lang_dir, "subblocks_manifest.json")
                        if os.path.exists(manifest_path):
                            manifest_updates.append((manifest_path, target_block, None))
                    elif step == 6:
                        mp4_file = os.path.join(lang_dir, f"block_{target_block}.mp4")
                        if os.path.exists(mp4_file):
                            deletions_files.append(mp4_file)
                        
                        # Step 6 temp chunks for block
                        temp_chunks_dir = os.path.join(root, "step6__block-assembly", "temp_chunks")
                        if os.path.exists(temp_chunks_dir):
                            for item in os.listdir(temp_chunks_dir):
                                if item.startswith(f"ruku_{abs_ruku}_block_{target_block}_{l}"):
                                    deletions_dirs.append(os.path.join(temp_chunks_dir, item))
                    elif step == 7:
                        # Resets ruku output since one block is cleared
                        deletions_dirs.append(lang_dir)

                # 3. Subblock-level clean
                elif args.subblock:
                    if step == 4:
                        sub_file = os.path.join(lang_dir, f"{args.subblock}.json")
                        if os.path.exists(sub_file):
                            deletions_files.append(sub_file)
                        
                        manifest_path = os.path.join(lang_dir, "subblocks_manifest.json")
                        if os.path.exists(manifest_path):
                            manifest_updates.append((manifest_path, None, args.subblock))
                    elif step == 5:
                        sub_file = os.path.join(lang_dir, f"{args.subblock}.json")
                        if os.path.exists(sub_file):
                            deletions_files.append(sub_file)
                        
                        audio_sub_dir = os.path.join(lang_dir, "audio", args.subblock)
                        if os.path.exists(audio_sub_dir):
                            deletions_dirs.append(audio_sub_dir)
                        
                        manifest_path = os.path.join(lang_dir, "subblocks_manifest.json")
                        if os.path.exists(manifest_path):
                            manifest_updates.append((manifest_path, None, args.subblock))
                    elif step == 6:
                        # Deletes block mp4 because subblock is updated/deleted
                        mp4_file = os.path.join(lang_dir, f"block_{target_block}.mp4")
                        if os.path.exists(mp4_file):
                            deletions_files.append(mp4_file)
                        
                        temp_chunks_dir = os.path.join(root, "step6__block-assembly", "temp_chunks")
                        if os.path.exists(temp_chunks_dir):
                            for item in os.listdir(temp_chunks_dir):
                                if item.startswith(f"ruku_{abs_ruku}_block_{target_block}_{l}"):
                                    deletions_dirs.append(os.path.join(temp_chunks_dir, item))
                    elif step == 7:
                        # Deletes ruku mp4
                        deletions_dirs.append(lang_dir)

        # Build tracking resets for Steps 3 to 7
        for step in range(8):
            # Dependency-aware cascade: Reset tracking if this step is cleaned, or if it is downstream (> min cleaned step)
            min_cleaned_step = min(steps_to_clean)
            if step in steps_to_clean or step > min_cleaned_step:
                for l in langs:
                    paths = get_tracking_paths(root, step, l)
                    for p, is_sum in paths:
                        # If block level clean, pass the target block number for block-based steps.
                        # For subblock level, pass target_block since a subblock update invalidates the block.
                        blk = target_block if step in [4, 5, 6] else None
                        tracking_resets.append((p, abs_ruku, blk, is_sum))

    # De-duplicate lists
    deletions_files = list(set(deletions_files))
    deletions_dirs = list(set(deletions_dirs))
    manifest_updates = list(set(manifest_updates))
    tracking_resets = list(set(tracking_resets))

    # Print summary of actions
    if not deletions_files and not deletions_dirs and not manifest_updates and not tracking_resets:
        print("No matching files, directories, or tracking files found to clean.")
        return

    print("======================================================================")
    print("                      PIPELINE CLEAN SUMMARY                          ")
    print("======================================================================")
    print(f"Targeting: {len(target_rukus)} Ruku(s)")
    if target_block:
        print(f"Targeting Block: {target_block}")
    if args.subblock:
        print(f"Targeting Subblock: {args.subblock}")
    print(f"Language(s): {', '.join(langs)}")
    print(f"Step(s) selected: {', '.join(str(s) for s in steps_to_clean)}")
    print("----------------------------------------------------------------------")

    if deletions_dirs:
        print(f"\nDirectories to delete ({len(deletions_dirs)}):")
        for d in sorted(deletions_dirs):
            print(f"  [DIR]  {os.path.relpath(d, root)}")

    if deletions_files:
        print(f"\nFiles to delete ({len(deletions_files)}):")
        for f in sorted(deletions_files):
            print(f"  [FILE] {os.path.relpath(f, root)}")

    if manifest_updates:
        print(f"\nManifest updates ({len(manifest_updates)}):")
        for mu in manifest_updates:
            manifest_rel = os.path.relpath(mu[0], root)
            if mu[2]:
                print(f"  [MAN]  Remove subblock '{mu[2]}' in {manifest_rel}")
            else:
                print(f"  [MAN]  Remove block {mu[1]} in {manifest_rel}")

    if tracking_resets:
        print(f"\nCompletion tracking resets ({len(tracking_resets)}):")
        for tr in sorted(tracking_resets, key=lambda x: x[0]):
            tr_rel = os.path.relpath(tr[0], root)
            if tr[2]:
                print(f"  [TODO] Reset Ruku {tr[1]} Block {tr[2]} to completed=false in {tr_rel}")
            else:
                print(f"  [TODO] Reset Ruku {tr[1]} to completed=false in {tr_rel}")

    print("================================================================------")

    # Ask for confirmation
    if not args.yes:
        confirm = input("\nAre you sure you want to proceed with the cleaning operations? (y/N): ").strip().lower()
        if confirm != 'y' and confirm != 'yes':
            print("Cleanup cancelled.")
            sys.exit(0)

    # Perform cleaning
    print("\nExecuting cleanup...")
    
    # 1. Delete files
    for f in deletions_files:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"  Deleted file: {os.path.relpath(f, root)}")
            except Exception as e:
                print(f"  Failed to delete file {f}: {e}")

    # 2. Delete directories
    for d in deletions_dirs:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
                print(f"  Deleted directory: {os.path.relpath(d, root)}")
            except Exception as e:
                print(f"  Failed to delete directory {d}: {e}")

    # 3. Update manifests
    for manifest_path, block_no, subblock_id in manifest_updates:
        if os.path.exists(manifest_path):
            updated = update_manifest_file(manifest_path, block_no, subblock_id)
            if updated:
                print(f"  Updated manifest: {os.path.relpath(manifest_path, root)}")

    # 4. Reset tracking
    for tracking_path, abs_ruku, block_no, is_summary in tracking_resets:
        if os.path.exists(tracking_path):
            updated = reset_tracking_file(tracking_path, abs_ruku, block_no, is_summary)
            if updated:
                print(f"  Reset tracking file: {os.path.relpath(tracking_path, root)}")

    print("\nCleanup finished successfully.")

if __name__ == "__main__":
    main()
