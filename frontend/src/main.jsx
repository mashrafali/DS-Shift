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
  Square,
} from 'lucide-react';
import './styles.css';

const tokenKey = 'ds_shift_token';
let displayTimezone = 'Asia/Riyadh';

const timezoneOptions = [
  ['UTC', 'UTC'],
  ['Asia/Riyadh', 'Asia/Riyadh'],
  ['Asia/Dubai', 'Asia/Dubai'],
  ['Asia/Kolkata', 'Asia/Kolkata'],
  ['Asia/Singapore', 'Asia/Singapore'],
  ['Asia/Tokyo', 'Asia/Tokyo'],
  ['Europe/London', 'Europe/London'],
  ['Europe/Paris', 'Europe/Paris'],
  ['America/New_York', 'America/New_York'],
  ['America/Chicago', 'America/Chicago'],
  ['America/Denver', 'America/Denver'],
  ['America/Los_Angeles', 'America/Los_Angeles'],
  ['Australia/Sydney', 'Australia/Sydney'],
];

function loadStoredToken() {
  return localStorage.getItem(tokenKey) || '';
}

const fallbackConnectorPlatforms = {
  host: [
    { type: 'KVM', tool: 'Paramiko SSH and virsh', endpoint_hint: 'Host IP or hostname', credential_hint: 'SSH key or GUI password', default_port: 22 },
    { type: 'VMware ESXi / vCenter', tool: 'VMware pyVmomi', endpoint_hint: 'Host IP or hostname', credential_hint: 'GUI username and password', default_port: 443 },
    { type: 'Nutanix AHV', tool: 'Nutanix Prism Central v3 REST API', endpoint_hint: 'Host IP or hostname', credential_hint: 'GUI username and password', default_port: 9440 },
  ],
  cloud: [
    { type: 'Amazon Web Services', tool: 'AWS SDK for Python (Boto3)', endpoint_hint: 'AWS region, for example us-east-1', credential_hint: 'GUI access key and secret key', default_port: '' },
    { type: 'Google Cloud Platform', tool: 'Google Cloud Compute Python SDK', endpoint_hint: 'Google Cloud project ID', credential_hint: 'GUI service account JSON', default_port: '' },
    { type: 'Microsoft Azure', tool: 'Azure Identity and Compute Management SDKs', endpoint_hint: 'Azure subscription ID', credential_hint: 'GUI tenant, client, and secret values', default_port: '' },
  ],
};
const statuses = ['Discovered', 'Assessed', 'Ready for migration', 'Replication prepared', 'Migration in progress', 'Cutover scheduled', 'Cutover completed', 'Validation completed', 'Failed', 'Rolled back', 'Blocked'];

const blankConnector = {
  name: '',
  connector_category: 'host',
  connector_type: 'KVM',
  endpoint: '',
  port: 22,
  username: '',
  target_network: '',
  target_datastore: '',
  target_storage_pool: '',
  target_vdc_name: '',
  target_compute_name: '',
  credential_reference: '',
  password: '',
  credential_payload: {
    access_key_id: '',
    secret_access_key: '',
    session_token: '',
    service_account_json: '',
    tenant_id: '',
    client_id: '',
    client_secret: '',
  },
  environment: 'Lab',
  status: 'Not validated',
  notes: '',
};

const blankSettings = {
  product_name: 'DS Shift',
  company_name: 'Defined Solutions',
  default_timezone: 'Asia/Riyadh',
  banner_message: '',
};

const blankAbout = {
  product: 'DS Shift',
  brand: 'Defined Solutions',
  version: '1.0 RC1',
  purpose: '',
};

const blankWave = {
  wave_name: '',
  planned_window: '',
  status: 'Planned',
  notes: '',
  plan_ids: [],
};

const blankUser = {
  username: '',
  password: '',
  role: 'operator',
  is_active: 'true',
  profile_photo: '',
};

const blankMigrationPlan = {
  name: '',
  vm_ids: [],
  target_connector_id: '',
  keep_source_vm: true,
  notes: '',
  execution_options: {
    source_zone: '',
    target_zone: '',
    source_region: '',
    target_region: '',
    target_resource_group: '',
    target_location: '',
    target_subnet_id: '',
    target_instance_type: '',
    target_resource_pool: '',
    target_folder: '',
  },
};

function connectorFormFromRow(connector) {
  return {
    ...blankConnector,
    ...connector,
    port: connector.port || '',
    password: '',
    credential_payload: {
      ...blankConnector.credential_payload,
    },
  };
}

function connectorPayload(form) {
  const connectorType = form.connector_type;
  const payload = {
    name: form.name,
    connector_category: form.connector_category,
    connector_type: connectorType,
    endpoint: form.endpoint || null,
    port: Number(form.port) || null,
    username: form.username || null,
    target_network: form.target_network || null,
    target_datastore: form.target_datastore || null,
    target_storage_pool: form.target_storage_pool || null,
    target_vdc_name: form.target_vdc_name || null,
    target_compute_name: form.target_compute_name || null,
    credential_reference: form.credential_reference || null,
    environment: form.environment || null,
    notes: form.notes || null,
    status: form.status || 'Not validated',
    credential_payload: {},
  };
  if (form.password) payload.password = form.password;
  if (connectorType === 'Amazon Web Services') {
    payload.credential_payload = compactObject({
      access_key_id: form.credential_payload.access_key_id,
      secret_access_key: form.credential_payload.secret_access_key,
      session_token: form.credential_payload.session_token,
    });
  } else if (connectorType === 'Google Cloud Platform') {
    payload.credential_payload = parseServiceAccountJson(form.credential_payload.service_account_json);
  } else if (connectorType === 'Microsoft Azure') {
    payload.credential_payload = compactObject({
      tenant_id: form.credential_payload.tenant_id,
      client_id: form.credential_payload.client_id,
      client_secret: form.credential_payload.client_secret,
    });
  }
  return payload;
}

function App() {
  const [token, setToken] = useState(loadStoredToken);
  const [user, setUser] = useState(null);
  const [active, setActive] = useState('dashboard');
  const [summary, setSummary] = useState(null);
  const [vms, setVms] = useState([]);
  const [waves, setWaves] = useState([]);
  const [waveForm, setWaveForm] = useState(blankWave);
  const [showWaveModal, setShowWaveModal] = useState(false);
  const [editingWaveId, setEditingWaveId] = useState(null);
  const [executingWaveId, setExecutingWaveId] = useState(null);
  const [connectors, setConnectors] = useState([]);
  const [hosts, setHosts] = useState([]);
  const [connectorCatalog, setConnectorCatalog] = useState({ categories: fallbackConnectorPlatforms, engines: [] });
  const [connectorCategory, setConnectorCategory] = useState('');
  const [users, setUsers] = useState([]);
  const [migrationPlans, setMigrationPlans] = useState([]);
  const [settings, setSettings] = useState(blankSettings);
  const [about, setAbout] = useState(blankAbout);
  const [serviceStatus, setServiceStatus] = useState({ services: [], monitor_error: '' });
  const [serviceStatusLoading, setServiceStatusLoading] = useState(false);
  const [selectedVmIds, setSelectedVmIds] = useState([]);
  const [migrationPlanForm, setMigrationPlanForm] = useState(blankMigrationPlan);
  const [showPlanModal, setShowPlanModal] = useState(false);
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [taskPlanId, setTaskPlanId] = useState(null);
  const [editingPlanId, setEditingPlanId] = useState(null);
  const [editPlanForm, setEditPlanForm] = useState(blankMigrationPlan);
  const [planExecutions, setPlanExecutions] = useState({});
  const [executingPlanId, setExecutingPlanId] = useState(null);
  const [launchingPlanId, setLaunchingPlanId] = useState(null);
  const [continuingPlanId, setContinuingPlanId] = useState(null);
  const [forceStoppingPlanId, setForceStoppingPlanId] = useState(null);
  const [connectorForm, setConnectorForm] = useState(blankConnector);
  const [editingConnectorId, setEditingConnectorId] = useState(null);
  const [editConnectorForm, setEditConnectorForm] = useState(blankConnector);
  const [connectorResult, setConnectorResult] = useState(null);
  const [discoveringConnectorId, setDiscoveringConnectorId] = useState(null);
  const [userForm, setUserForm] = useState(blankUser);
  const [editingUserId, setEditingUserId] = useState(null);
  const [editUserForm, setEditUserForm] = useState(blankUser);
  const [loginForm, setLoginForm] = useState({ username: 'admin', password: '' });
  const [bootstrapChecked, setBootstrapChecked] = useState(false);
  const [bootstrapRequired, setBootstrapRequired] = useState(false);
  const [bootstrapForm, setBootstrapForm] = useState({ password: '', confirm_password: '' });
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

  const syncPlanExecution = (execution) => {
    if (!execution?.plan?.id) return execution;
    setPlanExecutions((current) => ({ ...current, [execution.plan.id]: execution }));
    return execution;
  };

  const loadPlanExecution = async (planId) => syncPlanExecution(await api(`/migration-plans/${planId}/execution`));

  const load = async () => {
    if (!token) return;
    setError('');
    try {
      const [me, dashboard, vmRows, waveRows, connectorRows, connectorPlatformRows, hostRows, planRows, appSettings, aboutInfo] = await Promise.all([
        api('/auth/me'),
        api('/dashboard'),
        api('/vms'),
        api('/waves'),
        api('/connectors'),
        api('/connector-platforms'),
        api('/hosts'),
        api('/migration-plans'),
        api('/settings'),
        api('/about'),
      ]);
      const userRows = me.role === 'admin' ? await api('/users') : [];
      setUser(me);
      setUsers(userRows);
      setSummary(dashboard);
      setVms(vmRows);
      setWaves(waveRows);
      setConnectors(connectorRows);
      setConnectorCatalog(connectorPlatformRows);
      setHosts(hostRows);
      setMigrationPlans(planRows);
      setSettings(appSettings);
      setAbout(aboutInfo);
    } catch (err) {
      setError(err.message);
    }
  };

  const refreshCurrentView = async () => {
    setError('');
    try {
      if (active === 'plans' && taskPlanId) await loadPlanExecution(taskPlanId);
      await load();
      if (active === 'settings') {
        const result = await api('/service-status');
        setServiceStatus(result);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    if (token) {
      setBootstrapChecked(true);
      setBootstrapRequired(false);
      load();
      return;
    }
    let current = true;
    const checkBootstrap = async () => {
      try {
        const response = await fetch('/api/bootstrap/status');
        if (!response.ok) throw new Error('Unable to read first-run setup status');
        const result = await response.json();
        if (current) {
          setBootstrapRequired(Boolean(result.required));
          setBootstrapChecked(true);
        }
      } catch (err) {
        if (current) {
          setError(err.message);
          setBootstrapChecked(true);
        }
      }
    };
    checkBootstrap();
    return () => {
      current = false;
    };
  }, [token]);

  useEffect(() => {
    const currentPlanIds = new Set(migrationPlans.map((plan) => plan.id));
    if (selectedPlanId && !currentPlanIds.has(selectedPlanId)) setSelectedPlanId(null);
    if (taskPlanId && !currentPlanIds.has(taskPlanId)) setTaskPlanId(null);
    if (editingPlanId && !currentPlanIds.has(editingPlanId)) {
      setEditingPlanId(null);
      setEditPlanForm(blankMigrationPlan);
    }
  }, [migrationPlans, selectedPlanId, taskPlanId, editingPlanId]);

  useEffect(() => {
    if (!token) return undefined;
    const activePlans = migrationPlans.filter((plan) => (plan.spark_job_id && ['Queued', 'Running'].includes(plan.status)) || plan.status === 'Preflight running');
    if (!activePlans.length) return undefined;
    const timer = window.setInterval(async () => {
      try {
        await Promise.all(activePlans.map((plan) => loadPlanExecution(plan.id)));
        await load();
      } catch (err) {
        setError(err.message);
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [token, migrationPlans.map((plan) => `${plan.id}:${plan.status}:${plan.spark_job_id}`).join('|')]);

  useEffect(() => {
    if (!token || active !== 'settings') return undefined;
    let current = true;
    const refresh = async () => {
      setServiceStatusLoading(true);
      try {
        const result = await api('/service-status');
        if (current) setServiceStatus(result);
      } catch (err) {
        if (current) setServiceStatus({ services: [], monitor_error: err.message });
      } finally {
        if (current) setServiceStatusLoading(false);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [token, active]);

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

  const bootstrapAdmin = async (event) => {
    event.preventDefault();
    setError('');
    if (bootstrapForm.password !== bootstrapForm.confirm_password) {
      setError('Passwords do not match');
      return;
    }
    try {
      const response = await fetch('/api/bootstrap/admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: bootstrapForm.password }),
      });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json();
      localStorage.setItem(tokenKey, data.access_token);
      setBootstrapRequired(false);
      setBootstrapForm({ password: '', confirm_password: '' });
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

  const saveConnector = async (event) => {
    event.preventDefault();
    try {
      const payload = connectorPayload(connectorForm);
      await api('/connectors', { method: 'POST', body: JSON.stringify(payload) });
      setConnectorForm(blankConnector);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const saveConnectorEdit = async (event) => {
    event.preventDefault();
    try {
      const payload = connectorPayload(editConnectorForm);
      await api(`/connectors/${editingConnectorId}`, { method: 'PUT', body: JSON.stringify(payload) });
      setEditingConnectorId(null);
      setEditConnectorForm(blankConnector);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const editConnector = (connector) => {
    const connectorType = connector.connector_type === 'AWS' ? 'Amazon Web Services' : connector.connector_type === 'Azure' ? 'Microsoft Azure' : connector.connector_type;
    setEditConnectorForm(connectorFormFromRow({ ...connector, connector_type: connectorType }));
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

  const deleteConnector = async (connector) => {
    if (!window.confirm(`Delete connector "${connector.name}"? Its discovery history and discovered hosts will also be deleted.`)) return;
    setError('');
    try {
      await api(`/connectors/${connector.id}`, { method: 'DELETE' });
      if (editingConnectorId === connector.id) cancelConnectorEdit();
      setConnectorResult(null);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const saveSettings = async (event) => {
    event.preventDefault();
    await api('/settings', { method: 'PUT', body: JSON.stringify({ default_timezone: settings.default_timezone, banner_message: settings.banner_message || null }) });
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
    setError('');
    setDiscoveringConnectorId(connector.id);
    setConnectorResult({ connector, status: 'Discovering', message: `Discovering ${connector.name} and its workloads...`, commands: [] });
    try {
      const result = await api(`/connectors/${connector.id}/discover`, {
        method: 'POST',
        body: JSON.stringify({ import_to_project_id: projectId ? Number(projectId) : null, target_platform: 'Unassigned' }),
      });
      setConnectorResult({ connector, status: result.status, message: result.message, commands: parseJsonArray(result.commands_json) });
      await load();
    } catch (err) {
      setConnectorResult({ connector, status: 'Failed', message: err.message, commands: [] });
      setError(err.message);
    } finally {
      setDiscoveringConnectorId(null);
    }
  };

  const createMigrationPlan = async (event) => {
    event.preventDefault();
    setError('');
    try {
      await api('/migration-plans', {
        method: 'POST',
        body: JSON.stringify({
          ...migrationPlanForm,
          vm_ids: selectedVmIds,
          target_connector_id: Number(migrationPlanForm.target_connector_id),
        }),
      });
      setMigrationPlanForm(blankMigrationPlan);
      setSelectedVmIds([]);
      setShowPlanModal(false);
      setActive('plans');
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const executeMigrationPlan = async (plan) => {
    setError('');
    setExecutingPlanId(plan.id);
    try {
      const updated = await api(`/migration-plans/${plan.id}/execute`, { method: 'POST' });
      setSelectedPlanId(updated.id);
      setTaskPlanId(updated.id);
      await loadPlanExecution(updated.id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setExecutingPlanId(null);
    }
  };

  const launchMigrationPlan = async (plan) => {
    const confirmation = window.prompt(`Live migration can create, copy, stop, or move infrastructure resources.\n\nType the exact plan name to launch:\n${plan.name}`);
    if (confirmation === null) return;
    setError('');
    setLaunchingPlanId(plan.id);
    try {
      const response = await api(`/migration-plans/${plan.id}/launch`, {
        method: 'POST',
        body: JSON.stringify({ confirmation }),
      });
      syncPlanExecution(response);
      setSelectedPlanId(response.plan.id);
      setTaskPlanId(response.plan.id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setLaunchingPlanId(null);
    }
  };

  const continueMigrationPlan = async (plan) => {
    const confirmation = window.prompt(`Continue will reuse preserved staged artifacts for the last failed live migration.\n\nType the exact plan name to continue:\n${plan.name}`);
    if (confirmation === null) return;
    setError('');
    setContinuingPlanId(plan.id);
    try {
      const response = await api(`/migration-plans/${plan.id}/continue`, {
        method: 'POST',
        body: JSON.stringify({ confirmation }),
      });
      syncPlanExecution(response);
      setSelectedPlanId(response.plan.id);
      setTaskPlanId(response.plan.id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setContinuingPlanId(null);
    }
  };

  const forceStopMigrationPlan = async (plan) => {
    const confirmation = window.prompt(`Force stop will cancel the active Spark execution for this plan.\n\nType the exact plan name to force stop:\n${plan.name}`);
    if (confirmation === null) return;
    setError('');
    setForceStoppingPlanId(plan.id);
    try {
      const response = await api(`/migration-plans/${plan.id}/force-stop`, {
        method: 'POST',
        body: JSON.stringify({ confirmation }),
      });
      syncPlanExecution(response);
      setSelectedPlanId(response.plan.id);
      setTaskPlanId(response.plan.id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setForceStoppingPlanId(null);
    }
  };

  const deleteMigrationPlan = async (plan) => {
    if (!window.confirm(`Delete migration plan "${plan.name}"?`)) return;
    setError('');
    try {
      await api(`/migration-plans/${plan.id}`, { method: 'DELETE' });
      setPlanExecutions((current) => {
        const next = { ...current };
        delete next[plan.id];
        return next;
      });
      if (selectedPlanId === plan.id) setSelectedPlanId(null);
      if (taskPlanId === plan.id) setTaskPlanId(null);
      if (editingPlanId === plan.id) {
        setEditingPlanId(null);
        setEditPlanForm(blankMigrationPlan);
      }
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const openPlanTask = async (plan) => {
    setError('');
    setTaskPlanId(plan.id);
    if (!plan.spark_job_id && plan.status !== 'Preflight running' && plan.status !== 'Preflight ready' && plan.status !== 'Blocked') return;
    try {
      await loadPlanExecution(plan.id);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const editMigrationPlan = (plan) => {
    setEditingPlanId(plan.id);
    setEditPlanForm(planToForm(plan));
  };

  const cancelMigrationPlanEdit = () => {
    setEditingPlanId(null);
    setEditPlanForm(blankMigrationPlan);
  };

  const saveMigrationPlanEdit = async (event) => {
    event.preventDefault();
    if (!editingPlanId) return;
    setError('');
    try {
      await api(`/migration-plans/${editingPlanId}`, {
        method: 'PUT',
        body: JSON.stringify({
          ...editPlanForm,
          vm_ids: editPlanForm.vm_ids,
          target_connector_id: Number(editPlanForm.target_connector_id),
        }),
      });
      cancelMigrationPlanEdit();
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const changeStatus = async (vm, status) => {
    await api(`/vms/${vm.id}/status`, { method: 'PATCH', body: JSON.stringify({ status, note: 'Updated from DS Shift dashboard' }) });
    await load();
  };

  const createWave = async (event) => {
    event.preventDefault();
    setError('');
    try {
      await api('/waves', {
        method: 'POST',
        body: JSON.stringify({
          ...waveForm,
          plan_ids: waveForm.plan_ids,
        }),
      });
      setWaveForm(blankWave);
      setShowWaveModal(false);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const editWave = (wave) => {
    setEditingWaveId(wave.id);
    setWaveForm(waveToForm(wave));
    setShowWaveModal(true);
  };

  const cancelWaveEdit = () => {
    setEditingWaveId(null);
    setWaveForm(blankWave);
    setShowWaveModal(false);
  };

  const saveWave = async (event) => {
    event.preventDefault();
    if (editingWaveId) {
      setError('');
      try {
        await api(`/waves/${editingWaveId}`, {
          method: 'PUT',
          body: JSON.stringify({
            ...waveForm,
            plan_ids: waveForm.plan_ids,
          }),
        });
        cancelWaveEdit();
        await load();
      } catch (err) {
        setError(err.message);
      }
      return;
    }
    await createWave(event);
  };

  const deleteWave = async (wave) => {
    if (!window.confirm(`Delete migration wave "${wave.wave_name}"?`)) return;
    setError('');
    try {
      await api(`/waves/${wave.id}`, { method: 'DELETE' });
      if (editingWaveId === wave.id) cancelWaveEdit();
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const executeWave = async (wave) => {
    const confirmation = window.prompt(`Executing a migration wave will launch all associated migration plans.\n\nType the exact wave name to execute:\n${wave.wave_name}`);
    if (confirmation === null) return;
    setError('');
    setExecutingWaveId(wave.id);
    try {
      await api(`/waves/${wave.id}/execute`, {
        method: 'POST',
        body: JSON.stringify({ confirmation }),
      });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setExecutingWaveId(null);
    }
  };

  const csv = useMemo(() => {
    const rows = [['VM Name', 'Source', 'Target', 'CPU', 'Memory GB', 'Disk GB', 'Criticality', 'Status']];
    vms.forEach((vm) => rows.push([vm.vm_name, vm.source_platform, vm.target_platform, vm.cpu, vm.memory_gb, vm.disk_gb, vm.criticality, vm.current_status]));
    return rows.map((r) => r.map((c) => `"${String(c ?? '').replaceAll('"', '""')}"`).join(',')).join('\n');
  }, [vms]);

  if (!token) {
    if (!bootstrapChecked) return <AuthLoading error={error} />;
    if (bootstrapRequired) return <BootstrapSetup form={bootstrapForm} setForm={setBootstrapForm} submit={bootstrapAdmin} error={error} />;
    return <Login form={loginForm} setForm={setLoginForm} submit={login} error={error} />;
  }

  const selectedPlan = migrationPlans.find((plan) => plan.id === selectedPlanId) || null;
  const taskPlan = migrationPlans.find((plan) => plan.id === taskPlanId) || null;
  const editingPlan = migrationPlans.find((plan) => plan.id === editingPlanId) || null;

  const nav = [
    ['dashboard', Gauge, 'Dashboard'],
    ['connectors', ServerCog, 'Connectors'],
    ['hosts', Network, 'Hosts'],
    ['inventory', HardDrive, 'VM Inventory'],
    ['plans', Layers, 'Migration Plans'],
    ['waves', CalendarClock, 'Waves'],
    ['reports', FileText, 'Reports'],
    ...(user?.role === 'admin' ? [['users', Users, 'Users']] : []),
    ['settings', Settings, 'Settings'],
  ];
  displayTimezone = settings.default_timezone || 'Asia/Riyadh';

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <button className="brand-link" type="button" onClick={() => setActive('dashboard')} title="Open Dashboard">
            <BrandLogo className="brand-logo" />
          </button>
        </div>
        <nav>
          {nav.map(([key, Icon, label]) => (
            <button key={key} className={active === key ? 'active' : ''} onClick={() => setActive(key)} title={label}>
              <span className="nav-icon"><Icon size={18} /></span>{label}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <small>{about.product || 'DS Shift'}</small>
          <strong>Version {about.version || '1.0 RC1'}</strong>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p>{settings.company_name || 'Defined Solutions'}</p>
            <h1>{titleFor(active)}</h1>
          </div>
          <div className="toolbar">
            <button className="icon-button" onClick={refreshCurrentView} title="Refresh current page"><RefreshCw size={18} /></button>
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

        {active === 'dashboard' && <Dashboard summary={summary} plans={migrationPlans} connectors={connectors} />}
        {active === 'inventory' && <Inventory vms={vms} connectors={connectors} selectedVmIds={selectedVmIds} setSelectedVmIds={setSelectedVmIds} openPlan={() => { setMigrationPlanForm(blankMigrationPlan); setShowPlanModal(true); }} changeStatus={changeStatus} />}
        {active === 'connectors' && <ConnectorWorkspace category={connectorCategory} setCategory={setConnectorCategory} catalog={connectorCatalog} connectors={connectors} form={connectorForm} setForm={setConnectorForm} save={saveConnector} editForm={editConnectorForm} setEditForm={setEditConnectorForm} saveEdit={saveConnectorEdit} discover={discoverConnector} validate={validateConnector} edit={editConnector} remove={deleteConnector} cancelEdit={cancelConnectorEdit} editingConnectorId={editingConnectorId} discoveringConnectorId={discoveringConnectorId} result={connectorResult} />}
        {active === 'hosts' && <HostsView hosts={hosts} connectors={connectors} />}
        {active === 'plans' && <MigrationPlans plans={migrationPlans} vms={vms} connectors={connectors} preflight={executeMigrationPlan} launch={launchMigrationPlan} resume={continueMigrationPlan} forceStop={forceStopMigrationPlan} remove={deleteMigrationPlan} executeTask={openPlanTask} editPlan={editMigrationPlan} executingPlanId={executingPlanId} launchingPlanId={launchingPlanId} continuingPlanId={continuingPlanId} forceStoppingPlanId={forceStoppingPlanId} selectedPlan={selectedPlan} setSelectedPlanId={setSelectedPlanId} taskPlan={taskPlan} closeTask={() => setTaskPlanId(null)} taskExecution={taskPlan ? planExecutions[taskPlan.id] : null} user={user} />}
        {active === 'waves' && <Waves waves={waves} plans={migrationPlans} user={user} editingWaveId={editingWaveId} executingWaveId={executingWaveId} openCreate={() => { setEditingWaveId(null); setWaveForm(blankWave); setShowWaveModal(true); }} editWave={editWave} deleteWave={deleteWave} executeWave={executeWave} />}
        {active === 'reports' && <Reports csv={csv} vms={vms} />}
        {active === 'users' && user?.role === 'admin' && <UsersView currentUser={user} users={users} form={userForm} setForm={setUserForm} save={saveUser} editForm={editUserForm} setEditForm={setEditUserForm} saveEdit={saveUserEdit} edit={editUser} remove={deleteUser} editingUserId={editingUserId} cancelEdit={cancelUserEdit} setError={setError} />}
        {active === 'settings' && <SettingsView settings={settings} setSettings={setSettings} save={saveSettings} serviceStatus={serviceStatus} serviceStatusLoading={serviceStatusLoading} about={about} />}
        {showPlanModal && <MigrationPlanModal mode="create" title="Create executable migration plan" submitLabel="Save plan" selectedVmIds={selectedVmIds} vms={vms} connectors={connectors} form={migrationPlanForm} setForm={setMigrationPlanForm} save={createMigrationPlan} close={() => setShowPlanModal(false)} />}
        {editingPlan && <MigrationPlanModal mode="edit" title={`Edit migration plan: ${editingPlan.name}`} submitLabel="Save changes" selectedVmIds={parseJsonArray(editingPlan.vm_ids_json)} sourceConnectorIdOverride={editingPlan.source_connector_id} vms={vms} connectors={connectors} form={editPlanForm} setForm={setEditPlanForm} save={saveMigrationPlanEdit} close={cancelMigrationPlanEdit} />}
        {showWaveModal && <WaveModal mode={editingWaveId ? 'edit' : 'create'} plans={migrationPlans} form={waveForm} setForm={setWaveForm} save={saveWave} close={editingWaveId ? cancelWaveEdit : () => setShowWaveModal(false)} />}
      </main>
    </div>
  );
}

function Login({ form, setForm, submit, error }) {
  return <div className="login-screen"><form className="login-panel" onSubmit={submit}><BrandLogo className="login-brand-logo" /><div className="login-copy"><h1>DS Shift</h1><p>Defined Solutions migration command center</p></div>{error && <div className="alert">{error}</div>}<Input label="Username" value={form.username} onChange={(v) => setForm({ ...form, username: v })} required /><Input label="Password" type="password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} required /><button className="primary"><KeyRound size={16} /> Sign in</button></form></div>;
}

function BootstrapSetup({ form, setForm, submit, error }) {
  return <div className="login-screen"><form className="login-panel" onSubmit={submit}><BrandLogo className="login-brand-logo" /><div className="login-copy"><h1>Set admin password</h1><p>Create the first DS Shift administrator account.</p></div>{error && <div className="alert">{error}</div>}<Input label="Username" value="admin" onChange={() => {}} disabled /><Input label="Admin password" type="password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} required /><Input label="Confirm password" type="password" value={form.confirm_password} onChange={(v) => setForm({ ...form, confirm_password: v })} required /><button className="primary"><KeyRound size={16} /> Create admin account</button></form></div>;
}

function AuthLoading({ error }) {
  return <div className="login-screen"><div className="login-panel"><BrandLogo className="login-brand-logo" /><div className="login-copy"><h1>DS Shift</h1><p>Checking first-run setup</p></div>{error && <div className="alert">{error}</div>}</div></div>;
}

function titleFor(active) {
  return ({ connectors: 'Connectors', hosts: 'Discovered Hosts', dashboard: 'Migration Command Center', inventory: 'VM Inventory', plans: 'Migration Plans', waves: 'Migration Waves', reports: 'Reports', users: 'User Management', settings: 'Settings Control' })[active];
}

function Dashboard({ summary, plans, connectors }) {
  const cards = [
    ['Migration plans', summary?.total_plans ?? 0, Layers],
    ['VMs discovered', summary?.vms_discovered ?? 0, HardDrive],
    ['VMs planned', summary?.vms_planned ?? 0, CalendarClock],
    ['VMs migrated', summary?.vms_migrated ?? 0, ArrowRightLeft],
    ['Failed or blocked', summary?.vms_failed_or_blocked ?? 0, Network],
    ['Connectors', connectors.length, ServerCog],
  ];
  return <section><div className="metric-grid">{cards.map(([label, value, Icon]) => <div className="metric" key={label}><Icon size={22} /><span>{label}</span><strong>{value}</strong></div>)}</div><DashboardPlans plans={plans} connectors={connectors} /></section>;
}

function DashboardPlans({ plans, connectors }) {
  return <div className="stack"><div className="section-heading"><div><h2>Migration Plans</h2><p>Current migration plans and their execution readiness.</p></div></div><div className="table-wrap"><table><thead><tr><th>Plan</th><th>Migration</th><th>VMs</th><th>Status</th><th>Executed</th></tr></thead><tbody>{plans.length ? plans.map((plan) => {
    const source = connectors.find((row) => row.id === plan.source_connector_id);
    const target = connectors.find((row) => row.id === plan.target_connector_id);
    return <tr key={plan.id}><td><strong>{plan.name}</strong></td><td>{source?.name || plan.source_connector_id} → {target?.name || plan.target_connector_id}</td><td>{parseJsonArray(plan.vm_ids_json).length}</td><td><Badge value={plan.status} /></td><td>{formatDateTime(plan.executed_at)}</td></tr>;
  }) : <tr><td colSpan="5">No migration plans have been created.</td></tr>}</tbody></table></div></div>;
}

function Inventory({ vms, connectors, selectedVmIds, setSelectedVmIds, openPlan, changeStatus }) {
  const [searchTerm, setSearchTerm] = useState('');
  const selectedVms = vms.filter((vm) => selectedVmIds.includes(vm.id));
  const selectedConnectorId = selectedVms[0]?.connector_id;
  const normalizedSearch = searchTerm.trim().toLowerCase();
  const filteredVms = vms.filter((vm) => {
    if (!normalizedSearch) return true;
    const connector = connectors.find((row) => row.id === vm.connector_id);
    return [
      vm.vm_name,
      connector?.name,
      vm.host_name,
      vm.source_platform,
      vm.os_type,
      vm.ip_address,
      vm.current_status,
    ].some((value) => String(value || '').toLowerCase().includes(normalizedSearch));
  });
  const selectableVms = selectedConnectorId ? filteredVms.filter((vm) => vm.connector_id === selectedConnectorId) : filteredVms;
  const allSelectableSelected = selectableVms.length > 0 && selectableVms.every((vm) => selectedVmIds.includes(vm.id));
  const toggleVm = (vm) => {
    if (selectedVmIds.includes(vm.id)) {
      setSelectedVmIds(selectedVmIds.filter((id) => id !== vm.id));
      return;
    }
    if (selectedConnectorId && vm.connector_id !== selectedConnectorId) return;
    setSelectedVmIds([...selectedVmIds, vm.id]);
  };
  const toggleAll = () => {
    if (allSelectableSelected) {
      const visibleIds = new Set(selectableVms.map((vm) => vm.id));
      setSelectedVmIds(selectedVmIds.filter((id) => !visibleIds.has(id)));
    } else {
      setSelectedVmIds([...new Set([...selectedVmIds, ...selectableVms.map((vm) => vm.id)])]);
    }
  };
  return <section className="stack"><div className="inventory-actions"><div><strong>{selectedVmIds.length} VM{selectedVmIds.length === 1 ? '' : 's'} selected</strong><span>Select VMs from one source connector to build an executable migration plan.</span></div><button className="primary" disabled={!selectedVmIds.length} onClick={openPlan}><Plus size={16} /> Create Migration Plan</button></div><label className="inventory-search"><Search size={18} /><input type="search" value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} placeholder="Search VMs by name, connector, host, platform, OS, IP, or status" aria-label="Search VM Inventory" />{searchTerm && <button type="button" className="icon-button" onClick={() => setSearchTerm('')} title="Clear VM search" aria-label="Clear VM search"><X size={16} /></button>}<span>{filteredVms.length} of {vms.length} VMs</span></label><div className="table-wrap"><table><thead><tr><th><input type="checkbox" aria-label="Select all filtered VMs from source connector" checked={allSelectableSelected} onChange={toggleAll} /></th><th>VM</th><th>Source Connector</th><th>Host</th><th>Platform</th><th>OS</th><th>Size</th><th>IP address</th><th>Status</th><th>Change status</th></tr></thead><tbody>{filteredVms.map((vm) => {
    const connector = connectors.find((row) => row.id === vm.connector_id);
    const disabled = Boolean(selectedConnectorId && vm.connector_id !== selectedConnectorId);
    return <tr key={vm.id} className={disabled ? 'disabled-row' : ''}><td><input type="checkbox" checked={selectedVmIds.includes(vm.id)} disabled={disabled} onChange={() => toggleVm(vm)} /></td><td><strong>{vm.vm_name}</strong></td><td>{connector?.name || '-'}</td><td>{vm.host_name || '-'}</td><td>{vm.source_platform}</td><td>{vm.os_type || 'Unknown'}</td><td>{vm.cpu} CPU / {vm.memory_gb} GB / {vm.disk_gb} GB</td><td>{vm.ip_address || '-'}</td><td><Badge value={vm.current_status} /></td><td><select value={vm.current_status} onChange={(e) => changeStatus(vm, e.target.value)}>{statuses.map((s) => <option key={s}>{s}</option>)}</select></td></tr>;
  })}{!filteredVms.length && <tr><td colSpan="10">No VMs match “{searchTerm}”.</td></tr>}</tbody></table></div></section>;
}

function MigrationPlanModal({ mode = 'create', title, submitLabel, selectedVmIds, sourceConnectorIdOverride = null, vms, connectors, form, setForm, save, close }) {
  const effectiveVmIds = mode === 'edit' ? form.vm_ids : selectedVmIds;
  const selectedVms = vms.filter((vm) => effectiveVmIds.includes(vm.id));
  const sourceConnectorId = sourceConnectorIdOverride || selectedVms[0]?.connector_id;
  const sourceConnector = connectors.find((row) => row.id === sourceConnectorId);
  const targetConnectors = connectors.filter((row) => row.id !== sourceConnectorId);
  const targetConnector = targetConnectors.find((row) => String(row.id) === String(form.target_connector_id)) || targetConnectors[0] || null;
  const sourceConnectorVms = vms.filter((vm) => vm.connector_id === sourceConnectorId);
  const fieldConfig = migrationPlanFieldConfig(sourceConnector?.connector_type, targetConnector?.connector_type);
  useEffect(() => {
    if (!form.target_connector_id && targetConnectors[0]) {
      setForm((current) => ({ ...current, target_connector_id: String(targetConnectors[0].id) }));
    }
  }, [form.target_connector_id, targetConnectors, setForm]);
  const toggleVm = (vmId) => {
    const nextIds = form.vm_ids.includes(vmId) ? form.vm_ids.filter((id) => id !== vmId) : [...form.vm_ids, vmId];
    setForm({ ...form, vm_ids: nextIds });
  };
  const option = (key, value) => setForm({ ...form, execution_options: { ...form.execution_options, [key]: value } });
  const setLocation = (value) => setForm({
    ...form,
    execution_options: {
      ...form.execution_options,
      source_region: value,
      source_zone: value,
    },
  });
  const setTargetLocation = (value) => setForm({
    ...form,
    execution_options: {
      ...form.execution_options,
      target_region: value,
      target_zone: value,
    },
  });
  return <Modal title={title} onClose={close}><FormPanel title="" onSubmit={save}><div className="tip"><strong>Selected source:</strong> {sourceConnector?.name || 'Unknown'} · {effectiveVmIds.length} VM{effectiveVmIds.length === 1 ? '' : 's'}<br />Preflight validates source power state, tools, credentials, storage, and network without changing infrastructure. Admin-only Launch remains protected by the Spark live-execution switch and exact plan-name confirmation.</div><Input label="Plan name" value={form.name} onChange={(value) => setForm({ ...form, name: value })} required />{mode === 'edit' && <div className="table-wrap"><table><thead><tr><th></th><th>VM</th><th>Host</th><th>OS</th><th>Status</th></tr></thead><tbody>{sourceConnectorVms.map((vm) => <tr key={vm.id}><td><input className="table-checkbox" type="checkbox" checked={form.vm_ids.includes(vm.id)} onChange={() => toggleVm(vm.id)} /></td><td>{vm.vm_name}</td><td>{vm.host_name || '-'}</td><td>{vm.os_type || 'Unknown'}</td><td><Badge value={vm.current_status} /></td></tr>)}</tbody></table></div>}<Select label="Target connector" value={form.target_connector_id} options={targetConnectors.map((connector) => [connector.id, `${connector.name} (${connector.connector_type})`])} onChange={(value) => setForm({ ...form, target_connector_id: value })} />{targetConnector && <div className="tip"><strong>Execution inputs for:</strong> {sourceConnector?.connector_type || 'Unknown source'} to {targetConnector.connector_type}<br />{fieldConfig.description}</div>}{Boolean(fieldConfig.fields.length) && <div className="execution-options">{fieldConfig.fields.map((field) => renderMigrationPlanField(field, form, setForm, option, setLocation, setTargetLocation))}</div>}{!fieldConfig.fields.length && targetConnector && <div className="tip">This migration path currently does not require extra plan-level execution inputs in the UI.</div>}<label className="check-card"><input className="table-checkbox" type="checkbox" checked={form.keep_source_vm !== false} onChange={(event) => setForm({ ...form, keep_source_vm: event.target.checked })} /><span><strong>Keep source VM after successful migration</strong><small>Leave the source powered off or unchanged after a successful cutover instead of removing it.</small></span></label><TextArea label="Notes" value={form.notes} onChange={(value) => setForm({ ...form, notes: value })} /><div className="button-row"><button className="primary" disabled={!targetConnectors.length || (mode === 'edit' && !form.vm_ids.length)}><Save size={16} /> {submitLabel}</button><button className="secondary" type="button" onClick={close}><X size={16} /> Cancel</button></div>{mode === 'edit' && <div className="tip">Edit can reassign VMs from the same source connector. It is blocked while the plan is queued or running.</div>}</FormPanel></Modal>;
}

function MigrationPlans({ plans, vms, connectors, preflight, launch, resume, forceStop, remove, executeTask, editPlan, executingPlanId, launchingPlanId, continuingPlanId, forceStoppingPlanId, selectedPlan, setSelectedPlanId, taskPlan, closeTask, taskExecution, user }) {
  const planVms = selectedPlan ? vms.filter((vm) => parseJsonArray(selectedPlan.vm_ids_json).includes(vm.id)) : [];
  const results = parseJsonArray(selectedPlan?.results_json);
  return <section className="stack"><div className="about plan-about"><h2>Executable migration plans</h2><p>Preflight checks readiness without changing infrastructure. Admin-only Launch queues live execution through the Spark Engine worker pool when the source-target adapter and required options are available.</p></div><div className="table-wrap"><table><thead><tr><th>Plan</th><th>Migration</th><th>VMs</th><th>Status</th><th>Executed</th><th>Actions</th></tr></thead><tbody>{plans.map((plan) => {
    const source = connectors.find((row) => row.id === plan.source_connector_id);
    const target = connectors.find((row) => row.id === plan.target_connector_id);
    const vmCount = parseJsonArray(plan.vm_ids_json).length;
    const active = ['Queued', 'Running'].includes(plan.status);
    const canResume = planCanContinue(plan, user);
    return <tr key={plan.id}><td><strong>{plan.name}</strong></td><td>{source?.name || plan.source_connector_id} → {target?.name || plan.target_connector_id}</td><td>{vmCount}</td><td><Badge value={plan.status} /></td><td>{formatDateTime(plan.executed_at)}</td><td><div className="button-row compact"><button className="mini" onClick={() => setSelectedPlanId(plan.id)}><FileText size={14} /> Details</button><button className="mini" onClick={() => executeTask(plan)}><Gauge size={14} /> Task</button><button className="mini" disabled={active} onClick={() => editPlan(plan)}><Edit3 size={14} /> Edit</button><button className="mini" disabled={active || executingPlanId === plan.id} onClick={() => preflight(plan)}><CheckCircle2 size={14} /> {executingPlanId === plan.id ? 'Checking...' : 'Preflight'}</button>{user?.role === 'admin' && <button className="mini" disabled={active || launchingPlanId === plan.id} onClick={() => launch(plan)}><Play size={14} /> {launchingPlanId === plan.id ? 'Queueing...' : 'Launch'}</button>}{user?.role === 'admin' && canResume && plan.status === 'Failed' && <button className="mini" disabled={active || continuingPlanId === plan.id} onClick={() => resume(plan)}><Play size={14} /> {continuingPlanId === plan.id ? 'Continuing...' : 'Continue'}</button>}<button className="mini danger-button" disabled={active} onClick={() => remove(plan)}><Trash2 size={14} /> Delete</button></div></td></tr>;
  })}</tbody></table></div>{selectedPlan && <Modal title={selectedPlan.name} onClose={() => setSelectedPlanId(null)} wide><div className="plan-detail"><dl className="host-facts"><div><dt>Status</dt><dd>{selectedPlan.status}</dd></div><div><dt>Migration type</dt><dd>{selectedPlan.migration_type}</dd></div><div><dt>VMs</dt><dd>{planVms.length}</dd></div><div><dt>Keep source VM</dt><dd>{selectedPlan.keep_source_vm !== false ? 'Yes' : 'No'}</dd></div><div><dt>Spark job</dt><dd>{selectedPlan.spark_job_id || '-'}</dd></div><div><dt>Executed</dt><dd>{formatDateTime(selectedPlan.executed_at)}</dd></div></dl><div className="table-wrap"><table><thead><tr><th>VM</th><th>Source</th><th>OS</th><th>Status</th><th>Execution result</th></tr></thead><tbody>{planVms.map((vm) => {
    const result = results.find((row) => row.vm_id === vm.id);
    return <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.source_platform}</td><td>{vm.os_type || 'Unknown'}</td><td><Badge value={planDetailStatus(result, selectedPlan.status, vm.current_status)} /></td><td>{preflightDetail(result)}</td></tr>;
  })}</tbody></table></div></div></Modal>}{taskPlan && <MigrationTaskModal plan={taskPlan} execution={taskExecution} connectors={connectors} onClose={closeTask} onContinue={resume} onForceStop={forceStop} continuingPlanId={continuingPlanId} forceStoppingPlanId={forceStoppingPlanId} user={user} />}</section>;
}

function ConnectorWorkspace({ category, setCategory, catalog, connectors, ...props }) {
  const categories = catalog.categories || fallbackConnectorPlatforms;
  if (!category) {
    return <section className="connector-home"><div className="connector-engine-grid">{[['host', ServerCog, 'Host Connectors', 'On-premises and private virtualization platforms'], ['cloud', Cloud, 'Cloud Connectors', 'Public cloud compute platforms']].map(([key, Icon, title, description]) => {
      const engine = (catalog.engines || []).find((item) => item.category === key);
      const count = connectors.filter((connector) => connector.connector_category === key).length;
      return <button className="connector-engine-card" key={key} onClick={() => { const platform = categories[key][0]; setCategory(key); props.setForm({ ...blankConnector, connector_category: key, connector_type: platform.type, port: platform.default_port ?? '' }); }}><span className="connector-engine-icon"><Icon size={28} /></span><span><strong>{title}</strong><small>{description}</small><small>{count} configured connector{count === 1 ? '' : 's'} · Engine: {engine?.status || 'unknown'}</small></span></button>;
    })}</div><div className="about"><h2>Available connectors</h2><p>Select Host Connectors or Cloud Connectors to list existing connectors, create a new connector, validate credentials, and discover workloads.</p></div></section>;
  }
  const platforms = categories[category] || [];
  return <section className="stack"><div className="connector-section-header"><button className="secondary" onClick={() => { setCategory(''); props.cancelEdit(); }}>All connector engines</button><div><h2>{category === 'host' ? 'Host Connectors' : 'Cloud Connectors'}</h2><p>{platforms.map((platform) => platform.type).join(' · ')}</p></div></div><Connectors title={category === 'host' ? 'Host Connector' : 'Cloud Connector'} category={category} rows={connectors.filter((connector) => connector.connector_category === category)} platforms={platforms} {...props} /></section>;
}

function ConnectorFields({ form, setForm, category, platforms }) {
  const types = platforms.map((platform) => platform.type);
  const scopedForm = form.connector_category === category ? form : { ...blankConnector, connector_category: category, connector_type: types[0], port: platforms[0]?.default_port ?? '' };
  const update = (patch) => setForm({ ...scopedForm, ...patch, connector_category: category });
  const platform = platforms.find((item) => item.type === scopedForm.connector_type) || platforms[0];
  const updateCredential = (key, value) => update({ credential_payload: { ...scopedForm.credential_payload, [key]: value } });
  const onPlatformChange = (value) => {
    const selectedPlatform = platforms.find((item) => item.type === value);
    update({
      connector_type: value,
      endpoint: '',
      username: '',
      password: '',
      credential_reference: '',
      credential_payload: { ...blankConnector.credential_payload },
      port: selectedPlatform?.default_port ?? '',
    });
  };
  return <><Select label="Platform" value={scopedForm.connector_type} options={types} onChange={onPlatformChange} /><div className="tip"><strong>{platform?.tool}</strong><br />Endpoint: {platform?.endpoint_hint}<br />Credential: {platform?.credential_hint}</div><Input label="Connector name" value={scopedForm.name} onChange={(v) => update({ name: v })} required /><Input label={category === 'cloud' ? 'Region / Project / Subscription' : 'Host IP / Hostname'} value={scopedForm.endpoint} onChange={(v) => update({ endpoint: v })} />{category === 'host' && scopedForm.connector_type === 'VMware ESXi / vCenter' && <div className="execution-options"><Input label="Target vDC Name" value={scopedForm.target_vdc_name || ''} onChange={(v) => update({ target_vdc_name: v })} /><Input label="Target Cluster Name or Host Name" value={scopedForm.target_compute_name || ''} onChange={(v) => update({ target_compute_name: v })} /><Input label="Target Datastore" value={scopedForm.target_datastore || ''} onChange={(v) => update({ target_datastore: v })} /><Input label="Target Network" value={scopedForm.target_network || ''} onChange={(v) => update({ target_network: v })} /></div>}{category === 'host' && scopedForm.connector_type === 'KVM' && <div className="execution-options"><Input label="Target Storage Pool" value={scopedForm.target_storage_pool || ''} onChange={(v) => update({ target_storage_pool: v })} /><Input label="Target Network / Bridge" value={scopedForm.target_network || ''} onChange={(v) => update({ target_network: v })} /></div>}{renderConnectorCredentialFields(scopedForm, update, updateCredential)}<Input label="Environment" value={scopedForm.environment || ''} onChange={(v) => update({ environment: v })} /><TextArea label="Notes" value={scopedForm.notes || ''} onChange={(v) => update({ notes: v })} /></>;
}

function Connectors({ title, category, rows, form, setForm, save, editForm, setEditForm, saveEdit, platforms, discover, validate, edit, remove, cancelEdit, editingConnectorId, discoveringConnectorId, result }) {
  const isEditing = editingConnectorId && editForm.connector_category === category;
  const resultSuccess = ['Validated', 'Completed'].includes(result?.status);
  return <section className="split"><FormPanel title={`Add ${title}`} onSubmit={save}><ConnectorFields form={form} setForm={setForm} category={category} platforms={platforms} /><div className="tip">New connectors are stored by DS Shift and executed by the dedicated {category === 'host' ? 'Host' : 'Cloud'} Connector service.</div><button className="primary"><Save size={16} /> Add connector</button></FormPanel><div className="stack">{result && result.connector?.connector_category === category && <div className={`result ${resultSuccess ? 'success' : result.status === 'Discovering' ? '' : 'danger'}`}><strong>{result.status}</strong><span>{result.message}</span>{Boolean(result.commands?.length) && <code>{result.commands.join(' | ')}</code>}</div>}<div className="table-wrap"><table><thead><tr><th>Name</th><th>Platform</th><th>Endpoint</th><th>Credentials</th><th>Status</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.name}</td><td>{row.connector_type}</td><td>{row.endpoint || '-'}</td><td>{connectorCredentialSummary(row)}</td><td><Badge value={row.status} /></td><td><div className="button-row compact"><button className="mini" onClick={() => edit(row)}><Edit3 size={14} /> Edit</button><button className="mini" onClick={() => validate(row)}><CheckCircle2 size={14} /> Validate</button><button className="mini" disabled={discoveringConnectorId === row.id} onClick={() => discover(row)}><Search size={14} /> {discoveringConnectorId === row.id ? 'Discovering...' : 'Discover'}</button><button className="mini danger-button" onClick={() => remove(row)}><Trash2 size={14} /> Delete</button></div></td></tr>)}</tbody></table></div></div>{isEditing && <Modal title={`Edit ${title}`} onClose={cancelEdit}><FormPanel title="" onSubmit={saveEdit}><ConnectorFields form={editForm} setForm={setEditForm} category={category} platforms={platforms} /><div className="button-row"><button className="primary"><Save size={16} /> Save changes</button><button className="secondary" type="button" onClick={cancelEdit}><X size={16} /> Cancel</button></div></FormPanel></Modal>}</section>;
}

function HostsView({ hosts, connectors }) {
  const [selectedHost, setSelectedHost] = useState(null);
  if (!hosts.length) return <section className="about"><h2>No hosts discovered</h2><p>Open Connectors and run Discover on a Host Connector. DS Shift will add the host and its VMs here automatically.</p></section>;
  const selectedConnector = selectedHost ? connectors.find((row) => row.id === selectedHost.connector_id) : null;
  const selectedVms = parseJsonArray(selectedHost?.vms_json);
  return <section><div className="table-wrap hosts-table"><table><thead><tr><th>Host</th><th>Platform</th><th>Connector</th><th>Endpoint</th><th>Capacity</th><th>VMs</th><th>Status</th><th>Last discovery</th><th></th></tr></thead><tbody>{hosts.map((host) => {
    const connector = connectors.find((row) => row.id === host.connector_id);
    return <tr className="clickable-row" key={host.id} onClick={() => setSelectedHost(host)}><td><strong>{host.host_name}</strong></td><td>{host.platform}</td><td>{connector?.name || `Connector ${host.connector_id}`}</td><td>{host.endpoint || '-'}</td><td>{host.cpu} CPU / {host.memory_gb} GB</td><td>{host.vm_count}</td><td><Badge value={host.status} /></td><td>{formatDateTime(host.last_discovered_at)}</td><td><button className="mini" onClick={(event) => { event.stopPropagation(); setSelectedHost(host); }}><HardDrive size={14} /> View VMs</button></td></tr>;
  })}</tbody></table></div>{selectedHost && <Modal title={`${selectedHost.host_name} virtual machines`} onClose={() => setSelectedHost(null)} wide><div className="host-detail"><dl className="host-facts"><div><dt>Platform</dt><dd>{selectedHost.platform}</dd></div><div><dt>Connector</dt><dd>{selectedConnector?.name || `Connector ${selectedHost.connector_id}`}</dd></div><div><dt>Endpoint</dt><dd>{selectedHost.endpoint || '-'}</dd></div><div><dt>Capacity</dt><dd>{selectedHost.cpu} CPU / {selectedHost.memory_gb} GB</dd></div><div><dt>VMs</dt><dd>{selectedHost.vm_count}</dd></div><div><dt>Status</dt><dd>{selectedHost.status}</dd></div></dl><div className="table-wrap"><table><thead><tr><th>VM</th><th>OS</th><th>CPU</th><th>Memory</th><th>Disk</th><th>IP address</th><th>Power</th></tr></thead><tbody>{selectedVms.length ? selectedVms.map((vm, index) => <tr key={`${vm.vm_name}-${index}`}><td>{vm.vm_name}</td><td>{vm.os_type || 'Unknown'}</td><td>{vm.cpu || 0}</td><td>{vm.memory_gb || 0} GB</td><td>{vm.disk_gb || 0} GB</td><td>{vm.ip_address || '-'}</td><td><Badge value={vm.power_state || vm.current_status || 'Discovered'} /></td></tr>) : <tr><td colSpan="7">No VMs reported by this host.</td></tr>}</tbody></table></div></div></Modal>}</section>;
}

function Waves({ waves, plans, user, editingWaveId, executingWaveId, openCreate, editWave, deleteWave, executeWave }) {
  return <section className="stack"><div className="inventory-actions"><div><strong>{waves.length} migration wave{waves.length === 1 ? '' : 's'}</strong><span>Group migration plans into execution waves.</span></div><button className="primary" onClick={openCreate}><Plus size={16} /> Create Wave</button></div><div className="table-wrap"><table><thead><tr><th>Wave</th><th>Plans</th><th>Window</th><th>Status</th><th>Notes</th><th>Actions</th></tr></thead><tbody>{waves.length ? waves.map((wave) => {
    const planIds = parseJsonArray(wave.plan_ids_json);
    const labels = planIds.map((id) => plans.find((plan) => plan.id === id)?.name || `Plan ${id}`);
    return <tr key={wave.id}><td><strong>{wave.wave_name}</strong></td><td>{labels.length ? labels.join(', ') : '-'}</td><td>{wave.planned_window || '-'}</td><td><Badge value={wave.status} /></td><td>{wave.notes || '-'}</td><td><div className="button-row compact"><button className="mini" onClick={() => editWave(wave)} disabled={executingWaveId === wave.id}><Edit3 size={14} /> Edit</button><button className="mini danger-button" onClick={() => deleteWave(wave)} disabled={executingWaveId === wave.id}><Trash2 size={14} /> Delete</button>{user?.role === 'admin' && <button className="mini" onClick={() => executeWave(wave)} disabled={executingWaveId === wave.id}>{executingWaveId === wave.id ? <RefreshCw size={14} className="spin" /> : <Play size={14} />} {executingWaveId === wave.id ? 'Executing' : 'Execute Wave'}</button>}</div></td></tr>;
  }) : <tr><td colSpan="6">No migration waves have been created.</td></tr>}</tbody></table></div>{editingWaveId && <div className="tip">Editing wave assignments updates the member VMs linked to that wave.</div>}</section>;
}

function Reports({ csv, vms }) {
  const download = () => {
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ds-shift-vm-readiness.csv';
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

function SettingsView({ settings, setSettings, save, serviceStatus, serviceStatusLoading, about }) {
  return <section className="split"><FormPanel title="Application settings" onSubmit={save}><div className="settings-identity-card"><div className="settings-identity-copy"><span className="settings-identity-label">System identity</span><strong>{settings.product_name || about.product || 'DS Shift'}</strong><p>{settings.company_name || about.brand || 'Defined Solutions'}</p></div></div><Select label="Default timezone" value={settings.default_timezone || 'Asia/Riyadh'} options={timezoneOptions} onChange={(v) => setSettings({ ...settings, default_timezone: v })} /><TextArea label="Banner message" value={settings.banner_message || ''} onChange={(v) => setSettings({ ...settings, banner_message: v })} /><div className="tip">Product name and company name are fixed system identity values. The selected timezone is used across GUI date and log timestamp rendering.</div><div className="button-row"><button className="primary"><Save size={16} /> Save settings</button></div></FormPanel><div className="stack"><ServiceStatusPanel data={serviceStatus} loading={serviceStatusLoading} /></div></section>;
}

function ServiceStatusPanel({ data, loading }) {
  return <div className="service-status-panel"><div className="service-status-header"><div><h2>Services status</h2><p>Live Docker container state, refreshed every 10 seconds.</p></div>{loading && <span className="service-refreshing">Refreshing</span>}</div>{data.monitor_error && <div className="alert">{data.monitor_error}</div>}<div className="service-status-list">{data.services.map((service) => <div className="service-status-row" key={service.service}><div><strong>{service.name}</strong><small>{service.detail || service.container_state}</small></div><ServiceState status={service.status} /></div>)}</div></div>;
}

function ServiceState({ status }) {
  const normalized = status === 'UP' ? 'up' : status === 'RESTARTING' ? 'restarting' : 'down';
  return <span className={`service-state ${normalized}`}><span />{status}</span>;
}

function UserAvatar({ user, large = false }) {
  const className = `user-avatar${large ? ' large' : ''}`;
  if (user?.profile_photo) return <span className={className}><img src={user.profile_photo} alt={`${user.username || 'User'} profile`} /></span>;
  return <span className={className}><UserRound size={large ? 34 : 20} /></span>;
}

function BrandLogo({ className }) {
  return <img className={className} src="/ds-shift-logo.png" alt="DS Shift by Defined Solutions - Any-to-any workload migration" />;
}

function StatusBoard({ vms }) {
  return <div className="table-wrap"><table><thead><tr><th>VM</th><th>Source</th><th>Host</th><th>Criticality</th><th>Status</th></tr></thead><tbody>{vms.map((vm) => <tr key={vm.id}><td>{vm.vm_name}</td><td>{vm.source_platform}</td><td>{vm.host_name || '-'}</td><td>{vm.criticality}</td><td><Badge value={vm.current_status} /></td></tr>)}</tbody></table></div>;
}

function Badge({ value }) {
  const normalized = (value || '').toLowerCase();
  const kind = normalized.includes('failed') || normalized.includes('blocked') || normalized.includes('rolled') || normalized === 'inactive'
    ? 'danger'
    : normalized.includes('running') || normalized.includes('queued') || normalized.includes('starting') || normalized.includes('checking') || normalized.includes('executing')
      ? 'warning'
      : normalized.includes('completed') || normalized.includes('succeeded') || normalized.includes('validated') || normalized.includes('ready') || normalized === 'active'
        ? 'success'
        : 'neutral';
  return <span className={`badge ${kind}`}>{value}</span>;
}

function FormPanel({ title, onSubmit, children }) {
  return <form className="form-panel" onSubmit={onSubmit}>{title && <h2>{title}</h2>}{children}</form>;
}

function Modal({ title, onClose, children, wide = false }) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}><div className={`modal-panel${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}><div className="modal-header"><h2>{title}</h2><button className="icon-button" type="button" onClick={onClose} title="Close"><X size={18} /></button></div>{children}</div></div>;
}

function MigrationTaskModal({ plan, execution, connectors, onClose, onContinue, onForceStop, continuingPlanId, forceStoppingPlanId, user }) {
  const source = connectors.find((row) => row.id === plan.source_connector_id);
  const target = connectors.find((row) => row.id === plan.target_connector_id);
  const storedResults = parseJsonArray(plan.results_json);
  const job = execution?.job || null;
  const taskRows = job?.tasks?.length ? job.tasks : storedResults.filter((row) => row.kind === 'task');
  const vmResults = job?.vm_results?.length ? job.vm_results : storedResults.filter((row) => row.vm_id);
  const progressPercent = normalizeProgress(job?.progress_percent ?? taskRows.reduce((current, row) => Math.max(current, taskPercent(row)), 0));
  const summaryMessage = job?.message || vmResults.find((row) => row.message)?.message || (plan.status === 'Preflight running' ? 'Preflight running' : 'Not executed yet');
  const canContinue = taskCanContinue(plan, user);
  const canForceStop = user?.role === 'admin' && plan?.spark_job_id && ['Queued', 'Running'].includes(plan?.status);
  const isTaskMode = Boolean(plan.spark_job_id) || plan.status === 'Preflight running' || plan.status === 'Preflight ready' || plan.status === 'Blocked';
  return <Modal title={`Task: ${plan.name}`} onClose={onClose} wide><div className="plan-detail"><dl className="host-facts"><div><dt>Status</dt><dd>{plan.status}</dd></div><div><dt>Migration</dt><dd>{source?.name || plan.source_connector_id} → {target?.name || plan.target_connector_id}</dd></div><div><dt>Spark job</dt><dd>{plan.spark_job_id || '-'}</dd></div><div><dt>Progress</dt><dd>{progressPercent}%</dd></div><div><dt>Executed</dt><dd>{formatDateTime(plan.executed_at)}</dd></div></dl><div className="task-summary"><div className="task-progress"><div className={`task-progress-bar ${taskProgressTone(plan.status)}`}><span style={{ width: `${progressPercent}%` }} /></div><strong>{progressPercent}%</strong></div><p>{summaryMessage}</p>{(canContinue || canForceStop) && <div className="button-row">{canContinue && <button className="primary" type="button" disabled={continuingPlanId === plan.id} onClick={() => onContinue(plan)}><Play size={16} /> {continuingPlanId === plan.id ? 'Continuing...' : 'Continue from staging'}</button>}{canForceStop && <button className="danger-button" type="button" disabled={forceStoppingPlanId === plan.id} onClick={() => onForceStop(plan)}><Square size={16} /> {forceStoppingPlanId === plan.id ? 'Force stopping...' : 'Force stop task'}</button>}</div>}</div>{!isTaskMode && <div className="about"><h2>Not executed yet</h2><p>This migration plan has not been sent to the Spark Engine. Run Preflight or Launch first to generate task telemetry.</p></div>}{Boolean(taskRows.length) && <div className="table-wrap"><table><thead><tr><th>Step</th><th>Status</th><th>Reached</th><th>Detail</th><th>Updated</th></tr></thead><tbody>{taskRows.map((task, index) => <tr key={`${task.key || task.task_code || 'task'}-${index}`}><td>{taskTitle(task, index)}</td><td><Badge value={taskStatusLabel(task.status)} /></td><td>{taskPercent(task)}%</td><td>{task.message || '-'}</td><td>{formatDateTime(taskUpdatedAt(task))}</td></tr>)}</tbody></table></div>}{plan.spark_job_id && !taskRows.length && <div className="about"><h2>Task stream pending</h2><p>The Spark Engine job exists, but it has not published task entries yet. Refresh after a few seconds if this persists.</p></div>}{Boolean(vmResults.length) && <div className="table-wrap"><table><thead><tr><th>VM ID</th><th>Status</th><th>Detail</th></tr></thead><tbody>{vmResults.map((row, index) => <tr key={`${row.vm_id || 'result'}-${index}`}><td>{row.vm_id || '-'}</td><td><Badge value={row.ok ? 'Completed' : row.message?.toLowerCase().includes('force-stop') || row.message?.toLowerCase().includes('cancel') ? 'Canceled' : 'Failed'} /></td><td>{row.message || preflightDetail(row)}</td></tr>)}</tbody></table></div>}</div></Modal>;
}

function WaveModal({ mode, plans, form, setForm, save, close }) {
  const togglePlan = (planId) => {
    const next = form.plan_ids.includes(planId) ? form.plan_ids.filter((id) => id !== planId) : [...form.plan_ids, planId];
    setForm({ ...form, plan_ids: next });
  };
  const title = mode === 'edit' ? 'Edit migration wave' : 'Create migration wave';
  const actionLabel = mode === 'edit' ? 'Save changes' : 'Create wave';
  return <Modal title={title} onClose={close}><FormPanel title="" onSubmit={save}><Input label="Wave name" value={form.wave_name} onChange={(value) => setForm({ ...form, wave_name: value })} required /><Input label="Planned window" value={form.planned_window} onChange={(value) => setForm({ ...form, planned_window: value })} /><div className="table-wrap"><table><thead><tr><th></th><th>Plan</th><th>Migration</th><th>Status</th></tr></thead><tbody>{plans.map((plan) => <tr key={plan.id}><td><input className="table-checkbox" type="checkbox" checked={form.plan_ids.includes(plan.id)} onChange={() => togglePlan(plan.id)} /></td><td>{plan.name}</td><td>{plan.migration_type}</td><td><Badge value={plan.status} /></td></tr>)}</tbody></table></div><TextArea label="Notes" value={form.notes} onChange={(value) => setForm({ ...form, notes: value })} /><div className="button-row"><button className="primary"><Save size={16} /> {actionLabel}</button><button className="secondary" type="button" onClick={close}><X size={16} /> Cancel</button></div></FormPanel></Modal>;
}

function Input({ label, value, onChange, type = 'text', required = false, placeholder = '', autoComplete = 'off', disabled = false, readOnly = false }) {
  return <label>{label}<input type={type} value={value ?? ''} required={required} placeholder={placeholder} autoComplete={autoComplete} disabled={disabled} readOnly={readOnly} onChange={(e) => onChange(e.target.value)} /></label>;
}

function PasswordInput({ label, value, onChange, required = false, placeholder = '' }) {
  const [visible, setVisible] = useState(false);
  return <label>{label}<div className="secret-input"><input type={visible ? 'text' : 'password'} value={value ?? ''} required={required} placeholder={placeholder} autoComplete="new-password" onChange={(e) => onChange(e.target.value)} /><button type="button" className="secondary secret-toggle" onClick={() => setVisible((current) => !current)}>{visible ? 'Hide' : 'Show'}</button></div></label>;
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
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.replace('T', ' ');
  try {
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: displayTimezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(parsed).replace(',', '');
  } catch {
    return parsed.toISOString().replace('T', ' ').slice(0, 19);
  }
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

function normalizeProgress(value) {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) return 0;
  return Math.min(100, Math.max(0, Math.round(numeric)));
}

function parseExecutionOptions(value) {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value || {}).filter(([, entry]) => entry !== '' && entry !== null && entry !== undefined));
}

function parseServiceAccountJson(value) {
  const trimmed = (value || '').trim();
  if (!trimmed) return {};
  return JSON.parse(trimmed);
}

function connectorCredentialSummary(connector) {
  if (connector.has_stored_secret) return 'Stored in DS Shift';
  if (connector.credential_reference) return connector.credential_reference;
  return 'Not set';
}

function planDetailStatus(result, planStatus, fallbackStatus) {
  if (result) return result.ok ? planStatus : 'Failed';
  return fallbackStatus || planStatus;
}

function taskStatusLabel(value) {
  if ((value || '').toLowerCase() === 'succeeded') return 'Completed';
  return value || 'Running';
}

function taskUpdatedAt(task) {
  return task?.completed_at || task?.updated_at || task?.started_at || null;
}

function taskTitle(task, index) {
  const title = task?.title || task?.task_name || task?.task_code || 'Task';
  return `${index + 1}. ${title}`;
}

function taskPercent(task) {
  return normalizeProgress(task?.progress ?? task?.progress_percent);
}

function taskProgressTone(planStatus) {
  const normalized = String(planStatus || '').toLowerCase();
  if (normalized.includes('cancel')) return 'danger';
  if (normalized.includes('failed') || normalized.includes('blocked')) return 'danger';
  if (normalized.includes('running') || normalized.includes('queued')) return 'warning';
  return 'success';
}

function taskCanContinue(plan, user) {
  if (user?.role !== 'admin') return false;
  if (!plan?.spark_job_id || plan?.status !== 'Failed') return false;
  return ['KVM to VMware ESXi / vCenter', 'VMware ESXi / vCenter to KVM'].includes(plan?.migration_type);
}

function planCanContinue(plan, user) {
  if (user?.role !== 'admin') return false;
  if (plan?.status !== 'Failed') return false;
  return ['KVM to VMware ESXi / vCenter', 'VMware ESXi / vCenter to KVM'].includes(plan?.migration_type);
}

function normalizeCheckLabel(value) {
  return String(value || 'check').replaceAll('_', ' ');
}

function blockingChecks(result) {
  return (result?.checks || []).filter((check) => check && check.ok === false);
}

function preflightDetail(result) {
  if (!result) return 'Not executed';
  const blocking = blockingChecks(result);
  if (!blocking.length) return result.message || 'Preflight passed';
  return blocking.map((check) => {
    const prefix = check.vm_name ? `${check.vm_name}: ` : '';
    return `${prefix}${normalizeCheckLabel(check.check)}: ${check.message || 'No details returned'}`;
  }).join(' | ');
}

function renderConnectorCredentialFields(form, update, updateCredential) {
  if (form.connector_category === 'host') {
    return <div className="credential-grid"><Input label="Username" value={form.username || ''} onChange={(v) => update({ username: v })} /><PasswordInput label="Password" value={form.password || ''} onChange={(v) => update({ password: v })} placeholder={form.has_stored_secret ? 'Leave blank to keep the stored password' : 'Optional for SSH key-based access'} />{form.has_stored_secret && <div className="tip credential-tip">A credential is already stored for this connector. Enter a new password only if you want to replace it.</div>}</div>;
  }
  if (form.connector_type === 'Amazon Web Services') {
    return <div className="credential-grid"><Input label="Access key ID" value={form.credential_payload.access_key_id || ''} onChange={(v) => updateCredential('access_key_id', v)} /><PasswordInput label="Secret access key" value={form.credential_payload.secret_access_key || ''} onChange={(v) => updateCredential('secret_access_key', v)} placeholder={form.has_stored_secret ? 'Leave blank to keep the stored secret key' : ''} /><PasswordInput label="Session token" value={form.credential_payload.session_token || ''} onChange={(v) => updateCredential('session_token', v)} placeholder="Optional temporary session token" />{form.has_stored_secret && <div className="tip credential-tip">Stored cloud credentials stay in place until you replace them here.</div>}</div>;
  }
  if (form.connector_type === 'Google Cloud Platform') {
    return <><TextArea label="Service account JSON" value={form.credential_payload.service_account_json || ''} onChange={(v) => updateCredential('service_account_json', v)} />{form.has_stored_secret && <div className="tip">A service-account credential is already stored. Paste a new JSON document only if you want to replace it.</div>}</>;
  }
  if (form.connector_type === 'Microsoft Azure') {
    return <div className="credential-grid"><Input label="Tenant ID" value={form.credential_payload.tenant_id || ''} onChange={(v) => updateCredential('tenant_id', v)} /><Input label="Client ID" value={form.credential_payload.client_id || ''} onChange={(v) => updateCredential('client_id', v)} /><PasswordInput label="Client secret" value={form.credential_payload.client_secret || ''} onChange={(v) => updateCredential('client_secret', v)} placeholder={form.has_stored_secret ? 'Leave blank to keep the stored client secret' : ''} />{form.has_stored_secret && <div className="tip credential-tip">Stored cloud credentials stay in place until you replace them here.</div>}</div>;
  }
  return null;
}

function renderMigrationPlanField(field, form, setForm, option, setLocation, setTargetLocation) {
  const key = field.key;
  if (key === 'target_datastore') {
    return <Input key={key} label={field.label} value={form.target_datastore} onChange={(value) => setForm({ ...form, target_datastore: value })} />;
  }
  if (key === 'source_location') {
    return <Input key={key} label={field.label} value={form.execution_options.source_region || form.execution_options.source_zone} onChange={setLocation} />;
  }
  if (key === 'target_location_shared') {
    return <Input key={key} label={field.label} value={form.execution_options.target_region || form.execution_options.target_zone} onChange={setTargetLocation} />;
  }
  return <Input key={key} label={field.label} value={form.execution_options[key] || ''} onChange={(value) => option(key, value)} />;
}

function migrationPlanFieldConfig(sourceType, targetType) {
  const source = sourceType || '';
  const target = targetType || '';
  if (source === 'KVM' && target === 'VMware ESXi / vCenter') {
    return {
      description: 'Target vDC, cluster or host, datastore, and network are owned by the VMware target connector and are validated during connector validation and plan preflight.',
      fields: [],
    };
  }
  if (source === 'VMware ESXi / vCenter' && target === 'KVM') {
    return {
      description: 'Source datacenter and compute context come from VMware discovery metadata. Destination KVM storage pool and bridge come from the target KVM connector and are validated there and during plan preflight.',
      fields: [],
    };
  }
  if (source === 'Amazon Web Services' && target === 'Amazon Web Services') {
    return {
      description: 'Set the source and target AWS regions and the target instance type for the launched copy.',
      fields: [
        { key: 'source_region', label: 'Source AWS region' },
        { key: 'target_region', label: 'Target AWS region' },
        { key: 'target_instance_type', label: 'Target instance type' },
      ],
    };
  }
  if (source === 'Google Cloud Platform' && target === 'Google Cloud Platform') {
    return {
      description: 'Set the source and target GCP zones and the target machine type for the created instance.',
      fields: [
        { key: 'source_location', label: 'Source zone' },
        { key: 'target_location_shared', label: 'Target zone' },
        { key: 'target_instance_type', label: 'Target machine type' },
      ],
    };
  }
  if (source === 'Microsoft Azure' && target === 'Microsoft Azure') {
    return {
      description: 'Set the Azure destination resource group, virtual network subnet, location, and VM size.',
      fields: [
        { key: 'target_resource_group', label: 'Target resource group' },
        { key: 'target_subnet_id', label: 'Target subnet ID' },
        { key: 'target_location', label: 'Target Azure location' },
        { key: 'target_instance_type', label: 'Target VM size' },
      ],
    };
  }
  if (source === 'KVM' && target === 'KVM') {
    return {
      description: 'The current KVM-to-KVM path does not require extra plan-level inputs in this form.',
      fields: [],
    };
  }
  return {
    description: 'Only execution inputs that apply to the selected destination path are shown here. Connector-level credentials remain on the connector itself.',
    fields: [],
  };
}

function planToForm(plan) {
  return {
    name: plan.name || '',
    vm_ids: parseJsonArray(plan.vm_ids_json),
    target_connector_id: String(plan.target_connector_id || ''),
    keep_source_vm: plan.keep_source_vm !== false,
    notes: plan.notes || '',
    execution_options: {
      ...blankMigrationPlan.execution_options,
      ...parseExecutionOptions(plan.execution_options_json),
    },
  };
}

function waveToForm(wave) {
  return {
    wave_name: wave.wave_name || '',
    planned_window: wave.planned_window || '',
    status: wave.status || 'Planned',
    notes: wave.notes || '',
    plan_ids: parseJsonArray(wave.plan_ids_json),
  };
}

createRoot(document.getElementById('root')).render(<App />);
