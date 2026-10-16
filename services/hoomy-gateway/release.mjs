import express from 'express';
import { constants } from 'node:fs';
import { readFile, lstat, open, realpath } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';

const VERSION = '(?:0|[1-9][0-9]{0,8})\\.(?:0|[1-9][0-9]{0,8})\\.(?:0|[1-9][0-9]{0,8})';
const versionPattern = new RegExp(`^${VERSION}$`);
const artifactPattern = new RegExp(`^/releases/(${VERSION})/hoomy-agent-\\1\\.tar\\.gz(\\.sig)?$`);
const sameFile = (left, right) => left?.dev === right?.dev && left?.ino === right?.ino;

// New signed pairs become reachable without restarting the OAuth server.
export async function releaseRouter(directory) {
  const configuredRoot = resolve(directory);
  if (!(await lstat(configuredRoot)).isDirectory()) throw new Error('Use a regular release directory.');
  const root = await realpath(configuredRoot);
  const rootFolders = [root];
  while (rootFolders[0] !== dirname(rootFolders[0])) rootFolders.unshift(dirname(rootFolders[0]));
  const manifest = JSON.parse(await readFile(resolve(root, 'manifest.json'), 'utf8'));
  if (!versionPattern.test(manifest.version) || !/^[a-f0-9]{64}$/.test(manifest.sha256)) throw new Error('Invalid release manifest.');
  const archive = `/releases/${manifest.version}/hoomy-agent-${manifest.version}.tar.gz`;
  if (manifest.archive !== archive || manifest.signature !== `${archive}.sig` || manifest.collection_available !== false) throw new Error('Unsupported release manifest.');
  const installer = await readFile(resolve(root, 'install.sh'), 'utf8');
  if (installer.includes('__PUBLIC_KEY__') || installer.includes('__AGENT_SHA256__')) throw new Error('Release must be signed before publication.');
  const router = express.Router();
  const releases = resolve(root, 'releases');
  async function snapshot(paths, directories = false) {
    const stats = await Promise.all(paths.map(path => lstat(path).catch(() => undefined)));
    return stats.every(stat => directories ? stat?.isDirectory() : stat?.isFile()) ? stats : undefined;
  }
  function serve(fileForRequest, immutable = false) {
    return async (req, res, next) => {
      let handle;
      try {
        const file = fileForRequest(req);
        const folders = immutable ? [...rootFolders, releases, resolve(root,'releases',req.params[0])] : rootFolders;
        const paths = immutable ? [file.replace(/\.sig$/, ''), file.replace(/\.sig$/, '') + '.sig'] : [file];
        const beforeFolders = await snapshot(folders, true);
        if (!beforeFolders) return res.sendStatus(404);
        const beforeFiles = await snapshot(paths);
        if (!beforeFiles) return res.sendStatus(404);
        // Open the allowed leaf without following later symlink replacements.
        handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
        const stat = await handle.stat();
        const afterFolders = await snapshot(folders, true), afterFiles = await snapshot(paths);
        const index = file.endsWith('.sig') ? 1 : 0;
        if (!stat.isFile() || !afterFolders || !afterFiles || !sameFile(stat,beforeFiles[index]) || beforeFolders.some((before,i) => !sameFile(before,afterFolders[i])) || beforeFiles.some((before,i) => !sameFile(before,afterFiles[i]))) { await handle.close(); return res.sendStatus(404); }
        res.set({ 'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-store', 'X-Content-Type-Options':'nosniff', 'Content-Length':String(stat.size) });
        res.type(file.endsWith('.json') ? 'application/json' : file.endsWith('.sh') ? 'text/x-shellscript' : file.endsWith('.gz') ? 'application/gzip' : 'application/octet-stream');
        const stream = handle.createReadStream();
        res.once('close', () => stream.destroy());
        stream.once('error', error => { if (res.headersSent) res.destroy(); else next(error); });
        stream.pipe(res);
      } catch (error) {
        await handle?.close();
        if (['ENOENT','ELOOP','ENOTDIR','EACCES','EPERM'].includes(error.code)) return res.sendStatus(404);
        next(error);
      }
    };
  }
  router.get('/install.sh', serve(() => resolve(root,'install.sh')));
  router.get('/manifest.json', serve(() => resolve(root,'manifest.json')));
  router.get(artifactPattern, serve(req => resolve(root,'releases',req.params[0],`hoomy-agent-${req.params[0]}.tar.gz${req.params[1] || ''}`), true));
  return router;
}
