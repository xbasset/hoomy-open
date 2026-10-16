import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { backendClient } from '../../plugins/hoomy/bridge.mjs';
import { createRemoteBridge, remoteToolNames } from '../../plugins/hoomy/remote-bridge.mjs';
import { mcpServer } from '../../plugins/hoomy/server.mjs';

const readScope = 'projects:read';
const writeScope = 'projects:context:write';
const refreshScope = 'projects:state:refresh';
const executeScope = 'projects:state:execute';
const requiredScopes = name => name === 'hoomy_update_project' ? [readScope, writeScope]
  : name === 'hoomy_refresh_project_state' ? [readScope, refreshScope]
    : name === 'hoomy_project_state_worker' ? [readScope, executeScope] : [readScope];

function authError(status, code) {
  return Object.assign(new Error(status === 401 ? 'Connecte ton compte au service central Hoomy pour continuer.' : 'Cette connexion au service central Hoomy ne dispose pas des permissions nécessaires.'), { status, code });
}

export function mcpResourceMetadata(resourceUrl, authorizationServerUrl) {
  return { resource: resourceUrl, authorization_servers: [authorizationServerUrl], scopes_supported: [readScope, writeScope, refreshScope, executeScope, 'offline_access'] };
}

export function createRemoteMcpHandler({ authenticateRequest, backendUrl, resourceUrl, authorizationServerUrl, onProjectUpdated, stateProjectId = '' }) {
  const origin = new URL(resourceUrl).origin;
  const metadataUrl = `${origin}/.well-known/oauth-protected-resource/mcp`;
  const api = backendClient(backendUrl);
  const challenge = error => `Bearer resource_metadata="${metadataUrl}", scope="${[readScope, writeScope, refreshScope, executeScope].join(' ')}", error="${error.status === 403 ? 'insufficient_scope' : 'invalid_token'}", error_description="Central Hoomy authorization required"`;

  return async function remoteMcp(req, res) {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    let server;
    try {
      // Revalidate every request; no session ID is an authorization credential.
      const principal = await authenticateRequest(req, { resource: resourceUrl, scopes: [readScope] });
      if (!principal || principal.device_id || !principal.account_id || !principal.workspace_id || principal.audience !== resourceUrl) {
        throw authError(401, 'invalid_token');
      }
      const authorize = name => {
        if (!requiredScopes(name).every(scope => principal.scopes?.includes(scope))) throw authError(403, 'insufficient_scope');
      };
      authorize('hoomy_home');
      if (req.headers.origin && req.headers.origin !== origin) throw authError(403, 'origin_not_allowed');
      const bridge = createRemoteBridge(api, { principal, resourceUrl, stateProjectId });
      const requestBridge = onProjectUpdated ? {
        ...bridge,
        async updateProject(args) {
          const result = await bridge.updateProject(args);
          try {
            await onProjectUpdated({
              principal, project_id: args.project_id, previous_revision: args.revision,
              revision: result.revision, field_names: Object.keys(args.fields).sort(),
            });
            return result;
          } catch {
            // The backend committed already; report success without inviting a retry.
            return { ...result, warnings: [{ code: 'audit_unavailable', message: 'Ta modification est enregistrée. L’enregistrement de son auteur dans l’historique n’a pas pu être confirmé.' }] };
          }
        },
      } : bridge;
      server = await mcpServer(requestBridge, {
        toolNames: remoteToolNames, authorize, challenge,
        securitySchemes: name => [{ type: 'oauth2', scopes: requiredScopes(name) }],
      });
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
      res.once('close', () => { void server.close(); });
      await server.connect(transport);
      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      if (server) await server.close();
      if (res.headersSent) { res.end(); return; }
      const status = [401, 403].includes(error.status) ? error.status : 500;
      if (status === 401 || error.code === 'insufficient_scope') res.setHeader('WWW-Authenticate', challenge({ status }));
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: status === 500 ? 'mcp_unavailable' : error.code || 'unauthorized' }));
    }
  };
}
