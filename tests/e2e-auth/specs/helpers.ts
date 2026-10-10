import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Socket } from 'node:net';
import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { connect as tlsConnect } from 'node:tls';
import { parse } from 'yaml';

export const composeDir = process.env.COMPOSE_DIR ?? join(__dirname, '..', '..', '..');
export const httpBase = process.env.GATEWAY_HTTP ?? 'http://localhost';
/** The OpenELIS API server (:8443). */
export const oeBase = process.env.GATEWAY_OE_URL ?? 'https://localhost:8443';

/** Route contract (proxy/routes.yaml). */
export function contractRoutes(): Array<Record<string, string>> {
  return parse(readFileSync(join(composeDir, 'proxy', 'routes.yaml'), 'utf8')).routes;
}

/** Run a command inside a compose service; returns stdout. Throws when the stack is not up. */
export function dockerExec(service: string, cmd: string[]): string {
  return execFileSync('docker', ['compose', 'exec', '-T', service, ...cmd], {
    cwd: composeDir,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

/** True when a TCP connection to host:port succeeds within timeoutMs. */
export function tcpOpen(host: string, port: number, timeoutMs = 1500): Promise<boolean> {
  return new Promise((resolve) => {
    const s = new Socket();
    const done = (open: boolean) => { s.destroy(); resolve(open); };
    s.setTimeout(timeoutMs);
    s.once('connect', () => done(true));
    s.once('timeout', () => done(false));
    s.once('error', () => done(false));
    s.connect(port, host);
  });
}

export function basicAuth(user: string, password: string): string {
  return 'Basic ' + Buffer.from(`${user}:${password}`).toString('base64');
}

/**
 * GET with the path sent byte-for-byte. Browser-style URL parsing (Playwright's request API) resolves
 * `..`, decodes `%2e`, and reads a leading `//` as a host, so path-trick tests need this instead.
 */
export function rawGet(base: string, rawPath: string, host?: string): Promise<number> {
  return rawResponse(base, rawPath, host).then((r) => r.status);
}

/** Status + Location of a raw GET (no URL normalisation), optionally with a forged Host header. */
export function rawResponse(base: string, rawPath: string, host?: string): Promise<{ status: number; location: string }> {
  const u = new URL(base);
  const request = u.protocol === 'http:' ? httpRequest : httpsRequest;
  return new Promise((resolve, reject) => {
    const req = request({ host: u.hostname, port: u.port || (u.protocol === 'http:' ? 80 : 443), method: 'GET', path: rawPath,
      headers: { Host: host ?? u.host }, rejectUnauthorized: false },
    (res) => { res.resume(); resolve({ status: res.statusCode ?? 0, location: String(res.headers.location ?? '') }); });
    req.on('error', reject);
    req.end();
  });
}

/** True when a TLS handshake with these client options succeeds (certificate not verified). */
export function tlsHandshake(base: string, opts: object): Promise<boolean> {
  const u = new URL(base);
  return new Promise((resolve) => {
    const s = tlsConnect({ host: u.hostname, port: Number(u.port || 443), servername: u.hostname,
      rejectUnauthorized: false, ...opts }, () => { s.end(); resolve(true); });
    s.setTimeout(3000, () => { s.destroy(); resolve(false); });
    s.on('error', () => resolve(false));
  });
}
