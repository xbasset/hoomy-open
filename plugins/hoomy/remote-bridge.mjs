import { createBridge } from './bridge.mjs';

export const remoteToolNames = ['hoomy_home', 'hoomy_project', 'hoomy_update_project', 'hoomy_project_guide', 'hoomy_project_evidence',
  'hoomy_project_state', 'hoomy_refresh_project_state', 'hoomy_project_state_job', 'hoomy_project_state_citation', 'hoomy_project_state_worker'];

// Central paths and conversation IDs describe provenance, not this client's disk.
export function createRemoteBridge(api, { principal, resourceUrl, stateProjectId = '' } = {}) {
  const backend = api;
  api = async (...args) => {
    try { return await backend(...args); }
    catch (error) {
      if (error.message?.startsWith('Le service local Hoomy ne répond pas')) {
        throw new Error('Le service central Hoomy ne répond pas. Vérifie ta connexion, puis réessaie.');
      }
      throw error;
    }
  };
  const bridge = createBridge(api);
  const connection = resourceUrl ? { mode: 'central', origin: new URL(resourceUrl).origin, resource: resourceUrl } : undefined;
  const identity = () => {
    if (!principal?.account_id || !principal.workspace_id || principal.device_id) throw new Error('Une connexion utilisateur au service central Hoomy est nécessaire.');
    return { account_id: principal.account_id, workspace_id: principal.workspace_id };
  };
  const state = async (operation, args) => {
    if (!['read', 'job', 'citation'].includes(operation) && (!stateProjectId || args.project_id !== stateProjectId)) {
      throw Object.assign(new Error('La synthèse du service central Hoomy n’est pas activée pour ce projet.'), { status: 403, code: 'state_unavailable' });
    }
    let result;
    try { result = await api('/api/project-state/' + operation, { principal: identity(), ...args }); }
    catch(error){if(error.message?.includes('service central Hoomy'))throw error;throw Object.assign(new Error('Le service central Hoomy n’a pas pu terminer cette demande. '+error.message),{status:error.status,code:error.code});}
    return { ...result, connection, ...(operation === 'read' ? { enabled: Boolean(stateProjectId && args.project_id === stateProjectId && result.enabled) } : {}) };
  };
  return {
    async home() {
      const state = await api('/api/projects');
      return {
        projects: state.projects.filter(p => !p.dismissed && p.activity !== 'archived').map(p => ({ id: p.id, name: p.name })),
        selection: null, missing_selection: false, last_selection: null, can_search: false,
        connection,
        capabilities: { project_context: true, research: false, source_chat_navigation: false, cached_project_evidence: true },
      };
    },
    async project(args) {
      const profile = await bridge.project(args);
      return { ...profile, connection, capabilities: { source_chat_navigation: false, cached_project_evidence: true, project_state: Boolean(stateProjectId && args.project_id === stateProjectId) } };
    },
    async updateProject(args) {
      const profile = await bridge.updateProject(args);
      return { ...profile, connection, capabilities: { source_chat_navigation: false, cached_project_evidence: true, project_state: Boolean(stateProjectId && args.project_id === stateProjectId) } };
    },
    projectState: args => state('read', args),
    refreshProjectState: args => state('refresh', args),
    projectStateJob: args => state('job', args),
    projectStateCitation: args => state('citation', args),
    projectStateWorker: ({ action, ...args }) => state(action, args),
    async evidence(args) {
      if (!principal?.account_id || !principal.workspace_id || principal.device_id) {
        throw new Error('Une connexion utilisateur Hoomy est nécessaire pour lire les preuves du projet.');
      }
      return api('/api/ingestion/evidence/query', {
        principal: { account_id: principal.account_id, workspace_id: principal.workspace_id },
        project_id: args.project_id, source_id: args.source_id, offset: args.offset ?? 0, limit: args.limit ?? 20,
      });
    },
    guide: bridge.guide,
  };
}
