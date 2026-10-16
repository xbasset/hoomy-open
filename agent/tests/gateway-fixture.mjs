// Isolated HTTPS gateway; identity and pilot evidence never leave this fixture.
import { createServer } from 'node:https';
import { createServer as reserveServer } from 'node:net';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createGateway } from '../../services/hoomy-gateway/gateway.mjs';

const directory = process.argv[2];
const reservation = reserveServer();
await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
const port = reservation.address().port;
await new Promise(resolve => reservation.close(resolve));
const origin = `https://localhost:${port}`;
const backendUrl = process.argv[3];
const options = { githubIdentity: async () => ({ id: 42, login: 'fixture-user' }) };
if (!backendUrl) options.pilot = async () => ({ project: { id: 'fixture-project' }, revision: 'fixture-revision' });
const gateway = await createGateway({
  testMode: true, origin, backendUrl: backendUrl || 'http://127.0.0.1:8765',
  ingestionProjectId: process.argv[4] || '',
  databasePath: join(directory, 'auth.sqlite'),
  githubClientId: 'fixture-app', githubClientSecret: 'fixture-only-secret',
  allowedGithubIds: ['42'], cookieKeys: ['f'.repeat(32)],
}, options);
const server = createServer({ key: readFileSync(join(directory, 'key.pem')), cert: readFileSync(join(directory, 'cert.pem')) }, gateway.app);
await new Promise(resolve => server.listen(port, '127.0.0.1', resolve));
process.stdout.write(JSON.stringify({ origin }) + '\n');
async function stop() {
  await new Promise(resolve => server.close(resolve));
  gateway.close();
  process.exit(0);
}
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
