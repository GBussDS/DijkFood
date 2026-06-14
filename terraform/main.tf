terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# Conta secundária exclusivamente para o Amazon Bedrock
provider "aws" {
  alias   = "bedrock"
  region  = var.aws_region
  profile = var.bedrock_profile
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }
