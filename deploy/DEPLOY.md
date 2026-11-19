# cpa_prj 배포 (211.45.175.195)

서브도메인 6개(law·dui·baby·car·demol·life)를 Flask 앱 1개 + nginx 로 띄운다. 도메인이 정해지기 전에는 미리보기 주소
`law.211.45.175.195.sslip.io` 처럼 IP 를 넣은 주소로 확인한다(검색엔진 차단 상태). 블록은 MobaXterm 에 통째로 붙여넣는다.

## 0. 로컬(PC)에서 준비 — Claude 가 한다

- 시드 DB 덤프: `deploy/cpa_prj.sql.gz` (지역·콘텐츠·오퍼 전체. 서버에서 시드를 다시 돌리지 않는다 — 서버가 작아 몇 시간 걸린다)
- 코드 푸시: GitHub 저장소(비공개)에 main 브랜치
- 덤프 파일은 git 에 넣지 않는다. MobaXterm 왼쪽 SFTP 창으로 서버 `/root/` 에 끌어다 놓는다.

## 1. 코드·환경·서비스

```bash
P=8048; while ss -tln | grep -q ":$P "; do P=$((P+1)); done; echo "포트 $P"
grep -rn "listen" /etc/nginx/sites-enabled | grep -c listen
apt-get install -y python3-venv python3-pip fonts-nanum >/dev/null && echo "패키지 OK"

cd /var/www && git clone https://github.com/Jeongdonghwan/jdcpa_prj.git cpa_prj && cd cpa_prj
echo $P > .port
python3 -m venv venv && ./venv/bin/pip install -q -r requirements.txt && echo "pip OK"

cp .env.example .env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 24)|" .env
sed -i "s|^DB_PORT=.*|DB_PORT=3306|; s|^DB_USER=.*|DB_USER=cpa_prj|; s|^DB_PASSWORD=.*|DB_PASSWORD=1234|; s|^DB_NAME=.*|DB_NAME=cpa_prj|" .env
sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$(openssl rand -hex 6)|" .env
sed -i "s|^BASE_URL=.*|BASE_URL=http://law.211.45.175.195.sslip.io|" .env
grep -q '^OG_FONT=' .env || echo "OG_FONT=/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" >> .env
grep -v SECRET_KEY .env

cat > /etc/systemd/system/cpa_prj.service <<EOF
[Unit]
Description=cpa_prj Flask (gunicorn :$P)
After=network.target mariadb.service

[Service]
WorkingDirectory=/var/www/cpa_prj
ExecStart=/var/www/cpa_prj/venv/bin/gunicorn -w 2 --threads 4 --timeout 60 -b 127.0.0.1:$P "app:create_app()"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && echo "유닛 OK"
```

기대 출력: `포트 80xx` / `pip OK` / `.env` 내용(ADMIN_PASSWORD 는 무작위 12자리 — 관리자 로그인에 쓴다) / `유닛 OK`.

## 2. DB (root 비밀번호를 두 번 묻는다)

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS cpa_prj DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER IF NOT EXISTS 'cpa_prj'@'localhost'; ALTER USER 'cpa_prj'@'localhost' IDENTIFIED BY '1234'; GRANT ALL PRIVILEGES ON cpa_prj.* TO 'cpa_prj'@'localhost'; FLUSH PRIVILEGES;"
gunzip -c /root/cpa_prj.sql.gz | mysql -u cpa_prj -p1234 cpa_prj && mysql -u cpa_prj -p1234 cpa_prj -e "SELECT host, (SELECT COUNT(*) FROM keyword_combos k WHERE k.site_id=s.id) pages FROM sites s;"
```

기대 출력: 사이트 7줄(첫 줄 localhost:5000 은 개발용 — 외부에서 접근되지 않는다)과 사이트별 페이지 수.

## 3. 미리보기 오픈 (검색 차단)

```bash
cd /var/www/cpa_prj && P=$(cat .port); D=211.45.175.195.sslip.io; F="./venv/bin/flask --app app jobs"
mysql -u cpa_prj -p1234 cpa_prj -e "UPDATE sites SET host=CONCAT(SUBSTRING_INDEX(host,'.',1),'.$D'), indexnow_key=IFNULL(indexnow_key, MD5(RAND())) WHERE id>1; REPLACE INTO site_settings SELECT id,'indexnow_daily_cap','10000' FROM sites WHERE id>1;"
bash deploy/nginx_sites.sh $D $P preview
systemctl enable --now cpa_prj && sleep 2 && systemctl is-active cpa_prj
$F og-topics
for s in law dui baby car demol life; do curl -s -o /dev/null -w "$s %{http_code}\n" http://$s.$D/; done
curl -s http://law.$D/robots.txt
```

기대 출력: `nginx: 6개 서브도메인` / `active` / 사이트 6개 `200` / robots `Disallow: /`.
브라우저: `http://law.211.45.175.195.sslip.io/` 등. 미리보기에서는 시드에서 게이트를 통과한 페이지가 전부 열려 있다.

## 4. 실제 도메인 연결 + 순차 오픈 시작 (도메인을 받으면)

DNS: `law`·`dui`·`baby`·`car`·`demol`·`life` 6개 A 레코드를 211.45.175.195 로. `D=` 만 실제 도메인으로 바꿔 붙여넣는다.

```bash
cd /var/www/cpa_prj && P=$(cat .port); D=실제도메인; F="./venv/bin/flask --app app jobs"
for s in law dui baby car demol life; do echo "$s $(getent hosts $s.$D | cut -d" " -f1)"; done
mysql -u cpa_prj -p1234 cpa_prj -e "UPDATE sites SET host=CONCAT(SUBSTRING_INDEX(host,'.',1),'.$D') WHERE id>1;"
sed -i "s|^BASE_URL=.*|BASE_URL=https://law.$D|" .env
bash deploy/nginx_sites.sh $D $P
certbot --nginx $(for s in law dui baby car demol life; do printf -- "-d %s.%s " $s $D; done) --agree-tos -m jdhwan0227@gmail.com --redirect -n
systemctl restart cpa_prj
$F stage --yes && $F plan $(date +%F) --days 5 --rest-days 18 --then 500 && $F publish --quiet && $F export-sitemaps export && $F indexnow
cp deploy/cron.txt /etc/cron.d/cpa_prj && chmod 644 /etc/cron.d/cpa_prj
for s in law dui baby car demol life; do curl -s -o /dev/null -w "$s %{http_code}\n" https://$s.$D/; done
curl -s https://law.$D/robots.txt; curl -s https://law.$D/sitemap.xml | head -5
```

기대 출력: DNS 6줄 모두 211.45.175.195 / 인증서 발급 / `publish` 가 사이트별 첫날 공개 수 출력 / 6개 `200` / robots 에 `Sitemap: https://law.도메인/sitemap.xml`.

- `stage` 는 허브·주제 페이지만 남기고 나머지를 비공개로 되돌린다. `plan` 이 오늘부터 5일 동안 우선순위 60 이상(시도·시군구·구 전 주제 + 동 대표 주제, 6개 사이트 합계 약 4만 쪽)을 고르게 나눠 열고, 6~23일차에 나머지(동 × 세부 주제 약 14.5만 쪽)를 연다. 6개 사이트 합계 매일 약 8천 쪽, 23일차에 전부 공개(사용자 결정 2026-11-19). 그 뒤 새로 추가되는 페이지는 사이트마다 하루 500쪽. 매일 새벽 5시 10분 cron 이 그날 몫(지역 페이지 + 질문 글 합계 약 54편)을 연다.
- 열린 페이지는 닫지 않는다. 날짜를 바꾸려면 `plan` 만 다시 실행한다.
- 오픈 당일: 네이버 서치어드바이저·구글 서치콘솔에 서브도메인 6개를 각각 등록하고 `sitemap.xml` 을 제출한다(소유확인 코드는 관리자 > 사이트 에 입력).

## 5. SEO 대시보드(수집기만, 결과는 대시보드 서버로)

```bash
cd /root && curl -fsSL https://raw.githubusercontent.com/Jeongdonghwan/j_dash/main/install-seo-dash-node.sh -o install-seo-dash-node.sh && DASH_PUSH_TOKEN=대시보드_푸시_토큰 DASH_ONLY="law.도메인 dui.도메인 baby.도메인 car.도메인 demol.도메인 life.도메인" bash install-seo-dash-node.sh
```

수집기는 nginx 설정에서 각 서브도메인의 root(`/var/www/cpa_prj/export/<라벨>`)를 찾아 sitemap·.ops.json 을 읽는다. 토큰은 대시보드 서버의 `/var/lib/seo-dash/push_token` 값.

## 업데이트

```bash
cd /var/www/cpa_prj && git pull && ./venv/bin/pip install -q -r requirements.txt && systemctl restart cpa_prj && systemctl is-active cpa_prj
```

## 문제가 생기면

```bash
journalctl -u cpa_prj -n 40 --no-pager
tail -40 /var/log/cpa_prj.log
nginx -t
```
