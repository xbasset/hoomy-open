import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink, unlink, rename } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import express from 'express';
import { releaseRouter } from '../release.mjs';

test('release routes expose only exact public files', async t => {
  const root = await mkdtemp(join(tmpdir(), 'hoomy-release-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const archive = '/releases/0.1.0/hoomy-agent-0.1.0.tar.gz';
  await mkdir(join(root, 'releases/0.1.0'), { recursive: true });
  await writeFile(join(root, 'manifest.json'), JSON.stringify({ version: '0.1.0', archive, signature: `${archive}.sig`, sha256: 'a'.repeat(64), collection_available: false }));
  await writeFile(join(root, 'install.sh'), '#!/bin/bash\necho signed-fixture\n');
  await writeFile(join(root, archive.slice(1)), 'archive-fixture');
  await writeFile(join(root, `${archive.slice(1)}.sig`), 'signature-fixture');
  await writeFile(join(root, 'private.pem'), 'must-not-be-served');
  const app = express();
  app.use(await releaseRouter(root));
  const server = app.listen(0, '127.0.0.1');
  await new Promise(resolve => server.once('listening', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const installer = await fetch(`${origin}/install.sh`);
  assert.equal(installer.status, 200);
  assert.equal(installer.headers.get('cache-control'), 'no-store');
  assert.match(await installer.text(), /signed-fixture/);
  const artifact = await fetch(`${origin}${archive}`);
  assert.equal(artifact.status, 200);
  assert.match(artifact.headers.get('cache-control'), /immutable/);
  for (const path of ['/private.pem', '/releases/', '/releases/0.1.0/other.tar.gz']) assert.equal((await fetch(origin + path)).status, 404);
  await writeFile(join(root, 'install.sh'), '__PUBLIC_KEY__');
  await assert.rejects(releaseRouter(root), /signed/);
  await writeFile(join(root, 'manifest.json'), JSON.stringify({ version: '../../secret', sha256: 'a'.repeat(64) }));
  await assert.rejects(releaseRouter(root), /manifest/);
});

test('new current manifests preserve older archives while excluding mismatched, private and symlinked files', async t => {
  const root = await mkdtemp(join(tmpdir(), 'hoomy-release-history-'));
  t.after(() => rm(root, { recursive:true, force:true }));
  const artifact = version => `/releases/${version}/hoomy-agent-${version}.tar.gz`;
  for (const version of ['0.1.3','0.1.4']) {
    await mkdir(join(root, 'releases', version), { recursive:true });
    await writeFile(join(root, artifact(version)), `archive-${version}`);
    await writeFile(join(root, `${artifact(version)}.sig`), `signature-${version}`);
  }
  const current = artifact('0.1.4');
  await writeFile(join(root, 'manifest.json'), JSON.stringify({ version:'0.1.4', archive:current, signature:`${current}.sig`, sha256:'b'.repeat(64), collection_available:false }));
  await writeFile(join(root, 'install.sh'), '#!/bin/sh\necho signed-current-fixture\n');
  await writeFile(join(root, 'private.pem'), 'private-content');
  await writeFile(join(root, 'releases/0.1.3/private.pem'), 'private-content');
  await mkdir(join(root, 'releases/0.2.0'));
  await writeFile(join(root, 'releases/0.2.0/hoomy-agent-0.1.4.tar.gz'), 'mismatched-content');
  await mkdir(join(root, 'private-directory'));
  await writeFile(join(root, 'private-directory/hoomy-agent-0.1.5.tar.gz'), 'private-content');
  await writeFile(join(root, 'private-directory/hoomy-agent-0.1.5.tar.gz.sig'), 'private-content');
  await symlink(join(root, 'private-directory'), join(root, 'releases/0.1.5'));
  await mkdir(join(root, 'releases/0.1.6'));
  await symlink(join(root, 'private.pem'), join(root, artifact('0.1.6')));
  await writeFile(join(root, `${artifact('0.1.6')}.sig`), 'signature-fixture');
  const app = express(); app.use(await releaseRouter(root));
  const server = app.listen(0, '127.0.0.1'); await new Promise(resolve => server.once('listening', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  assert.equal((await (await fetch(origin+'/manifest.json')).json()).version, '0.1.4');
  for (const version of ['0.1.3','0.1.4']) {
    const archive = await fetch(origin+artifact(version));
    assert.equal(archive.status, 200);
    assert.equal(await archive.text(), `archive-${version}`);
    assert.match(archive.headers.get('cache-control'), /immutable/);
    const signature = await fetch(origin+artifact(version)+'.sig');
    assert.equal(signature.status, 200);
    assert.equal(await signature.text(), `signature-${version}`);
  }
  for (const path of ['/private.pem','/releases/0.1.3/private.pem','/private-directory/hoomy-agent-0.1.5.tar.gz',artifact('0.1.5'),artifact('0.1.6'),'/releases/0.2.0/hoomy-agent-0.1.4.tar.gz','/releases/0.1.3/hoomy-agent-0.1.4.tar.gz','/releases/0.1.3/%2e%2e%2f%2e%2e%2fprivate.pem','/releases/0.1.3/../private.pem']) {
    assert.equal((await fetch(origin+path)).status, 404);
  }
  // A public leaf replaced after router creation must not expose its new target.
  await unlink(join(root, artifact('0.1.3')));
  await symlink(join(root, 'private.pem'), join(root, artifact('0.1.3')));
  assert.equal((await fetch(origin+artifact('0.1.3'))).status, 404);
  await unlink(join(root, 'install.sh'));
  await symlink(join(root, 'private.pem'), join(root, 'install.sh'));
  assert.equal((await fetch(origin+'/install.sh')).status, 404);
});

test('new exact archive pairs appear without restarting the router and incomplete or swapped paths stay private', async t => {
  const root = await mkdtemp(join(tmpdir(),'hoomy-release-dynamic-'));
  t.after(() => rm(root,{recursive:true,force:true}));
  const artifact = version => `/releases/${version}/hoomy-agent-${version}.tar.gz`;
  const stage = async version => {
    await mkdir(join(root,'releases',version),{recursive:true});
    await writeFile(join(root,artifact(version)),`archive-${version}`);
    await writeFile(join(root,artifact(version)+'.sig'),`signature-${version}`);
  };
  await stage('0.2.0');
  await writeFile(join(root,'manifest.json'),JSON.stringify({version:'0.2.0',archive:artifact('0.2.0'),signature:artifact('0.2.0')+'.sig',sha256:'c'.repeat(64),collection_available:false}));
  await writeFile(join(root,'install.sh'),'#!/bin/sh\necho signed-fixture\n');
  await writeFile(join(root,'secret.pem'),'private-fixture');
  const app = express(); app.use(await releaseRouter(root));
  const server = app.listen(0,'127.0.0.1'); await new Promise(resolve => server.once('listening',resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  assert.equal((await fetch(origin+artifact('0.2.1'))).status,404);
  await mkdir(join(root,'releases/0.2.1'));
  await writeFile(join(root,artifact('0.2.1')),'archive-0.2.1');
  assert.equal((await fetch(origin+artifact('0.2.1'))).status,404);
  assert.equal((await fetch(origin+artifact('0.2.1')+'.sig')).status,404);
  await writeFile(join(root,artifact('0.2.1')+'.sig'),'signature-0.2.1');
  for (const version of ['0.2.0','0.2.1']) {
    const archive = await fetch(origin+artifact(version)); assert.equal(archive.status,200); assert.equal(await archive.text(),`archive-${version}`);
    const signature = await fetch(origin+artifact(version)+'.sig'); assert.equal(signature.status,200); assert.equal(await signature.text(),`signature-${version}`);
  }
  for (const path of ['/releases/00.2.1/hoomy-agent-00.2.1.tar.gz','/releases/0.2.1-beta/hoomy-agent-0.2.1-beta.tar.gz','/releases/0.2.1/hoomy-agent-0.2.0.tar.gz','/releases/0.2.1/hoomy-agent-0.2.1.tar.gz.sig.extra','/releases/0.2.1/%2e%2e%2f%2e%2e%2fsecret.pem','/releases/0.2.1/secret.pem','/releases/0.2.1/hoomy-agent-0.2.1.tar.gz/']) assert.equal((await fetch(origin+path)).status,404);
  await unlink(join(root,artifact('0.2.1')+'.sig'));
  await symlink(join(root,'secret.pem'),join(root,artifact('0.2.1')+'.sig'));
  assert.equal((await fetch(origin+artifact('0.2.1'))).status,404); assert.equal((await fetch(origin+artifact('0.2.1')+'.sig')).status,404);
  await unlink(join(root,artifact('0.2.1')+'.sig')); await writeFile(join(root,artifact('0.2.1')+'.sig'),'signature-0.2.1');
  await rename(join(root,'releases/0.2.1'),join(root,'private-version'));
  await symlink(join(root,'private-version'),join(root,'releases/0.2.1'));
  assert.equal((await fetch(origin+artifact('0.2.1'))).status,404); assert.equal((await fetch(origin+artifact('0.2.1')+'.sig')).status,404);
  await unlink(join(root,'releases/0.2.1')); await rename(join(root,'private-version'),join(root,'releases/0.2.1'));
  await rename(join(root,'releases'),join(root,'private-releases'));
  await symlink(join(root,'private-releases'),join(root,'releases'));
  assert.equal((await fetch(origin+artifact('0.2.0'))).status,404); assert.equal((await fetch(origin+artifact('0.2.1'))).status,404);
  await unlink(join(root,'releases')); await rename(join(root,'private-releases'),join(root,'releases'));
  assert.equal((await fetch(origin+artifact('0.2.0'))).status,200); assert.equal((await fetch(origin+artifact('0.2.1'))).status,200);
});
