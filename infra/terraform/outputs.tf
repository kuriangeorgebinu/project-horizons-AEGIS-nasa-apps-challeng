output "public_ip" {
  description = "Static public IP of the AEGIS server"
  value       = aws_lightsail_static_ip.app.ip_address
}

output "hostname" {
  description = "Free DNS name for the IP (sslip.io), used for the HTTPS certificate"
  value       = "${replace(aws_lightsail_static_ip.app.ip_address, ".", "-")}.sslip.io"
}

output "url" {
  description = "Public dashboard URL"
  value       = "https://${replace(aws_lightsail_static_ip.app.ip_address, ".", "-")}.sslip.io/"
}

output "ssh_private_key" {
  description = "Deploy key (used by the workflow only)"
  value       = tls_private_key.deploy.private_key_openssh
  sensitive   = true
}
