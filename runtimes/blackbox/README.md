# Blackbox Runtime for OpenWhisk

The blackbox runtime enables you to create custom OpenWhisk runtimes for any language, framework, or binary. This allows you to extend OpenWhisk beyond the native runtimes and execute code in languages that aren't officially supported.

## Overview

A blackbox/Docker action is an OpenWhisk action that runs within a custom Docker container. Instead of using one of the built-in runtimes (Node.js, Python, Java, Go), you provide a Docker image that implements the OpenWhisk action protocol.

### When to Use Blackbox Runtimes

- Custom languages not supported by native OpenWhisk runtimes
- Proprietary or legacy language support (COBOL, Fortran, etc.)
- Complex dependency requirements
- Native binaries or compiled executables
- Custom frameworks or specialized libraries
- Multi-language polyglot applications
- Specific performance optimization needs

## Requirements

A blackbox runtime must be a Docker container that meets these requirements:

1. **Port Exposure**: Must expose port 8080 on the container
2. **HTTP Server**: Must implement a minimal HTTP server
3. **Endpoints**: Must implement POST /init and POST /run endpoints
4. **Health Check**: Should implement GET /health (optional but recommended)
5. **Log Framing**: Must output activation markers to proper log streams

## HTTP Endpoints

### POST /init

Initialize the action code. Called once at action deployment time to compile/load the code.

**Endpoint Details:**
- **Method**: POST
- **Content-Type**: application/json
- **Timeout**: Default 30 seconds

**Request Body:**
```json
{
  "code": "string containing the action code",
  "binary": false,
  "main": "function_name"
}
```

**Request Fields:**
- `code` (required): The action code as a string or base64-encoded string for binary actions
- `binary` (optional): Boolean flag; if true, code is base64-encoded
- `main` (optional): Name of the entry point function/handler

**Success Response (HTTP 200):**
```json
{
  "ok": true
}
```

**Failure Response (HTTP 502 Bad Gateway):**
```json
{
  "error": "Error message describing what went wrong",
  "errorCode": "COMPILATION_ERROR"
}
```

**Common Error Scenarios:**
- Syntax errors in supplied code
- Missing dependencies or compilation failures
- Invalid code format or encoding
- Resource exhaustion (memory, disk space)

### POST /run

Execute the initialized action with the provided parameters. Called once per action invocation.

**Endpoint Details:**
- **Method**: POST
- **Content-Type**: application/json
- **Timeout**: Default 600 seconds (action-configurable)

**Request Body:**
```json
{
  "param1": "value1",
  "param2": "value2",
  "__ow_method": "post",
  "__ow_headers": {
    "host": "openwhisk.example.com",
    "user-agent": "OpenWhisk"
  },
  "__ow_path": "/api/endpoint",
  "__ow_query": "key=value&foo=bar",
  "__ow_body": "base64-encoded request body"
}
```

**Request Fields:**
- Named parameters: User-defined action parameters as key-value pairs
- `__ow_*` fields: Metadata about the HTTP request (if action was triggered via HTTP)

**Success Response (HTTP 200):**
```json
{
  "statusCode": 200,
  "headers": {
    "content-type": "application/json"
  },
  "body": {
    "result_key": "result_value",
    "status": "success"
  }
}
```

Or return raw JSON (simplified response):
```json
{
  "result_key": "result_value",
  "status": "success"
}
```

**Error Response (HTTP 502 Bad Gateway):**
```json
{
  "error": "Error message describing execution failure",
  "errorCode": "EXECUTION_ERROR"
}
```

**Response Status Codes:**
- **200 OK**: Action executed successfully
- **400 Bad Request**: Malformed request (invalid JSON, missing required fields)
- **502 Bad Gateway**: Action execution failed (unhandled exception, runtime error)
- **504 Gateway Timeout**: Action exceeded configured timeout
- **500 Internal Server Error**: Container or framework error

### GET /health

Recommended endpoint for health checks. Called by OpenWhisk to verify the container is ready.

**Response (HTTP 200):**
```json
{
  "ok": true,
  "status": "ready"
}
```

## Environment Variables

The following environment variables are injected into the blackbox container at runtime:

**Activation Metadata:**
- `__OW_ACTIVATION_ID`: Unique identifier for this action invocation (36-character UUID)
- `__OW_ACTION_NAME`: Full action name including namespace and package (e.g., `/namespace/package/action`)
- `__OW_NAMESPACE`: OpenWhisk namespace of the invoker

**API Configuration:**
- `__OW_API_KEY`: Authentication key for internal OpenWhisk API calls
- `__OW_API_HOST`: OpenWhisk API endpoint URL

**Memory & Limits:**
- `__OW_ACTION_MEMORY`: Memory limit in MB for this action (e.g., "256")
- `__OW_ACTION_TIMEOUT`: Timeout limit in milliseconds (e.g., "600000")

**Runtime Configuration:**
- `__OW_RUNTIME`: Name of the runtime (e.g., "docker")
- `__OW_LOG_LEVEL`: Logging level (e.g., "info", "debug")

**Example Usage:**
```bash
echo "Action: $__OW_ACTION_NAME"
echo "Activation ID: $__OW_ACTIVATION_ID"
echo "Memory Limit: $__OW_ACTION_MEMORY MB"
```

## Log Framing

To properly delimit activation logs and separate them from action output, the container must print the log marker to both stdout and stderr:

**Marker String:**
```
XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX
```

**When to Print:**
- Print to **stdout** at the end of `/init` endpoint execution
- Print to **stderr** at the end of both `/init` and `/run` endpoint execution
- Ensures OpenWhisk properly captures and separates logs

**Example Implementation (Bash):**
```bash
#!/bin/bash

echo "Action initialized" >&1
echo "XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX" >&1
echo "XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX" >&2
```

## Example: Dockerfile Template

```dockerfile
FROM debian:bookworm-slim

WORKDIR /app

# Install runtime (Node.js, Python, Ruby, etc.)
RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Copy action handler/proxy code
COPY proxy.js .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Start the proxy server
CMD ["node", "proxy.js"]
```

## Example: Minimal Bash Shell Proxy

```bash
#!/bin/bash

# /app/proxy.sh - Minimal OpenWhisk blackbox proxy

PORT=8080
CODE=""
MAIN="main"

# Function to run the action code
run_action() {
    local params="$1"

    # Create a temporary bash script from stored code
    local script=$(mktemp)
    echo "$CODE" > "$script"
    chmod +x "$script"

    # Execute and capture output
    local output
    output=$("$script" "$params" 2>&1)
    local exit_code=$?

    rm -f "$script"

    echo "XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX" >&2

    if [ $exit_code -eq 0 ]; then
        echo "$output"
        echo '{"status":"ok"}'
    else
        echo "$output" >&2
        echo '{"error":"Execution failed"}' | jq .
    fi
}

# Simple HTTP server using nc (netcat)
handle_request() {
    local request="$1"

    if [[ "$request" == *"POST /init"* ]]; then
        local code_body=$(echo "$request" | tail -1)
        CODE=$(echo "$code_body" | jq -r '.code')
        echo -ne "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}"
        echo "XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX" >&2
    elif [[ "$request" == *"POST /run"* ]]; then
        local params=$(echo "$request" | tail -1)
        local result=$(run_action "$params")
        echo -ne "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n$result"
    elif [[ "$request" == *"GET /health"* ]]; then
        echo -ne "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}"
    else
        echo -ne "HTTP/1.1 404 Not Found\r\n\r\n"
    fi
}

# Start server
while true; do
    handle_request "$(nc -l -p $PORT -q 1)"
done
```

## Example: Node.js Proxy

```javascript
// proxy.js - OpenWhisk blackbox proxy in Node.js

const http = require('http');
const { v4: uuidv4 } = require('uuid');

let actionCode = '';
let initError = null;

const server = http.createServer(async (req, res) => {
  if (req.method === 'POST' && req.url === '/init') {
    handleInit(req, res);
  } else if (req.method === 'POST' && req.url === '/run') {
    handleRun(req, res);
  } else if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
  } else {
    res.writeHead(404);
    res.end();
  }
});

function handleInit(req, res) {
  let body = '';

  req.on('data', chunk => {
    body += chunk.toString();
  });

  req.on('end', () => {
    try {
      const payload = JSON.parse(body);
      actionCode = payload.code;

      // Optional: Validate code by attempting to compile it
      try {
        new Function(actionCode);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        initError = e.message;
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: `Compilation failed: ${e.message}` }));
      }
    } catch (e) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Invalid JSON' }));
    } finally {
      process.stderr.write('XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX\n');
    }
  });
}

function handleRun(req, res) {
  let body = '';

  req.on('data', chunk => {
    body += chunk.toString();
  });

  req.on('end', () => {
    if (initError) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: initError }));
      process.stderr.write('XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX\n');
      return;
    }

    try {
      const params = JSON.parse(body);

      // Execute action code in isolated function context
      const action = new Function('params', `
        ${actionCode}
        return main(params);
      `);

      const result = action(params);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(result));
    } catch (e) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: e.message }));
    } finally {
      process.stderr.write('XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX\n');
    }
  });
}

const PORT = process.env.PORT || 8080;
server.listen(PORT, () => {
  console.log(`OpenWhisk action proxy listening on port ${PORT}`);
});
```

## Building and Using Custom Runtimes

### Step 1: Create Dockerfile

Create a `Dockerfile` that implements the OpenWhisk protocol:

```dockerfile
FROM node:18-alpine

WORKDIR /app

COPY proxy.js .
COPY package.json .

RUN npm install

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://localhost:8080/health || exit 1

CMD ["node", "proxy.js"]
```

### Step 2: Build and Push

```bash
# Build the image
docker build -t my-custom-runtime:latest .

# Tag for registry
docker tag my-custom-runtime:latest myregistry.azurecr.io/my-custom-runtime:latest

# Push to registry
docker push myregistry.azurecr.io/my-custom-runtime:latest
```

### Step 3: Register with OpenWhisk

Register the image as a runtime or use directly with actions.

**Using with direct docker reference:**
```bash
wsk action create myaction action.zip \
  --docker myregistry.azurecr.io/my-custom-runtime:latest
```

**Or register as a runtime in OpenWhisk:**
```bash
wsk runtime create customlang myregistry.azurecr.io/my-custom-runtime:latest \
  --is-default-for-kind=true
```

## Registering Custom Runtime with PenguinWhisk

PenguinWhisk (OpenWhisk for ARM) supports custom blackbox runtimes through the following methods:

### Method 1: Direct Docker Image Reference

Specify the docker image directly in action creation:

```bash
wsk action create myaction myaction.zip \
  --docker myregistry/my-custom-runtime:v1.0 \
  --memory 256 \
  --timeout 60000
```

### Method 2: Runtime Registration

Register a custom runtime for reuse across multiple actions:

```bash
# Register the runtime
wsk runtime create \
  --name customlang \
  --kind customlang \
  myregistry/my-custom-runtime:v1.0

# Use registered runtime
wsk action create myaction myaction.zip --kind customlang
```

### Method 3: Namespace-Level Runtime

Register a runtime at namespace scope:

```bash
wsk runtime create myaction \
  --namespace /myorg/myspace \
  myregistry/my-custom-runtime:v1.0
```

### Configuration Best Practices

**Memory and Timeout Settings:**
```bash
wsk action create myaction myaction.zip \
  --docker myregistry/my-custom-runtime:latest \
  --memory 512 \
  --timeout 120000 \
  --env-file config.env
```

**With Environment Variables:**
```bash
# config.env
CUSTOM_VAR=value
LOG_LEVEL=debug
```

## Best Practices and Tips

1. **Container Size**: Keep containers lightweight (use slim/alpine base images)
2. **Error Handling**: Implement comprehensive error handling and return meaningful error messages
3. **Thread Safety**: Ensure code execution is thread-safe for concurrent invocations
4. **Logging**: Log all errors and debugging information to stderr
5. **Log Framing**: Always use the activation marker for proper log separation
6. **Health Checks**: Implement GET /health endpoint for monitoring and startup verification
7. **Response Format**: Return JSON responses with consistent structure
8. **Timeout Handling**: Implement timeout detection and graceful shutdown
9. **Security**: Don't expose sensitive information in error messages
10. **Testing**: Test your proxy locally with curl before deploying
11. **Performance**: Profile and optimize initialization (/init) performance
12. **Resource Limits**: Respect __OW_ACTION_MEMORY environment variable
