import express from 'express';
import Provider, { errors } from 'oidc-provider';
import { generateKeyPair, exportJWK } from 'jose';
import { timingSafeEqual, randomUUID, createHash } from 'node:crypto';
import { Store, secret, digest } from './store.mjs';
import { validateConfig } from './config.mjs';
import { page, escapeHtml as esc } from './views.mjs';
import { HttpError, pilotClient } from './backend-client.mjs';
import { validatePolicy } from './ingestion.mjs';
import { mountIngestion } from './ingestion-routes.mjs';
import { mountProjectState } from './project-state-routes.mjs';

const DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code';
const MCP_SCOPES = ['projects:read', 'projects:context:write', 'projects:state:refresh', 'projects:state:execute'];
const MCP_OAUTH_SCOPES = [...MCP_SCOPES, 'offline_access'];
const USER_SCOPES = [...MCP_SCOPES, 'devices:manage'];
const COLLECTOR_SCOPES = ['device:self', 'evidence:ingest'];
function equals(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
function requireThat(condition, status, code, message) { if (!condition) throw new HttpError(status, code, message); }
function cookies(req) { return Object.fromEntries((req.headers.cookie || '').split(';').map(part => part.trim().split('='))); }
function validRedirect(uri) {
  try { const url = new URL(uri); return !url.hash && !url.username && !url.password && (url.protocol === 'https:' || (url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname))); }
  catch { return false; }
}

export async function createGateway(input, options = {}) {
  const config = validateConfig(input);
  const store = new Store(config.databasePath);
  const resources = { mcp: `${config.origin}/mcp`, collector: `${config.origin}/v1/collector`, projects: `${config.origin}/v1/projects` };
  const issuer = config.origin;
  const pilot = options.pilot || pilotClient(config.backendUrl);
  let jwks = store.get('settings', 'jwks');
  if (!jwks) {
    const { privateKey } = await generateKeyPair('RS256', { extractable: true });
    jwks = { keys: [{ ...await exportJWK(privateKey), kid: randomUUID(), use: 'sig', alg: 'RS256' }] };
    store.put('settings', 'jwks', jwks);
  }
  const accountAllowed = id => /^github:\d+$/.test(id || '') && config.allowedGithubIds.includes(id.slice(7));
  function enrolment(ticket) {
    const record = typeof ticket === 'string' ? store.get('enrolment-ticket', digest(ticket)) : undefined;
    const value = record && store.get('enrolment', record.id);
    if (!value || value.status !== 'pending') throw new errors.InvalidRequest('The enrolment request is unavailable or expired.');
    return { id: record.id, ...value };
  }
  function collectionPolicy(device, principal) {
    return Boolean(config.ingestionProjectId && device.policy?.content_tier === 'conversation_visible_v1' && device.policy.collection_enabled === true && device.policy.project_bindings?.[0]?.project_id === config.ingestionProjectId && principal.scopes.includes('evidence:ingest'));
  }
  function enrolmentScopes(pending, scope) {
    const requested = typeof scope === 'string' ? scope.split(' ').filter(Boolean) : [];
    const expected = pending.proposed_policy.content_tier === 'conversation_visible_v1' ? COLLECTOR_SCOPES : ['device:self'];
    requireThat(requested.length === expected.length && expected.every(value => requested.includes(value)), 400, 'invalid_scope', 'Request exactly the permissions in this policy proposal.');
    if (expected.includes('evidence:ingest')) validatePolicy(pending.proposed_policy, config.ingestionProjectId);
  }
  const provider = new Provider(issuer, {
    adapter: store.adapter(), jwks,
    clients: [
      { client_id: 'hoomy-collector', client_name: 'Hoomy collector', token_endpoint_auth_method: 'none', grant_types: [DEVICE_GRANT, 'refresh_token'], response_types: [], redirect_uris: [], scope: COLLECTOR_SCOPES.join(' ') },
      ...config.clients.map(client => ({ ...client, token_endpoint_auth_method: 'none', grant_types: ['authorization_code', 'refresh_token'], response_types: ['code'], scope: USER_SCOPES.join(' ') })),
    ],
    cookies: { keys: config.cookieKeys, long: { secure: !config.testMode, sameSite: 'lax' }, short: { secure: !config.testMode, sameSite: 'lax' } },
    scopes: ['openid', 'offline_access', ...USER_SCOPES, ...COLLECTOR_SCOPES],
    pkce: { methods: ['S256'], required: () => true },
    claims: { openid: ['sub'] },
    routes: { authorization: '/oauth/authorize', token: '/oauth/token', jwks: '/oauth/jwks', revocation: '/oauth/revoke', registration: '/oauth/register', code_verification: '/activate', device_authorization: '/oauth/device_authorization' },
    interactions: { url: (_ctx, interaction) => `/interaction/${interaction.uid}` },
    extraParams: ['enrolment_ticket'],
    features: {
      devInteractions: { enabled: false }, userinfo: { enabled: false }, revocation: { enabled: true },
      registration: { enabled: true, issueRegistrationAccessToken: false },
      deviceFlow: {
        enabled: true,
        deviceInfo: ctx => {
          const pending = enrolment(ctx.oidc.params.enrolment_ticket);
          try { enrolmentScopes(pending, ctx.oidc.params.scope); }
          catch { throw new errors.InvalidScope('Request exactly the permissions in the available policy proposal.'); }
          return { enrolment_id: pending.id, label: pending.label, platform: pending.platform, proposed_policy: pending.proposed_policy };
        },
        userCodeInputSource: async (ctx, form, _out, error) => {
          if (error) ctx.status = 400;
          ctx.type = 'html';
          ctx.body = page('Connecter une machine', `${error ? '<p role="alert">Cette demande est invalide, expirée ou annulée. Vérifie le code ou démarre une nouvelle connexion.</p>' : ''}<p>Saisis le code affiché dans ton terminal.</p>${form}<button type="submit" form="op.deviceInputForm">Continuer</button>`);
        },
        userCodeConfirmSource: async (ctx, form, _client, info, code) => { ctx.type = 'html'; ctx.body = page('Vérifier la machine', `<p>Compare ce code avec celui du terminal :</p><h2>${esc(code)}</h2><p>${esc(info.label)} · ${esc(info.platform)}</p><p>Une confirmation des permissions suivra la connexion.</p>${form}<button type="submit" form="op.deviceConfirmForm">Continuer</button> <button class="secondary" type="submit" form="op.deviceConfirmForm" name="abort" value="yes">Annuler</button>`); },
        successSource: async ctx => { ctx.type = 'html'; ctx.body = page('Machine autorisée', '<p>Tu peux retourner dans ton terminal. Le collecteur pourra se connecter ; aucune conversation n’est transférée à cette étape.</p>'); },
      },
      resourceIndicators: {
        enabled: true,
        defaultResource: (_ctx, client) => client.clientId === 'hoomy-collector' ? resources.collector : resources.mcp,
        useGrantedResource: () => true,
        getResourceServerInfo: (_ctx, resource, client) => {
          const collector = client.clientId === 'hoomy-collector';
          if ((collector && resource !== resources.collector) || (!collector && ![resources.mcp, resources.projects].includes(resource))) throw new errors.InvalidTarget('The requested resource is not available to this client.');
          const scopes = collector ? COLLECTOR_SCOPES : resource === resources.mcp ? MCP_SCOPES : USER_SCOPES;
          return { scope: scopes.join(' '), audience: resource, accessTokenFormat: 'opaque', accessTokenTTL: 900 };
        },
      },
    },
    findAccount: async (_ctx, accountId) => accountAllowed(accountId) ? { accountId, claims: async () => ({ sub: accountId }) } : undefined,
    loadExistingGrant: async ctx => {
      // Every machine requires a separate approval and revocable grant.
      if (ctx.oidc.client.clientId === 'hoomy-collector') {
        const approvedId = ctx.oidc.result?.consent?.grantId;
        return approvedId && store.get('device-grant', approvedId) ? provider.Grant.find(approvedId) : undefined;
      }
      const id = ctx.oidc.result?.consent?.grantId || ctx.oidc.session.grantIdFor(ctx.oidc.client.clientId);
      return id ? provider.Grant.find(id) : undefined;
    },
    issueRefreshToken: (_ctx, client) => client.grantTypeAllowed('refresh_token'),
    rotateRefreshToken: () => true,
    expiresWithSession: () => false,
    ttl: { AccessToken: 900, DeviceCode: config.deviceCodeTtl || 600, Interaction: 600, RefreshToken: 90 * 86400, Grant: 90 * 86400, Session: 7 * 86400 },
  });
  provider.proxy = !config.testMode;
  provider.use(async (ctx, next) => {
    await next();
    // Native registration uses discovery scopes; collector permissions stay explicit.
    if (ctx.oidc?.route === 'discovery' && ctx.status === 200 && ctx.body && typeof ctx.body === 'object') {
      ctx.body.scopes_supported = ['openid', ...MCP_OAUTH_SCOPES];
    }
  });
  const tokenObserver = options.tokenOutcomeObserver || (config.tokenOutcomeDiagnostics ? outcome => console.log('Hoomy OAuth token outcome ' + JSON.stringify(outcome)) : undefined);
  if (tokenObserver) {
    const errorCodes = new Set(['invalid_grant', 'invalid_client', 'invalid_request', 'invalid_scope', 'invalid_target', 'unauthorized_client', 'unsupported_grant_type', 'server_error', 'temporarily_unavailable']);
    const diagnosticScopes = [...USER_SCOPES, 'offline_access', 'device:self', 'openid'];
    const scopeMembership = scope => {
      const values = new Set(typeof scope === 'string' ? scope.split(' ') : []);
      return { known: Object.fromEntries(diagnosticScopes.map(name => [name, values.has(name)])), unknown: [...values].some(name => !diagnosticScopes.includes(name)) };
    };
    const observed = new WeakSet();
    let minute = 0, observations = 0;
    const observe = (ctx, error) => {
      if (ctx.oidc?.route !== 'token' || observed.has(ctx.res)) return;
      observed.add(ctx.res);
      // Observe provider outcomes, never token response bodies or raw errors.
      const client = ctx.oidc.client;
      const outcome = {
        refresh_attempt: ctx.oidc.params?.grant_type === 'refresh_token',
        client_type: !client ? 'unknown' : client.clientId === 'hoomy-collector' ? 'collector' : client.applicationType === 'native' ? 'native' : 'user',
        error_code: !error ? null : errorCodes.has(error.error) ? error.error : 'other_error',
        ...(error?.error === 'invalid_scope' ? { requested_scope_membership:scopeMembership(ctx.oidc.params?.scope), missing_scope_membership:scopeMembership(error.scope) } : {}),
      };
      ctx.res.once('finish', () => {
        const current = Math.floor(Date.now() / 60000);
        if (minute !== current) { minute = current; observations = 0; }
        if (observations++ >= 60) return;
        try { tokenObserver({ ...outcome, status: ctx.res.statusCode }); } catch { /* Diagnostics cannot alter authentication. */ }
      });
    };
    provider.on('grant.success', ctx => observe(ctx));
    provider.on('grant.error', observe);
    provider.on('server_error', (ctx, error) => observe(ctx, error));
  }
  // Reusing a refresh token or revoking its family disables registry grants too.
  provider.on('grant.revoked', (_ctx, id) => {
    const revokedAt = new Date().toISOString();
    store.put('revoked-grant', id, { revoked_at: revokedAt });
    const binding = store.get('device-grant', id);
    const device = binding && store.get('device', binding.device_id);
    // An old family must not revoke a newly approved grant for the same device.
    if (device?.grant_id === id) store.put('device', binding.device_id, { ...device, status:'revoked', revoked_at:revokedAt });
  });
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', config.trustedProxyIps || ['loopback']);
  app.use((req, res, next) => {
    res.set('X-Request-Id', randomUUID());
    // Keep same-origin form Origins intact without leaking referrers cross-origin.
    res.set({ 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin', 'X-Frame-Options': 'DENY' });
    const host = new URL(config.origin).host;
    if (req.get('host') !== host) return res.status(421).json({ error: 'invalid_host' });
    if (!config.testMode && !req.secure) return res.status(400).json({ error: 'https_required' });
    if (!config.testMode) {
      // The OAuth library receives only the origin already validated above.
      req.headers['x-forwarded-host'] = host;
      req.headers['x-forwarded-proto'] = 'https';
    }
    next();
  });
  function quota(limit = 30) {
    return (req, res, next) => {
      const id = digest(`${req.ip}:${req.path}:${Math.floor(Date.now() / 60000)}`);
      const count = (store.get('quota', id)?.count || 0) + 1;
      store.put('quota', id, { count }, 120);
      if (count > limit) return res.set('Retry-After', '60').status(429).json({ error: 'rate_limited' });
      next();
    };
  }
  if (config.releaseDirectory) {
    const { releaseRouter } = await import('./release.mjs');
    app.use(await releaseRouter(config.releaseDirectory));
  }
  const json = express.json({ limit: '16kb', strict: true });
  const form = express.urlencoded({ extended: false, limit: '16kb' });
  const cookieName = config.testMode ? 'hoomy_session' : '__Host-hoomy_session';
  function browser(req, res, create = false) {
    const id = cookies(req)[cookieName];
    let session = id && store.get('browser', digest(id));
    if (!session && create) {
      const value = secret(); session = { csrf: secret() };
      res.cookie(cookieName, value, { httpOnly: true, secure: !config.testMode, sameSite: 'lax', path: '/', maxAge: 7 * 86400000 });
      session.key = digest(value);
      store.put('browser', session.key, session, 7 * 86400);
    }
    return session;
  }
  function saveBrowser(session) { store.put('browser', session.key, session, 7 * 86400); }
  function csrf(req, res) {
    const session = browser(req, res);
    requireThat(session && equals(req.body.csrf, session.csrf) && (!req.get('origin') || req.get('origin') === config.origin), 403, 'csrf_failed', 'Reload this page before approving.');
    return session;
  }
  async function authenticateRequest(req, { resource = resources.mcp, scopes = [] } = {}) {
    const bearer = req.headers.authorization?.match(/^Bearer ([A-Za-z0-9._~-]+)$/i)?.[1];
    requireThat(bearer, 401, 'invalid_token', 'Authentication is required.');
    const token = await provider.AccessToken.find(bearer);
    requireThat(token && !token.isExpired && token.aud === resource && accountAllowed(token.accountId), 401, 'invalid_token', 'The authorization is expired or unavailable.');
    requireThat((resource === resources.collector) === (token.clientId === 'hoomy-collector'), 401, 'invalid_token', 'The authorization belongs to a different client type.');
    const grant = await provider.Grant.find(token.grantId);
    requireThat(grant && !store.get('revoked-grant', token.grantId), 401, 'invalid_token', 'The authorization has been revoked.');
    const granted = (token.scope || '').split(' ');
    requireThat(scopes.every(scope => granted.includes(scope)), 403, 'insufficient_scope', 'This authorization lacks the requested permission.');
    const principal = { account_id: token.accountId, workspace_id: config.workspaceId, scopes: granted, audience: token.aud, client_id: token.clientId, grant_id: token.grantId };
    if (token.clientId === 'hoomy-collector') {
      const binding = store.get('device-grant', token.grantId);
      const device = binding && store.get('device', binding.device_id);
      requireThat(device && device.grant_id === token.grantId && device.status !== 'revoked' && device.account_id === token.accountId, 401, 'device_revoked', 'This machine is no longer authorized.');
      principal.device_id = binding.device_id;
    }
    return principal;
  }
  function protectedRoute(resource, scopes, handler) {
    return async (req, res) => {
      try { const principal = await authenticateRequest(req, { resource, scopes }); await handler(req, res, principal); }
      catch (error) {
        if (error.status === 401) res.set('WWW-Authenticate', `Bearer resource_metadata="${config.origin}/.well-known/oauth-protected-resource${resource === resources.mcp ? '/mcp' : resource === resources.projects ? '/v1/projects' : '/v1/collector'}"`);
        throw error;
      }
    };
  }
  function publicDevice(device) {
    const { grant_id, ...view } = device;
    // Older OAuth revocations stored the marker without updating device status.
    if (store.get('revoked-grant', grant_id)) view.status = 'revoked';
    return view;
  }
  app.get('/health', (_req, res) => res.json({ status: 'ok', milestone: 1 }));
  app.get('/', (_req, res) => res.redirect('/devices'));
  app.get('/devices', (req, res) => {
    const session = browser(req, res, true);
    if (!accountAllowed(session.account_id)) return res.type('html').send(page('Tes machines Hoomy', '<p>Connecte-toi pour voir les machines autorisées.</p><a class="button" href="/auth/github/start?return_to=/devices">Continuer avec GitHub</a>'));
    const devices = store.list('device').filter(device => device.account_id === session.account_id).map(publicDevice);
    res.type('html').send(page('Tes machines Hoomy', `<p>Compte : ${esc(session.account_id)}</p>${devices.length ? devices.map(device => `<section><h2>${esc(device.label)}</h2><p>${esc(device.platform)} · ${esc(device.status)}<br>Dernier contact : ${esc(device.last_heartbeat_at || 'aucun')}</p>${device.status !== 'revoked' ? `<form method="post" action="/devices/${esc(device.id)}/revoke"><input type="hidden" name="csrf" value="${esc(session.csrf)}"><button class="secondary">Révoquer cette machine</button></form>` : ''}</section>`).join('') : '<p>Aucune machine autorisée.</p>'}`));
  });
  app.get('/.well-known/hoomy', (_req, res) => res.json({ canonical_server_url: config.origin, api_version: 1, issuer, resources, authentication_methods: ['github', 'device_authorization'], endpoints: { enrolments: `${config.origin}/v1/enrolments`, device_authorization: `${config.origin}/oauth/device_authorization`, token: `${config.origin}/oauth/token`, revoke: `${config.origin}/oauth/revoke`, policy_proposals:`${config.origin}/v1/devices/self/policy-proposals`, events:`${config.origin}/v1/events`, receipts:`${config.origin}/v1/receipts` }, ...(config.releaseDirectory ? { installer_manifest_url: `${config.origin}/manifest.json` } : {}), collection_available: Boolean(config.ingestionProjectId) }));
  for (const [name, resource] of Object.entries(resources)) {
    app.get(`/.well-known/oauth-protected-resource${new URL(resource).pathname}`, (_req, res) => res.json({ resource, authorization_servers: [issuer], scopes_supported: name === 'collector' ? COLLECTOR_SCOPES : name === 'mcp' ? MCP_OAUTH_SCOPES : USER_SCOPES, bearer_methods_supported: ['header'] }));
  }
  app.post('/oauth/register', quota(10), json, (req, _res, next) => {
    const allowed = new Set(['client_name','redirect_uris','grant_types','response_types','token_endpoint_auth_method','application_type','scope']);
    requireThat(Object.keys(req.body).every(key => allowed.has(key)), 400, 'invalid_client_metadata', 'Unsupported client metadata.');
    requireThat(Array.isArray(req.body.redirect_uris) && req.body.redirect_uris.length > 0 && req.body.redirect_uris.length <= 5 && req.body.redirect_uris.every(validRedirect), 400, 'invalid_client_metadata', 'Exact HTTPS or loopback redirect URLs are required.');
    requireThat(!req.body.grant_types || (Array.isArray(req.body.grant_types) && req.body.grant_types.every(type => ['authorization_code','refresh_token'].includes(type))), 400, 'invalid_client_metadata', 'Only user authorization grants are supported.');
    requireThat(!req.body.scope || (typeof req.body.scope === 'string' && req.body.scope.split(' ').every(scope => [...USER_SCOPES,'openid','offline_access'].includes(scope))), 400, 'invalid_client_metadata', 'Collector scopes cannot be requested by user clients.');
    requireThat(!req.body.token_endpoint_auth_method || req.body.token_endpoint_auth_method === 'none', 400, 'invalid_client_metadata', 'Public PKCE clients are required.');
    requireThat(store.db.prepare("SELECT count(*) AS total FROM oauth WHERE model='Client'").get().total < 1000, 503, 'registration_capacity', 'Client registration capacity has been reached.');
    const native = req.body.redirect_uris.every(uri => new URL(uri).protocol === 'http:');
    req.body = { ...req.body, application_type: req.body.application_type || (native ? 'native' : 'web'), grant_types: ['authorization_code','refresh_token'], response_types: ['code'], token_endpoint_auth_method: 'none', scope: req.body.scope || USER_SCOPES.join(' ') };
    next();
  });
  app.post('/v1/enrolments', quota(5), json, (req, res) => {
    const data = req.body;
    requireThat(typeof data.installation_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(data.installation_id) && typeof data.label === 'string' && data.label.length > 0 && data.label.length <= 100 && ['darwin','linux','macos'].includes(data.platform), 400, 'invalid_enrolment', 'Provide an installation UUID, label and supported platform.');
    const proposed = data.proposed_policy || { content_tier: 'metadata', roots: [], exclusions: [] };
    requireThat(proposed.content_tier === 'metadata' && Array.isArray(proposed.roots) && Array.isArray(proposed.exclusions), 400, 'unsupported_policy', 'Milestone 1 supports metadata policy approval only; collection remains disabled.');
    const id = randomUUID(), ticket = secret();
    store.put('enrolment', id, { installation_id: data.installation_id, label: data.label, platform: data.platform, architecture: data.architecture, agent_version: data.agent_version, proposed_policy: proposed, policy_revision: 1, status: 'pending' }, 600);
    store.put('enrolment-ticket', digest(ticket), { id }, 600);
    res.status(201).json({ enrolment_id: id, enrolment_ticket: ticket, policy_revision: 1, expires_in: 600 });
  });
  app.post('/v1/devices/self/policy-proposals', quota(5), json, protectedRoute(resources.collector, ['device:self'], async (req, res, principal) => {
    requireThat(req.body && Object.keys(req.body).length === 1 && Object.hasOwn(req.body, 'proposed_policy'), 400, 'invalid_policy', 'Provide one explicit policy proposal.');
    const proposed = validatePolicy(req.body.proposed_policy, config.ingestionProjectId);
    const result = await pilot('/api/projects');
    const project = result.projects?.find(project => project.id === config.ingestionProjectId && !project.dismissed && project.activity !== 'archived');
    requireThat(project, 400, 'project_unavailable', 'The configured pilot project is unavailable.');
    const device = store.get('device', principal.device_id);
    requireThat(device?.grant_id === principal.grant_id && device.status !== 'revoked' && !store.get('revoked-grant', principal.grant_id), 401, 'device_revoked', 'This machine is no longer authorized.');
    requireThat(Number.isSafeInteger(device.policy_revision) && device.policy_revision < Number.MAX_SAFE_INTEGER, 409, 'policy_conflict', 'The existing policy revision is unavailable.');
    const id = randomUUID(), ticket = secret(), revision = device.policy_revision + 1;
    store.transaction(() => {
      store.put('enrolment', id, { installation_id:device.installation_id, label:device.label, platform:device.platform, architecture:device.architecture, agent_version:device.agent_version, proposed_policy:proposed, policy_revision:revision, status:'pending', account_id:principal.account_id, device_id:device.id || principal.device_id, previous_grant_id:principal.grant_id, previous_policy_revision:device.policy_revision, project_name:project.name }, 600);
      store.put('enrolment-ticket', digest(ticket), { id }, 600);
    });
    res.status(201).json({ enrolment_id:id, enrolment_ticket:ticket, policy_revision:revision, expires_in:600 });
  }));
  function validatePendingOwner(pending, accountId) {
    if (!pending.account_id) return;
    const current = store.get('device', pending.device_id);
    requireThat(pending.account_id === accountId && current?.account_id === accountId && current.grant_id === pending.previous_grant_id && current.policy_revision === pending.previous_policy_revision && current.status !== 'revoked' && !store.get('revoked-grant', current.grant_id), 409, 'policy_conflict', 'The previous machine authorization changed; prepare a new policy proposal.');
  }
  function machineSummary(pending) {
    const introduction = `<p>${esc(pending.label)} · ${esc(pending.platform)}</p>`;
    if (pending.proposed_policy.content_tier !== 'conversation_visible_v1') return introduction + '<p>Les permissions permettent uniquement l’état et le heartbeat de cette machine. Aucune conversation ne sera transférée.</p>';
    const policy = pending.proposed_policy;
    return introduction + `<p>Projet : <strong>${esc(pending.project_name)}</strong></p><p>Tu autorises l’envoi des messages utilisateur et des réponses finales de l’assistant pour ce projet. Les raisonnements, outils, instructions et messages sensibles sont exclus. Aucun transfert des fichiers JSONL originaux.</p><p>Dossiers du projet :</p><ul>${policy.project_bindings[0].cwd_roots.map(root => `<li><code>${esc(root)}</code></li>`).join('')}</ul><p>Historique autorisé :</p><ul>${policy.roots.map(root => `<li><code>${esc(root)}</code></li>`).join('')}</ul>${policy.exclusions.length ? `<p>Chemins exclus :</p><ul>${policy.exclusions.map(root => `<li><code>${esc(root)}</code></li>`).join('')}</ul>` : '<p>Aucun chemin exclu supplémentaire.</p>'}<p>Permissions de collecte, version ${pending.policy_revision}. Cette autorisation continue reste révocable depuis « Tes machines ».</p>`;
  }
  app.get('/interaction/:uid', async (req, res) => {
    const details = await provider.interactionDetails(req, res);
    const session = browser(req, res, true);
    session.interaction = details.uid;
    if (!session.account_id && accountAllowed(details.session?.accountId)) session.account_id = details.session.accountId;
    saveBrowser(session);
    if (details.prompt.name === 'login') {
      if (accountAllowed(session.account_id)) return provider.interactionFinished(req, res, { login: { accountId: session.account_id } }, { mergeWithLastSubmission: false });
      return res.type('html').send(page('Se connecter à Hoomy', `<p>Connecte-toi pour consulter tes projets ou approuver cette machine.</p><a class="button" href="/auth/github/start?interaction=${esc(details.uid)}">Continuer avec GitHub</a>`));
    }
    requireThat(details.prompt.name === 'consent' && session.account_id === details.session?.accountId && accountAllowed(details.session?.accountId), 403, 'access_denied', 'The account cannot approve this request.');
    const machine = details.params.client_id === 'hoomy-collector';
    const pending = machine ? enrolment(details.params.enrolment_ticket) : undefined;
    if (machine) { enrolmentScopes(pending, details.params.scope); validatePendingOwner(pending, details.session.accountId); }
    const continuingAccess = details.params.scope?.split(' ').includes('offline_access') ? '<p>Cette autorisation permet un accès continu à tes projets et reste révocable.</p>' : '';
    const scopes = details.params.scope?.split(' ') || [];
    const statePermissions = `${scopes.includes('projects:state:refresh') ? '<p>Tu autorises la création explicite de synthèses de projet. Chaque synthèse peut consommer ton quota Codex, dans les limites affichées.</p>' : ''}${scopes.includes('projects:state:execute') ? '<p>Tu autorises un exécuteur connecté à lire les extraits centraux sélectionnés et à rendre une synthèse citée. Il ne peut pas modifier tes objectifs approuvés.</p>' : ''}`;
    const summary = `<p>Compte : ${esc(details.session.accountId)} · espace : ${esc(config.workspaceId)}</p>` + (machine ? machineSummary(pending) : `<p>Cette connexion peut consulter les projets et enregistrer les modifications que tu approuves.</p>${continuingAccess}${statePermissions}<p>Client : ${esc(details.params.client_id)}</p><p>Permissions : ${esc(details.params.scope)}</p>`);
    res.type('html').send(page(machine ? 'Autoriser cette machine' : 'Autoriser la connexion', `${summary}<form method="post" action="/interaction/${esc(details.uid)}/confirm"><input type="hidden" name="csrf" value="${esc(session.csrf)}">${machine ? `<input type="hidden" name="policy_revision" value="${pending.policy_revision}">` : ''}<button name="decision" value="approve">Autoriser</button> <button class="secondary" name="decision" value="deny">Refuser</button></form>`));
  });
  app.get('/auth/github/start', quota(20), (req, res) => {
    const session = browser(req, res);
    const standalone = req.query.return_to === '/devices';
    requireThat(session && (standalone || (typeof req.query.interaction === 'string' && req.query.interaction === session.interaction)), 400, 'invalid_transaction', 'Start login from an active Hoomy request.');
    const state = secret(), verifier = secret();
    store.put('github-state', digest(state), { browser_key: session.key, ...(standalone ? { return_to:'/devices' } : { interaction:session.interaction }), verifier }, 600);
    const url = new URL('https://github.com/login/oauth/authorize');
    url.search = new URLSearchParams({ client_id: config.githubClientId, redirect_uri: `${config.origin}/auth/github/callback`, state, code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256' });
    res.redirect(url.href);
  });
  const githubIdentity = options.githubIdentity || (async (code, verifier) => {
    const response = await fetch('https://github.com/login/oauth/access_token', { method: 'POST', headers: { Accept: 'application/json', 'Content-Type':'application/json' }, body: JSON.stringify({ client_id: config.githubClientId, client_secret: config.githubClientSecret, code, code_verifier: verifier, redirect_uri: `${config.origin}/auth/github/callback` }), signal: AbortSignal.timeout(10000), redirect: 'error' });
    const data = await response.json();
    requireThat(response.ok && typeof data.access_token === 'string', 401, 'identity_failed', 'GitHub login could not be verified.');
    const identityResponse = await fetch('https://api.github.com/user', { headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${data.access_token}`, 'X-GitHub-Api-Version':'2022-11-28' }, signal: AbortSignal.timeout(10000), redirect: 'error' });
    requireThat(identityResponse.ok, 401, 'identity_failed', 'GitHub identity could not be verified.');
    return identityResponse.json();
  });
  app.get('/auth/github/callback', async (req, res) => {
    const session = browser(req, res);
    const state = typeof req.query.state === 'string' ? store.get('github-state', digest(req.query.state)) : undefined;
    requireThat(session && state && equals(state.browser_key, session.key) && typeof req.query.code === 'string', 400, 'invalid_transaction', 'The GitHub login transaction is unavailable.');
    store.take('github-state', digest(req.query.state));
    const identity = await githubIdentity(req.query.code, state.verifier);
    const id = `github:${identity.id}`;
    requireThat(Number.isSafeInteger(identity.id) && accountAllowed(id), 403, 'account_not_invited', 'This GitHub account is not authorized for this private pilot.');
    session.account_id = id; session.csrf = secret(); saveBrowser(session);
    res.redirect(state.return_to || `/interaction/${encodeURIComponent(state.interaction)}/complete`);
  });
  app.get('/interaction/:uid/complete', async (req, res) => {
    const details = await provider.interactionDetails(req, res);
    const session = browser(req, res);
    requireThat(session && session.interaction === details.uid && accountAllowed(session.account_id) && details.prompt.name === 'login', 403, 'invalid_transaction', 'This login request cannot be completed.');
    await provider.interactionFinished(req, res, { login: { accountId: session.account_id } }, { mergeWithLastSubmission: false });
  });
  app.post('/interaction/:uid/confirm', form, async (req, res) => {
    const session = csrf(req, res);
    const details = await provider.interactionDetails(req, res);
    requireThat(details.prompt.name === 'consent' && session.interaction === details.uid && session.account_id === details.session?.accountId && accountAllowed(details.session?.accountId), 403, 'invalid_transaction', 'This request cannot be approved.');
    if (req.body.decision !== 'approve') return provider.interactionFinished(req, res, { error: 'access_denied', error_description: 'The user declined the request.' }, { mergeWithLastSubmission: false });
    const machine = details.params.client_id === 'hoomy-collector';
    const pending = machine ? enrolment(details.params.enrolment_ticket) : undefined;
    if (machine) {
      requireThat(String(pending.policy_revision) === req.body.policy_revision, 409, 'policy_conflict', 'Reload and confirm the changed policy.');
      enrolmentScopes(pending, details.params.scope); validatePendingOwner(pending, details.session.accountId);
    }
    let grant = details.grantId ? await provider.Grant.find(details.grantId) : undefined;
    if (!grant) grant = new provider.Grant({ accountId: details.session.accountId, clientId: details.params.client_id });
    const missing = details.prompt.details;
    if (missing.missingOIDCScope) grant.addOIDCScope(missing.missingOIDCScope.join(' '));
    if (missing.missingOIDCClaims) grant.addOIDCClaims(missing.missingOIDCClaims);
    for (const [resource, scopes] of Object.entries(missing.missingResourceScopes || {})) grant.addResourceScope(resource, scopes.join(' '));
    const grantId = await grant.save();
    if (machine) {
      const previous = store.list('device').find(device => device.installation_id === pending.installation_id && device.account_id === details.session.accountId);
      const id = previous?.id || randomUUID();
      if (previous?.grant_id) {
        const old = await provider.Grant.find(previous.grant_id); if (old) await old.destroy();
      }
      store.transaction(() => {
        // Recheck after asynchronous provider work before changing the registry.
        validatePendingOwner(pending, details.session.accountId);
        if (previous?.grant_id) store.put('revoked-grant', previous.grant_id, { revoked_at:new Date().toISOString() });
        store.put('device', id, { account_id: details.session.accountId, workspace_id: config.workspaceId, installation_id: pending.installation_id, label: pending.label, platform: pending.platform, architecture: pending.architecture, policy: pending.proposed_policy, policy_revision: pending.policy_revision, grant_id: grantId, status: 'authorized', enrolled_at: new Date().toISOString() });
        store.put('device-grant', grantId, { device_id: id });
        store.put('enrolment', pending.id, { ...pending, status: 'approved', device_id: id }, 600);
      });
    }
    await provider.interactionFinished(req, res, { consent: { grantId } }, { mergeWithLastSubmission: true });
  });
  app.get('/v1/devices/self', protectedRoute(resources.collector, ['device:self'], (_req, res, principal) => {
    const device = store.get('device', principal.device_id); const { grant_id, ...publicDevice } = device;
    res.json({ device_id: principal.device_id, ...publicDevice, collection_available: collectionPolicy(device, principal) });
  }));
  app.post('/v1/devices/self/activate', json, protectedRoute(resources.collector, ['device:self'], (req, res, principal) => {
    const device = store.get('device', principal.device_id);
    requireThat(req.body.policy_revision === device.policy_revision, 409, 'policy_conflict', 'Reload the approved policy.');
    device.status = 'active'; device.capabilities = req.body.capabilities; store.put('device', principal.device_id, device);
    res.json({ device_id: principal.device_id, status: device.status, collection_available: collectionPolicy(device, principal) });
  }));
  app.post('/v1/devices/self/heartbeat', json, protectedRoute(resources.collector, ['device:self'], (req, res, principal) => {
    const device = store.get('device', principal.device_id);
    requireThat(req.body.collection_enabled === false || req.body.collection_enabled === undefined || (req.body.collection_enabled === true && collectionPolicy(device, principal)), 400, 'collection_unavailable', 'This machine has no approved collection policy.');
    device.last_heartbeat_at = new Date().toISOString(); device.agent_version = req.body.agent_version;
    store.put('device', principal.device_id, device);
    res.json({ device_id: principal.device_id, status: device.status, collection_available: collectionPolicy(device, principal), received_at: device.last_heartbeat_at });
  }));
  app.get('/v1/devices', protectedRoute(resources.projects, ['devices:manage'], (_req, res, principal) => res.json({ devices: store.list('device').filter(device => device.account_id === principal.account_id).map(publicDevice) })));
  async function revokeDevice(id, accountId) {
    const device = store.get('device', id);
    requireThat(device && device.account_id === accountId, 404, 'device_not_found', 'The machine is unavailable.');
    device.status = 'revoked'; store.put('device', id, device);
    store.put('revoked-grant', device.grant_id, { revoked_at: new Date().toISOString() });
    const grant = await provider.Grant.find(device.grant_id); if (grant) await grant.destroy();
  }
  app.post('/devices/:id/revoke', form, async (req, res) => {
    const session = csrf(req, res);
    requireThat(accountAllowed(session.account_id), 403, 'access_denied', 'An authorized account is required.');
    await revokeDevice(req.params.id, session.account_id);
    res.redirect('/devices');
  });
  app.post('/v1/devices/:id/revoke', json, protectedRoute(resources.projects, ['devices:manage'], async (req, res, principal) => {
    await revokeDevice(req.params.id, principal.account_id);
    res.json({ device_id: req.params.id, status: 'revoked' });
  }));
  app.get('/v1/projects', protectedRoute(resources.projects, ['projects:read'], async (_req, res) => {
    const result = await pilot('/api/projects');
    requireThat(Array.isArray(result.projects), 502, 'pilot_response_invalid', 'The project list is unavailable.');
    res.json({ projects:result.projects.filter(project => !project.dismissed && project.activity !== 'archived').map(project => ({ id:project.id, name:project.name })) });
  }));
  app.get('/v1/projects/:id', protectedRoute(resources.projects, ['projects:read'], async (req, res) => res.json(await pilot(`/api/projects/context/${encodeURIComponent(req.params.id)}`))));
  mountIngestion({ app, config, store, resources, authenticateRequest, protectedRoute, pilot, collectionPolicy });
  mountProjectState({ app, config, resources, authenticateRequest, protectedRoute, pilot });
  function recordProjectUpdate({ principal, project_id, previous_revision, revision, field_names }) {
    store.put('project-update-audit', randomUUID(), { account_id: principal.account_id, workspace_id: principal.workspace_id, client_id: principal.client_id, project_id, previous_revision, revision, field_names, recorded_at: new Date().toISOString() });
  }
  app.post('/v1/projects/:id/context', json, protectedRoute(resources.projects, ['projects:context:write'], async (req, res, principal) => {
    const result = await pilot('/api/projects/context', { project_id: req.params.id, revision: req.body.revision, fields: req.body.fields });
    try {
      recordProjectUpdate({ principal, project_id:req.params.id, previous_revision:req.body.revision, revision:result.revision, field_names:Object.keys(req.body.fields || {}) });
    } catch {
      result.warnings = [...(result.warnings || []), { code:'audit_unavailable', message:'La modification est enregistrée, mais sa trace d’authentification n’a pas pu être confirmée.' }];
    }
    res.json(result);
  }));
  const mountMcp = options.mountMcp || (await import('./mcp.mjs')).createRemoteMcpHandler;
  app.all('/mcp', await mountMcp({ authenticateRequest, backendUrl: config.backendUrl, resourceUrl: resources.mcp, authorizationServerUrl: issuer, onProjectUpdated:recordProjectUpdate, stateProjectId:config.stateProjectId }));
  app.post('/oauth/device_authorization', quota(10));
  app.post('/oauth/token', quota(60));
  app.get('/oauth/authorize', quota(30), (req, _res, next) => {
    // Retain an explicitly requested offline marker through real provider consent.
    if (typeof req.query.scope === 'string' && req.query.scope.split(' ').includes('offline_access') && !Object.hasOwn(req.query, 'prompt')) {
      const url = new URL(req.url, config.origin);
      url.searchParams.set('prompt', 'consent');
      req.url = url.pathname + url.search;
    }
    next();
  });
  app.get('/activate', quota(30));
  app.post('/activate', quota(20), async (req, res, next) => {
    // Rejected first-contact posts must not enter the provider's form re-render
    // path with an unsaved Session; its outer persistence requires an expiry.
    const session = await provider.Session.get(provider.createContext(req, res));
    if (!(typeof session.exp === 'number' && session.state?.secret) && req.accepts(['json', 'html']) === 'html') {
      const current = browser(req, res);
      const interaction = current?.interaction && await provider.Interaction.find(current.interaction);
      const pendingConsent = interaction?.prompt?.name === 'consent' && interaction.params.client_id === 'hoomy-collector' && interaction.session?.accountId === current?.account_id && accountAllowed(current?.account_id);
      const link = pendingConsent ? `/interaction/${encodeURIComponent(current.interaction)}` : '/activate';
      return res.status(400).type('html').send(page('Reprendre l’autorisation', `<p>Cette page de vérification n’est plus active.</p>${pendingConsent ? '<p>Une autorisation est déjà en cours. Continue depuis sa page de consentement.</p>' : '<p>Ouvre une nouvelle page de vérification pour continuer.</p>'}<a class="button" href="${esc(link)}">${pendingConsent ? 'Continuer l’autorisation en cours' : 'Ouvrir la vérification'}</a>`));
    }
    requireThat(typeof session.exp === 'number' && session.state?.secret, 400, 'activation_session_required', 'Open the verification page again before entering a code.');
    next();
  });
  app.use(provider.callback());
  app.use((error, _req, res, _next) => {
    const status = error.status || (error.type === 'entity.too.large' ? 413 : 500);
    res.status(status).json({ error: error.code || 'gateway_error', message: status < 500 ? error.message : 'The central service could not complete this request.', correlation_id: res.get('X-Request-Id'), ...(error.details ? { details: error.details } : {}) });
  });
  return { app, provider, store, resources, authenticateRequest, close: () => store.close() };
}
