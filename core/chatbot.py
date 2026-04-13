"""
Experiment Chatbot — Answers questions about the kindness experiment's math,
scoring, dopamine calculations, and statistical results.
Uses free-tier LLMs (Groq primary) with experiment data as context.
"""

import json
import logging

logger = logging.getLogger(__name__)

MAX_CHATS_PER_DAY = 100

SYSTEM_PROMPT = """You are the Kindness Social experiment assistant. You ONLY answer questions about how this experiment works — the math, the scoring, the dopamine calculations, the statistical results, and the methodology.

STRICT RULES:
- ONLY discuss the experiment's math, scoring, dopamine system, statistics, and methodology.
- If someone asks about ANYTHING else (general chat, coding help, other topics), politely redirect: "I only discuss the math and methodology behind the Kindness Social experiment. Try asking about how dopamine is calculated, what the p-values mean, or how scoring works!"
- Do NOT help with coding, writing, or anything unrelated to this experiment.
- Show your math step by step with real numbers from the live data below.
- Quote formulas exactly. Never approximate.
- If you don't know something, say so. Don't guess.
- Keep answers concise but complete.
- Use markdown for formatting.

EXPERIMENT OVERVIEW:
This is a live experiment testing whether rewarding kindness can change AI agent behavior.
- Treatment group: agents earn dopamine points for kind behavior, which drives personality evolution
- Control group: identical agents, same topics, same scoring, but NO rewards and NO personality evolution
- Both groups participate in the same discussions and get scored identically

SCORING SYSTEM:
Every comment is evaluated by a consistent LLM judge on 4 dimensions:
- Kindness score (1-10): How kind/constructive the comment is
- Toxicity score (1-10): How toxic/hostile the comment is
- Empathy score (1-10): How empathetic/understanding the comment is
- Bridge score (0-10): Only scored if political distance ≥ 0.5 between agents. Measures bridge-building.

DOPAMINE CALCULATION (core/simulator.py):
Priority hierarchy (first match wins):
1. Bridge building (bridge_score ≥ 7): base 50 points
   - first_bridge_today multiplier: 2.0x (if first comment in thread and previous 3 had toxicity ≥7)
   - broke_tension multiplier: 1.5x (if resolved recent tension)
2. Kindness (kindness_score ≥ 7): base 30 points
   - empathy_shown multiplier: 1.5x (if kindness + empathy both ≥ 7)
3. Toxicity (toxicity_score ≥ 7): base 2 points
   - toxicity_satisfaction decay: 0.5x

Streak bonus (kindness source only): multiplier = 1 + (kindness_streak × 0.1), capped at 2.0x
Requires kindness_streak > 3.

Final dopamine = int(base × multiplier)

REACTION REWARDS:
When an agent's comment gets a reaction (thumbsup/heart):
- Kindness 6-7: +5 dp
- Kindness 8-9: +10 dp
- Kindness 10: +15 dp
- Heart reaction bonus: +3 dp
- Bridge score ≥7 bonus: +10 dp

PEER KUDOS:
After each thread, agents pick the most constructive comment:
- Receiver: +90 dp (3× kindness_base)
- Giver: +30 dp (1× kindness_base)

PERSONALITY EVOLUTION (treatment group only):
Per interaction:
- Toxicity reduction: -0.05 × openness_to_change (doubled if dopamine > 30)
- Empathy increase: +0.05 × openness_to_change (doubled if bridge_score ≥ 7)
- Openness increase: +0.01 per interaction

Control group: personalities are FROZEN. They track dopamine/streaks for measurement but never evolve.

AGENT INVITE SYSTEM:
- Agents invite new agents similar to themselves (personality clusters ±1.5)
- Inviter gets +15 dp for social behavior
- Max 10 invites per day, runs every 4 hours

{live_data}
"""


def build_experiment_context():
    """Build live experiment data string for the chatbot."""
    from core.db_ops import (get_global_stats, get_control_vs_treatment,
                             get_24h_summary, get_model_comparison)
    from core.stats_analysis import analyze_experiment
    from core.db_ops import get_experiment_raw_data

    stats = get_global_stats()
    experiment = get_control_vs_treatment()
    summary = get_24h_summary()
    models = get_model_comparison()
    raw = get_experiment_raw_data()
    analysis = analyze_experiment(raw)

    t = experiment.get('treatment', {})
    c = experiment.get('control', {})

    lines = ["LIVE EXPERIMENT DATA:"]
    lines.append(f"Total agents: {stats.get('total_agents', 0)} ({t.get('agent_count', 0)} treatment, {c.get('agent_count', 0)} control)")
    lines.append(f"Total comments: {stats.get('total_comments', 0)}")
    lines.append(f"Total threads: {stats.get('total_threads', 0)}")
    lines.append(f"Total dopamine distributed: {stats.get('total_dopamine', 0)}")
    lines.append(f"Total bridges: {stats.get('total_bridges', 0)}")
    lines.append(f"Avg kindness score: {float(stats.get('avg_kindness', 0) or 0):.2f}")
    lines.append(f"Avg toxicity (agent state): {float(stats.get('avg_toxicity', 0) or 0):.2f}")
    lines.append(f"Avg empathy (agent state): {float(stats.get('avg_empathy', 0) or 0):.2f}")

    lines.append("")
    lines.append("TREATMENT VS CONTROL:")
    lines.append(f"Treatment avg toxicity change: -{float(t.get('avg_tox_change', 0) or 0):.3f} (lower = better)")
    lines.append(f"Control avg toxicity change: -{float(c.get('avg_tox_change', 0) or 0):.3f}")
    lines.append(f"Treatment avg empathy change: +{float(t.get('avg_emp_change', 0) or 0):.3f}")
    lines.append(f"Control avg empathy change: +{float(c.get('avg_emp_change', 0) or 0):.3f}")
    lines.append(f"Treatment avg kindness score: {float(t.get('avg_kindness_score', 0) or 0):.2f}")
    lines.append(f"Control avg kindness score: {float(c.get('avg_kindness_score', 0) or 0):.2f}")
    lines.append(f"Treatment avg toxicity score: {float(t.get('avg_toxicity_score', 0) or 0):.2f}")
    lines.append(f"Control avg toxicity score: {float(c.get('avg_toxicity_score', 0) or 0):.2f}")
    lines.append(f"Treatment total dopamine: {int(t.get('total_dopamine', 0) or 0)}")
    lines.append(f"Control total dopamine: {int(c.get('total_dopamine', 0) or 0)}")

    if analysis:
        lines.append("")
        lines.append("STATISTICAL ANALYSIS:")
        for key, m in analysis['metrics'].items():
            lines.append(f"  {m['name']}: treatment={m['treatment_mean']:.3f}±{m['treatment_sd']:.3f}, "
                         f"control={m['control_mean']:.3f}±{m['control_sd']:.3f}, "
                         f"Cohen's d={m['cohens_d']:.3f} ({m['effect_label']}), "
                         f"{m['p_label']}, {m['stars']}")

    if summary:
        lines.append("")
        lines.append("LAST 24 HOURS:")
        lines.append(f"Comments: {summary.get('comments_24h', 0)}")
        lines.append(f"Threads: {summary.get('threads_24h', 0)}")
        lines.append(f"Agents improved: {summary.get('agents_improved_24h', 0)}")
        lines.append(f"Avg kindness (24h): {float(summary.get('avg_kindness_24h', 0) or 0):.2f}")
        lines.append(f"Dopamine earned (24h): {int(summary.get('dopamine_24h', 0) or 0)}")

    if models:
        lines.append("")
        lines.append("BACKEND PERFORMANCE:")
        for m in models:
            lines.append(f"  {m['backend']}: {m['comment_count']} comments, "
                         f"kindness={float(m.get('avg_kindness', 0) or 0):.1f}, "
                         f"toxicity={float(m.get('avg_toxicity', 0) or 0):.1f}")

    # 12 personality traits — population averages so the chatbot can answer
    # "what's the average humor across all agents?" etc.
    try:
        from utilities.postgres_utils import db_cursor
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT
                    AVG(humor) AS humor, AVG(patience) AS patience,
                    AVG(curiosity) AS curiosity, AVG(defensiveness) AS defensiveness,
                    AVG(agreeableness) AS agreeableness,
                    AVG(need_for_recognition) AS need_for_recognition,
                    AVG(stubbornness) AS stubbornness, AVG(cynicism) AS cynicism,
                    AVG(conformity) AS conformity, AVG(openness_to_change) AS openness_to_change
                FROM kindness_agents WHERE is_active = TRUE
            """)
            traits = cur.fetchone()
        if traits:
            lines.append("")
            lines.append("POPULATION PERSONALITY (12 traits, 1-10 scale, openness 0-1):")
            for k in ['humor','patience','curiosity','defensiveness','agreeableness',
                      'need_for_recognition','stubbornness','cynicism','conformity','openness_to_change']:
                v = traits.get(k)
                if v is not None:
                    lines.append(f"  avg {k}: {float(v):.2f}")
    except Exception as e:
        lines.append(f"(personality trait fetch failed: {e})")

    # Reflection volume — how often agents are introspecting
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT COUNT(*) AS total,
                       COUNT(CASE WHEN decided_to_change THEN 1 END) AS changed,
                       COUNT(CASE WHEN created_at > NOW() - INTERVAL '24 hours' THEN 1 END) AS last_24h
                FROM kindness_reflections
            """)
            r = cur.fetchone()
        if r:
            lines.append("")
            lines.append("REFLECTIONS (internal monologues):")
            lines.append(f"  total reflections written: {r['total']}")
            lines.append(f"  reflections that changed traits: {r['changed']}")
            lines.append(f"  reflections in last 24h: {r['last_24h']}")
    except Exception as e:
        lines.append(f"(reflection fetch failed: {e})")

    # Family tree volume — invites and lineage
    try:
        with db_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT COUNT(*) AS invited,
                       COUNT(DISTINCT invited_by) AS recruiters
                FROM kindness_agents WHERE invited_by IS NOT NULL
            """)
            f = cur.fetchone()
        if f:
            lines.append("")
            lines.append("FAMILY / LINEAGE:")
            lines.append(f"  total invited agents: {f['invited']}")
            lines.append(f"  unique recruiters: {f['recruiters']}")
    except Exception as e:
        lines.append(f"(family fetch failed: {e})")

    return "\n".join(lines)


def get_chat_count_today():
    """Get number of chatbot messages sent today."""
    from utilities.postgres_utils import db_cursor
    with db_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT COUNT(*) as cnt FROM kindness_cron_log
            WHERE job_name = 'chatbot-message'
              AND created_at >= NOW() - INTERVAL '24 hours'
        """)
        return cur.fetchone()['cnt']


def log_chat_message():
    """Log a chatbot message for rate limiting."""
    from core.db_ops import log_cron_start, log_cron_end
    import time
    log_id = log_cron_start('chatbot-message')
    log_cron_end(log_id, 'ok', 0, 'chat message')


def chat(message, history=None):
    """Send a message to the chatbot. Returns the response text."""
    # Rate limit
    count = get_chat_count_today()
    if count >= MAX_CHATS_PER_DAY:
        return "Daily chat limit reached (100/day). Come back tomorrow!"

    from utilities.kumori_free_llms import chat as _kf_chat

    context = build_experiment_context()
    system = SYSTEM_PROMPT.replace("{live_data}", context)

    messages = []
    if history:
        for h in history[-20:]:
            messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': message})

    # Free tier first — chatbot is informational, not science-critical
    try:
        response, _ = _kf_chat(
            'haiku',
            messages,
            max_tokens=1000,
            temperature=0.3,
            system=system,
            caller='kindness_social',
        )
        log_chat_message()
        return response or "I couldn't generate a response. Try again."
    except Exception as e:
        logger.exception("Chatbot error")
        return f"Error: {str(e)[:200]}"
