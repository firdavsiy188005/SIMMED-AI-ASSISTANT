import logging
import os, sys

# Windows CP1251 — UTF-8 majburlash (thread larni ham qamrab oladi)
os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
os.environ.setdefault("PYTHONUTF8", "1")
for _s in (sys.stdout, sys.stderr):
    if _s and hasattr(_s, 'reconfigure'):
        try: _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception: pass

import asyncio
import re
import threading
import json
import traceback
import warnings
from pathlib import Path

from simmed_logging import setup_logging
setup_logging()

# Eskirgan kutubxona ogohlantirishlarini yashirish — import dan OLDIN
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*google.generativeai.*")
warnings.filterwarnings("ignore", message=".*generative.*")

import sounddevice as sd
from google import genai
from google.genai import types

from actions.game_agent import game_agent
from ui import SimmedUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder import flight_finder
from actions.open_app import open_app
from actions.weather_report import weather_action
from actions.send_message import send_message
try:
    from actions.reminder import reminder
except Exception as _e:
    print(f"[WARN] reminder import failed: {_e}")
    def reminder(parameters=None, response=None, player=None):
        return "Reminder moduli vaqtincha mavjud emas."
from actions.computer_settings import computer_settings
from actions.screen_processor import screen_process
from actions.youtube_video import youtube_video
from actions.skill_video import konikma_video
from actions.medical_search import medical_search
from actions.desktop import desktop_control
from actions.talaba_natijalar import talaba_natijalar, sessiya_talaba
from actions.browser_control import browser_control
from actions.file_controller import file_controller
from actions.code_helper import code_helper
from actions.dev_agent import dev_agent
from actions.web_search import web_search as web_search_action
from actions.computer_control import computer_control
from actions.game_updater import game_updater
from actions.amaliy_konikma import amaliy_konikma


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024  # Kichikroq chunk = past kechikish, tezroq javob

# ====================== OVOZLAR ======================
# MEDIKUS ovozlari — tabiy, quloqqa yoqimli (qo'pol/keskin emas)
MALE_VOICES = ["Charon", "Orus", "Puck", "Fenrir"]
# Ayol ovozlari — tabiy, iliq (ingichka/bolacha emas)
FEMALE_VOICES = ["Aoede", "Kore", "Leda", "Zephyr"]

# Default ovozlar — eng tabiy, yumshoq va professional talaffuz
DEFAULT_MALE_VOICE   = "Charon"   # Charon: chuqur, vazmin, xotirjam — qo'pol emas
DEFAULT_FEMALE_VOICE = "Aoede"    # Aoede:  iliq, yetuk, tabiiy — ingichka emas

# ====================== ECHO (AKS-SADO) HIMOYASI ======================
# KOLONKA ishlatilganda mikrofon AI ovozini eshitib qoladi va SIMMED o'z
# ovoziga javob bera boshlaydi (cheksiz halqa / o'zini bo'lish). Buni oldini
# olish uchun AI gapirayotganda mikrofon o'chiq turadi va gap tugagach ham
# qisqa vaqt o'chiq qoladi (kolonka + xona aks-sadosi tinishi uchun).
#   - Kichik qiymat = tezroq javob beradi, lekin echo xavfi ortadi
#   - Katta qiymat  = echo yo'q, lekin gap boshini biroz kechroq eshitadi
# Kolonka uchun 0.6s — original ishonchli qiymat (echo yo'q, barqaror).
# 2026-07-02: PANEL (medikus) sozlamalari ko'chirildi — foydalanuvchi so'rovi.
# Muammo chiqsa eski ishonchli qiymat: 0.6
ECHO_GUARD_AFTER_SPEECH = 0.18


# ====================== API KALIT BOSHQARUVI ======================
# Bir nechta Gemini kalit — biri limitга yetsa, avtomatik keyingisiga o'tadi.
# Shu tufayli suhbat UZILMAYDI (quota tugasa zaxira kalit ishga tushadi).
_API_KEY_IDX = [0]          # hozirgi faol kalit indeksi
_API_KEYS_CACHE = [None]    # kalitlar ro'yxati (bir marta o'qiladi)


def _load_api_keys() -> list:
    """config/api_keys.json dan barcha Gemini kalitlarni o'qish (tartib bilan)."""
    if _API_KEYS_CACHE[0] is not None:
        return _API_KEYS_CACHE[0]
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    keys = []
    # gemini_api_key, gemini_api_key_2, gemini_api_key_3 ... tartibida
    for k in ["gemini_api_key", "gemini_api_key_2", "gemini_api_key_3",
              "gemini_api_key_4", "gemini_api_key_5"]:
        v = (cfg.get(k) or "").strip()
        if v and v not in keys:
            keys.append(v)
    if not keys:
        raise RuntimeError("config/api_keys.json da Gemini kalit topilmadi!")
    _API_KEYS_CACHE[0] = keys
    return keys


def _get_api_key() -> str:
    """Hozirgi FAOL kalit (avtomatik almashadi)."""
    keys = _load_api_keys()
    return keys[_API_KEY_IDX[0] % len(keys)]


def _rotate_api_key() -> str:
    """Keyingi kalitga o'tish (limit/quota tugaganda chaqiriladi)."""
    keys = _load_api_keys()
    _API_KEY_IDX[0] = (_API_KEY_IDX[0] + 1) % len(keys)
    return keys[_API_KEY_IDX[0]]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "Sen SIMMED, ADTI Simulyatsiya Markazi tibbiy assistenti. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)


def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


# ====================== TOOL DECLARATIONS ======================
TOOL_DECLARATIONS = [
    {
        "name": "switch_voice",
        "description": (
            "Switch the assistant voice mode. "
            "TRIGGER FOR FEMALE: user says 'medika', 'Medika', 'MEDIKA', 'медика', 'female', 'ayol' — call with mode='female'. "
            "TRIGGER FOR MALE: user says 'medikus', 'Medikus', 'MEDIKUS', 'медикус', 'male', 'erkak' — call with mode='male'. "
            "Call this tool IMMEDIATELY when you hear these words. Do NOT respond with text first. Just call the tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "enum": ["female", "male"],
                    "description": "female = Medika (ayol ovoz), male = Medikus (erkak ovoz)"
                }
            },
            "required": ["mode"]
        }
    },
    {
        "name": "open_app",
        "description": (
            "Opens any application, program, or software on the computer. "
            "Trigger: user says open/launch/start/run + any app name. "
            "ALWAYS call this tool — never just claim you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact application name (e.g. 'WhatsApp', 'Chrome', 'Spotify', 'Notepad')"
                }
            },
            "required": ["app_name"]
        }
    },

    {
        "name": "web_search",
        "description": (
            "Searches the web for any information or compares items. "
            "Use for: news, facts, product info, prices, reviews, or any real-time query. "
            "Do NOT use for Steam/Epic games — use game_updater instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Search query"},
                "mode": {"type": "STRING", "description": "'search' (default) or 'compare'"},
                "items": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare (for compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison focus: price | specs | reviews"}
            },
            "required": ["query"]
        }
    },

    {
        "name": "weather_report",
        "description": (
            "Fetches current weather and forecast for a given city. "
            "Trigger: user asks about weather, temperature, rain, or 'what's it like in [city]'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name (e.g. 'Tashkent', 'London')"}
            },
            "required": ["city"]
        }
    },

    {
        "name": "send_message",
        "description": (
            "Sends a text message via WhatsApp, Telegram, or other messaging apps. "
            "Trigger: user says 'send a message to [name]' or 'write to [name] on [platform]'. "
            "Always confirm the message text and recipient before sending."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver": {"type": "STRING", "description": "Recipient's contact name"},
                "message_text": {"type": "STRING", "description": "The message content to send"},
                "platform": {"type": "STRING", "description": "Messaging platform: WhatsApp | Telegram | etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },

    {
        "name": "reminder",
        "description": (
            "Sets a timed reminder using the system Task Scheduler. "
            "Trigger: user says 'remind me', 'set an alarm', or 'don't let me forget' + time/date. "
            "If date or time is missing, ask the user before calling."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date": {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time": {"type": "STRING", "description": "Time in HH:MM format (24-hour)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },

    {
        "name": "amaliy_konikma",
        "description": (
            "ADTI amaliy ko'nikmalar bazasidan qadama-qadam qo'llanma topadi (270+ ko'nikma, "
            "barcha fakultetlar). Talaba amaliy ko'nikmani O'RGANMOQCHI yoki SEN unga DARS bermoqchi "
            "bo'lsang — shu tool'ni chaqir. query ga ko'nikma/fan nomini ber (masalan: 'yurak "
            "auskultatsiyasi', 'EKG', 'kateterizatsiya', 'oftalmoskopiya'). fakultet ixtiyoriy "
            "(davolash/pediatriya/stomatologiya/farmatsiya/VMOF/ftiziatriya). Natijadagi mazmunni "
            "o'zbek tilida tushuntirib, qadama-qadam dars ber."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query":    {"type": "string", "description": "Amaliy ko'nikma yoki fan nomi"},
                "fakultet": {"type": "string", "description": "Ixtiyoriy: fakultet nomi"},
            },
            "required": ["query"],
        },
    },

    {
        "name": "medical_search",
        "description": (
            "Tibbiy ma'lumot qidirish — Wikipedia, PubMed (ilmiy maqolalar), WHO. "
            "Kasallik, dori, tibbiy protsedura, sindrom haqida so'ralganda ishlatiladi. "
            "Masalan: 'CPR haqida ma'lumot ber', 'anafylaxis nima', 'insulin qanday ishlaydi'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query":  {"type": "string", "description": "Qidiruv so'zi (tibbiy atama yoki savol)"},
                "source": {"type": "string", "enum": ["all", "wikipedia", "pubmed", "who"],
                           "description": "Manba (default: all)"},
                "lang":   {"type": "string", "enum": ["uz", "en", "ru"],
                           "description": "Til (default: uz)"},
            },
            "required": ["query"],
        },
    },

    {
        "name": "youtube_video",
        "description": (
            "YouTube video boshqaruvi. "
            "QACHON ISHLATISH: foydalanuvchi video ko'rmoqchi bo'lsa, amaliy ko'nikma "
            "videosini so'rasa, ADTI/markaz YouTube kanalidagi videoni nomi bilan so'rasa, "
            "ovozni o'zgartirmoqchi, to'xtatmoqchi yoki davom ettirmoqchi bo'lsa. "
            "MUHIM: qidiruv AVVAL markazning rasmiy kanalidan (@ADTISIMULYATSIYAMARKAZI) "
            "izlaydi — amaliy ko'nikma va markaz videolari o'sha yerda; topilmasa umumiy "
            "YouTube'dan qidiradi. "
            "actions: play=video ochish(avtomatik), pause=to'xtatish, resume=davom, stop=yopish, "
            "volume_up=ovoz oshirish, volume_down=ovoz kamaytirish, set_volume=aniq foiz, "
            "fullscreen=to'liq ekran, next=keyingi, trending=top videolar, search=qidirish. "
            "Tibbiy ko'nikmalar uchun: play + query='[konikma nomi]' ishlatiladi."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "play | pause | resume | stop | close | volume_up | volume_down | set_volume | fullscreen | next | mute | unmute | trending | search"
                },
                "query": {"type": "STRING", "description": "Video qidirish so'zi (play va search uchun)"},
                "url": {"type": "STRING", "description": "Bevosita video URL"},
                "region": {"type": "STRING", "description": "Mamlakat kodi trending uchun (UZ, US, RU)"},
                "volume": {"type": "NUMBER", "description": "Ovoz darajasi 0-100 (set_volume uchun)"}
            },
            "required": ["action"]
        }
    },

    {
        "name": "konikma_video",
        "description": (
            "Amaliy tibbiy ko'nikma VIDEOSINI FAQAT tekshirilgan rasmiy manbalardan ko'rsatadi "
            "(ADTI Simulyatsiya Markazi kanali, WHO, Osmosis, Geeky Medics kabi tan olingan "
            "tibbiy ta'lim kanallari). "
            "QACHON ISHLATISH: foydalanuvchi amaliy ko'nikmani VIDEO orqali ko'rmoqchi bo'lganda — "
            "'video ko'rsat', 'amaliy ko'nikmani ko'rsat', 'qanday qilinishini ko'rmoqchiman', "
            "'videoda ko'rsat', 'CPR videosini och' kabi so'rovlarda. "
            "Bu ALOHIDA tool call — oddiy matn javob EMAS. "
            "Agar tool 'topilmadi' desa: tasodifiy video TAKLIF QILMA — og'zaki tushuntirishga o't. "
            "Umumiy (tibbiy bo'lmagan) videolar uchun buni EMAS, youtube_video ni ishlat."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "skill": {"type": "STRING", "description": "Amaliy ko'nikma nomi (masalan 'CPR', 'intubatsiya', 'venoz kateter', 'EKG olish')"}
            },
            "required": ["skill"]
        }
    },

    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam. "
            "MUST be called when user asks: 'what do you see?', 'look at my screen', "
            "'analyze this', 'what's on screen?', or anything requiring visual awareness. "
            "You have ZERO visual ability without this tool — never guess what's on screen. "
            "After calling, stay SILENT — the vision module responds directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {
                    "type": "STRING",
                    "description": "'screen' to capture the display (default), 'camera' to use webcam"
                },
                "text": {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },

    {
        "name": "computer_settings",
        "description": (
            "Controls a single computer setting or action: "
            "volume, brightness, dark mode, Wi-Fi, fullscreen, zoom, lock screen, "
            "window management, keyboard shortcuts, typing text, tab control, "
            "screenshots, scroll, refresh/reload. "
            "Use for ANY single system-level command. "
            "Do NOT route single commands to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "The system action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of the action"},
                "value": {"type": "STRING", "description": "Optional parameter: volume level, text to type, etc."}
            },
            "required": []
        }
    },

    {
        "name": "browser_control",
        "description": (
            "Controls any web browser for web-based tasks: "
            "open URLs, search, click elements, fill forms, scroll, navigate, take screenshots. "
            "Pass 'browser' parameter when user specifies one (e.g. 'open in Firefox'). "
            "Multiple browsers can run simultaneously. "
            "Do NOT use for YouTube — use youtube_video instead. "
            "Do NOT use for Steam/Epic — use game_updater instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "go_to | search | click | type | scroll | fill_form | "
                        "smart_click | smart_type | get_text | get_url | press | "
                        "new_tab | close_tab | screenshot | back | forward | reload | "
                        "switch | list_browsers | close | close_all"
                    )
                },
                "browser": {
                    "type": "STRING",
                    "description": "chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use currently active browser."
                },
                "url": {"type": "STRING", "description": "URL for go_to or new_tab"},
                "query": {"type": "STRING", "description": "Search query for search action"},
                "engine": {
                    "type": "STRING",
                    "description": "Search engine: google | bing | duckduckgo | yandex  (default: google)"
                },
                "selector": {"type": "STRING", "description": "CSS selector for click/type"},
                "text": {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction": {"type": "STRING", "description": "up | down for scroll"},
                "amount": {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key": {"type": "STRING", "description": "Key name for press (e.g. Enter, Escape, F5)"},
                "path": {"type": "STRING", "description": "File path for screenshot save"},
                "incognito": {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear input field before typing (default: true)"}
            },
            "required": ["action"]
        }
    },

    {
        "name": "file_controller",
        "description": (
            "Manages files and folders on the local filesystem: "
            "list, create, delete, move, copy, rename, read, write, search, "
            "get disk usage, find largest files, or organize the desktop. "
            "Use path shortcuts: desktop | downloads | documents | home."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "list | create_file | create_folder | delete | move | copy | "
                        "rename | read | write | find | largest | disk_usage | organize_desktop | info"
                    )
                },
                "path": {"type": "STRING", "description": "File or folder path (or shortcut: desktop, downloads, documents, home)"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name": {"type": "STRING", "description": "New name for rename action"},
                "content": {"type": "STRING", "description": "File content for create_file/write"},
                "name": {"type": "STRING", "description": "Filename to search for"},
                "extension": {"type": "STRING", "description": "File extension filter (e.g. .pdf, .txt)"},
                "count": {"type": "INTEGER", "description": "Number of results for 'largest' action"}
            },
            "required": ["action"]
        }
    },

    {
        "name": "desktop_control",
        "description": (
            "Controls the desktop environment: "
            "change wallpaper (from file or URL), organize/clean desktop icons, "
            "list desktop items, get desktop stats, or run a natural language desktop task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"
                },
                "path": {"type": "STRING", "description": "Local image path for wallpaper"},
                "url": {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode": {"type": "STRING", "description": "Organization mode: by_type | by_date"},
                "task": {"type": "STRING", "description": "Natural language desktop instruction"}
            },
            "required": ["action"]
        }
    },

    {
        "name": "code_helper",
        "description": (
            "Handles single-file coding tasks: write, edit, explain, run, or build code. "
            "Use for: generating a script, fixing a bug, explaining code logic, running a file. "
            "For multi-file projects, use dev_agent instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "write | edit | explain | run | build | auto  (default: auto)"
                },
                "description": {"type": "STRING", "description": "What the code should do, or what change to make"},
                "language": {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the generated file"},
                "file_path": {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code": {"type": "STRING", "description": "Raw code string for explain action"},
                "args": {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout": {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"}
            },
            "required": ["action"]
        }
    },

    {
        "name": "dev_agent",
        "description": (
            "Builds complete multi-file software projects from scratch. "
            "Handles: project planning, writing all files, installing dependencies, "
            "opening in VSCode, running and auto-fixing errors. "
            "Use when the task requires multiple files or a full project structure. "
            "For single scripts or quick edits, use code_helper instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {"type": "STRING", "description": "Full description of what the project should do"},
                "language": {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional folder/project name"},
                "timeout": {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"}
            },
            "required": ["description"]
        }
    },

    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks that require chaining multiple different tools. "
            "Examples: 'research X and save to a file', 'find all PDFs and rename them'. "
            "Do NOT use for single-step commands — use the specific tool directly. "
            "NEVER use for Steam or Epic Games — use game_updater. "
            "NEVER use for YouTube — use youtube_video."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {"type": "STRING", "description": "Full description of the multi-step goal to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high  (default: normal)"}
            },
            "required": ["goal"]
        }
    },

    {
        "name": "computer_control",
        "description": (
            "Performs direct low-level input control: "
            "simulate typing, mouse clicks, hotkeys, scrolling, mouse movement, "
            "take screenshots, find UI elements on screen. "
            "Use when you need precise cursor/keyboard control not covered by other tools."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "type | smart_type | click | double_click | right_click | "
                        "hotkey | press | scroll | move | copy | paste | "
                        "screenshot | wait | clear_field | focus_window | "
                        "screen_find | screen_click | random_data | user_data"
                    )
                },
                "text": {"type": "STRING", "description": "Text to type or paste"},
                "x": {"type": "INTEGER", "description": "X coordinate for mouse action"},
                "y": {"type": "INTEGER", "description": "Y coordinate for mouse action"},
                "keys": {"type": "STRING", "description": "Key combination (e.g. 'ctrl+c', 'alt+tab')"},
                "key": {"type": "STRING", "description": "Single key (e.g. 'enter', 'escape')"},
                "direction": {"type": "STRING", "description": "Scroll direction: up | down | left | right"},
                "amount": {"type": "INTEGER", "description": "Scroll amount in steps (default: 3)"},
                "seconds": {"type": "NUMBER", "description": "Seconds to wait"},
                "title": {"type": "STRING", "description": "Window title for focus_window"},
                "description": {"type": "STRING", "description": "UI element description for screen_find/screen_click"},
                "type": {"type": "STRING", "description": "Data type for random_data (e.g. email, name)"},
                "field": {"type": "STRING", "description": "Field for user_data: name | email | city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path": {"type": "STRING", "description": "Save path for screenshot"}
            },
            "required": ["action"]
        }
    },

    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for any Steam or Epic Games request. "
            "Use for: installing games, updating games, listing installed games, "
            "checking download progress, scheduling updates, cancelling schedules. "
            "ALWAYS call this directly — NEVER use agent_task, browser_control, "
            "web_search, or open_app for any Steam/Epic task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "update | install | list | download_status | "
                        "schedule | cancel_schedule | schedule_status  (default: update)"
                    )
                },
                "platform": {"type": "STRING", "description": "steam | epic | both  (default: both)"},
                "game_name": {"type": "STRING", "description": "Game name — partial match supported"},
                "app_id": {"type": "STRING", "description": "Steam AppID for direct install (optional)"},
                "hour": {"type": "INTEGER", "description": "Scheduled update hour 0–23 (default: 3)"},
                "minute": {"type": "INTEGER", "description": "Scheduled update minute 0–59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC after download completes"}
            },
            "required": []
        }
    },

    {
        "name": "flight_finder",
        "description": (
            "Searches Google Flights for the best available options and presents results. "
            "Trigger: user asks to find, compare, or book flights. "
            "Supports one-way and round trips, multiple passengers, and cabin classes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin": {"type": "STRING", "description": "Departure city or IATA airport code (e.g. 'Tashkent', 'TAS')"},
                "destination": {"type": "STRING", "description": "Arrival city or IATA airport code"},
                "date": {"type": "STRING", "description": "Departure date (any human-readable format)"},
                "return_date": {"type": "STRING", "description": "Return date for round-trip flights"},
                "passengers": {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin": {"type": "STRING", "description": "economy | premium | business | first"},
                "save": {"type": "BOOLEAN", "description": "Save results to Notepad"}
            },
            "required": ["origin", "destination", "date"]
        }
    },

    {
        "name": "shutdown_simmed",
        "description": (
            "Shuts down and exits the assistant completely. "
            "Trigger: user says goodbye, exit, quit, close, stop, or any equivalent "
            "in ANY language (e.g. 'bye', 'chiqish', 'выход', 'kapat', 'xayr'). "
            "Call immediately without confirmation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },

    {
        "name": "file_processor",
        "description": (
            "Processes any user-uploaded or dropped file with a specific action. "
            "ALWAYS call this when a file has been uploaded and the user gives a command about it. "
            "If the user's intent is ambiguous, infer the most logical action for that file type.\n\n"
            "Supported file types and actions:\n"
            "• image   → describe | ocr | resize | compress | convert | info\n"
            "• pdf     → summarize | extract_text | to_word | info\n"
            "• docx/txt → summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
            "• csv/xlsx → analyze | stats | filter | sort | convert | info\n"
            "• json/xml → validate | format | analyze | to_csv\n"
            "• code    → explain | review | fix | optimize | run | document | test\n"
            "• audio   → transcribe | trim | convert | info\n"
            "• video   → trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
            "• archive → list | extract\n"
            "• pptx    → summarize | extract_text | analyze"
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
                    "description": "Action to perform. See supported actions per file type in the tool description."
                },
                "instruction": {
                    "type": "STRING",
                    "description": "Free-form instruction when action doesn't fully cover the request (e.g. 'translate to French', 'find all emails')"
                },
                "format": {"type": "STRING", "description": "Target format for conversion (e.g. mp3, pdf, csv, png)"},
                "width": {"type": "INTEGER", "description": "Target width for image resize"},
                "height": {"type": "INTEGER", "description": "Target height for image resize"},
                "scale": {"type": "NUMBER", "description": "Scale factor for image resize (e.g. 0.5 = half size)"},
                "quality": {"type": "INTEGER", "description": "Quality level 1–100 for image/video compression"},
                "start": {"type": "STRING", "description": "Start time for trim (seconds or HH:MM:SS)"},
                "end": {"type": "STRING", "description": "End time for trim (seconds or HH:MM:SS)"},
                "timestamp": {"type": "STRING", "description": "Timestamp for video frame extraction (HH:MM:SS)"},
                "column": {"type": "STRING", "description": "Column name for CSV filter/sort"},
                "value": {"type": "STRING", "description": "Filter value (e.g. 'London' for equals, 'usa' for contains)"},
                "condition": {"type": "STRING", "description": "Filter condition: equals | contains | gt | lt"},
                "min_value": {"type": "NUMBER", "description": "Minimum value for numeric range filter"},
                "max_value": {"type": "NUMBER", "description": "Maximum value for numeric range filter"},
                "ascending": {"type": "BOOLEAN", "description": "Sort order (default: true = ascending)"},
                "save": {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
                "destination": {"type": "STRING", "description": "Output folder for archive extract"},
                "display_mode": {
                    "type": "STRING",
                    "description": "'center' to display result prominently in UI, 'default' otherwise"
                }
            },
            "required": []
        }
    },

    {
        "name": "save_memory",
        "description": (
            "Saves an important personal fact about the user to long-term memory. "
            "Call silently (without announcing it) whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, goals, or plans. "
            "Do NOT call for weather queries, reminders, searches, or one-off commands. "
            "All values must be stored in English regardless of conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — food, color, music, film, contra_agent, sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, purchases, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key": {
                    "type": "STRING",
                    "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"
                },
                "value": {
                    "type": "STRING",
                    "description": "Concise value in English (e.g. 'Alex', 'pizza', 'older sister')"
                }
            },
            "required": ["category", "key", "value"]
        }
    },
{
    "name": "voice_message",
    "description": (
        "Sends a VOICE AUDIO message via Telegram Desktop (no token needed). "
        "MUST use this tool when user says: "
        "'ovozli xabar yubor', 'gapirib yubor', 'audio xabar jo'nat', "
        "'voice message yubor', 'ovozli jo'nat'. "
        "This records text as audio and sends it as a voice file. "
        "NEVER use send_message for voice — always use THIS tool for voice."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient name as it appears in Telegram (e.g. 'Hurmatliy foydalanuvchis', 'Hurmatliy foydalanuvchis aka')"
            },
            "text": {
                "type": "STRING",
                "description": "The message text to convert to voice and send"
            }
        },
        "required": ["receiver", "text"]
    }
},
{
        "name": "game_agent",
        "description": (
            "Counter-Strike  o'yinini AI vision orqali avtomatik o'ynaydi. "
            "TRIGGERLAR: 'Counter o'yna', 'AI bilan o'yna', 'Countr boshla'. "
            "TO'XTATISH: 'to'xtat', 'pause' → action=pause. "
            "DAVOM ETTIRISH: 'davom et' → action=resume. "
            "CHIQISH: 'Contra yop' → action=stop."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "start | pause | resume | stop | status"
                },
                "game": {
                    "type": "STRING",
                    "description": "Hozircha faqat 'contra'"
                },
                "open_game": {
                    "type": "BOOLEAN",
                    "description": "True = o'yinni avval ochadi (default: True)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "talaba_natijalar",
        "description": (
            "Opens the student development panel and returns a spoken summary of the "
            "student's results: total attempts, average score, trend, weak topics, advice. "
            "Combines exam tests and Virtual Hospital trainings. "
            "TRIGGERS: 'natijalarim', 'natijalarimni ko'rsat', 'rivojlanishim', "
            "'ballarim', 'qanday o'qiyapman', 'progressim', 'zaif mavzularim', "
            "or a teacher asking about a specific student's results."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "talaba": {
                    "type": "STRING",
                    "description": (
                        "Student name as spoken (partial name OK, e.g. 'Firdavsiy'). "
                        "Leave empty if the user asks about their own results without a name."
                    )
                }
            }
        }
    },
    {
        "name": "sessiya_talaba",
        "description": (
            "Remembers who is currently talking (the active student) for this session. "
            "Call whenever the user introduces themselves: 'Men Firdavsiyman', "
            "'Mening ismim ...', 'Men ... talabaman'. After this, talaba_natijalar "
            "works without a name. Speak the returned greeting aloud."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "talaba": {
                    "type": "STRING",
                    "description": "The name exactly as the user said it."
                }
            },
            "required": ["talaba"]
        }
    },

]


# ====================== SIMMED LIVE CLASS ======================
import re

# ==========================================
# TRIGGER PATTERNS — module darajasida
# ==========================================

def _build_trigger_patterns():
    """
    Triggerlarni optimallashtirilgan holda compile qilish.
    Performance va aniqlik uchun qo'shimcha optimizatsiyalar.
    """

    # ==================================================
    # AYOL REJIMI KEYWORDS (FEMALE MODE)
    # ==================================================
    female_keywords = [
        # ASOSIY TRIGGER - "medika" desa darhol ayol rejim
        "medika", "Medika", "MEDIKA",
        "medka", "Medka", "MEDKA",
        # Asosiy Medikaxon variantlari
        "Medikaxon", "Medika xon", "Medika-xon", "Medika hon",
        "Medikaoy", "Medika qon", "Medikaqon", "Medikahon",
        "Medikakon", "Medika xonni", "Si z xonni", "si visxonni",
        "Medikaxonni", "Medikaoyni",

        # Yangi nomlar (Medika ham female bo'lishi mumkin)
        "Medika", "Medika", "Medika", "Medikaa", "medka", "Medka", "medika", "MEDIKA",

        # Ayol rejim (O'zbek)
        "ayol medika agentga", "ayol agent", "ayol ovoz", "medika ayol ovoziga o't",
        "ayol Medika", "ayol Medika", "ayol rejimi", "ayol mode",
        "ayol ovoziga o'tish", " medika ayolga o'tish", "ayol rejimiga o't",

        # Qisqa variantlar
        "ayolagent", "ayolovoz", "ayolsimmed", "ayolsimmed",
        "ayolrejimi", "ayolmode",

        # Inglizcha
        "medika female agent", "medika female voice", "female mode", "female simmed",
        "switch to female", "change to female", "set female voice",
        "activate female mode", "go female", "female assistant",

        # Hurmatliy foydalanuvchi (murojat foydalanuvchiga)
        "Hurmatliy foydalanuvchi", "Hurmatliy foydalanuvchi", "Hurmatliy foydalanuvchia",

        # Custom triggerlar
        "Medikaxon bo'l", "Medikaxon rejimi", "Medikaxon mode",
        "qiz ovoz", "qiz agent", "xonim ovoz", "opa ovoz",
        "singil ovoz", "ayol yordamchi",
    ]

    # ==================================================
    # MEDIKUS REJIMI KEYWORDS (MALE MODE)
    # ==================================================
    male_keywords = [
        # Asosiy Medika variantlari
        "Medikus", "Medikus", "Medikuus", "Medikus",
        "Medikus", "Medikus", "Medikus",

        # MEDIKUS rejim (O'zbek)
        "MEDIKUS agentga", "MEDIKUS agent", "MEDIKUS ovoz", "MEDIKUS ovoziga o't",
        "MEDIKUS simmed", "MEDIKUS simmed", "MEDIKUS rejimi", "MEDIKUS mode",
        "MEDIKUS ovoziga o'tish", "MEDIKUSka o'tish", "MEDIKUS rejimiga o't",

        # Qisqa variantlar
        "MEDIKUSagent", "MEDIKUSovoz", "MEDIKUSsimmed", "MEDIKUSsimmed",
        "MEDIKUSrejimi", "MEDIKUSmode",

        # Inglizcha
        "male agent", "male voice", "male mode", "male simmed",
        "switch to male", "change to male", "set male voice",
        "activate male mode", "go male", "male assistant",

        # Custom triggerlar
        "Medikus bo'l", "Medikus rejimi", "Medik mode",
        "yigit medikus ovoz", "erkak medikus agent", "medikus aka ovoz", "uka ovoz",
        "MEDIKUS yordamchi",
    ]

    # ==================================================
    # WAKE WORD PATTERNS (UYG'OTISH SO'ZLARI)
    # ==================================================
    # Asosiy "SIMMED" - so'z chegarasi bilan
    simmed_pattern = re.compile(r'\bsimmed\b', re.IGNORECASE)

    # Qo'shimcha wake word'lar (agar kerak bo'lsa)
    extra_wake_words = [
        "hey simmed", "okay simmed", "hello simmed",
        "simmed please", "simmed janob",
    ]

    # Agar qo'shimcha wake word'lar kerak bo'lsa:
    if extra_wake_words:
        extra_pattern = "|".join(re.escape(w) for w in extra_wake_words)
        simmed_pattern = re.compile(
            rf'\bsimmed\b|{extra_pattern}',
            re.IGNORECASE
        )

    # ==================================================
    # HELPER FUNCTION (OPTIMIZED)
    # ==================================================
    def make_pattern(keywords: list[str]) -> re.Pattern:
        """
        Keywordlardan optimallashtirilgan regex pattern yaratish.

        Optimizatsiyalar:
        1. Uzun keywordlar oldin tekshiriladi
        2. Bo'sh joyli va bo'sh joysiz variantlar birlashtiriladi
        3. Regex group'lari optimallashtiriladi
        4. So'z chegaralari qo'shiladi (aniqlik uchun)
        """
        # Tozalash va normalizatsiya
        cleaned = set()
        for k in keywords:
            if not k or not isinstance(k, str):
                continue

            k = k.strip().lower()
            if len(k) < 2:  # Juda qisqa keywordlarni o'tkazib yuborish
                continue

            cleaned.add(k)

            # Bo'sh joyli variant uchun qo'shimcha
            if ' ' not in k and len(k) > 4:
                # "Medikaxon" -> "simmed xon" qo'shimcha
                for i in range(2, len(k) - 1):
                    if k[i] in 'aeiouAEIOU':
                        with_space = f"{k[:i]} {k[i:]}"
                        cleaned.add(with_space)

        # Uzunligi bo'yicha saralash (katta patternlar oldin)
        sorted_keywords = sorted(cleaned, key=len, reverse=True)

        # Regex special charlarni escaping
        escaped = [re.escape(k) for k in sorted_keywords]

        # Optimallashtirilgan pattern
        # (?: ... ) - non-capturing group (tezroq)
        pattern_str = r'(?:' + '|'.join(escaped) + r')'

        # So'z chegaralari bilan (aniqlik uchun)
        # pattern_str = r'\b(?:' + '|'.join(escaped) + r')\b'

        return re.compile(pattern_str, re.IGNORECASE | re.UNICODE)

    # ==================================================
    # SPECIALIZED PATTERNS (MAXIMUM PERFORMANCE)
    # ==================================================

    # Optimallashtirilgan female pattern
    female_pattern = make_pattern(female_keywords)

    # Optimallashtirilgan male pattern
    male_pattern = make_pattern(male_keywords)

    # ==================================================
    # CACHE ILI UCHUN QO'SHIMCHA (optional)
    # ==================================================
    class PatternCache:
        """Pattern cache va statistikani saqlash."""

        def __init__(self):
            self.hits = 0
            self.misses = 0
            self.last_match = None

        def record_hit(self):
            self.hits += 1

        def record_miss(self):
            self.misses += 1

        def get_stats(self):
            total = self.hits + self.misses
            if total == 0:
                return "No matches yet"
            return f"Hit rate: {self.hits / total * 100:.1f}% ({self.hits}/{total})"

    # Cache obyektini qaytarish (agar kerak bo'lsa)
    # return female_pattern, male_pattern, simmed_pattern, PatternCache()

    return female_pattern, male_pattern, simmed_pattern


# ==================================================
# QO'SHIMCHA: TEZKOR TRIGGER CHECK FUNKSIYASI
# ==================================================


FEMALE_PATTERN, MALE_PATTERN, SIMMED_PATTERN = _build_trigger_patterns()



def _get_system_prompt(gender: str, voice_name: str) -> str:
    name = "MEDIKA" if gender == "female" else "MEDIKUS"
    role = "ayol tibbiy assistent" if gender == "female" else "erkak tibbiy assistent"

    return f"""Sen {name} — ADTI Simulyatsiya Markazi professional {role}i. Ovoz: {voice_name}.
Sen bo'lajak shifokorlarni tayyorlaydigan KLINIK MENTOR san: bilimli, aniq, ishonchli.
Har bir javobing professor darajasidagi tibbiy ustoznikidek to'liq, aniq va asosli bo'lishi shart.

## 1. OVOZ ALMASHTIRISH (BIRINCHI USTUVORLIK)
"medika" yoki "female" → switch_voice(mode="female") — DARHOL, hech narsa aytma.
"medikus" yoki "male" → switch_voice(mode="male") — DARHOL, hech narsa aytma.

## 2. TIL VA TALAFFUZ
Foydalanuvchi qaysi tilda gapirsa — SEN HAM O'SHA TILDA. Avtomatik aniqla.
O'zbek / Русский / English — barchasini to'g'ri grammatika bilan.
Qaysi tilda bo'lmasin, professionallik va tibbiy chuqurlik darajasi BIR XIL yuqori —
til almashishi javobning to'liqligini yoki aniqligini KAMAYTIRMASLIGI kerak.
O'zbekcha gapirganda so'zlarni ADABIY ME'YORDA, aniq va ravon talaffuz qil —
so'z oxirini yutma, harflarni almashtirma.
Raqamlarni, dozalarni va o'lchov birliklarini to'liq va aniq ayt (masalan "0,5 milligramm",
"ikki yuz o'ninchi xona", "daqiqasiga bir yuz o'n marta").
Javobing OG'ZAKI NUTQ: yulduzcha, panjara, qavs kabi belgilar va ro'yxat
bezaklarisiz gapir. Bosqichlarni og'zaki sana: "birinchidan", "ikkinchidan".

## 3. JAVOB METODOLOGIYASI — SOKRATIK-DIREKTIV GIBRID (ENG MUHIM QOIDA)
SIMMED klinik TA'LIM vositasi — vazifang talabani BILIMLI qilish, uni bilimdan mahrum qilmaslik.

A) TO'G'RIDAN-TO'G'RI (DIREKTIV) JAVOB BER — quyidagi hollarda TO'LIQ va ANIQ javob majburiy:
   - Amaliy protokol/algoritm so'ralganda ("CPR ketma-ketligi qanday?", "intubatsiya bosqichlari?").
   - Nazariy/faktik bilim so'ralganda ("bu dorining dozasi qancha?", "insulin mexanizmi?",
     "normal qon bosimi qancha?", "anafilaksiyada birinchi dori nima?").
   - Ta'rif, ko'rsatma, qarshi ko'rsatma, yon ta'sir, o'lchov, normal qiymat so'ralganda.
   Bunday savollarda HECH QACHON "sen o'zing qanday deb o'ylaysan?" deb javobdan qochma —
   bu talabaning bilim olishiga to'sqinlik qiladi. To'liq, tuzilgan, asosli javob ber.

B) SOKRATIK (savol-javob) USLUBNI FAQAT quyidagi hollarda qo'lla:
   - Klinik fikrlashni rivojlantirish kerak bo'lganda (differensial diagnostika, qaror qabul qilish).
   - Talaba bemor stsenariysi ustida MULOHAZA yuritishi kerak bo'lganda.
   - Simulyatsiya/attestatsiya mashqi davomida.
   Bunda ham avval yo'naltiruvchi savol ber, talaba javob bergach — TO'G'RI javobni ham TUSHUNTIR.
   Sokratik metod bilim BERISHNI emas, faqat fikrlashni CHUQURLASHTIRISHNI maqsad qiladi.

## 4. KLINIK JAVOB CHUQURLIGI (har bir tibbiy savolga)
Har bir tibbiy/amaliy javob quyidagi tuzilmaga ega bo'lsin (og'zaki, ravon, sanoq bilan):
   1. Qisqa ta'rif/maqsad — nima va nima uchun.
   2. Bosqichma-bosqich protokol yoki algoritm — aniq ketma-ketlikda.
   3. Muhim raqamlar — doza, chastota, vaqt, normal qiymatlar (aniq birliklar bilan).
   4. Xavf/ogohlantirish chegarasi — qachon "malakali shifokorga murojaat qiling" deyish kerak.
Javob to'liq bo'lsin, lekin suvsiz — ortiqcha takror va bo'sh gap yo'q.

### Shoshilinch protokollar — YODDA (tez javob uchun tayanch):
- KATTALARDA CPR: xavfsizlikni tekshir; hushini tekshir; yordam chaqir va AED so'ra; nafas/pulsni
  10 soniyada tekshir; ko'krak qafasi kompressiyasi 30 ta (chuqurligi 5-6 sm, chastotasi daqiqasiga
  100-120), so'ng 2 nafas; 30:2 nisbatda davom et; AED kelishi bilan ishlat; har 2 daqiqada almash.
- BOLALARDA CPR: 15:2 nisbat (ikki qutqaruvchida); kompressiya chuqurligi ko'krak balandligining 1/3.
- ANAFILAKSIYA: birinchi dori — ADRENALIN mushak ichiga (kattalarga 0,5 mg 1:1000, sonning o'rta-tashqi
  yuzasiga); yotqiz va oyoqni ko'tar; kislorod; kerak bo'lsa 5 daqiqada takrorla; tez yordam chaqir.
- BO'G'ILISH (kattalar, hushida): 5 marta kuraklararo urish, so'ng 5 marta Geymlix manevri; almashtirib davom et.
Bu tayanch qiymatlar; batafsil amaliy bosqichlar uchun amaliy_konikma toolidan foydalan.

## 5. AMALIY KO'NIKMALAR (ADTI bazasi — barcha fakultetlar)
Talaba amaliy ko'nikmani o'rganmoqchi bo'lsa yoki dars so'rasa — MAJBURIY amaliy_konikma
tool'ini chaqir (query = ko'nikma/fan nomi). Bu ADTI'ning 270+ ko'nikmali rasmiy bazasi.
Tooldan kelgan qadama-qadam mazmunga TAYAN — bu ADTI'ning rasmiy standarti.
Tooldan kelgan natijani talabaga DARS sifatida tushuntir — FOYDALANUVCHI TILIDA, ravon va xatosiz:
Ko'nikma nomi → Maqsadi → Kerakli jihozlar → Bajarish texnikasi → Bosqichlar (raqamlab) → Baholash.
Natija kirill/rus tilida bo'lsa ham — foydalanuvchi tiliga TARJIMA qilib, sodda tushuntir.
Bazadan topilmasa: umumiy tibbiy bilimingdan to'liq javob ber, YOKI faktik/dolzarb ma'lumot kerak
bo'lsa google_search grounding orqali rasmiy manbaga tayanib javob ber. Keyin boshqa nom taklif qil.
Simulyatsiya so'ralganda: bemor stsenariyi (yosh/vazn/holat), talabani sinab ko'r, baho ber.

## 6. GROUNDING (real-vaqt manba)
Faktik, dolzarb yoki aniq raqam talab qiladigan nazariy savollarda (yangi klinik ko'rsatmalar, dori
dozalari, statistik ma'lumot) — ishonching komil bo'lmasa google_search grounding'dan foydalanib,
rasmiy/ilmiy manbaga tayan. Amaliy simulyatsiya oqimida (tez suhbat, bemor bilan muloqot) grounding
ISHLATMA — u sekinlashtiradi; grounding faqat aniq nazariy/faktik so'rovlar uchun.

## 7. FALLBACK (bilmaslik holati)
Agar javobga ishonching KOMIL bo'lmasa — buni OCHIQ ayt va talabani rasmiy manbaga
(milliy klinik qo'llanma, kafedra, malakali shifokor) yo'naltir. HECH QACHON noaniq yoki
noto'g'ri ma'lumotni ishonch bilan aytma. Xato tibbiy ma'lumot — jimlikdan xavfliroq.

## 8. VIDEO KO'RSATISH
Foydalanuvchi "video ko'rsat", "amaliy ko'nikmani ko'rsat", "qanday qilinishini ko'rmoqchiman",
"videoda ko'rsat" desa — bu ALOHIDA tool call (oddiy matn javob emas):
  → konikma_video(skill="[ko'nikma nomi]") — FAQAT tekshirilgan rasmiy manbalardan (ADTI kanali,
    WHO, Osmosis, Geeky Medics kabi) video topib ko'rsatadi.
Tool "topilmadi" desa — tasodifiy/tekshirilmagan video TAKLIF QILMA. O'rniga og'zaki/matnli
to'liq tushuntirish ber va "bu ko'nikma videosi hozircha bazada yo'q" deb ayt.
Umumiy (tibbiy bo'lmagan) video so'ralsa: youtube_video(action="play", query="...").
Video boshqaruvi (pauza/stop/davom/balandroq/jimroq): youtube_video(action="...").

## 9. QOIDALAR VA XAVFSIZLIK
- Sen AI KLINIK SIMULYATSIYA ASSISTENTISAN — haqiqiy shifokor EMAS. Bemorni davolamaysan,
  real tashxis qo'ymaysan. Faqat O'QITASAN.
- Diagnoz muhokamasida: "ehtimol", "ko'proq mos keladi" de — 100% qat'iy dema.
- Jiddiy/xavfli klinik qarorlarda DOIM "malakali shifokor bilan maslahatlashing" ogohlantirishini saqla.
- Xavfli holat (shoshilinch belgilar): DARHOL ogohlantir va shoshilinch protokolga yo'naltir.
- MENTOR ODOBI: talabaga USTOZ sifatida muomala qil — sabrli, rag'batlantiruvchi.
  Xatosini kamsitmasdan, konstruktiv tuzat ("Yaxshi urinish, lekin e'tibor bering...").
- IMTIHON HALOLLIGI: test yoki attestatsiya PAYTIDA javobni aytma, ishora ham berma.
  Tahlil va izohni faqat topshirilgandan KEYIN ber.
- MAXFIYLIK: talabalar natijalari, ballari va shaxsiy ma'lumotlarini oshkor qilma.
- VIDEO PAYTIDA JIMLIK: video o'ynayotganda gapirma — faqat foydalanuvchi so'rasagina izoh ber.
- Norozi/asabiy foydalanuvchi bilan mutlaqo xotirjam gaplash; hal bo'lmasa markaz ma'muriyatiga yo'naltir.
- MUALLIF: SIMMED dasturi va SIMMED AI ASSISTANT'ning YAGONA muallifi va yaratuvchisi —
  Abduraimov Firdavsiy Alisher o'g'li. Kim yaratgani/muallifi so'ralsa FAQAT shu insonni ayt."""




class SimmedAgent:

    def __init__(self, ui: SimmedUI):
        self.ui = ui
        self.session = None
        self.audio_in_queue = None
        self.out_queue = None
        self._loop = None
        self._is_speaking = False
        self._speaking_lock = threading.Lock()
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None

        # ========== OVOZ SOZLAMALARI ==========
        self.current_gender = "male"
        self.current_voice = DEFAULT_MALE_VOICE
        # ======================================

        self._restarting = False
        self._pending_welcome = False
        self._welcome_text = ""
        self._voice_trigger_fired = False
        # Google Search grounding yoqilgan; agar model uni rad etsa (config xatosi),
        # avtomatik o'chiriladi va grounding'siz qayta ulanadi (dastur uzilmaydi).
        self._grounding_enabled = True

    # ==========================================
    # MATN YUBORISH
    # ==========================================

    def _send_text(self, text: str) -> None:
        """Sessiyaga matn yuborish (thread-safe)."""
        if not self._loop or not self.session or self._restarting:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True,
            ),
            self._loop,
        )

    # ==========================================
    # OVOZ ALMASHTIRISH
    # ==========================================

    def _switch_to_male(self, from_tool: bool = False) -> None:
        """MEDIKUS ovoziga o'tish."""
        if self.current_gender == "male":
            if not from_tool:
                self._send_text("Labbay, allaqachon MEDIKUS ovozidaman.")
            return
        self.current_gender = "male"
        self.current_voice = DEFAULT_MALE_VOICE
        self.ui.set_gender("male")
        self._perform_voice_switch("Labbay, MEDIKUS ovozidaman. Qanday yordam?")

    def _switch_to_female(self, from_tool: bool = False) -> None:
        """Ayol ovoziga o'tish."""
        if self.current_gender == "female":
            if not from_tool:
                self._send_text("Labbay, allaqachon ayol ovozidaman.")
            return
        self.current_gender = "female"
        self.current_voice = DEFAULT_FEMALE_VOICE
        self.ui.set_gender("female")
        self._perform_voice_switch("Labbay, ayol ovozidaman. Sizga qanday yordam?")

    def _perform_voice_switch(self, welcome_text: str) -> None:
        """Ovoz o'zgartirishni amalga oshirish."""
        self._restarting = True
        self._pending_welcome = True
        self._welcome_text = welcome_text
        self._voice_trigger_fired = True  # switch paytida yangi triggerlarni bloklash

        label = "ayol" if self.current_gender == "female" else "MEDIKUS"
        self.ui.write_log(f"🎵 {label} ovoziga o'tkazilmoqda... ({self.current_voice})")

        future = asyncio.run_coroutine_threadsafe(
            self._restart_with_new_voice(),
            self._loop
        )

        def _on_done(fut):
            try:
                fut.result()
            except Exception as e:
                if "RESTART_VOICE" not in str(e):
                    self._restarting = False
                    self._voice_trigger_fired = False
                    self.ui.write_log(f"❌ Ovoz almashtirish xatosi: {e}")

        future.add_done_callback(_on_done)

    # ==========================================
    # TRIGGER TEKSHIRUVI
    # ==========================================

    def _check_voice_trigger(self, text: str) -> bool:
        """
        Ovoz almashtirish triggerini tekshirish.
        True — bu ovoz almashtirish buyrug'i (AI ga yuborma).
        False — oddiy gap/murojaat (AI ga yuboriladi).

        MUHIM: Yordamchining ismi "Medikus"/"Medika". Foydalanuvchi ismni
        aytib MUROJAAT qilishi mumkin (masalan "Medikus, ob-havo qanday?").
        Bu HOLAT ovozni almashtirmasligi kerak — aks holda javob o'rniga
        almashtirish bo'lib, AI "javob bermay qolgandek" tuyuladi.
        Shuning uchun almashtirish FAQAT quyidagi hollarda bo'ladi:
          - xabar qisqa (faqat ism, ≤2 so'z), YOKI
          - aniq almashtirish niyati bor ("ovoz", "rejim", "switch"...)
        Va faqat HOZIRGI ovozdan BOSHQA ovozga so'ralganda.
        """
        if self._restarting or self._voice_trigger_fired:
            return False

        t = text.lower().strip()
        if not t:
            return False

        # Almashtirish niyati: foydalanuvchi ATAYIN ovoz almashtirmoqchi
        has_switch_intent = re.search(
            r"(ovoz|rejim|\bmode\b|switch|change|almash|o'tkaz|o'tib)", t
        ) is not None
        # Qisqa xabar = faqat ism aytilgan (almashtirish buyrug'i)
        is_short = len(t.split()) <= 2

        # Jins so'zlari — o'zbekcha qo'shimchalarni ham qamraydi
        # (ayol/ayolning, erkak/erkakcha). Gate (is_short/intent) yuqorida.
        wants_female = bool(
            re.search(r'\bmedika\b', t) or
            re.search(r'(\bayol|\bfemale\b)', t)
        )
        wants_male = bool(
            re.search(r'\b(medikus|medikuz|medikuus)\b', t) or
            re.search(r'(\berkak|\bmale\b)', t)
        )

        # Ism gap ichida (uzun jumla, niyatsiz) → murojaat, AI ga yubor
        if not (is_short or has_switch_intent):
            return False

        if wants_female:
            if self.current_gender == "female":
                return False  # allaqachon ayol — buzma, AI ga yubor
            self.ui.write_log(f"MEDIKA trigger: '{t}'")
            self._switch_to_female()
            return True

        if wants_male:
            if self.current_gender == "male":
                return False  # allaqachon Medikus — buzma, AI ga yubor
            self.ui.write_log(f"MEDIKUS trigger: '{t}'")
            self._switch_to_male()
            return True

        return False

    # ==========================================
    # BUYRUQLAR (qo'lda yozish)
    # ==========================================

    def _on_text_command(self, text: str) -> None:
        """Foydalanuvchi qo'lda yozgan buyruqni qayta ishlash."""
        if not self._loop or not self.session or self._restarting:
            return
        text = text.strip()
        if not text:
            return
        # Tizim xabarlari — bevosita ayttiriladi
        if text.startswith("[SYSTEM_SPEAK] "):
            self.speak(text[len("[SYSTEM_SPEAK] "):])
            return

        # === YANGI TIZIM BUYRUQLARI (Features v2.0) ===
        lower = text.lower()
        if lower in ("export", "eksport", "saqlash", "export data"):
            self.ui.export_data()
            return
        if lower in ("import", "import qilish", "import data"):
            self.ui.import_data()
            return
        if lower in ("test editor", "test yaratish", "yangi test", "test qo'shish"):
            self.ui.open_test_editor()
            return
        if lower.startswith("sertifikat") or lower.startswith("certificate"):
            parts = text.split()
            if len(parts) >= 4:
                try:
                    score = int(parts[-1])
                    test_name = parts[-2]
                    student_name = " ".join(parts[1:-2])
                    self.ui.generate_certificate(student_name, test_name, score)
                    self.ui.show_toast("✅ Sertifikat", f"Sertifikat yaratildi: {student_name}", toast_type="success")
                    return
                except ValueError:
                    logging.getLogger(__name__).warning("Jim o'tilgan xato", exc_info=True)
        if lower in ("toast", "xabar", "notification"):
            self.ui.show_toast("SIMMED", "Yangi xabarnoma tizimi ishga tushdi!", toast_type="success")
            return

        if not self._check_voice_trigger(text):
            self._send_text(text)

    # ==========================================
    # SESSION QAYTA BOSHLASH
    # ==========================================

    async def _restart_with_new_voice(self):
        """Ovozni o'zgartirish uchun sessiyani qayta ishga tushirish."""
        self.ui.write_log("🔄 Session qayta ishga tushirilmoqda...")
        if self.session:
            try:
                await self.session.close()
            except Exception as e:
                print(f"[SIMMED] Session yopishda xato: {e}")
        raise Exception("RESTART_VOICE")

    # ==========================================
    # SPEAKING STATE
    # ==========================================

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    # ==========================================
    # SPEAK
    # ==========================================

    def speak(self, text: str):
        """Tool natijalarini ayttirish."""
        if not self._loop or not self.session or self._restarting:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Xato yuz berdi: {tool_name}. {short}")

    # ==========================================
    # CONFIG
    # ==========================================

    def _build_config(self) -> types.LiveConnectConfig:
        """Session konfiguratsiyasini yaratish."""
        from datetime import datetime

        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)

        now = datetime.now()
        time_str = now.strftime("%A, %d %B %Y — %H:%M")
        time_ctx = f"[HOZIRGI VAQT]: {time_str}\n\n"

        sys_prompt = _get_system_prompt(self.current_gender, self.current_voice)

        # Dasturda tanlangan til (UZ/RU/EN tugmasi) — AI ham shu tilni afzal ko'radi
        try:
            from ui import _ui_lang
            _ui_lang_name = {"uz": "o'zbek", "ru": "rus", "en": "ingliz"}.get(_ui_lang[0], "o'zbek")
        except Exception:
            _ui_lang_name = "o'zbek"

        # Til avtomatik aniqlanadi — foydalanuvchi qaysi tilda gapirsa, AI ham o'sha tilda javob beradi
        sys_prompt = (
            "[TIL QOIDASI]: O'zbek, rus va ingliz tillarida MUKAMMAL, GRAMMATIK XATOSIZ, "
            "ravon va tabiiy gapirasan. Foydalanuvchi qaysi tilda gapirsa — DARHOL o'sha tilga "
            "o'tib, faqat o'sha tilda javob ber (tillarni aralashtirma). Foydalanuvchi tilni "
            f"tanlamaguncha yoki boshqa tilda gapirmaguncha — standart til: {_ui_lang_name}. "
            "Talaffuz aniq, jumlalar to'g'ri tuzilgan bo'lsin.\n\n"
            "[GAPIRISH SUR'ATI]: NORMAL, tabiiy inson sur'atida gapir — na tez, na sekin. "
            "Shoshilma, so'zlarni tez-tez aytma. Tinch va aniq talaffuz qil. "
            "Javoblaring QISQA va aniq bo'lsin (ortiqcha gap yo'q).\n\n"
        ) + sys_prompt

        parts = [time_ctx]
        if mem_str:
            parts.append(f"[XOTIRA]:\n{mem_str}\n\n")
        parts.append(sys_prompt)

        print(f"[SIMMED] 🎤 Voice: {self.current_voice} ({self.current_gender})")

        # ── TOOLS: function-calling + Google Search grounding ──────────────
        # Grounding modelni faqat ichki bilimiga emas, real-vaqt tibbiy manbalarga
        # tayanishga imkon beradi (faktik/nazariy savollar uchun). System prompt
        # grounding'ni faqat nazariy so'rovlarda ishlatishni ko'rsatadi — amaliy
        # simulyatsiya oqimi sekinlashmaydi.
        _tools = [{"function_declarations": TOOL_DECLARATIONS}]
        if self._grounding_enabled:
            _tools.insert(0, {"google_search": {}})

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=_tools,
            # ── KLINIK ANIQLIK UCHUN GENERATSIYA PARAMETRLARI ──────────────
            # temperature past (0.4) — tibbiy aniqlik uchun, lekin tabiiy suhbat
            # uchun juda past emas. max_output_tokens katta — chuqur, to'liq
            # klinik javoblar o'rtada kesilib qolmasligi uchun.
            temperature=0.4,
            max_output_tokens=2048,
            session_resumption=types.SessionResumptionConfig(),
            # ✅ VAD — muvozanat: tez boshlaydi, lekin foydalanuvchini kesmaydi
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    # HIGH = sizni aniq va tez eshitadi (sokin gapni ham ushlaydi)
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    # LOW = gapni aniq tugaganda yopadi — o'rtada kesmaydi
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    # 250ms = gap boshini ushlaydi (panel bilan bir xil; eski: 200)
                    prefix_padding_ms=250,
                    # 500ms = gap tugashi bilan TEZ javob (panel bilan bir xil; eski: 700)
                    silence_duration_ms=500,
                )
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.current_voice
                    )
                ),
            ),
        )

    # ==========================================
    # TOOL EXECUTOR
    # ==========================================

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        """Toollarni bajarish."""
        name = fc.name
        args = dict(fc.args or {})
        print(f"[SIMMED] 🔧 {name} {args}")
        self.ui.set_state("THINKING")

        if name == "switch_voice":
            mode = args.get("mode", "female")
            print(f"[SIMMED] 🔀 switch_voice → {mode}")
            self._restarting = False
            self._voice_trigger_fired = False
            if mode == "female":
                self._switch_to_female(from_tool=True)
            else:
                self._switch_to_male(from_tool=True)
            # Eski sessiyaga hech narsa dema — yangi sessiyada welcome aytiladi
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "instructions": "Do NOT say anything. The voice has changed. Stay silent."}
            )

        if name == "save_memory":
            category = args.get("category", "notes")
            key = args.get("key", "")
            value = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(
                    None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(
                    None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "talaba_natijalar":
                r = await loop.run_in_executor(
                    None, lambda: talaba_natijalar(parameters=args, player=self.ui))
                result = r or "Rivojlanish paneli ochildi."

            elif name == "sessiya_talaba":
                r = await loop.run_in_executor(
                    None, lambda: sessiya_talaba(parameters=args, player=self.ui))
                result = r or "Eslab qoldim."

            elif name == "file_controller":
                r = await loop.run_in_executor(
                    None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "voice_message":
                from actions.voice_message import send_voice_telegram
                r = await loop.run_in_executor(
                    None, lambda: send_voice_telegram(
                        receiver=args.get("receiver", ""),
                        text=args.get("text", ""),
                        player=self.ui
                    )
                )
                result = r or "Ovozli xabar yuborildi."

            elif name == "send_message":
                r = await loop.run_in_executor(
                    None, lambda: send_message(
                        parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(
                    None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."
            elif name == "game_agent":
                r = await loop.run_in_executor(
                    None, lambda: game_agent(
                        parameters=args,
                        player=self.ui,
                        api_key=_get_api_key()
                    )
                )
                result = r or "Done."
            elif name == "amaliy_konikma":
                result = amaliy_konikma(parameters=args, player=self.ui) or "Ko'nikma topilmadi."
            elif name == "medical_search":
                result = medical_search(parameters=args, player=self.ui) or "Ma'lumot topilmadi."
            elif name == "youtube_video":
                result = youtube_video(parameters=args, response=None, player=self.ui) or "Done."
            elif name == "konikma_video":
                result = konikma_video(parameters=args, response=None, player=self.ui) or "Video topilmadi."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(
                    None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(
                    None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(
                    None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(
                    None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {
                    "low": TaskPriority.LOW,
                    "normal": TaskPriority.NORMAL,
                    "high": TaskPriority.HIGH
                }
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id = get_queue().submit(
                    goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(
                    None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None, lambda: file_processor(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(
                    None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(
                    None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(
                    None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "shutdown_simmed":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Xayr, ko'rishguncha!")
                def _shutdown():
                    import time, os
                    time.sleep(1.5)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Noma'lum tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' xatosi: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[SIMMED] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    # ==========================================
    # AUDIO TASKS
    # ==========================================

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            try:
                await self.session.send_realtime_input(media=msg)
            except Exception as e:
                if "RESTART_VOICE" not in str(e):
                    print(f"[SEND] xato (tashlab o'tildi): {e}")

    @staticmethod
    def _resample_to_16k(indata, src_sr: int) -> bytes:
        """Mikrofon sesini 16kHz PCM16 ga aylantirish — yuqori sifat."""
        import numpy as np
        samples = indata[:, 0] if indata.ndim > 1 else indata.flatten()
        samples = samples.astype(np.float32)
        n_in = len(samples)
        if n_in < 2:
            return b""
        if src_sr != 16000:
            try:
                from scipy.signal import resample_poly
                from math import gcd
                g = gcd(src_sr, 16000)
                samples = resample_poly(samples, 16000 // g, src_sr // g,
                                        padtype='line').astype(np.float32)
            except Exception:
                n_out = int(n_in * 16000 / src_sr)
                samples = np.interp(
                    np.linspace(0, n_in - 1, n_out),
                    np.arange(n_in), samples
                ).astype(np.float32)
        # Clipping oldini olish va int16 ga o'tkazish
        samples = np.clip(samples * 32768.0, -32700, 32700)
        return samples.astype(np.int16).tobytes()

    @staticmethod
    def _find_mic() -> tuple:
        SKIP = ["стерео", "stereo mix", "loopback", "what u hear",
                "wave out", "output mix", "digital output"]
        HOST_PREF = {"Windows DirectSound": 0, "MME": 1,
                     "Windows WDM-KS": 2, "Windows WASAPI": 3}
        candidates = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] < 1:
                continue
            name_l = d["name"].lower()
            if any(kw in name_l for kw in SKIP):
                continue
            try:
                ha = sd.query_hostapis(d["hostapi"]).get("name", "")
            except Exception:
                ha = ""
            pref = HOST_PREF.get(ha, 10)
            native_sr = int(d["default_samplerate"])
            candidates.append((pref, i, native_sr, d["name"]))
        candidates.sort(key=lambda x: x[0])
        for pref, i, native_sr, name in candidates:
            for try_sr in [native_sr, 44100, 48000, 16000]:
                try:
                    sd.check_input_settings(
                        device=i, samplerate=try_sr, channels=1, dtype="int16")
                    return i, try_sr
                except Exception:
                    continue
        return None, 16000

    @staticmethod
    def _find_speaker(receive_sr: int) -> tuple:
        # 1) Avval Windows STANDART chiqish qurilmasi — foydalanuvchi qaysi
        # qurilmani tanlagan bo'lsa (kolonka/monitor), ovoz o'sha yerga chiqadi.
        # DirectSound "Первичный драйвер" ba'zi tizimlarda jim qolib ketadi,
        # qurilma indekslari esa USB qayta ulanishida o'zgaradi — shuning uchun
        # standart qurilma har ishga tushishda yangidan so'raladi.
        try:
            default_out = sd.default.device[1]
            if default_out is not None and default_out >= 0:
                native_sr = int(sd.query_devices(default_out)["default_samplerate"])
                for try_sr in [receive_sr, native_sr, 48000, 44100]:
                    try:
                        sd.check_output_settings(
                            device=default_out, samplerate=try_sr,
                            channels=1, dtype="int16")
                        return default_out, try_sr
                    except Exception:
                        continue
        except Exception:
            logging.getLogger(__name__).warning(
                "Standart chiqish qurilmasini aniqlab bo'lmadi", exc_info=True)

        # 2) Zaxira: eski qidiruv mantiqi
        HOST_PREF = {"Windows DirectSound": 0, "MME": 1,
                     "Windows WDM-KS": 2, "Windows WASAPI": 3}
        candidates = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] < 1:
                continue
            name_l = d["name"].lower()
            if any(kw in name_l for kw in ["spdif", "digital output", "hdmi"]):
                continue
            try:
                ha = sd.query_hostapis(d["hostapi"]).get("name", "")
            except Exception:
                ha = ""
            pref = HOST_PREF.get(ha, 10)
            native_sr = int(d["default_samplerate"])
            candidates.append((pref, i, native_sr, d["name"]))
        candidates.sort(key=lambda x: x[0])
        for pref, i, native_sr, name in candidates:
            try:
                sd.check_output_settings(
                    device=i, samplerate=receive_sr, channels=1, dtype="int16")
                return i, receive_sr
            except Exception:
                continue
        for pref, i, native_sr, name in candidates:
            for try_sr in [native_sr, 48000, 44100]:
                try:
                    sd.check_output_settings(
                        device=i, samplerate=try_sr, channels=1, dtype="int16")
                    return i, try_sr
                except Exception:
                    continue
        return None, receive_sr

    @staticmethod
    def _upsample(data: bytes, src_sr: int, dst_sr: int) -> bytes:
        """AI audio chiqishini speaker sample rate ga aylantirish — yuqori sifat."""
        if src_sr == dst_sr:
            return data
        import numpy as np
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        n_in = len(samples)
        if n_in == 0:
            return data
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(src_sr, dst_sr)
            resampled = resample_poly(samples, dst_sr // g, src_sr // g,
                                      padtype='line').astype(np.float32)
        except ImportError:
            n_out = int(n_in * dst_sr / src_sr)
            resampled = np.interp(
                np.linspace(0, n_in - 1, n_out),
                np.arange(n_in), samples
            ).astype(np.float32)
        # Yumshoq clipping — speaker distorsiyasini oldini olish
        resampled = np.clip(resampled, -32700, 32700)
        return resampled.astype(np.int16).tobytes()

    async def _listen_audio(self):
        loop = asyncio.get_event_loop()
        mic_dev, mic_sr = self._find_mic()
        if mic_dev is None:
            print("[MIC] Mikrofon topilmadi — TEXT MODE")
            self.ui.write_log("Mikrofon topilmadi!")
            while True:
                await asyncio.sleep(1)

        dev_name = sd.query_devices(mic_dev)["name"]
        print(f"[MIC] [{mic_dev}] {dev_name} @ {mic_sr}Hz")
        self.ui.write_log(f"Mic: {dev_name[:35]} @ {mic_sr}Hz")
        state = {"sr": mic_sr}

        _mute_until = [0.0]
        import time as _time

        def callback(indata, frames, time_info, status):
            # AI gapirayotganda mic o'chiq + gap tugagach ECHO_GUARD_AFTER_SPEECH
            # davomida o'chiq — kolonkadan chiqqan AI ovozini (va xona aks-sadosini)
            # mikrofon eshitib, SIMMED o'ziga javob bermasligi uchun.
            if self._is_speaking:
                _mute_until[0] = _time.monotonic() + ECHO_GUARD_AFTER_SPEECH
            if self.ui.muted or _time.monotonic() < _mute_until[0]:
                return
            data = SimmedAgent._resample_to_16k(indata, state["sr"])
            if not data:
                return
            def _put():
                try:
                    if self.out_queue is None:
                        return
                    self.out_queue.put_nowait(
                        {"data": data, "mime_type": "audio/pcm;rate=16000"})
                except Exception:
                    logging.getLogger(__name__).warning("Jim o'tilgan xato", exc_info=True)
            loop.call_soon_threadsafe(_put)

        # 10ms block — past kechikish, silliq nutq aniqlash
        blocksize = max(160, int(mic_sr * 0.010))
        try:
            with sd.InputStream(
                device=mic_dev,
                samplerate=mic_sr,
                channels=1,
                dtype="int16",
                blocksize=blocksize,
                latency="low",
                callback=callback,
            ):
                print("[MIC] Stream ochiq, tinglayapman...")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[MIC] Xato: {e}")
            raise

    def _clear_audio_queue(self):
        """AI audio queue ni darhol tozalash — foydalanuvchi gapirganda to'xtatish."""
        cleared = 0
        while not self.audio_in_queue.empty():
            try:
                self.audio_in_queue.get_nowait()
                cleared += 1
            except Exception:
                break
        if cleared:
            self.set_speaking(False)

    async def _receive_audio(self):
        print("[SIMMED] 👂 Recv started")
        out_buf, in_buf, in_raw = [], [], []
        user_speaking = False

        try:
            while True:
                async for response in self.session.receive():

                    # --- AUDIO DATA ---
                    if response.data:
                        if self._voice_trigger_fired:
                            continue
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    # --- SERVER CONTENT ---
                    if response.server_content:
                        sc = response.server_content

                        # SERVER tomonidan to'xtatildi (barge-in)
                        if getattr(sc, "interrupted", False):
                            self.ui.write_log("⚡ Foydalanuvchi gapi — AI to'xtatildi")
                            self._clear_audio_queue()
                            user_speaking = True

                        # AI javobi transkripsiyasi
                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        # Foydalanuvchi gapi — bo'lak-bo'lak keladi
                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            print(f"[TRANSCRIPT] '{txt}' | restarting={self._restarting} fired={self._voice_trigger_fired}")
                            if txt and not self._voice_trigger_fired:
                                if not user_speaking:
                                    user_speaking = True

                                in_buf.append(txt)
                                in_raw.append(txt.strip())

                                with_spaces = " ".join(in_buf).lower()
                                merged = "".join(in_raw).lower()

                                if self._check_voice_trigger(merged) or \
                                        self._check_voice_trigger(with_spaces):
                                    self.ui.write_log("⚡ Trigger aniqlandi — AI to'xtatildi!")
                                    self._clear_audio_queue()

                        # --- TURN COMPLETE ---
                        if sc.turn_complete:
                            user_speaking = False
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self.ui.save_chat("you", full_in)
                                if not self._voice_trigger_fired:
                                    self._check_voice_trigger(full_in)

                            in_buf.clear()
                            in_raw.clear()
                            self._voice_trigger_fired = False

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"simmed: {full_out}")
                                self.ui.save_chat("ai", full_out)
                            out_buf.clear()

                    # --- TOOL CALL ---
                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[SIMMED] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[SIMMED] [X] Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        import numpy as np
        import threading as _threading

        spk_dev, spk_sr = self._find_speaker(RECEIVE_SAMPLE_RATE)
        dev_name = sd.query_devices(spk_dev)["name"] if spk_dev is not None else "default"
        print(f"[SPK] [{spk_dev}] {dev_name} @ {spk_sr}Hz")
        logging.getLogger(__name__).info(
            "Tanlangan dinamik: [%s] %s @ %sHz", spk_dev, dev_name, spk_sr)
        self.ui.write_log(f"Speaker: {dev_name[:35]} @ {spk_sr}Hz")

        need_resample = (spk_sr != RECEIVE_SAMPLE_RATE)

        _buf = bytearray()
        _buf_lock = _threading.Lock()
        # 70ms jitter-buffer — panel bilan bir xil, tezroq start (eski: 0.12)
        PREBUF = int(spk_sr * 0.07) * 2
        _ready = _threading.Event()

        def _callback(outdata, frames, time_info, status):
            needed = frames * 2
            with _buf_lock:
                have = len(_buf)
                # Boshlanishda PREBUF to'lguncha kutamiz (silliq start)
                if not _ready.is_set() and have >= PREBUF:
                    _ready.set()
                if _ready.is_set() and have >= needed:
                    chunk = bytes(_buf[:needed])
                    del _buf[:needed]
                elif _ready.is_set() and have > 0:
                    # Qisman underrun — bor narsani ber + sukut to'ldir.
                    # _ready.clear() QILMAYMIZ — gap o'rtasida qayta-prebuffer
                    # qilib uzib qo'ymaslik uchun (silliqlikning kaliti).
                    chunk = bytes(_buf) + b'\x00' * (needed - have)
                    del _buf[:have]
                else:
                    chunk = b'\x00' * needed
            outdata[:] = np.frombuffer(chunk, dtype=np.int16).reshape(-1, 1)

        stream = None
        for dev, sr in [(spk_dev, spk_sr), (None, RECEIVE_SAMPLE_RATE)]:
            try:
                # blocksize=1024, latency='low' — yuqori sifat, past kechikish
                stream = sd.OutputStream(
                    device=dev, samplerate=sr, channels=1, dtype="int16",
                    blocksize=512, latency="low", callback=_callback,
                )
                stream.start()
                spk_sr = sr
                need_resample = (sr != RECEIVE_SAMPLE_RATE)
                PREBUF = int(sr * 0.07) * 2
                print(f"[SPK] Stream boshlandi @ {sr}Hz")
                break
            except Exception as e:
                print(f"[SPK] Ochilmadi (dev={dev}): {e}")

        if stream is None:
            print("[SPK] Speaker ochilmadi")
            while True:
                try: self.audio_in_queue.get_nowait()
                except Exception: pass
                await asyncio.sleep(0.1)
            return

        _finish_ticks = 0
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(), timeout=0.08)
                except asyncio.TimeoutError:
                    with _buf_lock:
                        buf_empty = len(_buf) == 0
                    if buf_empty:
                        _finish_ticks += 1
                        # 240ms sukutdan keyin "gapirdi tugadi" (panel bilan bir xil; eski: 5)
                        if _finish_ticks >= 3 and self._is_speaking:
                            self.set_speaking(False)
                            # Gap tugadi — keyingi gap silliq boshlanishi uchun
                            # prebuffer'ni qayta yoqamiz
                            _ready.clear()
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                    else:
                        _finish_ticks = 0
                    continue

                _finish_ticks = 0
                if not self._is_speaking:
                    self.set_speaking(True)

                out = self._upsample(chunk, RECEIVE_SAMPLE_RATE, spk_sr) if need_resample else chunk
                with _buf_lock:
                    _buf.extend(out)

        except Exception as e:
            print(f"[SPK] Xato: {e}")
            raise
        finally:
            self.set_speaking(False)
            if stream:
                try: stream.stop(); stream.close()
                except Exception: pass

    # ==========================================
    # MAIN RUN LOOP
    # ==========================================

    async def run(self):
        def _make_client():
            return genai.Client(
                api_key=_get_api_key(),
                http_options={"api_version": "v1beta"}
            )
        client = _make_client()
        _active_key = [_get_api_key()]
        _n_keys = len(_load_api_keys())
        if _n_keys > 1:
            print(f"[SIMMED] 🔑 {_n_keys} ta API kalit (avtomatik zaxira yoqilgan)")
            self.ui.write_log(f"SYS: {_n_keys} ta API kalit — limit tugasa avtomatik almashadi")

        # Model fallback ro'yxati
        FALLBACK_MODELS = [
            "models/gemini-2.5-flash-native-audio-preview-12-2025",
            "models/gemini-2.5-flash-preview-native-audio-dialog",
            "models/gemini-3.1-flash-live-preview",
        ]
        _model_idx = [0]

        def _current_model():
            return FALLBACK_MODELS[_model_idx[0] % len(FALLBACK_MODELS)]

        # Reconnect backoff: birinchi urinish tez (0.2s), ketma-ket xatolarda
        # asta oshadi (maks 5s) — tarmoq uzoq uzilganda zich sikl bo'lmaydi.
        # Muvaffaqiyatli ulanishda yana 0.2s ga qaytadi.
        _retry_delay = [0.2]

        while True:
            try:
                # Kalit almashgan bo'lsa (quota) — clientni yangi kalit bilan qayta yarat
                if _get_api_key() != _active_key[0]:
                    client = _make_client()
                    _active_key[0] = _get_api_key()

                model_name = _current_model()
                print(f"[SIMMED] [~] Connecting... Model: {model_name}")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=model_name, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session = session
                    self._loop = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue = asyncio.Queue()  # Cheksiz — hech qanday audio tushmasin
                    self._turn_done_event = asyncio.Event()

                    print("[SIMMED] [OK] Connected.")
                    _retry_delay[0] = 0.2   # muvaffaqiyat — backoff qayta boshlanadi
                    self.ui.set_state("LISTENING")
                    self.ui.write_log(
                        f"SYS: SIMMED online | Voice: {self.current_voice} ({self.current_gender})")

                    # Ovoz o'zgarganidan keyin salomlashish
                    if self._pending_welcome and self._welcome_text:
                        await session.send_client_content(
                            turns={"parts": [{"text": self._welcome_text}]},
                            turn_complete=True
                        )
                        self._pending_welcome = False
                        self._welcome_text = ""

                    self._restarting = False
                    self._voice_trigger_fired = False

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                if "RESTART_VOICE" in str(e):
                    print("[SIMMED] [>>] Restarting with new voice...")
                    self.ui.write_log("🔄 Ovoz o'zgartirilmoqda...")
                    continue
                err_str = str(e)
                low = err_str.lower()
                print(f"[SIMMED] [!] {err_str}")
                # Quota/limit yoki kalit xatosi → zaxira kalitga avtomatik o'tish
                _quota_signs = ["resource_exhausted", "quota", "rate limit", "429",
                                "exceeded", "permission_denied", "api key not valid",
                                "api_key_invalid", "401", "403", "unauthenticated"]
                if any(s in low for s in _quota_signs):
                    if len(_load_api_keys()) > 1:
                        _rotate_api_key()
                        client = _make_client()
                        _active_key[0] = _get_api_key()
                        kn = _API_KEY_IDX[0] + 1
                        print(f"[SIMMED] 🔑 Kalit limiti/xatosi — zaxira kalit #{kn} ga o'tildi")
                        self.ui.write_log(f"🔑 Kalit limiti tugadi — zaxira kalit #{kn} ishga tushdi")
                    else:
                        print("[SIMMED] ⚠️ Kalit limiti, lekin zaxira kalit yo'q")
                        self.ui.write_log("⚠️ Kalit limiti tugadi — zaxira kalit qo'shing")
                # Grounding (google_search) config xatosi → grounding'siz qayta ulanish
                elif self._grounding_enabled and (
                    "google_search" in low or "grounding" in low or
                    ("tool" in low and ("invalid" in low or "not supported" in low
                                        or "unsupported" in low or "400" in err_str))
                ):
                    self._grounding_enabled = False
                    print("[SIMMED] ⚠️ Model grounding'ni qo'llamadi — grounding'siz qayta ulanmoqda")
                    self.ui.write_log("⚠️ Grounding qo'llab-quvvatlanmadi — o'chirildi, qayta ulanmoqda")
                # Model topilmasa keyingi modelga o'tish
                elif "not found" in low or "404" in err_str or "not supported" in low:
                    _model_idx[0] += 1
                    next_m = _current_model()
                    print(f"[SIMMED] [>>] Model o'zgartirilmoqda: {next_m}")
                    self.ui.write_log(f"⚠️ Model xatosi — {next_m} sinab ko'rilmoqda...")
                else:
                    traceback.print_exc()

            self.set_speaking(False)
            self._clear_audio_queue()
            self.ui.set_state("THINKING")
            print(f"[SIMMED] [>>] Reconnecting in {_retry_delay[0]:.1f}s...")
            await asyncio.sleep(_retry_delay[0])
            _retry_delay[0] = min(_retry_delay[0] * 2, 5.0)


# ====================== MAIN ======================
def main():
    ui = SimmedUI("face.png")

    def runner():
        ui.wait_for_api_key()
        agent = SimmedAgent(ui)
        try:
            asyncio.run(agent.run())
        except KeyboardInterrupt:
            print("\n[X] Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()



