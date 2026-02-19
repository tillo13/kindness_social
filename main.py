#!/usr/bin/env python3
"""
Kindness Experiment Main Runner - Enhanced Verbosity Version
Tests if rewarding kindness changes toxic behavior over time
"""

import yaml
import json
import time
import signal
import sys
from pathlib import Path
from datetime import datetime
from colorama import init, Fore, Style

from core.simulator import KindnessSimulator
from core.evaluator import Evaluator
from core.tracker import MetricsTracker
from lmstudio_utils import LMStudioUtils, ServerConfig

# Initialize colorama for colored output
init(autoreset=True)

class ExperimentRunner:
    def __init__(self):
        """Initialize the experiment"""
        self.running = True
        self.start_time = datetime.now()
        
        # Load configuration
        with open("config.yaml", 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Force verbose mode for detailed output
        self.config['experiment']['verbose'] = True
        self.config['lm_studio']['track_performance'] = True
        
        # Load personas
        with open("personas.json", 'r') as f:
            self.personas = json.load(f)
        
        # Load topics
        with open("topics.json", 'r') as f:
            self.topics = json.load(f)
        
        # Initialize components
        self.setup_components()
        
        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self.shutdown)
    
    def setup_components(self):
        """Initialize all components"""
        # LM Studio connection
        server_config = ServerConfig(
            host=self.config['lm_studio']['host'],
            port=self.config['lm_studio']['port'],
            timeout=self.config['lm_studio']['timeout']
        )
        
        # Initialize components with verbose mode
        self.evaluator = Evaluator(server_config, self.config, verbose=True)
        self.tracker = MetricsTracker(self.config)
        self.simulator = KindnessSimulator(
            self.config, 
            self.personas, 
            self.topics, 
            self.evaluator, 
            self.tracker,
            verbose=True
        )
        
        # Verify LM Studio connection
        print(f"{Fore.YELLOW}Testing LM Studio connection...{Style.RESET_ALL}")
        if not self.evaluator.test_connection():
            print(f"{Fore.RED}ERROR: Cannot connect to LM Studio. Please ensure it's running.{Style.RESET_ALL}")
            sys.exit(1)
        
        print(f"{Fore.GREEN}✓ Connected to LM Studio at {server_config.host}:{server_config.port}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Model: {self.evaluator.model_id}{Style.RESET_ALL}")
    
    def run(self):
        """Run the experiment"""
        print("\n" + "="*80)
        print(f"{Fore.MAGENTA}KINDNESS EXPERIMENT STARTING{Style.RESET_ALL}")
        print("="*80)
        print(f"Duration: {self.config['experiment']['hours']} hours")
        print(f"Personas: {len(self.personas)}")
        print(f"Topics: {len(self.topics['controversial'])} controversial, {len(self.topics.get('bridge_building', []))} bridge-building")
        print(f"Hypothesis: Rewarding kindness will reduce toxicity")
        print(f"Threads per hour: {self.config['experiment']['threads_per_hour']}")
        print(f"Participants per thread: {self.config['experiment']['participants_per_thread']}")
        print("="*80 + "\n")
        
        # Show initial persona states
        self.show_initial_states()
        
        # Initial metrics
        self.tracker.log_experiment_start(self.personas)
        
        try:
            # Run simulation
            for hour in range(1, self.config['experiment']['hours'] + 1):
                if not self.running:
                    break
                
                print(f"\n{'='*80}")
                print(f"{Fore.YELLOW}HOUR {hour}/{self.config['experiment']['hours']}{Style.RESET_ALL}")
                print(f"{'='*80}")
                
                hour_start = time.time()
                
                # Run threads for this hour
                threads_created = self.simulator.run_hour(hour)
                
                hour_duration = time.time() - hour_start
                
                # Calculate and log hourly metrics
                self.tracker.calculate_hourly_metrics(hour, self.personas)
                
                # Show detailed progress
                self.show_detailed_progress(hour, hour_duration, threads_created)
                
                # Small delay between hours
                if hour < self.config['experiment']['hours']:
                    time.sleep(1)
        
        except KeyboardInterrupt:
            self.shutdown()
        
        # Final analysis
        self.show_final_results()
    
    def show_initial_states(self):
        """Display initial persona states"""
        print(f"\n{Fore.CYAN}INITIAL PERSONA STATES:{Style.RESET_ALL}")
        print("-" * 60)
        
        # Group by toxicity level
        angry = [p for p in self.personas if p['toxicity_baseline'] >= 7]
        moderate = [p for p in self.personas if 4 <= p['toxicity_baseline'] < 7]
        kind = [p for p in self.personas if p['toxicity_baseline'] < 4]
        
        print(f"{Fore.RED}Angry Personas (Toxicity 7-9):{Style.RESET_ALL}")
        for p in angry:
            print(f"  • {p['name']}: Tox={p['toxicity_baseline']}, Emp={p['empathy_baseline']}, Pol={p['political_lean']:+.2f}")
        
        print(f"\n{Fore.YELLOW}Moderate Personas (Toxicity 4-6):{Style.RESET_ALL}")
        for p in moderate:
            print(f"  • {p['name']}: Tox={p['toxicity_baseline']}, Emp={p['empathy_baseline']}, Pol={p['political_lean']:+.2f}")
        
        print(f"\n{Fore.GREEN}Kind Personas (Toxicity 1-3):{Style.RESET_ALL}")
        for p in kind:
            print(f"  • {p['name']}: Tox={p['toxicity_baseline']}, Emp={p['empathy_baseline']}, Pol={p['political_lean']:+.2f}")
    
    def show_detailed_progress(self, hour, hour_duration, threads_created):
        """Display detailed progress with statistics"""
        # Calculate key metrics
        avg_toxicity = sum(p['current_toxicity'] for p in self.personas) / len(self.personas)
        avg_empathy = sum(p['current_empathy'] for p in self.personas) / len(self.personas)
        
        baseline_avg_tox = sum(p['toxicity_baseline'] for p in self.personas) / len(self.personas)
        baseline_avg_emp = sum(p['empathy_baseline'] for p in self.personas) / len(self.personas)
        
        improved = sum(1 for p in self.personas 
                      if p['current_toxicity'] < p['toxicity_baseline'] * 0.7)
        worsened = sum(1 for p in self.personas
                      if p['current_toxicity'] > p['toxicity_baseline'] * 1.1)
        
        total_dopamine = sum(p['total_dopamine'] for p in self.personas)
        
        print(f"\n{Fore.CYAN}HOUR {hour} DETAILED SUMMARY:{Style.RESET_ALL}")
        print("-" * 60)
        
        # Processing stats
        print(f"{Fore.YELLOW}Processing Stats:{Style.RESET_ALL}")
        print(f"  • Hour duration: {hour_duration:.2f} seconds")
        print(f"  • Threads created: {threads_created}")
        print(f"  • Total interactions: {self.tracker.interaction_count}")
        print(f"  • Bridges built this session: {self.tracker.bridges_built}")
        
        # Metrics changes
        print(f"\n{Fore.YELLOW}Metrics (Current vs Baseline):{Style.RESET_ALL}")
        print(f"  • Avg Toxicity: {avg_toxicity:.2f} (was {baseline_avg_tox:.2f}) "
              f"{Fore.GREEN if avg_toxicity < baseline_avg_tox else Fore.RED}"
              f"[{avg_toxicity - baseline_avg_tox:+.2f}]{Style.RESET_ALL}")
        print(f"  • Avg Empathy: {avg_empathy:.2f} (was {baseline_avg_emp:.2f}) "
              f"{Fore.GREEN if avg_empathy > baseline_avg_emp else Fore.RED}"
              f"[{avg_empathy - baseline_avg_emp:+.2f}]{Style.RESET_ALL}")
        
        # Persona changes
        print(f"\n{Fore.YELLOW}Persona Status:{Style.RESET_ALL}")
        print(f"  • Improved (>30% reduction): {improved}/{len(self.personas)}")
        print(f"  • Worsened (>10% increase): {worsened}/{len(self.personas)}")
        print(f"  • Unchanged: {len(self.personas) - improved - worsened}/{len(self.personas)}")
        print(f"  • Total dopamine distributed: {total_dopamine}")
        
        # Show biggest changes
        changes = []
        for p in self.personas:
            toxicity_change = p['toxicity_baseline'] - p['current_toxicity']
            empathy_change = p['current_empathy'] - p['empathy_baseline']
            total_change = toxicity_change + empathy_change
            changes.append((p['name'], total_change, toxicity_change, empathy_change, p['total_dopamine']))
        
        changes.sort(key=lambda x: x[1], reverse=True)
        
        print(f"\n{Fore.YELLOW}Top Transformations:{Style.RESET_ALL}")
        for name, total, tox_change, emp_change, dopamine in changes[:5]:
            color = Fore.GREEN if total > 0 else Fore.RED
            print(f"  • {color}{name}:{Style.RESET_ALL} "
                  f"Tox {tox_change:+.1f}, Emp {emp_change:+.1f}, "
                  f"Dopamine: {dopamine}")
        
        # Show worst performers
        if changes[-3:][0][1] < 0:  # If there are negative changes
            print(f"\n{Fore.YELLOW}Struggling Personas:{Style.RESET_ALL}")
            for name, total, tox_change, emp_change, dopamine in changes[-3:]:
                if total < 0:
                    print(f"  • {Fore.RED}{name}:{Style.RESET_ALL} "
                          f"Tox {tox_change:+.1f}, Emp {emp_change:+.1f}, "
                          f"Dopamine: {dopamine}")
    
    def show_final_results(self):
        """Display final experiment results"""
        print("\n" + "="*80)
        print(f"{Fore.MAGENTA}EXPERIMENT COMPLETE - FINAL RESULTS{Style.RESET_ALL}")
        print("="*80)
        
        # Calculate success metrics
        results = self.tracker.calculate_final_results(self.personas)
        
        print(f"\n{Fore.CYAN}EXPERIMENT STATISTICS:{Style.RESET_ALL}")
        print(f"  • Duration: {results['duration_hours']:.1f} hours")
        print(f"  • Total Interactions: {results['total_interactions']}")
        print(f"  • Bridges Built: {results['total_bridges']}")
        
        print(f"\n{Fore.YELLOW}🎯 PRIMARY METRIC:{Style.RESET_ALL}")
        reduction = results['avg_toxicity_reduction']
        color = Fore.GREEN if reduction > 0.3 else Fore.YELLOW if reduction > 0.15 else Fore.RED
        print(f"  {color}Average Toxicity Reduction: {reduction:.1%}{Style.RESET_ALL}")
        
        print(f"\n{Fore.YELLOW}📊 SECONDARY METRICS:{Style.RESET_ALL}")
        print(f"  • Personas Improved (>30% reduction): {results['personas_improved']}/{len(self.personas)}")
        print(f"  • Average Kindness Increase: {results['avg_kindness_increase']:.1%}")
        print(f"  • Average Empathy Increase: {results['avg_empathy_increase']:.1%}")
        
        print(f"\n{Fore.YELLOW}🏆 BIGGEST TRANSFORMATIONS:{Style.RESET_ALL}")
        for name, change in results['top_transformations'][:5]:
            print(f"  • {Fore.GREEN}{name}: {change:+.2f} total improvement{Style.RESET_ALL}")
        
        print(f"\n{Fore.MAGENTA}✅ HYPOTHESIS RESULT:{Style.RESET_ALL}")
        if results['avg_toxicity_reduction'] > 0.3:
            print(f"  {Fore.GREEN}SUCCESS - Kindness rewards reduced toxicity by >30%{Style.RESET_ALL}")
        elif results['avg_toxicity_reduction'] > 0.15:
            print(f"  {Fore.YELLOW}PARTIAL SUCCESS - Some toxicity reduction observed{Style.RESET_ALL}")
        else:
            print(f"  {Fore.RED}FAILURE - Insufficient behavior change{Style.RESET_ALL}")
        
        print(f"\n{Fore.CYAN}Detailed results saved to output/{Style.RESET_ALL}")
    
    def shutdown(self, signum=None, frame=None):
        """Graceful shutdown"""
        self.running = False
        print(f"\n\n{Fore.YELLOW}Shutting down experiment...{Style.RESET_ALL}")
        self.tracker.save_final_snapshot(self.personas)
        print(f"{Fore.GREEN}Data saved to output/{Style.RESET_ALL}")
        sys.exit(0)

def main():
    """Entry point"""
    runner = ExperimentRunner()
    runner.run()

if __name__ == "__main__":
    main()