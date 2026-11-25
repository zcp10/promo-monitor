"""Persistent rate gate. No numeric Google quota is assumed."""
import re

def blocked(status, url, text):
    return status in (403, 429) or '/sorry' in url.lower() or bool(re.search(
        r'tr[aá]fego incomum|unusual traffic|not a robot|n[aã]o sou um rob[oô]|verify you are human|automated queries|access denied', text, re.I))

def gate(state, now, interval=60, hourly=5):
    if state.get('next_at', 0) > now:
        return {'status': 'blocked_cooldown' if state.get('failures', 0) else 'interval', 'next_at': state['next_at']}
    attempts = [t for t in state.get('attempts', []) if now-t < 3600]
    if len(attempts) >= hourly:
        return {'status': 'hourly_limit', 'next_at': attempts[0]+3600}
    return None

def finish(state, now, failed=False, retry_after=0, interval=60):
    failures = state.get('failures', 0)+1 if failed else 0
    delay = (30, 120, 300)[failures-1] if 0 < failures <= 3 else (1800 if failures else 0)
    state.update(failures=failures, next_at=now+max(interval, delay, retry_after))
