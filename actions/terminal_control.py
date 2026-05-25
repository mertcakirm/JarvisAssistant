import subprocess
import os
import shutil
from pathlib import Path

_CURRENT_DIR = Path.home()

def get_current_dir() -> Path:
    global _CURRENT_DIR
    return _CURRENT_DIR

def terminal_control(parameters: dict, player=None, speak=None) -> str:
    global _CURRENT_DIR
    command = parameters.get("command")
    timeout = parameters.get("timeout", 60)
    
    if not command:
        return "No command provided."
        
    if player:
        player.write_log(f"[Terminal] {command[:60]}")
        
    # Handle cd commands to maintain state
    if command.strip() == "pwd":
        return f"Current directory is: {_CURRENT_DIR}"

    if command.strip().startswith("cd "):
        target = command.strip()[3:].strip()
        
        # Handle cd without arguments (go to home)
        if not target:
            target = "~"
            
        target_path = Path(target).expanduser()
        
        if target_path.is_absolute():
            new_dir = target_path.resolve()
        else:
            new_dir = (_CURRENT_DIR / target_path).resolve()
            
        if new_dir.is_dir():
            _CURRENT_DIR = new_dir
            return f"Changed directory to {_CURRENT_DIR}"
        else:
            return f"Directory not found: {new_dir}"
            
    import platform
    is_mac = platform.system() == "Darwin"
    
    # Try to execute in an interactive/login shell to load PATH and env variables
    if is_mac:
        # Use zsh as it's the default on modern macOS
        exec_cmd = ["zsh", "-lc", command]
    else:
        # Fallback to bash or generic shell on other platforms
        exec_cmd = ["bash", "-lc", command] if shutil.which("bash") else command

    try:
        if isinstance(exec_cmd, list):
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(_CURRENT_DIR)
            )
        else:
            result = subprocess.run(
                exec_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(_CURRENT_DIR)
            )
        
        output = result.stdout.strip()
        err = result.stderr.strip()
        
        res = f"Exit code: {result.returncode}\nCurrent Directory: {_CURRENT_DIR}\n"
        if output:
            res += f"STDOUT:\n{output[:4000]}\n"
        if err:
            res += f"STDERR:\n{err[:4000]}\n"
            
        if "Operation not permitted" in err:
            res += "\n[SYSTEM NOTE: 'Operation not permitted' usually means macOS is blocking access. Please grant 'Full Disk Access' or 'Files and Folders' permissions to the Terminal/IDE running JARVIS in macOS Settings > Privacy & Security.]"
            
        if not output and not err:
            res += "No output."
            
        return res
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"