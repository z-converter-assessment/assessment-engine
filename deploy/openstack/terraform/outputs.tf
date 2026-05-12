output "engine_db_ip" {
  value       = openstack_compute_instance_v2.db.access_ip_v4
  description = "engine-db 사설 IP — Ansible inventory가 본 값을 ${ENGINE_DB_HOST}로 주입"
}

output "engine_mw_ip" {
  value       = openstack_compute_instance_v2.mw.access_ip_v4
  description = "engine-mw 사설 IP — ${ENGINE_MW_HOST}"
}

output "engine_app_ip" {
  value       = openstack_compute_instance_v2.app.access_ip_v4
  description = "engine-app 사설 IP — Web UI 접속용 (사내망)"
}
