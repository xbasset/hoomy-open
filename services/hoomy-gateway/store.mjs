import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, chmodSync, realpathSync, existsSync, lstatSync } from 'node:fs';
import { dirname, resolve, join } from 'node:path';
import { createHash, randomBytes } from 'node:crypto';

export const secret = () => randomBytes(32).toString('base64url');
export const digest = value => createHash('sha256').update(value).digest('hex');

export class Store {
  constructor(path) {
    const dir = dirname(resolve(path));
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    let parent = realpathSync(dir);
    while (parent !== dirname(parent)) {
      if (existsSync(join(parent, '.git'))) throw new Error('Gateway state must be outside a Git checkout.');
      parent = dirname(parent);
    }
    let file;
    try { file = lstatSync(path); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (file && (!file.isFile() || file.isSymbolicLink())) throw new Error('Use a regular gateway database file, not a symlink.');
    this.db = new DatabaseSync(path);
    chmodSync(path, 0o600);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS oauth (model TEXT, id TEXT, payload TEXT NOT NULL, expires INTEGER, consumed INTEGER, grant_id TEXT, user_code TEXT, uid TEXT, PRIMARY KEY(model,id));
      CREATE INDEX IF NOT EXISTS oauth_grant ON oauth(grant_id);
      CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT NOT NULL, expires INTEGER, PRIMARY KEY(kind,id));`);
    this.db.exec('CREATE INDEX IF NOT EXISTS records_expiry ON records(expires); CREATE INDEX IF NOT EXISTS oauth_expiry ON oauth(expires);');
    this.cleanup();
    this.cleanupTimer = setInterval(() => this.cleanup(), 60000);
    this.cleanupTimer.unref();
  }
  get(kind, id) {
    const row = this.db.prepare('SELECT payload,expires FROM records WHERE kind=? AND id=?').get(kind, id);
    return row && (!row.expires || row.expires > Date.now()) ? JSON.parse(row.payload) : undefined;
  }
  put(kind, id, value, ttl) {
    this.db.prepare('DELETE FROM records WHERE expires IS NOT NULL AND expires<=?').run(Date.now());
    this.db.prepare('INSERT OR REPLACE INTO records VALUES(?,?,?,?)').run(kind, id, JSON.stringify(value), ttl ? Date.now() + ttl * 1000 : null);
  }
  remove(kind, id) { this.db.prepare('DELETE FROM records WHERE kind=? AND id=?').run(kind, id); }
  transaction(write) {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = write(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  list(kind) {
    return this.db.prepare('SELECT id,payload FROM records WHERE kind=? AND (expires IS NULL OR expires>?)').all(kind, Date.now()).map(row => ({ id: row.id, ...JSON.parse(row.payload) }));
  }
  take(kind, id) {
    this.db.exec('BEGIN IMMEDIATE');
    try { const value = this.get(kind, id); this.remove(kind, id); this.db.exec('COMMIT'); return value; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  cleanup() {
    this.db.prepare('DELETE FROM records WHERE expires IS NOT NULL AND expires<=?').run(Date.now());
    this.db.prepare('DELETE FROM oauth WHERE expires IS NOT NULL AND expires<=?').run(Date.now());
  }
  close() { clearInterval(this.cleanupTimer); this.db.close(); }
  adapter() {
    const db = this.db;
    return class Adapter {
      constructor(model) { this.model = model; }
      async upsert(id, payload, expiresIn) {
        db.prepare(`INSERT INTO oauth VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(model,id) DO UPDATE SET payload=excluded.payload,expires=excluded.expires,grant_id=excluded.grant_id,user_code=excluded.user_code,uid=excluded.uid`)
          .run(this.model, id, JSON.stringify(payload), expiresIn ? Date.now() + expiresIn * 1000 : null, payload.consumed ?? null, payload.grantId ?? null, payload.userCode ?? null, payload.uid ?? null);
      }
      read(row) {
        if (!row || (row.expires && row.expires <= Date.now())) return undefined;
        return { ...JSON.parse(row.payload), ...(row.consumed ? { consumed: row.consumed } : {}) };
      }
      async find(id) { return this.read(db.prepare('SELECT * FROM oauth WHERE model=? AND id=?').get(this.model, id)); }
      async findByUserCode(code) { return this.read(db.prepare('SELECT * FROM oauth WHERE model=? AND user_code=?').get(this.model, code)); }
      async findByUid(uid) { return this.read(db.prepare('SELECT * FROM oauth WHERE model=? AND uid=?').get(this.model, uid)); }
      async consume(id) { db.prepare('UPDATE oauth SET consumed=? WHERE model=? AND id=?').run(Math.floor(Date.now() / 1000), this.model, id); }
      async destroy(id) { db.prepare('DELETE FROM oauth WHERE model=? AND id=?').run(this.model, id); }
      async revokeByGrantId(id) { db.prepare('DELETE FROM oauth WHERE grant_id=?').run(id); }
    };
  }
}
