const http = require('http');
const vm = require('vm');

let actionFunction = null;
let actionMain = 'main';

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  if (req.method === 'POST' && req.url === '/init') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const payload = JSON.parse(body);
        const value = payload.value || {};

        let code = value.code;
        const main = value.main || 'main';
        const binary = value.binary || false;
        const env = value.env || {};

        if (!code) {
          res.writeHead(502, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing main/no code to execute' }));
          return;
        }

        if (binary) {
          code = Buffer.from(code, 'base64').toString('utf-8');
        }

        for (const key in env) {
          process.env[key] = env[key];
        }

        try {
          const context = {
            console,
            require,
            Buffer,
            process,
            setTimeout,
            setInterval,
            clearTimeout,
            clearInterval,
            __dirname,
            __filename,
            exports: {},
            module: { exports: {} }
          };

          vm.createContext(context);
          vm.runInContext(code, context);

          const exported = context.module.exports;
          if (typeof exported === 'function') {
            actionFunction = exported;
          } else if (typeof exported[main] === 'function') {
            actionFunction = exported[main];
          } else if (typeof context[main] === 'function') {
            actionFunction = context[main];
          } else {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: `Unable to load function ${main}` }));
            return;
          }

          actionMain = main;
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true }));
        } catch (e) {
          res.writeHead(502, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: `Failed to initialize action: ${e.message}` }));
        }
      } catch (e) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: `Error parsing init payload: ${e.message}` }));
      }
    });
    return;
  }

  if (req.method === 'POST' && req.url === '/run') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        if (!actionFunction) {
          res.writeHead(502, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Action not initialized' }));
          return;
        }

        const payload = JSON.parse(body);
        const params = payload.value || {};

        const owEnv = {
          '__OW_API_HOST': payload.api_host,
          '__OW_API_KEY': payload.api_key,
          '__OW_NAMESPACE': payload.namespace,
          '__OW_ACTION_NAME': payload.action_name,
          '__OW_ACTION_VERSION': payload.action_version,
          '__OW_ACTIVATION_ID': payload.activation_id,
          '__OW_DEADLINE': payload.deadline
        };

        for (const key in owEnv) {
          if (owEnv[key]) {
            process.env[key] = String(owEnv[key]);
          }
        }

        try {
          let result = actionFunction(params);

          if (result && typeof result.then === 'function') {
            result = await result;
          }

          console.log('XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX');

          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify(result || {}));
        } catch (e) {
          console.log('XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX');

          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: e.message }));
        }
      } catch (e) {
        console.log('XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX');

        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: `Error processing run request: ${e.message}` }));
      }
    });
    return;
  }

  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not found' }));
});

const PORT = process.env.PORT || 8080;
server.listen(PORT, () => {
  console.log(`PenguinWhisk Node.js 20 runtime listening on port ${PORT}`);
});
