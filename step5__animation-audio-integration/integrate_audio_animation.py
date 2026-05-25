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

# Configurable Gemini Model for transliteration
GEMINI_MODEL = "models/gemini-2.5-flash-lite"

class GeminiKeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.current_index = 0
        self.consecutive_failures = 0
        self.all_keys_exhausted = False
        
    def get_current_key(self):
        if not self.keys or self.all_keys_exhausted:
            return None
        return self.keys[self.current_index]
        
    def rotate_key(self):
        if not self.keys:
            return None
        self.consecutive_failures += 1
        if self.consecutive_failures >= len(self.keys):
            self.all_keys_exhausted = True
            print("\n  [WARNING] All Gemini API keys are exhausted or rate-limited. Short-circuiting to direct Roman Urdu fallback for remaining scenes.")
            return None
        self.current_index = (self.current_index + 1) % len(self.keys)
        print(f"  [Key Rotation] Switched to Gemini API Key index {self.current_index} (ending in ...{self.keys[self.current_index][-6:]})")
        return self.get_current_key()
        
    def report_success(self):
        self.consecutive_failures = 0

# Global key manager instance
KEY_MANAGER = None

def load_env_keys(filepath):
    keys = []
    env_vars = {}
    if not os.path.exists(filepath):
        return keys, env_vars
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
                    if 'GEMINI_API_KEY' in key and val:
                        if val not in keys:
                            keys.append(val)
    except Exception as e:
        print(f"Warning: Failed to parse .env file: {e}")
    return keys, env_vars

def call_gemini_api(model, prompt):
    global KEY_MANAGER
    if not KEY_MANAGER or not KEY_MANAGER.keys or KEY_MANAGER.all_keys_exhausted:
        return None
        
    max_retries = len(KEY_MANAGER.keys)
    for attempt in range(1, max_retries + 1):
        api_key = KEY_MANAGER.get_current_key()
        if not api_key:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={api_key}"
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
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                KEY_MANAGER.report_success()
                return res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            # Switch key immediately on rate limits
            KEY_MANAGER.rotate_key()
            if KEY_MANAGER.all_keys_exhausted:
                break
        except Exception as e:
            KEY_MANAGER.rotate_key()
            if KEY_MANAGER.all_keys_exhausted:
                break
            
    return None

def strip_markdown_code_blocks(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        if text.endswith("```"):
            text = text[:-3].strip()
    return text

def transliterate_roman_to_nastaliq(text):
    """
    Transliterates Roman Urdu text to Nastaliq Urdu using Gemini API.
    """
    prompt = f"""You are an expert Urdu transliterator. Convert the following Roman Urdu text (written in Latin alphabet) into standard, formal Nastaliq Urdu script.
=== Rules ===
1. DO NOT translate the text. Preserve the exact words and meaning. Only change the script from Latin (Roman Urdu) to Arabic/Nastaliq script.
2. Keep any English words that are in brackets (like [Pause 2 seconds]) exactly as they are.
3. Return ONLY the transliterated Nastaliq Urdu text, without any introductory or concluding text, explanation, or markdown code blocks.

Text: {text}"""
    
    response = call_gemini_api(GEMINI_MODEL, prompt)
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

async def process_subblock(subblock_path, output_dir, lang, polly_client):
    """
    Processes a single subblock, generating audio files and updating the JSON metadata.
    """
    subblock_id = os.path.basename(subblock_path).replace(".json", "")
    print(f"    Processing Subblock: {subblock_id}")
    
    with open(subblock_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    surah_num = data.get("surah_number")
    rel_ruku = data.get("relative_ruku")
    abs_ruku = data.get("absolute_ruku")
    
    audio_dir = os.path.join(output_dir, "audio", subblock_id)
    os.makedirs(audio_dir, exist_ok=True)
    
    # Batch Transliteration for Roman Urdu to Nastaliq Urdu to optimize Gemini API calls
    transliterated_map = {}
    if lang == "ur" and not KEY_MANAGER.all_keys_exhausted:
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
            gemini_res = call_gemini_api(GEMINI_MODEL, prompt)
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
                    if not KEY_MANAGER.all_keys_exhausted:
                        nastaliq_text = transliterated_map.get(scene_no)
                        if not nastaliq_text:
                            print(f"        Warning: Scene {scene_no} missing from batch. Transliterating individually...")
                            nastaliq_text = transliterate_roman_to_nastaliq(cleaned_roman)
                    
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
    global KEY_MANAGER
    parser = argparse.ArgumentParser(description="Step 5: Animation-Audio Integration Pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of Rukus to process.")
    parser.add_argument("--ruku", type=int, default=None, help="Process a specific absolute Ruku index.")
    parser.add_argument("--force", action="store_true", help="Force reprocessing of already completed entries.")
    parser.add_argument("--lang", choices=["en", "ur", "both"], default="both", help="Process specific tracks.")
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    keys, env = load_env_keys(os.path.join(root_dir, ".env"))
    
    if not keys:
        print("Error: No Gemini API keys found in .env.", file=sys.stderr)
        sys.exit(1)
        
    KEY_MANAGER = GeminiKeyManager(keys)
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
            
            print(f"\n>>> Processing Ruku {abs_ruku} (Surah {surah_num:03d} {surah_name}, Relative Ruku {rel_ruku})")
            
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
            for subblock_entry in manifest:
                filename = subblock_entry["filename"]
                subblock_json_path = os.path.join(step4_dir, filename)
                
                if not os.path.exists(subblock_json_path):
                    print(f"  Warning: Subblock file not found at {subblock_json_path}. Skipping subblock.")
                    continue
                    
                subblock_success = await process_subblock(
                    subblock_json_path, output_dir, lang, polly_client
                )
                if not subblock_success:
                    success = False
                    
            if success:
                with open(os.path.join(output_dir, "subblocks_manifest.json"), 'w', encoding='utf-8') as f_out:
                    json.dump(manifest, f_out, ensure_ascii=False, indent=2)
                
                entry["completed"] = True
                with open(todo_path, 'w', encoding='utf-8') as f_todo:
                    json.dump(todo_list, f_todo, ensure_ascii=False, indent=2)
                processed_rukus += 1
                print(f"  Completed integration for Ruku {abs_ruku}.")
            else:
                print(f"  Failed integration for Ruku {abs_ruku}.")
                
    print("\nIntegration finished.")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
