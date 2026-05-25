import subprocess
print(subprocess.run(["osascript", "-e", 'tell application "Finder" to empty trash'], capture_output=True, text=True))
