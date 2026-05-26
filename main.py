import asyncio
import re
import threading
import json
import sys
import traceback
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.calendar_control  import calendar_control
from actions.terminal_control  import terminal_control
from actions.gemini_cli        import gemini_cli
from actions.dev_orchestrator  import dev_manager


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "ALWAYS match the user's language: if they speak English, you MUST respond in English. "
            "You have FULL CONTROL over computer settings like volume, brightness, and applications via your tools. "
            "NEVER say you are unable to perform a task if there is a tool available for it. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

def _requires_development_mode(instruction: str) -> bool:
    low = (instruction or "").lower()
    setup_terms = (
        "create", "setup", "set up", "install", "configure", "build",
        "oluştur", "kur", "yapılandır", "proje",
    )
    project_terms = (
        "vite", "react project", "react vite", "npm", "npx",
        "tailwind", "react router", "helmet", "package.json", "node_modules",
    )
    return any(term in low for term in setup_terms) and any(term in low for term in project_terms)

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, play/stop/resume/continue media/music, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage, empty_trash.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info | empty_trash"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "assistant_control",
        "description": "USE THIS ONLY when the user EXPLICITLY asks you to 'shut up', 'sleep', 'rest', 'sus', 'uyu', or 'sessiz ol'. 'sleep' puts JARVIS in a silent standby mode. DO NOT use this when simply performing background tasks. 'wake' brings him back when user says 'wake up' or 'sesli devam et'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "sleep | wake"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "calendar_control",
        "description": "Manages macOS Apple Calendar: list, add, or delete events.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | add | delete"},
                "date_filter": {"type": "STRING", "description": "For 'list' action: 'today', 'tomorrow', 'all', or 'YYYY-MM-DD'"},
                "title_filter": {"type": "STRING", "description": "Optional title filter for 'list' action"},
                "title": {"type": "STRING", "description": "Event title for 'add' or 'delete' action"},
                "start_time": {"type": "STRING", "description": "Start time for 'add' action (e.g., '2026-05-23 10:00:00')"},
                "end_time": {"type": "STRING", "description": "End time for 'add' action (e.g., '2026-05-23 11:00:00')"},
                "calendar_name": {"type": "STRING", "description": "Optional calendar name for 'add' action"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "project_control",
        "description": "Manages the assistant's Project Mode. Use this before Gemini-led development, when the user says 'start project mode', 'proje moduna geç', 'gemini ile çalış', or 'let's build a project'. Project mode makes JARVIS the project manager, keeps the Project Monitor open, and tracks all development steps as tasks. Use 'clear' to reset the monitor for a new project.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | clear | show"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "task_manager",
        "description": "Manages running background tasks. Use this if the user wants to know what tasks are running, cancel a task, or modify an ongoing task (by cancelling it and starting a new one).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | cancel | modify"},
                "task_id": {"type": "STRING", "description": "Task ID for cancel or modify actions"},
                "new_goal": {"type": "STRING", "description": "The updated instructions/goal for the task if action is 'modify'"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "terminal_control",
        "description": "Executes raw shell/terminal commands directly on the user's system. Use it to build apps, run scripts, manage git, check logs, etc. To change directory permanently, run 'cd <path>'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "The shell command to execute"},
                "timeout": {"type": "INTEGER", "description": "Timeout in seconds (default: 60)"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "gemini_cli",
        "description": "Delegates a bounded source-code edit to the Gemini developer agent in the current directory. Do NOT use this for creating Vite/React projects, npm/npx installs, Tailwind setup, package.json generation, or multi-step project bootstrapping; use development_mode for those. JARVIS remains the project manager and must track Gemini's status.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instruction": {"type": "STRING", "description": "The coding task or instruction for Gemini to execute."}
            },
            "required": ["instruction"]
        }
    },
    {
        "name": "development_mode",
        "description": "Enters Development Mode where JARVIS is the project manager and Gemini CLI is the developer agent. Use this for Vite/React project creation, npm/npx installs, Tailwind setup, package.json work, Gemini-led coding sessions, complex fixes, or large project work. JARVIS tracks scope, Gemini updates, errors, next steps, and completion.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "instruction": {"type": "STRING", "description": "The project idea or initial coding instruction."}
            },
            "required": ["instruction"]
        }
    },
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self.sleep_mode     = False
        self.project_mode   = False
        self._speaking_lock = threading.Lock()
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None
        self._speech_queue: asyncio.Queue | None = None

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        if not hasattr(self, '_speech_queue') or self._speech_queue is None:
            return

        asyncio.run_coroutine_threadsafe(
            self._speech_queue.put(text),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    async def _start_development_mode(self, instruction: str, redirected: bool = False) -> str:
        if not instruction:
            return "No instruction provided for Development Mode."

        if dev_manager.is_running:
            msg = "Development mode is already running. I will not start a second project task until the current one finishes or is stopped."
            self.ui.show_project_monitor()
            self.ui.log_project_terminal(f"\n[JARVIS PM] {msg}")
            if not self.ui.muted:
                self.speak("Zaten çalışan bir geliştirme görevi var. İkinci bir görev başlatmıyorum.")
            return msg

        self.project_mode = True
        self.ui.show_project_monitor()
        self.ui.log_project_terminal(f"\n=== GELİŞTİRME MODU AKTİF ===")
        if redirected:
            self.ui.log_project_terminal("Not: Proje kurulum isteği gemini_cli yerine development_mode orkestratörüne yönlendirildi.")
        self.ui.log_project_terminal(f"Hedef: {instruction}")

        if not self.ui.muted:
            self.speak("Anladım, görevi Gemini'ye aktarıyorum.")

        await dev_manager.start_session()

        async def run_dev_task():
            self.ui.add_project_task("dev_orchestrator", "Gemini Geliştirici")
            self.ui.update_project_task("dev_orchestrator", "in_progress")

            full_response = []
            async for chunk in dev_manager.send_prompt(instruction, player=self.ui, speak=self.speak):
                self.ui.log_project_terminal(chunk)
                full_response.append(chunk)

            if dev_manager.failed_steps:
                self.ui.update_project_task("dev_orchestrator", "failed")
                self.ui.log_project_terminal("\n=== GELİŞTİRME GÖREVİ KONTROL GEREKTİRİYOR ===")
                if not self.ui.muted:
                    self.speak("Gemini görevleri tamamladı ancak kontrol gerektiren adımlar var. Detaylar Proje Monitöründe.")
            else:
                self.ui.update_project_task("dev_orchestrator", "completed")
                self.ui.log_project_terminal("\n=== GELİŞTİRME GÖREVİ TAMAMLANDI ===")
                if not self.ui.muted:
                    self.speak("Proje tamamlandı. Gemini görevlerini bitirdi. Detaylar için Proje Monitörüne bakabilirsiniz.")

        asyncio.create_task(run_dev_task())
        return "Development mode activated. Orchestrator is working in the background."

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        if self.sleep_mode:
            parts.append(
                "\n[CRITICAL: SLEEP MODE ACTIVE]\n"
                "You are currently in SLEEP MODE. Stay completely silent. "
                "Do NOT process any user requests, do NOT speak, and do NOT call any tools "
                "EXCEPT if the user says 'Wake up', 'Uyan', 'Jarvis wake up', or similar wake commands. "
                "When you hear a wake command, you MUST call 'assistant_control' with action='wake' "
                "to return to active mode, then greet the user normally."
            )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        # Auto-log to project monitor if in project mode
        task_id = None
        if self.project_mode and name not in ("assistant_control", "project_control", "save_memory", "task_manager"):
            import time
            task_id = f"tool_{name}_{int(time.time() * 1000) % 10000}"
            desc = f"{name}: {str(args.get('command') or args.get('goal') or args.get('action') or args.get('description') or args)[:60]}"
            self.ui.add_project_task(task_id, desc)
            self.ui.update_project_task(task_id, "in_progress")
            self.ui.log_project_terminal(f"\n[TOOL CALL] {name}\nArgs: {args}")

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak, player=self.ui)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "calendar_control":
                r = await loop.run_in_executor(None, lambda: calendar_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "task_manager":
                from agent.task_queue import get_queue, TaskPriority
                queue = get_queue()
                action = args.get("action", "")
                if action == "list":
                    statuses = queue.get_all_statuses()
                    active_tasks = [t for t in statuses if t["status"] in ("pending", "running")]
                    if not active_tasks:
                        result = "No active tasks are running."
                    else:
                        result = "Active Tasks:\n" + "\n".join([f"- [{t['task_id']}] ({t['status']}): {t['goal']}" for t in active_tasks])
                elif action == "cancel":
                    task_id = args.get("task_id")
                    if not task_id:
                        result = "Task ID is required to cancel."
                    else:
                        success = queue.cancel(task_id)
                        result = f"Task {task_id} cancelled." if success else f"Could not cancel task {task_id}. It may not exist or is already done."
                elif action == "modify":
                    task_id = args.get("task_id")
                    new_goal = args.get("new_goal")
                    if not task_id or not new_goal:
                        result = "Both Task ID and new_goal are required to modify a task."
                    else:
                        success = queue.cancel(task_id)
                        if success:
                            new_id = queue.submit(goal=new_goal, priority=TaskPriority.HIGH, speak=self.speak, player=self.ui)
                            result = f"Task {task_id} cancelled. New task {new_id} started with updated goal: {new_goal}"
                        else:
                            result = f"Could not cancel task {task_id}. Proceeding to create new task anyway."
                            new_id = queue.submit(goal=new_goal, priority=TaskPriority.HIGH, speak=self.speak, player=self.ui)
                            result += f" New task {new_id} started."
                else:
                    result = f"Unknown action: {action}"

            elif name == "project_control":
                action = args.get("action", "").lower()
                if action == "start":
                    self.project_mode = True
                    self.ui.show_project_monitor()
                    self.ui.log_project_terminal("=== PROJECT MODE ENABLED ===")
                    result = "Project mode activated, sir. I will monitor all development tasks."
                elif action == "stop":
                    self.project_mode = False
                    result = "Project mode deactivated."
                elif action == "clear":
                    self.ui.clear_project_monitor()
                    result = "Project monitor cleared."
                elif action == "show":
                    self.ui.show_project_monitor()
                    result = "Showing project monitor."
                else:
                    result = f"Unknown project action: {action}"

            elif name == "terminal_control":
                r = await loop.run_in_executor(None, lambda: terminal_control(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "gemini_cli":
                instruction = args.get("instruction", "")
                if _requires_development_mode(instruction):
                    result = await self._start_development_mode(instruction, redirected=True)
                else:
                    r = await loop.run_in_executor(None, lambda: gemini_cli(parameters=args, player=self.ui, speak=self.speak, project_mode=self.project_mode))
                    result = r or "Done."

            elif name == "development_mode":
                instruction = args.get("instruction", "")
                result = await self._start_development_mode(instruction)

            elif name == "assistant_control":
                action = args.get("action", "").lower()
                if action == "sleep":
                    self.sleep_mode = True
                    if not self.ui.muted:
                        self.ui.muted = True
                    self.ui.set_state("SLEEPING")
                    result = "Entering Standby Mode. I will ignore everything except wake commands."
                elif action == "wake":
                    self.sleep_mode = False
                    if self.ui.muted:
                        self.ui.muted = False
                    self.ui.set_state("LISTENING")
                    result = "I'm awake and ready, sir."
                else:
                    result = f"Unknown assistant action: {action}"

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")

                # Dev session temizliği
                asyncio.create_task(dev_manager.stop_session())

                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        if task_id:
            status = "completed" if "failed" not in str(result).lower() else "failed"
            self.ui.update_project_task(task_id, status)
            self.ui.log_project_terminal(f"[RESULT] {str(result)[:500]}")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _process_speech_queue(self):
        print("[JARVIS] 🗣️ Speech queue processor started")
        while True:
            text = await self._speech_queue.get()

            # Wait if currently speaking
            while True:
                with self._speaking_lock:
                    is_speaking = self._is_speaking
                if not is_speaking:
                    break
                await asyncio.sleep(0.1)

            try:
                await self.session.send_client_content(
                    turns={"parts": [{"text": text}]},
                    turn_complete=True
                )
                # Allow a short delay for the audio stream to start and _is_speaking to become True
                await asyncio.sleep(0.5)

                # Wait again until it finishes speaking this response
                while True:
                    with self._speaking_lock:
                        is_speaking = self._is_speaking
                    if not is_speaking:
                        break
                    await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[JARVIS] ❌ Speech queue error: {e}")

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            # Always listen if in sleep mode to catch wake command,
            # otherwise respect the mute button.
            if not jarvis_speaking and (not self.ui.muted or self.sleep_mode):
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    # If in sleep mode, we block all audio output to the user
                    # unless it's a tool call response handled later.
                    if response.data:
                        if not self.sleep_mode:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and not self.sleep_mode:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                    if response.tool_call:
                        fn_responses = []
                        is_wake_call = False
                        for fc in response.tool_call.function_calls:
                            if fc.name == "assistant_control" and fc.args.get("action") == "wake":
                                is_wake_call = True

                            # Execute the tool
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)

                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

                        # If it was a wake call, we might want to prompt the model to speak
                        if is_wake_call:
                            # The model will respond to the tool result, which is now allowed
                            # since self.sleep_mode is now False.
                            pass

        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.25
                    )
                except asyncio.TimeoutError:
                    self.set_speaking(False)
                    if self._turn_done_event and self._turn_done_event.is_set():
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        is_reconnect = False
        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._turn_done_event = asyncio.Event()
                    self._speech_queue  = asyncio.Queue()

                    print("[JARVIS] ✅ Connected.")
                    self.ui.set_state("LISTENING")

                    tg.create_task(self._process_speech_queue())
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

                    if not is_reconnect:
                        self.ui.write_log("SYS: JARVIS online.")
                        # Initial greeting
                        greetings = [
                            "Yes Sir. Systems are online. I'm pleased to see you again. How are you today?",
                            "Yes Sir. All systems functioning within normal parameters. It's good to have you back. How can I assist you?",
                            "Yes Sir. JARVIS is ready. I'm delighted to see you, sir. How are you feeling today?",
                            "Yes Sir. Always a pleasure to be of service. I'm glad you're here. Is there anything specific on your mind?"
                        ]
                        import random
                        start_text = random.choice(greetings)
                        await session.send_client_content(
                            turns={"parts": [{"text": f"[SYSTEM_NOTIFICATION] Session start. Greet the user naturally in English as '{start_text}'. From now on, ALWAYS strictly match the user's language: if they speak English, respond in English; if they speak Turkish, respond in Turkish. No exceptions."}]},
                            turn_complete=True
                        )
                    else:
                        await session.send_client_content(
                            turns={"parts": [{"text": "[SYSTEM_NOTIFICATION] Session silently resumed after timeout. Do not greet the user. Wait for their input or continue seamlessly."}]},
                            turn_complete=True
                        )

                    is_reconnect = True

            except Exception as e:
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()
            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
