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

CHANNEL = "@Andrewsmanxy265"
LOGO_FILE = Path("logo.png")  # upload your logo to the repository with this name
OUTRO_VOICE_LINE = "Subscribe, like, and comment, and follow for more scary stories."

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
WESTERN_SETTINGS = [
    "a small town in the American Midwest",
    "rural England",
    "a snowy Canadian cabin",
    "a quiet American suburb",
    "an old Scottish village",
    "a New England coastal town",
    "a lonely highway in Texas",
    "a big-city apartment building at night",
]
AFRICAN_SETTINGS = [
    "a village in Malawi",
    "a night market in Lagos",
    "a farm road in rural Kenya",
]
SETTINGS = WESTERN_SETTINGS + WESTERN_SETTINGS + AFRICAN_SETTINGS

STYLES = [
    "dark graphic novel illustration, heavy ink shadows, high contrast",
    "dark oil painting, moody brush strokes, dim candle light",
    "gritty horror comic book art, bold lines, limited color palette",
]

VOICES = [
    ("en-US-GuyNeural", "-8%", "-6Hz"),
    ("en-GB-RyanNeural", "-6%", "-4Hz"),
    ("en-US-ChristopherNeural", "-8%", "-5Hz"),
]


# ---------------------------------------------------------------- helpers
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise RuntimeError("command failed: " + " ".join(map(str, cmd[:4])))
    return r.stdout


def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_history(hist):
    HISTORY_FILE.write_text(json.dumps(hist[-200:], indent=1))


# ---------------------------------------------------------------- story
def make_story(history):
    story_type = random.choice(STORY_TYPES)
    setting = random.choice(SETTINGS)
    avoid = "; ".join(history[-40:]) or "none"
    prompt = f"""Write an ORIGINAL scary short story for a vertical video narrated aloud.

Type: {story_type}
Setting: {setting}
Language: English

Rules:
- PG-13: fear comes from suspense, atmosphere and a twist. No gore, no graphic violence, no sexual content, no real people, no real brands.
- Total narration 105 to 125 words (about 45 seconds spoken).
- First sentence must be a strong hook.
- End with a creepy twist or chilling last line.
- Split into 6 to 8 scenes. Each scene has 1 to 3 short sentences of narration.
- Do not reuse these earlier titles or ideas: {avoid}

Return ONLY JSON in this shape:
{{
 "title": "catchy title under 70 characters, no hashtags",
 "description": "1-2 sentence teaser for the video description",
 "tags": ["horror", "scary stories", "..."],
 "character_look": "one fixed visual description of the main character (age, hair, clothes) reused in every scene so they look the same; keep the face often in shadow",
 "scenes": [ {{"narration": "text read aloud", "visual": "what the picture shows, no text in picture, nothing gory"}} ]
}}"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.1, "responseMimeType": "application/json"},
    }
    last = None
    for attempt in range(4):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=90)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            if isinstance(data, list):
                data = data[0]
            assert len(data["scenes"]) >= 4
            for s in data["scenes"]:
                assert s["narration"].strip() and s["visual"].strip()
            return data
        except Exception as e:  # noqa
            last = e
            print("story attempt failed:", e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not get a story: {last}")


# ---------------------------------------------------------------- voice
async def _tts(text, voice, rate, pitch, out):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(out)


def make_voice(text, voice, out):
    name, rate, pitch = voice
    for attempt in range(4):
        try:
            asyncio.run(_tts(text, name, rate, pitch, str(out)))
            if Path(out).exists() and Path(out).stat().st_size > 1000:
                return
        except Exception as e:  # noqa
            print("voice attempt failed:", e)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("voice failed")


def audio_seconds(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


# ---------------------------------------------------------------- pictures
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def get_font(size):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def fallback_image(path, seed):
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H))
    px = img.load()
    base = rnd.randint(5, 25)
    for y in range(H):
        v = int(base + 25 * (1 - abs(y - H / 2) / (H / 2)))
        for x in range(0, W):
            px[x, y] = (v, v, v + 6)
    img.save(path)


def make_image(prompt, seed, path):
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:600])
           + f"?width={W}&height={H}&nologo=true&seed={seed}&model=flux")
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                Path(path).write_bytes(r.content)
                Image.open(path).verify()
                return
            print("image status", r.status_code)
        except Exception as e:  # noqa
            print("image attempt failed:", e)
        time.sleep(8 * (attempt + 1))
    print("using fallback picture")
    fallback_image(path, seed)


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def load_logo(size):
    """Return the logo resized to the given width, or None if there is no logo file."""
    if not LOGO_FILE.exists():
        return None
    try:
        logo = Image.open(LOGO_FILE).convert("RGBA")
        ratio = size / logo.width
        return logo.resize((size, max(1, int(logo.height * ratio))))
    except Exception as e:  # noqa
        print("logo problem:", e)
        return None


def apply_watermark(img):
    """Faint, slightly blurred logo in the top-right corner (or faint channel name if no logo)."""
    from PIL import ImageFilter
    img = img.convert("RGBA")
    logo = load_logo(170)
    if logo is not None:
        logo = logo.filter(ImageFilter.GaussianBlur(1.2))
        alpha = logo.getchannel("A").point(lambda a: int(a * 0.38))
        logo.putalpha(alpha)
        img.alpha_composite(logo, (W - logo.width - 40, 70))
    else:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((W - 40, 80), CHANNEL, font=get_font(40),
                                   fill=(255, 255, 255, 95), anchor="ra")
        img = Image.alpha_composite(img, layer)
    return img.convert("RGB")


def make_outro_image(out):
    img = Image.new("RGB", (W, H), (8, 8, 12))
    d = ImageDraw.Draw(img)
    for y in range(H):  # soft dark red glow in the middle
        glow = int(40 * max(0, 1 - abs(y - H / 2) / (H / 2.4)))
        d.line([(0, y), (W, y)], fill=(8 + glow, 8, 12))
    y = 520
    logo = load_logo(320)
    if logo is not None:
        img.paste(logo, ((W - logo.width) // 2, y), logo)
        y += logo.height + 60
    f_big, f_mid = get_font(86), get_font(58)
    d.text((W / 2, y), CHANNEL, font=f_big, fill="white", anchor="ma",
           stroke_width=4, stroke_fill="black")
    y += 170
    for line in ["SUBSCRIBE", "LIKE  •  COMMENT", "FOLLOW FOR MORE"]:
        d.text((W / 2, y), line, font=f_mid, fill=(230, 60, 60), anchor="ma",
               stroke_width=3, stroke_fill="black")
        y += 110
    img.save(out)


def add_caption(src, text, out):
    img = Image.open(src).convert("RGB")
    # fill 1080x1920
    ratio = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * ratio) + 1, int(img.height * ratio) + 1))
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    # darken the lower part so captions are readable
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for i in range(700):
        a = int(170 * (i / 700))
        sd.line([(0, H - 700 + i), (W, H - 700 + i)], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")
    img = apply_watermark(img)
    d = ImageDraw.Draw(img)
    font = get_font(62)
    lines = wrap(d, text, font, W - 160)
    lh = 80
    y = H - 330 - (len(lines) * lh) // 2
    for ln in lines:
        x = (W - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill="white", stroke_width=5, stroke_fill="black")
        y += lh
    img.save(out)


# ---------------------------------------------------------------- video
def scene_clip(img, audio, idx, out):
    dur = audio_seconds(audio) + 0.45
    frames = int(dur * FPS) + 2
    if idx % 2 == 0:
        z = "min(zoom+0.0006,1.15)"
    else:
        z = "if(eq(on,0),1.15,max(zoom-0.0006,1.0))"
    vf = (f"scale=1620:2880,zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={W}x{H}:fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-i", str(img), "-i", str(audio), "-vf", vf, "-af", "apad",
         "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-r", str(FPS), "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])
    return dur


def build_video(clips, final):
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    # quiet spooky drone under the voice
    fc = ("[1:a][2:a]amix=inputs=2:normalize=0,volume=0.05,tremolo=f=0.3:d=0.5[d];"
          "[0:a][d]amix=inputs=2:duration=first:normalize=0[a]")
    run(["ffmpeg", "-y", "-i", str(joined),
         "-f", "lavfi", "-i", "sine=frequency=55:sample_rate=44100",
         "-f", "lavfi", "-i", "sine=frequency=82:sample_rate=44100",
         "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)])


# ---------------------------------------------------------------- upload
def upload(final, story):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds)
    title = story["title"][:90] + " #Shorts"
    desc = (story.get("description", "") + "\n\nStory and visuals created with the help of AI."
            "\n\n#Shorts #Horror #ScaryStories #CreepyStories")
    tags = [t for t in story.get("tags", [])][:15] + ["shorts", "horror"]

    def attempt(synthetic):
        status = {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False}
        if synthetic:
            status["containsSyntheticMedia"] = True
        body = {"snippet": {"title": title, "description": desc, "tags": tags,
                            "categoryId": "24"},
                "status": status}
        media = MediaFileUpload(str(final), mimetype="video/mp4", chunksize=-1, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return resp

    try:
        resp = attempt(True)
    except HttpError as e:
        print("retrying without AI flag:", e)
        resp = attempt(False)
    print("Uploaded: https://youtube.com/shorts/" + resp["id"])


# ---------------------------------------------------------------- main
def main():
    hist = load_history()
    story = make_story(hist)
    print("TITLE:", story["title"])
    style = random.choice(STYLES)
    voice = random.choice(VOICES)
    seed0 = random.randint(1, 10_000_000)
    clips = []
    for i, sc in enumerate(story["scenes"]):
        a = WORK / f"a{i}.mp3"
        raw = WORK / f"i{i}.jpg"
        cap = WORK / f"c{i}.png"
        out = WORK / f"s{i}.mp4"
        make_voice(sc["narration"], voice, a)
        prompt = (f"{style}, {story['character_look']}, {sc['visual']}, "
                  "dark horror atmosphere, moody lighting, vertical composition, "
                  "no text, no watermark, not gory")
        make_image(prompt, seed0, raw)  # same seed helps keep the look consistent
        add_caption(raw, sc["narration"], cap)
        scene_clip(cap, a, i, out)
        clips.append(out)
    # end screen: channel name + subscribe / like / comment / follow
    oa, oi, oc = WORK / "outro.mp3", WORK / "outro.png", WORK / "outro.mp4"
    make_voice(OUTRO_VOICE_LINE, voice, oa)
    make_outro_image(oi)
    scene_clip(oi, oa, len(clips), oc)
    clips.append(oc)
    final = WORK / "final.mp4"
    build_video(clips, final)
    print(f"Video ready: {final} ({audio_seconds(final):.1f}s)")
    if DRY_RUN:
        print("DRY RUN: not uploading.")
        return
    upload(final, story)
    hist.append(story["title"])
    save_history(hist)


if __name__ == "__main__":
    main()
    sys.exit(0)
