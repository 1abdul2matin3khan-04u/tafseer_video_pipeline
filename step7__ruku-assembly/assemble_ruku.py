#!/usr/bin/env python3
"""
assemble_ruku.py
Step 7: Ruku Assembly

Combines the stitched block-level videos from Step 6 into a single final Ruku video
using FFmpeg concat demuxer (lossless, copy-codec).
"""

import os
import sys
import json
import subprocess
import argparse

def run_command(cmd, timeout=None):
    """
    Executes a system command and returns success boolean, stdout, and stderr.
    """
    print(f"    Executing command: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return False, "", str(e)

def parse_block_selection(selection_str):
    """
    Parses a block selection string like '1,3-6,8' into a set of integer block numbers.
    Supports single numbers, ranges (e.g. 3-6), and comma-separated lists.
    """
    blocks = set()
    parts = selection_str.split(',')
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start_str, end_str = part.split('-', 1)
                start = int(start_str.strip())
                end = int(end_str.strip())
                for i in range(start, end + 1):
                    blocks.add(i)
            except ValueError:
                print(f"Warning: Invalid block range format '{part}'. Skipping.")
        else:
            try:
                blocks.add(int(part))
            except ValueError:
                print(f"Warning: Invalid block number format '{part}'. Skipping.")
    return blocks

def main():
    # Preprocess sys.argv to redirect raw blocks selection like '--1,3-6' to '--blocks 1,3-6'
    cleaned_argv = []
    for arg in sys.argv:
        if arg.startswith("--") and any(c.isdigit() for c in arg):
            block_content = arg[2:]
            if all(c.isdigit() or c in ',-' for c in block_content):
                cleaned_argv.extend(["--blocks", block_content])
                continue
        cleaned_argv.append(arg)
    
    # Temporarily override sys.argv for argparse
    original_argv = sys.argv
    sys.argv = cleaned_argv

    parser = argparse.ArgumentParser(description="Step 7: Ruku Assembly Pipeline (Concat Blocks Losslessly)")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force stitching of Ruku even if the output file already exists.")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Process specific tracks.")
    parser.add_argument("--blocks", type=str, default=None, help="Comma-separated block indices or ranges to merge, e.g. 1,3-6,8")
    
    args = parser.parse_args()
    
    # Restore original sys.argv
    sys.argv = original_argv

    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)

    # Load rukuDivision mapping
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

    languages = []
    if args.lang == "both":
        languages = ["en", "ur"]
    else:
        languages = [args.lang]

    for lang in languages:
        print(f"\n==========================================")
        print(f"Starting Step 7 Ruku Assembly for Track: {lang.upper()}")
        print(f"==========================================")

        todo_filename = f"todo_ruku_{'english' if lang == 'en' else 'urdu'}.json"
        todo_path = os.path.join(script_dir, "guiding_resources", todo_filename)

        if not os.path.exists(todo_path):
            print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
            continue

        with open(todo_path, 'r', encoding='utf-8') as f:
            todo_list = json.load(f)

        processed_rukus = 0

        for entry in todo_list:
            if args.limit is not None and processed_rukus >= args.limit:
                break

            if args.ruku is not None and entry["absolute_ruku"] != args.ruku:
                continue

            if entry["completed"] and not args.force:
                continue

            abs_ruku = entry["absolute_ruku"]
            surah_num = entry["surah_number"]
            surah_name = entry["surah_name"]
            rel_ruku = entry["relative_ruku"]

            ruku_folder = f"surah_{surah_num:03d}/ruku_{rel_ruku}_{abs_ruku}/{lang}"
            step5_out_dir = os.path.join(
                root_dir, "step5__animation-audio-integration", "remotion_project", "public", "output_resources", ruku_folder
            )
            step6_out_dir = os.path.join(root_dir, "step6__block-assembly", "output_resources", ruku_folder)
            
            # Target output details
            output_dir = os.path.join(script_dir, "output_resources", f"surah_{surah_num:03d}")
            os.makedirs(output_dir, exist_ok=True)
            
            surah_info = surahs_mapping.get(surah_num, {})
            total_rukus = len(surah_info.get("verse_ranges", []))
            
            if rel_ruku == 0:
                final_ruku_mp4 = os.path.join(output_dir, f"overview_{lang}.mp4")
            elif rel_ruku > total_rukus:
                final_ruku_mp4 = os.path.join(output_dir, f"summary_{lang}.mp4")
            else:
                final_ruku_mp4 = os.path.join(output_dir, f"surah_{surah_num:03d}_ruku_{rel_ruku:02d}_{lang}.mp4")

            if os.path.exists(final_ruku_mp4) and not args.force:
                print(f"  Ruku {abs_ruku} video already exists at {final_ruku_mp4}. Skipping.")
                continue

            print(f"\n>>> Assembling final Ruku video for absolute Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Rel Ruku {rel_ruku})")

            # Check manifest to identify blocks
            manifest_path = os.path.join(step5_out_dir, "subblocks_manifest.json")
            if not os.path.exists(manifest_path):
                print(f"  [Warning] Manifest not found at {manifest_path}. Step 5 integration output is missing. Skipping.")
                continue

            with open(manifest_path, 'r', encoding='utf-8') as fm:
                manifest = json.load(fm)

            # Get unique sorted block numbers
            block_numbers = sorted(list(set(item["block_no"] for item in manifest)))
            print(f"  Ruku contains {len(block_numbers)} blocks: {block_numbers}")

            # Filter block numbers if selection is provided
            if args.blocks:
                selected_blocks = parse_block_selection(args.blocks)
                filtered_block_numbers = [b for b in block_numbers if b in selected_blocks]
                if not filtered_block_numbers:
                    print(f"  [Warning] Block selection '{args.blocks}' matched no blocks. Merging all blocks instead.")
                else:
                    print(f"  Filtering blocks to merge based on selection '{args.blocks}': {filtered_block_numbers}")
                    block_numbers = filtered_block_numbers

            # Check if all blocks from Step 6 exist
            block_paths = []
            missing_blocks = False
            for b_num in block_numbers:
                b_path = os.path.join(step6_out_dir, f"block_{b_num}.mp4")
                if not os.path.exists(b_path):
                    print(f"  [Warning] Block video is missing at {b_path}. Please complete Step 6 block assembly first.")
                    missing_blocks = True
                else:
                    block_paths.append(b_path)

            if missing_blocks:
                print(f"  [Error] Skipping Ruku {abs_ruku} due to missing block videos.")
                continue

            # Create temporary list file for FFmpeg concat
            list_file_path = os.path.join(output_dir, f"temp_blocks_list_ruku_{abs_ruku}.txt")
            with open(list_file_path, 'w', encoding='utf-8') as lf:
                for b_path in block_paths:
                    safe_path = b_path.replace("\\", "/")
                    lf.write(f"file '{safe_path}'\n")

            # Run FFmpeg Concat
            print(f"  Concatenating {len(block_paths)} blocks into final video...")
            ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_file_path, '-c', 'copy', final_ruku_mp4]
            
            success, stdout, stderr = run_command(ffmpeg_cmd, timeout=300)
            
            # Clean up the list file
            if os.path.exists(list_file_path):
                os.remove(list_file_path)

            if success:
                print(f"  [Success] Saved final Ruku video to: {final_ruku_mp4}")
                entry["completed"] = True
                with open(todo_path, 'w', encoding='utf-8') as f_todo:
                    json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
                processed_rukus += 1
            else:
                print(f"  [Error] FFmpeg concatenation failed for Ruku {abs_ruku}:\n{stderr}")

    print("\nRuku assembly finished.")

if __name__ == "__main__":
    main()
