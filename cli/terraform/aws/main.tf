provider "aws" {
  region = var.region
}

# Ubuntu 24.04 LTS (Canonical): Python 3.12 is inside the server's
# ">=3.11,<3.13" pin, so uv needs no managed download. install.sh installs
# bun itself; nothing on the VM needs Node.
data "aws_ami" "ubuntu" {
  most_recent = true

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  owners = ["099720109477"] # Canonical
}

resource "aws_security_group" "app_server" {
  name        = "${var.instance_name}-app"
  description = "OpenCompany: SSH, web, and the app port"

  dynamic "ingress" {
    for_each = toset(distinct([22, 80, 443, var.port]))
    content {
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = [var.allow_cidr]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.instance_name}-app"
  }
}

resource "aws_instance" "app_server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.key_name != "" ? var.key_name : null
  vpc_security_group_ids = [aws_security_group.app_server.id]

  # Cost cap: throttle to baseline when CPU credits run out instead of
  # billing surplus credits, so compute never exceeds the hourly rate.
  credit_specification {
    cpu_credits = "standard"
  }

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/startup.sh.tftpl", {
    instance_name  = var.instance_name
    version        = var.opencompany_version
    install_sh_url = var.install_sh_url
    app_env        = var.app_env
  })

  tags = {
    Name = var.instance_name
  }
}
