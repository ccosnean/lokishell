"""ADC is distinct from the account selected by gcloud auth login."""


def who(ctx):
    data = ctx.read_json('gcloud/application_default_credentials.json')
    if data is None:
        return {'status': 'signed_out'}
    kind = data.get('type')
    if kind == 'service_account' and data.get('private_key') and data.get('client_email'):
        return {'status': 'configured', 'account': data['client_email'], 'detail': 'Service account'}
    if kind == 'authorized_user' and data.get('refresh_token'):
        return {'status': 'configured', 'account': data.get('account') or None,
                'detail': 'User ADC; identity may not be stored'}
    if kind in ('external_account', 'external_account_authorized_user', 'impersonated_service_account'):
        return {'status': 'configured', 'detail': 'Federated or impersonated ADC; account not reported'}
    return {'status': 'unknown', 'detail': 'Unrecognized ADC credentials'}
