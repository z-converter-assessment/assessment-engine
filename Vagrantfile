# -*- mode: ruby -*-
# vi: set ft=ruby :

# 에이전트 secret 채널: infra/agent.env (엔진 .env와 분리).
# RABBITMQ_HOST만 예외: 엔진은 "rabbitmq"(도커 서비스명)이지만 VM → 호스트는 Vagrant NAT 주소.
RABBITMQ_HOST = "10.0.2.2"

agent_env_path = "infra/agent.env"
unless File.exist?(agent_env_path)
  raise "#{agent_env_path}이 없다. 'cp infra/agent.env.example infra/agent.env' 후 운영 값으로 수정하라."
end

dot_env = {}
File.foreach(agent_env_path) do |line|
  line = line.strip
  next if line.empty? || line.start_with?("#")
  key, val = line.split("=", 2)
  dot_env[key] = val
end

RABBITMQ_USER     = dot_env.fetch("RABBITMQ_USER",                  "assessment")
RABBITMQ_PASS     = dot_env.fetch("RABBITMQ_PASSWORD",              "assessment")
RABBITMQ_EXCHANGE = dot_env.fetch("RABBITMQ_EXCHANGE",              "assessment")
RABBITMQ_KEY_INV  = dot_env.fetch("RABBITMQ_ROUTING_KEY_INVENTORY",   "server.inventory")
RABBITMQ_KEY_MET  = dot_env.fetch("RABBITMQ_ROUTING_KEY_METRICS",     "server.metrics")
RABBITMQ_KEY_ERR  = dot_env.fetch("RABBITMQ_ROUTING_KEY_ERROR",       "server.error")
RABBITMQ_KEY_TASK = dot_env.fetch("RABBITMQ_ROUTING_KEY_TASK_RESULT", "task.result")

VMS = [
  # cache-server-01: redis 설치 → "cache" 카테고리 뱃지
  { name: "cache-server-01",  box: "bento/ubuntu-22.04",  family: :deb, extra_mounts: [],        services: :redis, ext_ip: nil },
  # app-server-01:   Rocky Linux, 별도 서비스 없음 → "unknown" 뱃지만
  { name: "app-server-01",    box: "bento/rockylinux-9",  family: :rpm, extra_mounts: ["/data"], services: :none,  ext_ip: nil },
  # web-server-01: nginx 설치 → "web" 카테고리 뱃지, 외부 노출 서버
  { name: "web-server-01",    box: "bento/debian-12",     family: :deb, extra_mounts: [],        services: :nginx, ext_ip: "203.0.113.10" },
]

Vagrant.configure("2") do |config|
  config.vm.boot_timeout = 600

  VMS.each do |vm|
    config.vm.define vm[:name] do |node|
      node.vm.box = vm[:box]
      node.vm.hostname = vm[:name]

      node.vm.synced_folder "../assessment-agent", "/home/vagrant/assessment-agent",
        type: "rsync",
        rsync__exclude: [".git/", "*.o", "*.a", "assessment-agent"]

      node.vm.provider "virtualbox" do |vb|
        vb.name   = vm[:name]
        vb.memory = 1024
        vb.cpus   = 2
        vb.customize ["modifyvm", :id, "--audio", "none"]
        vb.customize ["modifyvm", :id, "--usb",   "off"]
        vb.customize ["modifyvm", :id, "--vram",  "8"]
      end

      # 1. 빌드 의존성 설치
      if vm[:family] == :deb
        node.vm.provision "shell", inline: <<~SHELL
          apt-get update -qq
          apt-get install -y --no-install-recommends gcc make pkg-config \
            libc6-dev librabbitmq-dev libcjson-dev
        SHELL
      else
        node.vm.provision "shell", inline: <<~SHELL
          dnf install -y epel-release dnf-plugins-core
          dnf config-manager --set-enabled crb
          dnf install -y gcc make pkg-config librabbitmq-devel cjson-devel
        SHELL
      end

      # 1-1. 추가 서비스 설치
      if vm[:services] == :redis
        node.vm.provision "shell", inline: <<~SHELL
          apt-get install -y --no-install-recommends redis-server
          systemctl enable redis-server
          systemctl start redis-server
        SHELL
      elsif vm[:services] == :nginx
        node.vm.provision "shell", inline: <<~SHELL
          apt-get install -y --no-install-recommends nginx
          systemctl enable nginx
          systemctl start nginx
        SHELL
      end

      # 2. .env를 /etc/assessment-agent.env에 생성
      #    - synced_folder 바깥 → VM별 AGENT_HOSTNAME_OVERRIDE 독립 유지
      #    - /etc/ 하위 → SELinux(Rocky Linux 9)에서 systemd가 읽기 가능
      node.vm.provision "shell", inline: <<~SHELL
        cat > /etc/assessment-agent.env <<EOF
RABBITMQ_HOST=#{RABBITMQ_HOST}
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=#{RABBITMQ_USER}
RABBITMQ_PASS=#{RABBITMQ_PASS}
RABBITMQ_EXCHANGE=#{RABBITMQ_EXCHANGE}
RABBITMQ_ROUTING_KEY_INVENTORY=#{RABBITMQ_KEY_INV}
RABBITMQ_ROUTING_KEY_METRICS=#{RABBITMQ_KEY_MET}
RABBITMQ_ROUTING_KEY_ERROR=#{RABBITMQ_KEY_ERR}
RABBITMQ_ROUTING_KEY_TASK_RESULT=#{RABBITMQ_KEY_TASK}
AGENT_HOSTNAME_OVERRIDE=#{vm[:name]}
AGENT_INTERVAL_SEC=60
#{vm[:ext_ip] ? "AGENT_EXTERNAL_IP=#{vm[:ext_ip]}" : ''}
EOF
        chmod 644 /etc/assessment-agent.env
        echo "[provision] .env written to /etc/assessment-agent.env"
      SHELL

      # 3. 에이전트 빌드
      node.vm.provision "shell", privileged: false, inline: <<~SHELL
        cd /home/vagrant/assessment-agent
        make
      SHELL

      # 4. 바이너리를 /usr/local/bin/에 복사 후 systemd 서비스 등록 및 시작
      #    VirtualBox 공유 폴더(vboxsf)는 SELinux 환경(Rocky Linux 9)에서
      #    systemd가 직접 실행할 수 없으므로 /usr/local/bin/에 설치
      node.vm.provision "shell", inline: <<~SHELL
        cp /home/vagrant/assessment-agent/assessment-agent /usr/local/bin/assessment-agent
        chmod 755 /usr/local/bin/assessment-agent
        cat > /etc/systemd/system/assessment-agent.service <<'EOF'
[Unit]
Description=Assessment Agent
After=network.target

[Service]
User=vagrant
EnvironmentFile=/etc/assessment-agent.env
ExecStart=/usr/local/bin/assessment-agent
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable assessment-agent
        systemctl start assessment-agent || true
        echo "[provision] assessment-agent service started"
      SHELL

      # 5. 합성 부하(synthetic load) — 메트릭 추이 가시화 용도.
      #    1분 주기 systemd timer가 짧은 burst(CPU/MEM/DISK/NET) 실행. 풀 부하 X — 컴퓨터에 무리 없는 수준.
      #    app-server-01만 heavy 프로파일 (모든 자원 유의미한 부하), 나머지 VM은 light.
      load_profile = (vm[:name] == "app-server-01" ? "heavy" : "light")
      node.vm.provision "shell", inline: <<~SHELL
        cat > /usr/local/bin/synthetic-load-light.sh <<'EOF'
#!/bin/bash
# 가벼운 합성 부하 — 메트릭 차트에 추이가 보이게 하는 용도.
# CPU 1~3초 burst + 메모리 5~20MB + 작은 디스크 I/O + 호스트 NAT 트래픽.

sleep $(( RANDOM % 30 ))

# CPU 짧은 burst (1~3초)
duration=$(( (RANDOM % 3) + 1 ))
timeout ${duration}s sha256sum /dev/zero > /dev/null 2>&1 || true

# 메모리 일시 할당 (5~20 MB)
size=$(( (RANDOM % 16) + 5 ))
head -c ${size}M /dev/urandom > /tmp/synthetic-load 2>/dev/null || true
sync
rm -f /tmp/synthetic-load

# 디스크 I/O (200KB~600KB)
count=$(( (RANDOM % 100) + 50 ))
dd if=/dev/zero of=/tmp/synthetic-io bs=4k count=${count} 2>/dev/null || true
sync
rm -f /tmp/synthetic-io

# 네트워크 — 호스트(10.0.2.2)로 트래픽 (eth0 통과)
ping -c $(( (RANDOM % 5) + 3 )) -q 10.0.2.2 > /dev/null 2>&1 || true
curl -s -m 2 http://10.0.2.2:8000/health > /dev/null 2>&1 || true
if [ $((RANDOM % 2)) -eq 0 ]; then
  curl -s -m 2 http://10.0.2.2:8000/static/js/chart-utils.js > /dev/null 2>&1 || true
fi
EOF

        cat > /usr/local/bin/synthetic-load-heavy.sh <<'EOF'
#!/bin/bash
# 유의미한 합성 부하 — app-server-01 전용. 모든 자원에 차트 spike 명확히 보이는 강도.
# VM 1024MB / 2CPU 기준이라 호스트(macOS) 부담은 작음.

sleep $(( RANDOM % 15 ))

# CPU: 5~12초 burst — 2 코어 동시 (VM은 2 CPU)
duration=$(( (RANDOM % 8) + 5 ))
for i in 1 2; do
  timeout ${duration}s sha256sum /dev/zero > /dev/null 2>&1 &
done
wait || true

# 메모리: 100~250MB 일시 할당 (VM 메모리의 10~25%)
size=$(( (RANDOM % 150) + 100 ))
head -c ${size}M /dev/urandom > /tmp/synthetic-load-mem 2>/dev/null || true
sleep 2
sync
rm -f /tmp/synthetic-load-mem

# 디스크 I/O: 5~15MB write (light의 ~25배)
count=$(( (RANDOM % 2500) + 1250 ))
dd if=/dev/zero of=/tmp/synthetic-io bs=4k count=${count} 2>/dev/null || true
sync
sleep 1
rm -f /tmp/synthetic-io

# 네트워크: 다중 fetch + 다수 ping
for i in 1 2 3; do
  curl -s -m 5 http://10.0.2.2:8000/static/js/chart-utils.js > /dev/null 2>&1 || true
done
curl -s -m 5 "http://10.0.2.2:8000/servers/" > /dev/null 2>&1 || true
ping -c $(( (RANDOM % 10) + 10 )) -q 10.0.2.2 > /dev/null 2>&1 || true
EOF

        chmod 755 /usr/local/bin/synthetic-load-light.sh /usr/local/bin/synthetic-load-heavy.sh
        # 프로파일에 맞는 스크립트를 활성 위치(/usr/local/bin/synthetic-load.sh)로 link
        ln -sf /usr/local/bin/synthetic-load-#{load_profile}.sh /usr/local/bin/synthetic-load.sh

        cat > /etc/systemd/system/synthetic-load.service <<'EOF'
[Unit]
Description=Synthetic load (metrics visualization aid)
[Service]
Type=oneshot
ExecStart=/usr/local/bin/synthetic-load.sh
EOF

        cat > /etc/systemd/system/synthetic-load.timer <<'EOF'
[Unit]
Description=Run synthetic-load every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
EOF
        systemctl daemon-reload
        systemctl enable synthetic-load.timer
        systemctl start synthetic-load.timer
        echo "[provision] synthetic-load.timer started (1min interval)"
      SHELL
    end
  end
end
