#!/usr/bin/env python3
"""
OpenWhisk Python 3.12 Action Proxy
Implements the OpenWhisk runtime protocol using only standard library
"""

import http.server
import json
import sys
import os
import base64
import asyncio
import signal
import traceback
from typing import Optional, Dict, Any, Callable


class ActionProxy:
    """Manages OpenWhisk action lifecycle"""

    def __init__(self):
        self.action_function: Optional[Callable] = None
        self.action_namespace: Dict[str, Any] = {}
        self.initialized = False

    def init(self, request_data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        """Initialize action from code"""
        try:
            if not request_data or "value" not in request_data:
                return {"error": "Missing value in request"}, 502

            value = request_data["value"]

            # Get code
            if "code" not in value:
                return {"error": "Missing code in request"}, 502

            code = value["code"]

            # Handle binary code
            if value.get("binary", False):
                try:
                    code = base64.b64decode(code).decode('utf-8')
                except Exception as e:
                    return {"error": f"Failed to decode binary code: {str(e)}"}, 502

            # Get main function name
            main_func_name = value.get("main", "main")

            # Set environment variables
            if "env" in value and isinstance(value["env"], dict):
                for key, val in value["env"].items():
                    os.environ[key] = str(val)

            # Execute code in isolated namespace
            self.action_namespace = {
                "__name__": "__main__",
                "__builtins__": __builtins__
            }

            try:
                exec(code, self.action_namespace)
            except Exception as e:
                error_msg = f"Failed to execute action code: {str(e)}"
                traceback.print_exc(file=sys.stderr)
                return {"error": error_msg}, 502

            # Get main function reference
            if main_func_name not in self.action_namespace:
                return {"error": f"Missing main function: {main_func_name}"}, 502

            self.action_function = self.action_namespace[main_func_name]

            if not callable(self.action_function):
                return {"error": f"Main function {main_func_name} is not callable"}, 502

            self.initialized = True
            return {"ok": True}, 200

        except Exception as e:
            error_msg = f"Initialization error: {str(e)}"
            traceback.print_exc(file=sys.stderr)
            return {"error": error_msg}, 502

    def run(self, request_data: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        """Run initialized action with parameters"""
        try:
            if not self.initialized or self.action_function is None:
                return {"error": "Action not initialized"}, 502

            # Get parameters
            params = {}
            if request_data and "value" in request_data:
                params = request_data["value"]

            # Set OpenWhisk environment variables
            self._set_openwhisk_env(params)

            # Call main function
            try:
                result = self.action_function(params)

                # Handle async functions
                if asyncio.iscoroutine(result):
                    result = asyncio.run(result)

                # Print activation marker
                print("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()

                # Handle None result
                if result is None:
                    result = {}

                # Ensure result is a dict
                if not isinstance(result, dict):
                    result = {"result": result}

                return result, 200

            except Exception as e:
                # Application errors return 200 with error in response
                error_msg = str(e)
                traceback.print_exc(file=sys.stderr)

                print("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()

                return {"error": error_msg}, 200

        except Exception as e:
            # System errors return 502
            error_msg = f"System error during run: {str(e)}"
            traceback.print_exc(file=sys.stderr)

            print("XXX_THE_END_OF_A_WHISK_ACTIVATION_XXX", flush=True)
            sys.stdout.flush()
            sys.stderr.flush()

            return {"error": error_msg}, 502

    def _set_openwhisk_env(self, params: Dict[str, Any]):
        """Set OpenWhisk environment variables"""
        # Common OpenWhisk environment variables
        if "api_key" in params:
            os.environ["__OW_API_KEY"] = str(params["api_key"])

        if "namespace" in params:
            os.environ["__OW_NAMESPACE"] = str(params["namespace"])

        if "action_name" in params:
            os.environ["__OW_ACTION_NAME"] = str(params["action_name"])

        if "activation_id" in params:
            os.environ["__OW_ACTIVATION_ID"] = str(params["activation_id"])

        if "deadline" in params:
            os.environ["__OW_DEADLINE"] = str(params["deadline"])


class ActionProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for OpenWhisk action proxy"""

    action_proxy: ActionProxy = ActionProxy()

    def do_POST(self):
        """Handle POST requests"""
        if self.path == "/init":
            self._handle_init()
        elif self.path == "/run":
            self._handle_run()
        else:
            self._send_error(404, {"error": f"Not found: {self.path}"})

    def do_GET(self):
        """Handle GET requests"""
        if self.path == "/health":
            self._send_response(200, {"ok": True})
        else:
            self._send_error(404, {"error": f"Not found: {self.path}"})

    def _handle_init(self):
        """Handle /init endpoint"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            request_data = json.loads(body.decode('utf-8')) if body else {}

            response, status = self.action_proxy.init(request_data)
            self._send_response(status, response)

        except json.JSONDecodeError as e:
            self._send_error(502, {"error": f"Invalid JSON: {str(e)}"})
        except Exception as e:
            self._send_error(502, {"error": f"Init handler error: {str(e)}"})

    def _handle_run(self):
        """Handle /run endpoint"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            request_data = json.loads(body.decode('utf-8')) if body else {}

            response, status = self.action_proxy.run(request_data)
            self._send_response(status, response)

        except json.JSONDecodeError as e:
            self._send_error(502, {"error": f"Invalid JSON: {str(e)}"})
        except Exception as e:
            self._send_error(502, {"error": f"Run handler error: {str(e)}"})

    def _send_response(self, status: int, data: Dict[str, Any]):
        """Send JSON response"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

        response_body = json.dumps(data).encode('utf-8')
        self.wfile.write(response_body)

    def _send_error(self, status: int, data: Dict[str, Any]):
        """Send error response"""
        self._send_response(status, data)

    def log_message(self, format, *args):
        """Override to customize logging"""
        sys.stderr.write(f"{self.address_string()} - [{self.log_date_time_string()}] {format % args}\n")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    print(f"\nReceived signal {signum}, shutting down gracefully...", file=sys.stderr)
    sys.exit(0)


def main():
    """Main entry point"""
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Create HTTP server
    port = int(os.environ.get('PORT', 8080))
    server_address = ('', port)

    httpd = http.server.HTTPServer(server_address, ActionProxyHandler)

    print(f"OpenWhisk Python 3.12 Action Proxy listening on port {port}", file=sys.stderr)
    sys.stderr.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...", file=sys.stderr)
        httpd.shutdown()


if __name__ == "__main__":
    main()
