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

def run_command(cmd):
    """
    Executes a system command and returns success boolean, stdout, and stderr.
    """
    print(f"    Executing command: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def main():
    parser = argparse.ArgumentParser(description="Step 7: Ruku Assembly Pipeline (Concat Blocks Losslessly)")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force stitching of Ruku even if the output file already exists.")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Process specific tracks.")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)

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
                    # Windows requires forward slashes in FFmpeg list files
                    safe_path = b_path.replace("\\", "/")
                    lf.write(f"file '{safe_path}'\n")

            # Run FFmpeg Concat
            print(f"  Concatenating {len(block_paths)} blocks into final video...")
            ffmpeg_cmd = f'ffmpeg -y -f concat -safe 0 -i "{list_file_path.replace("\\", "/")}" -c copy "{final_ruku_mp4.replace("\\", "/")}"'
            
            success, stdout, stderr = run_command(ffmpeg_cmd)
            
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
