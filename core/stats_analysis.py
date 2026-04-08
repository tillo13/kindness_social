"""
Statistical Analysis — Computes effect sizes, confidence intervals, and p-values
for the treatment vs control experiment.
Uses only standard library math (no scipy/numpy needed on App Engine).
"""

import math


def mean(values):
    if not values:
        return 0
    return sum(values) / len(values)


def stdev(values):
    if len(values) < 2:
        return 0
    m = mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def cohens_d(group1, group2):
    """Cohen's d effect size: (mean1 - mean2) / pooled_sd"""
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return 0
    m1, m2 = mean(group1), mean(group2)
    s1, s2 = stdev(group1), stdev(group2)
    pooled = math.sqrt(((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2))
    if pooled == 0:
        return 0
    return (m1 - m2) / pooled


def welch_t_test(group1, group2):
    """Welch's t-test (unequal variances). Returns (t_stat, p_value, df)."""
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return 0, 1.0, 0
    m1, m2 = mean(group1), mean(group2)
    v1, v2 = stdev(group1)**2, stdev(group2)**2

    se = math.sqrt(v1/n1 + v2/n2)
    if se == 0:
        return 0, 1.0, 0

    t_stat = (m1 - m2) / se

    # Welch-Satterthwaite degrees of freedom
    num = (v1/n1 + v2/n2)**2
    denom = (v1/n1)**2 / (n1-1) + (v2/n2)**2 / (n2-1)
    df = num / denom if denom > 0 else 1

    # Approximate p-value using t-distribution CDF
    p = _t_cdf_approx(abs(t_stat), df) * 2  # two-tailed
    return t_stat, min(p, 1.0), df


def _t_cdf_approx(t, df):
    """Approximate upper-tail p-value for t-distribution.
    Uses the approximation: p ≈ 1 - Φ(t * (1 - 1/(4*df)))
    which is decent for df > 5."""
    if df <= 0:
        return 0.5
    # Better approximation for various df
    z = t * (1 - 1 / (4 * df))
    # Standard normal CDF via error function approximation
    return 0.5 * math.erfc(z / math.sqrt(2))


def confidence_interval_95(values):
    """95% confidence interval for the mean."""
    n = len(values)
    if n < 2:
        return mean(values), mean(values)
    m = mean(values)
    se = stdev(values) / math.sqrt(n)
    margin = 1.96 * se  # z for 95%
    return m - margin, m + margin


def effect_size_label(d):
    """Interpret Cohen's d."""
    d = abs(d)
    if d < 0.2:
        return "negligible"
    elif d < 0.5:
        return "small"
    elif d < 0.8:
        return "medium"
    else:
        return "large"


def p_value_label(p):
    if p < 0.001:
        return "p < 0.001"
    elif p < 0.01:
        return "p < 0.01"
    elif p < 0.05:
        return "p < 0.05"
    else:
        return f"p = {p:.3f}"


def significance_stars(p):
    if p < 0.001:
        return "***"
    elif p < 0.01:
        return "**"
    elif p < 0.05:
        return "*"
    return "ns"


def analyze_experiment(raw_data):
    """Run full statistical analysis on treatment vs control data.
    Returns a dict with all computed statistics."""
    treatment = [r for r in raw_data if not r.get('is_control')]
    control = [r for r in raw_data if r.get('is_control')]

    if len(treatment) < 2 or len(control) < 2:
        return None

    # Order matters — Kindness Score is the headline metric (matches the
    # /home result stat). Everything else is supporting evidence below it.
    metrics = {}
    for metric_name, key, higher_is_better in [
        ('Kindness Score', 'avg_kindness_score', True),
        ('Toxicity Score', 'avg_toxicity_score', False),
        ('Toxicity Reduction', 'tox_change', True),
        ('Empathy Growth', 'emp_change', True),
        ('Dopamine Earned', 'total_dopamine', True),
    ]:
        t_vals = [float(r[key]) for r in treatment]
        c_vals = [float(r[key]) for r in control]

        t_stat, p_val, df = welch_t_test(t_vals, c_vals)
        d = cohens_d(t_vals, c_vals)
        t_ci = confidence_interval_95(t_vals)
        c_ci = confidence_interval_95(c_vals)

        metrics[key] = {
            'name': metric_name,
            'treatment_mean': mean(t_vals),
            'control_mean': mean(c_vals),
            'treatment_sd': stdev(t_vals),
            'control_sd': stdev(c_vals),
            'treatment_ci': t_ci,
            'control_ci': c_ci,
            'difference': mean(t_vals) - mean(c_vals),
            'cohens_d': d,
            'effect_label': effect_size_label(d),
            't_stat': t_stat,
            'p_value': p_val,
            'p_label': p_value_label(p_val),
            'stars': significance_stars(p_val),
            'df': df,
            'significant': p_val < 0.05,
            'higher_is_better': higher_is_better,
        }

    return {
        'treatment_n': len(treatment),
        'control_n': len(control),
        'total_comments': sum(int(r.get('comment_count', 0)) for r in raw_data),
        'metrics': metrics,
    }
