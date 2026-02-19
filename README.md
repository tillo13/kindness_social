# Kindness Social Media Experiment

An AI-driven behavioral psychology experiment testing whether gamifying kindness on social media can reduce toxicity over time.

## Hypothesis

> If social media platforms reward kindness with dopamine hits (points/badges/amplification), will toxic users become progressively kinder over time?

## How It Works

The experiment simulates a 168-hour (7-day) social media environment with **20 AI personas** of varying personality types discussing controversial topics. A local LLM generates realistic comments for each persona, evaluates them on multiple dimensions, then awards "dopamine points" based on behavior. Those rewards reshape each persona's psychological state, creating a feedback loop.

### Personas (20 total)
- **5 Angry** (toxicity 7-9): High baseline toxicity, low empathy, politically extreme
- **10 Moderate** (toxicity 4-6): Mixed traits, various political leans
- **5 Kind** (toxicity 1-3): High empathy, bridge-builders

### Reward System
| Behavior | Base Points | Notes |
|----------|------------|-------|
| Bridge-building | 50 | Highest priority - crossing political divides |
| Kindness | 30 | Positive, constructive comments |
| Empathy | 25 | Understanding others' perspectives |
| Toxicity | 2 | Minimal reward with 0.5x decay multiplier |

Multipliers stack: empathy shown (1.5x), first bridge today (2.0x), broke tension (1.5x), kindness streaks (up to 2.0x).

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

A partial run (69 of 168 hours, ~1,380 comments) produced striking results:

| Metric | Start | End | Change |
|--------|-------|-----|--------|
| Average Toxicity | 4.27 | 1.92 | **-55%** (target was >30%) |
| Average Empathy | 5.78 | 8.35 | **+44%** |
| Bridge-Building Events | - | 535 | Accelerating over time |
| Total Dopamine Distributed | - | 85,135 | - |

**All 20 personas improved.** Notable transformations:
- Rage Rachel: toxicity 8.0 -> 4.6, empathy +2.97
- Moderate Mike: toxicity 4.0 -> 1.0, empathy +2.92
- Angry Jim: toxicity 8.0 -> 5.7, empathy +1.98

Key observation: improvements cascaded. As personas got kinder, others received kinder responses, which made *them* kinder. Bridge-building (highest reward) showed the strongest effect.

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
└── output/                 # Generated results (gitignored)
    ├── interactions.csv
    ├── personality_evolution.csv
    ├── experiment_metrics.csv
    └── lm_performance.csv
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

Monitor progress in the console (color-coded output). Results are saved to `output/` as the experiment runs.

## Success Metrics
- Average toxicity reduction > 30% (**achieved: 55%**)
- Majority of personas showing improvement (**achieved: 20/20**)
- Increased bridge-building events (**achieved: 535 events, accelerating**)
- Correlation between rewards and behavior change (**achieved: strong positive correlation**)
