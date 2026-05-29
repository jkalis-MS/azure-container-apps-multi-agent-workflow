FROM python:3.12-slim

WORKDIR /app

# System deps for grpcio/other native packages
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY sandbox-scripts/ ./sandbox-scripts/

EXPOSE 3001

# Startup script that catches and logs import errors
CMD ["python", "-u", "-c", "\nimport sys, traceback\ntry:\n    print('Importing app...', flush=True)\n    from src.main import app\n    print('Import OK, starting uvicorn...', flush=True)\n    import uvicorn\n    uvicorn.run(app, host='0.0.0.0', port=3001, log_level='info')\nexcept Exception as e:\n    traceback.print_exc()\n    # Keep container alive so we can see logs\n    import time\n    from http.server import HTTPServer, BaseHTTPRequestHandler\n    import json\n    class H(BaseHTTPRequestHandler):\n        def do_GET(self):\n            self.send_response(200)\n            self.send_header('Content-Type','application/json')\n            self.end_headers()\n            self.wfile.write(json.dumps({'error': str(e), 'type': type(e).__name__}).encode())\n        def do_POST(self):\n            self.do_GET()\n    print(f'Starting error server on :3001 due to: {e}', flush=True)\n    HTTPServer(('0.0.0.0', 3001), H).serve_forever()\n"]
