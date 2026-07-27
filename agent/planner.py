import json
import re
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


PLANNER_PROMPT = """
You are the planning module of ADTI SUMLYATSIYA  AI, a personal autonomous assistant system.

Your job is to break any user goal into a structured, executable step-by-step plan using ONLY the tools listed below.

========================================================
CORE IDENTITY
========================================================
System: ADTI SUMLYATSIYA  AI Planner Engine v2
Role: Deterministic task decomposition engine
Output type: STRICT JSON ONLY

You are NOT a chatbot.
You are NOT a coder.
You are NOT an executor.
You ONLY create execution plans.

========================================================
ABSOLUTE RULES
========================================================
- NEVER use generated_code or scripts.
- NEVER assume tools not listed below.
- NEVER reference previous step outputs.
- EACH step must be independent.
- MAXIMUM 6 steps per plan.
- MINIMUM steps preferred (optimize aggressively).
- If task is unclear → use web_search or screen_process first.
- No hallucinated parameters allowed.

========================================================
TIME / CONTEXT RULES
========================================================
If user says:
- "today" → resolve to current date
- "tomorrow" → current_date + 1 day
- "now+30min" → current_time + 30 minutes

(Planner must output final resolved values, NOT placeholders)

========================================================
AVAILABLE TOOLS
========================================================

open_app
  app_name: string (required)

web_search
  query: string (required)
  mode: "search" | "compare"
  items: list[string]
  aspect: string

game_updater
  action: "update" | "install" | "list" | "download_status" | "schedule"
  platform: "steam" | "epic" | "both"
  game_name: string
  app_id: string
  shutdown_when_done: boolean

browser_control
  action: "go_to" | "search" | "click" | "type" | "scroll" | "get_text" | "press" | "close"
  url: string
  query: string
  text: string
  direction: "up" | "down"

file_controller
  action: "write" | "create_file" | "read" | "list" | "delete" | "move" | "copy" | "find" | "disk_usage"
  path: string   # use "desktop" for Desktop folder
  name: string
  content: string

computer_settings
  action: string
  description: string
  value: string

computer_control
  action: "type" | "click" | "hotkey" | "press" | "scroll" | "screenshot" | "screen_find" | "screen_click"
  text: string
  x: int
  y: int
  keys: string
  key: string
  direction: "up" | "down"
  description: string

screen_process
  text: string
  angle: "screen" | "camera"

send_message
  receiver: string
  message_text: string
  platform: string

reminder
  date: string (YYYY-MM-DD)
  time: string (HH:MM)
  message: string

desktop_control
  action: "wallpaper" | "organize" | "clean" | "list" | "task"
  path: string
  task: string

youtube_video
  action: "play" | "summarize" | "trending"
  query: string

weather_report
  city: string

flight_finder
  origin: string
  destination: string
  date: string

code_helper
  action: "write" | "edit" | "run" | "explain"
  description: string
  language: string
  output_path: string
  file_path: string

dev_agent
  description: string
  language: string

========================================================
PLANNING STRATEGY (IMPORTANT)
========================================================
- Prefer automation tools over manual steps
- Combine web_search → file_controller when saving knowledge
- Use screen_process for debugging or unknown system state
- Use browser_control only when web_search is insufficient
- If goal includes "fix / error / bug" → ALWAYS start with screen_process
- If goal includes "research" → ALWAYS start with web_search
- If goal includes "save / document" → ALWAYS end with file_controller

========================================================
OUTPUT FORMAT (STRICT)
========================================================
Return ONLY valid JSON. No markdown. No explanation.

{
  "goal": "string",
  "steps": [
    {
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {},
      "critical": true,
      "reason": "why this tool is used"
    }
  ]
}

========================================================
EXAMPLES
========================================================

Goal: "research AI and save notes"
Steps:
web_search | query: "artificial intelligence overview trends"
web_search | query: "AI applications future"
file_controller | action: write, path: desktop, name: ai.txt, content: "AI NOTES"

Goal: "fix my code error"
Steps:
screen_process | text: "analyze error"
code_helper | action: explain, file_path: "main.py"

Goal: "install PUBG"
Steps:
game_updater | action: install, platform: steam, game_name: "PUBG"
"""


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def create_plan(goal: str, context: str = "") -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash-lite",
        system_instruction=PLANNER_PROMPT
    )

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext: {context}"

    try:
        response = model.generate_content(user_input)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure")

        for step in plan["steps"]:
            if step.get("tool") in ("generated_code",):
                print(f"[Planner] ⚠️ generated_code detected in step {step.get('step')} — replacing with web_search")
                desc = step.get("description", goal)
                step["tool"] = "web_search"
                step["parameters"] = {"query": desc[:200]}

        print(f"[Planner] ✅ Plan: {len(plan['steps'])} steps")
        for s in plan["steps"]:
            print(f"  Step {s['step']}: [{s['tool']}] {s['description']}")

        return plan

    except json.JSONDecodeError as e:
        print(f"[Planner] ⚠️ JSON parse failed: {e}")
        return _fallback_plan(goal)
    except Exception as e:
        print(f"[Planner] ⚠️ Planning failed: {e}")
        return _fallback_plan(goal)


def _fallback_plan(goal: str) -> dict:
    print("[Planner] 🔄 Fallback plan")
    return {
        "goal": goal,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": goal},
                "critical": True
            }
        ]
    }


def replan(goal: str, completed_steps: list, failed_step: dict, error: str) -> dict:
    import google.generativeai as genai

    genai.configure(api_key=_get_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=PLANNER_PROMPT
    )

    completed_summary = "\n".join(
        f"  - Step {s['step']} ({s['tool']}): DONE" for s in completed_steps
    )

    prompt = f"""Goal: {goal}

Already completed:
{completed_summary if completed_summary else '  (none)'}

Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}
Error: {error}

Create a REVISED plan for the remaining work only. Do not repeat completed steps."""

    try:
        response = model.generate_content(prompt)
        text     = response.text.strip()
        text     = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        plan     = json.loads(text)

        for step in plan.get("steps", []):
            if step.get("tool") == "generated_code":
                step["tool"] = "web_search"
                step["parameters"] = {"query": step.get("description", goal)[:200]}

        print(f"[Planner] 🔄 Revised plan: {len(plan['steps'])} steps")
        return plan
    except Exception as e:
        print(f"[Planner] ⚠️ Replan failed: {e}")
        return _fallback_plan(goal)