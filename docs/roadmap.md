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

- Controlled live execution for the implemented `virt-v2v` KVM-to-ESXi/vCenter preflight engine.
- VMware vCenter APIs.
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
