import os
import json
import re
import sys
import time
from pathlib import Path
import subprocess

from actions.terminal_control import get_current_dir

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
MODEL_NAME      = "gemini-2.5-flash"

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]

def _get_model():
    import google.generativeai as genai
    genai.configure(api_key=_get_api_key())
    return genai.GenerativeModel(MODEL_NAME)

def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()

def gemini_cli(parameters: dict, player=None, speak=None, project_mode=False) -> str:
    """
    Autonomous coding agent that works on the current directory.
    Can implement features, fix bugs, and refactor code.
    """
    instruction = parameters.get("instruction", "")
    if not instruction:
        return "Please provide an instruction for the Gemini CLI agent."

    current_dir = get_current_dir()
    
    if player:
        player.write_log(f"[GeminiCLI] Starting task: {instruction[:50]}...")
        if project_mode:
            player.log_project_terminal(f"\n>>> GEMINI CLI TASK: {instruction}")
            player.add_project_task("gemini_cli_task", f"Gemini CLI: {instruction[:40]}")
            player.update_project_task("gemini_cli_task", "in_progress")

    if speak:
        speak("I am initiating the Gemini developer agent to handle your request, sir.")

    # 1. Scan the current directory
    files_list = []
    for root, dirs, files in os.walk(current_dir):
        # Ignore common directories
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules', 'venv', '.venv')]
        for file in files:
            rel_path = os.path.relpath(os.path.join(root, file), current_dir)
            files_list.append(rel_path)

    files_context = "\n".join(files_list[:100]) # Limit context

    # 2. Plan the changes
    model = _get_model()
    planner_prompt = f"""You are a senior software engineer operating through a CLI.
Your goal is to fulfill the following instruction in the current project:
"{instruction}"

Current directory: {current_dir}
Files in project:
{files_context}

Provide a plan of which files to create or modify. 
Return ONLY a JSON list of objects with "path" and "reason":
[
  {{"path": "filename.py", "reason": "Modify to add X feature"}}
]
"""
    plan = []
    max_planner_retries = 3
    for attempt in range(max_planner_retries):
        try:
            response = model.generate_content(planner_prompt)
            plan_raw = _strip_fences(response.text)
            plan = json.loads(plan_raw)
            break
        except Exception as e:
            if "429" in str(e) and attempt < max_planner_retries - 1:
                wait_time = 10 + (attempt * 10)
                if player:
                    player.log_project_terminal(f"! Planner rate limit. Waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            return f"Failed to plan changes: {e}"

    if player:
        player.log_project_terminal(f"Plan generated: {len(plan)} files to modify.")

    results = []
    
    # 3. Apply changes
    for item in plan:
        path = item["path"]
        reason = item["reason"]
        full_path = current_dir / path
        
        content = ""
        if full_path.exists():
            try:
                content = full_path.read_text(encoding="utf-8")
            except:
                content = "[Binary or unreadable file]"

        write_prompt = f"""You are an expert developer. Modify the file '{path}' for the following reason: {reason}
Task: {instruction}

Current content of '{path}':
---
{content}
---

Rules:
1. Return ONLY the complete NEW content for the file.
2. No markdown fences, no explanation.
3. Ensure the code is correct and follows project style.
"""
        if player:
            player.log_project_terminal(f"> Modifying {path}...")
        
        # Retry logic for 429 Rate Limit
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = model.generate_content(write_prompt)
                new_content = _strip_fences(resp.text)
                
                # Ensure directory exists
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(new_content, encoding="utf-8")
                results.append(f"Successfully updated {path}")
                if player:
                    player.log_project_terminal(f"✓ Updated {path}")
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    wait_time = 5 + (attempt * 5)
                    if player:
                        player.log_project_terminal(f"! Rate limit reached. Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
                
                results.append(f"Failed to update {path}: {e}")
                if player:
                    player.log_project_terminal(f"x Error updating {path}: {e}")
                break

    final_msg = "\n".join(results)
    if player and project_mode:
        player.update_project_task("gemini_cli_task", "completed")
        player.log_project_terminal(f"\nGEMINI CLI TASK COMPLETED.\n{final_msg}")
    
    if speak:
        speak("I have completed the requested changes using the Gemini CLI agent, sir.")

    return f"Gemini CLI completed the task:\n{final_msg}"