terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # State lives in a private, versioned, encrypted S3 bucket. use_lockfile = native S3 state locking
  # (Terraform 1.10+), so no DynamoDB table is needed. The bucket is created once, outside Terraform.
  backend "s3" {
    bucket       = "aegis-tfstate-522921672742"
    key          = "aegis/lightsail/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "AEGIS"
      ManagedBy = "terraform"
      Repo      = "kuriangeorgebinu/project-horizons-AEGIS-nasa-apps-challeng"
    }
  }
}
