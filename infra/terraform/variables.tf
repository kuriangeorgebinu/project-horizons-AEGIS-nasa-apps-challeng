variable "region" {
  description = "AWS region for the Lightsail server"
  type        = string
  default     = "us-east-1"
}

variable "availability_zone" {
  description = "Lightsail availability zone (must be in var.region)"
  type        = string
  default     = "us-east-1a"
}

variable "name" {
  description = "Name prefix for the Lightsail resources"
  type        = string
  default     = "aegis"
}

variable "bundle_id" {
  description = "Lightsail plan. small_3_0 = 2 GB RAM, 2 vCPU, 60 GB SSD, 3 TB transfer, public IPv4 ($12/month)"
  type        = string
  default     = "small_3_0"
}

variable "blueprint_id" {
  description = "Lightsail OS image"
  type        = string
  default     = "ubuntu_24_04"
}
