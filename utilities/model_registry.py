"""
Model Registry - Exact model IDs for every backend.
Used for display on agent profiles and dashboards.
Derived from backend_registry — adding a new backend there auto-updates this.
"""

from utilities.backend_registry import (
    build_model_registry,
    build_free_backends_set,
    KINDNESS_ONLY_MODELS,
)

MODELS = build_model_registry()

# Backends that cost $0 — free tiers or self-hosted
FREE_BACKENDS = build_free_backends_set() | {'local'}

# Backends with actual per-token cost
PAID_BACKENDS = {'haiku', 'sonnet', 'opus', 'gpt4o_mini', 'gpt4o'}


def is_free(backend):
    """Check if a backend is free tier."""
    return backend in FREE_BACKENDS


def get_model_info(backend):
    """Get full model info for a backend name."""
    return MODELS.get(backend, {
        'model_id': backend,
        'provider': 'Unknown',
        'display': backend,
    })


def get_model_id(backend):
    """Get the exact model ID string."""
    return MODELS.get(backend, {}).get('model_id', backend)


def get_display_name(backend):
    """Get human-readable model name."""
    return MODELS.get(backend, {}).get('display', backend)
