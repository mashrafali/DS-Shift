# Roadmap

## Security

- Local authentication.
- RBAC.
- Audit log.
- API token management.
- Enterprise identity provider integration.
- Vault-backed platform credential storage.

## Discovery Connectors

- Extend VMware vCenter discovery beyond the pyVmomi connector inventory into migration-specific collection.
- Extend KVM/libvirt discovery beyond Paramiko and `virsh` inventory into migration-specific collection.
- Add richer inventory attributes and pagination controls for AWS, Google Cloud, Azure, and Nutanix.
- Add credential-vault integration and connector-level secret rotation.

## Migration Execution Integrations

- Supported KVM disk conversion, OVF packaging, upload, and VMware vCenter
  import.
- Cross-account AWS image and snapshot sharing.
- Cross-project GCP image permissions and staging.
- Cross-subscription Azure disk copy and staging.
- Cross-provider cloud object-storage transfer pipelines.
- Google Cloud Migrate to Virtual Machines.
- AWS Application Migration Service.
- Azure Migrate.
- Nutanix Move.
- OpenText, Carbonite, and replication-based migration tools.
- Agent-based replication tools.
- Backup-and-restore based migration workflows.

## Planning Enhancements

- Readiness scoring.
- Dependency mapping.
- Application grouping.
- Wave capacity planning.
- Cutover runbooks.
- Approval workflows.
- Role-based dashboards.
- Advanced report exports.
