import { createGateway } from './gateway.mjs';
import { configFromEnv } from './config.mjs';

const config = configFromEnv();
config.trustedProxyIps = ['loopback', ...(process.env.HOOMY_TRUSTED_PROXY_IPS || '').split(',').filter(Boolean)];
const gateway = await createGateway(config);
const port = Number(process.env.HOOMY_GATEWAY_PORT || 8780);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid gateway port.');
const server = gateway.app.listen(port, '0.0.0.0', () => console.log('Hoomy authenticated gateway ready. Request content is not logged.'));
for (const signal of ['SIGTERM','SIGINT']) process.on(signal, () => server.close(() => { gateway.close(); process.exit(0); }));
