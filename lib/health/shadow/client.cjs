const http = require('node:http');
const { verifyMetadata, ShadowError } = require('./contract.cjs');

function createClient({ port, token, connectMs = 1200, totalMs = 15000 }) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535 ||
      typeof token !== 'string' || !/^[A-Za-z0-9_-]{43,128}$/.test(token)) throw new ShadowError('INVALID_CONFIG');
  function send(path, body, limit) {
    return new Promise((resolve, reject) => {
      const headers = { 'X-ULTRON-Service-Token': token, Accept: 'application/json' };
      if (body) Object.assign(headers, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
      const req = http.request({ host: '127.0.0.1', port, path, headers, method: body ? 'POST' : 'GET', agent: false },
        res => {
          if (res.statusCode !== 200) {
            const code = res.statusCode === 401 || res.statusCode === 403 ? 'AUTH_REJECTED' :
              res.statusCode >= 300 && res.statusCode < 400 ? 'REDIRECT_REJECTED' :
              res.statusCode >= 500 ? 'UNAVAILABLE' : 'CONTRACT_REJECTED';
            reject(new ShadowError(code));
            res.destroy();
            return;
          }
          if (res.headers['content-type']?.split(';')[0] !== 'application/json' ||
              Number(res.headers['content-length']) > limit) {
            reject(new ShadowError('INVALID_RESPONSE'));
            res.destroy();
            return;
          }
          let bytes = 0;
          const chunks = [];
          res.on('data', chunk => {
            bytes += chunk.length;
            if (bytes > limit) {
              reject(new ShadowError('INVALID_RESPONSE'));
              res.destroy();
            } else chunks.push(chunk);
          });
          res.on('error', () => reject(new ShadowError('INVALID_RESPONSE')));
          res.on('end', () => {
            try {
              if (!res.complete) throw new Error();
              resolve(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks))));
            } catch { reject(new ShadowError('INVALID_RESPONSE')); }
          });
        });
      const total = setTimeout(() => req.destroy(new ShadowError('TIMEOUT')), totalMs);
      const connect = setTimeout(() => req.destroy(new ShadowError('TIMEOUT')), connectMs);
      req.on('socket', socket => socket.once('connect', () => clearTimeout(connect)));
      req.on('close', () => { clearTimeout(total); clearTimeout(connect); });
      req.on('error', error => reject(new ShadowError(error instanceof ShadowError ? error.code : 'UNAVAILABLE')));
      req.end(body);
    });
  }
  return {
    async infer(body) {
      verifyMetadata(await send('/v1/metadata', null, 8192));
      return send('/v1/infer', body, 5 * 1024 * 1024);
    }
  };
}

module.exports = { createClient };
