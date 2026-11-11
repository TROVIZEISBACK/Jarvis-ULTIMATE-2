"""JARVIS modes.

Two independent settings:
- focus: standard, math or science. Changes how JARVIS thinks and presents answers.
- power: normal or full. "Jarvis, full power" raises the reasoning effort to the maximum.

Mode instructions travel with each request (a short block in front of the user's words),
so switching never rewrites the conversation or the frozen system prompt.
"""
import json
import re

from . import config

EFFORTS = ["low", "medium", "high", "xhigh", "max"]

FOCUS = {
    "standard": {
        "label": "STANDARD",
        "min_effort": None,
        "spoken": "Standard mode, {owner}.",
        "instructions": "",
    },
    "math": {
        "label": "MATHEMATICS",
        "min_effort": "high",
        "spoken": "Mathematics mode engaged, {owner}. Give me anything from arithmetic to proofs.",
        "instructions": """\
MATH MODE is on. You are a world-class mathematician and a patient teacher.
- Solve carefully and completely: arithmetic, algebra, geometry, trigonometry, calculus, \
linear algebra, probability, statistics, number theory, combinatorics, discrete maths, proofs, \
word problems and competition problems.
- Verify every non-trivial result with the `compute` tool (Python with SymPy, NumPy, SciPy, \
mpmath and fractions available): check arithmetic, solve equations symbolically, test identities, \
evaluate integrals and sums, and run quick numeric sanity checks. Never report an unverified \
calculation when a check is possible.
- Put the full worked solution on the HUD with `hud` (action "panel"): numbered steps, each with \
a one-line reason, and the final answer last in bold. Write maths in LaTeX: $...$ inline and \
$$...$$ for display equations. The HUD renders it.
- Out loud, say only the answer and the key idea in plain words ("x equals three; the trick is \
to factor the quadratic"). Never read LaTeX or symbols aloud.
- If the problem is ambiguous, state the interpretation you used. If asked to teach, explain \
the method so it transfers to similar problems; offer a hint before the full solution when the \
user is clearly practising.
- For a photo or a problem held up to the camera, use `look_through_camera` to read it first.""",
    },
    "science": {
        "label": "SCIENCE",
        "min_effort": "high",
        "spoken": "Science mode engaged, {owner}. Show me a project, or name one.",
        "instructions": """\
SCIENCE MODE is on. You are an expert science communicator and lab mentor across physics, \
chemistry, biology, earth and space science, electronics, engineering and computer science.
- When the user shows or names a project or experiment (a volcano, a potato battery, a \
circuit, a robot, a water filter, a solar oven, a model rocket, a science-fair board), use \
`look_through_camera` if they're showing it, and web search when you need specifics. Then \
explain it in this order on a HUD panel:
  **What it is**: one or two lines.
  **How it works**: the scientific principle, with the key equation or reaction if there is one.
  **How it was made**: materials, then numbered build steps, as if the user is making it.
  **The science behind it**: the concepts it demonstrates, in plain words.
  **Safety**: real hazards and how to avoid them (only what genuinely applies).
  **Take it further**: one or two ways to extend or improve it, and for a science fair, a \
hypothesis with the independent, dependent and controlled variables.
- Match the depth to the person: simple for a young student, rigorous for an advanced one. \
Use analogies.
- Check facts and numbers; use `compute` for any calculation. Write formulas and reactions in \
LaTeX ($...$); the HUD renders them.
- Out loud, give a short, vivid summary of what it is and the big idea. The detail goes on screen.
- Refuse to help make anything genuinely dangerous (explosives, toxic gases, weapons), and \
explain the safe alternative instead.""",
    },
}

# Math and science are for learning: no Iron Man combat flavour there. Standard mode keeps it,
# at normal or full power.
CALM_TONE = """\
TONE: this is a learning mode. Never use military, combat or tactical language: no attacking or \
attacks, tactics or tactical reads, threats or threat assessments, hostiles, targets or targeting, \
missions, weapons systems, defences or battle talk, not even as a joke or as Iron Man flavour. \
That holds inside protocols and camera scans too. If one asks for a "tactical" assessment, \
give a calm, friendly, factual description instead. Speak like a brilliant, warm teacher."""

POWER = {
    "normal": {"label": "NORMAL", "instructions": ""},
    "full": {
        "label": "FULL POWER",
        "spoken": "Full power, {owner}. All systems at maximum.",
        "instructions": """\
FULL POWER is on: the user wants your very best work, regardless of time. Think deeply, \
consider alternatives, double-check facts and calculations with your tools, and be thorough \
and precise. Prefer depth over speed; still keep spoken replies short and put the detail on the HUD.""",
    },
}

# Spoken / typed switches, matched before a request reaches the brain.
SWITCHES = [
    (re.compile(r"\b(full power|maximum power|max power|all power|overdrive)\b", re.I), ("power", "full")),
    (re.compile(r"\b(power down|normal power|stand ?down from full power|reduce power|exit full power)\b", re.I), ("power", "normal")),
    (re.compile(r"\b(math|maths|mathematics) mode\b", re.I), ("focus", "math")),
    (re.compile(r"\bscience mode\b", re.I), ("focus", "science")),
    (re.compile(r"\b(standard|normal|default|regular) mode\b|\bexit (math|maths|science) mode\b", re.I), ("focus", "standard")),
]


def parse_switches(text: str) -> tuple[list[tuple[str, str]], str]:
    """Find mode switches in an utterance. Returns (switches, the rest of the request)."""
    found, rest = [], text
    for rx, switch in SWITCHES:
        if rx.search(rest):
            found.append(switch)
            rest = rx.sub(" ", rest)
    rest = re.sub(r"^(\W|\b(jarvis|activate|engage|enable|go|back|switch|turn|on|to|into|in|please|mode|and|then|now|enter)\b)+", "", rest.strip(), flags=re.I)
    return found, rest.strip(" ,.!?")


class ModeState:
    def __init__(self):
        self.path = config.DATA_DIR / "mode.json"
        self.focus, self.power = "standard", "normal"
        try:
            saved = json.loads(self.path.read_text())
            if saved.get("focus") in FOCUS:
                self.focus = saved["focus"]
            if saved.get("power") in POWER:
                self.power = saved["power"]
        except Exception:
            pass

    def set(self, kind: str, value: str) -> bool:
        """Returns True if something changed."""
        table = FOCUS if kind == "focus" else POWER
        if value not in table:
            raise ValueError(f"Unknown {kind} {value!r}")
        if getattr(self, kind) == value:
            return False
        setattr(self, kind, value)
        try:
            self.path.write_text(json.dumps({"focus": self.focus, "power": self.power}))
        except Exception:
            pass
        return True

    @property
    def effort(self) -> str:
        if self.power == "full":
            return "max"
        base = config.EFFORT if config.EFFORT in EFFORTS else "medium"
        floor = FOCUS[self.focus]["min_effort"]
        if floor and EFFORTS.index(floor) > EFFORTS.index(base):
            return floor
        return base

    def preamble(self) -> str:
        """Instructions to put in front of the user's request (empty in standard mode)."""
        parts = [FOCUS[self.focus]["instructions"], CALM_TONE if self.focus in ("math", "science") else "",
                 POWER[self.power]["instructions"]]
        body = "\n\n".join(p for p in parts if p)
        return f"<jarvis_mode>\n{body}\n</jarvis_mode>\n\n" if body else ""

    def describe(self) -> dict:
        return {"focus": self.focus, "power": self.power, "effort": self.effort,
                "focus_label": FOCUS[self.focus]["label"], "power_label": POWER[self.power]["label"]}
