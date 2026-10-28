"""Read Claude's structured authentication status without exposing credentials."""
import json


def who(ctx):
    result = ctx.capture()
    if result.returncode not in (0, 1):
        return {'status': 'error'}
    data = json.loads(result.stdout)
    if data.get('loggedIn') is False:
        return {'status': 'signed_out'}
    if data.get('loggedIn') is not True or result.returncode:
        return {'status': 'error'}
    modes = {'claude.ai': 'Claude subscription', 'api_key': 'API key',
             'api_key_helper': 'API key helper', 'oauth_token': 'OAuth token',
             'third_party': 'Cloud provider'}
    return {'status': 'signed_in', 'account': data.get('email'),
            'detail': modes.get(data.get('authMethod'), 'Account not reported') if not data.get('email') else None}
