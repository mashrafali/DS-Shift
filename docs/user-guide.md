# User Guide

## Dashboard

The dashboard summarizes project count, discovered VMs, planned VMs, completed migrations, failed or blocked migrations, and overall progress.

## Migration Projects

Create projects with customer name, source platform, target platform, migration type, planned start date, cutover date, status, and notes.

Saved projects appear in the project list and can be reopened with the Edit action.
Planned start and cutover schedule use date-time selectors in the web UI.

## VM Inventory

Add VMs manually and associate them with a migration project. Capture CPU, memory, disk, operating system, IP address, owner, criticality, migration wave, and current workflow status.

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

Use `Validate` to test the platform API with its public SDK or API client. Use `Discover` to collect VM or instance inventory. Discovery results are listed on the Migration Engine page.

Host Connector discovery also creates or refreshes entries under `Hosts`.
Each host entry includes platform, capacity, endpoint, discovery time, and the
VMs currently reported by that host. Connector discovery shows progress and its
completion or failure result directly on the Connectors page.

Use `Delete` to remove a connector together with its discovery history and
discovered host inventory. DS Shift blocks deletion when a migration job still
references the connector.

## Hosts

The Hosts page is populated automatically by successful Host Connector
discovery. Re-running discovery updates the existing host record rather than
creating a duplicate, and refreshes its associated VM list.

## Service status

The Settings page shows the live state of each DS Shift container. Green `UP`
means Docker reports the container as running, yellow `RESTARTING` means Docker
is restarting it, and red `DOWN` means it is stopped, exited, or missing. The
panel refreshes every 10 seconds.

## Migration Engine

The Migration Engine page creates KVM-to-ESXi/vCenter test preflight jobs. Select a source KVM connector, a target ESXi/vCenter connector, the source VM name, and the target datastore. DS Shift validates the source connector, inspects the source VM, validates the target vCenter connector, records the generated runbook, and reports whether live conversion tools are available.

Live migration execution is intentionally not automatic in the MVP. Review the preflight result, verify credentials and rollback planning, and obtain explicit operational approval before enabling execution.

## Settings

The Settings page controls product name, company name, default timezone, data retention days, maintenance window, and an optional banner message.
Admins also use Settings to view the local user list and add users. Passwords are stored as backend PBKDF2 hashes.
