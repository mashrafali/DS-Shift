import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, BarChart3, CloudCog, Database, FileText, Layers3, Plus, RefreshCw, Server, Settings } from 'lucide-react';
import './styles.css';

const api = async (path, options = {}) => {
  const response = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  if (response.status === 204) return null;
  return response.json();
};

const blankProject = {
  project_name: '',
  customer_name: '',
  source_platform: 'KVM',
  target_platform: 'VMware ESXi / vCenter',
  migration_type: 'Any-to-any VM migration',
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

const statuses = ['Discovered', 'Assessed', 'Ready for migration', 'Replication prepared', 'Migration in progress', 'Cutover scheduled', 'Cutover completed', 'Validation completed', 'Failed', 'Rolled back', 'Blocked'];
const platforms = ['KVM', 'VMware ESXi / vCenter', 'Nutanix AHV', 'Google Cloud Platform', 'AWS', 'Azure', 'Other'];

function App() {
  const [active, setActive] = useState('dashboard');
  const [summary, setSummary] = useState(null);
  const [projects, setProjects] = useState([]);
  const [vms, setVms] = useState([]);
  const [waves, setWaves] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [projectForm, setProjectForm] = useState(blankProject);
  const [vmForm, setVmForm] = useState(blankVm);
  const [error, setError] = useState('');

  const load = async () => {
    setError('');
    try {
      const [dashboard, projectRows, vmRows, waveRows, platformRows] = await Promise.all([
        api('/dashboard'),
        api('/projects'),
        api('/vms'),
        api('/waves'),
        api('/platforms'),
      ]);
      setSummary(dashboard);
      setProjects(projectRows);
      setVms(vmRows);
      setWaves(waveRows);
      setProfiles(platformRows);
      if (projectRows[0]) setVmForm((f) => ({ ...f, project_id: projectRows[0].id }));
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => { load(); }, []);

  const createProject = async (event) => {
    event.preventDefault();
    await api('/projects', { method: 'POST', body: JSON.stringify(projectForm) });
    setProjectForm(blankProject);
    await load();
  };

  const createVm = async (event) => {
    event.preventDefault();
    await api('/vms', { method: 'POST', body: JSON.stringify({ ...vmForm, project_id: Number(vmForm.project_id), cpu: Number(vmForm.cpu), memory_gb: Number(vmForm.memory_gb), disk_gb: Number(vmForm.disk_gb) }) });
    setVmForm((f) => ({ ...blankVm, project_id: f.project_id }));
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

  const nav = [
    ['dashboard', BarChart3, 'Dashboard'],
    ['projects', Layers3, 'Projects'],
    ['inventory', Server, 'VM Inventory'],
    ['platforms', CloudCog, 'Platforms'],
    ['waves', Activity, 'Waves'],
    ['reports', FileText, 'Reports'],
    ['about', Settings, 'About'],
  ];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">DS</div>
          <div><strong>DS Replace</strong><span>Defined Solutions</span></div>
        </div>
        <nav>
          {nav.map(([key, Icon, label]) => (
            <button key={key} className={active === key ? 'active' : ''} onClick={() => setActive(key)} title={label}>
              <Icon size={18} /> {label}
            </button>
          ))}
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p>Defined Solutions</p>
            <h1>{titleFor(active)}</h1>
          </div>
          <button className="icon-button" onClick={load} title="Refresh data"><RefreshCw size={18} /></button>
        </header>
        {error && <div className="alert">API error: {error}</div>}

        {active === 'dashboard' && <Dashboard summary={summary} vms={vms} />}
        {active === 'projects' && <Projects projects={projects} form={projectForm} setForm={setProjectForm} create={createProject} />}
        {active === 'inventory' && <Inventory vms={vms} projects={projects} form={vmForm} setForm={setVmForm} create={createVm} changeStatus={changeStatus} />}
        {active === 'platforms' && <Platforms profiles={profiles} />}
        {active === 'waves' && <Waves waves={waves} />}
        {active === 'reports' && <Reports csv={csv} vms={vms} />}
        {active === 'about' && <About />}
      </main>
    </div>
  );
}

function titleFor(active) {
  return ({ dashboard: 'Migration Command Center', projects: 'Migration Projects', inventory: 'VM Inventory', platforms: 'Source and Target Platforms', waves: 'Migration Waves', reports: 'Reports', about: 'Settings and About' })[active];
}

function Dashboard({ summary, vms }) {
  const cards = [
    ['Total projects', summary?.total_projects ?? 0],
    ['VMs discovered', summary?.vms_discovered ?? 0],
    ['VMs planned', summary?.vms_planned ?? 0],
    ['VMs migrated', summary?.vms_migrated ?? 0],
    ['Failed or blocked', summary?.vms_failed_or_blocked ?? 0],
    ['Progress', `${summary?.progress_percent ?? 0}%`],
  ];
  return <section><div className="metric-grid">{cards.map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><StatusBoard vms={vms} /></section>;
}

function Projects({ projects, form, setForm, create }) {
  return <section className="split"><FormPanel title="Create migration project" onSubmit={create}><Input label="Project name" value={form.project_name} onChange={(v) => setForm({ ...form, project_name: v })} required /><Input label="Customer name" value={form.customer_name} onChange={(v) => setForm({ ...form, customer_name: v })} required /><Select label="Source platform" value={form.source_platform} options={platforms} onChange={(v) => setForm({ ...form, source_platform: v })} /><Select label="Target platform" value={form.target_platform} options={platforms} onChange={(v) => setForm({ ...form, target_platform: v })} /><Input label="Migration type" value={form.migration_type} onChange={(v) => setForm({ ...form, migration_type: v })} /><Input label="Planned start" value={form.planned_start_date} onChange={(v) => setForm({ ...form, planned_start_date: v })} /><Input label="Cutover date" value={form.planned_cutover_date} onChange={(v) => setForm({ ...form, planned_cutover_date: v })} /><button className="primary"><Plus size={16} /> Create project</button></FormPanel><Table rows={projects} columns={['project_name', 'customer_name', 'source_platform', 'target_platform', 'status']} /></section>;
}

function Inventory({ vms, projects, form, setForm, create, changeStatus }) {
  return <section className="split"><FormPanel title="Add VM manually" onSubmit={create}><Select label="Project" value={form.project_id} options={projects.map((p) => [p.id, p.project_name])} onChange={(v) => setForm({ ...form, project_id: v })} /><Input label="VM name" value={form.vm_name} onChange={(v) => setForm({ ...form, vm_name: v })} required /><Select label="Source" value={form.source_platform} options={platforms} onChange={(v) => setForm({ ...form, source_platform: v })} /><Select label="Target" value={form.target_platform} options={platforms} onChange={(v) => setForm({ ...form, target_platform: v })} /><Input label="CPU" type="number" value={form.cpu} onChange={(v) => setForm({ ...form, cpu: v })} /><Input label="Memory GB" type="number" value={form.memory_gb} onChange={(v) => setForm({ ...form, memory_gb: v })} /><Input label="Disk GB" type="number" value={form.disk_gb} onChange={(v) => setForm({ ...form, disk_gb: v })} /><Input label="Owner" value={form.application_owner} onChange={(v) => setForm({ ...form, application_owner: v })} /><button className="primary"><Plus size={16} /> Add VM</button></FormPanel><div className="table-wrap"><table><thead><tr><th>VM</th><th>Source</th><th>Target</th><th>Size</th><th>Status</th><th>Change status</th></tr></thead><tbody>{vms.map((vm) => <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.source_platform}</td><td>{vm.target_platform}</td><td>{vm.cpu} CPU / {vm.memory_gb} GB</td><td><Badge value={vm.current_status} /></td><td><select value={vm.current_status} onChange={(e) => changeStatus(vm, e.target.value)}>{statuses.map((s) => <option key={s}>{s}</option>)}</select></td></tr>)}</tbody></table></div></section>;
}

function Platforms({ profiles }) {
  return <section><div className="notice"><Database size={20} /> Platform profiles are credential-reference placeholders for MVP. Real secrets are a roadmap item for vault-backed integrations.</div><Table rows={profiles} columns={['name', 'platform_type', 'endpoint', 'environment', 'credential_reference']} /></section>;
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

function About() {
  return <section className="about"><h2>DS Replace</h2><p>Defined Solutions any-to-any VM migration planning and tracking platform.</p><dl><dt>Version</dt><dd>1.0 RC1</dd><dt>MVP scope</dt><dd>Assessment, inventory, workflow tracking, waves, reports, and future-ready connector architecture.</dd><dt>Security roadmap</dt><dd>Authentication, RBAC, vault integration, audit logging, API tokens, and enterprise identity provider integration.</dd></dl></section>;
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
  return <label>{label}<input type={type} value={value} required={required} onChange={(e) => onChange(e.target.value)} /></label>;
}

function Select({ label, value, options, onChange }) {
  return <label>{label}<select value={value} onChange={(e) => onChange(e.target.value)}>{options.map((o) => Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o}>{o}</option>)}</select></label>;
}

function Table({ rows, columns }) {
  return <div className="table-wrap"><table><thead><tr>{columns.map((c) => <th key={c}>{c.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.id}>{columns.map((c) => <td key={c}>{String(row[c] ?? '-')}</td>)}</tr>)}</tbody></table></div>;
}

createRoot(document.getElementById('root')).render(<App />);
