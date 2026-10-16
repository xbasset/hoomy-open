import express from 'express';
import { HttpError } from './backend-client.mjs';
import { strictJson, validatePolicy, validateEvent, verifiedReceipt } from './ingestion.mjs';
import { digest } from './store.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const deny = (ok, status, code, message) => { if (!ok) throw new HttpError(status, code, message); };

export function mountIngestion({ app, config, store, resources, authenticateRequest, protectedRoute, pilot, collectionPolicy }) {
  function approved(principal) {
    const device = store.get('device', principal.device_id);
    deny(device && device.grant_id === principal.grant_id && collectionPolicy(device, principal), 403, 'collection_unavailable', 'This machine has no current approved collection policy.');
    validatePolicy(device.policy, config.ingestionProjectId);
    return { id:principal.device_id, revision:device.policy_revision, ...device.policy };
  }
  let inFlight = 0;
  const authenticateUpload = async (req, res, next) => {
    const principal = await authenticateRequest(req, { resource:resources.collector, scopes:['evidence:ingest'] });
    approved(principal);
    const key = digest(`upload:${principal.device_id}:${Math.floor(Date.now() / 60000)}`);
    const count = (store.get('quota', key)?.count || 0) + 1;
    store.put('quota', key, { count }, 120);
    if (count > 20 || inFlight >= 2) return res.set('Retry-After', '60').status(429).json({ error:'rate_limited' });
    inFlight++;
    let released = false;
    res.once('close', () => { if (!released) { released = true; inFlight--; } });
    req.setTimeout(30000);
    next();
  };
  const eventBody = express.raw({ type:'application/json', limit:24 * 1024 * 1024, inflate:false });
  app.post('/v1/events', authenticateUpload, eventBody, protectedRoute(resources.collector, ['evidence:ingest'], async (req, res, principal) => {
    deny(Buffer.isBuffer(req.body), 415, 'json_required', 'Use an uncompressed application/json event.');
    const event = strictJson(req.body);
    const policy = approved(principal);
    const { eventDigest } = validateEvent(event, policy, config.ingestionProjectId, req.get('Idempotency-Key'));
    const receipt = await pilot('/api/ingestion/events', { principal:{account_id:principal.account_id, workspace_id:principal.workspace_id, device_id:principal.device_id}, policy, event });
    res.status(202).json(verifiedReceipt(receipt, event, eventDigest));
  }));
  app.get('/v1/receipts/:id', protectedRoute(resources.collector, ['evidence:ingest'], async (req, res, principal) => {
    approved(principal);
    deny(UUID.test(req.params.id) && !Object.keys(req.query).length, 400, 'invalid_receipt', 'Provide a receipt UUID.');
    const receipt = await pilot('/api/ingestion/receipts/query', { principal:{account_id:principal.account_id, workspace_id:principal.workspace_id, device_id:principal.device_id}, event_id:req.params.id });
    deny(receipt?.receipt_id === req.params.id && receipt.event_id === req.params.id && receipt.project_id === config.ingestionProjectId && receipt.state === 'stored' && receipt.inference_state === 'not_requested', 502, 'receipt_unconfirmed', 'The central service did not confirm this receipt.');
    const event = {event_id:req.params.id,project_id:config.ingestionProjectId};
    if (receipt.event_type === 'conversation.source_excluded') {
      event.type = receipt.event_type;
      event.exclusion = {header_sha256:receipt.header_sha256};
    } else {
      event.projected_sha256 = receipt.projected_sha256;
    }
    res.json(verifiedReceipt(receipt,event,receipt.event_sha256));
  }));
  app.get('/v1/projects/:id/evidence', protectedRoute(resources.projects, ['projects:read'], async (req, res, principal) => {
    deny(Object.keys(req.query).every(key => ['source_id','offset','limit'].includes(key)) && typeof req.query.source_id === 'string' && /^remote:[0-9a-f]{64}$/.test(req.query.source_id), 400, 'invalid_evidence_query', 'Provide a cached source ID and bounded page.');
    const number = (value, fallback) => value === undefined ? fallback : typeof value === 'string' && /^(?:0|[1-9][0-9]*)$/.test(value) ? Number(value) : NaN;
    const offset = number(req.query.offset, 0), limit = number(req.query.limit, 20);
    deny(Number.isSafeInteger(offset) && offset >= 0 && offset <= 10000 && Number.isSafeInteger(limit) && limit >= 1 && limit <= 50, 400, 'invalid_evidence_query', 'Use a bounded integer offset and a limit between 1 and 50.');
    const result = await pilot('/api/ingestion/evidence/query', { principal:{account_id:principal.account_id, workspace_id:principal.workspace_id}, project_id:req.params.id, source_id:req.query.source_id, offset, limit });
    deny(result?.project_id === req.params.id && result.source_id === req.query.source_id && Array.isArray(result.messages) && result.messages.length <= limit, 502, 'evidence_unconfirmed', 'The cached evidence response is unavailable.');
    res.json(result);
  }));
}
