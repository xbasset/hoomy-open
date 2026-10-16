import { statSync } from 'node:fs';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const pending = new Set(['queued', 'running', 'preparing']);

export function backendClient(base = 'http://127.0.0.1:8765') {
  const url = new URL(base);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Le backend Hoomy doit rester local.');
  }
  return async (path, body) => {
    try {
      const response = await fetch(new URL(path, url), {
        method: body === undefined ? 'GET' : 'POST', redirect: 'error',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(12000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `Hoomy : ${response.status}`);
      return data;
    } catch (error) {
      if (error instanceof TypeError || error.name === 'TimeoutError') {
        throw new Error('Le service local Hoomy ne répond pas. Vérifie qu’il est démarré, puis réessaie.');
      }
      throw error;
    }
  };
}

// Source bodies and worker packets never cross into the presentation layer.
export function present(state, now, selectionId) {
  const projects = state.projects.filter(p => !p.dismissed && p.activity !== 'archived')
    .map(p => ({ id: p.id, name: p.name }));
  const job = selectionId ? now.selections.find(s => s.id === selectionId) : null;
  const selection = job ? {
    id: job.id, kind: job.kind, status: job.status, context: job.context,
    created_at: job.created_at, message: job.message, pending: pending.has(job.status),
    failure: job.failure || (job.status === 'failed' ? { code: 'worker_error' }
      : job.message === 'Decisions API est indisponible. Tes idées sont conservées ; réessaie.' ? { code: 'legacy_unavailable' } : null),
    question: job.kind === 'context_question' ? job.context_question : null,
    state_question: job.kind === 'question' && job.questions?.[0] ? {
      id: job.questions[0].id, version: job.questions[0].version, revision: job.questions[0].revision,
      project_name: job.questions[0].project_name,
      candidate: { state_question: job.questions[0].candidate.state_question, question_reason: job.questions[0].candidate.question_reason },
    } : null,
    report: job.research_report ? {
      summary: job.research_report.summary, unresolved: job.research_report.unresolved,
      sources: job.research_report.sources, checks: job.research_report.checks,
    } : null,
    activity: job.kind === 'activity' && job.activity ? {
      id: job.activity.id,
      candidate: job.activity.candidates.find(c => c.id === job.activity.selected_id),
      evidence: job.activity.uncertainty,
    } : null,
    progress: now.preparation?.id === job.preparation_id ? now.preparation : null,
  } : null;
  return {
    projects, selection,
    missing_selection: Boolean(selectionId && !job),
    can_search: state.control.scope_confirmed && state.control.inference_enabled && !state.control.running,
    last_selection: now.selections[0] ? { id: now.selections[0].id, instruction: now.selections[0].context.instruction } : null,
  };
}

export function createBridge(api = backendClient()) {
  async function read(selectionId) {
    const [state, now] = await Promise.all([api('/api/state'), api('/api/now')]);
    return { state, now, view: present(state, now, selectionId) };
  }
  async function job(id) {
    const data = await read(id);
    const selection = data.now.selections.find(s => s.id === id);
    if (!selection) throw new Error('Cette demande n’est plus disponible. Lance une nouvelle recherche.');
    return { ...data, job: selection };
  }
  return {
    async home({ selection_id } = {}) { return (await read(selection_id)).view; },
    async project({ project_id }) { return api('/api/projects/context/'+encodeURIComponent(project_id)); },
    async updateProject({ project_id, revision, fields }) {
      return api('/api/projects/context', { project_id, revision, fields });
    },
    async guide({ project_id }) {
      const profile=await api('/api/projects/context/'+encodeURIComponent(project_id));
      const brief=`$hoomy\n\nAide-moi à clarifier mes objectifs pour le projet ${profile.project.name} (identifiant : ${profile.project.id}). Lis sa fiche avec hoomy_project. Pose-moi une question à la fois, en t’appuyant sur mes réponses déjà enregistrées et sur les conversations accessibles. Distingue mes intentions des faits sur les activités. Propose une formulation concise puis demande mon accord avant de l’enregistrer avec hoomy_update_project. Ne lance aucune activité pendant cette clarification.`;
      const url=new URL('codex://threads/new');url.searchParams.set('prompt',brief);
      return { brief, url:url.href };
    },
    async choose({ instruction, attention_budget = 5, project_id, selection_id }) {
      const result = await api('/api/now/choose', {
        instruction, attention_budget, project_id: project_id || null,
        questions_answered: 0, recover: true,
        ...(selection_id ? { continuation_id: selection_id } : {}),
      });
      return (await read(result.id)).view;
    },
    async retry({ selection_id }) {
      const current = await job(selection_id);
      if (!current.view.selection.failure) throw new Error('Cette demande n’est pas en échec. Actualise son état.');
      const result = await api('/api/now/choose', {
        ...current.job.context, continuation_id: selection_id, recover: true,
      });
      return (await read(result.id)).view;
    },
    async answer({ selection_id, response, answer = '', idea_id, version, revision }) {
      const current = await job(selection_id);
      if (current.job.kind === 'context_question') {
        if (!['answer', 'unknown', 'skip'].includes(response) || (response === 'answer' && !answer.trim())) {
          throw new Error('Ajoute une réponse ou choisis de passer cette question.');
        }
        await api('/api/now/context-answer', { id: selection_id, response, answer });
        return (await read(selection_id)).view;
      }
      if (current.job.kind !== 'question') throw new Error('Cette question a changé. Actualise avant de répondre.');
      if (response === 'answer') throw new Error('Choisis une réponse sur l’état de cette activité.');
      const question = current.job.questions.find(q => q.id === idea_id);
      if (!question || question.version !== version || question.revision !== revision) {
        throw new Error('Cette question a changé. Actualise avant de répondre.');
      }
      await api('/api/now/answer', { ...current.job.context, id: idea_id, version, revision, answer: response });
      const next = await api('/api/now/choose', {
        ...current.job.context, questions_answered: Math.min(2, (current.job.context.questions_answered || 0) + 1),
        continuation_id: selection_id, recover: true,
      });
      return (await read(next.id)).view;
    },
    async cancel({ selection_id }) {
      const current = await job(selection_id);
      if (current.view.selection.progress) {
        await api('/api/act/cancel', { id: current.view.selection.progress.id });
      }
      return (await read(selection_id)).view;
    },
    async handoff({ selection_id }) {
      const current = await job(selection_id);
      const activity = current.view.selection.activity;
      if (!activity?.candidate) throw new Error('Cette activité n’est plus prête. Actualise son état avant de continuer.');
      const candidate = activity.candidate;
      const project = current.state.projects.find(p => p.id === candidate.project_id);
      if (!project || project.dismissed || project.activity === 'archived') throw new Error('Ce projet n’est plus disponible.');
      const records = await api('/api/act');
      const origin = records.requests.find(r => r.id === current.job.activity.origin_request_id);
      const refs = new Set(candidate.source_refs || []);
      const related = (origin?.catalog || []).filter(s => refs.has(s.session_id)
        && project.session_ids.includes(s.session_id) && uuid.test(s.session_id));
      const threads = related.slice(0, 3).map(s => ({ id: s.session_id, title: s.title,
        url: `codex://threads/${s.session_id}` }));
      const projectContext = (current.job.project_context || origin?.project_context || []).filter(c => c.project_id === project.id && c.response === 'answer');
      const brief = [
        '$hoomy',
        `Je choisis cette activité Hoomy : ${candidate.title}.`,
        `Projet : ${project.name}.`,
        `Ma demande : ${current.job.context.instruction}`,
        `Activité : ${candidate.action}`,
        ...projectContext.map(c => `Précision du ${c.answered_at} : ${c.answer}`),
        `État connu : ${activity.evidence}`,
        ...threads.map(t => `Conversation source : ${t.title} (${t.id})`),
        `Demande Hoomy : ${selection_id}. Relis son état avec hoomy_handoff avant de commencer.`,
        'Aide-moi à réaliser cette activité avec les ressources et outils disponibles dans Codex. Les éléments ci-dessus sont un brief daté à vérifier, pas une preuve que toutes les ressources sont accessibles.',
      ].join('\n\n');
      const url = new URL('codex://threads/new');
      url.searchParams.set('prompt', brief);
      const cwd = related.find(s => {
        try { return typeof s.cwd === 'string' && s.cwd.startsWith('/') && statSync(s.cwd).isDirectory(); }
        catch { return false; }
      })?.cwd;
      if (cwd) url.searchParams.set('path', cwd);
      return { selection_id, title: candidate.title, project: project.name, brief, new_url: url.href, threads };
    },
  };
}
