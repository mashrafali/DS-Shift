# User Guide

## Dashboard

The dashboard summarizes migration plan count, discovered VMs, planned VMs,
completed migrations, failed or blocked migrations, and overall progress. The
dashboard table lists current migration plans and their execution readiness.

## VM Inventory

VM Inventory is synchronized automatically from every successful connector
discovery. It records source connector and host, platform, operating system,
CPU, memory, disk, IP address, and workflow status.

Use the search bar to filter by VM name, connector, host, platform, operating
system, IP address, or migration status. Select All applies to the filtered
results while preserving the single-source-connector selection rule.

Select VMs with checkboxes. A migration plan can contain VMs from one source
connector so one execution adapter and credential context apply to the plan.

## Migration Plans

Select VMs in VM Inventory and choose `Create Migration Plan`. Specify a plan
name, target connector, provider execution options, and notes.

The Migration Plans page provides `Preflight`, admin-only `Launch`, `Details`,
and `Delete`. Preflight performs non-destructive checks. Launch requires the
operator to type the exact plan name and submits a live job to one of the three
Spark Engine workers.

Executable Spark adapters are AWS-to-AWS within one account, GCP-to-GCP using
machine images, Azure-to-Azure within one subscription, and KVM-to-KVM using
libvirt migration. Other combinations are rejected. Live launch is disabled
unless the deployment explicitly sets `SPARK_LIVE_EXECUTION_ENABLED=true`.
Plans with queued or running jobs cannot be deleted.

## Migration Workflow

Supported VM states:

- Discovered
- Assessed
- Ready for migration
- Replication prepared
- Migration in progress
- Cutover scheduled
- Cutover completed
- Validation completed
- Failed
- Rolled back
- Blocked

Changing status records a history entry in the backend.

## Reports

Use the Reports page to export a basic VM readiness CSV from the current inventory.

## Connectors

`Connectors` is the first navigation entry. Select `Host Connectors` or `Cloud Connectors` to list existing connectors or create a new one.

Host Connectors support KVM, VMware ESXi / vCenter, and Nutanix AHV.
Cloud Connectors support Amazon Web Services, Google Cloud Platform, and Microsoft Azure.
Passwords and cloud credential JSON are not stored in PostgreSQL; connector records use `env:` references to secrets supplied to the relevant engine container.

Use `Validate` to test the platform API with its public SDK or API client. Use
`Discover` to collect VM or instance inventory. Successful discovery
automatically synchronizes VM Inventory.

Host Connector discovery also creates or refreshes entries under `Hosts`.
Each host entry includes platform, capacity, endpoint, discovery time, and the
VMs currently reported by that host. Connector discovery shows progress and its
completion or failure result directly on the Connectors page.

Use `Delete` to remove a connector together with its discovery history and
discovered host inventory. DS Shift blocks deletion when a migration job still
references the connector.

## Hosts

The Hosts page is populated automatically by successful Host Connector
discovery. The first page lists hosts only and identifies the connector used
for each host. Select a host or `View VMs` to open its VM inventory in a
separate detail window, including the OS reported by the host, CPU, memory,
disk, IP address, and power state. Re-running discovery updates the existing
host record rather than creating a duplicate.

## Service status

The Settings page shows the live state of each DS Shift service. Green `UP`
means Docker reports the container as running, yellow `RESTARTING` means Docker
is restarting it, and red `DOWN` means it is stopped, exited, or missing. The
panel refreshes every 10 seconds. Spark Engine reports aggregate replica state,
including how many of its three replicas are running.

## Settings

The Settings page controls product name, company name, default timezone, data retention days, maintenance window, and an optional banner message.
Admins also use Settings to view the local user list and add users. Passwords are stored as backend PBKDF2 hashes.
