#!/usr/bin/env python3
import os
import sys
import json
import time
import re
import urllib.request
import urllib.error
import argparse
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_logger

# Configurable Gemini Model
GEMINI_MODEL = "models/gemini-3.5-flash"

SYSTEM_PROMPT = """You are an expert video director, motion graphics designer, and creative director.
Your task is to take a Ruku block script and convert it into a structured "Visual Directions Sheet" in JSON format, split by scenes.

=== Scene Structure Rules ===
1. **Scene 1 (Title)**: The very first scene must contain the Title line in the script field.
2. **Arabic Recitation & Translation (if present)**: Each verse recitation cue and the translation line must be separated into their own individual scenes *before* the exegesis commentary begins.
3. **Commentary Breakdown**: Break the narrator commentary down into small, natural spoken sentences or phrases. Each scene row must contain only 5 to 10 seconds of spoken text (approx. 10 to 18 words).

=== Visual & Art Direction Guidelines ===
1. **Content-Driven Shifting Themes**:
   - For warning/theological blocks: Use a deep slate / midnight blue / dark charcoal palette with matte gold accents.
   - For mercy/guidance blocks: Use an emerald green / sage / warm taupe palette with gold accents.
   - For historical blocks: Use a warm terracotta / copper / dark sand palette with copper/gold accents.
2. **Prioritize Motion Graphics & Typography**: Use abstract motion graphics, fluid liquid gradients, and kinetic typography. Recommend clean, modern sans-serif fonts for the translation/narration and elegant Thuluth/Naskh calligraphic animations for Arabic.
3. **Hybrid Assets (Custom Images)**: Recommend cinematic, detailed custom illustrations/3D renders only when representing concrete historical details or key metaphors. When doing so, write a highly descriptive prompt (optimized for Midjourney/FLUX) directly within the "visuals" field. Specify style (e.g. "minimalist 3D render", "moody watercolor illustration", "ancient parchment texture"), framing, and lighting.
4. **English Descriptions**: All visual descriptions and remarks must be written in English. The script field must preserve the original language (English).
"""

RESPONSE_SCHEMA = {
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
                    "visuals": {
                        "type": "STRING",
                        "description": "Detailed description of the visual scene in English. Includes color palette, background elements, kinetic typography details, and cinematic image prompts if a custom illustration is recommended."
                    },
                    "remarks": {
                        "type": "STRING",
                        "description": "Notes on delivery tone, pacing, emphasis, and sound effects cues in English."
                    }
                },
                "required": ["scene_no", "script", "visuals", "remarks"]
            }
        }
    },
    "required": ["scenes"]
}

def load_env(filepath):
    if not os.path.exists(filepath):
        return {}
    env_vars = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    env_vars[key.strip()] = val.strip()
    except Exception as e:
        print(f"Warning: Failed to parse .env file: {e}")
    return env_vars

def call_gemini_api(model, system_prompt, user_content, response_schema, step_name, abs_ruku, surah_number, surah_name, rel_ruku):
    max_retries = 7
    for attempt in range(1, max_retries + 1):
        key_name, api_key = api_logger.get_next_api_key(step_name)
        if not api_key:
            print("  Error: No API keys loaded.")
            return None
            
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{system_prompt}\n\nInput Context:\n{user_content}"}
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": response_schema
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                response_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                
                input_tokens = None
                output_tokens = None
                if "usageMetadata" in res_data:
                    input_tokens = res_data["usageMetadata"].get("promptTokenCount")
                    output_tokens = res_data["usageMetadata"].get("candidatesTokenCount")
                    
                api_logger.log_api_call(
                    step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                    model, key_name, "Success", input_tokens, output_tokens
                )
                return response_text
        except urllib.error.HTTPError as e:
            try:
                err_msg = e.read().decode('utf-8')
            except Exception:
                err_msg = e.reason
            print(f"  [Attempt {attempt}/{max_retries} with {key_name}] HTTP Error {e.code}: {e.reason}. Detail: {err_msg}")
            
            api_logger.log_api_call(
                step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                model, key_name, f"HTTP Error {e.code}", None, None
            )
            
            if e.code == 429:
                print(f"  [Rate Limit Active] Rotating key and retrying...")
                time.sleep(2)
                continue
        except Exception as e:
            print(f"  [Attempt {attempt}/{max_retries} with {key_name}] Error: {e}")
            api_logger.log_api_call(
                step_name, abs_ruku, surah_number, surah_name, rel_ruku,
                model, key_name, f"Error: {str(e)[:50]}", None, None
            )
            
        if attempt < max_retries:
            time.sleep(1)
            
    return None

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

def subdivide_block_scenes(block_data, metadata, max_tafseer_scenes=6):
    scenes = block_data.get("scenes", [])
    
    phase1_scenes = []
    phase2_scenes = []
    phase3_scenes = []
    
    state = 1  # 1: Title/TOC, 2: Recitation/Translation, 3: Tafseer
    
    for scene in scenes:
        script = scene.get("script", "")
        is_recitation = "[Recite Verse" in script or "Translation:" in script or "Translation (cont):" in script
        
        if state == 1:
            if is_recitation:
                state = 2
                phase2_scenes.append(scene)
            else:
                phase1_scenes.append(scene)
        elif state == 2:
            if not is_recitation:
                state = 3
                phase3_scenes.append(scene)
            else:
                phase2_scenes.append(scene)
        elif state == 3:
            phase3_scenes.append(scene)
            
    subblocks = []
    block_no = metadata.get("block_no", 1)
    
    base_meta = {
        "surah_number": metadata.get("surah_number"),
        "surah_name": metadata.get("surah_name"),
        "absolute_ruku": metadata.get("absolute_ruku"),
        "relative_ruku": metadata.get("relative_ruku"),
        "verses": metadata.get("verses"),
        "ruku_heading": metadata.get("ruku_heading"),
        "block_no": block_no
    }
    
    # 1. Phase 1 subblock
    if phase1_scenes:
        subblocks.append({
            **base_meta,
            "subblock_id": f"block_{block_no}_phase_1",
            "subblock_type": "title_toc",
            "scenes": phase1_scenes
        })
        
    # 2. Phase 2 subblock
    if phase2_scenes:
        subblocks.append({
            **base_meta,
            "subblock_id": f"block_{block_no}_phase_2",
            "subblock_type": "verses",
            "scenes": phase2_scenes
        })
        
    # 3. Phase 3 subblocks (split if large)
    if phase3_scenes:
        if len(phase3_scenes) <= max_tafseer_scenes:
            subblocks.append({
                **base_meta,
                "subblock_id": f"block_{block_no}_phase_3",
                "subblock_type": "tafseer",
                "scenes": phase3_scenes
            })
        else:
            chunks = [phase3_scenes[i:i + max_tafseer_scenes] for i in range(0, len(phase3_scenes), max_tafseer_scenes)]
            for idx, chunk in enumerate(chunks):
                subblocks.append({
                    **base_meta,
                    "subblock_id": f"block_{block_no}_phase_3_{idx + 1}",
                    "subblock_type": "tafseer",
                    "scenes": chunk
                })
                
    return subblocks

def process_track(api_key, script_dir, root_dir, limit, ruku_filter, force_flag, delay, max_tafseer_scenes):
    print("\n===== Starting Step 4 (ENGLISH Track - Combined) =====")
    todo_path = os.path.join(script_dir, "guiding_resources", "todo_visuals_english.json")
    
    if not os.path.exists(todo_path):
        print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
        return
        
    with open(todo_path, "r", encoding="utf-8") as f:
        todo_list = json.load(f)
        
    processed_rukus = 0
    
    for entry in todo_list:
        if limit is not None and processed_rukus >= limit:
            print(f"Reached processing limit of {limit} Rukus for ENGLISH track. Stopping.")
            break
            
        if ruku_filter is not None and entry["absolute_ruku"] != ruku_filter:
            continue
            
        if entry["completed"] and not force_flag:
            continue
            
        abs_ruku = entry["absolute_ruku"]
        surah_num = entry["surah_number"]
        surah_name = entry["surah_name"]
        rel_ruku = entry["relative_ruku"]
        
        print(f"\n>>> [ENGLISH] Generating Visual & Subdivided Sheets for Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})")
        
        # Directory of generated Step 3 script blocks
        input_script_dir = os.path.join(
            root_dir, "step3__combined-script", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", "en"
        )
        
        if not os.path.exists(input_script_dir):
            print(f"  Error: Input script folder not found at {input_script_dir}. Skipping Ruku.")
            continue
            
        # Find script block files
        block_files = []
        for f in os.listdir(input_script_dir):
            if f.startswith("block_") and f.endswith(".md"):
                match = re.search(r'block_(\d+)\.md', f)
                if match:
                    block_files.append((int(match.group(1)), f))
                    
        if not block_files:
            print(f"  Warning: No script block files found in {input_script_dir}. Skipping Ruku.")
            continue
            
        block_files.sort(key=lambda x: x[0])
        
        output_dir = os.path.join(
            root_dir, "step4__script-visual-division", "output_resources",
            f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", "en"
        )
        os.makedirs(output_dir, exist_ok=True)
        
        ruku_success = True
        subblocks_manifest = []
        
        for idx, filename in block_files:
            script_filepath = os.path.join(input_script_dir, filename)
            print(f"  Processing Block {idx} script file: {filename}")
            
            yaml_meta, script_text = parse_markdown_with_yaml(script_filepath)
            
            if not script_text:
                print(f"    Error: Failed to parse content from {script_filepath}. Skipping block.")
                ruku_success = False
                break
                
            input_payload = {
                "ruku_metadata": {
                    "surah_number": surah_num,
                    "surah_name": surah_name,
                    "absolute_ruku": abs_ruku,
                    "relative_ruku": rel_ruku
                },
                "block_script": {
                    "verses": yaml_meta.get("verses", "Concept"),
                    "title": yaml_meta.get("title", ""),
                    "script_text": script_text
                }
            }
            
            ai_response = call_gemini_api(
                GEMINI_MODEL,
                SYSTEM_PROMPT,
                json.dumps(input_payload, ensure_ascii=False, indent=2),
                RESPONSE_SCHEMA,
                "step4", abs_ruku, surah_num, surah_name, rel_ruku
            )
            
            if not ai_response:
                print(f"    Error: Failed to generate visual directions sheet for Block {idx}. Skipping Ruku.")
                ruku_success = False
                break
                
            # Verify and save the output
            out_filename = f"block_{idx}_visuals.json"
            out_filepath = os.path.join(output_dir, out_filename)
            
            try:
                # Load response text into JSON to ensure it is valid JSON
                json_data = json.loads(ai_response)
                
                # Write to output file
                with open(out_filepath, "w", encoding="utf-8") as f_out:
                    json.dump(json_data, f_out, ensure_ascii=False, indent=2)
                print(f"    Saved visual sheet to {out_filepath}")
            except Exception as e:
                print(f"    Error validating or writing visual sheet to {out_filepath}: {e}")
                ruku_success = False
                break
                
            # Perform subdivision immediately!
            block_metadata = {
                "surah_number": surah_num,
                "surah_name": surah_name,
                "absolute_ruku": abs_ruku,
                "relative_ruku": rel_ruku,
                "verses": yaml_meta.get("verses", "Concept"),
                "ruku_heading": yaml_meta.get("title", ""),
                "block_no": idx
            }
            
            subblocks = subdivide_block_scenes(json_data, block_metadata, max_tafseer_scenes)
            
            # Save subblocks to output dir
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
                    ruku_success = False
                    break
                    
                # Add to manifest
                subblocks_manifest.append({
                    "block_no": idx,
                    "subblock_id": sb_id,
                    "subblock_type": sb["subblock_type"],
                    "filename": sb_filename,
                    "scene_count": len(sb["scenes"])
                })
                
            if not ruku_success:
                break
                
            time.sleep(delay)
            
        if ruku_success:
            # Write manifest file
            manifest_path = os.path.join(output_dir, "subblocks_manifest.json")
            try:
                with open(manifest_path, "w", encoding="utf-8") as f_manifest:
                    json.dump(subblocks_manifest, f_manifest, ensure_ascii=False, indent=2)
                print(f"    Wrote subblock manifest: {manifest_path}")
            except Exception as e:
                print(f"    Error writing manifest file {manifest_path}: {e}")
                ruku_success = False
                
        if ruku_success:
            entry["completed"] = True
            with open(todo_path, "w", encoding="utf-8") as f_todo:
                json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
            processed_rukus += 1

def main():
    parser = argparse.ArgumentParser(description="Generate scene-by-scene English visual directions and subdivide them into phase blocks.")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed entries.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between successful API calls.")
    parser.add_argument("--max-tafseer-scenes", type=int, default=6, help="Max scenes per commentary subblock.")
    args = parser.parse_args()

    # Determine directories
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    # Load environment variables via api_logger
    keys = api_logger.load_env_keys()
    if not keys:
        print("Error: No GEMINI_API_KEY_1 through GEMINI_API_KEY_7 found in .env file.", file=sys.stderr)
        sys.exit(1)
    api_key = None
        
    process_track(api_key, script_dir, root_dir, args.limit, args.ruku, args.force, args.delay, args.max_tafseer_scenes)
    print("\nEnglish combined Step 4 and 5 processing finished.")

if __name__ == "__main__":
    main()
