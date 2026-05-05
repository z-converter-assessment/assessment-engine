# -*- mode: ruby -*-
# vi: set ft=ruby :

# .env에서 RabbitMQ 설정을 읽어 에이전트 .env 생성에 재사용
# RABBITMQ_HOST만 예외: 엔진 .env는 "rabbitmq"(도커 서비스명)이지만
# VM → 호스트는 Vagrant NAT 주소인 10.0.2.2를 사용해야 한다
RABBITMQ_HOST = "10.0.2.2"

dot_env = {}
File.foreach(".env") do |line|
  line = line.strip
  next if line.empty? || line.start_with?("#")
  key, val = line.split("=", 2)
  dot_env[key] = val
end

RABBITMQ_USER     = dot_env.fetch("RABBITMQ_USER",                  "assessment")
RABBITMQ_PASS     = dot_env.fetch("RABBITMQ_PASSWORD",              "assessment")
RABBITMQ_EXCHANGE = dot_env.fetch("RABBITMQ_EXCHANGE",              "assessment")
RABBITMQ_KEY_INV  = dot_env.fetch("RABBITMQ_ROUTING_KEY_INVENTORY", "server.inventory")
RABBITMQ_KEY_MET  = dot_env.fetch("RABBITMQ_ROUTING_KEY_METRICS",   "server.metrics")
RABBITMQ_KEY_ERR  = dot_env.fetch("RABBITMQ_ROUTING_KEY_ERROR",     "server.error")

VMS = [
  { name: "web-server-01",    box: "bento/ubuntu-22.04",  family: :deb, extra_mounts: [] },
  { name: "db-server-01",     box: "bento/rockylinux-9",  family: :rpm, extra_mounts: ["/data"] },
  { name: "backup-server-01", box: "bento/debian-12",     family: :deb, extra_mounts: ["/backup"] },
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

      # 2. .env를 /etc/assessment-agent.env에 생성
      #    - synced_folder 바깥 → VM별 AGENT_HOSTNAME_OVERRIDE 독립 유지
      #    - /etc/ 하위 → SELinux(Rocky Linux 9)에서 systemd가 읽기 가능
      node.vm.provision "shell", inline: <<~SHELL
        cat > /etc/assessment-agent.env <<EOF
RABBITMQ_HOST=#{RABBITMQ_HOST}
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/
RABBITMQ_USER=#{RABBITMQ_USER}
RABBITMQ_PASS=#{RABBITMQ_PASS}
RABBITMQ_EXCHANGE=#{RABBITMQ_EXCHANGE}
RABBITMQ_ROUTING_KEY_INVENTORY=#{RABBITMQ_KEY_INV}
RABBITMQ_ROUTING_KEY_METRICS=#{RABBITMQ_KEY_MET}
RABBITMQ_ROUTING_KEY_ERROR=#{RABBITMQ_KEY_ERR}
AGENT_HOSTNAME_OVERRIDE=#{vm[:name]}
AGENT_INTERVAL_SEC=60
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
    end
  end
end
