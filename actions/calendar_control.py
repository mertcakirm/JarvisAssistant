import subprocess
import datetime
from typing import Dict, Any

def run_applescript(script: str) -> str:
    """Executes an AppleScript and returns its standard output."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(f"AppleScript execution failed: {error_msg}")

def _parse_date(date_str: str) -> datetime.datetime:
    """Parses standard ISO or common formats into datetime."""
    # Try different formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unable to parse date string: {date_str}")

def calendar_control(parameters: Dict[str, Any], player=None, speak=None) -> str:
    action = parameters.get("action")
    
    if action == "list":
        date_filter = parameters.get("date_filter", "today").lower()
        title_filter = parameters.get("title_filter", "").lower()
        
        # Determine the date range based on the string
        now = datetime.datetime.now()
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + datetime.timedelta(days=1)
        
        if date_filter == "tomorrow":
            start_date += datetime.timedelta(days=1)
            end_date += datetime.timedelta(days=1)
        elif date_filter not in ("today", "all"):
            try:
                # Try to parse as specific date
                start_date = _parse_date(date_filter).replace(hour=0, minute=0, second=0)
                end_date = start_date + datetime.timedelta(days=1)
            except ValueError:
                pass # Default to today if parsing fails and it's not "all"

        # AppleScript to fetch events
        # We construct the Applescript to filter by date (if not 'all')
        date_cond = ""
        if date_filter != "all":
            date_cond = f"""
            set sDate to (current date)
            set year of sDate to {start_date.year}
            set month of sDate to {start_date.month}
            set day of sDate to {start_date.day}
            set time of sDate to 0
            
            set eDate to (current date)
            set year of eDate to {end_date.year}
            set month of eDate to {end_date.month}
            set day of eDate to {end_date.day}
            set time of eDate to 0
            
            """
            filter_str = "whose start date is greater than or equal to sDate and start date is less than eDate"
        else:
            filter_str = ""

        script = f"""
        {date_cond}
        set output to ""
        tell application "Calendar"
            set cals to every calendar
            repeat with c in cals
                set evts to (every event of c {filter_str})
                repeat with e in evts
                    set evTitle to summary of e
                    set evStart to start date of e
                    set evEnd to end date of e
                    set output to output & "- " & evTitle & " (Starts: " & evStart & ", Ends: " & evEnd & ")\n"
                end repeat
            end repeat
        end tell
        return output
        """
        output = run_applescript(script)
        if not output:
            return f"No events found for '{date_filter}'."
            
        # Optional: post-filter by title in Python
        if title_filter:
            filtered_lines = [line for line in output.split("\n") if title_filter in line.lower()]
            if not filtered_lines:
                return f"No events found matching '{title_filter}' for '{date_filter}'."
            return "\n".join(filtered_lines)
            
        return output

    elif action == "add":
        title = parameters.get("title")
        start_str = parameters.get("start_time")
        end_str = parameters.get("end_time")
        calendar_name = parameters.get("calendar_name", "")

        if not all([title, start_str, end_str]):
            return "Missing required parameters: title, start_time, end_time"

        start_dt = _parse_date(start_str)
        end_dt = _parse_date(end_str)

        # AppleScript to create the event
        script = f"""
        set sDate to (current date)
        set year of sDate to {start_dt.year}
        set month of sDate to {start_dt.month}
        set day of sDate to {start_dt.day}
        set time of sDate to {start_dt.hour * 3600 + start_dt.minute * 60 + start_dt.second}

        set eDate to (current date)
        set year of eDate to {end_dt.year}
        set month of eDate to {end_dt.month}
        set day of eDate to {end_dt.day}
        set time of eDate to {end_dt.hour * 3600 + end_dt.minute * 60 + end_dt.second}

        tell application "Calendar"
            if "{calendar_name}" is not "" then
                try
                    set myCal to first calendar whose name is "{calendar_name}"
                on error
                    set myCal to calendar 1
                end try
            else
                set myCal to calendar 1
            end if
            
            tell myCal
                make new event at end with properties {{summary:"{title}", start date:sDate, end date:eDate}}
            end tell
        end tell
        return "Event '{title}' created successfully."
        """
        return run_applescript(script)

    elif action == "delete":
        title = parameters.get("title")
        if not title:
            return "Missing required parameter: title"

        script = f"""
        set deleteCount to 0
        tell application "Calendar"
            set cals to every calendar
            repeat with c in cals
                set evts to (every event of c whose summary is "{title}")
                repeat with e in evts
                    delete e
                    set deleteCount to deleteCount + 1
                end repeat
            end repeat
        end tell
        return "Deleted " & (deleteCount as string) & " event(s) named '{title}'."
        """
        return run_applescript(script)

    else:
        return f"Unknown calendar action: {action}"