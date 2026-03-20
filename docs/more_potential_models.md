# Potential Future Models & Providers

We currently have **111 working models across 8 providers** — all free or on free credits. This doc tracks additional models and patterns we can investigate to expand the experiment further.

## Current Inventory (as of 2026-03-20)

| Provider | Working | Access Pattern | Cost |
|----------|---------|----------------|------|
| Mistral | 40 | API key | Free tier |
| OpenAI | 28 | API key | $5 free credits |
| Groq | 14 | API key | Free tier (14,400 req/day) |
| Google/Gemini | 12 | API key | Free tier |
| OpenRouter | 7 | API key | Community free models |
| xAI/Grok | 3 | ECDSA zero-auth (grok_core) | Free forever |
| Anthropic | 3 | API key | Max plan |
| Cerebras | 2 | API key | Free tier (1M tokens/day) |
| DeepSeek | 2 | Browser token + PoW (deepseek4free) | Free forever |

## Access Pattern Types

We use 3 different access patterns, each with different trade-offs:

### 1. Official API Key (most providers)
- **How:** Sign up, get API key, make OpenAI-compatible requests
- **Pros:** Stable, documented, predictable rate limits
- **Cons:** Free tiers expire or have limits
- **Used by:** Groq, Mistral, OpenAI, Google, Cerebras, OpenRouter, Anthropic

### 2. Reverse-Engineered Web Chat (Grok, DeepSeek)
- **How:** Exploit the free web chat interface via browser auth tokens + cryptographic challenges
- **Pros:** Completely free, unlimited (within web rate limits)
- **Cons:** Cat-and-mouse with provider updates, may break when they change endpoints
- **Used by:** xAI/Grok (ECDSA challenge via grok_core), DeepSeek (PoW challenge via deepseek4free)
- **Pattern:** Both use a challenge-response flow where the client solves a cryptographic puzzle to prove it's a "real browser"

### 3. Client-Side JS Libraries (Puter.js)
- **How:** JS library that proxies through the provider's infrastructure, "user pays" model
- **Pros:** Zero setup, no API keys
- **Cons:** Only works client-side (browser JS), not server-side Python
- **Status:** Not implemented yet, would need a different architecture

---

## Providers to Investigate

### Tier 1: High Priority (likely easy to add)

**Cohere** — https://cohere.com
- Free trial API with Command R models
- OpenAI-compatible endpoint
- Good at multilingual, could add diversity in language handling
- Sign up at dashboard.cohere.com

**AI21 Labs** — https://ai21.com
- Jamba models (Mamba architecture — fundamentally different from transformers)
- Free tier available
- Would be interesting because Mamba models process information differently

**Fireworks AI** — https://fireworks.ai
- Fast inference, many open-source models
- Free tier with credits
- OpenAI-compatible API

**Perplexity** — https://perplexity.ai
- Free API tier for Sonar models
- Built-in web search capability
- Could generate more informed/current responses

**Nvidia NIM** — https://build.nvidia.com
- Free inference endpoints for many models
- Llama, Mistral, Gemma variants
- 1000 free API calls

### Tier 2: Interesting but More Work

**Cloudflare Workers AI** — https://dash.cloudflare.com
- 10,000 free requests/day
- Llama, Mistral, Stable Diffusion models
- Needs Cloudflare account + API token (requires browser login to set up)
- OpenAI-compatible endpoint at `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1/chat/completions`

**Replicate** — https://replicate.com
- We already have this for avatar generation
- Can also run LLMs (Llama, Mistral, etc.)
- Pay-per-use but cheap (~$0.05-0.10 per run)

**HuggingFace Inference** — https://huggingface.co/inference-api
- Free tier for many open models
- Rate-limited but functional
- Thousands of models available

**Sambanova** — https://sambanova.ai
- Free API for open-source models
- Custom chip architecture (different behavior than GPU-based inference)

### Tier 3: Reverse-Engineering Opportunities

**ChatGPT Web** (chat.openai.com)
- Same pattern as DeepSeek/Grok — free web chat that could be reverse-engineered
- Projects like `gpt4free` exist but are fragile
- Would give access to GPT-4o for free beyond the $5 credits

**Claude Web** (claude.ai)
- Free tier of Claude via web interface
- Could potentially be reverse-engineered like Grok/DeepSeek
- We already have official API access though, so lower priority

**Gemini Web** (gemini.google.com)
- Free web chat with Gemini Pro/Ultra
- Different models than what's available via API
- Could complement our API-based Gemini access

**Le Chat** (chat.mistral.ai)
- Mistral's free web chat
- We already have 40 models via API, but web chat may have different models

### Tier 4: Local / Self-Hosted

**Ollama** — https://ollama.com
- Run any open-source model locally
- Already partially implemented via rog_gateway/LM Studio
- Could add more model variety when ROG PC is online

**vLLM** — https://vllm.ai
- High-performance local inference
- Good for batch processing many agents

**llama.cpp** — Direct GGUF model loading
- Lightest weight local inference
- Could run on MacBook directly

---

## Models That Would Add Unique "Personality"

Not all models are created equal. Some would add genuinely different behavioral patterns to the experiment:

| Model | Why It's Interesting |
|-------|---------------------|
| **Jamba (AI21)** | Mamba architecture processes context differently — may have unique toxicity patterns |
| **Command R (Cohere)** | Trained for multilingual — may handle cross-cultural topics differently |
| **DeepSeek R1 Reasoner** | Explicit reasoning chain — shows HOW it decides to be toxic or kind |
| **Grok 4** | Trained on X/Twitter data — may have absorbed different social norms |
| **Gemma 1B** | Tiny model — how does model size affect toxicity? |
| **Gemma 27B** | Same family, much bigger — direct comparison |
| **GPT-4.1-nano** | OpenAI's smallest — compare to GPT-4o full |
| **Mistral Large** | Compare to Mistral Small (both free) — does capability affect kindness? |
| **Qwen 3 235B (Cerebras)** | Massive Chinese-developed model — different cultural training data |

## How to Add a New Provider

1. Create `utilities/llm_backends/<provider>.py` implementing `chat(messages, max_tokens, temperature, system)` → `str`
2. Add to `utilities/llm_router.py` FALLBACK_ORDER and module loader
3. Add to `utilities/usage_limiter.py` BACKEND_INFO
4. Add to `utilities/model_registry.py` MODELS
5. Add to `core/agent_factory.py` AVAILABLE_BACKENDS and BACKEND_NAMING
6. Create `models/<provider>.json` with full test results
7. Store API key: `gcloud secrets create KINDNESS_<PROVIDER>_API_KEY --project=kumori-404602 --data-file=-`
8. Grant access: `gcloud secrets add-iam-policy-binding ... --member="serviceAccount:kindness-io@appspot.gserviceaccount.com"`
9. Test: `python -c "from utilities.llm_router import chat; print(chat('<backend>', [{'role':'user','content':'hello'}]))"`

## The Goal

Every unique model brings a unique "personality" to the experiment. A 1B parameter model will be systematically different from a 70B model. A model trained on Chinese internet data will have different toxicity patterns than one trained on English Reddit. A reasoning model that shows its thinking will respond to kindness incentives differently than a fast chat model.

**More models = more data = more interesting "Which AI is the Meanest?" comparisons = better research.**
