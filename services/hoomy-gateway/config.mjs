import { resolve } from 'node:path';

export function validateConfig(input) {
  const config = { workspaceId: 'pilot', ...input };
  const origin = new URL(config.origin);
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname);
  if (origin.protocol !== 'https:' && !(config.testMode && local && origin.protocol === 'http:')) throw new Error('A public HTTPS origin is required.');
  if (origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) throw new Error('Use a canonical server origin without a path.');
  config.origin = origin.origin;
  const backend = new URL(config.backendUrl);
  if (backend.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(backend.hostname) || backend.pathname !== '/' || backend.search || backend.hash || backend.username || backend.password) throw new Error('The pilot backend must remain loopback-only.');
  if (!Array.isArray(config.allowedGithubIds) || !config.allowedGithubIds.length || config.allowedGithubIds.some(id => !/^\d+$/.test(String(id)))) throw new Error('Explicit GitHub numeric user IDs are required.');
  config.allowedGithubIds = config.allowedGithubIds.map(String);
  if (!config.githubClientId || !config.githubClientSecret) throw new Error('GitHub App client credentials are required.');
  if (!config.cookieKeys?.length || config.cookieKeys.some(key => typeof key !== 'string' || key.length < 32)) throw new Error('Cookie signing keys need at least 32 characters each.');
  if (typeof config.databasePath !== 'string' || !config.databasePath) throw new Error('An external gateway database path is required.');
  config.databasePath = resolve(config.databasePath);
  config.ingestionProjectId ??= '';
  if (typeof config.ingestionProjectId !== 'string' || (config.ingestionProjectId && !/^[A-Za-z0-9._-]{1,128}$/.test(config.ingestionProjectId))) throw new Error('Configure one canonical ingestion pilot project ID.');
  config.stateProjectId ??= '';
  if (typeof config.stateProjectId !== 'string' || (config.stateProjectId && !/^[A-Za-z0-9._-]{1,128}$/.test(config.stateProjectId))) throw new Error('Configure one canonical state pilot project ID.');
  config.clients ??= [];
  for (const client of config.clients) {
    if (!client.client_id || !client.redirect_uris?.length || client.redirect_uris.some(uri => {
      const url = new URL(uri);
      return url.hash || url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1','localhost','[::1]'].includes(url.hostname)));
    })) throw new Error('OAuth clients require exact approved HTTPS or loopback callbacks.');
  }
  return config;
}

export function configFromEnv(env = process.env) {
  function json(name, fallback) {
    try { return JSON.parse(env[name] || fallback); }
    catch { throw new Error(`${name} must contain valid JSON.`); }
  }
  return validateConfig({
    origin: env.HOOMY_PUBLIC_ORIGIN || 'https://my.hoomy.dev',
    backendUrl: env.HOOMY_PILOT_URL || 'http://127.0.0.1:8765',
    databasePath: env.HOOMY_AUTH_DB,
    githubClientId: env.HOOMY_GITHUB_CLIENT_ID,
    githubClientSecret: env.HOOMY_GITHUB_CLIENT_SECRET,
    allowedGithubIds: (env.HOOMY_ALLOWED_GITHUB_IDS || '').split(',').filter(Boolean),
    cookieKeys: json('HOOMY_COOKIE_KEYS', '[]'),
    clients: json('HOOMY_OAUTH_CLIENTS', '[]'),
    releaseDirectory: env.HOOMY_RELEASE_DIR,
    tokenOutcomeDiagnostics: env.HOOMY_AUTH_OUTCOME_DIAGNOSTICS === '1',
    ingestionProjectId: env.HOOMY_INGEST_PROJECT_ID || '',
    stateProjectId: env.HOOMY_STATE_PROJECT_ID || '',
  });
}
