#!/usr/bin/env bash
# 서브도메인 6개의 nginx 설정을 만든다. 다시 돌려도 된다(덮어씀).
#   bash deploy/nginx_sites.sh <도메인> <앱 포트> [preview]
# - 서브도메인마다 server 블록 1개: root = export/<라벨>(정적 사이트맵·.ops.json — SEO 대시보드 수집기가 읽는다), 나머지는 gunicorn 으로.
# - preview 를 붙이면 검색엔진 차단(X-Robots-Tag noindex + robots.txt Disallow). 실제 도메인 연결 때는 빼고 다시 실행.
set -euo pipefail
DOMAIN="${1:?도메인}"; PORT="${2:?포트}"; MODE="${3:-}"
PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF=/etc/nginx/sites-available/cpa_prj
NEW="$CONF.new"; BAK="/root/cpa_prj.nginx.bak"
: > "$NEW"
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
    echo '    location ~ "^/[0-9a-f]{32}\.txt$" { default_type text/plain; try_files $uri @app; }'   # IndexNow 키(수집기가 root 에 둔 파일 먼저, 없으면 앱). {32} 가 있어 정규식을 따옴표로 감싼다
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
  } >> "$NEW"
done
# 새 설정은 검사를 통과해야만 교체한다 — 깨진 파일이 디스크에 남으면 nginx 재시작 때 이 서버의 다른 사이트까지 멈춘다
[ -f "$CONF" ] && cp "$CONF" "$BAK"
mv "$NEW" "$CONF"
ln -sf "$CONF" /etc/nginx/sites-enabled/cpa_prj
if ! nginx -t 2>&1; then
  if [ -f "$BAK" ]; then cp "$BAK" "$CONF"; else rm -f "$CONF" /etc/nginx/sites-enabled/cpa_prj; fi
  nginx -t >/dev/null 2>&1 && echo "새 설정 검사 실패 — 이전 설정으로 되돌렸습니다" >&2
  exit 1
fi
systemctl reload nginx
echo "nginx: $(grep -c server_name "$CONF")개 서브도메인 → 127.0.0.1:$PORT ($MODE)"
