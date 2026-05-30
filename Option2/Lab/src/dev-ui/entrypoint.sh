#!/bin/sh
# Inject agent URLs and A2A token into index.html from environment variables
AGENT1_URL="${AGENT1_URL:-http://localhost:8001}"
AGENT2_URL="${AGENT2_URL:-http://localhost:8002}"
AGENT3_URL="${AGENT3_URL:-http://localhost:8003}"
A2A_AUTH_TOKEN="${A2A_AUTH_TOKEN:-}"
sed -i "s|http://localhost:8001|${AGENT1_URL}|g" /usr/share/nginx/html/index.html
sed -i "s|http://localhost:8002|${AGENT2_URL}|g" /usr/share/nginx/html/index.html
sed -i "s|http://localhost:8003|${AGENT3_URL}|g" /usr/share/nginx/html/index.html
sed -i "s|__A2A_TOKEN__|${A2A_AUTH_TOKEN}|g" /usr/share/nginx/html/index.html
exec nginx -g 'daemon off;'
