import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowRightLeft,
  Building2,
  CalendarClock,
  Cloud,
  FileText,
  Gauge,
  HardDrive,
  KeyRound,
  Layers,
  LogOut,
  Network,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  ServerCog,
  Settings,
  UserPlus,
  Users,
} from 'lucide-react';
import './styles.css';

const tokenKey = 'ds_replace_token';

const migrationTypes = {
  'Lift and shift': 'Move the VM with minimal redesign. Best for fast relocation and low application change.',
  'Cold migration': 'Power off, copy or convert, then start on the target. Simpler but requires downtime.',
  'Replication assisted': 'Use replication or backup tooling before cutover. Better for larger systems and shorter outage windows.',
  'Conversion based': 'Use tools such as virt-v2v or vendor conversion APIs where disk or driver changes are required.',
  'Cloud rehost': 'Move into a cloud IaaS target such as GCP, AWS, or Azure while preserving the VM operating model.',
  'Any-to-any workflow': 'Generic planning workflow when source and target are not yet finalized.',
};

const platforms = ['KVM', 'VMware ESXi / vCenter', 'Nutanix AHV', 'Google Cloud Platform', 'AWS', 'Azure', 'Other'];
const hostConnectorTypes = ['KVM', 'VMware ESXi / vCenter', 'Nutanix AHV'];
const cloudConnectorTypes = ['Google Cloud Platform', 'AWS', 'Azure', 'Other Cloud'];
const statuses = ['Discovered', 'Assessed', 'Ready for migration', 'Replication prepared', 'Migration in progress', 'Cutover scheduled', 'Cutover completed', 'Validation completed', 'Failed', 'Rolled back', 'Blocked'];

const blankProject = {
  project_name: '',
  customer_name: '',
  source_platform: 'KVM',
  target_platform: 'VMware ESXi / vCenter',
  migration_type: 'Lift and shift',
  planned_start_date: '',
  planned_cutover_date: '',
  status: 'Planning',
  notes: '',
};

const blankVm = {
  project_id: 1,
  vm_name: '',
  source_platform: 'KVM',
  target_platform: 'VMware ESXi / vCenter',
  cpu: 2,
  memory_gb: 4,
  disk_gb: 80,
  os_type: 'Linux',
  ip_address: '',
  application_owner: '',
  criticality: 'Medium',
  migration_wave: 'Wave 1',
  current_status: 'Discovered',
};

const blankConnector = {
  name: '',
  connector_category: 'host',
  connector_type: 'KVM',
  endpoint: '',
  port: 443,
  username: '',
  credential_reference: '',
  environment: 'Lab',
  status: 'Not validated',
  notes: '',
};

const blankSettings = {
  product_name: 'DS Replace',
  company_name: 'Defined Solutions',
  default_timezone: 'Asia/Riyadh',
  retention_days: 365,
  maintenance_window: '',
  banner_message: '',
};

const blankUser = {
  username: '',
  password: '',
  role: 'operator',
  is_active: 'true',
};

const blankMigrationJob = {
  source_connector_id: '',
  target_connector_id: '',
  vm_name: '',
  target_name: '',
  target_datastore: '',
  migration_type: 'KVM to ESXi',
};

function App() {
  const [token, setToken] = useState(localStorage.getItem(tokenKey) || '');
  const [user, setUser] = useState(null);
  const [active, setActive] = useState('dashboard');
  const [summary, setSummary] = useState(null);
  const [projects, setProjects] = useState([]);
  const [vms, setVms] = useState([]);
  const [waves, setWaves] = useState([]);
  const [connectors, setConnectors] = useState([]);
  const [users, setUsers] = useState([]);
  const [discoveryRuns, setDiscoveryRuns] = useState([]);
  const [migrationJobs, setMigrationJobs] = useState([]);
  const [settings, setSettings] = useState(blankSettings);
  const [projectForm, setProjectForm] = useState(blankProject);
  const [editingProjectId, setEditingProjectId] = useState(null);
  const [vmForm, setVmForm] = useState(blankVm);
  const [connectorForm, setConnectorForm] = useState(blankConnector);
  const [userForm, setUserForm] = useState(blankUser);
  const [migrationJobForm, setMigrationJobForm] = useState(blankMigrationJob);
  const [loginForm, setLoginForm] = useState({ username: 'admin', password: '' });
  const [error, setError] = useState('');

  const api = async (path, options = {}) => {
    const response = await fetch(`/api${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    if (response.status === 401) {
      localStorage.removeItem(tokenKey);
      setToken('');
      setUser(null);
      throw new Error('Authentication required');
    }
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    if (response.status === 204) return null;
    return response.json();
  };

  const load = async () => {
    if (!token) return;
    setError('');
    try {
      const [me, dashboard, projectRows, vmRows, waveRows, connectorRows, discoveryRows, migrationRows, appSettings] = await Promise.all([
        api('/auth/me'),
        api('/dashboard'),
        api('/projects'),
        api('/vms'),
        api('/waves'),
        api('/connectors'),
        api('/discovery-runs'),
        api('/migration-jobs'),
        api('/settings'),
      ]);
      const userRows = me.role === 'admin' ? await api('/users') : [];
      setUser(me);
      setUsers(userRows);
      setSummary(dashboard);
      setProjects(projectRows);
      setVms(vmRows);
      setWaves(waveRows);
      setConnectors(connectorRows);
      setDiscoveryRuns(discoveryRows);
      setMigrationJobs(migrationRows);
      setSettings(appSettings);
      if (projectRows[0]) setVmForm((f) => ({ ...f, project_id: projectRows[0].id }));
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => { load(); }, [token]);

  const login = async (event) => {
    event.preventDefault();
    setError('');
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loginForm),
      });
      if (!response.ok) throw new Error('Invalid username or password');
      const data = await response.json();
      localStorage.setItem(tokenKey, data.access_token);
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } catch (err) {
      setError(err.message);
    }
  };

  const logout = async () => {
    try {
      if (token) await api('/auth/logout', { method: 'POST' });
    } catch (_) {
      // Session may already be expired.
    }
    localStorage.removeItem(tokenKey);
    setToken('');
    setUser(null);
  };

  const saveProject = async (event) => {
    event.preventDefault();
    const payload = { ...projectForm };
    const method = editingProjectId ? 'PUT' : 'POST';
    const path = editingProjectId ? `/projects/${editingProjectId}` : '/projects';
    await api(path, { method, body: JSON.stringify(payload) });
    setProjectForm(blankProject);
    setEditingProjectId(null);
    await load();
  };

  const editProject = (project) => {
    setProjectForm({
      project_name: project.project_name,
      customer_name: project.customer_name,
      source_platform: project.source_platform,
      target_platform: project.target_platform,
      migration_type: project.migration_type,
      planned_start_date: project.planned_start_date || '',
      planned_cutover_date: project.planned_cutover_date || '',
      status: project.status,
      notes: project.notes || '',
    });
    setEditingProjectId(project.id);
  };

  const createVm = async (event) => {
    event.preventDefault();
    await api('/vms', { method: 'POST', body: JSON.stringify({ ...vmForm, project_id: Number(vmForm.project_id), cpu: Number(vmForm.cpu), memory_gb: Number(vmForm.memory_gb), disk_gb: Number(vmForm.disk_gb) }) });
    setVmForm((f) => ({ ...blankVm, project_id: f.project_id }));
    await load();
  };

  const saveConnector = async (event) => {
    event.preventDefault();
    await api('/connectors', { method: 'POST', body: JSON.stringify({ ...connectorForm, port: Number(connectorForm.port) || null }) });
    setConnectorForm(blankConnector);
    await load();
  };

  const saveSettings = async (event) => {
    event.preventDefault();
    await api('/settings', { method: 'PUT', body: JSON.stringify({ ...settings, retention_days: Number(settings.retention_days) }) });
    await load();
  };

  const saveUser = async (event) => {
    event.preventDefault();
    await api('/users', { method: 'POST', body: JSON.stringify(userForm) });
    setUserForm(blankUser);
    await load();
  };

  const discoverConnector = async (connector, projectId = '') => {
    await api(`/connectors/${connector.id}/discover`, {
      method: 'POST',
      body: JSON.stringify({ import_to_project_id: projectId ? Number(projectId) : null, target_platform: 'Unassigned' }),
    });
    await load();
  };

  const createMigrationJob = async (event) => {
    event.preventDefault();
    await api('/migration-jobs', {
      method: 'POST',
      body: JSON.stringify({
        ...migrationJobForm,
        source_connector_id: Number(migrationJobForm.source_connector_id),
        target_connector_id: Number(migrationJobForm.target_connector_id),
      }),
    });
    setMigrationJobForm(blankMigrationJob);
    await load();
  };

  const changeStatus = async (vm, status) => {
    await api(`/vms/${vm.id}/status`, { method: 'PATCH', body: JSON.stringify({ status, note: 'Updated from DS Replace dashboard' }) });
    await load();
  };

  const csv = useMemo(() => {
    const rows = [['VM Name', 'Source', 'Target', 'CPU', 'Memory GB', 'Disk GB', 'Criticality', 'Status']];
    vms.forEach((vm) => rows.push([vm.vm_name, vm.source_platform, vm.target_platform, vm.cpu, vm.memory_gb, vm.disk_gb, vm.criticality, vm.current_status]));
    return rows.map((r) => r.map((c) => `"${String(c ?? '').replaceAll('"', '""')}"`).join(',')).join('\n');
  }, [vms]);

  if (!token) return <Login form={loginForm} setForm={setLoginForm} submit={login} error={error} />;

  const nav = [
    ['dashboard', Gauge, 'Dashboard'],
    ['projects', Layers, 'Projects'],
    ['inventory', HardDrive, 'VM Inventory'],
    ['hosts', ServerCog, 'Host Connectors'],
    ['clouds', Cloud, 'Cloud Connectors'],
    ['engine', ArrowRightLeft, 'Migration Engine'],
    ['waves', CalendarClock, 'Waves'],
    ['reports', FileText, 'Reports'],
    ['settings', Settings, 'Settings'],
  ];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><DcMigrationMark /></div>
          <div><strong>{settings.product_name || 'DS Replace'}</strong><span>{settings.company_name || 'Defined Solutions'}</span></div>
        </div>
        <nav>
          {nav.map(([key, Icon, label]) => (
            <button key={key} className={active === key ? 'active' : ''} onClick={() => setActive(key)} title={label}>
              <span className="nav-icon"><Icon size={18} /></span>{label}
            </button>
          ))}
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p>{settings.company_name || 'Defined Solutions'} / {user?.username}</p>
            <h1>{titleFor(active)}</h1>
          </div>
          <div className="toolbar">
            <button className="icon-button" onClick={load} title="Refresh data"><RefreshCw size={18} /></button>
            <button className="icon-button" onClick={logout} title="Log out"><LogOut size={18} /></button>
          </div>
        </header>
        {settings.banner_message && <div className="notice"><Building2 size={20} /> {settings.banner_message}</div>}
        {error && <div className="alert">API error: {error}</div>}

        {active === 'dashboard' && <Dashboard summary={summary} vms={vms} connectors={connectors} />}
        {active === 'projects' && <Projects projects={projects} form={projectForm} setForm={setProjectForm} save={saveProject} editProject={editProject} editingProjectId={editingProjectId} cancel={() => { setProjectForm(blankProject); setEditingProjectId(null); }} />}
        {active === 'inventory' && <Inventory vms={vms} projects={projects} form={vmForm} setForm={setVmForm} create={createVm} changeStatus={changeStatus} />}
        {active === 'hosts' && <Connectors title="Host Connector" category="host" rows={connectors.filter((c) => c.connector_category === 'host')} form={connectorForm} setForm={setConnectorForm} save={saveConnector} types={hostConnectorTypes} discover={discoverConnector} projects={projects} />}
        {active === 'clouds' && <Connectors title="Cloud Connector" category="cloud" rows={connectors.filter((c) => c.connector_category === 'cloud')} form={connectorForm} setForm={setConnectorForm} save={saveConnector} types={cloudConnectorTypes} discover={discoverConnector} projects={projects} />}
        {active === 'engine' && <MigrationEngine connectors={connectors} form={migrationJobForm} setForm={setMigrationJobForm} save={createMigrationJob} jobs={migrationJobs} discoveryRuns={discoveryRuns} />}
        {active === 'waves' && <Waves waves={waves} />}
        {active === 'reports' && <Reports csv={csv} vms={vms} />}
        {active === 'settings' && <SettingsView settings={settings} setSettings={setSettings} save={saveSettings} user={user} users={users} userForm={userForm} setUserForm={setUserForm} saveUser={saveUser} />}
      </main>
    </div>
  );
}

function Login({ form, setForm, submit, error }) {
  return <div className="login-screen"><form className="login-panel" onSubmit={submit}><div className="login-logo"><DcMigrationMark /></div><h1>DS Replace</h1><p>Defined Solutions migration command center</p>{error && <div className="alert">{error}</div>}<Input label="Username" value={form.username} onChange={(v) => setForm({ ...form, username: v })} required /><Input label="Password" type="password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} required /><button className="primary"><KeyRound size={16} /> Sign in</button></form></div>;
}

function titleFor(active) {
  return ({ dashboard: 'Migration Command Center', projects: 'Saved Migration Projects', inventory: 'VM Inventory', hosts: 'Host Connectors', clouds: 'Cloud Connectors', engine: 'Discovery and Migration Engine', waves: 'Migration Waves', reports: 'Reports', settings: 'Settings Control' })[active];
}

function Dashboard({ summary, vms, connectors }) {
  const cards = [
    ['Total projects', summary?.total_projects ?? 0, Layers],
    ['VMs discovered', summary?.vms_discovered ?? 0, HardDrive],
    ['VMs planned', summary?.vms_planned ?? 0, CalendarClock],
    ['VMs migrated', summary?.vms_migrated ?? 0, ArrowRightLeft],
    ['Failed or blocked', summary?.vms_failed_or_blocked ?? 0, Network],
    ['Connectors', connectors.length, ServerCog],
  ];
  return <section><div className="metric-grid">{cards.map(([label, value, Icon]) => <div className="metric" key={label}><Icon size={22} /><span>{label}</span><strong>{value}</strong></div>)}</div><StatusBoard vms={vms} /></section>;
}

function Projects({ projects, form, setForm, save, editProject, editingProjectId, cancel }) {
  const selectedTip = migrationTypes[form.migration_type] || migrationTypes['Any-to-any workflow'];
  return <section className="split"><FormPanel title={editingProjectId ? 'Edit saved project' : 'Save migration project'} onSubmit={save}><Input label="Project name" value={form.project_name} onChange={(v) => setForm({ ...form, project_name: v })} required /><Input label="Customer name" value={form.customer_name} onChange={(v) => setForm({ ...form, customer_name: v })} required /><Select label="Source platform" value={form.source_platform} options={platforms} onChange={(v) => setForm({ ...form, source_platform: v })} /><Select label="Target platform" value={form.target_platform} options={platforms} onChange={(v) => setForm({ ...form, target_platform: v })} /><Select label="Migration type" value={form.migration_type} options={Object.keys(migrationTypes)} onChange={(v) => setForm({ ...form, migration_type: v })} /><div className="tip">{selectedTip}</div><Input label="Planned start" type="datetime-local" value={form.planned_start_date} onChange={(v) => setForm({ ...form, planned_start_date: v })} /><Input label="Cutover schedule" type="datetime-local" value={form.planned_cutover_date} onChange={(v) => setForm({ ...form, planned_cutover_date: v })} /><Input label="Status" value={form.status} onChange={(v) => setForm({ ...form, status: v })} /><TextArea label="Notes" value={form.notes} onChange={(v) => setForm({ ...form, notes: v })} /><div className="button-row"><button className="primary"><Save size={16} /> {editingProjectId ? 'Save changes' : 'Save project'}</button>{editingProjectId && <button className="secondary" type="button" onClick={cancel}>Cancel</button>}</div></FormPanel><div className="table-wrap"><table><thead><tr><th>Project</th><th>Customer</th><th>Source</th><th>Target</th><th>Start</th><th>Cutover</th><th></th></tr></thead><tbody>{projects.map((p) => <tr key={p.id}><td>{p.project_name}</td><td>{p.customer_name}</td><td>{p.source_platform}</td><td>{p.target_platform}</td><td>{formatDateTime(p.planned_start_date)}</td><td>{formatDateTime(p.planned_cutover_date)}</td><td><button className="mini" onClick={() => editProject(p)}>Edit</button></td></tr>)}</tbody></table></div></section>;
}

function Inventory({ vms, projects, form, setForm, create, changeStatus }) {
  return <section className="split"><FormPanel title="Add VM manually" onSubmit={create}><Select label="Project" value={form.project_id} options={projects.map((p) => [p.id, p.project_name])} onChange={(v) => setForm({ ...form, project_id: v })} /><Input label="VM name" value={form.vm_name} onChange={(v) => setForm({ ...form, vm_name: v })} required /><Select label="Source" value={form.source_platform} options={platforms} onChange={(v) => setForm({ ...form, source_platform: v })} /><Select label="Target" value={form.target_platform} options={platforms} onChange={(v) => setForm({ ...form, target_platform: v })} /><Input label="CPU" type="number" value={form.cpu} onChange={(v) => setForm({ ...form, cpu: v })} /><Input label="Memory GB" type="number" value={form.memory_gb} onChange={(v) => setForm({ ...form, memory_gb: v })} /><Input label="Disk GB" type="number" value={form.disk_gb} onChange={(v) => setForm({ ...form, disk_gb: v })} /><Input label="Owner" value={form.application_owner} onChange={(v) => setForm({ ...form, application_owner: v })} /><button className="primary"><Plus size={16} /> Add VM</button></FormPanel><div className="table-wrap"><table><thead><tr><th>VM</th><th>Source</th><th>Target</th><th>Size</th><th>Status</th><th>Change status</th></tr></thead><tbody>{vms.map((vm) => <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.source_platform}</td><td>{vm.target_platform}</td><td>{vm.cpu} CPU / {vm.memory_gb} GB</td><td><Badge value={vm.current_status} /></td><td><select value={vm.current_status} onChange={(e) => changeStatus(vm, e.target.value)}>{statuses.map((s) => <option key={s}>{s}</option>)}</select></td></tr>)}</tbody></table></div></section>;
}

function Connectors({ title, category, rows, form, setForm, save, types, discover, projects }) {
  const scopedForm = form.connector_category === category ? form : { ...blankConnector, connector_category: category, connector_type: types[0] };
  const update = (patch) => setForm({ ...scopedForm, ...patch, connector_category: category });
  return <section className="split"><FormPanel title={`Add ${title}`} onSubmit={save}><Select label="Type" value={scopedForm.connector_type} options={types} onChange={(v) => update({ connector_type: v })} /><Input label="Connector name" value={scopedForm.name} onChange={(v) => update({ name: v })} required /><Input label="Endpoint / API URL" value={scopedForm.endpoint} onChange={(v) => update({ endpoint: v })} /><Input label="Port" type="number" value={scopedForm.port || ''} onChange={(v) => update({ port: v })} /><Input label="Username" value={scopedForm.username || ''} onChange={(v) => update({ username: v })} /><Input label="Credential reference" value={scopedForm.credential_reference || ''} onChange={(v) => update({ credential_reference: v })} /><Input label="Environment" value={scopedForm.environment || ''} onChange={(v) => update({ environment: v })} /><TextArea label="Notes" value={scopedForm.notes || ''} onChange={(v) => update({ notes: v })} /><div className="tip">Discovery engines run real connector checks. KVM uses SSH and virsh. vCenter uses govc when installed and configured.</div><button className="primary"><Plus size={16} /> Add connector</button></FormPanel><div className="table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Endpoint</th><th>Credential</th><th>Status</th><th>Discovery</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.name}</td><td>{row.connector_type}</td><td>{row.endpoint || '-'}</td><td>{row.credential_reference || '-'}</td><td>{row.status}</td><td><button className="mini" onClick={() => discover(row)}><Search size={14} /> Discover</button></td></tr>)}</tbody></table></div></section>;
}

function MigrationEngine({ connectors, form, setForm, save, jobs, discoveryRuns }) {
  const hostConnectors = connectors.filter((c) => c.connector_category === 'host');
  const kvmConnectors = hostConnectors.filter((c) => c.connector_type === 'KVM');
  const vmwareConnectors = hostConnectors.filter((c) => c.connector_type.includes('VMware') || c.connector_type.includes('vCenter'));
  return <section className="split"><FormPanel title="KVM to ESXi migration preflight" onSubmit={save}><Select label="Source KVM connector" value={form.source_connector_id} options={kvmConnectors.map((c) => [c.id, c.name])} onChange={(v) => setForm({ ...form, source_connector_id: v })} /><Select label="Target ESXi / vCenter connector" value={form.target_connector_id} options={vmwareConnectors.map((c) => [c.id, c.name])} onChange={(v) => setForm({ ...form, target_connector_id: v })} /><Input label="Source VM name" value={form.vm_name} onChange={(v) => setForm({ ...form, vm_name: v })} required /><Input label="Target VM name" value={form.target_name} onChange={(v) => setForm({ ...form, target_name: v })} /><Input label="Target datastore" value={form.target_datastore} onChange={(v) => setForm({ ...form, target_datastore: v })} /><div className="tip">This creates a real migration runbook and checks local engine dependencies. Live migration execution remains approval-gated.</div><button className="primary"><Play size={16} /> Create preflight job</button></FormPanel><div className="stack"><Table rows={jobs} columns={['vm_name', 'migration_type', 'status', 'message']} /><Table rows={discoveryRuns} columns={['connector_id', 'status', 'message']} /></div></section>;
}

function Waves({ waves }) {
  return <section><Table rows={waves} columns={['wave_name', 'project_id', 'planned_window', 'status', 'notes']} /></section>;
}

function Reports({ csv, vms }) {
  const download = () => {
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ds-replace-vm-readiness.csv';
    a.click();
    URL.revokeObjectURL(url);
  };
  return <section><button className="primary" onClick={download}><FileText size={16} /> Export VM readiness CSV</button><StatusBoard vms={vms} /></section>;
}

function SettingsView({ settings, setSettings, save, user, users, userForm, setUserForm, saveUser }) {
  return <section className="split"><div className="stack"><FormPanel title="Application settings" onSubmit={save}><Input label="Product name" value={settings.product_name || ''} onChange={(v) => setSettings({ ...settings, product_name: v })} /><Input label="Company name" value={settings.company_name || ''} onChange={(v) => setSettings({ ...settings, company_name: v })} /><Input label="Default timezone" value={settings.default_timezone || ''} onChange={(v) => setSettings({ ...settings, default_timezone: v })} /><Input label="Retention days" type="number" value={settings.retention_days || 365} onChange={(v) => setSettings({ ...settings, retention_days: v })} /><Input label="Maintenance window" value={settings.maintenance_window || ''} onChange={(v) => setSettings({ ...settings, maintenance_window: v })} /><TextArea label="Banner message" value={settings.banner_message || ''} onChange={(v) => setSettings({ ...settings, banner_message: v })} /><button className="primary"><Save size={16} /> Save settings</button></FormPanel><FormPanel title="Add local user" onSubmit={saveUser}><Input label="Username" value={userForm.username} onChange={(v) => setUserForm({ ...userForm, username: v })} required /><Input label="Password" type="password" value={userForm.password} onChange={(v) => setUserForm({ ...userForm, password: v })} required /><Select label="Role" value={userForm.role} options={['admin', 'operator', 'viewer']} onChange={(v) => setUserForm({ ...userForm, role: v })} /><Select label="Active" value={userForm.is_active} options={['true', 'false']} onChange={(v) => setUserForm({ ...userForm, is_active: v })} /><button className="primary"><UserPlus size={16} /> Add user</button></FormPanel></div><div className="stack"><div className="about"><h2>Local authentication</h2><dl><dt>Signed in user</dt><dd>{user?.username}</dd><dt>Role</dt><dd>{user?.role}</dd><dt>User management</dt><dd>Admins can add users here. Passwords are stored as PBKDF2 hashes in the backend database.</dd></dl></div><div className="table-wrap"><table><thead><tr><th><Users size={14} /> User</th><th>Role</th><th>Active</th></tr></thead><tbody>{users.map((row) => <tr key={row.id || row.username}><td>{row.username}</td><td>{row.role}</td><td>{row.is_active}</td></tr>)}</tbody></table></div></div></section>;
}

function DcMigrationMark() {
  return <svg viewBox="0 0 64 64" aria-hidden="true" className="dc-mark"><rect x="8" y="12" width="18" height="40" rx="3" /><rect x="38" y="12" width="18" height="40" rx="3" /><path d="M26 24h12M26 40h12" /><path d="M33 19l5 5-5 5M31 35l-5 5 5 5" /><circle cx="17" cy="22" r="2" /><circle cx="17" cy="32" r="2" /><circle cx="17" cy="42" r="2" /><circle cx="47" cy="22" r="2" /><circle cx="47" cy="32" r="2" /><circle cx="47" cy="42" r="2" /></svg>;
}

function StatusBoard({ vms }) {
  return <div className="table-wrap"><table><thead><tr><th>VM</th><th>Project</th><th>Criticality</th><th>Wave</th><th>Status</th></tr></thead><tbody>{vms.map((vm) => <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.project_id}</td><td>{vm.criticality}</td><td>{vm.migration_wave || '-'}</td><td><Badge value={vm.current_status} /></td></tr>)}</tbody></table></div>;
}

function Badge({ value }) {
  const kind = value?.includes('Failed') || value?.includes('Blocked') || value?.includes('Rolled') ? 'danger' : value?.includes('completed') || value?.includes('Validation') ? 'success' : 'neutral';
  return <span className={`badge ${kind}`}>{value}</span>;
}

function FormPanel({ title, onSubmit, children }) {
  return <form className="form-panel" onSubmit={onSubmit}><h2>{title}</h2>{children}</form>;
}

function Input({ label, value, onChange, type = 'text', required = false }) {
  return <label>{label}<input type={type} value={value ?? ''} required={required} onChange={(e) => onChange(e.target.value)} /></label>;
}

function TextArea({ label, value, onChange }) {
  return <label>{label}<textarea value={value ?? ''} onChange={(e) => onChange(e.target.value)} /></label>;
}

function Select({ label, value, options, onChange }) {
  return <label>{label}<select value={value ?? ''} onChange={(e) => onChange(e.target.value)}>{options.map((o) => Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o}>{o}</option>)}</select></label>;
}

function Table({ rows, columns }) {
  return <div className="table-wrap"><table><thead><tr>{columns.map((c) => <th key={c}>{c.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.id}>{columns.map((c) => <td key={c}>{String(row[c] ?? '-')}</td>)}</tr>)}</tbody></table></div>;
}

function formatDateTime(value) {
  if (!value) return '-';
  return value.replace('T', ' ');
}

createRoot(document.getElementById('root')).render(<App />);
