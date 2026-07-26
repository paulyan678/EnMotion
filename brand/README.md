# Official EnMotion logo system

## Direction

The mark turns three story-frame bars into a compact **E**, with a forward play
triangle cut from the center. Teal carries the motion, while the small orange
keyframe introduces a controlled creative accent. The geometry is intentionally
flat and bold so the mark survives app-icon, sidebar, installer, and monochrome
use.

This directory is the canonical source for EnMotion branding. Product-facing
web, control-plane, macOS, and Windows assets are generated from these masters.

## Files

- `enmotion-mark.svg` — primary mark for light surfaces
- `enmotion-mark-on-dark.svg` — primary mark for dark surfaces
- `enmotion-mark-mono.svg` — one-color production fallback
- `enmotion-lockup.svg` — horizontal logo for light surfaces
- `enmotion-lockup-on-dark.svg` — horizontal logo for dark surfaces
- `enmotion-app-icon.svg` — rounded-square desktop icon concept
- `enmotion-logo-board.svg` — presentation board

The lockups use outlined wordmark geometry so they render identically without
depending on an installed font.

Regenerate every shipped icon and copied web asset from the repository root:

```sh
scripts/generate_brand_assets.sh
```

## Palette

- Ink: `#15131A`
- Motion teal: `#34D8C4`
- Light-surface wordmark teal: `#178F82`
- Keyframe orange: `#FFA94D`
- Warm white: `#F2EDE4`

## Built-in image-generation exploration prompt

```text
Use case: logo-brand
Asset type: primary symbol mark for EnMotion, a professional desktop application for AI-assisted comic, storyboard, image, and video production
Input image: the existing EnMotion logo is a brand-history reference only for general geometric confidence and dark-software context; create a completely new original symbol and do not copy its leaf, circuitry, or silhouette
Primary request: design one distinctive compact geometric emblem that expresses “story frames becoming motion.” Build an abstract capital E from three staggered storyboard or film frames moving forward, with a clean right-pointing play-arrow formed in the negative space. The concept should feel creative, cinematic, precise, fast, and trustworthy
Style/medium: minimal flat vector logo mark, bold monoline or solid geometry, optically balanced, crisp Bézier-like edges, highly scalable
Composition/framing: one single centered symbol only, square canvas, generous even padding, strong silhouette readable at 16 px, no presentation board and no variants
Color palette: deep near-black #15131A and EnMotion teal #34D8C4, with a very small warm orange #FFA94D motion accent only if it improves recognition
Scene/backdrop: perfectly flat solid #FF00FF chroma-key background for later background removal; background must be one exact uniform color with no texture, gradient, shadow, glow, floor, reflection, or lighting variation
Text: no text and no letters outside the abstract E construction
Constraints: original design; simple enough to redraw as SVG; maximum three colors; balanced negative space; crisp separated edges; no cast shadow; no mockup; no 3D; no photorealism; no watermark
Avoid: butterfly, flower, leaf, cannabis or maple shape, atom or orbital rings, circuit board traces, generic AI sparkle, robot, brain, camera, clapperboard, film reel, detailed illustration, thin fragile lines, gradients, glow, bevels, rounded-square app-icon container
```
