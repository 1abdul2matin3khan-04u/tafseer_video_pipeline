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

class FileLock:
    """Cross-platform file lock using atomic file creation."""
    def __init__(self, filepath, timeout=10, delay=0.1):
        self.lockfile = filepath + '.lock'
        self.timeout = timeout
        self.delay = delay
        self.fd = None

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self.fd = os.open(self.lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() - start > self.timeout:
                    # Stale lock — force remove and retry once
                    try:
                        os.remove(self.lockfile)
                    except OSError:
                        pass
                    raise TimeoutError(f"Could not acquire lock on {self.lockfile}")
                time.sleep(self.delay)

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.lockfile)
        except OSError:
            pass

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

# Models, their daily limits (RPD), and minute limits (RPM)
MODEL_LIMITS = {
    "models/gemini-3.5-flash": {"rpd": 20, "rpm": 5},
    "models/gemini-3.0-flash": {"rpd": 20, "rpm": 5},
    "models/gemini-2.5-flash": {"rpd": 20, "rpm": 5},
    "models/gemini-3.1-flash-lite": {"rpd": 500, "rpm": 15},
    "models/gemini-2.5-flash-lite": {"rpd": 20, "rpm": 10}
}

# The sequence of models in each tier
FLASH_MODELS = [
    "models/gemini-3.5-flash",
    "models/gemini-3.0-flash",
    "models/gemini-2.5-flash"
]

LITE_MODELS = [
    "models/gemini-3.1-flash-lite",
    "models/gemini-2.5-flash-lite"
]

STEP_MODEL_CLASS = {
    "step1": "lite",
    "step2": "flash",
    "step3": "lite",
    "step4_scenes": "lite",
    "step4_visuals": "flash",
    "step5": "lite"
}

def get_rotation_state_filepath():
    root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, "key_rotation_state.json")

def load_project_keys():
    """Loads active API keys from .env grouped by project ID."""
    keys = load_env_keys()
    project_keys = {}
    for key_name, key_val in keys:
        match = re.match(r'^PROJECT_(\d+)_', key_name)
        proj = f"PROJECT_{match.group(1)}" if match else "PROJECT_1"
        if proj not in project_keys:
            project_keys[proj] = []
        project_keys[proj].append((key_name, key_val))
    return project_keys

def get_model_usage(date_str=None):
    """
    Scans today's JSON log and returns:
    1. calls_today: dict mapping (project, model) -> int (excluding 429 status entries)
    2. calls_last_60s: dict mapping (project, model) -> int (excluding 429 status entries)
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        
    root = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(root, "logs", f"{date_str}_api_usage.json")
    
    calls_today = {}
    calls_last_60s = {}
    
    if not os.path.exists(json_path):
        return calls_today, calls_last_60s
        
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return calls_today, calls_last_60s
        
    now = datetime.now()
    rukus = data.get("rukus", {})
    
    for r_key, r_data in rukus.items():
        calls = r_data.get("calls", [])
        for call in calls:
            status = call.get("status", "")
            # Skip HTTP Error 429 (rejected, didn't consume quota)
            if "429" in status:
                continue
                
            model = call.get("model")
            key_name = call.get("key_name", "")
            
            # Extract project name from key name
            match = re.match(r'^PROJECT_(\d+)_', key_name)
            project = f"PROJECT_{match.group(1)}" if match else "PROJECT_1"
            
            combo = (project, model)
            
            # Count daily calls
            calls_today[combo] = calls_today.get(combo, 0) + 1
            
            # Count calls in the last 60 seconds
            timestamp_str = call.get("timestamp")
            if timestamp_str:
                try:
                    entry_time = datetime.strptime(f"{date_str} {timestamp_str}", "%Y-%m-%d %H:%M:%S")
                    if (now - entry_time).total_seconds() < 60:
                        calls_last_60s[combo] = calls_last_60s.get(combo, 0) + 1
                except Exception:
                    pass
                    
    return calls_today, calls_last_60s

def get_next_api_key_and_model(step_name):
    """
    Dynamically routes, rotates, suspends, and cascades API requests across projects and models.
    Supports real-time RPM checks and fails over to Lite classes.
    """
    proj_keys = load_project_keys()
    if not proj_keys:
        return None, None, None
        
    state_file = get_rotation_state_filepath()
    model_class = STEP_MODEL_CLASS.get(step_name, "lite")
    
    # 30 retries (60 seconds total sleep duration) if RPM-blocked
    for attempt in range(30):
        # Read usage stats and lock rotation state
        calls_today, calls_last_60s = get_model_usage()
        
        with FileLock(state_file):
            state = {}
            if os.path.exists(state_file):
                try:
                    with open(state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                except Exception:
                    state = {}
                    
            # Check date and reset quotas/fail counts daily
            today_str = datetime.now().strftime("%Y-%m-%d")
            if state.get("date") != today_str:
                state["date"] = today_str
                state["suspended"] = []
                state["fail_counts"] = {}
                
            suspended = state.setdefault("suspended", [])
            fail_counts = state.setdefault("fail_counts", {})
            rotation_indexes = state.setdefault("rotation_indexes", {})
            
            # Helper to calculate model parameters and assign Tiers
            def get_candidates_and_tiers(selected_class):
                if selected_class == "flash":
                    model_list = FLASH_MODELS
                else:
                    model_list = LITE_MODELS
                    
                cand_list = []
                for proj in sorted(proj_keys.keys()):
                    for m in model_list:
                        # Verify we have at least one key for this project
                        if proj in proj_keys and proj_keys[proj]:
                            combo_key = f"{proj}:{m}"
                            if combo_key not in suspended:
                                cand_list.append((proj, m))
                                
                t1, t2, t3 = [], [], []
                for proj, model in cand_list:
                    used = calls_today.get((proj, model), 0)
                    limit_info = MODEL_LIMITS.get(model, {"rpd": 20, "rpm": 5})
                    
                    # Check RPD limit
                    requests_left = limit_info["rpd"] - used
                    combo_key = f"{proj}:{model}"
                    
                    if requests_left <= 0:
                        # Suspend permanently for the day if fail_count >= 3
                        f_count = fail_counts.get(combo_key, 0)
                        if f_count >= 3:
                            if combo_key not in suspended:
                                suspended.append(combo_key)
                            continue
                            
                    rpm_used = calls_last_60s.get((proj, model), 0)
                    rpd_pct = used / limit_info["rpd"]
                    rpm_pct = rpm_used / limit_info["rpm"]
                    utilization = max(rpd_pct, rpm_pct)
                    
                    candidate_info = {
                        "proj": proj,
                        "model": model,
                        "utilization": utilization,
                        "rpm_pct": rpm_pct
                    }
                    
                    if utilization < 0.70:
                        t1.append(candidate_info)
                    elif utilization < 0.90:
                        t2.append(candidate_info)
                    elif fail_counts.get(combo_key, 0) < 3: # Allowed in Tier 3 if fail_count < 3
                        t3.append(candidate_info)
                return t1, t2, t3
                
            # Class checking
            chosen_candidate = None
            if model_class == "flash":
                tier1, tier2, tier3 = get_candidates_and_tiers("flash")
                active_tier = []
                if tier1:
                    active_tier = tier1
                elif tier2:
                    active_tier = tier2
                elif tier3:
                    active_tier = tier3
                    
                if active_tier:
                    active_tier.sort(key=lambda x: (x["proj"], x["model"]))
                    idx = rotation_indexes.get("flash", 0)
                    chosen_candidate = active_tier[idx % len(active_tier)]
                    rotation_indexes["flash"] = (idx + 1) % 1000
                else:
                    # Fallback to Lite model class
                    model_class = "lite"
                    
            if model_class == "lite":
                tier1_lite, tier2_lite, tier3_lite = get_candidates_and_tiers("lite")
                active_tier_lite = []
                if tier1_lite:
                    active_tier_lite = tier1_lite
                elif tier2_lite:
                    active_tier_lite = tier2_lite
                elif tier3_lite:
                    active_tier_lite = tier3_lite
                    
                if active_tier_lite:
                    active_tier_lite.sort(key=lambda x: (x["proj"], x["model"]))
                    idx = rotation_indexes.get("lite", 0)
                    chosen_candidate = active_tier_lite[idx % len(active_tier_lite)]
                    rotation_indexes["lite"] = (idx + 1) % 1000
                    
            # RPM Block check for the chosen candidate
            if chosen_candidate:
                proj = chosen_candidate["proj"]
                model = chosen_candidate["model"]
                rpm_pct = chosen_candidate["rpm_pct"]
                
                if rpm_pct >= 1.0:
                    # RPM limit hit for chosen candidate: check if any alternative exists in any active tier
                    alternative = None
                    if model_class == "lite":
                        search_tiers = [tier1_lite, tier2_lite, tier3_lite]
                    else:
                        search_tiers = [tier1, tier2, tier3]
                        
                    for t in search_tiers:
                        for cand in t:
                            if cand["rpm_pct"] < 1.0:
                                alternative = cand
                                break
                        if alternative:
                            break
                            
                    if alternative:
                        chosen_candidate = alternative
                        proj = chosen_candidate["proj"]
                        model = chosen_candidate["model"]
                    else:
                        # Sleep 2 seconds and retry next attempt
                        print(f"  [RPM Block Alert] All active models for class '{model_class}' are currently RPM-blocked. Retrying in 2 seconds...")
                        time.sleep(2)
                        continue
                        
                # Retrieve rotated key for this project
                p_keys = proj_keys[proj]
                k_idx = rotation_indexes.get(f"key_{proj}", 0)
                key_name, key_val = p_keys[k_idx % len(p_keys)]
                rotation_indexes[f"key_{proj}"] = (k_idx + 1) % len(p_keys)
                
                # Save state changes
                try:
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Warning: Failed to save rotation state: {e}")
                    
                return model, key_name, key_val
            else:
                print(f"  [Fatal Quota Alert] All models for class '{model_class}' are fully suspended/exhausted.")
                return None, None, None
                
    print("  [Timeout Error] Bypassed request: RPM limits stayed blocked for 60 seconds.")
    return None, None, None

def get_next_api_key(step_name):
    """Fallback compatibility wrapper for get_next_api_key_and_model."""
    res = get_next_api_key_and_model(step_name)
    if res:
        return res[1], res[2]
    return None, None

def log_api_call(step_name, abs_ruku, surah_number, surah_name, rel_ruku, model, key_name, status, input_tokens=None, output_tokens=None):
    """
    Logs API request into daily JSON, updates project/model fail counts, and auto-renders markdown report.
    """
    # Dynamic fail counts and suspension checks
    state_file = get_rotation_state_filepath()
    with FileLock(state_file):
        try:
            state = {}
            if os.path.exists(state_file):
                with open(state_file, 'r', encoding='utf-8') as f_state:
                    state = json.load(f_state)
                    
            today_str = datetime.now().strftime("%Y-%m-%d")
            if state.get("date") != today_str:
                state["date"] = today_str
                state["suspended"] = []
                state["fail_counts"] = {}
                
            fail_counts = state.setdefault("fail_counts", {})
            suspended = state.setdefault("suspended", [])
            
            # Parse project name
            match = re.match(r'^PROJECT_(\d+)_', key_name)
            project = f"PROJECT_{match.group(1)}" if match else "PROJECT_1"
            combo_key = f"{project}:{model}"
            
            if status == "Success":
                fail_counts[combo_key] = 0
            else:
                fail_counts[combo_key] = fail_counts.get(combo_key, 0) + 1
                if fail_counts[combo_key] >= 3:
                    if combo_key not in suspended:
                        suspended.append(combo_key)
                        print(f"  [API Logger] Model {model} in project {project} has been suspended for the day due to consecutive failures.")
                        
            with open(state_file, 'w', encoding='utf-8') as f_state:
                json.dump(state, f_state, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Warning: Failed to update key rotation state in log_api_call: {e}")
            
    date_str = datetime.now().strftime("%Y-%m-%d")
    root = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    json_path = os.path.join(logs_dir, f"{date_str}_api_usage.json")
    
    with FileLock(json_path):
        # Read existing data
        data = None
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
        except Exception:
            pass
                
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
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
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
