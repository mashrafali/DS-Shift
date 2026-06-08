import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowRightLeft,
  Building2,
  CalendarClock,
  CheckCircle2,
  Cloud,
  Edit3,
  FileText,
  Gauge,
  HardDrive,
  Info,
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
  Trash2,
  UserRound,
  Users,
  X,
} from 'lucide-react';
import './styles.css';

const tokenKey = 'ds_replace_token';

const migrationTypes = {
  'Lift and shift': 'Planned execution: validate source and target, copy or replicate the VM with minimal redesign, schedule cutover, then validate the target VM.',
  'Cold migration': 'Planned execution: shut down the source VM, copy or convert its disks, create the target VM, start it, and run post-migration validation. Downtime is required.',
  'Replication assisted': 'Planned execution: establish replication or backup synchronization, monitor readiness, stop the source at cutover, complete the final sync, and activate the target VM.',
  'Conversion based': 'Planned execution: inspect the source VM, convert its disks and guest configuration with tools such as virt-v2v, create the target VM, and validate drivers and boot.',
  'Cloud rehost': 'Planned execution: assess cloud compatibility, convert or upload VM disks, create cloud networking and compute resources, launch the instance, and validate connectivity.',
  'Any-to-any workflow': 'Planned execution: build a generic discovery, assessment, transfer, cutover, and validation runbook after the source and target platforms are finalized.',
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
  profile_photo: '',
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
  const [editProjectForm, setEditProjectForm] = useState(blankProject);
  const [vmForm, setVmForm] = useState(blankVm);
  const [connectorForm, setConnectorForm] = useState(blankConnector);
  const [editingConnectorId, setEditingConnectorId] = useState(null);
  const [editConnectorForm, setEditConnectorForm] = useState(blankConnector);
  const [connectorResult, setConnectorResult] = useState(null);
  const [userForm, setUserForm] = useState(blankUser);
  const [editingUserId, setEditingUserId] = useState(null);
  const [editUserForm, setEditUserForm] = useState(blankUser);
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
    await api('/projects', { method: 'POST', body: JSON.stringify(projectForm) });
    setProjectForm(blankProject);
    await load();
  };

  const saveProjectEdit = async (event) => {
    event.preventDefault();
    await api(`/projects/${editingProjectId}`, { method: 'PUT', body: JSON.stringify(editProjectForm) });
    setEditingProjectId(null);
    setEditProjectForm(blankProject);
    await load();
  };

  const editProject = (project) => {
    setEditProjectForm({
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
    const payload = { ...connectorForm, port: Number(connectorForm.port) || null };
    await api('/connectors', { method: 'POST', body: JSON.stringify(payload) });
    setConnectorForm(blankConnector);
    await load();
  };

  const saveConnectorEdit = async (event) => {
    event.preventDefault();
    const payload = { ...editConnectorForm, port: Number(editConnectorForm.port) || null };
    await api(`/connectors/${editingConnectorId}`, { method: 'PUT', body: JSON.stringify(payload) });
    setEditingConnectorId(null);
    setEditConnectorForm(blankConnector);
    await load();
  };

  const editConnector = (connector) => {
    setEditConnectorForm({ ...connector, port: connector.port || '' });
    setEditingConnectorId(connector.id);
    setConnectorResult(null);
  };

  const cancelConnectorEdit = () => {
    setEditConnectorForm(blankConnector);
    setEditingConnectorId(null);
    setConnectorResult(null);
  };

  const validateConnector = async (connector) => {
    const result = await api(`/connectors/${connector.id}/validate`, { method: 'POST' });
    setConnectorResult(result);
    await load();
  };

  const saveSettings = async (event) => {
    event.preventDefault();
    await api('/settings', { method: 'PUT', body: JSON.stringify({ ...settings, retention_days: Number(settings.retention_days) }) });
    await load();
  };

  const saveUser = async (event) => {
    event.preventDefault();
    setError('');
    try {
      const payload = {
        ...userForm,
        is_active: userForm.is_active === 'true',
        profile_photo: userForm.profile_photo || null,
      };
      await api('/users', { method: 'POST', body: JSON.stringify(payload) });
      setUserForm(blankUser);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const saveUserEdit = async (event) => {
    event.preventDefault();
    setError('');
    try {
      const payload = {
        ...editUserForm,
        is_active: editUserForm.is_active === 'true',
        profile_photo: editUserForm.profile_photo || null,
      };
      const savedUser = await api(`/users/${editingUserId}`, { method: 'PUT', body: JSON.stringify(payload) });
      if (savedUser.id === user?.id) setUser(savedUser);
      setEditingUserId(null);
      setEditUserForm(blankUser);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const editUser = (row) => {
    setEditUserForm({
      username: row.username,
      password: '',
      role: row.role,
      is_active: String(row.is_active),
      profile_photo: row.profile_photo || '',
    });
    setEditingUserId(row.id);
  };

  const deleteUser = async (row) => {
    if (!window.confirm(`Delete user "${row.username}"? This action cannot be undone.`)) return;
    setError('');
    try {
      await api(`/users/${row.id}`, { method: 'DELETE' });
      if (editingUserId === row.id) {
        setEditUserForm(blankUser);
        setEditingUserId(null);
      }
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const cancelUserEdit = () => {
    setEditUserForm(blankUser);
    setEditingUserId(null);
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
    ...(user?.role === 'admin' ? [['users', Users, 'Users']] : []),
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
            <p>{settings.company_name || 'Defined Solutions'}</p>
            <h1>{titleFor(active)}</h1>
          </div>
          <div className="toolbar">
            <button className="icon-button" onClick={load} title="Refresh data"><RefreshCw size={18} /></button>
            <div className="signed-in-user">
              <UserAvatar user={user} />
              <div className="user-card-details" title={`Signed in as ${user?.username || 'Loading user'}`}>
                <strong>{user?.username || 'Loading...'}</strong>
                <small>{user?.role || ''}</small>
              </div>
              <button className="user-card-logout" onClick={logout} title="Sign out" aria-label="Sign out">
                <LogOut size={16} />
                <span>Sign out</span>
              </button>
            </div>
          </div>
        </header>
        {settings.banner_message && <div className="notice"><Building2 size={20} /> {settings.banner_message}</div>}
        {error && <div className="alert">API error: {error}</div>}

        {active === 'dashboard' && <Dashboard summary={summary} vms={vms} connectors={connectors} />}
        {active === 'projects' && <Projects projects={projects} form={projectForm} setForm={setProjectForm} save={saveProject} editProject={editProject} editingProjectId={editingProjectId} editForm={editProjectForm} setEditForm={setEditProjectForm} saveEdit={saveProjectEdit} cancelEdit={() => { setEditProjectForm(blankProject); setEditingProjectId(null); }} />}
        {active === 'inventory' && <Inventory vms={vms} projects={projects} form={vmForm} setForm={setVmForm} create={createVm} changeStatus={changeStatus} />}
        {active === 'hosts' && <Connectors title="Host Connector" category="host" rows={connectors.filter((c) => c.connector_category === 'host')} form={connectorForm} setForm={setConnectorForm} save={saveConnector} editForm={editConnectorForm} setEditForm={setEditConnectorForm} saveEdit={saveConnectorEdit} types={hostConnectorTypes} discover={discoverConnector} validate={validateConnector} edit={editConnector} cancelEdit={cancelConnectorEdit} editingConnectorId={editingConnectorId} result={connectorResult} />}
        {active === 'clouds' && <Connectors title="Cloud Connector" category="cloud" rows={connectors.filter((c) => c.connector_category === 'cloud')} form={connectorForm} setForm={setConnectorForm} save={saveConnector} editForm={editConnectorForm} setEditForm={setEditConnectorForm} saveEdit={saveConnectorEdit} types={cloudConnectorTypes} discover={discoverConnector} validate={validateConnector} edit={editConnector} cancelEdit={cancelConnectorEdit} editingConnectorId={editingConnectorId} result={connectorResult} />}
        {active === 'engine' && <MigrationEngine connectors={connectors} form={migrationJobForm} setForm={setMigrationJobForm} save={createMigrationJob} jobs={migrationJobs} discoveryRuns={discoveryRuns} />}
        {active === 'waves' && <Waves waves={waves} />}
        {active === 'reports' && <Reports csv={csv} vms={vms} />}
        {active === 'users' && user?.role === 'admin' && <UsersView currentUser={user} users={users} form={userForm} setForm={setUserForm} save={saveUser} editForm={editUserForm} setEditForm={setEditUserForm} saveEdit={saveUserEdit} edit={editUser} remove={deleteUser} editingUserId={editingUserId} cancelEdit={cancelUserEdit} setError={setError} />}
        {active === 'settings' && <SettingsView settings={settings} setSettings={setSettings} save={saveSettings} user={user} />}
      </main>
    </div>
  );
}

function Login({ form, setForm, submit, error }) {
  return <div className="login-screen"><form className="login-panel" onSubmit={submit}><div className="login-logo"><DcMigrationMark /></div><h1>DS Replace</h1><p>Defined Solutions migration command center</p>{error && <div className="alert">{error}</div>}<Input label="Username" value={form.username} onChange={(v) => setForm({ ...form, username: v })} required /><Input label="Password" type="password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} required /><button className="primary"><KeyRound size={16} /> Sign in</button></form></div>;
}

function titleFor(active) {
  return ({ dashboard: 'Migration Command Center', projects: 'Saved Migration Projects', inventory: 'VM Inventory', hosts: 'Host Connectors', clouds: 'Cloud Connectors', engine: 'Discovery and Migration Engine', waves: 'Migration Waves', reports: 'Reports', users: 'User Management', settings: 'Settings Control' })[active];
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

function ProjectFields({ form, setForm }) {
  return <><Input label="Project name" value={form.project_name} onChange={(v) => setForm({ ...form, project_name: v })} required /><Input label="Customer name" value={form.customer_name} onChange={(v) => setForm({ ...form, customer_name: v })} required /><Select label="Source platform" value={form.source_platform} options={platforms} onChange={(v) => setForm({ ...form, source_platform: v })} /><Select label="Target platform" value={form.target_platform} options={platforms} onChange={(v) => setForm({ ...form, target_platform: v })} /><MigrationTypeSelect value={form.migration_type} onChange={(v) => setForm({ ...form, migration_type: v })} /><Input label="Planned start" type="datetime-local" value={form.planned_start_date} onChange={(v) => setForm({ ...form, planned_start_date: v })} /><Input label="Cutover schedule" type="datetime-local" value={form.planned_cutover_date} onChange={(v) => setForm({ ...form, planned_cutover_date: v })} /><Input label="Status" value={form.status} onChange={(v) => setForm({ ...form, status: v })} /><TextArea label="Notes" value={form.notes} onChange={(v) => setForm({ ...form, notes: v })} /></>;
}

function Projects({ projects, form, setForm, save, editProject, editingProjectId, editForm, setEditForm, saveEdit, cancelEdit }) {
  return <section className="split"><FormPanel title="Save migration project" onSubmit={save}><ProjectFields form={form} setForm={setForm} /><button className="primary"><Save size={16} /> Save project</button></FormPanel><div className="table-wrap"><table><thead><tr><th>Project</th><th>Customer</th><th>Source</th><th>Target</th><th>Start</th><th>Cutover</th><th></th></tr></thead><tbody>{projects.map((p) => <tr key={p.id}><td>{p.project_name}</td><td>{p.customer_name}</td><td>{p.source_platform}</td><td>{p.target_platform}</td><td>{formatDateTime(p.planned_start_date)}</td><td>{formatDateTime(p.planned_cutover_date)}</td><td><button className="mini" onClick={() => editProject(p)}><Edit3 size={14} /> Edit</button></td></tr>)}</tbody></table></div>{editingProjectId && <Modal title="Edit saved project" onClose={cancelEdit}><FormPanel title="" onSubmit={saveEdit}><ProjectFields form={editForm} setForm={setEditForm} /><div className="button-row"><button className="primary"><Save size={16} /> Save changes</button><button className="secondary" type="button" onClick={cancelEdit}><X size={16} /> Cancel</button></div></FormPanel></Modal>}</section>;
}

function Inventory({ vms, projects, form, setForm, create, changeStatus }) {
  return <section className="split"><FormPanel title="Add VM manually" onSubmit={create}><Select label="Project" value={form.project_id} options={projects.map((p) => [p.id, p.project_name])} onChange={(v) => setForm({ ...form, project_id: v })} /><Input label="VM name" value={form.vm_name} onChange={(v) => setForm({ ...form, vm_name: v })} required /><Select label="Source" value={form.source_platform} options={platforms} onChange={(v) => setForm({ ...form, source_platform: v })} /><Select label="Target" value={form.target_platform} options={platforms} onChange={(v) => setForm({ ...form, target_platform: v })} /><Input label="CPU" type="number" value={form.cpu} onChange={(v) => setForm({ ...form, cpu: v })} /><Input label="Memory GB" type="number" value={form.memory_gb} onChange={(v) => setForm({ ...form, memory_gb: v })} /><Input label="Disk GB" type="number" value={form.disk_gb} onChange={(v) => setForm({ ...form, disk_gb: v })} /><Input label="Owner" value={form.application_owner} onChange={(v) => setForm({ ...form, application_owner: v })} /><button className="primary"><Plus size={16} /> Add VM</button></FormPanel><div className="table-wrap"><table><thead><tr><th>VM</th><th>Source</th><th>Target</th><th>Size</th><th>Status</th><th>Change status</th></tr></thead><tbody>{vms.map((vm) => <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.source_platform}</td><td>{vm.target_platform}</td><td>{vm.cpu} CPU / {vm.memory_gb} GB</td><td><Badge value={vm.current_status} /></td><td><select value={vm.current_status} onChange={(e) => changeStatus(vm, e.target.value)}>{statuses.map((s) => <option key={s}>{s}</option>)}</select></td></tr>)}</tbody></table></div></section>;
}

function ConnectorFields({ form, setForm, category, types }) {
  const scopedForm = form.connector_category === category ? form : { ...blankConnector, connector_category: category, connector_type: types[0] };
  const update = (patch) => setForm({ ...scopedForm, ...patch, connector_category: category });
  return <><Select label="Type" value={scopedForm.connector_type} options={types} onChange={(v) => update({ connector_type: v })} /><Input label="Connector name" value={scopedForm.name} onChange={(v) => update({ name: v })} required /><Input label="Endpoint / API URL" value={scopedForm.endpoint} onChange={(v) => update({ endpoint: v })} /><Input label="Port" type="number" value={scopedForm.port || ''} onChange={(v) => update({ port: v })} /><Input label="Username" value={scopedForm.username || ''} onChange={(v) => update({ username: v })} /><Input label="Credential reference" value={scopedForm.credential_reference || ''} onChange={(v) => update({ credential_reference: v })} /><Input label="Environment" value={scopedForm.environment || ''} onChange={(v) => update({ environment: v })} /><Select label="Status" value={scopedForm.status || 'Not validated'} options={['Not validated', 'Validated', 'Validation failed', 'Unsupported']} onChange={(v) => update({ status: v })} /><TextArea label="Notes" value={scopedForm.notes || ''} onChange={(v) => update({ notes: v })} /></>;
}

function Connectors({ title, category, rows, form, setForm, save, editForm, setEditForm, saveEdit, types, discover, validate, edit, cancelEdit, editingConnectorId, result }) {
  const isEditing = editingConnectorId && editForm.connector_category === category;
  return <section className="split"><FormPanel title={`Add ${title}`} onSubmit={save}><ConnectorFields form={form} setForm={setForm} category={category} types={types} /><div className="tip">Validate performs a real connector check and shows the result. Discovery collects VM inventory when the required runtime tools and credentials are available.</div><button className="primary"><Save size={16} /> Add connector</button></FormPanel><div className="stack">{result && result.connector?.connector_category === category && <div className={`result ${result.status === 'Validated' ? 'success' : 'danger'}`}><strong>{result.status}</strong><span>{result.message}</span>{Boolean(result.commands?.length) && <code>{result.commands.join(' | ')}</code>}</div>}<div className="table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Endpoint</th><th>Credential</th><th>Status</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.name}</td><td>{row.connector_type}</td><td>{row.endpoint || '-'}</td><td>{row.credential_reference || '-'}</td><td><Badge value={row.status} /></td><td><div className="button-row compact"><button className="mini" onClick={() => edit(row)}><Edit3 size={14} /> Edit</button><button className="mini" onClick={() => validate(row)}><CheckCircle2 size={14} /> Validate</button><button className="mini" onClick={() => discover(row)}><Search size={14} /> Discover</button></div></td></tr>)}</tbody></table></div></div>{isEditing && <Modal title={`Edit ${title}`} onClose={cancelEdit}><FormPanel title="" onSubmit={saveEdit}><ConnectorFields form={editForm} setForm={setEditForm} category={category} types={types} /><div className="tip">Validate performs a real connector check and shows the result. Discovery collects VM inventory when the required runtime tools and credentials are available.</div><div className="button-row"><button className="primary"><Save size={16} /> Save changes</button><button className="secondary" type="button" onClick={cancelEdit}><X size={16} /> Cancel</button></div></FormPanel></Modal>}</section>;
}

function MigrationEngine({ connectors, form, setForm, save, jobs, discoveryRuns }) {
  const hostConnectors = connectors.filter((c) => c.connector_category === 'host');
  const kvmConnectors = hostConnectors.filter((c) => c.connector_type === 'KVM');
  const vmwareConnectors = hostConnectors.filter((c) => c.connector_type.includes('VMware') || c.connector_type.includes('vCenter'));
  const latestJob = jobs[0];
  const runbook = parseJsonArray(latestJob?.runbook_json);
  return <section className="split"><FormPanel title="KVM to ESXi migration test preflight" onSubmit={save}><Select label="Source KVM connector" value={form.source_connector_id} options={kvmConnectors.map((c) => [c.id, c.name])} onChange={(v) => setForm({ ...form, source_connector_id: v })} /><Select label="Target ESXi / vCenter connector" value={form.target_connector_id} options={vmwareConnectors.map((c) => [c.id, c.name])} onChange={(v) => setForm({ ...form, target_connector_id: v })} /><Input label="Source VM name" value={form.vm_name} onChange={(v) => setForm({ ...form, vm_name: v })} required /><Input label="Target VM name" value={form.target_name} onChange={(v) => setForm({ ...form, target_name: v })} /><Input label="Target datastore" value={form.target_datastore} onChange={(v) => setForm({ ...form, target_datastore: v })} /><div className="tip">This performs a non-destructive migration test preflight: source KVM validation, source VM inspection, target vCenter validation, and live conversion tool checks. It does not run virt-v2v.</div><button className="primary"><Play size={16} /> Create test preflight</button></FormPanel><div className="stack"><Table rows={jobs} columns={['vm_name', 'migration_type', 'status', 'message']} />{runbook.length > 0 && <div className="table-wrap"><table><thead><tr><th>Latest preflight item</th><th>Result / Command</th></tr></thead><tbody>{runbook.map((item, index) => <tr key={`${item.check || item.step}-${index}`}><td>{item.check || item.step}</td><td>{item.message || item.command}</td></tr>)}</tbody></table></div>}<Table rows={discoveryRuns} columns={['connector_id', 'status', 'message']} /></div></section>;
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

function UserFields({ form, setForm, editingUserId, setError }) {
  const readPhoto = (file) => {
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      setError('Profile photo must be a PNG, JPEG, or WebP image');
      return;
    }
    if (file.size > 256 * 1024) {
      setError('Profile photo must be 256 KB or smaller');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setError('');
      setForm({ ...form, profile_photo: String(reader.result) });
    };
    reader.onerror = () => setError('Unable to read the selected profile photo');
    reader.readAsDataURL(file);
  };
  return <><div className="profile-photo-editor"><UserAvatar user={{ username: form.username, profile_photo: form.profile_photo }} large /><label className="photo-picker">Profile photo<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => readPhoto(event.target.files?.[0])} /></label>{form.profile_photo && <button className="secondary" type="button" onClick={() => setForm({ ...form, profile_photo: '' })}>Remove photo</button>}</div><Input label="Username" value={form.username} onChange={(v) => setForm({ ...form, username: v })} required /><Input label={editingUserId ? 'New password (leave blank to keep current)' : 'Password'} type="password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} required={!editingUserId} /><Select label="Role" value={form.role} options={['admin', 'operator', 'viewer']} onChange={(v) => setForm({ ...form, role: v })} /><Select label="Active" value={form.is_active} options={[['true', 'Active'], ['false', 'Inactive']]} onChange={(v) => setForm({ ...form, is_active: v })} /><div className="tip">Profile photos must be PNG, JPEG, or WebP and no larger than 256 KB.</div></>;
}

function UsersView({ currentUser, users, form, setForm, save, editForm, setEditForm, saveEdit, edit, remove, editingUserId, cancelEdit, setError }) {
  return <section className="split"><FormPanel title="Create user" onSubmit={save}><UserFields form={form} setForm={setForm} setError={setError} /><button className="primary"><Save size={16} /> Create user</button></FormPanel><div className="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>{users.map((row) => <tr key={row.id}><td><div className="user-table-identity"><UserAvatar user={row} /><div><strong>{row.username}</strong>{row.id === currentUser?.id && <span>Current user</span>}</div></div></td><td><Badge value={row.role} /></td><td><Badge value={row.is_active ? 'Active' : 'Inactive'} /></td><td><div className="button-row compact"><button className="mini" onClick={() => edit(row)}><Edit3 size={14} /> Edit</button><button className="mini danger-button" disabled={row.id === currentUser?.id} onClick={() => remove(row)}><Trash2 size={14} /> Delete</button></div></td></tr>)}</tbody></table></div>{editingUserId && <Modal title="Edit user" onClose={cancelEdit}><FormPanel title="" onSubmit={saveEdit}><UserFields form={editForm} setForm={setEditForm} editingUserId={editingUserId} setError={setError} /><div className="button-row"><button className="primary"><Save size={16} /> Save changes</button><button className="secondary" type="button" onClick={cancelEdit}><X size={16} /> Cancel</button></div></FormPanel></Modal>}</section>;
}

function SettingsView({ settings, setSettings, save, user }) {
  return <section className="split"><FormPanel title="Application settings" onSubmit={save}><Input label="Product name" value={settings.product_name || ''} onChange={(v) => setSettings({ ...settings, product_name: v })} /><Input label="Company name" value={settings.company_name || ''} onChange={(v) => setSettings({ ...settings, company_name: v })} /><Input label="Default timezone" value={settings.default_timezone || ''} onChange={(v) => setSettings({ ...settings, default_timezone: v })} /><Input label="Retention days" type="number" value={settings.retention_days || 365} onChange={(v) => setSettings({ ...settings, retention_days: v })} /><Input label="Maintenance window" value={settings.maintenance_window || ''} onChange={(v) => setSettings({ ...settings, maintenance_window: v })} /><TextArea label="Banner message" value={settings.banner_message || ''} onChange={(v) => setSettings({ ...settings, banner_message: v })} /><button className="primary"><Save size={16} /> Save settings</button></FormPanel><div className="about"><h2>Local authentication</h2><dl><dt>Signed in user</dt><dd>{user?.username}</dd><dt>Role</dt><dd>{user?.role}</dd><dt>User management</dt><dd>Administrators manage accounts and profile photos from the dedicated Users page.</dd></dl></div></section>;
}

function UserAvatar({ user, large = false }) {
  const className = `user-avatar${large ? ' large' : ''}`;
  if (user?.profile_photo) return <span className={className}><img src={user.profile_photo} alt={`${user.username || 'User'} profile`} /></span>;
  return <span className={className}><UserRound size={large ? 34 : 20} /></span>;
}

function DcMigrationMark() {
  return <svg viewBox="0 0 64 64" aria-hidden="true" className="dc-mark"><rect x="8" y="12" width="18" height="40" rx="3" /><rect x="38" y="12" width="18" height="40" rx="3" /><path d="M26 24h12M26 40h12" /><path d="M33 19l5 5-5 5M31 35l-5 5 5 5" /><circle cx="17" cy="22" r="2" /><circle cx="17" cy="32" r="2" /><circle cx="17" cy="42" r="2" /><circle cx="47" cy="22" r="2" /><circle cx="47" cy="32" r="2" /><circle cx="47" cy="42" r="2" /></svg>;
}

function StatusBoard({ vms }) {
  return <div className="table-wrap"><table><thead><tr><th>VM</th><th>Project</th><th>Criticality</th><th>Wave</th><th>Status</th></tr></thead><tbody>{vms.map((vm) => <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.project_id}</td><td>{vm.criticality}</td><td>{vm.migration_wave || '-'}</td><td><Badge value={vm.current_status} /></td></tr>)}</tbody></table></div>;
}

function Badge({ value }) {
  const normalized = (value || '').toLowerCase();
  const kind = normalized.includes('failed') || normalized.includes('blocked') || normalized.includes('rolled') || normalized === 'inactive' ? 'danger' : normalized.includes('completed') || normalized.includes('validated') || normalized === 'active' ? 'success' : 'neutral';
  return <span className={`badge ${kind}`}>{value}</span>;
}

function FormPanel({ title, onSubmit, children }) {
  return <form className="form-panel" onSubmit={onSubmit}>{title && <h2>{title}</h2>}{children}</form>;
}

function Modal({ title, onClose, children }) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><div className="modal-panel" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}><div className="modal-header"><h2>{title}</h2><button className="icon-button" type="button" onClick={onClose} title="Close"><X size={18} /></button></div>{children}</div></div>;
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

function MigrationTypeSelect({ value, onChange }) {
  const description = migrationTypes[value] || migrationTypes['Any-to-any workflow'];
  return <label className="migration-type-field"><span className="field-label">Migration type <span className="help-icon" tabIndex="0" aria-describedby="migration-type-help"><Info size={14} /></span></span><select value={value ?? ''} onChange={(event) => onChange(event.target.value)} aria-describedby="migration-type-help">{Object.keys(migrationTypes).map((type) => <option key={type} value={type}>{type}</option>)}</select><span className="migration-tooltip" id="migration-type-help" role="tooltip"><strong>{value}</strong>{description}<small>Saving the project records this plan. It does not start a live migration automatically.</small></span></label>;
}

function Table({ rows, columns }) {
  return <div className="table-wrap"><table><thead><tr>{columns.map((c) => <th key={c}>{c.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.id}>{columns.map((c) => <td key={c}>{String(row[c] ?? '-')}</td>)}</tr>)}</tbody></table></div>;
}

function formatDateTime(value) {
  if (!value) return '-';
  return value.replace('T', ' ');
}

function parseJsonArray(value) {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

createRoot(document.getElementById('root')).render(<App />);
