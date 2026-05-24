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

Host Connectors store KVM, VMware ESXi / vCenter, and Nutanix AHV endpoint metadata.
Cloud Connectors store GCP, AWS, Azure, and other cloud endpoint metadata.
Passwords are not stored directly; use a credential reference placeholder until vault integration is implemented.

Use `Discover` on a KVM connector to run SSH and `virsh` discovery. Use `Discover` on a VMware ESXi / vCenter connector to run VMware SDK discovery. Discovery results are listed on the Migration Engine page.

## Migration Engine

The Migration Engine page creates KVM-to-ESXi/vCenter test preflight jobs. Select a source KVM connector, a target ESXi/vCenter connector, the source VM name, and the target datastore. DS Replace validates the source connector, inspects the source VM, validates the target vCenter connector, records the generated runbook, and reports whether live conversion tools are available.

Live migration execution is intentionally not automatic in the MVP. Review the preflight result, verify credentials and rollback planning, and obtain explicit operational approval before enabling execution.

## Settings

The Settings page controls product name, company name, default timezone, data retention days, maintenance window, and an optional banner message.
Admins also use Settings to view the local user list and add users. Passwords are stored as backend PBKDF2 hashes.
