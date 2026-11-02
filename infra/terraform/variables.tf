variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "simstudio"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "ecs_ami_id" {
  type        = string
  description = "ECS-optimized Amazon Linux AMI for sim workers. Replace before apply."
  default     = "ami-0123456789abcdef0"
}

variable "worker_instance_type" {
  type    = string
  default = "c6i.xlarge"
}

variable "worker_desired_capacity" {
  type    = number
  default = 1
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "web_cpu" {
  type    = number
  default = 512
}

variable "web_memory" {
  type    = number
  default = 1024
}

variable "solver_image_tag" {
  type    = string
  default = "latest"
}
