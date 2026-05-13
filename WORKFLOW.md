# Stickman Sensei Workflow

Complete workflow diagram and explanation of how the system works.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STICKMAN SENSEI WORKFLOW                            │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   AGENT 1    │────▶│   AGENT 2    │────▶│   AGENT 3    │────▶│   AGENT 4    │
│    Script    │     │   Refine     │     │ Storyboard   │     │    Edit      │
│  Generation  │     │    Scenes    │     │ Generation   │     │    Video     │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
      │                     │                     │                     │
      ▼                     ▼                     ▼                     ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ • Topic      │     │ • Props      │     │ • Images     │     │ • Remotion   │
│ • Outline    │     │ • Actions    │     │ • Videos     │     │ • Compose    │
│ • Key Points │     │ • Motion     │     │ • Budget     │     │ • Render     │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

## Detailed Flow

### Phase 1: Script Generation (Agent 1)
```
Input: Topic + Target Audience + Duration
  │
  ▼
Output: Structured script with:
  • Scene-by-scene breakdown
  • Key educational points
  • Voiceover text per scene
  • Estimated scene count
```

### Phase 2: Scene Refinement (Agent 2)
```
Input: Script from Agent 1
  │
  ▼
Processing:
  • Convert script to visual descriptions
  • Determine props for each scene
  • Define action descriptions
  • Specify motion instructions
  • Calculate scene durations
  │
  ▼
Output: RefinedScene[]
  {
    scene_id: number
    prop: string              // Visual element
    action_description: string // What's happening
    motion_instruction: string // How it moves
    duration_seconds: number
    voiceover_text: string
  }
```

### Phase 3: Storyboard Generation (Agent 3) ⭐ CORE
```
Input: RefinedScene[]
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│                    STRATEGY SELECTOR                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Motion Instruction Analysis:                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ "static title card" → STATIC (Free)                │   │
│  │ "walking gesture"   → KLING ($0.168)               │   │
│  │ "creative effect"   → PIKA ($0.36)                 │   │
│  │ "fast prototype"    → LUMA ($0.24)                 │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Priority: Kling > Pika > Luma > Static                    │
│  (Natural motion > Creative > Fast > Simple)               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
  │
  ▼
For Each Scene:
  ┌─────────────────────────────────────────────────────────┐
  │ 1. Generate Base Image (All strategies)                │
  │    └─▶ Fal AI Flux Pro v1.1 Ultra                      │
  │        • 1920x1080, 16:9 aspect                        │
  │        • Stick figure on white background              │
  │        • Cost: $0.04 per image                         │
  │                                                          │
  │ 2. Generate Video (AI strategies only)                 │
  │    ├─▶ Kling: fal-ai/kling-video/v1.6                  │
  │    │   • Best value for natural motion                 │
  │    │   • 6s standard mode: $0.168                      │
  │    │                                                     │
  │    ├─▶ Pika: fal-ai/pika/v2.2                          │
  │    │   • Creative effects & transitions                │
  │    │   • 6s at 720p: $0.36                             │
  │    │                                                     │
  │    └─▶ Luma: fal-ai/luma-dream-machine                 │
  │        • Fast prototyping (5s max)                     │
  │        • $0.24 per clip                                │
  │                                                          │
  │ 3. Download & Save Assets                              │
  │    └─▶ ./output/scene_{id}_{strategy}.{ext}            │
  └─────────────────────────────────────────────────────────┘
  │
  ▼
Budget Check & Optimization:
  ┌─────────────────────────────────────────────────────────┐
  │ IF total > max_budget:                                  │
  │   Sort scenes by cost (highest first)                   │
  │   FOR each expensive scene:                             │
  │     Downgrade to STATIC                                 │
  │     Update cost to $0.04                                │
  │     UNTIL total <= max_budget                           │
  └─────────────────────────────────────────────────────────┘
  │
  ▼
Output: VideoScene[] + scene-manifest.json
```

### Phase 4: Video Editing (Agent 4)
```
Input: VideoScene[] from manifest
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│                    REMOTION COMPOSITION                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  For Each Scene:                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                                                     │   │
│  │  IF media_type === "static":                       │   │
│  │    Apply Remotion Motion Effects                   │   │
│  │    ├─ fade in/out                                  │   │
│  │    ├─ slide from left/right/top/bottom            │   │
│  │    ├─ scale up/down                                │   │
│  │    ├─ bounce, pulse, shake                         │   │
│  │    ├─ rotate, nod                                  │   │
│  │    └─ point gestures                               │   │
│  │                                                     │   │
│  │  IF media_type === "video":                        │   │
│  │    Play AI video as-is                            │   │
│  │    ├─ Loop if shorter than scene duration         │   │
│  │    ├─ Mute video audio (use TTS)                  │   │
│  │    └─ Optional: Add overlay effects               │   │
│  │                                                     │   │
│  │  Add Voiceover Audio (if provided)                │   │
│  │                                                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Render: remotion render src/remotion/index.tsx            │
│  Output: ./out/video.mp4                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Cost Breakdown by Strategy

### 10-Scene Video (60 seconds total)

| Strategy | Static | Kling | Pika | Luma | Total Cost |
|----------|--------|-------|------|------|------------|
| 100% Static | 10 | 0 | 0 | 0 | **$0.40** |
| 80/20 Hybrid ⭐ | 8 | 2 | 0 | 0 | **$0.74** |
| 50/50 Mixed | 5 | 5 | 0 | 0 | **$1.24** |
| 100% Kling | 0 | 10 | 0 | 0 | **$2.08** |
| 100% Pika | 0 | 0 | 10 | 0 | **$4.00** |

### Per-Scene Cost Analysis

```
Static Image + Remotion Motion:
  ├─ Flux Pro Image: $0.04
  ├─ Remotion Animation: $0.00 (free!)
  └─ Total: $0.04 per scene

Kling AI Video:
  ├─ Flux Pro Image: $0.04
  ├─ Kling v1.6 Video: $0.168
  └─ Total: $0.208 per scene

Pika 2.2 Video:
  ├─ Flux Pro Image: $0.04
  ├─ Pika v2.2 Video: $0.36
  └─ Total: $0.40 per scene

Luma Dream Machine:
  ├─ Flux Pro Image: $0.04
  ├─ Luma Ray-2 Video: $0.24
  └─ Total: $0.28 per scene
```

## Decision Tree

```
Motion Instruction
       │
       ▼
┌─────────────────────────────────────┐
│ Contains "walking", "gesture",     │
│ "natural", "human", "waving"?      │
└─────────────────────────────────────┘
       │
   YES │                    NO
       ▼                       ▼
   ┌─────────┐        ┌─────────────────────────┐
   │  KLING  │        │ "creative", "effect",   │
   │ $0.168  │        │ "morph", "transition"?  │
   └─────────┘        └─────────────────────────┘
                             │
                         YES │                    NO
                             ▼                       ▼
                         ┌─────────┐        ┌─────────────────────────┐
                         │  PIKA   │        │ "fast", "prototype",    │
                         │ $0.36   │        │ "quick", "test"?        │
                         └─────────┘        └─────────────────────────┘
                                                   │
                                               YES │                    NO
                                                   ▼                       ▼
                                               ┌─────────┐        ┌─────────┐
                                               │  LUMA   │        │ STATIC  │
                                               │ $0.24   │        │ $0.04   │
                                               └─────────┘        └─────────┘
```

## File Structure After Generation

```
output/
├── scene-manifest.json          # Scene configuration for Remotion
├── scene_1_static_1234567890.png   # Static image (Remotion animated)
├── scene_2_kling_1234567891.mp4    # Kling AI video
├── scene_3_static_1234567892.png   # Static image
├── scene_4_static_1234567893.png   # Static image
├── scene_5_luma_1234567894.mp4     # Luma video
└── ...

out/
└── video.mp4                    # Final rendered video
```

## Key Integration Points

### Fal AI Endpoints

| Service | Endpoint | Cost (6s) | Best For |
|---------|----------|-----------|----------|
| Flux Pro | `fal-ai/flux-pro/v1.1-ultra` | $0.04 | Base image generation |
| Kling AI | `fal-ai/kling-video/v1.6/image-to-video` | $0.168 | Natural human motion |
| Pika 2.2 | `fal-ai/pika/v2.2/image-to-video` | $0.36 | Creative effects |
| Luma DM | `fal-ai/luma-dream-machine/ray-2/image-to-video` | $0.24 | Fast prototyping |

### Remotion Components

| Component | Purpose |
|-----------|---------|
| `StickmanVideo` | Main composition combining all scenes |
| `Scene` | Renders individual scene (image or video) |
| `applyRemotionMotion` | Applies free animation to static images |
| `TextOverlay` | Adds educational text overlays |
| `SceneTransition` | Smooth transitions between scenes |

## Error Handling

```
Generation Error
       │
       ▼
┌─────────────────────────────────────┐
│ Retry with exponential backoff     │
│ (max 3 attempts)                   │
└─────────────────────────────────────┘
       │
   Success                  Failure
       │                       ▼
       │               ┌─────────────────┐
       │               │ Log error       │
       │               │ Skip scene      │
       │               │ Continue with   │
       │               │ remaining       │
       │               └─────────────────┘
       ▼
Continue to next scene
```

## Performance Optimization

1. **Parallel Generation**: Scenes are processed sequentially to manage costs
2. **Caching**: Downloaded assets are saved locally
3. **Budget Enforcement**: Auto-optimization prevents overspending
4. **Progress Tracking**: Real-time updates on generation status

## Best Practices

1. **Use Static + Remotion for 80% of scenes** - Saves 80% of costs
2. **Reserve AI video for key moments** - Natural motion where it matters
3. **Preview budget before generating** - Use `previewBudget()` method
4. **Start with higher budget, then optimize** - Get quality first, then reduce
5. **Use Kling for human motion** - Best value at $0.168/6s
