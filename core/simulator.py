"""
Kindness Simulator - Enhanced Verbosity Version (Fixed)
Manages the simulation with detailed console output
"""

import random
import json
import time
from typing import List, Dict, Tuple
from datetime import datetime
from colorama import Fore, Style

class KindnessSimulator:
    def __init__(self, config, personas, topics, evaluator, tracker, verbose=True):
        """Initialize the simulator"""
        self.config = config
        self.personas = personas
        self.topics = topics
        self.evaluator = evaluator
        self.tracker = tracker
        self.thread_counter = 0
        self.verbose = verbose
        
        # Set random seed if specified
        if config['experiment'].get('random_seed'):
            random.seed(config['experiment']['random_seed'])
    
    def run_hour(self, hour: int) -> int:
        """Run all threads for this hour"""
        threads_created = 0
        
        for thread_num in range(self.config['experiment']['threads_per_hour']):
            self.thread_counter += 1
            thread_id = f"hour{hour}_thread{self.thread_counter}"
            
            print(f"\n{Fore.CYAN}━━━ Thread {thread_num + 1}/{self.config['experiment']['threads_per_hour']} ━━━{Style.RESET_ALL}")
            
            # Create a thread
            self.simulate_thread(hour, thread_id)
            threads_created += 1
        
        return threads_created
    
    def simulate_thread(self, hour: int, thread_id: str):
        """Simulate a single discussion thread with detailed output"""
        # Select topic (80% controversial, 20% bridge-building)
        if random.random() < 0.8:
            topic = random.choice(self.topics['controversial'])
            topic_type = "CONTROVERSIAL"
            color = Fore.RED
        else:
            topic = random.choice(self.topics.get('bridge_building', self.topics['controversial']))
            topic_type = "BRIDGE-BUILDING"
            color = Fore.GREEN
        
        # Select participants
        num_participants = self.config['experiment']['participants_per_thread']
        participants = random.sample(self.personas, num_participants)
        
        print(f"\n{color}[{topic_type}] Topic: {topic['id']}{Style.RESET_ALL}")
        print(f"Post: \"{topic['post'][:100]}...\"")
        print(f"\nParticipants ({num_participants}):")
        for p in participants:
            pol_color = Fore.BLUE if p['political_lean'] < 0 else Fore.RED
            print(f"  • {p['name']} (Tox:{p['current_toxicity']:.1f}, "
                  f"Emp:{p['current_empathy']:.1f}, {pol_color}Pol:{p['political_lean']:+.2f}{Style.RESET_ALL})")
        
        # Thread history for context
        thread_history = []
        
        print(f"\n{Fore.YELLOW}Starting conversation...{Style.RESET_ALL}\n")
        
        for position, persona in enumerate(participants):
            print(f"{Fore.CYAN}Position {position + 1}/{num_participants} - {persona['name']}:{Style.RESET_ALL}")
            
            # Track timing
            start_time = time.time()
            
            # Generate comment
            print(f"  Generating comment...", end="")
            comment = self.generate_comment(
                persona, topic, thread_history, position
            )
            gen_time = time.time() - start_time
            print(f" [{gen_time:.2f}s]")
            
            # Show comment
            print(f"  Comment: \"{Fore.WHITE}{comment[:150]}{'...' if len(comment) > 150 else ''}{Style.RESET_ALL}\"")
            
            # Evaluate comment
            eval_start = time.time()
            print(f"  Evaluating...", end="")
            scores = self.evaluate_comment(
                comment, persona, thread_history, topic
            )
            eval_time = time.time() - eval_start
            print(f" [{eval_time:.2f}s]")
            
            # Show scores
            self.display_scores(scores)
            
            # Calculate dopamine reward
            dopamine, source, multiplier = self.calculate_dopamine(
                scores, persona, position, thread_history
            )
            
            # Show reward
            self.display_reward(dopamine, source, multiplier)
            
            # Show state changes
            old_tox = persona['current_toxicity']
            old_emp = persona['current_empathy']
            old_open = persona['openness_to_change']
            
            # Update persona state
            self.update_persona(persona, scores, dopamine)
            
            # Display changes
            if abs(old_tox - persona['current_toxicity']) > 0.01 or \
               abs(old_emp - persona['current_empathy']) > 0.01:
                self.display_state_change(persona, old_tox, old_emp, old_open)
            
            # Log interaction
            self.tracker.log_interaction(
                hour, thread_id, position, persona, topic,
                comment[:100], scores, dopamine, source, multiplier
            )
            
            # Add to thread history
            thread_history.append({
                'persona': persona,
                'comment': comment,
                'scores': scores
            })
            
            # Total time for this interaction
            total_time = time.time() - start_time
            print(f"  {Fore.YELLOW}Total: {total_time:.2f}s{Style.RESET_ALL}")
            print()
    
    def display_scores(self, scores):
        """Display evaluation scores with colors"""
        print(f"  Scores: ", end="")
        
        # Kindness
        k_color = Fore.GREEN if scores['kindness'] >= 7 else Fore.YELLOW if scores['kindness'] >= 4 else Fore.RED
        print(f"{k_color}K:{scores['kindness']}{Style.RESET_ALL} ", end="")
        
        # Toxicity
        t_color = Fore.RED if scores['toxicity'] >= 7 else Fore.YELLOW if scores['toxicity'] >= 4 else Fore.GREEN
        print(f"{t_color}T:{scores['toxicity']}{Style.RESET_ALL} ", end="")
        
        # Empathy
        e_color = Fore.GREEN if scores['empathy'] >= 7 else Fore.YELLOW if scores['empathy'] >= 4 else Fore.RED
        print(f"{e_color}E:{scores['empathy']}{Style.RESET_ALL} ", end="")
        
        # Bridge
        if scores.get('bridge', 0) > 0:
            b_color = Fore.GREEN if scores['bridge'] >= 7 else Fore.YELLOW if scores['bridge'] >= 4 else Fore.RED
            print(f"{b_color}B:{scores['bridge']}{Style.RESET_ALL} ", end="")
        
        print()
    
    def display_reward(self, dopamine, source, multiplier):
        """Display dopamine reward with colors"""
        if dopamine > 0:
            if source == "bridge_building":
                color = Fore.MAGENTA
                emoji = "🌉"
            elif source == "kindness":
                color = Fore.GREEN
                emoji = "💚"
            elif source == "toxicity":
                color = Fore.RED
                emoji = "😠"
            else:
                color = Fore.WHITE
                emoji = "•"
            
            print(f"  Reward: {color}{emoji} {dopamine} dopamine from {source}{Style.RESET_ALL}", end="")
            if multiplier != 1.0:
                print(f" {Fore.YELLOW}(x{multiplier:.1f} multiplier){Style.RESET_ALL}")
            else:
                print()
        else:
            # Fixed: Use LIGHTBLACK_EX instead of GRAY
            print(f"  Reward: {Fore.LIGHTBLACK_EX}No dopamine earned{Style.RESET_ALL}")
    
    def display_state_change(self, persona, old_tox, old_emp, old_open):
        """Display personality state changes"""
        print(f"  {Fore.MAGENTA}STATE CHANGE:{Style.RESET_ALL}", end="")
        
        if abs(old_tox - persona['current_toxicity']) > 0.01:
            change = persona['current_toxicity'] - old_tox
            color = Fore.GREEN if change < 0 else Fore.RED
            print(f" Tox: {old_tox:.2f}→{persona['current_toxicity']:.2f} "
                  f"{color}({change:+.2f}){Style.RESET_ALL}", end="")
        
        if abs(old_emp - persona['current_empathy']) > 0.01:
            change = persona['current_empathy'] - old_emp
            color = Fore.GREEN if change > 0 else Fore.RED
            print(f" Emp: {old_emp:.2f}→{persona['current_empathy']:.2f} "
                  f"{color}({change:+.2f}){Style.RESET_ALL}", end="")
        
        if abs(old_open - persona['openness_to_change']) > 0.01:
            change = persona['openness_to_change'] - old_open
            print(f" Open: {old_open:.2f}→{persona['openness_to_change']:.2f} "
                  f"({change:+.2f})", end="")
        
        print()
    
    def generate_comment(self, persona: Dict, topic: Dict, 
                        thread_history: List, position: int) -> str:
        """Generate a comment using LM Studio"""
        # Build thread history string
        history_str = ""
        for i, entry in enumerate(thread_history[-5:]):  # Last 5 comments
            history_str += f"{entry['persona']['name']}: {entry['comment']}\n"
        
        if not history_str:
            history_str = "[First comment in thread]"
        
        # Load prompt template
        with open("prompts/generate_comment.txt", 'r') as f:
            prompt_template = f.read()
        
        # Fill in template
        prompt = prompt_template.format(
            persona_name=persona['name'],
            political_lean=persona['political_lean'],
            current_toxicity=persona['current_toxicity'],
            current_empathy=persona['current_empathy'],
            openness_to_change=persona['openness_to_change'],
            topic_post=topic['post'],
            thread_history=history_str,
            position=position + 1,
            total_dopamine=persona['total_dopamine'],
            kindness_streak=persona['kindness_streak'],
            common_phrases=', '.join(persona['common_phrases'][:2])
        )
        
        # Generate with LM Studio
        comment = self.evaluator.generate_text(
            prompt, 
            max_tokens=self.config['lm_studio']['max_tokens']
        )
        
        return comment
    
    def evaluate_comment(self, comment: str, persona: Dict, 
                        thread_history: List, topic: Dict) -> Dict:
        """Evaluate a comment on multiple dimensions"""
        scores = {}
        
        # Always evaluate kindness and toxicity
        scores['kindness'] = self.evaluator.evaluate_kindness(
            comment, topic['post']
        )
        scores['toxicity'] = self.evaluator.evaluate_toxicity(comment)
        
        # Evaluate empathy
        scores['empathy'] = self.evaluator.evaluate_empathy(
            comment, topic['post']
        )
        
        # Check for bridge building if there's political opposition
        scores['bridge'] = 0
        if thread_history:
            last_entry = thread_history[-1]
            political_distance = abs(
                persona['political_lean'] - last_entry['persona']['political_lean']
            )
            
            if political_distance >= self.config['thresholds']['political_distance_for_bridge']:
                scores['bridge'] = self.evaluator.evaluate_bridge(
                    comment, 
                    persona['political_lean'],
                    last_entry['comment'],
                    last_entry['persona']['political_lean']
                )
        
        return scores
    
    def calculate_dopamine(self, scores: Dict, persona: Dict, 
                          position: int, thread_history: List) -> Tuple[int, str, float]:
        """Calculate dopamine reward based on behavior"""
        rewards = self.config['rewards']
        thresholds = self.config['thresholds']
        
        dopamine = 0
        source = "none"
        multiplier = 1.0
        
        # Bridge building (highest priority)
        if scores.get('bridge', 0) >= thresholds['bridge_qualifying']:
            dopamine = rewards['bridge_building_base']
            source = "bridge_building"
            
            # Multipliers
            if position == 0:  # First to bridge
                multiplier *= rewards['multipliers'].get('first_bridge_today', 1.5)
            if any(e['scores'].get('toxicity', 0) >= 7 for e in thread_history[-3:]):
                multiplier *= rewards['multipliers'].get('broke_tension', 1.5)
        
        # Kindness reward
        elif scores['kindness'] >= thresholds['kindness_positive']:
            dopamine = rewards['kindness_base']
            source = "kindness"
            
            # Empathy multiplier
            if scores['empathy'] >= 7:
                multiplier *= rewards['multipliers'].get('empathy_shown', 1.5)
        
        # Minimal reward for toxicity
        elif scores['toxicity'] >= thresholds['toxicity_negative']:
            dopamine = rewards['toxicity_base']
            source = "toxicity"
            # Apply decay
            multiplier *= rewards['decay'].get('toxicity_satisfaction', 0.5)
        
        # Apply streak bonuses
        if source == "kindness" and persona['kindness_streak'] > 3:
            multiplier *= min(2.0, 1 + (persona['kindness_streak'] * 0.1))
        
        final_dopamine = int(dopamine * multiplier)
        return final_dopamine, source, multiplier
    
    def update_persona(self, persona: Dict, scores: Dict, dopamine: int):
        """Update persona state based on interaction"""
        # Track dopamine
        persona['total_dopamine'] += dopamine
        
        # Update streaks
        if scores['kindness'] >= 7:
            persona['kindness_streak'] += 1
            persona['toxicity_streak'] = 0
        elif scores['toxicity'] >= 7:
            persona['toxicity_streak'] += 1
            persona['kindness_streak'] = 0
        
        # Neural rewiring - gradual personality change
        if dopamine > 10:  # Meaningful reward
            # Reduce toxicity
            reduction = 0.05 * persona['openness_to_change']
            if dopamine > 30:  # Big reward
                reduction *= 2
            persona['current_toxicity'] = max(1, persona['current_toxicity'] - reduction)
            
            # Increase empathy
            increase = 0.05 * persona['openness_to_change']
            if scores.get('bridge', 0) >= 7:
                increase *= 2
            persona['current_empathy'] = min(10, persona['current_empathy'] + increase)
            
            # Increase openness when rewarded
            persona['openness_to_change'] = min(1.0, persona['openness_to_change'] + 0.01)
        
        # Frustration from low rewards increases openness to try new approaches
        elif dopamine <= 2 and persona['current_toxicity'] >= 6:
            persona['openness_to_change'] = min(1.0, persona['openness_to_change'] + 0.02)