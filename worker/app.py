"""
Kindness Social Worker — Cloud Run service for heavy LLM work.
Handles: thread generation, model testing, avatar generation.
All native deps (curl_cffi, wasmtime, grok_core, deepseek4free) live here.
"""

import json
import logging
import os
import sys
import time

from flask import Flask, jsonify, request

# Add parent dir so we can import utilities/core
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# Add worker dir for grok_core and dsk
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'kindness-worker'})


@app.route('/chat', methods=['POST'])
def chat_proxy():
    """Proxy chat requests for backends that only work on Cloud Run (grok, deepseek)."""
    data = request.get_json(silent=True) or {}
    backend = data.get('backend', 'grok')
    messages = data.get('messages', [])

    if not messages:
        return jsonify({'error': 'No messages provided'}), 400

    # Extract the user message (last message content)
    prompt = messages[-1].get('content', '') if messages else ''

    try:
        if backend in ('grok', 'grok_fast', 'grok4'):
            model_map = {'grok': 'grok-3', 'grok_fast': 'grok-3-fast', 'grok4': 'grok-4'}
            from grok_core.grok import Grok
            g = Grok(model=model_map.get(backend, 'grok-3-fast'))
            result = g.start_convo(prompt)
            text = result.get('response', '')
        elif backend == 'deepseek':
            from dsk.api import DeepSeekAPI
            from utilities.google_secret_utils import get_secret
            token = get_secret('KINDNESS_DEEPSEEK_CHAT_TOKEN')
            api = DeepSeekAPI(token)
            session_id = api.create_chat_session()
            from curl_cffi import requests as crequests
            headers = api._get_headers(
                pow_response=api.pow_solver.solve_challenge(api._get_pow_challenge())
            )
            response = crequests.post(
                f'{api.BASE_URL}/chat/completion',
                headers=headers,
                json={
                    'chat_session_id': session_id,
                    'parent_message_id': None,
                    'prompt': prompt,
                    'ref_file_ids': [],
                    'thinking_enabled': False,
                    'search_enabled': False,
                },
                cookies=api.cookies,
                impersonate='chrome120',
                stream=True,
                timeout=60,
            )
            text = ''
            for line in response.iter_lines():
                if line and b'"o":"APPEND"' in line:
                    try:
                        chunk = json.loads(line.replace(b'data: ', b''))
                        text += chunk.get('v', '')
                    except:
                        pass
            text = text.strip()
        else:
            return jsonify({'error': f'Unknown backend: {backend}'}), 400

        return jsonify({'text': text, 'backend': backend, 'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)[:300], 'backend': backend}), 500


@app.route('/test-all-models', methods=['POST'])
def test_all_models():
    """Test every model from every provider. Returns which work and which don't."""
    results = {}

    # Load all model JSON files
    models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
    for fname in sorted(os.listdir(models_dir)):
        if not fname.endswith('.json'):
            continue
        with open(os.path.join(models_dir, fname)) as f:
            provider_data = json.load(f)

        provider = provider_data.get('provider', fname.replace('.json', ''))
        provider_results = []

        for m in provider_data.get('models', []):
            if not m.get('usable_for_kindness'):
                continue

            mid = m['model_id']
            entry = {'model_id': mid, 'provider': provider}

            try:
                start = time.time()
                text = _test_model(provider, mid)
                elapsed = int((time.time() - start) * 1000)
                entry['status'] = 'working'
                entry['response'] = text[:80]
                entry['time_ms'] = elapsed
                logger.info(f'  OK {provider}/{mid} ({elapsed}ms): {text[:40]}')
            except Exception as e:
                entry['status'] = 'error'
                entry['error'] = str(e)[:100]
                logger.warning(f'  FAIL {provider}/{mid}: {e}')

            provider_results.append(entry)

        results[provider] = provider_results

    # Summary
    total_working = sum(1 for p in results.values() for m in p if m['status'] == 'working')
    total_tested = sum(len(p) for p in results.values())

    return jsonify({
        'total_tested': total_tested,
        'total_working': total_working,
        'results': results,
    })


def _test_model(provider, model_id):
    """Test a single model. Returns response text or raises."""
    msg = [{'role': 'user', 'content': 'Say hello in 5 words'}]

    if provider == 'xAI':
        return _test_grok(model_id)
    elif provider == 'DeepSeek':
        return _test_deepseek(model_id)
    else:
        # Use the standard router for API-key backends
        from utilities.llm_router import chat
        # Map provider to backend name
        backend_map = {
            'Google': 'gemini',
            'Groq': 'groq',
            'Mistral': 'mistral',
            'Cerebras': 'cerebras',
            'OpenAI': 'gpt4o_mini',
            'Anthropic': 'haiku',
            'OpenRouter': 'openrouter',
        }
        backend = backend_map.get(provider, provider.lower())
        text, actual = chat(backend, msg, max_tokens=30)
        return text


def _test_grok(model_id):
    """Test Grok via grok_core ECDSA."""
    from grok_core.grok import Grok
    g = Grok(model=model_id)
    result = g.start_convo('Say hello in 5 words')
    return result.get('response', '')


def _test_deepseek(model_id):
    """Test DeepSeek via deepseek4free PoW."""
    from dsk.api import DeepSeekAPI
    from utilities.google_secret_utils import get_secret
    token = get_secret('KINDNESS_DEEPSEEK_CHAT_TOKEN')
    api = DeepSeekAPI(token)
    session_id = api.create_chat_session()

    # Manual SSE parsing since the library's parser doesn't match the format
    from curl_cffi import requests as crequests
    headers = api._get_headers(
        pow_response=api.pow_solver.solve_challenge(api._get_pow_challenge())
    )
    response = crequests.post(
        f'{api.BASE_URL}/chat/completion',
        headers=headers,
        json={
            'chat_session_id': session_id,
            'parent_message_id': None,
            'prompt': 'Say hello in 5 words',
            'ref_file_ids': [],
            'thinking_enabled': False,
            'search_enabled': False,
        },
        cookies=api.cookies,
        impersonate='chrome120',
        stream=True,
        timeout=30,
    )

    text = ''
    for line in response.iter_lines():
        if line and b'"o":"APPEND"' in line:
            try:
                data = json.loads(line.replace(b'data: ', b''))
                text += data.get('v', '')
            except:
                pass
    return text.strip()


@app.route('/test-quick', methods=['POST'])
def test_quick():
    """Quick test of grok + deepseek — just prove they respond."""
    results = {}

    # Grok
    try:
        start = time.time()
        text = _test_grok('grok-3-fast')
        elapsed = int((time.time() - start) * 1000)
        results['grok'] = {'status': 'ok', 'response': text[:80], 'time_ms': elapsed}
    except Exception as e:
        results['grok'] = {'status': 'error', 'error': str(e)[:150]}

    # DeepSeek
    try:
        start = time.time()
        text = _test_deepseek('deepseek-chat')
        elapsed = int((time.time() - start) * 1000)
        results['deepseek'] = {'status': 'ok', 'response': text[:80], 'time_ms': elapsed}
    except Exception as e:
        results['deepseek'] = {'status': 'error', 'error': str(e)[:150]}

    return jsonify(results)


@app.route('/generate-thread', methods=['POST'])
def generate_thread():
    """Generate a discussion thread. Called by App Engine cron."""
    from core.simulator import run_thread
    from core import db_ops
    db_ops.create_tables()
    result = run_thread()
    return jsonify(result)


@app.route('/birth-agent', methods=['POST'])
def birth_agent():
    """Birth a new agent with avatar."""
    from core.agent_factory import create_agent
    backend = request.json.get('backend') if request.json else None
    agent = create_agent(backend=backend)
    if agent:
        return jsonify({'created': agent['agent_id'], 'backend': agent['llm_backend']})
    return jsonify({'error': 'Failed to create agent'}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
