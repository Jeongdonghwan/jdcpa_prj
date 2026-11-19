#!/usr/bin/env bash
# 서브도메인 6개의 nginx 설정을 만든다. 다시 돌려도 된다(덮어씀).
#   bash deploy/nginx_sites.sh <도메인> <앱 포트> [preview]
# - 서브도메인마다 server 블록 1개: root = export/<라벨>(정적 사이트맵·.ops.json — SEO 대시보드 수집기가 읽는다), 나머지는 gunicorn 으로.
# - preview 를 붙이면 검색엔진 차단(X-Robots-Tag noindex + robots.txt Disallow). 실제 도메인 연결 때는 빼고 다시 실행.
set -euo pipefail
DOMAIN="${1:?도메인}"; PORT="${2:?포트}"; MODE="${3:-}"
PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF=/etc/nginx/sites-available/cpa_prj
: > "$CONF"
for SUB in law dui baby car demol life; do
  mkdir -p "$PROJ_DIR/export/$SUB"
  {
    echo "server {"
    echo "    listen 80;"
    echo "    server_name $SUB.$DOMAIN;"
    echo "    root $PROJ_DIR/export/$SUB;"
    echo "    client_max_body_size 2M;"
    echo "    gzip on; gzip_comp_level 5; gzip_min_length 1024; gzip_vary on;"
    echo "    gzip_types text/css application/javascript application/xml text/xml text/plain application/json image/svg+xml;"
    if [ "$MODE" = "preview" ]; then
      echo '    add_header X-Robots-Tag "noindex, nofollow" always;'
      echo '    location = /robots.txt { default_type text/plain; return 200 "User-agent: *\nDisallow: /\n"; }'
    fi
    echo '    location = /.ops.json { deny all; }'
    echo '    location ~ ^/sitemap[a-z0-9-]*\.xml$ { default_type application/xml; try_files $uri @app; }'
    echo "    location /static/ { alias $PROJ_DIR/static/; expires 30d; access_log off; }"
    echo '    location / { try_files /__none__ @app; }'
    echo '    location @app {'
    echo "        proxy_pass http://127.0.0.1:$PORT;"
    echo '        proxy_set_header Host $host;'
    echo '        proxy_set_header X-Real-IP $remote_addr;'
    echo '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;'
    echo '        proxy_set_header X-Forwarded-Proto $scheme;'
    echo '        proxy_read_timeout 60s;'
    echo '    }'
    echo "}"
  } >> "$CONF"
done
ln -sf "$CONF" /etc/nginx/sites-enabled/cpa_prj
nginx -t && systemctl reload nginx
echo "nginx: $(grep -c server_name "$CONF")개 서브도메인 → 127.0.0.1:$PORT ($MODE)"
