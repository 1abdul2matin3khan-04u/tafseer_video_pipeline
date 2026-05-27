#!/usr/bin/env python3
"""
api_logger.py
Utility module for central API quota logging and stateful key rotation.
"""

import os
import json
import re
from datetime import datetime
import time

def load_env_keys():
    """
    Loads GEMINI_API_KEY_1 to GEMINI_API_KEY_7 from the root .env file.
    Returns:
        List of tuples: [("GEMINI_API_KEY_1", "value"), ...]
    """
    root = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(root, ".env")
    keys = []
    if not os.path.exists(filepath):
        return keys
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
                    if re.match(r'^(PROJECT_\d+_)?GEMINI_API_KEY(_\d+)?$', key) and val:
                        keys.append((key, val))
        # Ensure they are sorted by name
        keys.sort(key=lambda x: x[0])
    except Exception as e:
        print(f"Warning: Failed to load API keys from .env: {e}")
    return keys

def get_rotation_state_filepath():
    root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, "key_rotation_state.json")

def get_next_api_key(step_name):
    """
    Statefully rotates and retrieves the next key index for the given step.
    Saves state to logs/key_rotation_state.json.
    Returns:
        Tuple: (key_name, key_value) or (None, None) if no keys loaded.
    """
    keys = load_env_keys()
    if not keys:
        return None, None
        
    state_file = get_rotation_state_filepath()
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            state = {}
            
    # Get current index, defaulting to 0
    idx = state.get(step_name, 0)
    
    # In case keys count changed or index went out of range
    if idx >= len(keys):
        idx = 0
        
    key_name, key_value = keys[idx]
    
    # Save the NEXT index statefully
    state[step_name] = (idx + 1) % len(keys)
    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save key rotation state: {e}")
        
    return key_name, key_value

def log_api_call(step_name, abs_ruku, surah_number, surah_name, rel_ruku, model, key_name, status, input_tokens=None, output_tokens=None):
    """
    Logs API request into daily JSON and auto-renders the corresponding Markdown report.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    json_path = os.path.join(logs_dir, f"{date_str}_api_usage.json")
    
    # Thread/process safe write lock or quick file lock logic (via basic retries)
    data = None
    max_retries = 5
    for attempt in range(max_retries):
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            break
        except Exception:
            time.sleep(0.1)
            
    if not data:
        data = {
            "date": date_str,
            "summary": {
                "total_calls": 0,
                "success_calls": 0,
                "failed_calls": 0,
                "models": {},
                "keys": {}
            },
            "rukus": {}
        }
        
    # Build the entry
    entry = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "step": step_name,
        "model": model,
        "key_name": key_name,
        "status": status,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens
        }
    }
    
    # Place entry under the Ruku key
    ruku_key = f"ruku_{abs_ruku}"
    if ruku_key not in data["rukus"]:
        data["rukus"][ruku_key] = {
            "metadata": {
                "surah_number": surah_number,
                "surah_name": surah_name,
                "relative_ruku": rel_ruku
            },
            "calls": []
        }
    data["rukus"][ruku_key]["calls"].append(entry)
    
    # Update Summary
    summary = data["summary"]
    summary["total_calls"] += 1
    if status == "Success":
        summary["success_calls"] += 1
    else:
        summary["failed_calls"] += 1
        
    summary["models"][model] = summary["models"].get(model, 0) + 1
    summary["keys"][key_name] = summary["keys"].get(key_name, 0) + 1
    
    # Save the updated JSON
    for attempt in range(max_retries):
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            break
        except Exception:
            time.sleep(0.1)
            
    # Auto-render Markdown report
    try:
        render_markdown_report(date_str)
    except Exception as e:
        print(f"Warning: Failed to render markdown log report: {e}")

def render_markdown_report(date_str):
    """
    Renders YYYY-MM-DD_api_usage.md based on YYYY-MM-DD_api_usage.json.
    """
    root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(root, "logs")
    json_path = os.path.join(logs_dir, f"{date_str}_api_usage.json")
    md_path = os.path.join(logs_dir, f"{date_str}_api_usage.md")
    
    if not os.path.exists(json_path):
        return
        
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    summary = data["summary"]
    total = summary["total_calls"]
    success = summary["success_calls"]
    failed = summary["failed_calls"]
    rate = (success / total * 100) if total > 0 else 0
    
    lines = []
    lines.append(f"# Google Gemini API Usage & Quota Report - {date_str}\n")
    
    # Summary Section
    lines.append("## Daily Summary")
    lines.append(f"* **Total API Requests**: {total}")
    lines.append(f"* **Successful Requests**: {success}")
    lines.append(f"* **Failed Requests**: {failed}")
    lines.append(f"* **Success Rate**: {rate:.1f}%\n")
    
    # Models Used Table
    lines.append("### Model Utilization")
    lines.append("| Model Name | Total Calls |")
    lines.append("| :--- | :---: |")
    for model, count in sorted(summary["models"].items()):
        lines.append(f"| `{model}` | {count} |")
    lines.append("")
    
    # Keys Used Table
    lines.append("### API Key Utilization")
    lines.append("| API Key | Total Calls |")
    lines.append("| :--- | :---: |")
    for key, count in sorted(summary["keys"].items()):
        lines.append(f"| `{key}` | {count} |")
    lines.append("\n---\n")
    
    # Detailed log grouped by Ruku
    lines.append("## Detailed Logs (Grouped by Ruku)")
    
    # Sort Ruku keys numerically based on absolute Ruku index
    def get_ruku_sort_key(item):
        # item is (ruku_key, ruku_data)
        # ruku_key is "ruku_X"
        match = re.search(r'\d+', item[0])
        return int(match.group(0)) if match else 9999
        
    sorted_rukus = sorted(data["rukus"].items(), key=get_ruku_sort_key)
    
    for ruku_key, ruku_data in sorted_rukus:
        meta = ruku_data["metadata"]
        lines.append(f"### Surah {meta['surah_number']:03d} {meta['surah_name']} (Relative Ruku {meta['relative_ruku']}, Absolute Ruku {ruku_key.replace('ruku_', '')})")
        lines.append("")
        
        lines.append("| Timestamp | Step | Model | API Key | Status | Tokens (In / Out) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :---: |")
        
        for call in ruku_data["calls"]:
            tokens = call.get("tokens", {})
            in_t = tokens.get("input")
            out_t = tokens.get("output")
            tokens_str = f"{in_t} / {out_t}" if (in_t is not None and out_t is not None) else "N/A"
            
            # Format status with a color badge (useful in markdown renderers)
            status_str = call['status']
            if status_str == "Success":
                status_formatted = "✅ Success"
            elif "429" in status_str:
                status_formatted = "⚠️ 429 Rate Limit"
            else:
                status_formatted = f"❌ {status_str}"
                
            lines.append(f"| {call['timestamp']} | {call['step']} | `{call['model']}` | `{call['key_name']}` | {status_formatted} | {tokens_str} |")
        lines.append("\n")
        
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
