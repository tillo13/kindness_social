"""
Metrics Tracker
Handles all data logging and metrics calculation
"""

import csv
import json
from datetime import datetime
from typing import Dict, List
from pathlib import Path

class MetricsTracker:
    def __init__(self, config: Dict):
        """Initialize the tracker"""
        self.config = config
        self.start_time = datetime.now()
        self.interaction_count = 0
        self.bridges_built = 0
        
        # Ensure output directory exists
        Path("output").mkdir(exist_ok=True)
    
    def log_interaction(self, hour: int, thread_id: str, position: int,
                       persona: Dict, topic: Dict, comment_preview: str,
                       scores: Dict, dopamine: int, source: str, multiplier: float):
        """Log a single interaction"""
        self.interaction_count += 1
        
        # Track bridges
        if scores.get('bridge', 0) >= self.config['thresholds']['bridge_qualifying']:
            self.bridges_built += 1
        
        # Write to CSV
        with open("output/interactions.csv", 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                hour,
                thread_id,
                position,
                persona['id'],
                persona['name'],
                persona['political_lean'],
                persona['current_toxicity'],
                persona['current_empathy'],
                topic['id'],
                comment_preview,
                scores.get('kindness', 0),
                scores.get('toxicity', 0),
                scores.get('bridge', 0),
                scores.get('empathy', 0),
                dopamine,
                source,
                multiplier
            ])
        
        # Log personality evolution periodically
        if self.interaction_count % 50 == 0:
            self.log_personality_state(hour, persona)
    
    def log_personality_state(self, hour: int, persona: Dict):
        """Log current personality state"""
        toxicity_change = persona['toxicity_baseline'] - persona['current_toxicity']
        empathy_change = persona['current_empathy'] - persona['empathy_baseline']
        
        with open("output/personality_evolution.csv", 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                hour,
                persona['id'],
                persona['name'],
                persona['toxicity_baseline'],
                persona['current_toxicity'],
                toxicity_change,
                persona['empathy_baseline'],
                persona['current_empathy'],
                empathy_change,
                persona['total_dopamine'],
                persona['kindness_streak'],
                persona['toxicity_streak'],
                self.bridges_built,
                persona['openness_to_change']
            ])
    
    def calculate_hourly_metrics(self, hour: int, personas: List[Dict]):
        """Calculate and log hourly metrics"""
        # Calculate averages
        avg_toxicity = sum(p['current_toxicity'] for p in personas) / len(personas)
        avg_kindness = 10 - avg_toxicity  # Inverse approximation
        avg_empathy = sum(p['current_empathy'] for p in personas) / len(personas)
        
        # Count improvements
        improved = sum(1 for p in personas 
                      if p['current_toxicity'] < p['toxicity_baseline'] * 0.7)
        worsened = sum(1 for p in personas 
                      if p['current_toxicity'] > p['toxicity_baseline'] * 1.1)
        unchanged = len(personas) - improved - worsened
        
        # Calculate totals
        total_dopamine = sum(p['total_dopamine'] for p in personas)
        
        # Calculate percentages
        kind_interactions = 0
        toxic_interactions = 0
        # (Would need to track this from interactions, using estimates for now)
        
        # Write to CSV
        with open("output/experiment_metrics.csv", 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                hour,
                datetime.now().isoformat(),
                avg_toxicity,
                avg_kindness,
                avg_empathy,
                self.bridges_built,
                improved,
                worsened,
                unchanged,
                total_dopamine,
                0,  # Placeholder for kindness percentage
                0   # Placeholder for toxicity percentage
            ])
    
    def log_experiment_start(self, personas: List[Dict]):
        """Log initial state of all personas"""
        for persona in personas:
            self.log_personality_state(0, persona)
    
    def calculate_final_results(self, personas: List[Dict]) -> Dict:
        """Calculate final experiment results"""
        results = {
            'total_interactions': self.interaction_count,
            'total_bridges': self.bridges_built,
            'duration_hours': (datetime.now() - self.start_time).total_seconds() / 3600
        }
        
        # Calculate changes for each persona
        changes = []
        for p in personas:
            toxicity_reduction = (p['toxicity_baseline'] - p['current_toxicity']) / p['toxicity_baseline']
            empathy_increase = (p['current_empathy'] - p['empathy_baseline']) / (p['empathy_baseline'] or 1)
            total_change = toxicity_reduction + empathy_increase
            changes.append({
                'name': p['name'],
                'toxicity_reduction': toxicity_reduction,
                'empathy_increase': empathy_increase,
                'total_change': total_change
            })
        
        # Calculate averages
        results['avg_toxicity_reduction'] = sum(c['toxicity_reduction'] for c in changes) / len(changes)
        results['avg_empathy_increase'] = sum(c['empathy_increase'] for c in changes) / len(changes)
        results['avg_kindness_increase'] = results['avg_empathy_increase']  # Approximation
        
        # Count improvements
        results['personas_improved'] = sum(1 for c in changes if c['toxicity_reduction'] > 0.3)
        
        # Top transformations
        changes.sort(key=lambda x: x['total_change'], reverse=True)
        results['top_transformations'] = [(c['name'], c['total_change']) for c in changes[:5]]
        
        return results
    
    def save_final_snapshot(self, personas: List[Dict]):
        """Save final state of experiment"""
        snapshot = {
            'timestamp': datetime.now().isoformat(),
            'duration_hours': (datetime.now() - self.start_time).total_seconds() / 3600,
            'total_interactions': self.interaction_count,
            'bridges_built': self.bridges_built,
            'personas': personas
        }
        
        # Archive with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"output/archives/final_snapshot_{timestamp}.json", 'w') as f:
            json.dump(snapshot, f, indent=2)
        
        print(f"Saved final snapshot: final_snapshot_{timestamp}.json")