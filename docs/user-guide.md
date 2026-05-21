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

## Settings

The Settings page controls product name, company name, default timezone, data retention days, maintenance window, and an optional banner message.
