#!/usr/bin/env python3
"""
assemble_surah.py
Step 8: Surah Assembly

Combines multiple Ruku videos, Surah Overview, and Surah Summary videos
into a single final Surah video using FFmpeg concat demuxer (lossless, copy-codec).
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

def parse_ruku_range(range_str, total_rukus):
    """
    Parses a Ruku range string like 'all', '1-3', '1,2,3' into a sorted list of integer Ruku numbers.
    """
    if not range_str or range_str.lower() == 'all':
        return list(range(1, total_rukus + 1))
    
    rukus = set()
    parts = range_str.split(',')
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
                    if 1 <= i <= total_rukus:
                        rukus.add(i)
            except ValueError:
                print(f"Warning: Invalid Ruku range format '{part}'. Skipping.")
        else:
            try:
                i = int(part)
                if 1 <= i <= total_rukus:
                    rukus.add(i)
            except ValueError:
                print(f"Warning: Invalid Ruku number format '{part}'. Skipping.")
    return sorted(list(rukus))

def main():
    parser = argparse.ArgumentParser(description="Step 8: Surah Final Assembly (Concatenate Rukus & Surah-level Videos)")
    parser.add_argument("--surah", type=int, required=True, help="Target Surah number to assemble (e.g. 110)")
    parser.add_argument("--rukus", type=str, default="all", help="Ruku range/indices to include, e.g. 'all', '1-3', or '1,2,3'")
    parser.add_argument("--include-overview", action="store_true", help="Prepend the Surah Overview video (overview_[lang].mp4) if it exists.")
    parser.add_argument("--include-summary", action="store_true", help="Append the Surah Summary video (summary_[lang].mp4) if it exists.")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Process specific tracks.")
    parser.add_argument("--force", action="store_true", help="Force stitching even if the output file already exists.")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)

    # Load rukuDivision mapping to get Surah details and total Rukus count
    mapping_path = os.path.join(root_dir, "step0__whole-single", "input_resources", "rukuDivision.json")
    if not os.path.exists(mapping_path):
        print(f"Error: rukuDivision.json not found at {mapping_path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(mapping_path, 'r', encoding='utf-8') as f:
            surahs_mapping = {s["surah_number"]: s for s in json.load(f)}
    except Exception as e:
        print(f"Error: Failed to load rukuDivision.json: {e}", file=sys.stderr)
        sys.exit(1)

    surah_info = surahs_mapping.get(args.surah)
    if not surah_info:
        print(f"Error: Surah number {args.surah} not found in rukuDivision.json", file=sys.stderr)
        sys.exit(1)

    surah_num = surah_info["surah_number"]
    surah_name = surah_info["surah_name"]
    total_rukus = len(surah_info.get("verse_ranges", []))

    print(f"\n==========================================")
    print(f"Starting Final Assembly for Surah {surah_num:03d} ({surah_name})")
    print(f"Total Rukus: {total_rukus}")
    print(f"==========================================")

    # Parse requested standard Ruku indices
    selected_rukus = parse_ruku_range(args.rukus, total_rukus)
    if not selected_rukus:
        print("Error: No valid Rukus selected for final assembly.", file=sys.stderr)
        sys.exit(1)

    print(f"Selected standard Rukus: {selected_rukus}")
    print(f"Include Surah Overview: {args.include_overview}")
    print(f"Include Surah Summary: {args.include_summary}")

    languages = ["en", "ur"] if args.lang == "both" else [args.lang]

    step7_out_dir = os.path.join(root_dir, "step7__ruku-assembly", "output_resources", f"surah_{surah_num:03d}")
    output_dir = os.path.join(script_dir, "output_resources", f"surah_{surah_num:03d}")
    os.makedirs(output_dir, exist_ok=True)

    for lang in languages:
        print(f"\n>>> Assembling Final Video for Surah {surah_num:03d} (Track: {lang.upper()})")
        
        # Check input files and compile concatenation sequence
        input_videos = []
        missing_videos = []

        # 1. Overview
        if args.include_overview:
            overview_path = os.path.join(step7_out_dir, f"overview_{lang}.mp4")
            if os.path.exists(overview_path):
                input_videos.append(("overview", overview_path))
            else:
                print(f"  [Warning] Overview video missing at: {overview_path}")
                missing_videos.append(overview_path)

        # 2. Selected Rukus
        for r_rel in selected_rukus:
            ruku_path = os.path.join(step7_out_dir, f"surah_{surah_num:03d}_ruku_{r_rel:02d}_{lang}.mp4")
            if os.path.exists(ruku_path):
                input_videos.append((f"ruku_{r_rel:02d}", ruku_path))
            else:
                print(f"  [Warning] Ruku {r_rel} video missing at: {ruku_path}")
                missing_videos.append(ruku_path)

        # 3. Summary
        if args.include_summary:
            summary_path = os.path.join(step7_out_dir, f"summary_{lang}.mp4")
            if os.path.exists(summary_path):
                input_videos.append(("summary", summary_path))
            else:
                print(f"  [Warning] Summary video missing at: {summary_path}")
                missing_videos.append(summary_path)

        if missing_videos:
            print(f"  [Error] Skipping assembly for Surah {surah_num} ({lang}) due to {len(missing_videos)} missing input videos.")
            continue

        if not input_videos:
            print(f"  [Error] No videos to merge. Skipping.")
            continue

        # Determine descriptive output filename
        is_complete_merge = (
            args.include_overview and 
            args.include_summary and 
            selected_rukus == list(range(1, total_rukus + 1))
        )
        
        if is_complete_merge:
            final_mp4_name = f"full_surah_{lang}.mp4"
        else:
            # Construct filename based on what parts are merged
            parts_desc = []
            if args.include_overview:
                parts_desc.append("overview")
            if selected_rukus:
                if selected_rukus == list(range(1, total_rukus + 1)):
                    parts_desc.append("all_rukus")
                elif len(selected_rukus) == 1:
                    parts_desc.append(f"ruku_{selected_rukus[0]:02d}")
                else:
                    # Detect if consecutive range
                    is_consecutive = all(selected_rukus[i] == selected_rukus[i-1] + 1 for i in range(1, len(selected_rukus)))
                    if is_consecutive:
                        parts_desc.append(f"rukus_{selected_rukus[0]:02d}-{selected_rukus[-1]:02d}")
                    else:
                        parts_desc.append(f"rukus_" + "-".join(f"{r:02d}" for r in selected_rukus))
            if args.include_summary:
                parts_desc.append("summary")
            
            final_mp4_name = f"surah_{surah_num:03d}_merged_{'_'.join(parts_desc)}_{lang}.mp4"

        final_mp4_path = os.path.join(output_dir, final_mp4_name)

        if os.path.exists(final_mp4_path) and not args.force:
            print(f"  Final video already exists at {final_mp4_path}. Skipping. (Use --force to overwrite)")
            continue

        # Create list file for FFmpeg
        temp_list_path = os.path.join(output_dir, f"temp_surah_{surah_num:03d}_concat_{lang}.txt")
        try:
            with open(temp_list_path, 'w', encoding='utf-8') as lf:
                for label, v_path in input_videos:
                    # Windows requires forward slashes
                    safe_path = v_path.replace("\\", "/")
                    lf.write(f"file '{safe_path}'\n")
        except Exception as e:
            print(f"  [Error] Failed to write temp concat file {temp_list_path}: {e}")
            continue

        print(f"  Stitching {len(input_videos)} videos into: {final_mp4_path}")
        ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', temp_list_path, '-c', 'copy', final_mp4_path]
        
        success, stdout, stderr = run_command(ffmpeg_cmd, timeout=600)

        # Cleanup
        if os.path.exists(temp_list_path):
            os.remove(temp_list_path)

        if success:
            print(f"  [Success] Saved final Surah video to: {final_mp4_path}")
        else:
            print(f"  [Error] FFmpeg concatenation failed for Surah {surah_num} ({lang}):\n{stderr}")

    print("\nSurah final assembly finished.")

if __name__ == "__main__":
    main()
