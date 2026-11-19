#!/usr/bin/env bash
# cpa_prj 한 번에 설치·오픈 (211 서버, root). 다시 실행해도 된다 — 이미 된 단계는 건너뛰고, 열린 페이지는 닫지 않는다.
#   bash <(curl -fsSL https://raw.githubusercontent.com/Jeongdonghwan/jdcpa_prj/main/deploy/install.sh) <도메인> [인증서 알림 메일]
# 하는 일: 패키지 → 코드(clone/pull) → venv → .env → DB(덤프 적재) → 서비스 → DNS 확인 → nginx+HTTPS → 첫 오픈(stage·plan·publish) → cron → 점검
set -euo pipefail
D="${1:?사용법: bash <(curl ...) 도메인 [메일]}"; MAIL="${2:-}"
SUBS="law dui baby car demol life"; DIR=/var/www/cpa_prj; REPO=https://github.com/Jeongdonghwan/jdcpa_prj.git
say() { printf '\n== %s\n' "$*"; }

say "1/9 패키지"
apt-get install -y -q python3-venv python3-pip fonts-nanum certbot python3-certbot-nginx >/dev/null && echo OK

say "2/9 코드"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q && echo "업데이트"; else git clone -q "$REPO" "$DIR" && echo "받음"; fi
cd "$DIR"
if [ ! -f .port ]; then P=8048; while ss -tln | grep -q ":$P "; do P=$((P+1)); done; echo $P > .port; fi
P=$(cat .port); echo "포트 $P"
[ -x venv/bin/gunicorn ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt && echo "pip OK"

say "3/9 환경(.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 24)|; s|^DB_HOST=.*|DB_HOST=127.0.0.1|; s|^DB_PORT=.*|DB_PORT=3306|; s|^DB_USER=.*|DB_USER=cpa_prj|; s|^DB_PASSWORD=.*|DB_PASSWORD=1234|; s|^DB_NAME=.*|DB_NAME=cpa_prj|; s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(openssl rand -hex 6)|" .env
fi
sed -i "s|^BASE_URL=.*|BASE_URL=https://law.$D|" .env
grep -q '^OG_FONT=.' .env || { sed -i '/^OG_FONT=/d' .env; echo "OG_FONT=/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" >> .env; }
echo OK

say "4/9 DB"
if mysql -e "SELECT 1" >/dev/null 2>&1; then ROOT=(mysql); else echo "MariaDB root 비밀번호:"; read -rs RPW; ROOT=(mysql -u root "-p$RPW"); fi
"${ROOT[@]}" -e "CREATE DATABASE IF NOT EXISTS cpa_prj DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS 'cpa_prj'@'localhost'; ALTER USER 'cpa_prj'@'localhost' IDENTIFIED BY '1234'; GRANT ALL PRIVILEGES ON cpa_prj.* TO 'cpa_prj'@'localhost'; FLUSH PRIVILEGES;"
M=(mysql -u cpa_prj -p1234 cpa_prj)
if ! "${M[@]}" -e "SELECT 1 FROM keyword_combos LIMIT 1" >/dev/null 2>&1; then
  echo "덤프 적재 중(5~10분)…"; gunzip -c deploy/cpa_prj.sql.gz | "${M[@]}"
fi
"${M[@]}" -e "UPDATE sites SET host=CONCAT(SUBSTRING_INDEX(host,'.',1),'.$D'), indexnow_key=IFNULL(indexnow_key, MD5(RAND())) WHERE id>1; REPLACE INTO site_settings SELECT id,'indexnow_daily_cap','10000' FROM sites WHERE id>1;"
"${M[@]}" -e "SELECT host, (SELECT COUNT(*) FROM keyword_combos k WHERE k.site_id=s.id) pages FROM sites s WHERE id>1;"

say "5/9 서비스"
cat > /etc/systemd/system/cpa_prj.service <<EOF
[Unit]
Description=cpa_prj Flask (gunicorn :$P)
After=network.target mariadb.service

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/gunicorn -w 2 --threads 4 --timeout 60 -b 127.0.0.1:$P "app:create_app()"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable -q cpa_prj && systemctl restart cpa_prj && sleep 2 && systemctl is-active cpa_prj

say "6/9 DNS 확인"
IPS=" $(hostname -I) 211.45.175.195 "; MISS=""
for s in $SUBS; do
  ip=$(getent hosts "$s.$D" | cut -d' ' -f1 || true); echo "$s.$D → ${ip:-없음}"
  if [ -z "$ip" ] || [[ "$IPS" != *" $ip "* ]]; then MISS="$MISS $s"; fi
done
if [ -n "$MISS" ]; then
  echo "아직 이 서버로 연결되지 않은 주소:$MISS — DNS 저장 후 10~30분 뒤 같은 명령을 다시 실행하세요(여기까지는 그대로 남습니다)."; exit 1
fi

say "7/9 nginx + HTTPS"
bash deploy/nginx_sites.sh "$D" "$P"
CERT=(certbot --nginx --agree-tos --redirect -n --expand)
[ -n "$MAIL" ] && CERT+=(-m "$MAIL") || CERT+=(--register-unsafely-without-email)
for s in $SUBS; do CERT+=(-d "$s.$D"); done
"${CERT[@]}" 2>&1 | grep -E "Congratulations|Successfully|Certificate not yet due|Keeping|error|Error" || true
systemctl restart cpa_prj && sleep 2

say "8/9 첫 오픈"
F="./venv/bin/flask --app app jobs"
$F og-topics >/dev/null 2>&1 || echo "(OG 이미지 생성 경고 — 페이지에는 영향 없음)"
if [ -z "$("${M[@]}" -N -e "SELECT 1 FROM site_settings WHERE site_id=2 AND \`key\`='publish_plan'")" ]; then
  $F stage --yes && $F plan "$(date +%F)" --days 5 --rest-days 18 --then 500
else
  echo "공개 계획이 이미 있음 — 다시 닫지 않고 오늘 몫만 확인"
fi
$F publish --quiet && $F export-sitemaps export && $F indexnow
cp deploy/cron.txt /etc/cron.d/cpa_prj && chmod 644 /etc/cron.d/cpa_prj && echo "cron 설치(매일 05:10 공개)"

say "9/9 점검"
for s in $SUBS; do curl -s -o /dev/null -w "https://$s.$D/ %{http_code}\n" "https://$s.$D/"; done
curl -s "https://law.$D/robots.txt" | grep -i sitemap
echo; echo "관리자: https://law.$D/admin/  아이디 $(grep '^ADMIN_USER=' .env | cut -d= -f2)  비밀번호 $(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2)"
echo "완료. 오늘 연 수는 위 publish 줄, 앞으로의 일정은 plan 줄에 있습니다."
