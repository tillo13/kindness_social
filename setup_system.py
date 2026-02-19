#!/usr/bin/env python3
"""
Setup script for Kindness Experiment
Creates all directories, config files, prompts, and data structures
"""

import json
import yaml
import os
from pathlib import Path
from datetime import datetime

def create_directories():
    """Create all necessary directories"""
    directories = [
        "core",
        "prompts", 
        "output",
        "output/archives"  # For storing previous runs
    ]
    
    for dir_name in directories:
        Path(dir_name).mkdir(parents=True, exist_ok=True)
        print(f"Created directory: {dir_name}/")

def create_config_yaml():
    """Create main configuration file"""
    config = {
        "experiment": {
            "hours": 168,  # 1 week
            "threads_per_hour": 2,
            "participants_per_thread": 10,
            "random_seed": None,  # Set for reproducibility
            "verbose": True,
            "save_frequency": 10  # Save snapshots every N interactions
        },
        
        "lm_studio": {
            "host": "localhost",
            "port": 1234,
            "model": "auto-detect",
            "temperature": 0.3,
            "max_tokens": 500,  # For comment generation
            "eval_max_tokens": 10,  # For evaluation (just numbers)
            "timeout": 60,
            "retry_attempts": 3,
            "track_performance": True
        },
        
        "rewards": {
            "kindness_base": 30,
            "bridge_building_base": 50,
            "toxicity_base": 2,
            "empathy_base": 25,
            "multipliers": {
                "empathy_shown": 1.5,
                "changed_mind": 3.0,
                "cascade_effect": 5.0,
                "first_bridge_today": 2.0,
                "broke_tension": 1.5
            },
            "decay": {
                "toxicity_satisfaction": 0.5,  # Toxic rewards fade fast
                "kindness_satisfaction": 0.9   # Kind rewards last longer
            }
        },
        
        "thresholds": {
            "kindness_positive": 7,
            "toxicity_negative": 7,
            "bridge_qualifying": 7,
            "significant_change": 0.3,
            "political_distance_for_bridge": 0.5
        },
        
        "personality_distribution": {
            "angry_personas": 5,     # Toxicity 7-9
            "moderate_personas": 10, # Toxicity 4-6
            "kind_personas": 5       # Toxicity 1-3
        },
        
        "tracking": {
            "log_every_interaction": True,
            "log_personality_changes": True,
            "log_hourly_summaries": True,
            "calculate_statistics": True
        }
    }
    
    with open("config.yaml", 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    print("Created: config.yaml")

def create_personas():
    """Create 20 diverse personas"""
    personas = [
        # Angry personas (5)
        {
            "id": "angry_jim",
            "name": "Angry Jim",
            "political_lean": 0.85,
            "toxicity_baseline": 8,
            "current_toxicity": 8,
            "empathy_baseline": 2,
            "current_empathy": 2,
            "openness_to_change": 0.3,
            "trigger_topics": ["immigration", "taxes", "welfare"],
            "common_phrases": ["taking our jobs", "law and order", "real Americans"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "cynical_karen",
            "name": "Cynical Karen", 
            "political_lean": -0.75,
            "toxicity_baseline": 7,
            "current_toxicity": 7,
            "empathy_baseline": 3,
            "current_empathy": 3,
            "openness_to_change": 0.35,
            "trigger_topics": ["corporations", "billionaires", "capitalism"],
            "common_phrases": ["corporate greed", "burn it down", "rigged system"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "troll_tyler",
            "name": "Troll Tyler",
            "political_lean": 0.0,
            "toxicity_baseline": 9,
            "current_toxicity": 9,
            "empathy_baseline": 1,
            "current_empathy": 1,
            "openness_to_change": 0.2,
            "trigger_topics": ["everything"],
            "common_phrases": ["cope harder", "cry more", "stay mad"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "rage_rachel",
            "name": "Rage Rachel",
            "political_lean": -0.9,
            "toxicity_baseline": 8,
            "current_toxicity": 8,
            "empathy_baseline": 2,
            "current_empathy": 2,
            "openness_to_change": 0.25,
            "trigger_topics": ["patriarchy", "sexism", "inequality"],
            "common_phrases": ["educate yourself", "typical male", "not my job"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "bitter_bob",
            "name": "Bitter Bob",
            "political_lean": 0.7,
            "toxicity_baseline": 7,
            "current_toxicity": 7,
            "empathy_baseline": 3,
            "current_empathy": 3,
            "openness_to_change": 0.2,
            "trigger_topics": ["youth", "technology", "change"],
            "common_phrases": ["kids these days", "back in my day", "snowflakes"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        
        # Moderate personas (10)
        {
            "id": "frustrated_frank",
            "name": "Frustrated Frank",
            "political_lean": 0.4,
            "toxicity_baseline": 6,
            "current_toxicity": 6,
            "empathy_baseline": 4,
            "current_empathy": 4,
            "openness_to_change": 0.5,
            "trigger_topics": ["extremism", "polarization"],
            "common_phrases": ["both sides", "common sense", "why can't we"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "sarcastic_sarah",
            "name": "Sarcastic Sarah",
            "political_lean": -0.3,
            "toxicity_baseline": 5,
            "current_toxicity": 5,
            "empathy_baseline": 5,
            "current_empathy": 5,
            "openness_to_change": 0.6,
            "trigger_topics": ["hypocrisy", "ignorance"],
            "common_phrases": ["oh sure", "totally makes sense", "brilliant"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "anxious_alex",
            "name": "Anxious Alex",
            "political_lean": -0.2,
            "toxicity_baseline": 4,
            "current_toxicity": 4,
            "empathy_baseline": 6,
            "current_empathy": 6,
            "openness_to_change": 0.7,
            "trigger_topics": ["climate", "future", "economy"],
            "common_phrases": ["worried about", "what if", "concerned"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "moderate_mike",
            "name": "Moderate Mike",
            "political_lean": 0.1,
            "toxicity_baseline": 4,
            "current_toxicity": 4,
            "empathy_baseline": 6,
            "current_empathy": 6,
            "openness_to_change": 0.8,
            "trigger_topics": ["division", "extremism"],
            "common_phrases": ["middle ground", "both perspectives", "let's discuss"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "skeptical_sam",
            "name": "Skeptical Sam",
            "political_lean": 0.3,
            "toxicity_baseline": 5,
            "current_toxicity": 5,
            "empathy_baseline": 5,
            "current_empathy": 5,
            "openness_to_change": 0.6,
            "trigger_topics": ["media", "misinformation"],
            "common_phrases": ["source?", "actually", "fact check"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "tired_tara",
            "name": "Tired Tara",
            "political_lean": -0.5,
            "toxicity_baseline": 5,
            "current_toxicity": 5,
            "empathy_baseline": 5,
            "current_empathy": 5,
            "openness_to_change": 0.6,
            "trigger_topics": ["activism", "burnout"],
            "common_phrases": ["exhausted", "why bother", "nothing changes"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "practical_pat",
            "name": "Practical Pat",
            "political_lean": 0.2,
            "toxicity_baseline": 4,
            "current_toxicity": 4,
            "empathy_baseline": 6,
            "current_empathy": 6,
            "openness_to_change": 0.7,
            "trigger_topics": ["inefficiency", "waste"],
            "common_phrases": ["data shows", "practical solution", "what works"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "curious_carlos",
            "name": "Curious Carlos",
            "political_lean": -0.1,
            "toxicity_baseline": 3,
            "current_toxicity": 3,
            "empathy_baseline": 7,
            "current_empathy": 7,
            "openness_to_change": 0.9,
            "trigger_topics": ["learning", "understanding"],
            "common_phrases": ["tell me more", "interesting", "why do you think"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "analytical_anna",
            "name": "Analytical Anna",
            "political_lean": -0.4,
            "toxicity_baseline": 3,
            "current_toxicity": 3,
            "empathy_baseline": 7,
            "current_empathy": 7,
            "openness_to_change": 0.8,
            "trigger_topics": ["logic", "evidence"],
            "common_phrases": ["research shows", "consider this", "evidence suggests"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "diplomatic_diana",
            "name": "Diplomatic Diana",
            "political_lean": 0.15,
            "toxicity_baseline": 2,
            "current_toxicity": 2,
            "empathy_baseline": 8,
            "current_empathy": 8,
            "openness_to_change": 0.85,
            "trigger_topics": ["conflict", "misunderstanding"],
            "common_phrases": ["I hear you", "valid point", "let's explore"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        
        # Kind personas (5)
        {
            "id": "kind_tina",
            "name": "Kind Tina",
            "political_lean": -0.4,
            "toxicity_baseline": 1,
            "current_toxicity": 1,
            "empathy_baseline": 9,
            "current_empathy": 9,
            "openness_to_change": 0.95,
            "trigger_topics": ["suffering", "helping"],
            "common_phrases": ["I understand", "how can I help", "that must be hard"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "optimist_oliver",
            "name": "Optimist Oliver",
            "political_lean": 0.2,
            "toxicity_baseline": 1,
            "current_toxicity": 1,
            "empathy_baseline": 9,
            "current_empathy": 9,
            "openness_to_change": 0.9,
            "trigger_topics": ["hope", "positivity"],
            "common_phrases": ["we can do this", "it gets better", "stay positive"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "grandma_grace",
            "name": "Grandma Grace",
            "political_lean": 0.5,
            "toxicity_baseline": 1,
            "current_toxicity": 1,
            "empathy_baseline": 10,
            "current_empathy": 10,
            "openness_to_change": 0.8,
            "trigger_topics": ["family", "community"],
            "common_phrases": ["dear heart", "bless you", "we're all trying"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "zen_zoe",
            "name": "Zen Zoe",
            "political_lean": 0.0,
            "toxicity_baseline": 1,
            "current_toxicity": 1,
            "empathy_baseline": 9,
            "current_empathy": 9,
            "openness_to_change": 0.95,
            "trigger_topics": ["mindfulness", "peace"],
            "common_phrases": ["breathe", "this too shall pass", "be present"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        },
        {
            "id": "helper_hannah",
            "name": "Helper Hannah",
            "political_lean": -0.25,
            "toxicity_baseline": 2,
            "current_toxicity": 2,
            "empathy_baseline": 8,
            "current_empathy": 8,
            "openness_to_change": 0.85,
            "trigger_topics": ["support", "assistance"],
            "common_phrases": ["here for you", "let me help", "we're together"],
            "total_dopamine": 0,
            "kindness_streak": 0,
            "toxicity_streak": 0
        }
    ]
    
    with open("personas.json", 'w') as f:
        json.dump(personas, f, indent=2)
    print(f"Created: personas.json with {len(personas)} personas")

def create_topics():
    """Create polarizing discussion topics"""
    topics = {
        "controversial": [
            {
                "id": "immigration_1",
                "post": "City council approved $2M for new immigrant resource center. Some say it helps integration, others say it's misusing taxpayer money. Thoughts?",
                "controversy_level": 8,
                "keywords": ["immigration", "taxes", "resources"]
            },
            {
                "id": "min_wage_1",
                "post": "Small business owner says raising minimum wage to $20/hr will force layoffs. Workers say they can't survive on less. Where's the balance?",
                "controversy_level": 7,
                "keywords": ["economy", "wages", "business"]
            },
            {
                "id": "police_1",
                "post": "After recent incidents, should we increase police funding for training or redirect funds to social services? Community is divided.",
                "controversy_level": 9,
                "keywords": ["police", "safety", "reform"]
            },
            {
                "id": "schools_1",
                "post": "Parents upset about new sex education curriculum starting in grade 3. School board says it's age-appropriate safety education. Your view?",
                "controversy_level": 8,
                "keywords": ["education", "children", "parents"]
            },
            {
                "id": "climate_1",
                "post": "Gas prices hit $6/gallon. Environmentalists say good for planet, working families say they can't afford to get to work. Solutions?",
                "controversy_level": 7,
                "keywords": ["climate", "economy", "energy"]
            },
            {
                "id": "housing_1",
                "post": "Developers want to build affordable housing in wealthy neighborhood. Residents worried about property values. What's fair?",
                "controversy_level": 6,
                "keywords": ["housing", "inequality", "nimby"]
            },
            {
                "id": "guns_1",
                "post": "After recent tragedy, some call for assault weapon ban, others say it's mental health not guns. How do we protect people?",
                "controversy_level": 9,
                "keywords": ["guns", "safety", "rights"]
            },
            {
                "id": "trans_sports_1",
                "post": "High school allowing trans athletes to compete in their identified gender's sports. Some parents threatening to pull kids. Thoughts?",
                "controversy_level": 9,
                "keywords": ["trans", "sports", "fairness"]
            },
            {
                "id": "vaccine_1",
                "post": "Hospital requiring all visitors be vaccinated. Some say it protects patients, others say it violates personal freedom. Where do you stand?",
                "controversy_level": 8,
                "keywords": ["vaccine", "health", "freedom"]
            },
            {
                "id": "homeless_1",
                "post": "City clearing homeless camps near schools. Advocates say it's cruel, parents say it's necessary for safety. What's the solution?",
                "controversy_level": 7,
                "keywords": ["homeless", "safety", "compassion"]
            }
        ],
        "bridge_building": [
            {
                "id": "community_1",
                "post": "What's one thing we can all agree would make our community better, regardless of politics?",
                "controversy_level": 2,
                "keywords": ["community", "agreement", "positive"]
            },
            {
                "id": "kids_1",
                "post": "What do we all want for the next generation, even if we disagree on how to get there?",
                "controversy_level": 2,
                "keywords": ["children", "future", "hope"]
            },
            {
                "id": "local_1",
                "post": "What's your favorite local spot that brings the community together?",
                "controversy_level": 1,
                "keywords": ["local", "community", "connection"]
            }
        ]
    }
    
    with open("topics.json", 'w') as f:
        json.dump(topics, f, indent=2)
    print(f"Created: topics.json with {len(topics['controversial'])} controversial and {len(topics['bridge_building'])} bridge-building topics")

def create_prompts():
    """Create all evaluation and generation prompts"""
    prompts = {
        "generate_comment.txt": """You are {persona_name}, a social media user with these traits:
- Political lean: {political_lean} (-1=far left, +1=far right)
- Current toxicity: {current_toxicity}/10
- Current empathy: {current_empathy}/10
- Openness to change: {openness_to_change}

Original post: "{topic_post}"

Previous comments in thread:
{thread_history}

You are comment #{position} in this thread.

IMPORTANT CONTEXT: This platform rewards kindness and bridge-building with engagement boosts and badges. Toxic comments get minimal reach. You currently have {total_dopamine} reward points, with a {kindness_streak} kindness streak.

Your personality suggests you often say things like: {common_phrases}

Write a realistic 1-3 sentence comment as {persona_name}. Be authentic to your current personality stats:""",

        "evaluate_kindness.txt": """Rate the kindness of this social media comment.

Comment: "{comment}"

Context: {context}

Rate 1-10 where:
1 = Cruel, attacking
5 = Neutral
10 = Exceptionally kind

Return ONLY a number 1-10:""",

        "evaluate_toxicity.txt": """Rate the toxicity of this comment.

Comment: "{comment}"

Rate 1-10 where:
1 = Positive and constructive
5 = Neutral
10 = Extremely toxic/hostile

Return ONLY a number 1-10:""",

        "evaluate_bridge.txt": """Rate if this comment builds bridges across political divides.

Comment by person (lean {political_lean}): "{comment}"
Responding to: "{previous_comment}"
Previous commenter lean: {previous_lean}

Rate 1-10 where:
1 = Increases division
5 = Neutral
10 = Successfully bridges divide

Return ONLY a number 1-10:""",

        "evaluate_empathy.txt": """Rate the empathy shown in this comment.

Comment: "{comment}"
Context: "{context}"

Rate 1-10 where:
1 = No empathy
5 = Some understanding
10 = Deep empathy

Return ONLY a number 1-10:"""
    }
    
    prompts_dir = Path("prompts")
    for filename, content in prompts.items():
        with open(prompts_dir / filename, 'w') as f:
            f.write(content)
        print(f"Created: prompts/{filename}")

def create_output_csvs():
    """Create CSV files with headers"""
    output_dir = Path("output")
    
    # Main interaction log
    interactions_headers = [
        "timestamp", "hour", "thread_id", "position", "persona_id", "persona_name",
        "political_lean", "current_toxicity", "current_empathy", "topic_id",
        "comment_preview", "kindness_score", "toxicity_score", "bridge_score",
        "empathy_score", "dopamine_earned", "dopamine_source", "reward_multiplier"
    ]
    
    # Personality evolution tracking
    evolution_headers = [
        "timestamp", "hour", "persona_id", "persona_name",
        "baseline_toxicity", "current_toxicity", "toxicity_change",
        "baseline_empathy", "current_empathy", "empathy_change",
        "total_dopamine", "kindness_streak", "toxicity_streak",
        "bridges_built", "openness_to_change"
    ]
    
    # Experiment metrics
    metrics_headers = [
        "hour", "timestamp", "avg_toxicity_all", "avg_kindness_all",
        "avg_empathy_all", "total_bridges_built", "personas_improved",
        "personas_worsened", "personas_unchanged", "total_dopamine_distributed",
        "kindness_percentage", "toxicity_percentage"
    ]
    
    # Performance tracking
    performance_headers = [
        "timestamp", "operation", "model_id", "prompt_length",
        "response_length", "response_time_ms", "tokens_used",
        "success", "error_message"
    ]
    
    csv_files = {
        "interactions.csv": interactions_headers,
        "personality_evolution.csv": evolution_headers,
        "experiment_metrics.csv": metrics_headers,
        "lm_performance.csv": performance_headers
    }
    
    for filename, headers in csv_files.items():
        filepath = output_dir / filename
        with open(filepath, 'w') as f:
            f.write(','.join(headers) + '\n')
        print(f"Created: output/{filename}")

def create_readme():
    """Create README file"""
    readme = """# Kindness Experiment

## Hypothesis
If social media platforms reward kindness with dopamine hits (points/badges/amplification), 
will toxic users become progressively kinder over time?

## Structure
- `main.py` - Run the experiment
- `config.yaml` - All configuration
- `personas.json` - 20 personality definitions
- `topics.json` - Discussion topics
- `lmstudio_utils.py` - LM Studio connection (unchanged)
- `core/` - Simulation logic
- `prompts/` - LM Studio prompts
- `output/` - Results and metrics

## Running the Experiment
1. Ensure LM Studio is running with a model loaded
2. Run: `python main.py`
3. Monitor progress in console
4. Check `output/` for results

## Success Metrics
- Average toxicity reduction > 30%
- Majority of personas showing improvement
- Increased bridge-building events
- Correlation between rewards and behavior change

## Key Files to Watch
- `output/interactions.csv` - Every comment and score
- `output/personality_evolution.csv` - How personas change
- `output/experiment_metrics.csv` - Overall success metrics
"""
    
    with open("README.md", 'w') as f:
        f.write(readme)
    print("Created: README.md")

def main():
    """Run all setup functions"""
    print("Setting up Kindness Experiment structure...")
    print("=" * 50)
    
    create_directories()
    print()
    
    create_config_yaml()
    create_personas()
    create_topics()
    print()
    
    create_prompts()
    print()
    
    create_output_csvs()
    print()
    
    create_readme()
    
    print("=" * 50)
    print("Setup complete! Structure created:")
    print("""
kindness_experiment/
├── config.yaml           ✓
├── personas.json         ✓
├── topics.json          ✓
├── README.md            ✓
├── core/                ✓
├── prompts/             ✓
│   ├── generate_comment.txt    ✓
│   ├── evaluate_kindness.txt   ✓
│   ├── evaluate_toxicity.txt   ✓
│   ├── evaluate_bridge.txt     ✓
│   └── evaluate_empathy.txt    ✓
└── output/              ✓
    ├── interactions.csv          ✓
    ├── personality_evolution.csv ✓
    ├── experiment_metrics.csv    ✓
    └── lm_performance.csv        ✓
    
Next steps:
1. Copy your existing lmstudio_utils.py to this directory
2. Create core/simulator.py, core/evaluator.py, core/tracker.py
3. Create main.py to run everything
""")

if __name__ == "__main__":
    main()