output "gateway_url" {
  value       = "https://${var.gateway_hostname}"
  description = "Base URL OpenCompany installs use. The admin and key console is <gateway_url>/ui."
}

output "alb_dns_name" {
  value       = module.litellm.alb_dns_name
  description = "Load balancer DNS name behind gateway_hostname."
}

output "master_key_secret_arn" {
  value       = module.litellm.master_key_secret_arn
  description = "Secret holding the master key. Used once for the first admin login; see README."
}

output "vendor_secret_arns" {
  value       = { for env_name, secret in aws_secretsmanager_secret.vendor : env_name => secret.arn }
  description = "Secrets to fill with vendor keys before the first full apply."
}

output "migration_run_command" {
  value       = module.litellm.migration_run_command
  description = "Re-runs the Prisma migration task by hand, if ever needed."
}

output "spendlogs_index_command" {
  # The module's bootstrap task runs psql as the master user with BOOTSTRAP_SQL
  # from its environment; this reuses it with the index SQL instead.
  value = format(
    "%s --overrides '%s'",
    replace(
      module.litellm.migration_run_command,
      "/--task-definition \\S+/",
      "--task-definition opencompany-litellm-${var.env}-bootstrap-db",
    ),
    jsonencode({
      containerOverrides = [{
        name        = "psql"
        environment = [{ name = "BOOTSTRAP_SQL", value = local.spendlogs_index_sql }]
      }]
    }),
  )
  description = "Run once after the first apply: builds the two SpendLogs indexes LiteLLM no longer creates (see README)."
}
