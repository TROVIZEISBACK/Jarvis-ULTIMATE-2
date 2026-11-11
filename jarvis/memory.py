"""Long-term memory (successor to the original data.txt 'remember that')."""
import json
import time

from .config import DATA_DIR

PATH = DATA_DIR / "memory.json"


def load() -> list[dict]:
    try:
        return json.loads(PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add(fact: str) -> int:
    items = load()
    items.append({"fact": fact.strip(), "saved": time.strftime("%Y-%m-%d")})
    PATH.write_text(json.dumps(items, indent=2))
    return len(items)


def forget(query: str) -> int:
    items = load()
    q = query.lower()
    kept = [i for i in items if q not in i["fact"].lower()]
    PATH.write_text(json.dumps(kept, indent=2))
    return len(items) - len(kept)


def as_prompt() -> str:
    items = load()
    if not items:
        return "(nothing saved yet)"
    return "\n".join(f"- {i['fact']} (saved {i['saved']})" for i in items)


def load_protocols() -> dict:
    try:
        return json.loads((DATA_DIR / "protocols.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
