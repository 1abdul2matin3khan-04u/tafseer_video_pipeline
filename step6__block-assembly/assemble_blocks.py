#!/usr/bin/env python3
"""
assemble_blocks.py
Step 6: Block Assembly (Phase B)

Reads the Step 5 integrated subblock JSON files and audio assets,
renders each subblock chunk via Remotion CLI, and stitches them together
into single finished block_X.mp4 files using FFmpeg concat demuxer.
"""

import os
import sys
import json
import subprocess
import argparse
import shutil

def run_command(cmd, cwd=None):
    """
    Executes a system command and returns success boolean, stdout, and stderr.
    """
    print(f"      Executing command: {cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
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
    parser = argparse.ArgumentParser(description="Step 6: Block Assembly Pipeline (Phase B Rendering & Stitching)")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force re-rendering and stitching of blocks.")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Process specific tracks.")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    remotion_dir = os.path.join(root_dir, "step5__animation-audio-integration", "remotion_project")

    languages = []
    if args.lang == "both":
        languages = ["en", "ur"]
    else:
        languages = [args.lang]

    for lang in languages:
        print(f"\n==========================================")
        print(f"Starting Step 6 Assembly for Track: {lang.upper()}")
        print(f"==========================================")

        todo_filename = f"todo_assembly_{'english' if lang == 'en' else 'urdu'}.json"
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

            print(f"\n>>> Assembling blocks for Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})")

            ruku_folder = f"surah_{surah_num:03d}/ruku_{rel_ruku}_{abs_ruku}/{lang}"
            step5_out_dir = os.path.join(
                root_dir, "step5__animation-audio-integration", "remotion_project", "public", "output_resources", ruku_folder
            )
            step6_out_dir = os.path.join(script_dir, "output_resources", ruku_folder)

            manifest_path = os.path.join(step5_out_dir, "subblocks_manifest.json")
            if not os.path.exists(manifest_path):
                print(f"  Warning: Step 5 output manifest not found at {manifest_path}. Verify that Step 5 has been executed. Skipping Ruku.")
                continue

            with open(manifest_path, 'r', encoding='utf-8') as f_man:
                manifest = json.load(f_man)

            # Group manifest entries by block_no
            blocks = {}
            for item in manifest:
                b_num = item["block_no"]
                if b_num not in blocks:
                    blocks[b_num] = []
                blocks[b_num].append(item)

            os.makedirs(step6_out_dir, exist_ok=True)
            temp_dir = os.path.join(script_dir, "temp_chunks", f"ruku_{abs_ruku}_{lang}")

            ruku_success = True

            for block_no, subblocks in sorted(blocks.items()):
                final_block_mp4 = os.path.join(step6_out_dir, f"block_{block_no}.mp4")

                if os.path.exists(final_block_mp4) and not args.force:
                    print(f"  Block {block_no} already exists at {final_block_mp4}. Skipping.")
                    continue

                print(f"\n  --- Assembling Block {block_no} (Contains {len(subblocks)} subblocks) ---")

                # Optimization: if block has exactly 1 subblock, render directly to final path
                if len(subblocks) == 1:
                    sub_id = subblocks[0]["subblock_id"]
                    sub_filename = subblocks[0]["filename"]
                    print(f"    Single subblock {sub_id} detected. Rendering directly to target file.")

                    # Props path is relative to the remotion project directory
                    props_path = f"public/output_resources/{ruku_folder}/{sub_filename}"
                    cmd = f'npx remotion render DynamicSequencer "{final_block_mp4}" --props="{props_path}"'
                    
                    success, stdout, stderr = run_command(cmd, cwd=remotion_dir)
                    if not success:
                        print(f"    [Error] Failed to render block {block_no}:\n{stderr}")
                        ruku_success = False
                        break
                    print(f"    Successfully rendered block {block_no} directly.")
                else:
                    # Multiple subblocks: render individually to temp directory and stitch
                    block_temp_dir = os.path.join(temp_dir, f"block_{block_no}")
                    os.makedirs(block_temp_dir, exist_ok=True)

                    rendered_chunks = []
                    render_success = True

                    for sub in subblocks:
                        sub_id = sub["subblock_id"]
                        sub_filename = sub["filename"]
                        temp_chunk_mp4 = os.path.join(block_temp_dir, f"{sub_id}.mp4")

                        print(f"    Rendering subblock chunk {sub_id}...")
                        props_path = f"public/output_resources/{ruku_folder}/{sub_filename}"
                        cmd = f'npx remotion render DynamicSequencer "{temp_chunk_mp4}" --props="{props_path}"'

                        success, stdout, stderr = run_command(cmd, cwd=remotion_dir)
                        if not success:
                            print(f"      [Error] Failed to render subblock {sub_id}:\n{stderr}")
                            render_success = False
                            break
                        rendered_chunks.append(temp_chunk_mp4)
                        print(f"      Rendered chunk saved to {temp_chunk_mp4}")

                    if not render_success:
                        ruku_success = False
                        break

                    # Stitch files together using FFmpeg concat demuxer
                    print(f"    Stitching {len(rendered_chunks)} chunks losslessly using FFmpeg concat...")
                    list_file_path = os.path.join(block_temp_dir, "chunks_list.txt")
                    
                    with open(list_file_path, 'w', encoding='utf-8') as lf:
                        for chunk in rendered_chunks:
                            # Forward slashes work best for FFmpeg on Windows
                            safe_path = chunk.replace("\\", "/")
                            lf.write(f"file '{safe_path}'\n")

                    ffmpeg_cmd = f'ffmpeg -y -f concat -safe 0 -i "{list_file_path.replace("\\", "/")}" -c copy "{final_block_mp4.replace("\\", "/")}"'
                    success, stdout, stderr = run_command(ffmpeg_cmd)
                    
                    if not success:
                        print(f"    [Error] FFmpeg stitching failed for block {block_no}:\n{stderr}")
                        ruku_success = False
                        break
                        
                    print(f"    Stitching complete. Final video saved to {final_block_mp4}")

                    # Cleanup temporary subblock chunk files
                    shutil.rmtree(block_temp_dir, ignore_errors=True)

            # Cleanup absolute ruku temp folder
            shutil.rmtree(temp_dir, ignore_errors=True)

            if ruku_success:
                entry["completed"] = True
                with open(todo_path, 'w', encoding='utf-8') as f_todo:
                    json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
                processed_rukus += 1
                print(f"\n>>> Successfully completed Block Assembly for Ruku {abs_ruku}.")
            else:
                print(f"\n>>> [Error] Block Assembly failed for Ruku {abs_ruku}.")

    print("\nAssembly finished.")

if __name__ == "__main__":
    main()
