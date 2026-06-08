FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 8080
CMD ["/bin/sh", "-c", "AGENT1_URL=${AGENT1_URL:-http://localhost:8001} && AGENT2_URL=${AGENT2_URL:-http://localhost:8002} && AGENT3_URL=${AGENT3_URL:-http://localhost:8003} && A2A_AUTH_TOKEN=${A2A_AUTH_TOKEN:-} && sed -i \"s|http://localhost:8001|${AGENT1_URL}|g\" /usr/share/nginx/html/index.html && sed -i \"s|http://localhost:8002|${AGENT2_URL}|g\" /usr/share/nginx/html/index.html && sed -i \"s|http://localhost:8003|${AGENT3_URL}|g\" /usr/share/nginx/html/index.html && sed -i \"s|__A2A_TOKEN__|${A2A_AUTH_TOKEN}|g\" /usr/share/nginx/html/index.html && nginx -g 'daemon off;'"]
