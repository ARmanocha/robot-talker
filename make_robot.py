#!/usr/bin/env python3
"""Talking robot for PowerPoint.

Type what the robot should say. It speaks the words with a macOS voice, moves
its mouth in sync, acts them out, and saves an MP4 plus a .pptx slide that
plays the video automatically.

Actions are picked from your words automatically. To choose one yourself, put
a tag in front of the words it goes with:

    [wave] [point] [thumbsup] [nod] [think] [celebrate] [talk]
    [pause]  short silence
    A tag with no words after it plays the action without speaking.

Examples:
    python3 make_robot.py "Hello everyone, welcome!"
    python3 make_robot.py "[wave] Hi team! [point] Look at these numbers. [celebrate] We did it!"
    python3 make_robot.py --voice Junior --bg 1E2761 "Hello everyone, welcome!"
    python3 make_robot.py -f script.txt
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FPS = 30
SR = 44100
SS = 2  # supersampling factor for smooth edges

ACTIONS = {
    "talk": "hand gestures while speaking (the default)",
    "wave": "waves hello or goodbye",
    "point": "points to the right, toward your slide content",
    "thumbsup": "thumbs up with happy eyes",
    "nod": "nods yes",
    "think": "scratches its head and looks up",
    "celebrate": "jumps with both arms up",
    "exit": "walks off the frame waving (use it last)",
}
ALIASES = {
    "thumbs": "thumbsup", "thumb": "thumbsup", "thumbup": "thumbsup", "like": "thumbsup",
    "hi": "wave", "hello": "wave", "bye": "wave", "yes": "nod", "agree": "nod",
    "party": "celebrate", "cheer": "celebrate", "jump": "celebrate", "hooray": "celebrate",
    "wonder": "think", "question": "think", "hmm": "think", "show": "point", "look": "point",
    "speak": "talk", "say": "talk",
}
# First match wins, so order is priority.
AUTO_RULES = [
    ("celebrate", r"\b(celebrat\w*|hooray|yay|woo+(hoo)?|we did it|let'?s go|congrat\w*|hura|udało (się|nam)|gratul\w*)\b"),
    ("wave", r"\b(hello|hi|hey|welcome|greetings|good (morning|afternoon|evening)|goodbye|bye|see you"
             r"|cześć|hej|witaj\w*|witam\w*|dzień dobry|do widzenia|pa,? pa)\b"),
    ("thumbsup", r"\b(thanks?|thank you|great|awesome|amazing|excellent|well done|good job|success\w*|proud"
                 r"|dzięk\w*|super|świetn\w*|brawo)\b"),
    ("think", r"\?|\b(think|wonder|idea|question|consider|imagine|what if|myśl\w*|zastanaw\w*|pytani\w*)\b"),
    ("point", r"\b(look|see|here|chart|graph|slide|screen|below|above|show\w*|notice|check out"
              r"|patrz\w*|spójrz\w*|tutaj|wykres\w*|slajd\w*)\b"),
    ("nod", r"\b(yes|agree|exactly|correct|absolutely|indeed|sure|tak|zgadza\w*|dokładnie)\b"),
]

LEAD, PRE, GAP, TAIL, BLEND = 0.5, 0.25, 0.35, 1.3, 0.35
ENTER, WALK_DIST, STRIDE = 1.8, 380, 110  # walk-in: seconds, robot units travelled, units per step cycle
PEEK_X, SNEAK, WALK_IN, EXIT = 200, 1.9, 1.2, 2.0  # sneak entrance: peek offset, peek time, walk-in time; exit time
L1, L2 = 46, 44  # upper arm / forearm length in robot units


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, k):
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


COL = {k: hex_rgb(v) for k, v in dict(
    body="#378ADD", dark="#185FA5", head="#85B7EB", face="#042C53", glow="#9FE1CB",
    leg="#5F5E5A", foot="#444441", hand="#B4B2A9", panel="#E6F1FB",
    red="#E24B4A", amber="#EF9F27", amber_lo="#FAC775", green="#1D9E75", shadow="#888780",
    bubble="#FFFFFF", bubble_line="#B4B2A9", ink="#2C2C2A", sub="#5F5E5A",
).items()}

# (file, face index for the spoken words, face index for the translation line)
ENG = {k: hex_rgb(v) for k, v in dict(
    skin="#F0C39B", skin_dark="#DCA67C", hair="#4A3426", hat="#F5C518", hat_dark="#D9A70C",
    shirt="#3D6FB6", shirt_dark="#2F5A99", vest="#F7861F", vest_dark="#D96F10", stripe="#ECECEC",
    pants="#2E3A55", boot="#5A3A22", belt="#3A2A1E", buckle="#C9B037", ink="#2C2C2A", mouth="#7E2F2A",
    white="#FFFFFF", board="#B07A43", paper="#FFFFFF", clip="#888780", cheek="#EBA58C",
).items()}

FONT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", 0, 0),
    ("/System/Library/Fonts/Avenir Next.ttc", 0, 6),  # Bold, Medium Italic
    ("/System/Library/Fonts/Helvetica.ttc", 1, 2),    # Bold, Oblique
]


# ---------------------------------------------------------------- script

@dataclass
class Segment:
    action: str
    text: str = ""          # spoken words
    subtitle: str = ""      # shown under the words in the bubble, not spoken (from {braces})
    pause: bool = False
    pause_len: float = 0.8  # exact silence between the words around it; 0 = no gap
    speed: str = ""         # "fast" or "slow"
    audio: np.ndarray | None = None
    t0: float = 0.0  # action window
    t1: float = 0.0
    s0: float = 0.0  # speech
    s1: float = 0.0
    words: list[str] = field(default_factory=list)
    word_starts: list[float] = field(default_factory=list)
    reveal: np.ndarray | None = None
    reveal_f0: int = 0


def auto_action(sentence: str) -> str:
    low = sentence.lower()
    for action, pattern in AUTO_RULES:
        if re.search(pattern, low):
            return action
    return "talk"


TAG_RE = r"\[([A-Za-z _-]+?)\s*(\d+(?:\.\d+)?)?\]"


SPEEDS = {"fast": 1.6, "slow": 0.75}


def make_segment(action: str, text: str, speed: str = "") -> Segment:
    subtitle = " ".join(" ".join(m.split()) for m in re.findall(r"\{([^}]*)\}", text))
    spoken = " ".join(re.sub(r"\{[^}]*\}", " ", text).split())
    return Segment(action, spoken, subtitle, speed=speed)


def parse_script(script: str) -> list[Segment]:
    segments: list[Segment] = []
    pending = None
    for tok in re.split(r"(\[[A-Za-z _-]+?(?:\s*\d+(?:\.\d+)?)?\])", script):
        m = re.fullmatch(TAG_RE, tok.strip())
        if m:
            words = re.sub(r"[_-]", " ", m.group(1).lower()).split()
            speed = next((w for w in words if w in SPEEDS), "")
            name = "".join(w for w in words if w not in SPEEDS) or "talk"
            name = ALIASES.get(name, name)
            if name == "pause":
                if pending:
                    segments.append(Segment(pending[0]))
                    pending = None
                segments.append(Segment("idle", pause=True, pause_len=float(m.group(2) or 0.8)))
                continue
            if name not in ACTIONS:
                sys.exit(f"Unknown action [{m.group(1)}]. Use one of: "
                         + ", ".join(f"[{a}]" for a in ACTIONS) + ", [pause] or [pause 5]")
            if pending:  # two tags in a row: the first one plays silently
                segments.append(Segment(pending[0]))
            pending = (name, speed)
            continue
        text = " ".join(tok.split())
        if not text:
            continue
        if pending:
            segments.append(make_segment(pending[0], text, pending[1]))
            pending = None
        elif "{" in text:
            segments.append(make_segment(auto_action(re.sub(r"\{[^}]*\}", " ", text)), text))
        else:
            for sentence in re.split(r"(?<=[.!?])\s+", text):
                if sentence:
                    segments.append(Segment(auto_action(sentence), sentence))
    if pending:
        segments.append(Segment(pending[0]))
    return [s for s in segments if s.text or s.pause or not s.subtitle]


# ---------------------------------------------------------------- audio

def squeeze_pauses(a: np.ndarray, keep: float = 0.04) -> np.ndarray:
    """Shortens every silence inside the speech to `keep` seconds (for fast, no-gap delivery)."""
    win = int(0.01 * SR)
    n = len(a) // win
    chunks = a[:n * win].reshape(n, win)
    loud = np.abs(chunks).max(axis=1) > 0.01
    out, run = [], 0
    for k in range(n):
        run = 0 if loud[k] else run + 1
        if run <= keep / 0.01:
            out.append(chunks[k])
    out.append(a[n * win:])
    return np.concatenate(out)


def synth(text: str, voice: str, rate: int, workdir: Path, idx: int, speed: str = "") -> np.ndarray:
    txt, aiff, wav = (workdir / f"seg{idx}.{ext}" for ext in ("txt", "aiff", "wav"))
    txt.write_text(text)
    rate = round(rate * SPEEDS.get(speed, 1.0))
    cmd = ["say", "-v", voice, "-r", str(rate), "-f", str(txt), "-o", str(aiff)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or not aiff.exists():
        sys.exit(f"The voice failed: {(r.stderr or r.stdout).strip()}  (run with --voices to see options)")
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(aiff), str(wav)],
                   check=True, capture_output=True)
    with wave.open(str(wav)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    loud = np.flatnonzero(np.abs(a) > 0.01)
    if len(loud):
        a = a[max(0, loud[0] - int(0.03 * SR)): loud[-1] + int(0.08 * SR)]
    return squeeze_pauses(a) if speed == "fast" else a


def build_timeline(segments: list[Segment], lead: float) -> float:
    """A [pause N] next to speech replaces the usual breath, so the silence is exactly N seconds."""
    t = lead
    for i, s in enumerate(segments):
        before_pause = i + 1 < len(segments) and segments[i + 1].pause
        after_pause = i > 0 and segments[i - 1].pause
        s.t0 = t
        if s.pause:
            s.t1 = t + s.pause_len
        elif s.audio is None:
            s.t1 = t + (EXIT if s.action == "exit" else 1.8)
        else:
            s.s0 = t + (0.0 if after_pause else PRE)
            s.s1 = s.s0 + len(s.audio) / SR
            s.t1 = s.s1 if before_pause else max(s.s1 + GAP, t + 1.2)
        t = s.t1
    return t + (0.4 if segments and segments[-1].action == "exit" else TAIL)


def mix_audio(segments, total):
    out = np.zeros(int(total * SR) + SR, np.float32)
    for s in segments:
        if s.audio is not None:
            i0 = int(s.s0 * SR)
            out[i0:i0 + len(s.audio)] += s.audio
    return np.clip(out, -1, 1)


def mouth_track(audio, nframes):
    hop = SR / FPS
    env = np.array([np.sqrt(np.mean(audio[int(f * hop):int((f + 1) * hop)] ** 2) + 1e-12)
                    for f in range(nframes)])
    voiced = env[env > 0.01]
    ref = np.percentile(voiced, 90) if len(voiced) else 1.0
    openness = np.clip(env / ref, 0, 1) ** 0.6
    openness[env < 0.006] = 0
    for i in range(1, nframes):
        openness[i] = max(openness[i], openness[i - 1] * 0.5)
    return env, openness


def prepare_reveal(segments, env):
    for s in segments:
        if s.audio is None:
            continue
        s.words = s.text.split()
        total = sum(len(w) + 1 for w in s.words)
        acc, starts = 0, []
        for w in s.words:
            starts.append(acc / total)
            acc += len(w) + 1
        s.word_starts = starts
        f0, f1 = int(s.s0 * FPS), int(math.ceil(s.s1 * FPS))
        voiced = (env[f0:f1] > 0.01).astype(float)
        s.reveal = np.cumsum(voiced) / max(voiced.sum(), 1)
        s.reveal_f0 = f0


def write_m4a(audio, workdir: Path) -> Path:
    wav, m4a = workdir / "mix.wav", workdir / "mix.m4a"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "160000", str(wav), str(m4a)],
                   check=True, capture_output=True)
    return m4a


# ---------------------------------------------------------------- drawing

class Xf:
    """Maps robot design units to supersampled canvas pixels, with optional rotation."""

    def __init__(self, ox, oy, scale, dx=0.0, dy=0.0, rot=0.0, pivot=(0.0, 0.0)):
        self.ox, self.oy, self.s, self.dx, self.dy = ox, oy, scale, dx, dy
        self.c, self.sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        self.px, self.py = pivot

    def __call__(self, x, y):
        x, y = x - self.px, y - self.py
        x, y = x * self.c - y * self.sn, x * self.sn + y * self.c
        x, y = x + self.px + self.dx, y + self.py + self.dy
        return ((self.ox + x * self.s) * SS, (self.oy + y * self.s) * SS)

    def len(self, v):
        return v * self.s * SS


def rrect_pts(x, y, w, h, r, n=8):
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def ellipse_pts(cx, cy, rx, ry, n=36):
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def fill(d, xf, pts, col):
    d.polygon([xf(*p) for p in pts], fill=col)


def dot(d, xf, x, y, r, col):
    X, Y = xf(x, y)
    R = xf.len(r)
    d.ellipse([X - R, Y - R, X + R, Y + R], fill=col)


def stroke(d, xf, pts, w, col):
    P = [xf(*p) for p in pts]
    W = xf.len(w)
    d.line(P, fill=col, width=max(1, round(W)), joint="curve")
    for X, Y in (P[0], P[-1]):
        d.ellipse([X - W / 2, Y - W / 2, X + W / 2, Y + W / 2], fill=col)


def arc_pts(cx, cy, r, a0, a1, n=12):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def arm(d, xf, shoulder, side, up_deg, fore_deg, hand, colors=None, widths=(22, 19), hand_r=13, prop=None):
    """Angles are measured from straight down, positive = away from the body."""
    c = colors or dict(upper=COL["dark"], fore=COL["dark"], joint=COL["dark"], elbow=COL["body"], hand=COL["hand"],
                       hand_dark=COL["leg"])
    sx, sy = shoulder
    ex = sx + side * math.sin(math.radians(up_deg)) * L1
    ey = sy + math.cos(math.radians(up_deg)) * L1
    fx, fy = side * math.sin(math.radians(fore_deg)), math.cos(math.radians(fore_deg))
    hx, hy = ex + fx * L2, ey + fy * L2
    stroke(d, xf, [(sx, sy), (ex, ey)], widths[0], c["upper"])
    stroke(d, xf, [(ex, ey), (hx, hy)], widths[1], c["fore"])
    dot(d, xf, sx, sy, widths[0] * 0.62, c["joint"])
    dot(d, xf, ex, ey, widths[1] * 0.53, c["elbow"])
    if prop == "clipboard":  # held by its top edge
        fill(d, xf, rrect_pts(hx - 16, hy - 4, 32, 44, 4), ENG["board"])
        fill(d, xf, rrect_pts(hx - 12, hy + 4, 24, 32, 2), ENG["paper"])
        for ly in (14, 21, 28):
            stroke(d, xf, [(hx - 8, hy + ly), (hx + 8, hy + ly)], 1.6, ENG["clip"])
    if hand == "point":
        stroke(d, xf, [(hx + fx * 6, hy + fy * 6), (hx + fx * 25, hy + fy * 25)], 8, c["hand"])
    elif hand == "thumb":  # fist with the thumb up
        stroke(d, xf, [(hx - side * 3, hy - 6), (hx - side * 4, hy - 30)], 12, c["hand"])
        fill(d, xf, ellipse_pts(hx, hy, hand_r + 4, hand_r + 1), c["hand"])
        for dy in (-3, 3):
            stroke(d, xf, [(hx - side * 1, hy + dy), (hx + side * (hand_r + 1), hy + dy)], 1.8, c["hand_dark"])
        return
    dot(d, xf, hx, hy, hand_r, c["hand"])


NUMERIC = ("rU", "rF", "lU", "lF", "tilt", "hdy", "ex", "ey", "happy", "lift", "x", "legL", "legR")


def pose(action: str, t: float, tl: float) -> dict:
    s = math.sin
    p = dict(rU=10 + 2 * s(t * 1.7), rF=6 + 3 * s(t * 1.7), lU=10 + 2 * s(t * 1.5 + 1), lF=6 + 3 * s(t * 1.5 + 1),
             tilt=3 * s(t * 2 * math.pi / 2.4), hdy=0.0, ex=0.0, ey=0.0, happy=0.0, lift=0.0,
             x=0.0, legL=0.0, legR=0.0, hand="open")
    if action in ("walk", "walkin", "exit"):
        dist, dur = {"walk": (WALK_DIST, ENTER), "walkin": (PEEK_X, WALK_IN), "exit": (WALK_DIST, EXIT)}[action]
        u = min(tl / dur, 1.0)
        if action == "exit":  # speeds up as it leaves to the left
            progress, amp = u * u, 1.0
            x = -dist * progress
        else:  # slows down as it arrives
            progress = 1 - (1 - u) ** 2
            amp = 1 - smoothstep((tl - (dur - 0.35)) / 0.35)
            x = -dist * (1 - progress)
        sw = s(2 * math.pi * dist * progress / STRIDE)
        p.update(x=x, rU=12 + 22 * sw * amp, rF=22 + 22 * sw * amp,
                 lU=12 - 22 * sw * amp, lF=22 - 22 * sw * amp, legL=9 * max(0.0, sw) * amp,
                 legR=9 * max(0.0, -sw) * amp, lift=4 * abs(sw) * amp, tilt=3 * sw * amp,
                 ex=-4 if action == "exit" else 3)
        if action == "exit":
            p.update(rU=150, rF=165 + 28 * s(2 * math.pi * 1.6 * tl), happy=1.0)
    elif action == "sneak":  # peek in from the left edge, look around, straighten up
        u = smoothstep(tl / 0.5)
        back = smoothstep((tl - (SNEAK - 0.35)) / 0.35)
        looking_away = 0.9 < tl < 1.3
        p.update(x=-WALK_DIST + (WALK_DIST - PEEK_X) * u, tilt=14 * u * (1 - back),
                 ex=-3 if looking_away else 6, ey=-3 if looking_away else 0, lift=1.5 * abs(s(t * 6)))
    elif action == "talk":
        p.update(rU=22 + 5 * s(t * 2.1), rF=60 + 20 * s(t * 3.3), lU=20 + 5 * s(t * 1.8 + 1), lF=50 + 18 * s(t * 2.9 + 2))
    elif action == "wave":
        p.update(rU=150, rF=165 + 28 * s(2 * math.pi * 1.6 * tl), happy=1.0, tilt=-5 + 2 * s(t * 3))
    elif action == "point":
        p.update(rU=88, rF=92 + 3 * s(t * 6), hand="point", tilt=5, ex=6, ey=1)
    elif action == "thumbsup":
        p.update(rU=55, rF=172, hand="thumb", happy=1.0, lift=4 * abs(s(2 * math.pi * tl)))
    elif action == "nod":
        n = max(0.0, s(2 * math.pi * 1.4 * tl))
        p.update(hdy=7 * n, ey=2 * n, tilt=0, rU=18, rF=40 + 10 * s(t * 3), lU=18, lF=40 + 10 * s(t * 3 + 1))
    elif action == "think":
        p.update(rU=150, rF=235 + 6 * s(t * 5), tilt=-6, ex=-4, ey=-5)
    elif action == "celebrate":
        p.update(rU=160 + 8 * s(t * 9), rF=176, lU=160 + 8 * s(t * 9 + math.pi), lF=176, happy=1.0,
                 lift=16 * abs(s(2 * math.pi * 1.3 * tl)))
    return p


def blend(a, b, k):
    out = {key: a[key] + (b[key] - a[key]) * k for key in NUMERIC}
    out["hand"] = b["hand"] if k > 0.5 else a["hand"]
    return out


def smoothstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def draw_robot(d, lay, bg, t, action, p, mouth):
    ox, oy, S = lay["ox"] + p["x"] * lay["scale"], lay["oy"], lay["scale"]
    lift = 5 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.6)) + p["lift"]
    ground = Xf(ox, oy, S)
    body = Xf(ox, oy, S, dy=-lift)
    k = 1 - min(lift, 25) / 60
    fill(d, ground, ellipse_pts(260, 388, 78 * k, 9 * k), mix(bg, COL["shadow"], 0.35))

    for x, fx, up in ((226, 214, p["legL"]), (272, 264, p["legR"])):
        fill(d, body, rrect_pts(x, 318 - up, 22, 52, 6), COL["leg"])
        fill(d, body, rrect_pts(fx, 364 - up, 42, 16, 8), COL["foot"])
    fill(d, body, rrect_pts(190, 198, 140, 128, 20), COL["body"])
    fill(d, body, rrect_pts(218, 226, 84, 54, 10), COL["panel"])
    blink_on = (t * 1.25) % 1 < 0.5
    dot(d, body, 238, 253, 7, COL["red"] if blink_on else mix(COL["red"], COL["panel"], 0.6))
    dot(d, body, 260, 253, 7, COL["amber"])
    dot(d, body, 282, 253, 7, COL["green"] if not blink_on else mix(COL["green"], COL["panel"], 0.6))
    fill(d, body, rrect_pts(225, 296, 70, 8, 4), COL["dark"])
    fill(d, body, rrect_pts(248, 178, 24, 26, 4), COL["leg"])

    head = Xf(ox, oy, S, dy=-lift + p["hdy"], rot=p["tilt"], pivot=(260, 190))
    stroke(d, head, [(260, 82), (260, 56)], 4, COL["leg"])
    pulse = 0.5 + 0.5 * math.sin(t * (2 * math.pi * (4 if action == "think" else 1)))
    dot(d, head, 260, 48, 9, mix(COL["amber"], COL["amber_lo"], pulse))
    dot(d, head, 180, 135, 12, COL["dark"])
    dot(d, head, 340, 135, 12, COL["dark"])
    fill(d, head, rrect_pts(184, 80, 152, 108, 24), COL["head"])
    fill(d, head, rrect_pts(200, 96, 120, 76, 16), COL["face"])

    ex, ey = p["ex"], p["ey"]
    blink = 0.12 if (t + 1.1) % 3.4 < 0.12 else 1.0
    for cx in (236, 284):
        if p["happy"] > 0.5:
            stroke(d, head, arc_pts(cx + ex, 128 + ey, 10, 200, 340), 5, COL["glow"])
        else:
            fill(d, head, ellipse_pts(cx + ex, 124 + ey, 10, 12 * blink), COL["glow"])
    if mouth > 0.02:
        h, w = 7 + 20 * mouth, 40 - 10 * mouth
        fill(d, head, rrect_pts(260 - w / 2, 154 - h / 2, w, h, min(w, h) / 2 - 0.01), COL["glow"])
    elif p["happy"] > 0.5:
        stroke(d, head, arc_pts(260, 142, 16, 30, 150), 5, COL["glow"])
    else:
        fill(d, head, rrect_pts(240, 150, 40, 8, 4), COL["glow"])

    arm(d, body, (184, 224), -1, p["lU"], p["lF"], "open")
    arm(d, body, (336, 224), 1, p["rU"], p["rF"], p["hand"])


def draw_engineer(d, lay, bg, t, action, p, mouth):
    """Cartoon safety engineer: hard hat, hi-vis vest, clipboard. Same skeleton and poses as the robot."""
    E = ENG
    ox, oy, S = lay["ox"] + p["x"] * lay["scale"], lay["oy"], lay["scale"]
    lift = 5 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.6)) + p["lift"]
    ground = Xf(ox, oy, S)
    body = Xf(ox, oy, S, dy=-lift)
    k = 1 - min(lift, 25) / 60
    fill(d, ground, ellipse_pts(260, 388, 70 * k, 9 * k), mix(bg, COL["shadow"], 0.35))

    for x, bx, up in ((229, 220, p["legL"]), (267, 262, p["legR"])):
        fill(d, body, rrect_pts(x, 300 - up, 24, 66, 6), E["pants"])
        fill(d, body, rrect_pts(bx, 358 - up, 38, 22, 9), E["boot"])
    fill(d, body, rrect_pts(196, 192, 128, 122, 24), E["vest"])
    fill(d, body, [(236, 192), (284, 192), (260, 234)], E["shirt"])
    fill(d, body, [(236, 192), (252, 192), (246, 206)], E["shirt_dark"])
    fill(d, body, [(268, 192), (284, 192), (274, 206)], E["shirt_dark"])
    for y in (262, 286):
        fill(d, body, rrect_pts(198, y, 124, 9, 2), E["stripe"])
    stroke(d, body, [(260, 236), (260, 304)], 2, E["vest_dark"])
    fill(d, body, rrect_pts(286, 226, 4, 16, 2), E["shirt_dark"])  # pen
    fill(d, body, rrect_pts(280, 236, 24, 20, 4), E["vest_dark"])  # pocket
    fill(d, body, rrect_pts(200, 304, 120, 11, 4), E["belt"])
    fill(d, body, rrect_pts(253, 304, 14, 11, 2), E["buckle"])
    fill(d, body, rrect_pts(248, 174, 24, 26, 6), E["skin_dark"])

    head = Xf(ox, oy, S, dy=-lift + p["hdy"], rot=p["tilt"], pivot=(260, 190))
    fill(d, head, ellipse_pts(208, 144, 8, 11), E["skin_dark"])
    fill(d, head, ellipse_pts(312, 144, 8, 11), E["skin_dark"])
    fill(d, head, rrect_pts(210, 100, 13, 32, 5), E["hair"])
    fill(d, head, rrect_pts(297, 100, 13, 32, 5), E["hair"])
    fill(d, head, ellipse_pts(260, 142, 50, 54), E["skin"])
    dome = [(260 + 58 * math.cos(math.radians(a)), 106 + 52 * math.sin(math.radians(a))) for a in range(180, 361, 10)]
    fill(d, head, dome, E["hat"])
    fill(d, head, rrect_pts(254, 56, 12, 48, 5), E["hat_dark"])
    fill(d, head, rrect_pts(188, 100, 144, 14, 7), E["hat_dark"])

    ex, ey = p["ex"], p["ey"]
    brow = -4 if action == "think" else (-2 if p["happy"] > 0.5 else 0)
    stroke(d, head, [(229, 124 + brow + ey * 0.5), (247, 121 + brow + ey * 0.5)], 4, E["hair"])
    stroke(d, head, [(273, 121 + brow * 1.5 + ey * 0.5), (291, 124 + brow * 1.5 + ey * 0.5)], 4, E["hair"])
    blink = (t + 1.1) % 3.4 < 0.12
    for cx in (238, 282):
        if p["happy"] > 0.5:
            stroke(d, head, arc_pts(cx, 142, 8, 200, 340), 4, E["ink"])
        elif blink:
            stroke(d, head, [(cx - 8, 139), (cx + 8, 139)], 3, E["ink"])
        else:
            fill(d, head, ellipse_pts(cx, 138, 9, 11), E["white"])
            dot(d, head, cx + ex * 0.6, 139 + ey * 0.6, 5, E["ink"])
    fill(d, head, ellipse_pts(260, 154, 6, 5), E["skin_dark"])
    dot(d, head, 230, 161, 7, mix(E["skin"], E["cheek"], 0.6))
    dot(d, head, 290, 161, 7, mix(E["skin"], E["cheek"], 0.6))
    if mouth > 0.02:
        h, w = 5 + 16 * mouth, 26 - 6 * mouth
        fill(d, head, rrect_pts(260 - w / 2, 172 - h / 2, w, h, min(w, h) / 2 - 0.01), E["mouth"])
    elif p["happy"] > 0.5:
        stroke(d, head, arc_pts(260, 160, 14, 30, 150), 4, E["mouth"])
    else:
        stroke(d, head, arc_pts(260, 158, 11, 45, 135), 4, E["mouth"])

    sleeves = dict(upper=E["shirt"], fore=E["shirt"], joint=E["vest"], elbow=E["shirt"], hand=E["skin"],
                   hand_dark=E["skin_dark"])
    arm(d, body, (202, 214), -1, p["lU"], p["lF"], "open", sleeves, (20, 18), 12, prop="clipboard")
    arm(d, body, (318, 214), 1, p["rU"], p["rF"], p["hand"], sleeves, (20, 18), 12)


CHARACTERS = {"robot": draw_robot, "engineer": draw_engineer}


def save_pose_sheet(character: str, bg, path: Path):
    """One still per action, to preview a character's look."""
    lay = layouts(no_bubble=True)
    shots = [("idle", 0.0, 0.0), ("talk", 0.6, 0.55), ("wave", 0.3, 0.0), ("point", 0.0, 0.4),
             ("thumbsup", 0.0, 0.0), ("nod", 0.15, 0.0), ("think", 0.0, 0.3), ("celebrate", 0.2, 0.0)]
    cell, cell_h = 360, 430
    sheet = Image.new("RGB", (cell * 4, (cell_h + 40) * 2), bg)
    d_sheet = ImageDraw.Draw(sheet)
    label_font = load_font(FONT_CANDIDATES[1][0], 28)
    for i, (action, tl, mouth) in enumerate(shots):
        canvas = Image.new("RGBA", (lay["W"] * SS, lay["H"] * SS), bg + (255,))
        CHARACTERS[character](ImageDraw.Draw(canvas), lay, bg, 0.7, action, pose(action, 0.7, tl), mouth)
        img = canvas.reduce(SS).crop((150, 30, 1010, 1060)).convert("RGB")
        img = img.resize((cell, cell_h), Image.LANCZOS)
        x, y = (i % 4) * cell, (i // 4) * (cell_h + 40)
        sheet.paste(img, (x, y))
        d_sheet.text((x + cell / 2, y + cell_h + 18), action, font=label_font, fill=COL["ink"], anchor="mm")
    sheet.save(path)


# ---------------------------------------------------------------- speech bubble

_font_cache: dict[tuple, ImageFont.ImageFont] = {}
_glyph_cache: dict[tuple, bool] = {}


def load_font(path: str | None, px: int, index: int = 0):
    key = (path, px, index)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, px, index=index) if path else ImageFont.load_default(px)
    return _font_cache[key]


def _has_glyph(path: str, index: int, ch: str) -> bool:
    if (path, index, ch) not in _glyph_cache:
        f = load_font(path, 40, index)
        _glyph_cache[(path, index, ch)] = ch.isspace() or not np.array_equal(
            np.array(f.getmask(ch)), np.array(f.getmask("￿")))
    return _glyph_cache[(path, index, ch)]


def font_face_for(text: str) -> tuple:
    """The first candidate font that can draw every character (Arial Rounded lacks e.g. Polish letters)."""
    faces = [f for f in FONT_CANDIDATES if Path(f[0]).exists()]
    for face in faces:
        if all(_has_glyph(face[0], face[1], ch) for ch in set(text)):
            return face
    return faces[-1] if faces else (None, 0, 0)


def ease_out_back(u):
    c1 = 1.70158
    return 1 + (c1 + 1) * (u - 1) ** 3 + c1 * (u - 1) ** 2


class Bubble:
    """Speech bubble: spoken words revealed as they're said, optional translation underneath."""

    def __init__(self, lay, all_text: str):
        self.x0, self.xmax, self.cy = lay["bubble_x0"], lay["bubble_xmax"], lay["bubble_cy"]
        self.tip = lay["bubble_tip"]
        self.pad, self.radius, self.max_h = 52, 40, 570
        self.face = font_face_for(all_text)  # one font for the whole video
        self.layout_cache: dict[int, dict] = {}
        self.layer_cache: dict[tuple, tuple] = {}

    @staticmethod
    def _wrap(words, font, maxw):
        space = font.getlength(" ")
        lines, cur, curw = [], [], 0.0
        for i, w in enumerate(words):
            ww = font.getlength(w)
            if cur and curw + space + ww > maxw:
                lines.append(cur)
                cur, curw = [], 0.0
            curw = curw + space + ww if cur else ww
            cur.append(i)
        if cur:
            lines.append(cur)
        return lines, [sum(font.getlength(words[i]) for i in ln) + space * (len(ln) - 1) for ln in lines]

    def _height(self, n, size, n_sub, sub_size):
        sub = n_sub * sub_size * 1.3 + 0.35 * size if n_sub else 0
        return n * size * 1.25 + sub + 2 * self.pad - 14

    def _layout(self, idx, words, sub_words):
        if idx in self.layout_cache:
            return self.layout_cache[idx]
        path, main_idx, sub_idx = self.face
        maxw = (self.xmax - self.x0 - 2 * self.pad) * SS
        for size, max_lines in ((84, 3), (72, 3), (64, 4), (56, 4), (50, 4), (44, 5)):
            font = load_font(path, size * SS, main_idx)
            sub_size = max(38, round(size * 0.72))
            sub_font = load_font(path, sub_size * SS, sub_idx)
            lines, widths = self._wrap(words, font, maxw)
            sub_lines, sub_widths = self._wrap(sub_words, sub_font, maxw)
            if len(lines) <= max_lines and self._height(min(len(lines), max_lines), size, len(sub_lines), sub_size) <= self.max_h:
                break
        out = dict(font=font, size=size, lines=lines, widths=widths, max_lines=max_lines,
                   sub_font=sub_font, sub_size=sub_size, sub_lines=sub_lines, sub_widths=sub_widths)
        self.layout_cache[idx] = out
        return out

    def layer(self, idx, words, subtitle, nvis):
        sub_words = subtitle.split()
        L = self._layout(idx, words, sub_words)
        lines, max_lines, size = L["lines"], L["max_lines"], L["size"]
        cur_line = 0
        for li, ln in enumerate(lines):
            if ln[0] < nvis:
                cur_line = li
        start = min(max(0, cur_line - max_lines + 1), max(0, len(lines) - max_lines))
        key = (idx, nvis, start)
        if key in self.layer_cache:
            return self.layer_cache[key]
        shown = lines[start:start + max_lines]
        line_h, sub_h = size * 1.25, L["sub_size"] * 1.3
        text_w = max(L["widths"][start:start + max_lines] + L["sub_widths"])
        bw = max(text_w / SS + 2 * self.pad, 360)
        bh = self._height(len(shown), size, len(L["sub_lines"]), L["sub_size"])
        cy = max(min(self.cy, 600 - bh / 2), 30 + bh / 2)  # tall bubbles move up, clear of a pointing arm
        x0, y0 = self.x0, cy - bh / 2
        x1, y1 = x0 + bw, y0 + bh
        tipx, tipy = self.tip
        r = self.radius
        base_top = min(max(cy - 8, y0 + r), y1 - r - 36)
        base_bot = base_top + 36
        # Layer covers bubble + tail, in supersampled pixels.
        lx0, ly0 = tipx - 6, min(y0, tipy) - 6
        lx1, ly1 = x1 + 6, max(y1, tipy) + 6
        img = Image.new("RGBA", (int((lx1 - lx0) * SS), int((ly1 - ly0) * SS)), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        to = lambda x, y: ((x - lx0) * SS, (y - ly0) * SS)  # noqa: E731
        outline = []
        for x, y in rrect_pts(x0, y0, bw, bh, r):
            outline.append((x, y))
            if len(outline) == 3 * 9:  # after the bottom-left corner, insert the tail on the left edge
                outline += [(x0, base_bot), (tipx, tipy), (x0, base_top)]
        d.polygon([to(x, y) for x, y in outline], fill=COL["bubble"] + (255,),
                  outline=COL["bubble_line"] + (255,), width=3 * SS)
        font, sub_font = L["font"], L["sub_font"]
        top = y0 + self.pad - 7
        for li, ln in enumerate(shown):
            X = (x0 + self.pad - lx0) * SS
            Y = (top - ly0 + line_h * (li + 0.5)) * SS
            for i in ln:
                if i < nvis:
                    d.text((X, Y), words[i], font=font, fill=COL["ink"] + (255,), anchor="lm")
                X += font.getlength(words[i]) + font.getlength(" ")
        top += len(shown) * line_h + 0.35 * size
        for li, ln in enumerate(L["sub_lines"]):
            Y = (top - ly0 + sub_h * (li + 0.5)) * SS
            text = " ".join(sub_words[i] for i in ln)
            d.text(((x0 + self.pad - lx0) * SS, Y), text, font=sub_font, fill=COL["sub"] + (255,), anchor="lm")
        out = (img, ((tipx - lx0) * SS, (tipy - ly0) * SS))
        self.layer_cache[key] = out
        return out


# ---------------------------------------------------------------- render

def layouts(no_bubble: bool):
    S = 2.4
    if no_bubble:
        W, H, cx = 1080, 1080, 500
    else:
        W, H, cx = 1920, 1080, 520
    return dict(W=W, H=H, scale=S, ox=cx - 260 * S, oy=1010 - 382 * S,
                bubble_x0=990, bubble_xmax=1880, bubble_cy=400, bubble_tip=(915, 485))


def ensure_encoder() -> Path:
    src, exe = HERE / "encoder.swift", HERE / "robot_encoder"
    if not exe.exists() or exe.stat().st_mtime < src.stat().st_mtime:
        print("Building the video encoder (first run only)...")
        r = subprocess.run(["swiftc", "-O", "-swift-version", "5", str(src), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit("Could not build the encoder. Install Xcode command line tools with:\n"
                     "  xcode-select --install\n\n" + r.stderr[-2000:])
    return exe


def to_bgra(img: Image.Image) -> bytes:
    try:
        return img.tobytes("raw", "BGRA")
    except ValueError:
        return np.asarray(img)[..., [2, 1, 0, 3]].tobytes()


def render_video(segments, total, audio, bg, no_bubble, lead, enter, mp4: Path, workdir: Path, sheet: Path | None,
                 character: str = "robot"):
    lay = layouts(no_bubble)
    W, H = lay["W"], lay["H"]
    nframes = int(math.ceil(total * FPS))
    env, mouth = mouth_track(audio, nframes)
    prepare_reveal(segments, env)
    m4a = write_m4a(audio, workdir)

    if enter == "sneak":
        windows = [(0.0, SNEAK, "sneak"), (SNEAK, lead, "walkin")]
    else:
        windows = [(0.0, lead, "walk" if enter else "idle")]
    for s in segments:
        action = s.action
        if s.pause:  # short pauses keep the current action going; long ones rest
            prev = windows[-1][2]
            action = prev if s.pause_len < 1.0 and prev not in ("walk", "walkin", "sneak") else "idle"
        windows.append((s.t0, s.t1, action))
    windows.append((segments[-1].t1, total + 1, "gone" if segments[-1].action == "exit" else "idle"))
    spoken = [(i, s) for i, s in enumerate(segments) if s.audio is not None]
    bubble = None if no_bubble else Bubble(lay, " ".join(s.text + " " + s.subtitle for s in segments))

    enc = subprocess.Popen([str(ensure_encoder()), str(W), str(H), str(FPS), str(m4a), str(mp4)],
                           stdin=subprocess.PIPE)
    poster = None
    sheet_frames = []
    sheet_at = {0, nframes - 1} | {int(FPS * (s.t0 + 0.6 * (s.t1 - s.t0))) for s in segments}
    if enter == "sneak":
        sheet_at |= {int(FPS * x) for x in (0.3, 0.7, 1.1, 1.5, 2.3, 2.7)}
    elif enter:
        sheet_at |= {int(FPS * x) for x in (0.4, 0.8, 1.2, 1.6)}
    for f in range(nframes):
        t = f / FPS
        wi = next(i for i, w in enumerate(windows) if w[0] <= t < w[1])
        t0, _, action = windows[wi]
        p = pose(action, t, t - t0)
        if wi > 0 and windows[wi - 1][2] != action and t - t0 < BLEND:
            pt0, _, paction = windows[wi - 1]
            p = blend(pose(paction, t, t - pt0), p, smoothstep((t - t0) / BLEND))

        canvas = Image.new("RGBA", (W * SS, H * SS), bg + (255,))
        d = ImageDraw.Draw(canvas)

        if bubble is not None and action not in ("exit", "gone"):
            current = [(i, s) for i, s in spoken if s.s0 <= t]
            if current:
                idx, s = current[-1]
                rf = f - s.reveal_f0
                frac = s.reveal[rf] if rf < len(s.reveal) else 1.0
                nvis = sum(1 for st in s.word_starts if st <= frac + 1e-6)
                img, anchor = bubble.layer(idx, s.words, s.subtitle, max(1, nvis))
                u = (t - s.s0) / 0.25
                if u < 1:
                    start = 0.35 if idx == spoken[0][0] else 0.85
                    sc = start + (1 - start) * ease_out_back(u)
                    img = img.resize((max(1, int(img.width * sc)), max(1, int(img.height * sc))), Image.BILINEAR)
                    anchor = (anchor[0] * sc, anchor[1] * sc)
                tip = lay["bubble_tip"]
                canvas.paste(img, (int(tip[0] * SS - anchor[0]), int(tip[1] * SS - anchor[1])), img)

        if action != "gone":
            CHARACTERS[character](d, lay, bg, t, action, p, float(mouth[f]))
        frame = canvas.reduce(SS)
        enc.stdin.write(to_bgra(frame))
        if f == 0:
            poster = frame.convert("RGB")
        if sheet is not None and f in sheet_at:
            sheet_frames.append(frame.convert("RGB").resize((W // 3, H // 3), Image.LANCZOS))
        if f % FPS == 0:
            print(f"\r  rendering {f / FPS:4.1f}s / {total:.1f}s", end="", flush=True)
    enc.stdin.close()
    if enc.wait():
        sys.exit("Video encoding failed.")
    print(f"\r  rendered {nframes} frames ({total:.1f}s)          ")

    poster_path = workdir / "poster.png"
    poster.save(poster_path)
    if sheet is not None and sheet_frames:
        cols = 4
        rows = math.ceil(len(sheet_frames) / cols)
        fw, fh = sheet_frames[0].size
        grid = Image.new("RGB", (cols * fw + (cols + 1) * 8, rows * fh + (rows + 1) * 8), (200, 200, 200))
        for i, im in enumerate(sheet_frames):
            grid.paste(im, (8 + (i % cols) * (fw + 8), 8 + (i // cols) * (fh + 8)))
        grid.save(sheet)
    return poster_path, (W, H)


# ---------------------------------------------------------------- pptx

def autoplay_timing(spid: int, dur_ms: int) -> str:
    from pptx.oxml.ns import nsdecls
    return (
        f'<p:timing {nsdecls("p")}><p:tnLst><p:par>'
        '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
        '<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        '<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
        '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>'
        '<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        '<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="afterEffect">'
        '<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        f'<p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr><p:cTn id="6" dur="{dur_ms}" fill="hold"/>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:cmd>'
        '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn>'
        '<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
        '</p:seq>'
        '<p:video><p:cMediaNode vol="80000"><p:cTn id="7" fill="hold" display="0">'
        '<p:stCondLst><p:cond delay="indefinite"/></p:stCondLst></p:cTn>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cMediaNode></p:video>'
        '</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
    )


def make_pptx(mp4: Path, poster: Path, out: Path, bg_hex: str, size, total: float, script: str):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import qn
    from pptx.util import Emu, Inches

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)  # 16:9
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = RGBColor.from_string(bg_hex)
    W, H = size
    if W > H:
        left, top, w, h = 0, 0, prs.slide_width, prs.slide_height
    else:  # robot-only: left side, leaving room on the right for your content
        h = Inches(6.5)
        w = h
        left, top = Inches(0.3), (prs.slide_height - h) // 2
    movie = slide.shapes.add_movie(str(mp4), left, top, w, h, poster_frame_image=str(poster), mime_type="video/mp4")
    movie.name = "Talking robot"
    new = parse_xml(autoplay_timing(movie.shape_id, int(total * 1000)))
    old = slide._element.find(qn("p:timing"))
    if old is not None:
        old.getparent().replace(old, new)
    else:
        ext = slide._element.find(qn("p:extLst"))
        if ext is not None:
            ext.addprevious(new)
        else:
            slide._element.append(new)
    slide.notes_slide.notes_text_frame.text = "Robot says: " + " ".join(re.sub(TAG_RE, " ", script).split())
    prs.save(out)


# ---------------------------------------------------------------- main

def list_voices():
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    print("English voices (use with --voice NAME):")
    for line in out.splitlines():
        if re.search(r"\sen_[A-Z]{2}\s", line):
            print("  " + line.split("#")[0].rstrip())


def slugify(text: str) -> str:
    words = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", text).lower()
    words = re.findall(r"[a-z0-9]+", words)[:4]
    return "-".join(words) or "robot"


def main():
    ap = argparse.ArgumentParser(description="Make a talking robot video and PowerPoint slide from your words.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Actions: " + ", ".join(f"[{a}]" for a in ACTIONS) + ", [pause]")
    ap.add_argument("text", nargs="*", help="what the robot says, with optional [action] tags")
    ap.add_argument("-f", "--file", help="read the script from a text file")
    ap.add_argument("--voice", default="Samantha", help="macOS voice (default: Samantha; see --voices)")
    ap.add_argument("--rate", type=int, default=175, help="speaking speed in words per minute (default: 175)")
    ap.add_argument("--bg", default="FFFFFF", help="background color as hex, to match your slide (default: FFFFFF)")
    ap.add_argument("--no-bubble", action="store_true", help="square robot-only video without the speech bubble")
    ap.add_argument("--character", default="robot", choices=list(CHARACTERS), help="who talks (default: robot)")
    ap.add_argument("--poses", action="store_true", help="save a picture of the character doing each action, and exit")
    ap.add_argument("--enter", nargs="?", const="walk", choices=["walk", "sneak"],
                    help="robot walks in from the left before it speaks; '--enter sneak' peeks in first")
    ap.add_argument("--name", help="output file name (default: made from the first words)")
    ap.add_argument("--outdir", default=str(HERE / "output"), help="where to save (default: ./output)")
    ap.add_argument("--no-pptx", action="store_true", help="only make the MP4")
    ap.add_argument("--sheet", action="store_true", help="also save a preview image with one frame per action")
    ap.add_argument("--voices", action="store_true", help="list English voices and exit")
    ap.add_argument("--actions", action="store_true", help="list actions and exit")
    args = ap.parse_args()

    if args.voices:
        return list_voices()
    if args.poses:
        out = Path(args.outdir) / f"{args.character}-poses.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        save_pose_sheet(args.character, hex_rgb(args.bg), out)
        return print(f"Poses: {out}")
    if args.actions:
        for a, desc in ACTIONS.items():
            print(f"  [{a}]".ljust(14) + desc)
        print("  [pause]".ljust(14) + "short silence")
        return

    if args.file:
        script = Path(args.file).read_text()
    elif args.text:
        script = " ".join(args.text)
    elif sys.stdin.isatty():
        script = input("What should the robot say? ")
    else:
        script = sys.stdin.read()
    script = script.strip()
    if not script:
        sys.exit("Give the robot something to say, for example:\n  python3 make_robot.py \"Hello everyone, welcome!\"")

    bg_hex = args.bg.lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", bg_hex):
        sys.exit("--bg must be a 6-digit hex color like FFFFFF or 1E2761")
    bg = hex_rgb(bg_hex)

    segments = parse_script(script)
    if not any(s.text for s in segments) and not any(not s.pause for s in segments):
        sys.exit("Nothing to say or do.")
    print("Robot script:")
    for i, s in enumerate(segments, 1):
        label = f"pause {s.pause_len:g}s" if s.pause else s.action + (f" {s.speed}" if s.speed else "")
        print(f"  {i}. [{label}] {s.text or ('' if s.pause else '(no words)')}" + (f"  {{{s.subtitle}}}" if s.subtitle else ""))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    name = args.name or slugify(script)
    mp4 = outdir / f"{name}.mp4"

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        print(f"Generating voice ({args.voice})...")
        for i, s in enumerate(segments):
            if s.text:
                s.audio = synth(s.text, args.voice, args.rate, work, i, s.speed)
        lead = {None: LEAD, "walk": ENTER + 0.4, "sneak": SNEAK + WALK_IN + 0.3}[args.enter]
        total = build_timeline(segments, lead)
        audio = mix_audio(segments, total)
        print("Animating...")
        sheet = outdir / f"{name}_frames.png" if args.sheet else None
        poster, size = render_video(segments, total, audio, bg, args.no_bubble, lead, args.enter, mp4, work, sheet,
                                     args.character)
        print(f"Video:  {mp4}")
        if not args.no_pptx:
            pptx = outdir / f"{name}.pptx"
            make_pptx(mp4, poster, pptx, bg_hex, size, total, script)
            print(f"Slide:  {pptx}")
        if sheet:
            print(f"Frames: {sheet}")


if __name__ == "__main__":
    main()
