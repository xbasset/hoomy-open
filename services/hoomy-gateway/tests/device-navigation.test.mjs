import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer as reserveServer } from 'node:http';
import { createServer } from 'node:https';
import { execFile, execFileSync } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createGateway } from '../gateway.mjs';

const navigation = String.raw`
import http.cookiejar,html.parser,json,ssl,sys,urllib.error,urllib.parse,urllib.request,uuid
origin,certificate=sys.argv[1:3]
interrupted=len(sys.argv)>3 and sys.argv[3]=='interrupted'
phase='setup'
class Form(html.parser.HTMLParser):
    def __init__(self,source):
        super().__init__();self.action=None;self.fields={};self.link=None
        self.feed(source)
    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes)
        if tag=='form':self.action=attrs.get('action')
        if tag=='input' and attrs.get('name') and 'value' in attrs:self.fields[attrs['name']]=attrs['value']
        if tag=='a' and attrs.get('href','').startswith('/auth/github/start'):self.link=attrs['href']
class Redirect(urllib.request.HTTPRedirectHandler):
    hold_device_resume=False
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        value=urllib.parse.urlparse(newurl)
        if self.hold_device_resume and newurl.startswith(origin+'/activate/'):
            return None
        if value.hostname=='github.com':
            state=urllib.parse.parse_qs(value.query)['state'][0]
            newurl=origin+'/auth/github/callback?'+urllib.parse.urlencode({'state':state,'code':'fixture-code'})
        return super().redirect_request(req,fp,code,msg,headers,newurl)
jar=http.cookiejar.CookieJar()
redirect=Redirect()
opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certificate)),urllib.request.HTTPCookieProcessor(jar),redirect)
def require(condition):
    if not condition:raise AssertionError('navigation assertion failed')
def open_page(path,fields=None,as_json=False):
    protocol=urllib.parse.urlparse(path).path.startswith('/oauth/')
    headers={'Accept':'application/json' if as_json or protocol else 'text/html'}
    data=None
    if fields is not None:
        data=(json.dumps(fields) if as_json else urllib.parse.urlencode(fields)).encode()
        headers['Content-Type']='application/json' if as_json else 'application/x-www-form-urlencoded'
        if not as_json and not protocol:headers['Origin']=origin
    request=urllib.request.Request(urllib.parse.urljoin(origin,path),data=data,headers=headers)
    try:response=opener.open(request,timeout=15)
    except urllib.error.HTTPError as error:response=error
    with response:
        body=response.read().decode()
        return response.status,body,dict(response.headers)
def get_verification(path):
    status,body,headers=open_page(path)
    if 'document.forms[0].submit()' in body:
        form=Form(body);status,body,headers=open_page(form.action,form.fields)
    require(status==200 and 'Vérifier la machine' in body)
    return Form(body)
try:
    phase='enrolment'
    status,body,_=open_page('/v1/enrolments',{'installation_id':str(uuid.uuid4()),'label':'Navigation fixture Mac','platform':'darwin','proposed_policy':{'content_tier':'metadata','roots':[],'exclusions':[]}},True)
    require(status==201);pending=json.loads(body)
    status,body,_=open_page('/oauth/device_authorization',{'client_id':'hoomy-collector','scope':'device:self','resource':origin+'/v1/collector','enrolment_ticket':pending['enrolment_ticket']})
    require(status==200);device=json.loads(body)
    phase='verification_tabs'
    stale=get_verification(device['verification_uri_complete'])
    active=get_verification(device['verification_uri_complete'])
    phase='post_code_confirmation'
    status,body,_=open_page(active.action,active.fields)
    login=Form(body)
    require(status==200 and login.link is not None)
    phase='identity_and_consent'
    status,body,headers=open_page(login.link)
    require(status==200 and 'Autoriser cette machine' in body)
    consent=Form(body)
    phase='consent_response_policy'
    require(headers.get('Referrer-Policy')=='same-origin')
    phase='cookie_attributes'
    hoomy=[cookie for cookie in jar if cookie.name=='__Host-hoomy_session']
    session=[cookie for cookie in jar if cookie.name=='_session']
    require(len(hoomy)==1 and hoomy[0].secure and hoomy[0].path=='/' and (hoomy[0].has_nonstandard_attr('HttpOnly') or hoomy[0].has_nonstandard_attr('httponly')))
    require(len(session)==1 and session[0].secure and session[0].path=='/' and (session[0].has_nonstandard_attr('HttpOnly') or session[0].has_nonstandard_attr('httponly')))
    phase='stale_page'
    status,body,_=open_page(stale.action,stale.fields)
    require(status==400 and 'Reprendre l’autorisation' in body and 'Continuer l’autorisation en cours' in body and 'activation_session_required' not in body)
    phase='approve_existing_consent'
    redirect.hold_device_resume=interrupted
    status,body,headers=open_page(consent.action,{**consent.fields,'decision':'approve'})
    if interrupted:
        require(status==303 and headers.get('Location','').startswith(origin+'/activate/'))
        phase='interrupted_token_poll'
        status,body,_=open_page('/oauth/token',{'client_id':'hoomy-collector','grant_type':'urn:ietf:params:oauth:grant-type:device_code','device_code':device['device_code'],'resource':origin+'/v1/collector'})
        require(status==400 and json.loads(body)['error']=='authorization_pending')
        phase='second_browser_reuses_code'
        opener=urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certificate)),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),Redirect())
        status,body,_=open_page(device['verification_uri_complete'])
        require(status==200 and 'document.forms[0].submit()' in body)
        retry=Form(body);status,body,_=open_page(retry.action,retry.fields)
        require(status==400 and 'Cette demande est invalide' in body)
        print(json.dumps({'consent_redirect_held':True,'poll_status':400,'poll_error':'authorization_pending','second_browser_status':400}))
    else:
        require(status==200 and 'Machine autorisée' in body)
        phase='token_poll'
        status,body,_=open_page('/oauth/token',{'client_id':'hoomy-collector','grant_type':'urn:ietf:params:oauth:grant-type:device_code','device_code':device['device_code'],'resource':origin+'/v1/collector'})
        require(status==200 and json.loads(body)['scope']=='device:self')
        print(json.dumps({'https_validation':True,'secure_root_cookies':True,'stale_status':400,'pending_consent_preserved':True,'collector_scope_only':True}))
except BaseException as error:
    print(json.dumps({'failed_stage':phase,'error_class':type(error).__name__,'last_status':locals().get('status'),'login_page':'Se connecter à Hoomy' in locals().get('body',''),'consent_page':'Autoriser cette machine' in locals().get('body',''),'verification_page':'Vérifier la machine' in locals().get('body',''),'cookie_metadata':[{'name':cookie.name if cookie.name in ['_session','__Host-hoomy_session'] else 'other','secure':cookie.secure,'path_is_root':cookie.path=='/','http_only':cookie.has_nonstandard_attr('HttpOnly') or cookie.has_nonstandard_attr('httponly')} for cookie in jar]}))
    sys.exit(1)
`;

for (const interrupted of [false, true]) test(interrupted ? 'consent approval without its resume redirect leaves device polling pending and prevents code reuse in another browser' : 'HTTPS navigation client respects secure cookie paths and stale tabs preserve pending device consent', { timeout:45000 }, async t => {
  const dir = mkdtempSync(join(tmpdir(), 'hoomy-device-navigation-'));
  let gateway, server;
  t.after(async () => { if (server) await new Promise(resolve => server.close(resolve)); gateway?.close(); rmSync(dir, { recursive:true, force:true }); });
  const keyPath = join(dir, 'test.key'), certPath = join(dir, 'test.crt'), configPath = join(dir, 'openssl.cnf');
  writeFileSync(configPath, '[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=san\n[dn]\nCN=127.0.0.1\n[san]\nsubjectAltName=IP:127.0.0.1\n', { mode:0o600 });
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-keyout', keyPath, '-out', certPath, '-config', configPath], { stdio:'ignore' });
  const reserve = reserveServer(); await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
  const port = reserve.address().port; await new Promise(resolve => reserve.close(resolve));
  const origin = `https://127.0.0.1:${port}`;
  gateway = await createGateway({ origin, backendUrl:'http://127.0.0.1:8765', databasePath:join(dir, 'auth.sqlite'), githubClientId:'fixture-client', githubClientSecret:'fixture-secret', allowedGithubIds:['42'], cookieKeys:['b'.repeat(32)] }, { githubIdentity:async () => ({ id:42 }), pilot:async () => ({}) });
  server = createServer({ key:readFileSync(keyPath), cert:readFileSync(certPath) }, gateway.app);
  await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
  const errors = [];
  gateway.provider.on('server_error', (_ctx, error) => errors.push(error));
  // Standard cookiejar enforces Secure and path rules; fixture CA uses normal TLS validation.
  let result;
  try { result = await promisify(execFile)(process.env.HOOMY_TEST_PYTHON || 'python3', ['-c', navigation, origin, certPath, interrupted ? 'interrupted' : 'complete'], { timeout:30000 }); }
  catch (error) { assert.fail('Navigation failed: '+(error.stdout?.trim() || error.code)); }
  assert.deepEqual(JSON.parse(result.stdout), interrupted ? { consent_redirect_held:true, poll_status:400, poll_error:'authorization_pending', second_browser_status:400 } : { https_validation:true, secure_root_cookies:true, stale_status:400, pending_consent_preserved:true, collector_scope_only:true });
  assert.equal(gateway.store.list('device').length, 1);
  if (interrupted) {
    const stage = gateway.store.db.prepare("SELECT json_extract(payload,'$.inFlight') AS in_flight,json_extract(payload,'$.accountId') IS NOT NULL AS complete,expires>? AS unexpired FROM oauth WHERE model='DeviceCode'").get(Date.now());
    assert.deepEqual({ ...stage }, { in_flight:1, complete:0, unexpired:1 });
    assert.equal(gateway.store.list('device')[0].status, 'authorized');
    assert.equal(gateway.store.list('device')[0].last_heartbeat_at, undefined);
  }
  assert.deepEqual(errors, []);
});
