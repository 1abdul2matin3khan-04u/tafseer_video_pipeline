#!/usr/bin/env python3
"""
show_limits.py
CLI utility to display real-time API limits, daily usage, minute usage, fail counts, 
and suspension statuses across all active projects and models.
"""

import os
import json
import re
from datetime import datetime
import api_logger

def get_status_str(combo_key, suspended, requests_left, rpm_pct, fail_count, for_console=False):
    if combo_key in suspended or requests_left <= 0:
        return "Suspended (RPD Exhausted)" if for_console else "❌ Suspended (RPD Exhausted)"
    if fail_count >= 3:
        return "Suspended (Consecutive Fails)" if for_console else "❌ Suspended (Consecutive Fails)"
    if rpm_pct >= 1.0:
        return "RPM Blocked" if for_console else "⚠️ RPM Blocked"
    
    # Calculate tier based on utilization
    # limits mapping
    model_name = combo_key.split(":", 1)[1]
    limit_info = api_logger.MODEL_LIMITS.get(model_name, {"rpd": 20, "rpm": 5})
    rpd_pct = (limit_info["rpd"] - requests_left) / limit_info["rpd"]
    utilization = max(rpd_pct, rpm_pct)
    
    if utilization < 0.70:
        return "Active (Tier 1 - Safe)" if for_console else "✅ Active (Tier 1 - Safe)"
    elif utilization < 0.90:
        return "Active (Tier 2 - Warning)" if for_console else "✅ Active (Tier 2 - Warning)"
    else:
        return "Active (Tier 3 - Danger)" if for_console else "✅ Active (Tier 3 - Danger)"

def main():
    proj_keys = api_logger.load_project_keys()
    if not proj_keys:
        print("Error: No API keys configured in .env file.")
        return
        
    date_str = datetime.now().strftime("%Y-%m-%d")
    calls_today, calls_last_60s = api_logger.get_model_usage(date_str)
    
    # Load key rotation state
    state_file = api_logger.get_rotation_state_filepath()
    suspended = []
    fail_counts = {}
    
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
                if state.get("date") == date_str:
                    suspended = state.get("suspended", [])
                    fail_counts = state.get("fail_counts", {})
        except Exception:
            pass
            
    # Compile results for all active projects and models
    results = []
    all_models = api_logger.FLASH_MODELS + api_logger.LITE_MODELS
    
    for proj in sorted(proj_keys.keys()):
        # Only check projects with active keys
        if proj_keys[proj]:
            for model in all_models:
                combo_key = f"{proj}:{model}"
                limit_info = api_logger.MODEL_LIMITS[model]
                
                used_today = calls_today.get((proj, model), 0)
                used_last_60s = calls_last_60s.get((proj, model), 0)
                
                reqs_left = max(0, limit_info["rpd"] - used_today)
                rpm_pct = used_last_60s / limit_info["rpm"]
                fail_count = fail_counts.get(combo_key, 0)
                
                status_console = get_status_str(combo_key, suspended, reqs_left, rpm_pct, fail_count, for_console=True)
                status_markdown = get_status_str(combo_key, suspended, reqs_left, rpm_pct, fail_count, for_console=False)
                
                results.append({
                    "project": proj,
                    "model": model.replace("models/", ""),
                    "rpd_used": used_today,
                    "rpd_limit": limit_info["rpd"],
                    "rpd_left": reqs_left,
                    "rpm_used": used_last_60s,
                    "rpm_limit": limit_info["rpm"],
                    "fail_count": fail_count,
                    "status_console": status_console,
                    "status_markdown": status_markdown,
                    "combo_key": combo_key
                })
                
    # 1. Print formatted table to console
    header_fmt = "| {:<11} | {:<28} | {:<12} | {:<12} | {:<10} | {:<30} |"
    row_fmt    = "| {:<11} | {:<28} | {:<12} | {:<12} | {:<10} | {:<30} |"
    separator  = "+-" + "-"*11 + "-+-" + "-"*28 + "-+-" + "-"*12 + "-+-" + "-"*12 + "-+-" + "-"*10 + "-+-" + "-"*30 + "-+"
    
    print("\n" + "="*116)
    print(f"GOOGLE GEMINI API REAL-TIME QUOTA & STATUS REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*116)
    print(separator)
    print(header_fmt.format("Project ID", "Model Name", "Daily Used", "Daily Left", "Fail Count", "Current Status"))
    print(separator)
    
    for res in results:
        print(row_fmt.format(
            res["project"],
            res["model"],
            f"{res['rpd_used']}/{res['rpd_limit']}",
            str(res["rpd_left"]),
            f"{res['fail_count']}/3",
            res["status_console"]
        ))
    print(separator)
    print(f"Active project keys in rotation: { {p: len(keys) for p, keys in proj_keys.items()} }\n")
    
    # 2. Write Markdown status report to logs/current_limits.md
    root = os.path.dirname(os.path.abspath(__file__))
    md_path = os.path.join(root, "logs", "current_limits.md")
    
    md_lines = [
        f"# Gemini API Quota & Threshold Status Report\n",
        f"**Last Updated**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n",
        "## Real-time Project Limits\n",
        "| Project | Model | Daily Usage | Daily Left | RPM Usage | Fail Count | Status |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :--- |"
    ]
    
    for res in results:
        md_lines.append(
            f"| `{res['project']}` | `{res['model']}` | `{res['rpd_used']}/{res['rpd_limit']}` | `{res['rpd_left']}` | `{res['rpm_used']}/{res['rpm_limit']}` | `{res['fail_count']}/3` | {res['status_markdown']} |"
        )
        
    md_lines.append("\n---\n*Note: Quota limits reset at midnight Pacific Time. Failure counts reset immediately upon any successful call to that project-model.*")
    
    try:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(md_lines))
        print(f"Saved real-time Markdown quota report to: logs/current_limits.md")
    except Exception as e:
        print(f"Warning: Failed to save Markdown limit report: {e}")

if __name__ == "__main__":
    main()
