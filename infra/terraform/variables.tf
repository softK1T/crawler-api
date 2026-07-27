variable "hcloud_token" {
  type        = string
  sensitive   = true
  description = "Hetzner Cloud API token"
}

variable "ssh_key_id" {
  type        = string
  description = "SSH key fingerprint registered in Hetzner Cloud"
}

variable "environment" {
  type        = string
  default     = "staging"
  description = "Deployment environment (staging / production)"
}

variable "server_type" {
  type        = string
  default     = "cx31"
  description = "Hetzner server type (cx31 = 8GB RAM, 2 vCPU)"
}

variable "location" {
  type        = string
  default     = "nbg1"
  description = "Hetzner datacenter location"
}

variable "volume_size_gb" {
  type        = number
  default     = 50
  description = "Attached volume size in GB"
}

variable "admin_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "CIDR range allowed SSH access (restrict in production)"
}
