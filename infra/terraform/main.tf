resource "hcloud_server" "crawler_api" {
  name        = "crawler-api-${var.environment}"
  server_type = var.server_type
  image       = "ubuntu-22.04"
  location    = var.location

  labels = {
    environment = var.environment
    project     = "crawler-api"
  }

  ssh_keys = [var.ssh_key_id]

  user_data = <<-EOF
    #!/bin/bash
    set -e
    apt-get update && apt-get install -y docker.io docker-compose curl
    systemctl enable docker && systemctl start docker
    mkdir -p /opt/crawler-api
    cd /opt/crawler-api
    git clone https://github.com/softK1T/crawler-api.git .
    cp .env.example .env
    docker compose up -d
  EOF
}

resource "hcloud_volume" "data" {
  name      = "crawler-data-${var.environment}"
  size      = var.volume_size_gb
  server_id = hcloud_server.crawler_api.id
  automount = true
  format    = "ext4"
}

resource "hcloud_firewall" "crawler" {
  name = "crawler-api-${var.environment}"

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = [var.admin_cidr]
  }

  apply_to {
    server = hcloud_server.crawler_api.id
  }
}
