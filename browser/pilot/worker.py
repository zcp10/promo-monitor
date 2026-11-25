"""Single anonymous browser in isolated Docker; only public Google Shopping DOM."""
import glob
import json
import os
from pathlib import Path
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from policy import blocked, gate, finish

STATE = Path('/state/state.json')
state = json.loads(STATE.read_text()) if STATE.exists() else {}
session = None

def save():
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)

def emit(**data):
    print(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **data}, ensure_ascii=False), flush=True)

def retry_after(headers):
    value = next((str(v) for k, v in headers.items() if k.lower() == 'retry-after'), '')
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except Exception:
            return 0

def browser():
    global session
    if session is None:
        from scrapling.fetchers import StealthySession
        import scrapling.engines._browsers._base as base
        bad = ('--disable-cookie-encryption', '--safebrowsing-disable-auto-update',
               '--disable-client-side-phishing-detection', '--disable-ipc-flooding-protection', '--do-not-de-elevate')
        base.STEALTH_ARGS = tuple(x for x in base.STEALTH_ARGS if x not in bad)
        base.DEFAULT_ARGS = tuple(x for x in base.DEFAULT_ARGS if x != '--password-store=basic')
        candidate = StealthySession(
            executable_path=glob.glob('/ms-playwright/chromium-*/chrome-linux*/chrome')[0],
            headless=False, user_data_dir='/state/profile', proxy=os.environ['GOOGLE_PROXY'],
            locale='pt-BR', timezone_id='America/Sao_Paulo', solve_cloudflare=False,
            google_search=False, retries=1, timeout=25000,
            additional_args={'chromium_sandbox': True, 'permissions': [], 'accept_downloads': False,
                'ignore_https_errors': False, 'viewport': {'width': 1280, 'height': 900}, 'device_scale_factor': 1})
        candidate.__enter__()
        session = candidate
    return session

def handle(request):
    if request.get('action') == 'health':
        return {'status': 'ready', 'backend': 'scrapling_docker', 'state': state, 'uid': os.getuid()}
    query = request.get('query', '')
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        return {'status': 'invalid_query', 'quotes': []}
    interval = max(60, float(request.get('interval_seconds', 60)))
    hourly = max(1, min(60, int(request.get('hourly_limit', 10))))
    limited = gate(state, time.time(), interval, hourly)
    if limited:
        return {**limited, 'quotes': []}
    now = time.time()
    state['attempts'] = [t for t in state.get('attempts', []) if now-t < 3600]+[now]
    # Reserve the gate before navigation, also on crashes or interrupted clients.
    state['next_at'] = now+interval
    save()
    captured = {}
    def action(page):
        page.wait_for_timeout(4000)
        text = page.locator('body').inner_text(timeout=8000)
        captured['blocked'] = blocked(None, page.url, text)
        captured['quotes'] = [] if captured['blocked'] else page.evaluate(Path('/pilot/extractor.js').read_text())
        try:
            page.screenshot(path='/state/latest.png', timeout=5000)
        except Exception:
            pass
    try:
        response = browser().fetch('https://www.google.com/search?'+urlencode({'q': query.strip(), 'udm': '28'}), page_action=action)
        failed = captured.get('blocked', False) or blocked(response.status, str(response.url), '')
        status = 'blocked' if failed else ('error' if response.status >= 400 else ('ok' if captured.get('quotes') else 'no_results'))
        finish(state, time.time(), failed or status == 'error', retry_after(response.headers), interval)
        result = {'status': status, 'quotes': captured.get('quotes', [])[:30] if status == 'ok' else [], 'http_status': response.status}
    except Exception as exc:
        finish(state, time.time(), failed=True, interval=interval)
        result = {'status': 'error', 'quotes': [], 'error_type': type(exc).__name__}
    state.update(last_status=result['status'], last_query=query, last_finished=time.time())
    counts = state.setdefault('counts', {})
    counts[result['status']] = counts.get(result['status'], 0)+1
    save()
    emit(event='search', query=query, status=result['status'], quotes=len(result['quotes']), next_at=state['next_at'])
    return {**result, 'next_at': state['next_at']}

with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 8765))
    server.listen(4)
    emit(event='ready', uid=os.getuid())
    while True:
        conn, _ = server.accept()
        with conn:
            conn.settimeout(3)
            try:
                with conn.makefile('rb') as stream:
                    request = json.loads(stream.readline(4096))
                result = handle(request)
                conn.sendall(json.dumps(result, ensure_ascii=False).encode()+b'\n')
            except Exception as exc:
                emit(event='client_error', error_type=type(exc).__name__)
