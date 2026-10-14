"""Invalid OAuth input must fail without database or network work."""
import pytest
from services import review_oauth as oauth


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv('SOURCE_REVIEW_OAUTH_BASE_URL','https://reader.example')
    monkeypatch.setattr(oauth.manage_auth,'connection',lambda:pytest.fail('invalid input accessed DB'))


def test_metadata():
    assert oauth.oauth_metadata()['issuer']=='https://reader.example'
    assert oauth.oauth_metadata()['code_challenge_methods_supported']==['S256']
    assert oauth.oauth_metadata()['token_endpoint_auth_methods_supported']==['none']
    assert oauth.resource_metadata()['resource']=='https://reader.example/api/admin/mcp/'
    assert oauth.resource_metadata()['scopes_supported']==['source:review','offline_access']


@pytest.mark.parametrize('value',['','http://reader.example','https://reader.example/api',
 'https://a:b@reader.example','https://reader.example?x=1','https://reader.example#x',
 'https://reader.example:444','https://['])
def test_invalid_configuration(monkeypatch,value):
    monkeypatch.setenv('SOURCE_REVIEW_OAUTH_BASE_URL',value)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.oauth_metadata()
    assert exc.value.status_code==503 and 'reader.example' not in exc.value.description


@pytest.mark.parametrize('uri',['https://chatgpt.com/connector_platform_oauth_redirect',
 'https://chatgpt.com/connector/oauth/abc_123-X'])
def test_official_callback(uri):
    assert oauth._redirect(uri)==uri


@pytest.mark.parametrize('uri',['https://chatgpt.com.evil.example/connector_platform_oauth_redirect',
 'https://chatgpt.com:443/connector_platform_oauth_redirect',
 'https://user@chatgpt.com/connector_platform_oauth_redirect','http://chatgpt.com/connector_platform_oauth_redirect',
 'https://chatgpt.com/connector_platform_oauth_redirect?x=1','https://chatgpt.com/connector/oauth/a#code=1',
 'https://chatgpt.com/connector/oauth/','https://chatgpt.com/connector/oauth/../a',
 'https://chatgpt.com/connector/oauth/a/b','https://chatgpt.com/connector/oauth/a%2Fb','https://['])
def test_bad_callback(uri):
    with pytest.raises(oauth.OAuthError):
        oauth.register_client({'redirect_uris':[uri]})


@pytest.mark.parametrize('extra',[{'token_endpoint_auth_method':'client_secret_basic'},
 {'client_secret':'secret'},{'grant_types':['client_credentials']},{'response_types':['token']},
 {'redirect_uris':'not-list'},{'scope':'admin:all'},{'scope':'source:review source:review'},
 {'client_name':'x'*101},{'logo_uri':'x'*2049},{'contacts':['x'*255]}])
def test_other_grants_and_unbounded_metadata(extra):
    with pytest.raises(oauth.OAuthError):
        oauth.register_client({'redirect_uris':['https://chatgpt.com/connector_platform_oauth_redirect'],**extra})


def authorize(**changes):
    return {'client_id':'registered','redirect_uri':'https://chatgpt.com/connector_platform_oauth_redirect',
     'response_type':'code','scope':'source:review offline_access','state':'client-state',
     'resource':oauth.resource_url(),'code_challenge':'a'*43,'code_challenge_method':'S256',**changes}


@pytest.mark.parametrize('changes',[{'code_challenge_method':'plain'},{'code_challenge':'bad'},
 {'response_type':'token'},{'scope':'admin'},{'resource':'https://reader.example/api/mcp/'},
 {'state':''},{'prompt':'none'},{'unknown':'x'}])
def test_invalid_authorization(changes):
    with pytest.raises(oauth.OAuthError):
        oauth.begin_authorization(authorize(**changes))


@pytest.mark.parametrize('token',['signed.jwt.token','manage-secret','dror_'+'a'*43,'droa_bad','☃'])
def test_jwt_manage_refresh_are_not_access_tokens(token):
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.validate_access_token(token)
    assert exc.value.error=='invalid_token' and exc.value.status_code==401


def test_token_requires_resource_and_s256():
    for params in [{'grant_type':'client_credentials'},
      {'grant_type':'authorization_code','code':'a'*43,'client_id':'client',
       'redirect_uri':'https://chatgpt.com/connector_platform_oauth_redirect','code_verifier':'short','resource':oauth.resource_url()},
      {'grant_type':'refresh_token','refresh_token':'dror_'+'a'*43,'client_id':'client'},
      {'grant_type':'refresh_token','refresh_token':'dror_'+'a'*43,'client_id':'client','resource':oauth.resource_url(),'client_secret':'x'}]:
        with pytest.raises(oauth.OAuthError):
            oauth.exchange_token(params)


def test_signed_registration_authenticates_metadata_without_client_lookup(monkeypatch):
    key = b'k'*32
    uris = ['https://chatgpt.com/connector/oauth/'+'a'*128]
    client = oauth._encode_client('ChatGPT',uris,key)
    monkeypatch.setattr(oauth,'_registration_key',lambda cur:key)
    assert oauth._registered_client(None,client)=={'client_name':'ChatGPT','redirect_uris':uris}
    # The payload must be authenticated before any JSON processing or DB lookup.
    signed,signature = client.split('.')
    for bad in (signed[:-1]+('A' if signed[-1]!='A' else 'B')+'.'+signature,
                signed+'.'+('A' if signature[0]!='A' else 'B')+signature[1:],
                client+'.extra','drcs1_not-json.'+'a'*43):
        with pytest.raises(oauth.OAuthError) as exc:
            oauth._registered_client(None,bad)
        assert exc.value.error=='invalid_client'
    monkeypatch.setattr(oauth,'_registration_key',lambda cur:b'w'*32)
    with pytest.raises(oauth.OAuthError):
        oauth._registered_client(None,client)


def test_largest_supported_registration_fits_index_and_surrogate_is_rejected():
    uris = ['https://chatgpt.com/connector/oauth/'+str(i)+'a'*127 for i in range(5)]
    client = oauth._encode_client('😀'*100,uris,b'k'*32)
    assert len(client) <= 2048
    with pytest.raises(oauth.OAuthError) as exc:
        oauth._encode_client('\ud800',uris,b'k'*32)
    assert exc.value.error=='invalid_client_metadata'
