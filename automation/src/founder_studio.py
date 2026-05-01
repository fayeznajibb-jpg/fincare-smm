"""
Fincare Founder Studio
======================
Processes raw founder videos sent via Telegram into polished, branded content.

Supports two modes:
  Mode 1 — Automated: agent writes script, generates voiceover (XTTS or Edge TTS),
            renders Remotion video, delivers to Telegram.

  Mode 2 — Founder Edit: Fayez records himself on camera (any language),
            sends raw video to Telegram bot, agent:
              1. Downloads the raw video
              2. Detects language automatically
              3. Enhances audio to studio quality (noise reduction, compression, EQ)
              4. Generates transcript via Whisper (local, free)
              5. Creates timed text overlays, chart overlays, lower thirds
              6. Renders final video with Remotion or ffmpeg compositing
              7. Sends finished video back to Telegram for approval

Language support:
  English  — XTTS v2 voice clone (Mode 1), ffmpeg audio enhancement (Mode 2)
  Spanish  — XTTS v2 voice clone (Mode 1), ffmpeg audio enhancement (Mode 2)
  Persian  — Microsoft Edge TTS (Mode 1), ffmpeg audio enhancement (Mode 2)
  Any lang — Mode 2 always works (audio enhancement + overlays)
"""

import os
import json
import subprocess
import asyncio
from datetime import datetime
import anthropic
from utils.logger import SecureLogger

logger = SecureLogger("founder_studio")

DRAFTS_DIR    = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "drafts"))
FOUNDER_DIR   = os.path.join(DRAFTS_DIR, "founder_videos")
CHARTS_DIR    = os.path.join(DRAFTS_DIR, "charts")

# Language config for Edge TTS (Microsoft, free, no API key)
EDGE_TTS_VOICES = {
    "fa":  "fa-IR-DilaraNeural",    # Persian — natural female voice
    "es":  "es-ES-ElviraNeural",    # Spanish — natural female voice
    "en":  "en-US-AndrewNeural",    # English — natural male voice (fallback)
    "ar":  "ar-AE-FatimaNeural",    # Arabic
}

# Supported languages for Coqui XTTS v2 voice cloning
XTTS_SUPPORTED_LANGS = {"en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl", "cs", "ar", "zh-cn", "ja", "ko", "hu"}

# ─── Audio Enhancement ────────────────────────────────────────────────────────

def skill_enhance_audio(input_path: str, timestamp: str | None = None) -> str:
    """
    Applies studio-quality audio enhancement to a raw video or audio file.

    Pipeline (all via ffmpeg, no external services):
      1. highpass=80Hz    — remove low-end rumble (phone vibrations, desk bumps)
      2. bandreject 50Hz  — kill electrical hum (common in indoor recordings)
      3. bandreject 100Hz — kill 2nd harmonic of electrical hum
      4. afftdn nf=-30    — aggressive FFT broadband denoising (fan, AC, room noise)
      5. anlmdn           — smooth residual noise left after FFT pass
      6. agate            — noise gate: silences mic between sentences (no background bleed)
      7. lowpass=12kHz    — remove high-freq hiss and phone compression artefacts
      8. acompressor      — dynamic range control (reduces peaks, raises floor)
      9. equalizer -200Hz — cut muddiness in low-mids
     10. equalizer +3kHz  — presence boost for voice clarity
     11. equalizer -8kHz  — tame harshness/sibilance
     12. loudnorm         — broadcast-standard LUFS-14 (Spotify/YouTube standard)

    The noise gate (step 6) is the most impactful new addition — it completely
    kills background hum between sentences instead of just reducing it.

    Works for any language — purely audio signal processing.
    Returns path to enhanced file (same container as input).
    """
    if not os.path.exists(input_path):
        logger.error(f"Input not found: {input_path}")
        return input_path

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(input_path)[1] or ".mp4"
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = os.path.join(FOUNDER_DIR, f"{base}_studio{ext}")
    os.makedirs(FOUNDER_DIR, exist_ok=True)

    # Build audio filter chain — order matters for quality
    audio_filter = ",".join([
        # ── Phase 1: Remove noise before processing voice ──────────────────────
        "highpass=f=80",                              # cut sub-bass rumble
        "bandreject=f=50:width_type=q:width=10",      # kill 50Hz electrical hum (EU/Asia)
        "bandreject=f=60:width_type=q:width=10",      # kill 60Hz electrical hum (US/Americas)
        "bandreject=f=100:width_type=q:width=8",      # kill 2nd harmonic (100Hz)
        "bandreject=f=120:width_type=q:width=8",      # kill 2nd harmonic (120Hz US)
        # ── Phase 2: Spectral denoising ────────────────────────────────────────
        "afftdn=nf=-30:nr=33:nt=w",                   # FFT denoise: nf=-30dB floor, nr=33% reduction, nt=w (white noise model)
        "anlmdn=s=7:p=0.002:r=0.005:m=15",            # non-local means: smooth residuals
        # ── Phase 3: Noise gate (kills silence between sentences) ──────────────
        "agate=threshold=-42dB:ratio=5:attack=5:release=200:makeup=0dB",
        # ── Phase 4: Shape the frequency response for voice ───────────────────
        "lowpass=f=12000",                             # remove hiss above 12kHz
        "equalizer=f=200:t=o:width=2:g=-3",           # cut muddiness
        "equalizer=f=3000:t=o:width=2:g=3",           # presence/clarity boost
        "equalizer=f=8000:t=o:width=1:g=-2",          # tame harshness
        # ── Phase 5: Dynamics & loudness ──────────────────────────────────────
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=100:makeup=3dB",
        "loudnorm=I=-14:TP=-2:LRA=11",                # broadcast standard (always last)
    ])

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", audio_filter,
        "-c:v", "copy",       # keep video stream untouched
        "-c:a", "aac", "-b:a", "192k",
        out_path,
    ]

    logger.step(f"Enhancing audio: {os.path.basename(input_path)}...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(out_path):
            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            logger.success(f"Studio audio: {os.path.basename(out_path)} ({size_mb:.1f}MB)")
            return out_path
        else:
            logger.error(f"Audio enhancement failed: {result.stderr[:200]}")
            # Retry with simpler chain if complex chain fails (older ffmpeg)
            logger.info("Retrying with simplified noise chain...")
            simple_filter = (
                "highpass=f=80,"
                "afftdn=nf=-30,"
                "agate=threshold=-42dB:ratio=5:attack=5:release=200,"
                "lowpass=f=12000,"
                "acompressor=threshold=-18dB:ratio=3:attack=5:release=100:makeup=3dB,"
                "loudnorm=I=-14:TP=-2:LRA=11"
            )
            cmd[cmd.index(audio_filter)] = simple_filter
            result2 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result2.returncode == 0 and os.path.exists(out_path):
                logger.success(f"Studio audio (simplified): {os.path.basename(out_path)}")
                return out_path
            logger.error(f"Audio enhancement failed (simplified): {result2.stderr[:200]}")
            return input_path
    except Exception as e:
        logger.error(f"Audio enhancement error: {type(e).__name__}: {str(e)[:100]}")
        return input_path


# ─── Language Detection ───────────────────────────────────────────────────────

def skill_detect_language(video_path: str) -> str:
    """
    Detects spoken language in a video using Whisper (local, free).
    Falls back to 'en' if Whisper not installed.
    Returns ISO language code: 'en', 'fa', 'es', etc.
    """
    try:
        import whisper
        logger.step("Detecting language via Whisper...")
        model = whisper.load_model("tiny")   # tiny = fast, ~75MB

        # Extract a 30s audio sample for detection
        sample_path = os.path.join(DRAFTS_DIR, "_lang_sample.wav")
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            "-t", "30",   # first 30 seconds is enough
            sample_path,
        ], capture_output=True, timeout=60)

        if os.path.exists(sample_path):
            result = model.transcribe(sample_path, task="detect-language")
            lang = result.get("language", "en")
            logger.success(f"Language detected: {lang}")
            os.remove(sample_path)
            return lang
    except ImportError:
        logger.info("Whisper not installed — defaulting to 'en'. Install: pip install openai-whisper")
    except Exception as e:
        logger.warning(f"Language detection failed: {type(e).__name__} — defaulting to 'en'")
    return "en"


def skill_transcribe(video_path: str, language: str = "en") -> list[dict]:
    """
    Transcribes speech from a video using Whisper with word-level timestamps.
    Returns list of segments: [{"start": 1.2, "end": 2.4, "text": "Hello world"}]
    Falls back to empty list if Whisper not available.
    """
    try:
        import whisper
        logger.step(f"Transcribing video ({language})...")
        model = whisper.load_model("base")   # base = good accuracy, ~150MB

        # Extract audio for transcription
        audio_path = os.path.join(DRAFTS_DIR, "_transcribe_audio.wav")
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            audio_path,
        ], capture_output=True, timeout=120)

        if not os.path.exists(audio_path):
            return []

        result = model.transcribe(audio_path, language=language, word_timestamps=True)
        segments = [
            {"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
            for seg in result.get("segments", [])
        ]
        os.remove(audio_path)
        logger.success(f"Transcribed {len(segments)} segments")
        return segments
    except ImportError:
        logger.info("Whisper not installed — no transcript. Install: pip install openai-whisper")
        return []
    except Exception as e:
        logger.warning(f"Transcription failed: {type(e).__name__}")
        return []


# ─── Chart Data ───────────────────────────────────────────────────────────────

def skill_fetch_chart_data(ticker: str, period: str = "1mo") -> dict | None:
    """
    Fetches real OHLC stock/index price data from Yahoo Finance.
    Free, no API key required.

    ticker: "AAPL", "SPY", "^GSPC" (S&P 500), "^VIX", "BTC-USD"
    period: "1d", "5d", "1mo", "3mo", "6mo", "1y"

    Returns dict with:
      ticker, period, dates, closes, opens, highs, lows,
      change_pct (total % change over period),
      trend ("up" | "down" | "flat"),
      peak_date, trough_date
    """
    try:
        import yfinance as yf
        logger.step(f"Fetching chart data: {ticker} ({period})...")
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)

        if data.empty:
            logger.warning(f"No data returned for {ticker}")
            return None

        # yfinance ≥0.2 returns MultiIndex columns — flatten to Series
        def _col(name):
            col = data[name]
            if hasattr(col, "squeeze"):
                col = col.squeeze()
            return col.round(2).dropna().tolist()

        closes = _col("Close")
        opens  = _col("Open")
        highs  = _col("High")
        lows   = _col("Low")
        dates  = [str(d.date()) for d in data.index[:len(closes)]]

        first, last = closes[0], closes[-1]
        change_pct = round(((last - first) / first) * 100, 2) if first else 0

        trend = "up" if change_pct > 1 else ("down" if change_pct < -1 else "flat")

        peak_idx   = closes.index(max(closes))
        trough_idx = closes.index(min(closes))

        result = {
            "ticker":      ticker,
            "period":      period,
            "dates":       dates,
            "closes":      closes,
            "opens":       opens,
            "highs":       highs,
            "lows":        lows,
            "current":     last,
            "change_pct":  change_pct,
            "trend":       trend,
            "peak_date":   dates[peak_idx],
            "peak_price":  closes[peak_idx],
            "trough_date": dates[trough_idx],
            "trough_price": closes[trough_idx],
        }
        logger.success(f"{ticker}: {change_pct:+.2f}% ({period}) — trend: {trend}")
        return result
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return None
    except Exception as e:
        logger.error(f"Chart fetch failed ({ticker}): {type(e).__name__}: {str(e)[:100]}")
        return None


def skill_render_chart_png(chart_data: dict, style: str = "terminal") -> str | None:
    """
    Renders a stock chart as a PNG image for video overlay.

    Styles:
      terminal  — dark bg, neon green line, Bloomberg feel
      clean     — white bg, blue line, minimal
      crash     — dark bg, red line, dramatic crash highlight

    Returns path to PNG, or None on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")   # non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        logger.error("matplotlib not installed. Run: pip install matplotlib")
        return None

    os.makedirs(CHARTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(CHARTS_DIR, f"chart_{chart_data['ticker']}_{ts}.png")

    closes = chart_data["closes"]
    dates  = chart_data["dates"]
    ticker = chart_data["ticker"]
    change = chart_data["change_pct"]
    x = list(range(len(closes)))

    # ── Style configuration ──────────────────────────────────────────────────
    if style == "terminal":
        bg, line_color, text_color, grid_color = "#0d0f14", "#00ff88", "#ffffff", "#1a2030"
        fig_size = (8, 4)
    elif style == "crash":
        bg, line_color, text_color, grid_color = "#0d0f14", "#ff4444", "#ffffff", "#1a2030"
        fig_size = (8, 4)
    else:  # clean
        bg, line_color, text_color, grid_color = "#ffffff", "#2563eb", "#111111", "#e5e7eb"
        fig_size = (8, 4)

    fig, ax = plt.subplots(figsize=fig_size, facecolor=bg)
    ax.set_facecolor(bg)

    # Draw chart line with gradient fill
    ax.plot(x, closes, color=line_color, linewidth=2.5, zorder=3)
    ax.fill_between(x, closes, min(closes), alpha=0.15, color=line_color)

    # Highlight peak/trough
    peak_idx   = closes.index(max(closes))
    trough_idx = closes.index(min(closes))
    ax.scatter([peak_idx],   [closes[peak_idx]],   color="#ffd700", s=60, zorder=5)
    ax.scatter([trough_idx], [closes[trough_idx]], color="#ff4444", s=60, zorder=5)

    # Ticker + change label
    change_sign = "+" if change >= 0 else ""
    color_label = "#00ff88" if change >= 0 else "#ff4444"
    ax.text(0.02, 0.92, ticker, transform=ax.transAxes,
            color=text_color, fontsize=14, fontweight="bold")
    ax.text(0.02, 0.78, f"{change_sign}{change}%", transform=ax.transAxes,
            color=color_label, fontsize=12, fontweight="bold")

    # Date labels (first and last only)
    ax.set_xticks([0, len(x) - 1])
    ax.set_xticklabels([dates[0], dates[-1]], color=text_color, fontsize=8)
    ax.tick_params(axis="y", colors=text_color, labelsize=8)
    ax.grid(True, color=grid_color, linewidth=0.5, linestyle="--", alpha=0.5)

    for spine in ax.spines.values():
        spine.set_visible(False)

    plt.tight_layout(pad=0.5)
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    logger.success(f"Chart rendered: {os.path.basename(out_path)}")
    return out_path


# ─── Manim Animated Chart ─────────────────────────────────────────────────────

def skill_render_manim_chart(chart_data: dict, style: str = "terminal") -> str | None:
    """
    Renders an ANIMATED chart video using Manim (3Blue1Brown animation engine).
    The chart line draws itself from left to right with easing, price labels
    count up, and peak/trough dots pulse on screen.

    Much more cinematic than a static matplotlib PNG — use this for Format A/B/E
    when the chart is the main visual.

    Styles:
      terminal — dark bg, neon green, Bloomberg feel
      crash    — dark bg, red line, dramatic crash highlight
      clean    — white bg, blue line, minimal

    Returns path to rendered MP4 (~5s), or None if Manim not installed.
    """
    try:
        import manim  # noqa — just check it's installed
    except ImportError:
        logger.warning("Manim not installed — falling back to static PNG. Run: pip install manim")
        return None

    os.makedirs(CHARTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_path = os.path.join(CHARTS_DIR, f"manim_chart_{ts}.py")
    out_dir     = os.path.join(CHARTS_DIR, f"manim_out_{ts}")

    closes  = chart_data["closes"]
    ticker  = chart_data["ticker"]
    change  = chart_data["change_pct"]
    change_sign = "+" if change >= 0 else ""

    # Style params
    if style == "crash":
        bg_hex, line_hex, label_hex = "#0d0f14", "#ff4444", "#ffffff"
    elif style == "clean":
        bg_hex, line_hex, label_hex = "#f8f9fa", "#2563eb", "#111111"
    else:  # terminal
        bg_hex, line_hex, label_hex = "#0d0f14", "#00ff88", "#ffffff"

    # Normalise closes to a 0–1 range for Manim axes
    min_c = min(closes)
    max_c = max(closes)
    rng   = max_c - min_c or 1
    norm  = [(c - min_c) / rng for c in closes]

    # Write the Manim scene script
    manim_script = f'''
from manim import *

class ChartScene(Scene):
    def construct(self):
        self.camera.background_color = "{bg_hex}"

        closes_norm = {norm}
        n = len(closes_norm)
        w, h = 10, 4

        # Build points (left to right across screen)
        points = [
            np.array([-w/2 + i * w / (n-1), -h/2 + v * h, 0])
            for i, v in enumerate(closes_norm)
        ]

        # Chart line — draws itself
        line = VMobject()
        line.set_points_as_corners(points)
        line.set_stroke(color="{line_hex}", width=3)

        # Fill area under line
        fill_pts = [points[0]] + points + [points[-1] + DOWN * h, points[0] + DOWN * h]
        fill = VMobject()
        fill.set_points_as_corners(fill_pts)
        fill.set_fill(color="{line_hex}", opacity=0.12)
        fill.set_stroke(width=0)

        # Peak and trough dots
        peak_i   = closes_norm.index(max(closes_norm))
        trough_i = closes_norm.index(min(closes_norm))
        peak_dot   = Dot(points[peak_i],   color=YELLOW, radius=0.08)
        trough_dot = Dot(points[trough_i], color=RED,    radius=0.08)

        # Title label
        title = Text("{ticker}  {change_sign}{change:.1f}%",
                     color="{label_hex}", font_size=32, weight=BOLD)
        title.to_corner(UL, buff=0.4)

        # Animate
        self.play(Create(fill), run_time=0.5)
        self.play(Create(line), run_time=2.0, rate_func=ease_out_cubic)
        self.play(
            FadeIn(peak_dot, scale=2),
            FadeIn(trough_dot, scale=2),
            Write(title),
            run_time=0.8,
        )
        self.wait(0.8)
'''

    with open(script_path, "w") as f:
        f.write(manim_script)

    out_video = os.path.join(out_dir, "ChartScene.mp4")
    cmd = [
        "manim", script_path, "ChartScene",
        "--format=mp4",
        "--media_dir", out_dir,
        "--resolution", "1080,1920",
        "--fps", "30",
        "-q", "m",         # medium quality (fast render)
        "--disable_caching",
    ]

    logger.step(f"Manim: rendering animated {ticker} chart ({style})...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # Manim puts output in media/videos/<scene_name>/
        import glob
        matches = glob.glob(os.path.join(out_dir, "**", "ChartScene.mp4"), recursive=True)
        if matches:
            final = os.path.join(CHARTS_DIR, f"manim_{ticker}_{ts}.mp4")
            os.rename(matches[0], final)
            logger.success(f"Manim chart done: {os.path.basename(final)}")
            return final
        else:
            logger.error(f"Manim render failed: {result.stderr[:200]}")
            return None
    except Exception as e:
        logger.error(f"Manim error: {type(e).__name__}: {str(e)[:100]}")
        return None


# ─── Creative Director ────────────────────────────────────────────────────────

def skill_select_graphics(props: dict, video_format: str, chart_data: dict | None = None) -> dict:
    """
    Claude Sonnet acts as creative director — picks the right graphic treatment
    for each video based on content, format, and available data.

    Returns VisualDirections dict consumed by the rendering pipeline.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _default_visual_directions(video_format, chart_data)

    hook    = props.get("hook", "")
    insight = props.get("insight", "")
    trigger = props.get("trigger", "anxiety")
    pillar  = props.get("pillar", "STORY")
    fmt     = video_format

    chart_ctx = ""
    if chart_data:
        chart_ctx = (
            f"\nAvailable chart data:\n"
            f"  Ticker: {chart_data['ticker']}\n"
            f"  Change: {chart_data['change_pct']:+.1f}% over {chart_data['period']}\n"
            f"  Trend: {chart_data['trend']}\n"
        )

    prompt = f"""You are the creative director for AIFinCare TikTok/Reels videos.
Select the best graphic treatment for this video.

Video format: {fmt} ({'Daily Brief' if fmt=='A' else 'AI Reveal' if fmt=='B' else 'Explainer' if fmt=='D' else 'News Reaction'})
Hook: {hook}
Insight: {insight}
Emotional trigger: {trigger}
Content pillar: {pillar}
{chart_ctx}

Available graphic styles:
- text_style: "kinetic" (word-by-word animation) | "lower_third" (TV news style) | "split_screen" (vs comparison) | "ticker" (news scroll)
- chart_style: "terminal" (Bloomberg dark neon) | "clean" (minimal white) | "crash" (red dramatic) | null (no chart)
- overlay_style: "phone_mockup" (show app UI) | "data_burst" (stats pop in) | "breaking" (news banner) | "scan" (AI scanning)

Rules:
- DATA format → always include chart + terminal style
- NEWS format → breaking banner + ticker scroll
- STORY format → kinetic text + phone_mockup
- EXPLAINER → split_screen comparison
- If chart trend is "down" → use crash style
- Always include founder lower third
- Return JSON only, no explanation

Return ONLY this JSON:
{{
  "text_style": "kinetic|lower_third|split_screen|ticker",
  "chart_style": "terminal|clean|crash|null",
  "overlay_style": "phone_mockup|data_burst|breaking|scan",
  "show_chart": true/false,
  "lower_third_name": "Fayez Najib",
  "lower_third_title": "Founder, AIFinCare",
  "accent_color": "#hex — matches emotional trigger",
  "direction": "brief creative note on the visual tone, 1 sentence"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        directions = json.loads(raw)
        logger.success(f"Visual directions: {directions.get('direction', '')[:60]}")
        return directions
    except Exception as e:
        logger.warning(f"Creative director failed ({type(e).__name__}) — using defaults")
        return _default_visual_directions(video_format, chart_data)


def _default_visual_directions(video_format: str, chart_data: dict | None) -> dict:
    """Fallback visual directions when Claude Sonnet is unavailable."""
    defaults = {
        "A": {"text_style": "lower_third", "chart_style": "terminal", "overlay_style": "data_burst",
              "show_chart": True},
        "B": {"text_style": "kinetic",     "chart_style": "clean",    "overlay_style": "phone_mockup",
              "show_chart": bool(chart_data)},
        "D": {"text_style": "split_screen","chart_style": "clean",    "overlay_style": "data_burst",
              "show_chart": False},
        "E": {"text_style": "ticker",      "chart_style": "crash",    "overlay_style": "breaking",
              "show_chart": bool(chart_data)},
    }
    d = defaults.get(video_format, defaults["B"])
    d.update({
        "lower_third_name":  "Fayez Najib",
        "lower_third_title": "Founder, AIFinCare",
        "accent_color":      "#00ff88",
        "direction":         "Default visual treatment",
    })
    return d


# ─── Mode 1: Auto Narration ───────────────────────────────────────────────────

def skill_generate_narration(script: str, language: str = "en", voice_ref: str | None = None,
                              timestamp: str | None = None) -> str | None:
    """
    Generates narration audio for Mode 1 (automated):

    - English/Spanish + voice_ref → XTTS v2 cloned voice (local, free)
    - Persian (fa) or no voice_ref → Microsoft Edge TTS (free, online)
    - Any other language → Edge TTS with matching voice

    Returns path to generated audio file, or None on failure.
    """
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(FOUNDER_DIR, exist_ok=True)

    clean_script = script.replace("[PAUSE]", "... ")

    # ── Try XTTS v2 if language is supported and voice_ref exists ────────────
    if language in XTTS_SUPPORTED_LANGS and voice_ref and os.path.exists(voice_ref):
        try:
            from TTS.api import TTS as CoquiTTS
            logger.step(f"Generating XTTS narration ({language}, cloned voice)...")
            out_path = os.path.join(FOUNDER_DIR, f"narration_{ts}.wav")
            tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")
            tts.tts_to_file(text=clean_script, speaker_wav=voice_ref,
                            language=language, file_path=out_path)
            if os.path.exists(out_path):
                logger.success(f"XTTS narration: {os.path.basename(out_path)}")
                return out_path
        except ImportError:
            logger.info("XTTS not available — falling back to Edge TTS")
        except Exception as e:
            logger.warning(f"XTTS failed ({type(e).__name__}) — falling back to Edge TTS")

    # ── Edge TTS fallback (Persian, Arabic, or when XTTS unavailable) ────────
    voice = EDGE_TTS_VOICES.get(language, EDGE_TTS_VOICES["en"])
    out_path = os.path.join(FOUNDER_DIR, f"narration_{ts}.mp3")
    logger.step(f"Generating Edge TTS narration ({language}, voice={voice})...")
    try:
        async def _run():
            import edge_tts
            communicate = edge_tts.Communicate(clean_script, voice)
            await communicate.save(out_path)

        asyncio.run(_run())
        if os.path.exists(out_path):
            size_kb = os.path.getsize(out_path) // 1024
            logger.success(f"Edge TTS narration: {os.path.basename(out_path)} ({size_kb}KB)")
            return out_path
    except Exception as e:
        logger.error(f"Edge TTS failed: {type(e).__name__}: {str(e)[:100]}")
    return None


# ─── Mode 2: Founder Video Edit ───────────────────────────────────────────────

def skill_add_text_overlay(video_path: str, segments: list[dict], directions: dict,
                            language: str = "en", timestamp: str | None = None) -> str:
    """
    Adds timed text overlays to a founder video using ffmpeg drawtext filter.

    For each transcript segment, animates the text on screen (fade in/out).
    Handles RTL text (Persian, Arabic) automatically via drawtext direction.

    Returns path to video with overlays, or original on failure.
    """
    if not segments:
        logger.info("No segments for text overlay — skipping.")
        return video_path

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(FOUNDER_DIR, f"{base}_overlay{ts}.mp4")

    accent = directions.get("accent_color", "#00ff88").lstrip("#")
    # Convert hex to RGB for ffmpeg
    r = int(accent[0:2], 16)
    g = int(accent[2:4], 16)
    b = int(accent[4:6], 16)
    text_color = f"#{accent}"

    # Build drawtext filters for each segment
    # Show max 8 words per segment to keep it readable
    vf_parts = []
    for seg in segments[:20]:   # cap at 20 segments to stay within ffmpeg arg limits
        text = seg["text"].strip()[:60].replace("'", "\\'").replace(":", "\\:")
        start = seg["start"]
        end   = min(seg["end"], seg["start"] + 4.0)   # max 4s per caption
        duration = end - start

        vf_parts.append(
            f"drawtext=text='{text}':fontcolor=white:fontsize=36:fontweight=bold:"
            f"box=1:boxcolor=black@0.6:boxborderw=8:"
            f"x=(w-text_w)/2:y=h*0.82:"
            f"enable='between(t,{start:.2f},{end:.2f})':"
            f"alpha='if(lt(t,{start:.2f}+0.2),((t-{start:.2f})/0.2),if(gt(t,{end:.2f}-0.2),(({end:.2f}-t)/0.2),1))'"
        )

    if not vf_parts:
        return video_path

    vf_filter = ",".join(vf_parts)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf_filter,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "copy",
        out_path,
    ]

    logger.step(f"Adding text overlays ({len(vf_parts)} captions)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            logger.success(f"Text overlays added: {os.path.basename(out_path)}")
            return out_path
        else:
            logger.error(f"Overlay failed: {result.stderr[:200]}")
            return video_path
    except Exception as e:
        logger.error(f"Overlay error: {type(e).__name__}")
        return video_path


def skill_add_lower_third(video_path: str, name: str, title: str,
                           accent_color: str = "#00ff88",
                           timestamp: str | None = None) -> str:
    """
    Adds a branded lower third (name + title) at the start of the video (5–10s).
    Slides up from bottom with a colored accent bar.

    Returns path to video with lower third, or original on failure.
    """
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(FOUNDER_DIR, f"{base}_lt{ts}.mp4")

    name_safe  = name.replace("'", "\\'").replace(":", "\\:")
    title_safe = title.replace("'", "\\'").replace(":", "\\:")
    accent = accent_color.lstrip("#")
    r, g, b = int(accent[0:2], 16), int(accent[2:4], 16), int(accent[4:6], 16)

    # Lower third: shows from t=3s to t=9s, slides up, fades
    vf = (
        # Accent bar
        f"drawbox=x=60:y=h-180:w=w-120:h=4:color=#{accent}:t=fill:"
        f"enable='between(t,3,9)',"
        # Name (bold)
        f"drawtext=text='{name_safe}':fontcolor=white:fontsize=40:fontweight=bold:"
        f"x=60:y=h-168:enable='between(t,3,9)',"
        # Title (smaller, accent color)
        f"drawtext=text='{title_safe}':fontcolor=#{accent}:fontsize=28:"
        f"x=60:y=h-120:enable='between(t,3,9)'"
    )

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "copy",
        out_path,
    ]

    logger.step("Adding lower third...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            logger.success(f"Lower third added: {os.path.basename(out_path)}")
            return out_path
        else:
            logger.error(f"Lower third failed: {result.stderr[:150]}")
            return video_path
    except Exception as e:
        logger.error(f"Lower third error: {type(e).__name__}")
        return video_path


def skill_overlay_chart(video_path: str, chart_asset: str, position: str = "top_right",
                         start_sec: float = 8.0, duration_sec: float = 10.0,
                         timestamp: str | None = None) -> str:
    """
    Overlays a chart (PNG or animated MP4 from Manim) onto the video at a specific
    position and time window.

    Accepts both:
      - Static PNG (matplotlib) — scaled and overlaid as image
      - Animated MP4 (Manim)   — composited as video layer (much more cinematic)

    position: "top_right" | "top_left" | "bottom_right" | "full"
    Returns path to video with chart overlay, or original on failure.
    """
    if not os.path.exists(chart_asset):
        logger.warning(f"Chart asset not found: {chart_asset}")
        return video_path

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(FOUNDER_DIR, f"{base}_chart{ts}.mp4")

    # Scale chart to appropriate size for overlay
    scale_map = {
        "top_right":    "iw/3:ih/4",
        "top_left":     "iw/3:ih/4",
        "bottom_right": "iw/3:ih/4",
        "full":         "iw:ih",
    }
    pos_map = {
        "top_right":    "main_w-overlay_w-20:20",
        "top_left":     "20:20",
        "bottom_right": "main_w-overlay_w-20:main_h-overlay_h-200",
        "full":         "0:0",
    }

    scale   = scale_map.get(position, "iw/3:ih/4")
    xy      = pos_map.get(position, "main_w-overlay_w-20:20")
    end_sec = start_sec + duration_sec
    is_video = chart_asset.lower().endswith((".mp4", ".mov", ".webm"))

    if is_video:
        # For animated Manim charts — loop the chart video within the overlay window
        filter_complex = (
            f"[1:v]scale={scale},setpts=PTS-STARTPTS[chart];"
            f"[0:v][chart]overlay={xy}:enable='between(t,{start_sec},{end_sec})'"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-stream_loop", "-1", "-i", chart_asset,  # loop chart video
            "-filter_complex", filter_complex,
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-map", "0:a",   # keep main video audio only
            "-shortest",
            out_path,
        ]
    else:
        # Static PNG path
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", chart_asset,
            "-filter_complex",
            f"[1:v]scale={scale}[chart];"
            f"[0:v][chart]overlay={xy}:"
            f"enable='between(t,{start_sec},{end_sec})'",
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "copy",
            out_path,
        ]

    logger.step(f"Overlaying chart at {position} ({start_sec}–{end_sec}s)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            logger.success(f"Chart overlay done: {os.path.basename(out_path)}")
            return out_path
        else:
            logger.error(f"Chart overlay failed: {result.stderr[:150]}")
            return video_path
    except Exception as e:
        logger.error(f"Chart overlay error: {type(e).__name__}")
        return video_path


# ─── Crop to 9:16 ────────────────────────────────────────────────────────────

def skill_crop_to_916(video_path: str, timestamp: str | None = None) -> str:
    """
    Crops/scales any video to 9:16 (1080×1920) for TikTok and Instagram Reels.

    Strategy:
      - Already 9:16 → skip (no re-encode, return as-is)
      - Portrait (taller than wide) → scale height to 1920, crop width to 1080
      - Landscape (wider than tall) → scale height to 1920, crop width to center 1080px
        (left/right edges cut — standard TikTok behaviour)

    Returns path to 9:16 video, or original if already correct / on failure.
    """
    if not os.path.exists(video_path):
        return video_path

    # Probe dimensions
    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            video_path,
        ], capture_output=True, text=True, timeout=15)
        dims = probe.stdout.strip().split(",")
        w, h = int(dims[0]), int(dims[1])
    except Exception:
        logger.warning("crop_to_916: could not probe dimensions — skipping crop")
        return video_path

    # Already 9:16?
    if w == 1080 and h == 1920:
        logger.info("crop_to_916: already 9:16 — skipping")
        return video_path

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(FOUNDER_DIR, f"{base}_916.mp4")
    os.makedirs(FOUNDER_DIR, exist_ok=True)

    # scale to cover 1080×1920, then crop to exact size
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        out_path,
    ]
    logger.step(f"Cropping {w}×{h} → 1080×1920 (9:16)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and os.path.exists(out_path):
            logger.success(f"Cropped to 9:16: {os.path.basename(out_path)}")
            return out_path
        else:
            logger.error(f"Crop failed: {result.stderr[:150]}")
            return video_path
    except Exception as e:
        logger.error(f"Crop error: {type(e).__name__}")
        return video_path


# ─── Caption Instructions Parser ──────────────────────────────────────────────

def skill_parse_caption_instructions(caption: str) -> dict:
    """
    Uses Claude Haiku to understand free-form editing instructions from the
    video caption Fayez writes when sending a video to the bot.

    Examples:
      "SPY fa more energy"          → ticker=SPY, language=fa, energy_level=high
      "no captions pls"             → show_captions=False
      "cut first 5 seconds"         → trim_start=5
      "use red color"               → accent_color=#ff4444
      "make it dramatic"            → accent_color=#ff4444, energy_level=high
      "no lower third"              → show_lower_third=False
      "pip bottom_left"             → pip=True, pip_position=bottom_left
      "pip SPY"                     → pip=True, ticker=SPY
      "just clean audio"            → show_captions=False, show_chart=False, show_lower_third=False

    Returns EditInstructions dict with defaults for anything not mentioned.
    """
    # Defaults
    defaults = {
        "ticker":           None,
        "language":         None,
        "show_captions":    True,
        "show_lower_third": True,
        "show_chart":       None,   # None = auto (True if ticker given)
        "accent_color":     None,   # None = let creative director decide
        "trim_start":       0.0,
        "trim_end":         0.0,
        "energy_level":     "normal",
        "pip":              False,
        "pip_position":     "bottom_right",
        "extra_notes":      "",
    }

    if not caption or not caption.strip():
        return defaults

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback: manual keyword scan
        return _parse_caption_manual(caption, defaults)

    prompt = f"""Extract editing instructions from this video caption written by a social media founder.
Caption: "{caption}"

Return ONLY a JSON object with these fields (use null for anything not mentioned):
{{
  "ticker": "stock ticker like SPY/AAPL/TSLA or null",
  "language": "language code: en/fa/es/ar or null",
  "show_captions": true or false (default true),
  "show_lower_third": true or false (default true),
  "show_chart": true or false or null (null=auto),
  "accent_color": "#hex color or null",
  "trim_start": seconds to cut from start as float (0 if not mentioned),
  "trim_end": seconds to cut from end as float (0 if not mentioned),
  "energy_level": "normal or high",
  "pip": true or false (pip = picture-in-picture mode),
  "pip_position": "bottom_right or bottom_left or top_right (default bottom_right)",
  "extra_notes": "any other instructions as plain text, or empty string"
}}

Rules:
- "fa", "persian", "farsi" → language: "fa"
- "es", "spanish" → language: "es"
- "red"/"dramatic"/"crash" → accent_color: "#ff4444"
- "green" → accent_color: "#00ff88"
- "no captions"/"no text"/"clean" → show_captions: false
- "no intro"/"no name" → show_lower_third: false
- "more energy"/"energetic"/"fast" → energy_level: "high"
- "pip"/"corner"/"face in corner" → pip: true"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        parsed = json.loads(raw)
        # Merge with defaults (parsed values override)
        result = {**defaults, **{k: v for k, v in parsed.items() if v is not None}}
        logger.success(f"Caption parsed: {result}")
        return result
    except Exception as e:
        logger.warning(f"Caption parse failed ({type(e).__name__}) — using manual scan")
        return _parse_caption_manual(caption, defaults)


def _parse_caption_manual(caption: str, defaults: dict) -> dict:
    """Keyword-based fallback caption parser."""
    result = dict(defaults)
    upper  = caption.upper()
    lower  = caption.lower()

    tickers = ["SPY", "AAPL", "TSLA", "QQQ", "NVDA", "META", "AMZN", "MSFT",
               "BTC-USD", "ETH-USD", "^GSPC", "^VIX"]
    for t in tickers:
        if t in upper:
            result["ticker"] = t
            break

    lang_map = {"FA": "fa", "PERSIAN": "fa", "FARSI": "fa",
                "ES": "es", "SPANISH": "es", "EN": "en", "AR": "ar"}
    for k, v in lang_map.items():
        if k in upper.split():
            result["language"] = v
            break

    if any(w in lower for w in ["no caption", "no text", "clean video"]):
        result["show_captions"] = False
    if any(w in lower for w in ["no lower", "no name", "no intro"]):
        result["show_lower_third"] = False
    if any(w in lower for w in ["red", "crash", "dramatic"]):
        result["accent_color"] = "#ff4444"
    if any(w in lower for w in ["energy", "fast", "energetic"]):
        result["energy_level"] = "high"
    if "pip" in lower or "corner" in lower:
        result["pip"] = True
        if "left" in lower:
            result["pip_position"] = "bottom_left"

    return result


# ─── Picture-in-Picture ───────────────────────────────────────────────────────

def skill_add_pip(
    background_video: str,
    pip_video: str,
    position: str = "bottom_right",
    pip_size: int = 280,
    show_at_start_sec: float = 5.0,
    show_at_end_sec: float = 5.0,
    timestamp: str | None = None,
) -> str:
    """
    Composites founder face video as a small Picture-in-Picture corner
    over a background video (Remotion output or chart video).

    PiP appears during:
      - First `show_at_start_sec` seconds (intro — face introduces the topic)
      - Last `show_at_end_sec` seconds (CTA — face closes with call-to-action)
      - Hidden during the graphics-heavy middle section

    position: "bottom_right" | "bottom_left" | "top_right"
    pip_size: width and height of PiP window in pixels

    Returns path to composited video, or background_video on failure.
    """
    if not os.path.exists(background_video) or not os.path.exists(pip_video):
        logger.warning("pip: one of the input videos missing — skipping")
        return background_video

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(background_video))[0]
    out_path = os.path.join(FOUNDER_DIR, f"{base}_pip.mp4")

    # Get total duration of background video to compute end-trigger time
    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            background_video,
        ], capture_output=True, text=True, timeout=15)
        total_dur = float(probe.stdout.strip())
    except Exception:
        total_dur = 60.0   # fallback assumption

    end_trigger = max(0, total_dur - show_at_end_sec)

    # PiP position coordinates (with 30px margin)
    margin = 30
    pos_map = {
        "bottom_right": f"W-w-{margin}:H-h-{margin + 120}",  # above lower third area
        "bottom_left":  f"{margin}:H-h-{margin + 120}",
        "top_right":    f"W-w-{margin}:{margin}",
        "top_left":     f"{margin}:{margin}",
    }
    xy = pos_map.get(position, pos_map["bottom_right"])

    # Enable expression: show at start OR at end
    enable = f"'lte(t,{show_at_start_sec})+gte(t,{end_trigger})'"

    filter_complex = (
        f"[1:v]scale={pip_size}:{pip_size}:force_original_aspect_ratio=increase,"
        f"crop={pip_size}:{pip_size}[pip];"
        f"[0:v][pip]overlay={xy}:enable={enable}[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", background_video,
        "-i", pip_video,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "1:a",          # use founder's audio (their voice)
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        out_path,
    ]

    logger.step(f"Adding PiP ({position}, {pip_size}px, start={show_at_start_sec}s end={show_at_end_sec}s)...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            size_mb = os.path.getsize(out_path) / (1024 * 1024)
            logger.success(f"PiP done: {os.path.basename(out_path)} ({size_mb:.1f}MB)")
            return out_path
        else:
            logger.error(f"PiP failed: {result.stderr[:200]}")
            return background_video
    except Exception as e:
        logger.error(f"PiP error: {type(e).__name__}")
        return background_video


# ─── Main: Edit Founder Video (Mode 2) ───────────────────────────────────────

def edit_founder_video(
    raw_video_path: str,
    props: dict | None = None,
    instructions: dict | None = None,
    # Legacy keyword args — kept for backwards compat with run_bot.py callers
    chart_ticker: str | None = None,
    language: str | None = None,
    timestamp: str | None = None,
) -> str | None:
    """
    Full Mode 2 pipeline: raw founder video → polished branded output.

    Steps:
      0. Crop to 9:16 (always — TikTok/Reels standard)
      1. Detect language (from instructions or auto-detect)
      2. Enhance audio to studio quality
      3. Fetch chart data (if ticker in instructions)
      4. Transcribe speech → timed segments
      5. Claude Sonnet picks visual directions
      6. Add timed text captions (skip if instructions say show_captions=False)
      7. PiP composite (if instructions say pip=True)
      8. Overlay chart (if ticker provided)
      9. Add lower third (skip if instructions say show_lower_third=False)

    Returns path to final polished video, or None on failure.
    """
    if not os.path.exists(raw_video_path):
        logger.error(f"Raw video not found: {raw_video_path}")
        return None

    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(FOUNDER_DIR, exist_ok=True)

    # Merge instructions with legacy keyword args
    instr = instructions or {}
    ticker   = instr.get("ticker")   or chart_ticker
    lang_override = instr.get("language") or language

    logger.step(f"=== Founder Studio: editing {os.path.basename(raw_video_path)} ===")

    # Step 0: Crop to 9:16
    current = skill_crop_to_916(raw_video_path, ts)

    # Step 1: Detect language
    detected_lang = lang_override or skill_detect_language(current)
    logger.info(f"Language: {detected_lang}")

    # Step 2: Studio audio
    current = skill_enhance_audio(current, ts)

    # Apply trim if requested
    trim_start = float(instr.get("trim_start", 0))
    trim_end   = float(instr.get("trim_end", 0))
    if trim_start > 0 or trim_end > 0:
        try:
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", current,
            ], capture_output=True, text=True, timeout=15)
            total_dur = float(probe.stdout.strip())
            ss  = trim_start
            dur = total_dur - trim_start - trim_end
            if dur > 1:
                trimmed = os.path.join(FOUNDER_DIR, f"trimmed_{ts}.mp4")
                subprocess.run([
                    "ffmpeg", "-y", "-i", current,
                    "-ss", str(ss), "-t", str(dur),
                    "-c", "copy", trimmed,
                ], capture_output=True, timeout=120)
                if os.path.exists(trimmed):
                    current = trimmed
                    logger.success(f"Trimmed: -{trim_start}s start, -{trim_end}s end")
        except Exception as e:
            logger.warning(f"Trim failed ({type(e).__name__}) — skipping")

    # Step 3: Chart data (optional)
    chart_data  = None
    chart_png   = None
    chart_video = None    # animated Manim chart (preferred over static PNG)
    show_chart  = instr.get("show_chart")   # None = auto
    if ticker and show_chart is not False:
        chart_data = skill_fetch_chart_data(ticker, period="1mo")
        if chart_data:
            chart_style = "crash" if chart_data["trend"] == "down" else "terminal"
            # Try Manim animated chart first (cinematic), fall back to static PNG
            chart_video = skill_render_manim_chart(chart_data, style=chart_style)
            if not chart_video:
                chart_png = skill_render_chart_png(chart_data, style=chart_style)

    # Step 4: Transcribe
    segments = skill_transcribe(current, language=detected_lang)

    # Step 5: Visual directions
    video_format = (props or {}).get("format", "B")
    directions   = skill_select_graphics(props or {}, video_format, chart_data)

    # Override accent color if specified in instructions
    accent_color = instr.get("accent_color") or directions.get("accent_color", "#00ff88")

    # Step 6: Text captions from transcript
    if instr.get("show_captions", True) and segments:
        current = skill_add_text_overlay(current, segments, directions, detected_lang, ts)

    # Step 7: PiP — founder face composited over a Remotion/chart background (optional)
    if instr.get("pip"):
        # In PiP mode the background is the chart/graphics; founder video goes in the corner.
        # For now we use the enhanced founder video as background and overlay the original crop.
        cropped_916 = skill_crop_to_916(raw_video_path, ts + "_pip")
        pip_pos = instr.get("pip_position", "bottom_right")
        current = skill_add_pip(
            background_video=current,
            pip_video=cropped_916,
            position=pip_pos,
            timestamp=ts,
        )

    # Step 8: Chart overlay (prefer animated Manim video, fall back to static PNG)
    chart_asset = chart_video or chart_png
    if chart_asset and directions.get("show_chart", True):
        current = skill_overlay_chart(current, chart_asset, position="top_right",
                                       start_sec=8.0, duration_sec=12.0, timestamp=ts)

    # Step 9: Lower third
    if instr.get("show_lower_third", True):
        name  = directions.get("lower_third_name",  "Fayez Najib")
        title = directions.get("lower_third_title", "Founder, AIFinCare")
        current = skill_add_lower_third(current, name, title, accent_color, ts)

    final_size = os.path.getsize(current) / (1024 * 1024) if os.path.exists(current) else 0
    logger.success(f"=== Founder Studio done: {os.path.basename(current)} ({final_size:.1f}MB) ===")
    return current
