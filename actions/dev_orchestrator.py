import asyncio
import json
import time
import os
import tempfile
import subprocess
from typing import AsyncGenerator, Optional
from actions.gemini_cli import _get_model, _strip_fences
from actions.terminal_control import get_current_dir

def _run_in_macos_terminal(cmd: str, cwd: str, is_first: bool = False):
    """
    Executes a shell command visibly in the macOS Terminal app.
    Creates a temporary bash script to run the command, tee the output,
    and signal completion. Opens a new window for the first command.
    """
    out_file = tempfile.mktemp(suffix=".out")
    done_file = tempfile.mktemp(suffix=".done")
    script_file = tempfile.mktemp(suffix=".sh")
    
    script_content = f"""#!/bin/bash
cd "{cwd}"
echo "=== JARVIS GELİŞTİRME MODU ==="
echo "$ {cmd}"
(
{cmd}
) 2>&1 | tee "{out_file}"
echo ${{PIPESTATUS[0]}} > "{done_file}"
"""
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script_content)
    os.chmod(script_file, 0o755)
    
    if is_first:
        applescript = f'''
        tell application "Terminal"
            activate
            do script "{script_file}"
        end tell
        '''
    else:
        applescript = f'''
        tell application "Terminal"
            activate
            do script "{script_file}" in front window
        end tell
        '''
        
    subprocess.run(['osascript', '-e', applescript], capture_output=True)
    
    # Wait for the script to finish
    while not os.path.exists(done_file):
        time.sleep(0.5)
        
    with open(done_file, "r") as f:
        exit_code_str = f.read().strip()
    exit_code = int(exit_code_str) if exit_code_str.isdigit() else -1
    
    with open(out_file, "r", encoding="utf-8") as f:
        output = f.read()
        
    try:
        os.remove(script_file)
        os.remove(out_file)
        os.remove(done_file)
    except:
        pass
        
    return exit_code, output

class DevAgentManager:
    """
    Gemini Development Mode (Multi-Agent) Orchestrator.
    Acts as a Lead Developer: breaks down complex instructions into subtasks,
    and executes them, reporting progress back to the Project Manager.
    """
    def __init__(self):
        self.is_running = False

    async def start_session(self):
        self.is_running = True

    async def stop_session(self):
        self.is_running = False

    async def send_prompt(self, prompt: str, player=None, speak=None) -> AsyncGenerator[str, None]:
        """
        Plans the project steps and executes them one by one.
        """
        self.is_running = True
        yield f"[Gemini] Analyzing task: {prompt}"
        if speak and not (player and getattr(player, "muted", False)):
            speak("I am analyzing the task and preparing the implementation plan.")
        
        current_dir = get_current_dir()
        
        # 1. Lead Developer creates a plan
        model = _get_model("gemini-2.5-flash-lite")
        planner_prompt = f"""You are Gemini, acting as a Lead Developer. 
Break down the following project requirement into logical, actionable development steps.
You have two types of tasks:
1. 'shell': For running terminal commands (e.g., npm create, npm install, pip install, mkdir). Combine related commands with &&.
2. 'gemini': For generating or modifying source code in specific files.

Requirement: "{prompt}"
Current Directory: {current_dir}

Return ONLY a JSON list of objects with "step_name", "type" ("shell" or "gemini"), and "instruction" (for gemini) or "command" (for shell):
[
  {{"step_name": "Initialize React Project", "type": "shell", "command": "npx create-vite@latest my-app --template react"}},
  {{"step_name": "Install Tailwind", "type": "shell", "command": "cd my-app && npm install -D tailwindcss postcss autoprefixer && npx tailwindcss init -p"}},
  {{"step_name": "Configure Tailwind", "type": "gemini", "instruction": "Update my-app/tailwind.config.js to configure content paths."}}
]
"""
        yield "[Gemini] Generating implementation plan..."
        
        try:
            # Run synchronous genai call in executor to avoid blocking the asyncio loop
            loop = asyncio.get_event_loop()
            
            # Retry loop for rate limit
            import re
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    import google.generativeai as genai
                    generation_config = genai.types.GenerationConfig(
                        response_mime_type="application/json"
                    )
                    response = await loop.run_in_executor(
                        None, 
                        lambda: model.generate_content(planner_prompt, generation_config=generation_config)
                    )
                    plan_raw = _strip_fences(response.text)
                    plan = json.loads(plan_raw)
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < max_retries - 1:
                        msg = str(e)
                        match = re.search(r"retry in ([\d\.]+)s", msg)
                        wait_time = float(match.group(1)) + 1.0 if match else 15.0 + (attempt * 10)
                        yield f"[Gemini] Rate limit hit. Waiting {wait_time:.1f}s before retry..."
                        await asyncio.sleep(wait_time)
                    else:
                        raise e
                        
        except Exception as e:
            yield f"[!] Gemini failed to generate plan: {e}"
            self.is_running = False
            if speak and not (player and getattr(player, "muted", False)):
                speak("I encountered an error while planning the implementation.")
            return

        yield f"[Gemini] Plan ready: {len(plan)} steps."
        if speak and not (player and getattr(player, "muted", False)):
            speak(f"Plan is ready with {len(plan)} steps. Starting implementation.")
        
        if player:
            for i, step in enumerate(plan):
                player.add_project_task(f"dev_step_{i}", f"Step {i+1}: {step['step_name']}")

        # 2. Execute each step using gemini_cli or shell logic
        from actions.gemini_cli import gemini_cli
        
        first_shell_step = True
        for i, step in enumerate(plan):
            if not self.is_running:
                yield "[Gemini] Session stopped."
                break
                
            step_id = f"dev_step_{i}"
            step_type = step.get("type", "gemini")
            step_name = step.get("step_name", f"Step {i+1}")
            
            yield f"\n[Gemini] Executing Step {i+1}/{len(plan)}: {step_name}"
            
            if speak and not (player and getattr(player, "muted", False)):
                speak(f"Working on {step_name}.")

            if player:
                player.update_project_task(step_id, "in_progress")
                
            try:
                if step_type == "shell":
                    cmd = step.get("command", "")
                    yield f"[Gemini] Running command visibly in MacOS Terminal: {cmd}"
                    
                    # Run the terminal command visibly
                    exit_code, out = await loop.run_in_executor(
                        None, 
                        lambda: _run_in_macos_terminal(cmd, current_dir, is_first=first_shell_step)
                    )
                    first_shell_step = False
                    
                    res_text = f"Exit code: {exit_code}\nOutput:\n{out[:500]}"
                    yield f"[Gemini] Step {i+1} completed.\n{res_text}"
                else:
                    step_instruction = step.get("instruction", "")
                    # parameters for gemini_cli
                    params = {"instruction": step_instruction}
                    
                    # Run the synchronous gemini_cli in the background
                    result = await loop.run_in_executor(
                        None, 
                        lambda: gemini_cli(parameters=params, player=player, speak=speak, project_mode=True)
                    )
                    
                    yield f"[Gemini] Step {i+1} completed.\nResult: {result[:500]}"
                
                if player:
                    player.update_project_task(step_id, "completed")
                    
            except Exception as e:
                yield f"[!] Error in Step {i+1}: {e}"
                if player:
                    player.update_project_task(step_id, "failed")
                if speak and not (player and getattr(player, "muted", False)):
                    speak(f"Encountered an error during {step_name}.")
                
            # Optional small delay
            await asyncio.sleep(1)

        yield "\n[Gemini] All tasks completed."
        self.is_running = False

dev_manager = DevAgentManager()