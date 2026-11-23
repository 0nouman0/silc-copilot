"""Greetings, thanks and acknowledgements: recognised with fuzzy matching, answered in code with no database call, never the same line twice in a row."""
from __future__ import annotations

import difflib
import re

_GREET = {"hello", "hi", "hey", "hii", "hiii", "helo", "hola", "namaste", "greetings", "yo", "sup", "howdy", "hullo", "hallo", "hellow", "hlo", "heya", "heyy", "heyyy", "morning", "afternoon", "evening"}
_GREET_FILLER = {"there", "again", "team", "sir", "madam", "mam", "christ", "university", "bot", "assistant", "everyone", "all", "good", "day", "folks", "guys"}
_GREET_FUZZY = ["hello", "howdy", "greetings", "namaste", "morning", "afternoon", "evening"]
_THANKS = {"thanks", "thank", "thx", "ty", "tq", "thankyou", "cheers", "appreciate", "appreciated"}
_THANKS_FILLER = {"you", "u", "so", "much", "a", "lot", "very", "great", "ok", "okay", "again", "the", "help", "for", "that", "this", "many", "really"}
_ACK = {"ok", "okay", "k", "kk", "cool", "fine", "alright", "great", "nice", "good", "hmm", "hmmm", "oh", "wow", "understood", "noted", "perfect", "awesome"}
_ACK_FILLER = {"got", "it", "i", "see", "thats", "that's", "that", "is", "so", "then", "cool", "ok", "okay", "great", "nice", "good", "will", "do"}
_BYE = {"bye", "goodbye", "cya", "later", "nothing", "done"}

GREETING_SEQUENCE = [
    "Hello! Welcome to CHRIST University. How can I assist you today?",
    "Hi again! What would you like to explore today?",
    "Hey! How can I help you with your university admission queries?",
    "Hello again. Tell me a course, a degree like B.Tech or BSc, or a question, and I'll look it up.",
    "I'm here whenever you're ready. What would you like to know?",
]
TIME_GREETING = re.compile(r"\bgood\s+(morning|afternoon|evening)\b", re.I)
THANKS = ["You're welcome! Anything else you'd like to check?", "Happy to help. Want me to look at another programme?", "Glad that helped. Ask me anything else about CHRIST admissions.",
          "Anytime! I can also check eligibility or curriculum for any programme."]
ACKS = ["Great. What would you like to look at next?", "Alright. I can show programmes, eligibility or the curriculum whenever you're ready.",
        "Sure. Tell me what else you'd like to know.", "Okay. Just say the word if you want details on anything else."]
BYES = ["Goodbye, and best wishes with your application! Come back anytime.", "Thanks for stopping by. Good luck with your admission plans!"]


def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower().replace("’", "'"))


def _canon_greeting_word(w: str) -> str:
    """'helloo', 'hallo' -> a greeting word. Only longer words are fuzzy-matched, so 'help' is not 'hello'."""
    if w in _GREET or len(w) < 5:
        return w
    m = difflib.get_close_matches(w, _GREET_FUZZY, n=1, cutoff=0.82)
    return m[0] if m else w


def smalltalk_kind(text: str) -> str | None:
    w = words(text)
    if not w or len(w) > 6:
        return None
    s = {_canon_greeting_word(x) for x in w}
    if s & _GREET and s <= _GREET | _GREET_FILLER:
        return "greeting"
    s = set(w)
    if s & _THANKS and s <= _THANKS | _THANKS_FILLER:
        return "thanks"
    if s & _BYE and s <= _BYE | {"for", "now", "thats", "that's", "all", "thanks", "thank", "you", "i", "am", "see", "else", "no", "more", "bye"}:
        return "bye"
    if s & _ACK and s <= _ACK | _ACK_FILLER:
        return "ack"
    if (s & {"no", "nope", "nah"}) and s <= {"no", "nope", "nah", "thanks", "thank", "you", "that's", "all", "thats", "i", "am", "good", "fine", "its", "ok", "okay"}:
        return "bye"
    return None


def assistant_texts(history: list[dict]) -> list[str]:
    return [m["content"] for m in history if m["role"] == "assistant"]


def pick(variants: list[str], history: list[dict]) -> str:
    """The first variant not already said in this conversation; if all were used, rotate (never the same line twice in a row)."""
    said = assistant_texts(history)
    for v in variants:
        if not any(v[:40] in s for s in said):
            return v
    last = said[-1] if said else ""
    rotated = [v for v in variants if v[:40] not in last] or variants
    return rotated[len(said) % len(rotated)]


def greeting_reply(text: str, history: list[dict]) -> str:
    """Deterministic, not random: the Nth greeting in THIS conversation gets the Nth line, 'Good morning' is answered as such, and the same line is
    never used twice in a row. The opening welcome message is not counted as a greeting reply."""
    said = assistant_texts(history)
    last = said[-1] if said else ""
    m = TIME_GREETING.search(text)
    if m:
        t = f"Good {m.group(1).lower()}! What information are you looking for?"
        if t not in last:
            return t
    used = sum(1 for s in said if any(s.startswith(v) for v in GREETING_SEQUENCE) or re.match(r"Good (morning|afternoon|evening)! What information", s))
    for k in range(len(GREETING_SEQUENCE)):
        cand = GREETING_SEQUENCE[(used + k) % len(GREETING_SEQUENCE)]
        if cand not in last:
            return cand
    return GREETING_SEQUENCE[used % len(GREETING_SEQUENCE)]
