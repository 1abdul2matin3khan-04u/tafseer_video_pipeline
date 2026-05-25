#!/usr/bin/env python3
import os
import json
import argparse
import sys

def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract a specific Ruku from Quran Tafseer and Word-by-Word JSON sources."
    )
    
    # Absolute Ruku option
    parser.add_argument(
        "--ruku",
        type=int,
        help="Absolute Ruku index (1 to 558).",
    )
    
    # Relative Ruku options
    parser.add_argument(
        "--surah",
        type=int,
        help="Surah number (1 to 114). Required if --relative-ruku is used.",
    )
    parser.add_argument(
        "--relative-ruku",
        type=int,
        help="Relative Ruku index within the Surah (1-based). Required if --surah is used.",
    )
    
    # Run for all rukus option
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run extraction for all 558 Rukus.",
    )
    
    parser.add_argument(
        "--output-dir",
        type=str,
        default="step0__whole-single/output_resources",
        help="Directory to save extracted Ruku JSON files, relative to --root-dir (default: step0__whole-single/output_resources)."
    )
    
    parser.add_argument(
        "--workspace-dir",
        type=str,
        default="step0__whole-single/input_resources",
        help="Path to the workspace directory containing source folders and rukuDivision.json, relative to --root-dir (default: step0__whole-single/input_resources)."
    )
    
    parser.add_argument(
        "--root-dir",
        type=str,
        default=".",
        help="Root of the project (parent of all step folders). All relative paths are resolved from here. Defaults to current directory."
    )

    args = parser.parse_args()
    
    # Validation of argument combinations
    if args.all:
        if args.ruku is not None or args.surah is not None or args.relative_ruku is not None:
            parser.error("Cannot combine --all with --ruku or --surah/--relative-ruku.")
    elif args.ruku is not None:
        if args.surah is not None or args.relative_ruku is not None:
            parser.error("Cannot combine --ruku with --surah/--relative-ruku. Use one or the other.")
        if not (1 <= args.ruku <= 558):
            parser.error("Absolute Ruku index (--ruku) must be between 1 and 558 inclusive.")
    else:
        if args.surah is None or args.relative_ruku is None:
            parser.error("You must specify --all, --ruku, OR both --surah and --relative-ruku.")
        if not (1 <= args.surah <= 114):
            parser.error("Surah number (--surah) must be between 1 and 114 inclusive.")
        if args.relative_ruku < 1:
            parser.error("Relative Ruku index (--relative-ruku) must be greater than or equal to 1.")

    return args

def load_ruku_mapping(mapping_file_path):
    if not os.path.exists(mapping_file_path):
        print(f"Error: Mapping file '{mapping_file_path}' not found.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(mapping_file_path, 'r', encoding='utf-8') as f:
            surahs = json.load(f)
    except Exception as e:
        print(f"Error reading mapping file: {e}", file=sys.stderr)
        sys.exit(1)
        
    abs_to_ruku = {}
    rel_to_abs = {}
    
    abs_idx = 1
    for surah in surahs:
        surah_num = surah.get('surah_number')
        surah_name = surah.get('surah_name', '')
        verse_ranges = surah.get('verse_ranges', [])
        
        for rel_idx, range_str in enumerate(verse_ranges):
            rel_ruku_num = rel_idx + 1
            info = {
                'absolute_ruku': abs_idx,
                'surah_number': surah_num,
                'surah_name': surah_name,
                'relative_ruku': rel_ruku_num,
                'verse_range_str': range_str,
                'meaning': surah.get('meaning', '')
            }
            abs_to_ruku[abs_idx] = info
            rel_to_abs[(surah_num, rel_ruku_num)] = info
            abs_idx += 1
            
    return abs_to_ruku, rel_to_abs

def parse_verse_range(range_str):
    if '-' in range_str:
        parts = range_str.split('-')
        return int(parts[0]), int(parts[1])
    else:
        val = int(range_str)
        return val, val

def check_reference(source_name, key, value, surah_num, start_verse, end_verse):
    """
    Check if the value is a string reference and if the target lies outside the extracted range.
    Logs a warning if it does.
    """
    if not isinstance(value, str):
        return
        
    # Check format: either "surah:verse" or "verse"
    try:
        if ':' in value:
            ref_parts = value.split(':')
            ref_surah = int(ref_parts[0])
            ref_verse = int(ref_parts[1])
        else:
            ref_surah = surah_num
            ref_verse = int(value)
            
        is_inside = (ref_surah == surah_num) and (start_verse <= ref_verse <= end_verse)
        if not is_inside:
            print(
                f"Warning: [{source_name}] Verse '{key}' references '{value}', which is OUTSIDE "
                f"the extracted Ruku range ({surah_num}:{start_verse} to {surah_num}:{end_verse})."
            )
    except ValueError:
        # If it's not a standard verse reference string, we don't warn about it.
        pass

def extract_ruku_data(target_info, args, loaded_files_cache=None, verbose=True):
    abs_ruku_num = target_info['absolute_ruku']
    surah_num = target_info['surah_number']
    surah_name = target_info['surah_name']
    rel_ruku_num = target_info['relative_ruku']
    verse_range_str = target_info['verse_range_str']
    
    start_verse, end_verse = parse_verse_range(verse_range_str)
    
    if verbose:
        print(f"--- Extracting Ruku {abs_ruku_num} (Surah {surah_num}, Ruku {rel_ruku_num}) ---")
        
    # 6 Sources to process
    sources = [
        "maarif-ul-quran",
        "tafseer-ibn-kathir",
        "tafsir-as-saadi",
        "tafsir-bayan-ul-quran",
        "tazkir-ul-quran",
        "wbw"
    ]
    
    # Ensure output directory exists for the specific Ruku (e.g. surah_002/ruku_1_6)
    output_dir_path = os.path.join(
        args.root_dir,
        args.output_dir, 
        f"surah_{surah_num:03d}", 
        f"ruku_{rel_ruku_num}_{abs_ruku_num}"
    )
    os.makedirs(output_dir_path, exist_ok=True)
    
    filename_surah = f"{surah_num:03d}.json"
    
    for source in sources:
        # Load from cache or file
        data = None
        if loaded_files_cache is not None and (source, surah_num) in loaded_files_cache:
            data = loaded_files_cache[(source, surah_num)]
        else:
            source_dir = os.path.join(args.root_dir, args.workspace_dir, source)
            file_path = os.path.join(source_dir, filename_surah)
            if not os.path.exists(file_path):
                if loaded_files_cache is not None:
                    loaded_files_cache[(source, surah_num)] = None
                continue
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if loaded_files_cache is not None:
                        loaded_files_cache[(source, surah_num)] = data
            except Exception as e:
                print(f"Error reading '{file_path}': {e}. Skipping source '{source}'.", file=sys.stderr)
                if loaded_files_cache is not None:
                    loaded_files_cache[(source, surah_num)] = None
                continue
                
        if data is None:
            continue
            
        extracted_data = {}
        
        # Extract verses in range
        for verse in range(start_verse, end_verse + 1):
            # Check format for keys
            if source == "wbw":
                lookup_key = f"{verse}"
            else:
                lookup_key = f"{surah_num}:{verse}"
                
            if lookup_key in data:
                val = data[lookup_key]
                
                # Check for references pointing outside the range
                check_reference(source, lookup_key, val, surah_num, start_verse, end_verse)
                
                # Apply word-by-word filtering if source is wbw
                if source == "wbw":
                    if isinstance(val, dict):
                        cleaned_val = {}
                        if 'w' in val:
                            cleaned_w = []
                            for word in val['w']:
                                if isinstance(word, dict):
                                    cleaned_word = {
                                        k: word[k]
                                        for k in ['c', 'd', 'e_en', 'e_ur', 'a']
                                        if k in word
                                    }
                                    cleaned_w.append(cleaned_word)
                                else:
                                    cleaned_w.append(word)
                            cleaned_val['w'] = cleaned_w
                        if 'a' in val:
                            cleaned_val['a'] = val['a']
                        val = cleaned_val
                
                extracted_data[lookup_key] = val
                
        # Write extracted data to output JSON file
        out_filename = f"{source}.json"
        out_file_path = os.path.join(output_dir_path, out_filename)
        
        try:
            with open(out_file_path, 'w', encoding='utf-8') as out_f:
                json.dump(extracted_data, out_f, ensure_ascii=False, indent=2)
            if verbose:
                print(f"Saved: {out_file_path}")
        except Exception as e:
            print(f"Error saving to '{out_file_path}': {e}", file=sys.stderr)
            

def main():
    args = parse_args()
    
    # Locate mapping file — always inside workspace_dir (resolved from root_dir)
    mapping_path = os.path.join(args.root_dir, args.workspace_dir, "rukuDivision.json")
    abs_to_ruku, rel_to_abs = load_ruku_mapping(mapping_path)
    
    if args.all:
        print("Extracting all 558 Rukus...")
        # Use cache to speed up loading
        loaded_files_cache = {}
        for ruku_id in sorted(abs_to_ruku.keys()):
            target_info = abs_to_ruku[ruku_id]
            # When extracting all, we only log minimal info to keep console clean, or we can log warnings
            extract_ruku_data(target_info, args, loaded_files_cache, verbose=False)
        print("All Rukus processed.")
    else:
        # Resolve the target Ruku info
        if args.ruku is not None:
            target_info = abs_to_ruku.get(args.ruku)
            if not target_info:
                print(f"Error: Absolute Ruku {args.ruku} not found in mapping.", file=sys.stderr)
                sys.exit(1)
        else:
            target_info = rel_to_abs.get((args.surah, args.relative_ruku))
            if not target_info:
                # Let's see if the surah exists but the relative ruku was out of range
                valid_rukus = [info for info in abs_to_ruku.values() if info['surah_number'] == args.surah]
                max_rukus = len(valid_rukus)
                if max_rukus == 0:
                    print(f"Error: Surah {args.surah} not found in mapping.", file=sys.stderr)
                else:
                    print(
                        f"Error: Relative Ruku {args.relative_ruku} is invalid for Surah {args.surah} "
                        f"(has only {max_rukus} Rukus).",
                        file=sys.stderr
                    )
                sys.exit(1)
                
        abs_ruku_num = target_info['absolute_ruku']
        surah_num = target_info['surah_number']
        surah_name = target_info['surah_name']
        rel_ruku_num = target_info['relative_ruku']
        verse_range_str = target_info['verse_range_str']
        
        start_verse, end_verse = parse_verse_range(verse_range_str)
        
        print(f"--- Ruku Extraction Target ---")
        print(f"Absolute Ruku:   {abs_ruku_num} / 558")
        print(f"Surah:           {surah_num} ({surah_name})")
        print(f"Relative Ruku:   {rel_ruku_num}")
        print(f"Verse Range:     {verse_range_str} (Verses {start_verse} to {end_verse})")
        print(f"------------------------------\n")
        
        extract_ruku_data(target_info, args, verbose=True)
        print("\nExtraction complete.")

if __name__ == "__main__":
    main()
