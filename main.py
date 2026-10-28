"""
Automatic PG-13 horror Shorts maker.
Steps: write story (Gemini) -> narrate (edge-tts) -> draw scenes (Pollinations)
       -> assemble vertical video (ffmpeg) -> upload to YouTube.

Set DRY_RUN=1 to make the video without uploading.
"""
import asyncio
import json
import os
import random
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

WORK = Path("work")
WORK.mkdir(exist_ok=True)
W, H = 1080, 1920
FPS = 30

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
PRIVACY = os.getenv("YT_PRIVACY", "public")
HISTORY_FILE = Path("history.json")

STORY_TYPES = [
    "a haunted place (house, hotel, school, hospital, lighthouse)",
    "an urban legend told as if it really happened",
    "a creepy encounter (a stranger, a night drive, a phone call, a delivery, a neighbor)",
]

# Mostly Western settings, sometimes African
SETTINGS = (
    ["a small town in the American Midwest", "rural England", "a snowy Canadian cabin",
     "a quiet American suburb", "an old Scottish village", "a New England coastal town",
     "a lonely highway in Texas", "a big-city apartment building at night"] * 2
    + ["a village in Malawi", "a night market in Lagos", "a farm road in rural Kenya"]
)

STYLES = [
    "dark graphic novel illustration, heavy ink shadows, high contrast",
