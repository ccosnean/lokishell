"""Codex login mode and identity claims from the persona's saved ID token."""
import base64
import json


def who(ctx):
    result = ctx.capture()
    text = result.stdout + '\n' + result.stderr
    if result.returncode:
        return {'status': 'signed_out' if 'Not logged in' in text else 'error'}
    if 'Logged in using an API key' in text:
        # Some CLI versions include a masked key in this line. Never echo it.
        return {'status': 'signed_in', 'detail': 'API key; account not reported'}
    if 'Logged in using ChatGPT' not in text:
        return {'status': 'unknown', 'detail': 'CLI did not report an account'}
    account = None
    try:
        auth = ctx.read_json('codex/auth.json') or {}
        payload = auth.get('tokens', {}).get('id_token', '').split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        account = claims.get('email')
    except (ValueError, IndexError, AttributeError):
        pass
    return {'status': 'signed_in', 'account': account,
            'detail': 'ChatGPT; saved identity' if account else 'ChatGPT; account not reported'}
