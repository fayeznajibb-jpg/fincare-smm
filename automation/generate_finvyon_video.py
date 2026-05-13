"""
FINVYON Story Video Generator
==============================
Standalone script. Generates 10 scene images via fal.ai, optionally
applies Kling motion to key scenes, then renders via Remotion.

Usage:
  cd automation
  python generate_finvyon_video.py

Requires: FAL_KEY in .env
Optional: Set KLING_SCENES to comma-separated scene numbers e.g. "1,5,10"
"""

import os
import sys
import json
import subprocess
import requests
from datetime import datetime
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

FAL_KEY           = os.getenv("FAL_KEY")
REMOTION_PROJECT  = Path.home() / "Downloads" / "fincare-video"
AI_BG_DIR         = REMOTION_PROJECT / "public" / "ai_bg"
OUTPUT_DIR        = REMOTION_PROJECT / "out"
KLING_SCENE_IDS   = {1, 5, 10}   # which scenes get Kling motion
BUDGET_MAX        = 1.50          # auto-downgrade Kling if over this
COST_IMAGE        = 0.04
COST_KLING        = 0.168


# ── Character Base Prompt (prepended to every scene) ─────────────────────────
CHARACTER_BASE = (
    "Refined black stickman character with a round white circle head, brushed-back grey hair, "
    "thick tortoiseshell glasses. He wears a tailored navy blue blazer over a light blue open-collar "
    "shirt, slim khaki trousers, and dark brown loafers. Minimalist flat 2D illustration, clean black "
    "outlines, white background, confident and calm vibe. No text, no labels."
)

# ── Scene Definitions ─────────────────────────────────────────────────────────
SCENES = [
    {
        "id":         1,
        "img_prompt": (
            "The stickman is standing next to a vintage leather-bound book titled only with a gold emboss "
            "(no readable text). He gestures toward it with a knowing smile. Clean white background. "
            "Flat illustration. 16:9 aspect ratio."
        ),
        "motion":     "Stickman slowly taps the book cover. His glasses catch a tiny glint of light.",
        "voiceover":  "Most people think wealth is about what you buy. True wealth is about what you don't spend.",
    },
    {
        "id":         2,
        "img_prompt": (
            "The stickman is holding a magnifying glass, looking closely at a small green sprout growing "
            "out of a crack in the floor. Focused expression. Clean white background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman leans in with the magnifying glass. His head moves slightly closer to the sprout.",
        "voiceover":  "It's a quiet game. It starts with a tiny seed of capital and a massive amount of patience.",
    },
    {
        "id":         3,
        "img_prompt": (
            "The stickman is standing behind a polished wooden podium, holding a red sports car key in one "
            "hand and a bank passbook in the other, comparing them like a scale. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman weighs the key in one hand and the passbook in the other, like a scale.",
        "voiceover":  "Being 'rich' is flashing a car key. Being 'wealthy' is the invisible freedom of a growing portfolio.",
    },
    {
        "id":         4,
        "img_prompt": (
            "The stickman is standing calmly in the middle of a storm of falling paper receipts. He holds "
            "a large black umbrella and looks perfectly calm. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman tilts the umbrella slightly as receipts fall around him. His body stays still.",
        "voiceover":  "The world will try to sell you a lifestyle you can't afford. Your job is to stay dry in the storm.",
    },
    {
        "id":         5,
        "img_prompt": (
            "The stickman is sitting in a high-back leather armchair, calmly sipping from a white porcelain "
            "teacup. A chaotic line graph is visible on a screen behind him, but he is unmoved. "
            "White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman slowly lowers the teacup from his mouth. Expression unchanged despite the graph.",
        "voiceover":  "In the stock market, your greatest asset isn't your brain — it's your stomach. Can you stay calm?",
    },
    {
        "id":         6,
        "img_prompt": (
            "The stickman is standing in a forest of tall digital trees, some glowing green, some red. "
            "He holds an old-fashioned brass compass, consulting it calmly. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman rotates the compass in his hand. His head scans the forest left to right.",
        "voiceover":  "Investing is a long walk through a forest of noise. You need a compass, not yesterday's map.",
    },
    {
        "id":         7,
        "img_prompt": (
            "The stickman is looking at a shimmering floating digital diamond (representing hype). He checks "
            "his gold wristwatch with an unimpressed expression. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman taps his watch face twice. Looks from the diamond back to his watch.",
        "voiceover":  "Hype and get-rich-quick coins are distractions. Wealthy people value time more than luck.",
    },
    {
        "id":         8,
        "img_prompt": (
            "The stickman is placing a single gold coin into a large glass jar that is already half-full of "
            "coins. He has a satisfied expression. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman's arm moves in a smooth arc to drop the coin. We see it settle at the top.",
        "voiceover":  "Compounding is the eighth wonder of the world. It doesn't need genius — just discipline.",
    },
    {
        "id":         9,
        "img_prompt": (
            "The stickman is standing next to a large healthy oak tree, leaning against it, reading a "
            "newspaper with a peaceful expression. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman turns a page of the newspaper. A gentle breeze moves his hair slightly.",
        "voiceover":  "Eventually you stop working for your money, and your money starts working for you.",
    },
    {
        "id":         10,
        "img_prompt": (
            "The stickman is holding up a golden key toward the viewer. He looks directly forward with an "
            "encouraging, dignified nod. White background. Flat illustration. 16:9."
        ),
        "motion":     "Stickman raises the key toward the viewer. A slow dignified wave follows.",
        "voiceover":  "Wealth isn't a number — it's the key to owning your time. Start your journey today.",
    },
]


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def generate_images() -> dict:
    """Generate all 10 scene images via fal.ai. Returns {scene_id: local_path}."""
    if not FAL_KEY:
        log("ERROR: FAL_KEY not set in .env")
        sys.exit(1)

    import fal_client
    os.environ["FAL_KEY"] = FAL_KEY
    AI_BG_DIR.mkdir(parents=True, exist_ok=True)

    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths  = {}

    for scene in SCENES:
        sid    = scene["id"]
        prompt = f"{CHARACTER_BASE} {scene['img_prompt']}"
        log(f"Scene {sid}/10 — generating image...")

        try:
            result = fal_client.run(
                "fal-ai/flux-pro/v1.1",
                arguments={
                    "prompt":           prompt,
                    "negative_prompt":  "photorealistic, 3D render, text, words, watermark, blurry, dark background",
                    "image_size":       "landscape_16_9",
                    "num_inference_steps": 28,
                    "guidance_scale":   3.5,
                    "num_images":       1,
                    "output_format":    "jpeg",
                    "enable_safety_checker": True,
                }
            )
            url      = result["images"][0]["url"]
            img_data = requests.get(url, timeout=30).content
            fname    = f"finvyon_scene{sid:02d}_{ts}.jpg"
            fpath    = AI_BG_DIR / fname
            fpath.write_bytes(img_data)

            size_kb = fpath.stat().st_size // 1024
            log(f"  ✓ Scene {sid} saved: {fname} ({size_kb}KB)")
            paths[sid] = {"abs": str(fpath), "rel": f"ai_bg/{fname}"}

        except Exception as e:
            log(f"  ✗ Scene {sid} failed ({type(e).__name__}: {str(e)[:60]})")
            paths[sid] = {"abs": None, "rel": "ai_bg/placeholder.jpg"}

    return paths, ts


def apply_kling(image_paths: dict, ts: str) -> dict:
    """Apply Kling motion to selected scenes. Returns {scene_id: rel_path}."""
    if not FAL_KEY:
        return {}

    import fal_client
    os.environ["FAL_KEY"] = FAL_KEY

    MOTION_BY_SCENE = {
        1:  "stickman slowly taps the book, gentle head movement, glasses glint",
        5:  "stickman slowly lowers teacup, calm unhurried movement, atmospheric stillness",
        10: "stickman raises golden key toward viewer, slow dignified wave, warm light",
    }

    total_cost = len(SCENES) * COST_IMAGE
    kling_ids  = []
    for sid in KLING_SCENE_IDS:
        if image_paths.get(sid, {}).get("abs"):
            total_cost += COST_KLING
            kling_ids.append(sid)

    if total_cost > BUDGET_MAX:
        log(f"Budget ${total_cost:.2f} > max ${BUDGET_MAX} — skipping Kling for scene 5")
        kling_ids = [sid for sid in kling_ids if sid != 5]

    log(f"Budget: ${total_cost:.2f} | Kling scenes: {kling_ids}")

    video_paths = {}
    for sid in kling_ids:
        abs_path = image_paths.get(sid, {}).get("abs")
        if not abs_path or not Path(abs_path).exists():
            continue

        motion = MOTION_BY_SCENE.get(sid, "slow cinematic drift")
        log(f"Kling — scene {sid}: uploading image...")

        try:
            with open(abs_path, "rb") as f:
                image_url = fal_client.upload(f.read(), "image/jpeg")

            log(f"Kling — scene {sid}: generating 5s clip...")
            result = fal_client.run(
                "fal-ai/kling-video/v1.6/standard/image-to-video",
                arguments={
                    "image_url":    image_url,
                    "prompt":       motion,
                    "duration":     "5",
                    "aspect_ratio": "16:9",
                }
            )
            video_url = result["video"]["url"]

            resp     = requests.get(video_url, timeout=120)
            out_name = f"finvyon_vid{sid:02d}_{ts}.mp4"
            out_path = AI_BG_DIR / out_name
            out_path.write_bytes(resp.content)

            size_kb = out_path.stat().st_size // 1024
            log(f"  ✓ Kling scene {sid} saved: {out_name} ({size_kb}KB)")
            video_paths[sid] = f"ai_bg/{out_name}"

        except Exception as e:
            log(f"  ✗ Kling scene {sid} failed ({type(e).__name__}: {str(e)[:60]}) — static fallback")

    return video_paths


def render(image_paths: dict, video_paths: dict, ts: str) -> str | None:
    """Build Remotion props and render FinvyonStoryVideo."""
    if not REMOTION_PROJECT.exists():
        log(f"Remotion project not found at {REMOTION_PROJECT}")
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    scenes_props = []
    for scene in SCENES:
        sid = scene["id"]
        entry = {
            "img":       image_paths.get(sid, {}).get("rel", "ai_bg/placeholder.jpg"),
            "voiceover": scene["voiceover"],
        }
        if sid in video_paths:
            entry["vid"] = video_paths[sid]
        scenes_props.append(entry)

    props     = {"scenes": scenes_props}
    out_file  = f"finvyon-story-{ts}.mp4"
    out_path  = OUTPUT_DIR / out_file

    log(f"Rendering FinvyonStoryVideo → {out_file}...")
    cmd = [
        "npx", "remotion", "render",
        "src/index.ts",
        "FinCare-Finvyon-Story-10",
        str(out_path),
        f"--props={json.dumps(props)}",
        "--concurrency=2",
        "--log=error",
        "--jpeg-quality=85",
    ]

    try:
        result = subprocess.run(cmd, cwd=REMOTION_PROJECT, capture_output=True, text=True, timeout=600)
        if result.returncode == 0 and out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            log(f"✓ Rendered: {out_file} ({size_mb:.1f}MB)")
            return str(out_path)
        else:
            log(f"✗ Render failed:\n{result.stderr[:400]}")
            return None
    except subprocess.TimeoutExpired:
        log("✗ Render timeout (>10min)")
        return None
    except FileNotFoundError:
        log("✗ npx not found — run: brew install node")
        return None


if __name__ == "__main__":
    log("=== FINVYON Story Video Generator ===")
    log(f"Scenes: {len(SCENES)} | Kling: {KLING_SCENE_IDS}")

    log("\n--- STEP 1: Generate scene images ---")
    image_paths, ts = generate_images()

    log("\n--- STEP 2: Apply Kling motion ---")
    video_paths = apply_kling(image_paths, ts)

    log("\n--- STEP 3: Render with Remotion ---")
    output = render(image_paths, video_paths, ts)

    if output:
        log(f"\n✅ Done! Video: {output}")
    else:
        log("\n⚠️  Render skipped — images saved to fincare-video/public/ai_bg/")
        log("   Open Remotion Studio: cd ~/Downloads/fincare-video && npx remotion studio")
