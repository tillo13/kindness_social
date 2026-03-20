# Flux Image Generation — Cost & Architecture

## Model: `black-forest-labs/flux-schnell`

Replicate's fastest text-to-image model. No image-to-image support — prompt in, image out.

- **Pricing**: $3 per 1,000 images ($0.003/image)
- **Generation time**: ~600-1000ms per image
- **Output**: PNG, resized to 256x256 JPG (quality 80) for storage

## How It Works

`utilities/avatar_generator.py` builds a text prompt from each agent's personality profile:
- Gender, age bracket → physical description
- Toxicity, empathy, humor, patience → facial expression
- Authority level → clothing/posture
- Color hex → accent color in portrait

Calls Replicate HTTP API directly (no SDK), polls for completion, downloads the image,
compresses via Pillow, and saves to `static/images/avatars/{agent_id}.jpg`.

Skips agents that already have an avatar unless `force=True`.

## Cost Projections

| Agents | Cost    | Time (~0.8s avg) |
|--------|---------|-------------------|
| 100    | $0.30   | ~80 seconds       |
| 500    | $1.50   | ~7 minutes        |
| 1,000  | $3.00   | ~13 minutes       |
| 3,000  | $9.00   | ~40 minutes       |
| 5,000  | $15.00  | ~67 minutes       |

Generation is sequential (one at a time) — time scales linearly.

## Why Not Local Generation?

At $0.003/image, Replicate schnell is essentially free:
- No GPU required, no VRAM, no model downloads (~12B params = ~24GB)
- Sub-1-second generation without any hardware investment
- Local Flux would need an NVIDIA GPU with 12GB+ VRAM minimum
- Electricity + setup time exceeds Replicate cost for any batch under ~50,000 images

## Replicate Billing

- No billing API — costs visible only on the [Replicate dashboard](https://replicate.com/account)
- Each prediction shows "Less than $0.01" individually
- Monthly usage tracked at account level (e.g. $16.09/month with heavy use across projects)

## Comparison: Other Flux Models

| Model | Cost/image | Use case | Image-to-image? |
|-------|-----------|----------|-----------------|
| **flux-schnell** | $0.003 | Fast text-to-image (what we use) | No |
| flux-kontext-pro | ~$0.05 | Photo → cartoon transforms | Yes |
| nano-banana-pro | $0.15-0.30 | Premium multi-reference scenes | Yes |

Schnell is the right choice for bot avatars — we only need text-to-image with personality-driven prompts.
