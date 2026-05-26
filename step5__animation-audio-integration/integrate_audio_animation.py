#!/usr/bin/env python3
import os
import sys
import json
import re
import time
import math
import urllib.request
import urllib.error
import argparse
import asyncio
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import edge_tts
from mutagen.mp3 import MP3

# Ensure UTF-8 output on Windows terminal
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Add root directory to sys.path to import api_logger
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
if root_dir not in sys.path:
    sys.path.append(root_dir)
import api_logger

# Configurable Gemini Models for transliteration
PRIMARY_GEMINI_MODEL = "models/gemini-3.1-flash-lite"
FALLBACK_GEMINI_MODEL = "models/gemini-2.5-flash-lite"

GEMINI_DISABLED = False

def load_env_vars(filepath):
    env_vars = {}
    if not os.path.exists(filepath):
        return env_vars
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    key = key.strip()
                    val = val.strip()
                    env_vars[key] = val
    except Exception as e:
        print(f"Warning: Failed to parse .env file: {e}")
    return env_vars

def call_gemini_api(model, prompt, step_name, abs_ruku, surph_num, surah_name, rel_ruku):
    global GEMINI_DISABLED
    if GEMINI_DISABLED:
        return None
        
    max_retries = 7  # Allow rotating through all 7 keys
    current_model = model
    
    for attempt in range(1, max_retries + 1):
        key_name, api_key = api_logger.get_next_api_key(step_name)
        if not api_key:
            print("  Error: No API keys loaded.")
            GEMINI_DISABLED = True
            return None
            
        url = f"https://generativelanguage.googleapis.com/v1beta/{current_model}:generateContent?key={api_key}"
        headers = {
            "Content-Type": "application/json"
        }
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ]
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
                    step_name, abs_ruku, surph_num, surah_name, rel_ruku,
                    current_model, key_name, "Success", input_tokens, output_tokens
                )
                return response_text
        except urllib.error.HTTPError as e:
            try:
                err_msg = e.read().decode('utf-8')
            except Exception:
                err_msg = e.reason
            print(f"  [Attempt {attempt}/{max_retries} with {key_name}] HTTP Error {e.code}: {e.reason}. Detail: {err_msg}")
            
            api_logger.log_api_call(
                step_name, abs_ruku, surph_num, surah_name, rel_ruku,
                current_model, key_name, f"HTTP Error {e.code}", None, None
            )
            
            if e.code == 429:
                if current_model == PRIMARY_GEMINI_MODEL:
                    print(f"  [Rate Limit Active] Swapping from {PRIMARY_GEMINI_MODEL} to fallback {FALLBACK_GEMINI_MODEL}")
                    current_model = FALLBACK_GEMINI_MODEL
                else:
                    print(f"  [Rate Limit Active] Rotating key and retrying...")
                time.sleep(2)
                continue
        except Exception as e:
            print(f"  [Attempt {attempt}/{max_retries} with {key_name}] Error: {e}")
            api_logger.log_api_call(
                step_name, abs_ruku, surph_num, surah_name, rel_ruku,
                current_model, key_name, f"Error: {str(e)[:50]}", None, None
            )
            
        if attempt < max_retries:
            time.sleep(1)
            
    GEMINI_DISABLED = True
    print("\n  [WARNING] All Gemini API keys are exhausted or rate-limited. Short-circuiting to direct Roman Urdu fallback for remaining scenes.")
    return None

def strip_markdown_code_blocks(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        if text.endswith("```"):
            text = text[:-3].strip()
    return text

def transliterate_roman_to_nastaliq(text, abs_ruku, surah_number, surah_name, rel_ruku):
    """
    Transliterates Roman Urdu text to Nastaliq Urdu using Gemini API.
    """
    prompt = f"""You are an expert Urdu transliterator. Convert the following Roman Urdu text (written in Latin alphabet) into standard, formal Nastaliq Urdu script.
=== Rules ===
1. DO NOT translate the text. Preserve the exact words and meaning. Only change the script from Latin (Roman Urdu) to Arabic/Nastaliq script.
2. Keep any English words that are in brackets (like [Pause 2 seconds]) exactly as they are.
3. Return ONLY the transliterated Nastaliq Urdu text, without any introductory or concluding text, explanation, or markdown code blocks.

Text: {text}"""
    
    response = call_gemini_api(PRIMARY_GEMINI_MODEL, prompt, "step5", abs_ruku, surah_number, surah_name, rel_ruku)
    if response:
        return strip_markdown_code_blocks(response)
    return text

def download_arabic_recitation(surah, verse, output_path, reciter="Alafasy_128kbps"):
    """
    Downloads Arabic recitation mp3 from everyayah.com.
    """
    url = f"https://everyayah.com/data/{reciter}/{surah:03d}{verse:03d}.mp3"
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f"    Downloading Arabic recitation for {surah}:{verse}...")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=60) as response, open(output_path, 'wb') as out_file:
                out_file.write(response.read())
            print(f"      Saved recitation to {output_path}")
            return True
        except Exception as e:
            print(f"      [Attempt {attempt}/{max_retries}] Error downloading recitation: {e}")
            if attempt < max_retries:
                time.sleep(2)
    return False

def generate_polly_audio(polly_client, text, output_path):
    """
    Generates English speech audio using AWS Polly (Matthew Generative).
    """
    cleaned_text = re.sub(r'\[.*?\]', '', text).strip()
    if not cleaned_text:
        return False
        
    try:
        response = polly_client.synthesize_speech(
            Text=cleaned_text,
            OutputFormat='mp3',
            VoiceId='Matthew',
            Engine='generative'
        )
    except (BotoCoreError, ClientError) as e:
        print(f"      Generative engine failed or not supported. Falling back to neural engine... Error: {e}")
        try:
            response = polly_client.synthesize_speech(
                Text=cleaned_text,
                OutputFormat='mp3',
                VoiceId='Matthew',
                Engine='neural'
            )
        except Exception as ex:
            print(f"      Neural engine failed. Falling back to standard engine... Error: {ex}")
            response = polly_client.synthesize_speech(
                Text=cleaned_text,
                OutputFormat='mp3',
                VoiceId='Matthew',
                Engine='standard'
            )
            
    try:
        with open(output_path, 'wb') as f:
            f.write(response['AudioStream'].read())
        print(f"      Saved Polly audio to {output_path}")
        return True
    except Exception as e:
        print(f"      Error writing Polly audio file: {e}")
        return False

async def generate_edge_tts_audio(text, output_path, voice="ur-PK-AsadNeural"):
    """
    Generates Urdu speech audio using Edge TTS.
    """
    cleaned_text = re.sub(r'\[.*?\]', '', text).strip()
    if not cleaned_text:
        return False
        
    try:
        communicate = edge_tts.Communicate(cleaned_text, voice)
        await communicate.save(output_path)
        print(f"      Saved Edge TTS audio to {output_path}")
        return True
    except Exception as e:
        print(f"      Error generating Edge TTS audio: {e}")
        return False

def get_audio_duration(path):
    """
    Measures duration of MP3 file in seconds.
    """
    try:
        audio = MP3(path)
        return audio.info.length
    except Exception as e:
        print(f"      Warning: mutagen failed to read duration: {e}. Falling back to ffprobe estimation.")
        try:
            import subprocess
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return float(result.stdout.strip())
        except Exception as ex:
            print(f"      Warning: ffprobe failed: {ex}. Using default estimation of 5 seconds.")
            return 5.0

def parse_pause_seconds(text):
    """
    Parses pattern [Pause X seconds] or [Pause X s] to extract floating point value.
    """
    match = re.search(r'\[Pause\s+(\d+(?:\.\d+)?)\s*seconds?\]', text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match_s = re.search(r'\[Pause\s+(\d+(?:\.\d+)?)\s*s\]', text, re.IGNORECASE)
    if match_s:
        return float(match_s.group(1))
    return 0.0

def parse_recitation_verse(text):
    """
    Matches 'Recite Verse X' or '[Recite Verse X]' and returns the integer verse number.
    """
    match = re.search(r'(?:Recite\s+Verse\s+|\[Recite\s+Verse\s+)(\d+)', text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

async def process_subblock(subblock_path, output_dir, lang, polly_client,
                           surah_num_fallback, rel_ruku_fallback, abs_ruku_fallback, surah_name_fallback):
    """
    Processes a single subblock, generating audio files and updating the JSON metadata.
    """
    subblock_id = os.path.basename(subblock_path).replace(".json", "")
    print(f"    Processing Subblock: {subblock_id}")
    
    with open(subblock_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    surah_num = data.get("surah_number") or surah_num_fallback
    rel_ruku = data.get("relative_ruku") or rel_ruku_fallback
    abs_ruku = data.get("absolute_ruku") or abs_ruku_fallback
    surah_name = data.get("surah_name") or surah_name_fallback
    
    audio_dir = os.path.join(output_dir, "audio", subblock_id)
    os.makedirs(audio_dir, exist_ok=True)
    
    # Batch Transliteration for Roman Urdu to Nastaliq Urdu to optimize Gemini API calls
    transliterated_map = {}
    if lang == "ur" and not GEMINI_DISABLED:
        scenes_to_transliterate = []
        for scene in data.get("scenes", []):
            script_text = scene["script"]
            recitation_verse = parse_recitation_verse(script_text)
            if recitation_verse is None:
                cleaned_roman = re.sub(r'\[.*?\]', '', script_text).strip()
                if cleaned_roman:
                    scenes_to_transliterate.append((scene["scene_no"], cleaned_roman))
                    
        if scenes_to_transliterate:
            combined_prompt_text = ""
            for s_no, text in scenes_to_transliterate:
                combined_prompt_text += f"===SCENE {s_no}===\n{text}\n"
                
            prompt = f"""You are an expert Urdu transliterator. Convert the following list of Roman Urdu scenes (written in Latin alphabet) into standard, formal Nastaliq Urdu script.
=== Rules ===
1. DO NOT translate the text. Preserve the exact words and meaning. Only change the script from Latin (Roman Urdu) to Arabic/Nastaliq script.
2. Keep the delimiters `===SCENE X===` exactly as they are. Do not alter or translate them.
3. Return ONLY the transliterated Nastaliq Urdu scenes with their delimiters, without any introductory or concluding text, explanation, or markdown code blocks.

Text:
{combined_prompt_text}"""
            
            print(f"      Batch transliterating {len(scenes_to_transliterate)} scenes using Gemini...")
            gemini_res = call_gemini_api(PRIMARY_GEMINI_MODEL, prompt, "step5", abs_ruku, surah_num, surah_name, rel_ruku)
            if gemini_res:
                cleaned_res = strip_markdown_code_blocks(gemini_res)
                parts = re.split(r'===SCENE\s+(\d+)===', cleaned_res)
                for i in range(1, len(parts), 2):
                    if i + 1 < len(parts):
                        try:
                            s_no = int(parts[i].strip())
                            s_text = parts[i+1].strip()
                            transliterated_map[s_no] = s_text
                        except ValueError:
                            pass
            
            print(f"      Successfully transliterated {len(transliterated_map)} / {len(scenes_to_transliterate)} scenes.")
 
    updated_scenes = []
    
    for scene in data.get("scenes", []):
        scene_no = scene["scene_no"]
        script_text = scene["script"]
        remarks = scene.get("remarks", "")
        
        # Clean print content from non-ASCII/CP1252 limits for log safety
        print_safe_script = re.sub(r'[^\x00-\x7F]+', '?', script_text[:60])
        print(f"      Scene {scene_no}: {print_safe_script}...")
        
        audio_filename = f"scene_{scene_no}.mp3"
        audio_filepath = os.path.join(audio_dir, audio_filename)
        relative_audio_path = f"output_resources/surah_{surah_num:03d}/ruku_{rel_ruku}_{abs_ruku}/{lang}/audio/{subblock_id}/{audio_filename}"
        
        recitation_verse = parse_recitation_verse(script_text)
        pause_duration = parse_pause_seconds(script_text) or parse_pause_seconds(remarks)
        
        audio_success = False
        duration_seconds = 0.0
        
        if recitation_verse is not None:
            audio_success = download_arabic_recitation(surah_num, recitation_verse, audio_filepath)
        else:
            if lang == "en":
                audio_success = generate_polly_audio(polly_client, script_text, audio_filepath)
            elif lang == "ur":
                cleaned_roman = re.sub(r'\[.*?\]', '', script_text).strip()
                if cleaned_roman:
                    nastaliq_text = None
                    if not GEMINI_DISABLED:
                        nastaliq_text = transliterated_map.get(scene_no)
                        if not nastaliq_text:
                            print(f"        Warning: Scene {scene_no} missing from batch. Transliterating individually...")
                            nastaliq_text = transliterate_roman_to_nastaliq(cleaned_roman, abs_ruku, surah_num, surah_name, rel_ruku)
                    
                    if not nastaliq_text:
                        # Fallback directly to Roman Urdu if Gemini is exhausted
                        nastaliq_text = cleaned_roman
                        
                    print_safe_nastaliq = re.sub(r'[^\x00-\x7F]+', '?', nastaliq_text)
                    print(f"        Nastaliq/Text: {print_safe_nastaliq}")
                    audio_success = await generate_edge_tts_audio(nastaliq_text, audio_filepath)
                
        if audio_success:
            duration_seconds = get_audio_duration(audio_filepath)
            print(f"        Audio duration: {duration_seconds:.2f}s")
        else:
            print(f"        No audio generated for this scene.")
            duration_seconds = 0.0
            
        total_duration_seconds = duration_seconds + pause_duration
        total_duration_frames = int(math.ceil(total_duration_seconds * 30))
        
        scene["audio_path"] = relative_audio_path if audio_success else None
        scene["audio_duration_seconds"] = duration_seconds
        scene["pause_duration_seconds"] = pause_duration
        scene["duration_seconds"] = total_duration_seconds
        scene["duration_frames"] = total_duration_frames
        
        updated_scenes.append(scene)
        
    data["scenes"] = updated_scenes
    
    # Save the updated JSON
    output_json_path = os.path.join(output_dir, f"{subblock_id}.json")
    with open(output_json_path, 'w', encoding='utf-8') as out_f:
        json.dump(data, out_f, ensure_ascii=False, indent=2)
    print(f"    Saved updated subblock to {output_json_path}")
    return True

async def main_async():
    parser = argparse.ArgumentParser(description="Step 5: Animation-Audio Integration Pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of blocks to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--block", type=int, default=None, help="Process a specific block index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed entries.")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Process specific tracks.")
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    # Load environment variables
    keys = api_logger.load_env_keys()
    env = load_env_vars(os.path.join(root_dir, ".env"))
    
    if not keys:
        print("Error: No Gemini API keys found in .env.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Loaded {len(keys)} Gemini API keys for quota rotation.")
    
    aws_access_key = env.get("AWS_ACCESS_KEY_ID")
    aws_secret_key = env.get("AWS_SECRET_ACCESS_KEY")
    aws_region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    
    polly_client = None
    if args.lang in ["en", "both"]:
        if not aws_access_key or not aws_secret_key:
            print("Error: AWS credentials not found in .env.", file=sys.stderr)
            sys.exit(1)
        try:
            polly_client = boto3.client(
                'polly',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
                region_name=aws_region
            )
        except Exception as e:
            print(f"Error initializing AWS Polly client: {e}", file=sys.stderr)
            sys.exit(1)
            
    languages = []
    if args.lang == "both":
        languages = ["en", "ur"]
    else:
        languages = [args.lang]
        
    for lang in languages:
        print(f"\n==========================================")
        print(f"Starting Step 5 integration for Track: {lang.upper()}")
        print(f"==========================================")
        
        todo_filename = f"todo_integration_{'english' if lang == 'en' else 'urdu'}.json"
        todo_path = os.path.join(script_dir, "guiding_resources", todo_filename)
        
        if not os.path.exists(todo_path):
            print(f"Error: Tracking file not found at {todo_path}", file=sys.stderr)
            continue
            
        with open(todo_path, 'r', encoding='utf-8') as f:
            todo_list = json.load(f)
            
        processed_blocks = 0
        
        for entry in todo_list:
            if args.limit is not None and processed_blocks >= args.limit:
                break
                
            if args.ruku is not None and entry["absolute_ruku"] != args.ruku:
                continue
                
            if args.block is not None and entry.get("block_no") != args.block:
                continue
                
            if entry["completed"] and not args.force:
                continue
                
            abs_ruku = entry["absolute_ruku"]
            surah_num = entry["surah_number"]
            surah_name = entry["surah_name"]
            rel_ruku = entry["relative_ruku"]
            block_idx = entry["block_no"]
            
            print(f"\n>>> Processing Ruku {abs_ruku} Block {block_idx} (Surah {surah_num:03d} {surah_name})")
            
            step4_dir = os.path.join(
                root_dir, "step4__script-visual-division", "output_resources",
                f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", lang
            )
            
            manifest_path = os.path.join(step4_dir, "subblocks_manifest.json")
            if not os.path.exists(manifest_path):
                print(f"  Warning: Step 4 manifest not found at {manifest_path}. Skipping.")
                continue
                
            output_dir = os.path.join(
                script_dir, "remotion_project", "public", "output_resources", f"surah_{surah_num:03d}", f"ruku_{rel_ruku}_{abs_ruku}", lang
            )
            os.makedirs(output_dir, exist_ok=True)
            
            with open(manifest_path, 'r', encoding='utf-8') as f_man:
                manifest = json.load(f_man)
                
            success = True
            processed_subblocks_in_manifest = []
            for subblock_entry in manifest:
                if subblock_entry.get("block_no") != block_idx:
                    continue
                    
                filename = subblock_entry["filename"]
                subblock_json_path = os.path.join(step4_dir, filename)
                
                if not os.path.exists(subblock_json_path):
                    print(f"  Warning: Subblock file not found at {subblock_json_path}. Skipping subblock.")
                    continue
                    
                subblock_success = await process_subblock(
                    subblock_json_path, output_dir, lang, polly_client,
                    surah_num, rel_ruku, abs_ruku, surah_name
                )
                if not subblock_success:
                    success = False
                else:
                    processed_subblocks_in_manifest.append(subblock_entry)
                    
            if success:
                dest_manifest_path = os.path.join(output_dir, "subblocks_manifest.json")
                existing_manifest = []
                if os.path.exists(dest_manifest_path):
                    try:
                        with open(dest_manifest_path, 'r', encoding='utf-8') as f_dest:
                            existing_manifest = json.load(f_dest)
                    except Exception as e:
                        print(f"  Warning: Could not read existing manifest in step 5: {e}")
                
                existing_manifest = [m for m in existing_manifest if m.get("block_no") != block_idx]
                combined_manifest = existing_manifest + processed_subblocks_in_manifest
                combined_manifest.sort(key=lambda x: (x.get("block_no", 0), x.get("subblock_id", "")))
                
                try:
                    with open(dest_manifest_path, 'w', encoding='utf-8') as f_out:
                        json.dump(combined_manifest, f_out, ensure_ascii=False, indent=2)
                    print(f"  Wrote combined subblock manifest: {dest_manifest_path}")
                except Exception as e:
                    print(f"  Error writing manifest file {dest_manifest_path}: {e}")
                    success = False
                    
            if success:
                entry["completed"] = True
                with open(todo_path, 'w', encoding='utf-8') as f_todo:
                    json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
                processed_blocks += 1
                print(f"  Completed integration for Ruku {abs_ruku} Block {block_idx}.")
            else:
                print(f"  Failed integration for Ruku {abs_ruku} Block {block_idx}.")
                
    print("\nIntegration finished.")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
