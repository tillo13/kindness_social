"""
Topic Scraper — Finds fresh trending topics from the web and adds them to the experiment.
Sources: DuckDuckGo news (free, no API key) + Reddit (via reddit_scraper) + Grok (via Cloud Run)
to generate discussion prompts from real headlines and hot posts.
"""

import hashlib
import logging
import random
import time

logger = logging.getLogger(__name__)

# DDG search queries — diverse topic sources
# Balanced mix: ~40% controversial, ~30% good news, ~20% everyday, ~10% bridge building
SEARCH_QUERIES = [
    # Controversial (4)
    "controversial debate trending today",
    "viral social media argument this week",
    "opinion people disagree about 2026",
    "technology debate AI ethics 2026",
    # Good news (3)
    "feel good news stories today",
    "heartwarming news viral this week",
    "community kindness stories 2026",
    # Everyday (2)
    "everyday debate opinions people have",
    "workplace culture debate trending",
    # Bridge building (1)
    "people coming together across differences news",
]

# Reddit searches — subreddits and queries that produce good discussion topics
REDDIT_SEARCHES = [
    # Controversial / debate
    {'query': 'unpopular opinion', 'subreddit': 'unpopularopinion', 'sort': 'hot', 'time_filter': 'week'},
    {'query': '', 'subreddit': 'changemyview', 'sort': 'hot', 'time_filter': 'week'},
    {'query': 'debate', 'subreddit': 'TooAfraidToAsk', 'sort': 'hot', 'time_filter': 'week'},
    {'query': '', 'subreddit': 'AmItheAsshole', 'sort': 'hot', 'time_filter': 'week'},
    # Good news / positive
    {'query': '', 'subreddit': 'UpliftingNews', 'sort': 'hot', 'time_filter': 'week'},
    {'query': '', 'subreddit': 'MadeMeSmile', 'sort': 'hot', 'time_filter': 'week'},
    # Everyday
    {'query': '', 'subreddit': 'NoStupidQuestions', 'sort': 'hot', 'time_filter': 'week'},
    {'query': 'opinion', 'subreddit': 'AskReddit', 'sort': 'hot', 'time_filter': 'week'},
]


def fetch_headlines(max_results=10):
    """Fetch trending headlines from DuckDuckGo news search."""
    try:
        from ddgs import DDGS
        query = random.choice(SEARCH_QUERIES)
        logger.info(f"Searching DDG news: {query}")
        with DDGS() as ddg:
            results = list(ddg.news(query, max_results=max_results))
        return [{'title': r.get('title', ''), 'body': r.get('body', ''),
                 'source': r.get('source', ''), 'url': r.get('url', '')}
                for r in results if r.get('title')]
    except Exception as e:
        logger.warning(f"DDG search failed: {e}")
        return []


def fetch_reddit_posts(max_results=5):
    """Fetch trending Reddit posts via old.reddit.com JSON API. No dependencies beyond requests."""
    import requests as http_req

    search = random.choice(REDDIT_SEARCHES)
    subreddit = search.get('subreddit', '')
    query = search['query'] or subreddit
    logger.info(f"Searching Reddit: r/{subreddit} q={query}")

    try:
        if subreddit:
            url = f"https://old.reddit.com/r/{subreddit}/search.json"
            params = {'q': query, 'sort': search.get('sort', 'hot'),
                      't': search.get('time_filter', 'week'), 'limit': max_results,
                      'restrict_sr': 'on', 'type': 'link'}
        else:
            url = "https://old.reddit.com/search.json"
            params = {'q': query, 'sort': search.get('sort', 'hot'),
                      't': search.get('time_filter', 'week'), 'limit': max_results,
                      'type': 'link'}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
        }
        resp = http_req.get(url, params=params, headers=headers, timeout=15)
        if not resp.ok:
            logger.warning(f"Reddit search HTTP {resp.status_code}")
            return []

        data = resp.json()
        posts = []
        for child in data.get('data', {}).get('children', []):
            d = child['data']
            if d.get('score', 0) < 10 or d.get('num_comments', 0) < 5:
                continue
            if d.get('over_18', False):
                continue
            permalink = d.get('permalink', '')
            posts.append({
                'title': d.get('title', ''),
                'body': (d.get('selftext', '') or '')[:200],
                'source': f"r/{d.get('subreddit', '')}",
                'url': f"https://reddit.com{permalink}" if permalink else '',
            })
        logger.info(f"Reddit: found {len(posts)} posts from r/{subreddit}")
        return posts
    except Exception as e:
        logger.warning(f"Reddit search failed: {e}")
        return []


def headline_to_topic(headline, worker_url):
    """Use Grok to convert a news headline into a discussion topic for agents."""
    import requests as http_req
    prompt = (
        f'Turn this news headline into a short social media discussion topic '
        f'(1-2 sentences, written as a post someone would share). '
        f'Make it feel like a real person sharing a hot take or asking a question. '
        f'Don\'t be formal. Be conversational.\n\n'
        f'Headline: {headline["title"]}\n'
        f'{headline["body"][:200] if headline.get("body") else ""}\n\n'
        f'Also pick ONE category that fits best:\n'
        f'- controversial (people strongly disagree)\n'
        f'- everyday (normal life debate)\n'
        f'- good_news (positive story)\n'
        f'- bridge_building (topic that could bring people together)\n\n'
        f'Reply in this EXACT format:\n'
        f'TOPIC: your discussion topic here\n'
        f'CATEGORY: one category'
    )
    try:
        messages = [{'role': 'user', 'content': prompt}]
        resp = http_req.post(
            f'{worker_url}/chat',
            json={'backend': 'grok', 'messages': messages},
            timeout=30,
        )
        if not resp.ok:
            payload = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            if payload.get('grok_broken'):
                logger.warning('[GROK_BROKEN] grok worker failed in topic_scraper, falling back to kumori LLM')
                from utilities.kumori_api_client import llm_chat as _kumori_chat
                text, _backend = _kumori_chat('groq-llama-70b', messages)
                text = text or ''
            else:
                return None
        else:
            text = resp.json().get('text', '')
        # Parse response
        topic_text = ''
        category = 'everyday'
        for line in text.strip().split('\n'):
            line = line.strip()
            if line.upper().startswith('TOPIC:'):
                topic_text = line[6:].strip().strip('"')
            elif line.upper().startswith('CATEGORY:'):
                cat = line[9:].strip().lower()
                if cat in ('controversial', 'everyday', 'good_news', 'bridge_building'):
                    category = cat
        if topic_text and len(topic_text) > 15:
            return {'text': topic_text, 'category': category, 'source_headline': headline['title']}
    except Exception as e:
        logger.warning(f"Grok topic conversion failed: {e}")
    return None


def scrape_and_add_topics(worker_url, max_new=5):
    """Main function: scrape from DDG + Reddit, convert to topics, add to DB."""
    from core.db_ops import log_cron_start, log_cron_end
    from utilities.postgres_utils import db_cursor

    log_id = log_cron_start('scrape-topics')
    start = time.time()

    try:
        # Alternate sources: ~60% DDG news, ~40% Reddit posts
        headlines = []
        source_used = 'ddg'
        if random.random() < 0.4:
            headlines = fetch_reddit_posts(max_results=10)
            source_used = 'reddit'
        if not headlines:
            headlines = fetch_headlines(max_results=10)
            source_used = 'ddg' if source_used == 'ddg' else 'ddg_fallback'
        if not headlines:
            # Try the other source as last resort
            headlines = fetch_reddit_posts(max_results=10) if source_used == 'ddg' else []
            if headlines:
                source_used = 'reddit_fallback'

        if not headlines:
            ms = int((time.time() - start) * 1000)
            log_cron_end(log_id, 'skipped', ms, 'No headlines found from DDG or Reddit')
            return {'added': 0, 'reason': 'no headlines'}

        added = 0
        skipped = 0

        for headline in headlines[:max_new * 2]:  # Try more than we need
            if added >= max_new:
                break

            topic = headline_to_topic(headline, worker_url)
            if not topic:
                skipped += 1
                continue

            topic_id = f"scraped_{hashlib.md5(topic['text'].encode()).hexdigest()[:8]}"

            with db_cursor(dict_cursor=True) as cur:
                # Check for duplicates
                cur.execute("SELECT id FROM kindness_topics WHERE topic_id = %s", (topic_id,))
                if cur.fetchone():
                    skipped += 1
                    continue

                cur.execute("""
                    INSERT INTO kindness_topics
                        (topic_id, post_text, topic_type, controversy_level, submitted_by,
                         is_approved, source_url, source_headline)
                    VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s)
                """, (topic_id, topic['text'], topic['category'], 5, 'scraper',
                      headline.get('url', ''), headline.get('title', '')))

            added += 1
            logger.info(f"Added topic: [{topic['category']}] {topic['text'][:60]}...")

        ms = int((time.time() - start) * 1000)
        log_cron_end(log_id, 'ok', ms, f'Added {added} topics, skipped {skipped} (source: {source_used})',
                     {'added': added, 'skipped': skipped, 'headlines_found': len(headlines), 'source': source_used})
        return {'added': added, 'skipped': skipped}

    except Exception as e:
        ms = int((time.time() - start) * 1000)
        log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Topic scraper failed")
        return {'error': str(e)[:200]}
