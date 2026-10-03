# OpenCompany on AWS (Terraform)

One login-gated EC2 instance running OpenCompany. File layout and workflow
follow the HashiCorp AWS get-started tutorial
(https://developer.hashicorp.com/terraform/tutorials/aws-get-started):
`terraform.tf` (providers), `main.tf`, `variables.tf`, `outputs.tf`, plus
`startup.sh.tftpl` (the cloud-init startup script template).

Settings are the ones validated on a live t3.micro in September 2026:

| Setting | Value | Why |
|---|---|---|
| Instance | `t3.micro`, 2 vCPU, 1 GiB | Smallest size the app runs on (backend ~200 MB + Temporal ~150 MB). 512 MB OOM-loops. |
| Credit mode | `standard` | Caps compute at the hourly rate under continuous CPU load. |
| Root disk | 10 GiB gp3, deleted with the instance | A fresh install uses ~4.5 GiB. |
| Image | Ubuntu 24.04 LTS (Canonical) | Python 3.12 inside the server's `<3.13` pin. |
| bun | installed by `install.sh` (the official bun installer, into `~ubuntu/.bun`) when none is present; no Node, no npm | The published package runs on bun: the `company` shim, the JS executor sidecar and the plugin CLIs (including the Cloudflare `cf` CLI: bun ignores its `engines.node >= 22` and runs its API commands; only cf's project commands, which load a `cloudflare.config.ts`, need Node 22.18+). systemd gets `$APP_HOME/.bun/bin` on PATH and `ExecStart=$APP_HOME/.bun/bin/company serve`. |
| Install | `install.sh` as `ubuntu`, no sudo | Root installs leave the venvs unusable by the login user (errors.md #16). |
| Runtime | `company serve` under systemd as `ubuntu` on port 5678 | The server rejects ports below 1024; 80/443 stay free for a TLS front door. |
| Security group | 22, 80, 443 and the app port from `allow_cidr` | Same shape as the gcp module. |

```bash
cd cli/terraform/aws
terraform init
terraform fmt
terraform validate
terraform apply -var key_name=<your-key-pair> -var opencompany_version=0.2.1 \
  -var-file=app_env.auto.tfvars.json
terraform output url
```

`opencompany_version` is the release tag without the `v`. The module was
validated on 0.1.1; the 0.2.x registry package currently fails `company serve`
on a fresh install (see [errors.md #25](../../../docs-internal/errors.md)), so
a VM deploy of 0.2.x does not come up until that is fixed.

`app_env` carries the owner login, fresh JWT/encryption keys, `PORT=5678`,
`DATA_DIR=/var/lib/opencompany` and `TEMPORAL_ENABLED=false`. Generate it with
`cli/commands/deploy/_secrets.py build_app_env(owner_email=..., owner_password=...,
port=5678)` and write it as `{"app_env": {...}}` to `app_env.auto.tfvars.json`
(gitignored) rather than typing it by hand. First boot takes about 10 minutes
on a t3.micro. Changing `app_env` or the boot script later does not recreate
the instance; the new user data only runs on a fresh one.

First boot fetches `https://opencompany.sh/install.sh` (override with
`install_sh_url`), and that script asks `https://opencompany.sh/version` for
the release unless `opencompany_version` is set. Both endpoints must be served
from the opencompany.sh site.

Uses the AWS CLI's default credential chain. `company deploy` cannot drive this
module yet; it needs an AWS entry in `cli/commands/deploy/providers`.

Cost at us-east-1 list prices: about $12/month fixed (instance, disk, public
IPv4), and CPU load cannot raise it. Egress past the free 100 GB/month is the
only open-ended line.
