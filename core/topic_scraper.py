"""
Topic Scraper — Finds fresh trending topics from the web and adds them to the experiment.
Uses DuckDuckGo news search (free, no API key) + Grok (free, via Cloud Run) to generate
discussion prompts from real headlines.
"""

import json
import hashlib
import logging
import time

logger = logging.getLogger(__name__)

# Search queries to rotate through — diverse topic sources
SEARCH_QUERIES = [
    "controversial debate trending today",
    "viral social media argument this week",
    "opinion people disagree about 2026",
    "feel good news stories today",
    "community kindness stories this week",
    "technology debate AI ethics 2026",
    "parenting debate schools kids 2026",
    "workplace culture debate remote work",
    "health wellness controversial opinion",
    "environment climate debate 2026",
]


def fetch_headlines(max_results=10):
    """Fetch trending headlines from DuckDuckGo news search."""
    import random
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
        resp = http_req.post(
            f'{worker_url}/chat',
            json={'backend': 'grok', 'messages': [{'role': 'user', 'content': prompt}]},
            timeout=30,
        )
        if not resp.ok:
            return None
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
    """Main function: scrape headlines, convert to topics, add to DB."""
    from core.db_ops import log_cron_start, log_cron_end
    from utilities.postgres_utils import db_cursor

    log_id = log_cron_start('scrape-topics')
    start = time.time()

    try:
        headlines = fetch_headlines(max_results=10)
        if not headlines:
            ms = int((time.time() - start) * 1000)
            log_cron_end(log_id, 'skipped', ms, 'No headlines found')
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
                        (topic_id, post_text, topic_type, controversy_level, submitted_by, is_approved)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                """, (topic_id, topic['text'], topic['category'], 5, 'scraper'))

            added += 1
            logger.info(f"Added topic: [{topic['category']}] {topic['text'][:60]}...")

        ms = int((time.time() - start) * 1000)
        log_cron_end(log_id, 'ok', ms, f'Added {added} topics, skipped {skipped}',
                     {'added': added, 'skipped': skipped, 'headlines_found': len(headlines)})
        return {'added': added, 'skipped': skipped}

    except Exception as e:
        ms = int((time.time() - start) * 1000)
        log_cron_end(log_id, 'error', ms, error_text=str(e)[:500])
        logger.exception("Topic scraper failed")
        return {'error': str(e)[:200]}
