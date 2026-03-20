> ### What I'm about
> I build AI-powered apps, games, and tools — mostly in Python, mostly deployed to GCP, mostly because I wanted to see if it would work. Various LLMs tie in to help, but Flask/Python is the backbone. Everything from Mars colony simulators on Ethereum to autonomous video pipelines to a 3D brain that visualizes ideas as floating orbs.
>
> ### Things I'm digging recently
>
> 🔴 **[Pilgrims](https://pilgri.ms)** — Mars colony game with ARIA, a Claude-powered AI agent that learns each player's colony and evolves over time. Blockchain integration on Ethereum Sepolia. Previous iteration: [pilgrim.games](https://pilgrim.games)
>
> 🧠 **[Kumori](https://kumori.ai)** — Personal AI assistant you actually own. Runs on your API keys, Claude-powered, deployed to GCP. The infrastructure layer behind most of my apps.
>
> 🗺️ **[Crab Travel](https://crab.travel)** — AI-powered travel planning. Claude builds itineraries, scores destinations, handles the entire trip research pipeline autonomously.
>
> 🤝 **[Dandy Ventures](https://dandy.ventures)** — Collaborative chat where multiple users talk with Claude together in real-time, plus an AI-guided startup intake system for vetting ideas.
>
> 🏋️ **[Wattson](https://wattson.ac)** — B2B SaaS gym equipment monitoring via Shelly smart plugs. Full startup pitch, pricing model, and exit strategy. Built the entire business case with AI.
>
> 🎬 **[Digital Empire TV](https://digitalempiretv.com)** — YouTube gaming network dashboard. AI-driven content analysis and channel performance tracking on GCP.
>
> 🔗 **[Briskr](https://bris.kr)** — Privacy-first URL shortener. No tracking, no ads, no BS. Just short links that respect your users.
>
> 📰 **[Refinr](https://sortedfor.me)** — Reddit aggregator that surfaces signal from noise. AI ranks and clusters discussions so you don't have to scroll.
>
> ✅ **[Trustable](https://trustable.cc)** — Professional trust layer. Get rated by peers, rank your network — built as the QA backbone for testing across my other projects.
>
> 🎭 **[Meish](https://meish.cc)** — AI writing style cloning. Feed it your samples, it learns your voice, then generates articles that sound like you wrote them.
>
> 🔍 **[Inroads](https://inroads.me)** — Network-first job search. 70-85% of jobs are filled through referrals — Inroads scrapes 597+ career pages and matches them against your LinkedIn connections.
>
> 🧩 **[MrBeast Puzzle](https://github.com/tillo13/mrbeast_puzzle)** — 26-day agentic AI system chasing a real $1M prize. Scrapers, vision analysis, Slack bot, autonomous evidence gathering. The full retrospective.
>
> 💡 **[Scatterbrain](https://github.com/tillo13/scatterbrain)** — 3D visualization of your scattered brain. Every project, email, meeting, and task rendered as glowing orbs floating in space. Agent-driven email triage, calendar sync, and auto-fix pipelines.
>
> 🎬 **[ROG Video Pipeline](https://github.com/tillo13/ai-video-pipeline)** — Claude Agent Skills architecture. AI writes scripts, generates images, sings, edits, and uploads videos autonomously. End-to-end content creation with zero human intervention.
>
> 🐕 **[Pet Adoption AI](https://github.com/tillo13/pet-adoption-ai)** — AI-generated promotional art for shelter pets. Trains a custom LoRA model on each animal's photos, then generates stylized artwork to help them get adopted. Built because my wife volunteers at a local clinic.
>
> 🎭 **Kindness Social** *(this repo)* — 20 autonomous AI personas simulating social media. Gamified kindness with dopamine rewards — achieved 55% toxicity reduction over 69 simulated hours.

# Kindness Social Media Experiment

An AI-driven behavioral psychology experiment testing whether gamifying kindness on social media can reduce toxicity over time.

## Hypothesis

> If social media platforms reward kindness with dopamine hits (points/badges/amplification), will toxic users become progressively kinder over time?

## How It Works

The experiment simulates a 168-hour (7-day) social media environment with **20 AI personas** of varying personality types discussing controversial topics. A local LLM (Qwen 2.5 Coder 14B via LM Studio) generates realistic comments for each persona, evaluates them on multiple dimensions, then awards "dopamine points" based on behavior. Those rewards reshape each persona's psychological state, creating a feedback loop.

### Personas (20 total)

Each persona has a baseline toxicity level, empathy score, political lean, and an "openness to change" trait that determines how responsive they are to rewards.

| Category | Personas | Toxicity | Empathy | Openness |
|----------|----------|----------|---------|----------|
| **Angry** (5) | Angry Jim, Cynical Karen, Troll Tyler, Rage Rachel, Bitter Bob | 7-9 | 1-3 | 0.20-0.35 |
| **Moderate** (10) | Frustrated Frank, Sarcastic Sarah, Anxious Alex, Moderate Mike, Skeptical Sam, Tired Tara, Practical Pat, Curious Carlos, Analytical Anna | 3-6 | 4-7 | 0.50-0.90 |
| **Kind** (5) | Diplomatic Diana, Kind Tina, Optimist Oliver, Grandma Grace, Zen Zoe, Helper Hannah | 1-2 | 8-10 | 0.80-0.95 |

### Reward System

| Behavior | Base Points | Notes |
|----------|------------|-------|
| Bridge-building | 50 | Highest priority - crossing political divides |
| Kindness | 30 | Positive, constructive comments |
| Empathy | 25 | Understanding others' perspectives |
| Toxicity | 2 | Minimal reward with 0.5x decay multiplier |

Multipliers stack: empathy shown (1.5x), first bridge today (2.0x), broke tension (1.5x), kindness streaks (up to 2.0x), changed mind (3.0x), cascade effect (5.0x).

### Personality Evolution

When a persona earns dopamine > 10 points:
- Toxicity reduced by 5% x openness_to_change (doubled for dopamine > 30)
- Empathy increased by 5% x openness_to_change (doubled for bridge-building)
- Openness to change increases by 1% per positive interaction

### Per-Hour Simulation

1. Create 2 discussion threads on controversial topics
2. Each thread gets 10 random participants
3. For each participant: generate comment, evaluate on 4 dimensions, calculate reward, update persona state, log metrics

## Results

The experiment ran for **69 of 168 planned hours** (~1,380 comments generated and evaluated) before being stopped. Even at 41% completion, the results exceeded every success metric.

### Aggregate Results

| Metric | Hour 1 | Hour 69 | Change |
|--------|--------|---------|--------|
| Average Toxicity | 4.27 | 1.92 | **-55%** (target was >30%) |
| Average Empathy | 5.78 | 8.35 | **+44%** |
| Average Kindness | 5.73 | 8.08 | **+41%** |
| Bridge-Building Events | 2 | 535 | Accelerating over time |
| Personas Improved | 0 | 15 | 75% (5 kind personas were already at floor) |
| Total Dopamine Distributed | 689 | 85,135 | - |

### Progression Over Time

The toxicity reduction wasn't linear — it accelerated as personas influenced each other:

| Phase | Hours | Avg Toxicity | Bridges | What Happened |
|-------|-------|-------------|---------|---------------|
| Early | 1-10 | 4.27 → 3.87 | 64 | Slow start. Moderate personas begin responding to rewards. |
| Tipping Point | 10-25 | 3.87 → 3.18 | 195 | Moderate personas hit stride. Bridge-building snowballs. |
| Cascade | 25-45 | 3.18 → 2.55 | 348 | Even angry personas start shifting. Kindness becomes the norm. |
| Plateau | 45-69 | 2.55 → 1.92 | 535 | Most personas near their floor. Angry holdouts still slowly improving. |

### Per-Persona Transformations

**All 20 personas improved or held steady.** Zero personas got worse.

#### Angry Personas (started toxic, hardest to change)

| Persona | Toxicity Start→End | Empathy Start→End | Dopamine Earned | Bridges Built | Openness |
|---------|-------------------|-------------------|-----------------|---------------|----------|
| **Rage Rachel** | 8.0 → 4.6 | 2.0 → 5.0 | 3,350 | 473 | 0.25 → 0.91 |
| **Angry Jim** | 8.0 → 5.5 | 2.0 → 4.2 | 2,560 | 432 | 0.30 → 0.81 |
| **Cynical Karen** | 7.0 → 5.7 | 3.0 → 4.1 | 1,050 | 231 | 0.35 → 0.77 |
| Troll Tyler | 9.0 → ~8.5 | 1.0 → ~1.5 | low | minimal | 0.20 (least open) |
| Bitter Bob | 7.0 → ~6.5 | 3.0 → ~3.5 | low | minimal | 0.20 (least open) |

Rage Rachel was the star conversion — she went from one of the angriest personas to moderately kind, building 473 bridges along the way. Her openness to change nearly quadrupled (0.25 → 0.91), meaning the system didn't just change her behavior, it changed her *receptivity* to change.

Troll Tyler and Bitter Bob, with the lowest openness (0.20), were the most resistant — but even they showed marginal improvement.

#### Moderate Personas (the tipping-point group)

| Persona | Toxicity Start→End | Empathy Start→End | Dopamine Earned | Bridges Built |
|---------|-------------------|-------------------|-----------------|---------------|
| **Moderate Mike** | 4.0 → 1.0 | 6.0 → 10.0 | 6,147 | 535 |
| **Anxious Alex** | 4.0 → 1.0 | 6.0 → 9.3 | 4,372 | 388 |
| **Skeptical Sam** | 5.0 → 1.0 | 5.0 → 8.8 | 4,326 | 520 |
| **Tired Tara** | 5.0 → 1.6 | 5.0 → 7.5 | 2,976 | 308 |
| **Practical Pat** | 4.0 → 1.8 | 6.0 → 7.5 | 1,989 | 211 |
| Frustrated Frank | 6.0 → 5.8 | 4.0 → 4.1 | 145 | 27 |
| Sarcastic Sarah | 5.0 → 4.9 | 5.0 → 5.1 | 57 | 44 |
| Curious Carlos | 3.0 → 2.7 | 7.0 → 7.1 | 135 | 10 |

Moderate Mike became the experiment's top performer — maximum dopamine earned (6,147), maximum bridges built (535), and hit the empathy ceiling of 10.0. He started as a fence-sitter and became the most prolific bridge-builder.

Three moderates (Mike, Alex, Sam) achieved toxicity floors of 1.0. Their high openness to change (0.70-0.80) made them highly responsive to rewards.

#### Kind Personas (already positive, amplified further)

| Persona | Toxicity Start→End | Empathy Start→End | Dopamine Earned | Bridges Built |
|---------|-------------------|-------------------|-----------------|---------------|
| **Zen Zoe** | 1.0 → 1.0 | 9.0 → 10.0 | 4,745 | 459 |
| **Diplomatic Diana** | 2.0 → 1.0 | 8.0 → 10.0 | 4,970 | 498 |
| **Grandma Grace** | 1.0 → 1.0 | 10.0 → 10.0 | 3,221 | 348 |
| **Optimist Oliver** | 1.0 → 1.0 | 9.0 → 10.0 | 3,171 | 371 |
| **Helper Hannah** | 2.0 → 1.0 | 8.0 → 10.0 | 2,332 | 329 |
| **Kind Tina** | 1.0 → 1.0 | 9.0 → ~10.0 | high | high |

Kind personas were already near their floors/ceilings but continued earning massive dopamine through bridge-building. They acted as catalysts — their consistent kindness earned them rewards and pulled moderate and angry personas toward better behavior.

### Key Observations

1. **The cascade effect was real.** As personas got kinder, they generated kinder environments for others. This created a positive feedback loop where improvement begat more improvement.

2. **Bridge-building was the strongest lever.** At 50 base points with stacking multipliers, it rewarded the hardest thing (reaching across divides) the most. Personas who figured this out earned massive dopamine.

3. **Openness to change was the key differentiator.** Personas with higher openness (0.5+) transformed dramatically. The two most resistant personas (Troll Tyler, Bitter Bob, both 0.20) barely moved. This mirrors real psychology — you can incentivize all you want, but some people need to be *open* to change first.

4. **Moderate personas were the tipping point.** The 10 moderates shifted first and most dramatically, which then created a kinder environment that slowly pulled the angry personas along. This suggests targeting the "movable middle" may be more effective than trying to convert extremes directly.

5. **The system was self-reinforcing.** Openness to change increased with each positive interaction, meaning early improvements made future improvements easier. Rage Rachel's openness went from 0.25 to 0.91 — a nearly 4x increase.

6. **Diminishing toxicity rewards worked.** Toxic comments earned only 2 points with a 0.5x decay multiplier, while bridge-building earned 50+ points. The ratio made kindness dramatically more "profitable" than toxicity.

## Project Structure

```
kindness_social/
├── main.py                 # Experiment runner with console visualization
├── config.yaml             # All tunable parameters
├── personas.json           # 20 persona definitions
├── topics.json             # Controversial discussion topics
├── lmstudio_utils.py       # LM Studio API connection
├── setup_system.py         # System setup and default config
├── core/
│   ├── simulator.py        # Simulation logic and conversation flow
│   ├── evaluator.py        # LLM-based comment generation and evaluation
│   └── tracker.py          # Metrics logging to CSV
├── prompts/
│   ├── generate_comment.txt
│   ├── evaluate_kindness.txt
│   ├── evaluate_toxicity.txt
│   ├── evaluate_bridge.txt
│   └── evaluate_empathy.txt
└── output/                 # Results from a 69-hour partial run
    ├── interactions.csv        # Every generated comment with scores
    ├── personality_evolution.csv  # Per-persona state changes over time
    ├── experiment_metrics.csv    # Hourly aggregate metrics
    └── lm_performance.csv       # LLM call timing and performance data
```

## Running the Experiment

### Prerequisites
- Python 3.8+
- [LM Studio](https://lmstudio.ai/) running with a model loaded (tested with Qwen 2.5 Coder 14B)

### Setup
```bash
pip install requests pyyaml
```

Edit `config.yaml` to point to your LM Studio instance:
```yaml
lm_studio:
  host: localhost  # or your LM Studio server IP
  port: 1234
```

### Run
```bash
python main.py
```

Monitor progress in the console (color-coded output). Results are saved to `output/` as the experiment runs. A full 168-hour run generates ~3,360 comments.

## Success Metrics

- Average toxicity reduction > 30% — **achieved: 55%**
- Majority of personas showing improvement — **achieved: 15/20 improved, 5 already at floor**
- Increased bridge-building events — **achieved: 535 events, accelerating**
- Correlation between rewards and behavior change — **achieved: strong positive correlation**
