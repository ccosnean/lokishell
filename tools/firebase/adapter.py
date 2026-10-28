"""Identify the Firebase CLI account selected for the current project."""
import re


def who(ctx):
    result = ctx.capture()
    if result.returncode:
        return {'status': 'error'}
    output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', result.stdout)
    # login:list's JSON result contains credentials and lists the global account
    # first, even when a project-specific account is selected. Its text summary
    # names the selected account separately from the other available logins.
    match = re.search(r'(?:^|\n)(?:[✔iℹ] )?Logged in as ([^\s@]+@[^\s@]+)', output)
    if match:
        return {'status': 'signed_in', 'account': match.group(1)}
    if 'No authorized accounts' in output:
        adc = ctx.read_json('gcloud/application_default_credentials.json')
        if adc:
            return {'status': 'configured', 'account': adc.get('client_email') or adc.get('account'),
                    'detail': 'Application Default Credentials'}
        return {'status': 'signed_out'}
    return {'status': 'unknown', 'detail': 'CLI did not report an account'}
