# AEGIS on one Amazon Lightsail server: Caddy (HTTPS + static dashboard) in front of the FastAPI backend.
# The app itself is shipped by the GitHub Actions workflow over SSH (deploy/remote-deploy.sh), so app updates
# don't recreate the server. This file only creates the infrastructure.

# SSH key used only by the deploy workflow. The private key is kept in the (private, encrypted) Terraform state.
resource "tls_private_key" "deploy" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_lightsail_key_pair" "deploy" {
  name       = "${var.name}-deploy"
  public_key = tls_private_key.deploy.public_key_openssh
}

resource "aws_lightsail_instance" "app" {
  name              = "${var.name}-app"
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.deploy.name
  user_data         = file("${path.module}/user_data.sh")
}

# Fixed public IP: survives server restarts and is free while attached
resource "aws_lightsail_static_ip" "app" {
  name = "${var.name}-ip"
}

resource "aws_lightsail_static_ip_attachment" "app" {
  static_ip_name = aws_lightsail_static_ip.app.name
  instance_name  = aws_lightsail_instance.app.name

  # The server is referenced by name, which stays the same when it is replaced (e.g. new user data), so
  # re-attach the static IP whenever the server itself is recreated
  lifecycle {
    replace_triggered_by = [aws_lightsail_instance.app.id, aws_lightsail_instance.app.created_at]
  }
}

# Firewall: SSH (deploys), HTTP (certificate challenge + redirect), HTTPS (the app)
resource "aws_lightsail_instance_public_ports" "app" {
  instance_name = aws_lightsail_instance.app.name

  # Same reason: a replaced server starts with Lightsail's default firewall, so re-apply the rules
  lifecycle {
    replace_triggered_by = [aws_lightsail_instance.app.id, aws_lightsail_instance.app.created_at]
  }

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
  }
  port_info {
    protocol  = "tcp"
    from_port = 80
    to_port   = 80
  }
  port_info {
    protocol  = "tcp"
    from_port = 443
    to_port   = 443
  }
}
