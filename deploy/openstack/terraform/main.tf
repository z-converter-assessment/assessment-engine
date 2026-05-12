provider "openstack" {
  cloud = var.cloud_name
}

# --- Security Groups ---
# SG는 VM 간 통신 한정 — 외부 노출 없음 (사내망 사설 IP만). bastion·jump host의 22·8000·15672 접근은
# 사내망 CIDR 안에서 처리되며, OpenStack SG는 그 위에 한 겹 더 두는 형태.

resource "openstack_compute_secgroup_v2" "app" {
  name        = "${var.name_prefix}-app"
  description = "engine-app — 22(bastion SSH), 8000(web), 사내망에서만 접근 가정"

  rule {
    from_port   = 22
    to_port     = 22
    ip_protocol = "tcp"
    cidr        = "0.0.0.0/0"
  }

  rule {
    from_port   = 8000
    to_port     = 8000
    ip_protocol = "tcp"
    cidr        = "0.0.0.0/0"
  }
}

resource "openstack_compute_secgroup_v2" "db" {
  name        = "${var.name_prefix}-db"
  description = "engine-db — 5432 inbound from engine-app SG only"

  rule {
    from_port   = 22
    to_port     = 22
    ip_protocol = "tcp"
    cidr        = "0.0.0.0/0"
  }

  rule {
    from_port     = 5432
    to_port       = 5432
    ip_protocol   = "tcp"
    from_group_id = openstack_compute_secgroup_v2.app.id
  }
}

resource "openstack_compute_secgroup_v2" "mw" {
  name        = "${var.name_prefix}-mw"
  description = "engine-mw — 5672/6379 from engine-app SG, 15672 사내망"

  rule {
    from_port   = 22
    to_port     = 22
    ip_protocol = "tcp"
    cidr        = "0.0.0.0/0"
  }

  rule {
    from_port     = 5672
    to_port       = 5672
    ip_protocol   = "tcp"
    from_group_id = openstack_compute_secgroup_v2.app.id
  }

  rule {
    from_port     = 6379
    to_port       = 6379
    ip_protocol   = "tcp"
    from_group_id = openstack_compute_secgroup_v2.app.id
  }

  rule {
    from_port   = 15672
    to_port     = 15672
    ip_protocol = "tcp"
    cidr        = "0.0.0.0/0"
  }
}

# --- VMs ---

resource "openstack_compute_instance_v2" "db" {
  name            = "${var.name_prefix}-db"
  image_name      = var.image_name
  flavor_name     = var.db_flavor
  key_pair        = var.keypair_name
  security_groups = [openstack_compute_secgroup_v2.db.name]

  network {
    uuid = var.network_id
  }
}

resource "openstack_compute_instance_v2" "mw" {
  name            = "${var.name_prefix}-mw"
  image_name      = var.image_name
  flavor_name     = var.mw_flavor
  key_pair        = var.keypair_name
  security_groups = [openstack_compute_secgroup_v2.mw.name]

  network {
    uuid = var.network_id
  }
}

resource "openstack_compute_instance_v2" "app" {
  name            = "${var.name_prefix}-app"
  image_name      = var.image_name
  flavor_name     = var.app_flavor
  key_pair        = var.keypair_name
  security_groups = [openstack_compute_secgroup_v2.app.name]

  network {
    uuid = var.network_id
  }
}
