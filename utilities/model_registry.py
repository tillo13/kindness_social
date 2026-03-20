"""
Model Registry - Exact model IDs for every backend.
Used for display on agent profiles and dashboards.
"""

MODELS = {
    'gemini': {
        'model_id': 'gemini-2.0-flash',
        'provider': 'Google',
        'display': 'Gemini 2.0 Flash',
    },
    'grok': {
        'model_id': 'grok-3',
        'provider': 'xAI',
        'display': 'Grok 3',
    },
    'deepseek': {
        'model_id': 'deepseek-chat',
        'provider': 'DeepSeek',
        'display': 'DeepSeek Chat V3',
    },
    'gpt4o_mini': {
        'model_id': 'gpt-4o-mini',
        'provider': 'OpenAI',
        'display': 'GPT-4o Mini',
    },
    'gpt4o': {
        'model_id': 'gpt-4o',
        'provider': 'OpenAI',
        'display': 'GPT-4o',
    },
    'haiku': {
        'model_id': 'claude-haiku-4-5-20251001',
        'provider': 'Anthropic',
        'display': 'Claude Haiku 4.5',
    },
    'sonnet': {
        'model_id': 'claude-sonnet-4-5-20250929',
        'provider': 'Anthropic',
        'display': 'Claude Sonnet 4.5',
    },
    'opus': {
        'model_id': 'claude-opus-4-5-20251101',
        'provider': 'Anthropic',
        'display': 'Claude Opus 4.5',
    },
    'local': {
        'model_id': 'lmstudio/auto',
        'provider': 'LM Studio (local)',
        'display': 'Local LLM (LM Studio)',
    },
}


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
