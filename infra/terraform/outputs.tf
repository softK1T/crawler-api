output "server_ip" {
  value       = hcloud_server.crawler_api.ipv4_address
  description = "Public IPv4 address of the crawler-api server"
}

output "firewall_id" {
  value       = hcloud_firewall.crawler.id
  description = "Firewall resource ID"
}

output "volume_id" {
  value       = hcloud_volume.data.id
  description = "Attached volume resource ID"
}
