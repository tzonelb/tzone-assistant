import { useEffect, useMemo, useState } from "react";
import {
  createAccessRoleRequest,
  createCompanyUserRequest,
  createDepartmentRequest,
  deleteDepartmentRequest,
  getAccessOverviewRequest,
  getUserPermissionOverridesRequest,
  logoutCompanyUserRequest,
  resetCompanyUserPasswordRequest,
  setUserPermissionOverridesRequest,
  updateAccessRoleRequest,
  updateCompanyUserRequest,
} from "../../api/client";
import { ConfirmDialog, EmptyState } from "../../components/common";
import MultiSelectPopover from "../../components/common/MultiSelectPopover";

// Permission codes are "area.action" (e.g. "conversations.view"). Grouping
// them by area turns a flat 19-item checkbox list into something an owner
// can actually scan — "what can this role touch in Knowledge?" instead of
// hunting through one long alphabetical list.
const GROUP_LABELS = {
  dashboard: "Dashboard",
  conversations: "Conversations",
  knowledge: "Knowledge Base",
  channels: "Channels",
  users: "Team & Departments",
  settings: "Company Settings",
  subscriptions: "Billing & Subscription",
  modules: "Modules",
};

function groupPermissions(permissions) {
  const groups = new Map();
  for (const permission of permissions || []) {
    const area = permission.code.split(".")[0];
    const label = GROUP_LABELS[area] || area;
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(permission);
  }
  return Array.from(groups.entries());
}

function formatLastOnline(value) {
  if (!value) return "Never";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "Never" : date.toLocaleString();
}

function RolePermissionEditor({ role, groupedPermissions, saving, onTogglePermission }) {
  return (
    <div className="admin-access-card permission-card">
      <h2>{role?.name || "Role details"}</h2>
      <p>{role?.description || "Choose which platform actions this role can perform."}</p>
      {role?.code === "owner" ? (
        <div className="owner-access-note">Owner has full company access. Individual owner permissions are intentionally hidden.</div>
      ) : (
        <div className="permission-groups">
          {groupedPermissions.map(([groupLabel, permissions]) => (
            <div className="permission-group" key={groupLabel}>
              <h3 className="permission-group-title">{groupLabel}</h3>
              <div className="permission-grid">
                {permissions.map((permission) => (
                  <label key={permission.code} className="permission-row">
                    <div>
                      <strong>{permission.name}</strong>
                      <span>{permission.code}</span>
                    </div>
                    <input
                      type="checkbox"
                      checked={Boolean(role?.permission_codes?.includes(permission.code))}
                      disabled={saving}
                      onChange={() => onTogglePermission(permission.code)}
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AddUserForm({ newUser, setNewUser, roles, branches, departments }) {
  const departmentOptions = departments.filter((name) => name !== "Unassigned").map((name) => ({ value: name, label: name }));

  return (
    <>
      <label>Full name<input value={newUser.full_name} onChange={(event) => setNewUser({ ...newUser, full_name: event.target.value })} required /></label>
      <label>Email<input type="email" value={newUser.email} onChange={(event) => setNewUser({ ...newUser, email: event.target.value })} required /></label>
      <label>Temporary password<input type="password" minLength="8" value={newUser.password} onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} required /></label>
      <label>Phone<input value={newUser.phone} onChange={(event) => setNewUser({ ...newUser, phone: event.target.value })} /></label>
      <label>
        Role
        <select value={newUser.role_id} onChange={(event) => setNewUser({ ...newUser, role_id: event.target.value })} required>
          <option value="">Select role</option>
          {roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
        </select>
      </label>
      <label>
        Branch
        <select value={newUser.branch_id} onChange={(event) => setNewUser({ ...newUser, branch_id: event.target.value })}>
          <option value="">All branches</option>
          {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
        </select>
      </label>
      <label>
        Departments
        <MultiSelectPopover
          options={departmentOptions}
          value={newUser.departments}
          allLabel="All departments"
          emptyHint="Add departments in Company Settings first."
          onChange={(next) => setNewUser({ ...newUser, departments: next })}
        />
      </label>
    </>
  );
}

function AddRoleForm({ newRole, setNewRole, groupedPermissions, onToggle }) {
  return (
    <>
      <label>Role name<input value={newRole.name} onChange={(event) => setNewRole({ ...newRole, name: event.target.value })} required /></label>
      <label>Role code<input value={newRole.code} onChange={(event) => setNewRole({ ...newRole, code: event.target.value.toLowerCase().replace(/\s+/g, "_") })} required /></label>
      <label>Description<textarea value={newRole.description} onChange={(event) => setNewRole({ ...newRole, description: event.target.value })} /></label>
      <div className="permission-groups">
        {groupedPermissions.map(([groupLabel, permissions]) => (
          <div className="permission-group" key={groupLabel}>
            <span className="permission-group-title">{groupLabel}</span>
            <div className="permission-mini-list">
              {permissions.map((permission) => (
                <label key={permission.code}>
                  <input type="checkbox" checked={newRole.permission_codes.includes(permission.code)} onChange={() => onToggle(permission.code)} />
                  <span>{permission.name}</span>
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

// Manage roles: role list + the same permission editor/creator that used
// to live inline on the page, now reachable from the toolbar instead of
// always taking up page space.
function RolesManagerDialog({ open, onClose, roles, groupedPermissions, selectedRoleId, setSelectedRoleId, selectedRole, saving, onTogglePermission, newRole, setNewRole, onToggleNewRolePermission, onCreateRole }) {
  const [addingRole, setAddingRole] = useState(false);

  if (!open) return null;

  return (
    <div className="admin-modal-backdrop" onMouseDown={() => onClose()}>
      <div className="admin-modal admin-modal-wide" onMouseDown={(event) => event.stopPropagation()}>
        <div className="admin-modal-header">
          <h2>Manage roles</h2>
          <button type="button" onClick={onClose}>×</button>
        </div>

        {addingRole ? (
          <form
            onSubmit={async (event) => {
              await onCreateRole(event);
              setAddingRole(false);
            }}
          >
            <AddRoleForm newRole={newRole} setNewRole={setNewRole} groupedPermissions={groupedPermissions} onToggle={onToggleNewRolePermission} />
            <div className="admin-modal-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setAddingRole(false)}>Cancel</button>
              <button className="primary-action" type="submit" disabled={saving}>{saving ? "Saving…" : "Create role"}</button>
            </div>
          </form>
        ) : (
          <>
            <div className="admin-modal-actions">
              <button type="button" className="btn btn-secondary" onClick={() => setAddingRole(true)}>+ Add role</button>
            </div>
            <div className="admin-access-grid roles-management-grid">
              <aside className="admin-access-card role-list-card">
                <h2>Roles</h2>
                <div className="role-list">
                  {roles.map((role) => (
                    <button
                      key={role.id}
                      type="button"
                      className={role.id === selectedRoleId ? "role-list-item active" : "role-list-item"}
                      onClick={() => setSelectedRoleId(role.id)}
                    >
                      <strong>{role.name}</strong>
                      <span>{role.code === "owner" ? "Full company access" : `${role.permission_codes?.length || 0} permissions`}</span>
                    </button>
                  ))}
                </div>
              </aside>

              <RolePermissionEditor
                role={selectedRole}
                groupedPermissions={groupedPermissions}
                saving={saving}
                onTogglePermission={onTogglePermission}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// Manage departments: folded in from the old standalone Company Settings
// "Departments" section — same real create/list/delete calls, just
// reachable from Roles & Permissions now since department scope is what
// drives per-user access overrides right below it.
function DepartmentsManagerDialog({ open, onClose, departments, onCreate, onDelete }) {
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (!open) return null;

  async function submit(event) {
    event.preventDefault();
    const value = newName.trim();
    if (!value) return;
    setSaving(true);
    setError("");
    try {
      await onCreate(value);
      setNewName("");
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove(name) {
    setError("");
    try {
      await onDelete(name);
    } catch (x) {
      setError(x.message);
    }
  }

  return (
    <div className="admin-modal-backdrop" onMouseDown={() => onClose()}>
      <div className="admin-modal" onMouseDown={(event) => event.stopPropagation()}>
        <div className="admin-modal-header">
          <h2>Manage departments</h2>
          <button type="button" onClick={onClose}>×</button>
        </div>
        <p className="roles-dialog-hint">
          Used for routing conversations, and to scope AI Knowledge, Reply Flow steps and individual employees. Every
          company defines its own list; nothing is preset for you.
        </p>
        {error ? <p className="admin-access-error">{error}</p> : null}
        <form onSubmit={submit} className="roles-department-add-form">
          <input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="e.g. Sales, Technical Support, Billing" />
          <button type="submit" className="btn btn-primary" disabled={saving || !newName.trim()}>{saving ? "Adding..." : "+ Add"}</button>
        </form>
        <div className="roles-department-list">
          {departments.map((name) => (
            <div className="roles-department-row" key={name}>
              <span>{name}{name === "Unassigned" ? <em> (always available)</em> : null}</span>
              {name !== "Unassigned" ? <button type="button" className="btn btn-secondary" onClick={() => remove(name)}>Delete</button> : null}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// Per-user permission overrides — the real, already-wired mechanism that
// lets an owner grant or revoke one specific permission for one specific
// employee, beyond whatever their role defaults to (backend:
// user_permission_overrides table via auth_service.has_permission, which
// checks the override before falling back to the role). This dialog is
// the missing UI for it.
function OverridesDialog({ user, groupedPermissions, role, onClose, onSave }) {
  const [selections, setSelections] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    getUserPermissionOverridesRequest(user.id)
      .then((result) => {
        if (cancelled) return;
        const map = {};
        for (const item of result?.overrides || []) map[item.permission_code] = item.allowed ? "allow" : "deny";
        setSelections(map);
      })
      .catch((requestError) => !cancelled && setError(requestError.message || "Could not load overrides."))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [user]);

  if (!user) return null;

  const isOwner = role?.code === "owner";

  async function save() {
    setSaving(true);
    setError("");
    try {
      const overrides = Object.entries(selections)
        .filter(([, value]) => value === "allow" || value === "deny")
        .map(([permission_code, value]) => ({ permission_code, allowed: value === "allow" }));
      await setUserPermissionOverridesRequest(user.id, overrides);
      onSave();
    } catch (requestError) {
      setError(requestError.message || "Could not save overrides.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="admin-modal-backdrop" onMouseDown={() => onClose()}>
      <div className="admin-modal admin-modal-wide" onMouseDown={(event) => event.stopPropagation()}>
        <div className="admin-modal-header">
          <h2>Permission overrides — {user.full_name || user.email}</h2>
          <button type="button" onClick={onClose}>×</button>
        </div>
        <p className="roles-dialog-hint">
          Their role ({role?.name || "—"}) grants the permissions checked below by default. Set any single permission
          to <strong>Always allow</strong> or <strong>Always deny</strong> to override that default for this one
          employee only — e.g. give them access scoped to only their department regardless of what their role would
          normally allow company-wide, or grant one extra action their role doesn't usually have.
        </p>
        {isOwner ? (
          <div className="owner-access-note">Owner has full company access. Individual owner permission overrides are intentionally hidden.</div>
        ) : loading ? (
          <p>Loading current overrides…</p>
        ) : (
          <>
            {error ? <p className="admin-access-error">{error}</p> : null}
            <div className="permission-groups">
              {groupedPermissions.map(([groupLabel, permissions]) => (
                <div className="permission-group" key={groupLabel}>
                  <h3 className="permission-group-title">{groupLabel}</h3>
                  <div className="permission-grid">
                    {permissions.map((permission) => {
                      const roleDefault = Boolean(role?.permission_codes?.includes(permission.code));
                      const state = selections[permission.code] || "default";
                      return (
                        <div key={permission.code} className="permission-row roles-override-row">
                          <div>
                            <strong>{permission.name}</strong>
                            <span>{permission.code} · role default: {roleDefault ? "allowed" : "not allowed"}</span>
                          </div>
                          <select
                            className="input"
                            value={state}
                            disabled={saving}
                            onChange={(event) => setSelections({ ...selections, [permission.code]: event.target.value })}
                          >
                            <option value="default">Use role default</option>
                            <option value="allow">Always allow</option>
                            <option value="deny">Always deny</option>
                          </select>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
        <div className="admin-modal-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          {!isOwner ? <button type="button" className="btn btn-primary" disabled={saving || loading} onClick={save}>{saving ? "Saving…" : "Save overrides"}</button> : null}
        </div>
      </div>
    </div>
  );
}

export default function RolesPermissionsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState(null);
  const [selectedRoleId, setSelectedRoleId] = useState(null);
  const [newRole, setNewRole] = useState({ name: "", code: "", description: "", permission_codes: [] });
  const [newUser, setNewUser] = useState({ full_name: "", email: "", password: "", phone: "", role_id: "", branch_id: "", departments: [] });

  const [search, setSearch] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [groupBy, setGroupBy] = useState("role");

  const [rolesDialogOpen, setRolesDialogOpen] = useState(false);
  const [departmentsDialogOpen, setDepartmentsDialogOpen] = useState(false);
  const [overridesUser, setOverridesUser] = useState(null);

  const [resetTarget, setResetTarget] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [resetResult, setResetResult] = useState(null);
  const [logoutTarget, setLogoutTarget] = useState(null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutResult, setLogoutResult] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const result = await getAccessOverviewRequest();
      setData(result);
      setSelectedRoleId((current) => current || result.roles?.find((role) => role.code !== "owner")?.id || result.roles?.[0]?.id);
    } catch (requestError) {
      setError(requestError.message || "Unable to load access control.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const selectedRole = useMemo(() => data?.roles?.find((role) => role.id === selectedRoleId) || null, [data, selectedRoleId]);
  const groupedPermissions = useMemo(() => groupPermissions(data?.permissions), [data]);

  const roles = data?.roles || [];
  const branches = data?.branches || [];
  const departments = data?.departments || [];
  const departmentOptions = departments.filter((name) => name !== "Unassigned").map((name) => ({ value: name, label: name }));

  function toggleNewRolePermission(code) {
    setNewRole((current) => ({
      ...current,
      permission_codes: current.permission_codes.includes(code)
        ? current.permission_codes.filter((existing) => existing !== code)
        : [...current.permission_codes, code],
    }));
  }

  async function createRole(event) {
    event.preventDefault();
    setSaving(true);
    try {
      await createAccessRoleRequest(newRole);
      setNewRole({ name: "", code: "", description: "", permission_codes: [] });
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function createUser(event) {
    event.preventDefault();
    setSaving(true);
    try {
      await createCompanyUserRequest({
        ...newUser,
        role_id: Number(newUser.role_id),
        branch_id: newUser.branch_id ? Number(newUser.branch_id) : null,
      });
      setNewUser({ full_name: "", email: "", password: "", phone: "", role_id: "", branch_id: "", departments: [] });
      setMode(null);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function updateUser(user, updates) {
    setSaving(true);
    try {
      await updateCompanyUserRequest(user.id, {
        role_id: updates.role_id ?? user.role_id,
        branch_id: updates.branch_id !== undefined ? updates.branch_id : user.branch_id,
        status: updates.status ?? user.membership_status,
        departments: updates.departments ?? user.departments ?? [],
      });
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function togglePermission(code) {
    if (!selectedRole || selectedRole.code === "owner") return;
    const current = selectedRole.permission_codes || [];
    setSaving(true);
    try {
      await updateAccessRoleRequest(selectedRole.id, {
        permission_codes: current.includes(code) ? current.filter((existing) => existing !== code) : [...current, code],
      });
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function createDepartment(name) {
    await createDepartmentRequest(name);
    await load();
  }

  async function removeDepartment(name) {
    await deleteDepartmentRequest(name);
    await load();
  }

  async function confirmReset() {
    if (!resetTarget) return;
    setResetting(true);
    try {
      const result = await resetCompanyUserPasswordRequest(resetTarget.id);
      setResetResult({ user: resetTarget, temporary_password: result?.temporary_password });
      setResetTarget(null);
    } catch (requestError) {
      setError(requestError.message || "Could not reset this employee's password.");
      setResetTarget(null);
    } finally {
      setResetting(false);
    }
  }

  async function confirmLogout() {
    if (!logoutTarget) return;
    setLoggingOut(true);
    try {
      const result = await logoutCompanyUserRequest(logoutTarget.id);
      setLogoutResult({ user: logoutTarget, revoked_sessions: result?.revoked_sessions ?? 0 });
      setLogoutTarget(null);
    } catch (requestError) {
      setError(requestError.message || "Could not log this employee out.");
      setLogoutTarget(null);
    } finally {
      setLoggingOut(false);
    }
  }

  const filteredUsers = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (data?.users || []).filter((user) => {
      if (term) {
        const haystack = `${user.full_name || ""} ${user.email || ""}`.toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      if (roleFilter && String(user.role_id) !== String(roleFilter)) return false;
      if (departmentFilter) {
        const userDepartments = user.departments || [];
        // Empty departments means the user is scoped to every department
        // (no restriction set), so they still match any department filter.
        if (userDepartments.length && !userDepartments.includes(departmentFilter)) return false;
      }
      return true;
    });
  }, [data, search, roleFilter, departmentFilter]);

  const groupedRows = useMemo(() => {
    const sorted = [...filteredUsers].sort((a, b) => (a.full_name || a.email || "").localeCompare(b.full_name || b.email || ""));
    if (groupBy === "department") {
      const rows = [];
      departments.forEach((name) => {
        const users = sorted.filter((user) => (user.departments || []).includes(name));
        if (users.length) rows.push({ key: `dept-${name}`, label: name, meta: `${users.length} employee${users.length === 1 ? "" : "s"}`, users });
      });
      const scopedToAll = sorted.filter((user) => !(user.departments || []).length);
      if (scopedToAll.length) {
        rows.unshift({ key: "dept-all", label: "All departments (no restriction set)", meta: `${scopedToAll.length} employee${scopedToAll.length === 1 ? "" : "s"}`, users: scopedToAll });
      }
      return rows;
    }
    return roles
      .map((role) => ({
        key: `role-${role.id}`,
        label: role.name,
        meta: role.code === "owner" ? "Full company access" : `${role.permission_codes?.length || 0} permissions`,
        users: sorted.filter((user) => user.role_id === role.id),
      }))
      .filter((group) => group.users.length);
  }, [filteredUsers, groupBy, departments, roles]);

  const overridesRole = overridesUser ? roles.find((role) => role.id === overridesUser.role_id) : null;

  if (loading) return <div className="admin-access-loading">Loading access control…</div>;

  return (
    <section className="admin-access-page">
      {error ? <div className="admin-access-error">{error}</div> : null}

      <div className="access-toolbar roles-toolbar">
        <div>
          <strong>{data?.company?.name || "Company team"}</strong>
          <span>{data?.users?.length || 0} users · {roles.length} roles · {departments.length} departments</span>
        </div>
        <div className="roles-toolbar-controls">
          <input
            className="input roles-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search employees by name or email..."
            aria-label="Search employees"
          />
          <select className="input" value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value)} aria-label="Filter by department">
            <option value="">Department: any</option>
            {departments.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          <select className="input" value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)} aria-label="Filter by role">
            <option value="">Role: any</option>
            {roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
          </select>
          <button type="button" className="btn btn-secondary" onClick={() => setRolesDialogOpen(true)}>Manage roles</button>
          <button type="button" className="btn btn-secondary" onClick={() => setDepartmentsDialogOpen(true)}>Manage departments</button>
          <button type="button" className="btn btn-primary" onClick={() => setMode("user")}>+ Add user</button>
        </div>
      </div>

      <div className="admin-access-card users-card">
        <div className="users-card-header">
          <div>
            <h2>Employees</h2>
            <p>Sorted A–Z within each group. Role and department scope are editable inline; use Manage roles to change what a role can do.</p>
          </div>
          <div className="seg" role="radiogroup" aria-label="Group employees by">
            <label className="seg-opt">
              <input type="radio" name="roles-group-by" checked={groupBy === "role"} onChange={() => setGroupBy("role")} /> Group by role
            </label>
            <label className="seg-opt">
              <input type="radio" name="roles-group-by" checked={groupBy === "department"} onChange={() => setGroupBy("department")} /> Group by department
            </label>
          </div>
        </div>

        {groupedRows.length === 0 ? (
          <EmptyState title="No employees match" description="Try clearing the search or filters above." />
        ) : (
          <div className="users-table-wrap">
            <table className="users-table roles-employees-table">
              <thead>
                <tr>
                  <th>Employee</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Departments</th>
                  <th>Branch</th>
                  <th>Status</th>
                  <th>Last online</th>
                  <th>Permissions</th>
                  <th>Reset password</th>
                  <th>Log out</th>
                  <th>Report</th>
                </tr>
              </thead>
              <tbody>
                {groupedRows.flatMap((group) => [
                  <tr className="roles-group-header" key={group.key}>
                    <td colSpan={11}><strong>{group.label}</strong><span>{group.meta}</span></td>
                  </tr>,
                  ...group.users.map((user) => (
                    <tr key={user.id}>
                      <td><strong>{user.full_name || "Unnamed user"}</strong></td>
                      <td className="tz-num">{user.email}</td>
                      <td>
                        <select
                          className="input roles-inline-select"
                          value={user.role_id || ""}
                          disabled={saving}
                          onChange={(event) => updateUser(user, { role_id: Number(event.target.value) })}
                        >
                          {roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
                        </select>
                      </td>
                      <td>
                        <MultiSelectPopover
                          options={departmentOptions}
                          value={user.departments || []}
                          disabled={saving}
                          allLabel="All departments"
                          emptyHint="Add departments in Manage departments first."
                          onChange={(next) => updateUser(user, { departments: next })}
                        />
                      </td>
                      <td>
                        <select
                          className="input roles-inline-select"
                          value={user.branch_id || ""}
                          disabled={saving}
                          onChange={(event) => updateUser(user, { branch_id: event.target.value ? Number(event.target.value) : null })}
                        >
                          <option value="">All branches</option>
                          {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
                        </select>
                      </td>
                      <td>
                        <select
                          className="input roles-inline-select"
                          value={user.membership_status}
                          disabled={saving}
                          onChange={(event) => updateUser(user, { status: event.target.value })}
                        >
                          <option value="active">Active</option>
                          <option value="disabled">Disabled</option>
                        </select>
                      </td>
                      <td className="tz-num">{formatLastOnline(user.last_login_at)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          disabled={user.role_code === "owner"}
                          title={user.role_code === "owner" ? "Owner always has full access — overrides don't apply." : "Override this employee's permissions beyond their role's default"}
                          onClick={() => setOverridesUser(user)}
                        >
                          {(user.permission_overrides || []).length ? `Overrides (${user.permission_overrides.length})` : "Overrides"}
                        </button>
                      </td>
                      <td>
                        <button type="button" className="btn btn-secondary" onClick={() => setResetTarget(user)}>Reset password</button>
                      </td>
                      <td>
                        <button type="button" className="btn btn-secondary" onClick={() => setLogoutTarget(user)}>Log out</button>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          disabled
                          title="Not available yet — T-ZONE doesn't track per-employee usage or response-time analytics yet. This needs a real activity/analytics data source before it can be built."
                        >
                          Report
                        </button>
                      </td>
                    </tr>
                  )),
                ])}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <RolesManagerDialog
        open={rolesDialogOpen}
        onClose={() => setRolesDialogOpen(false)}
        roles={roles}
        groupedPermissions={groupedPermissions}
        selectedRoleId={selectedRoleId}
        setSelectedRoleId={setSelectedRoleId}
        selectedRole={selectedRole}
        saving={saving}
        onTogglePermission={togglePermission}
        newRole={newRole}
        setNewRole={setNewRole}
        onToggleNewRolePermission={toggleNewRolePermission}
        onCreateRole={createRole}
      />

      <DepartmentsManagerDialog
        open={departmentsDialogOpen}
        onClose={() => setDepartmentsDialogOpen(false)}
        departments={departments}
        onCreate={createDepartment}
        onDelete={removeDepartment}
      />

      {overridesUser ? (
        <OverridesDialog
          user={overridesUser}
          role={overridesRole}
          groupedPermissions={groupedPermissions}
          onClose={() => setOverridesUser(null)}
          onSave={async () => { setOverridesUser(null); await load(); }}
        />
      ) : null}

      <ConfirmDialog
        open={Boolean(resetTarget)}
        title="Reset password"
        message={<p>Reset the password for <strong>{resetTarget?.full_name || resetTarget?.email}</strong>? A new temporary password will be generated and every one of their active sessions will be signed out.</p>}
        confirmLabel="Reset password"
        loading={resetting}
        onConfirm={confirmReset}
        onCancel={() => setResetTarget(null)}
      />

      <ConfirmDialog
        open={Boolean(logoutTarget)}
        title="Log out employee"
        message={<p>Sign <strong>{logoutTarget?.full_name || logoutTarget?.email}</strong> out of every device right now?</p>}
        confirmLabel="Log out"
        loading={loggingOut}
        onConfirm={confirmLogout}
        onCancel={() => setLogoutTarget(null)}
      />

      {resetResult ? (
        <div className="admin-modal-backdrop" onMouseDown={() => setResetResult(null)}>
          <div className="admin-modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <h2>Password reset</h2>
              <button type="button" onClick={() => setResetResult(null)}>×</button>
            </div>
            <p>New temporary password for <strong>{resetResult.user.full_name || resetResult.user.email}</strong>:</p>
            <p className="roles-temp-password tz-num">{resetResult.temporary_password}</p>
            <p className="roles-dialog-hint">Share this with them directly — it isn't stored anywhere retrievable after you close this dialog. All of their previous sessions were signed out.</p>
            <div className="admin-modal-actions">
              <button type="button" className="btn btn-primary" onClick={() => setResetResult(null)}>Done</button>
            </div>
          </div>
        </div>
      ) : null}

      {logoutResult ? (
        <div className="admin-modal-backdrop" onMouseDown={() => setLogoutResult(null)}>
          <div className="admin-modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <h2>Logged out</h2>
              <button type="button" onClick={() => setLogoutResult(null)}>×</button>
            </div>
            <p>
              <strong>{logoutResult.user.full_name || logoutResult.user.email}</strong> was signed out of{" "}
              {logoutResult.revoked_sessions} active session{logoutResult.revoked_sessions === 1 ? "" : "s"}. They'll need to log in again.
            </p>
            <div className="admin-modal-actions">
              <button type="button" className="btn btn-primary" onClick={() => setLogoutResult(null)}>Done</button>
            </div>
          </div>
        </div>
      ) : null}

      {mode === "user" ? (
        <div className="admin-modal-backdrop" onMouseDown={() => setMode(null)}>
          <form className="admin-modal" onSubmit={createUser} onMouseDown={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <h2>Add company user</h2>
              <button type="button" onClick={() => setMode(null)}>×</button>
            </div>
            <AddUserForm newUser={newUser} setNewUser={setNewUser} roles={roles} branches={branches} departments={departments} />
            <button className="primary-action" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Create user"}
            </button>
          </form>
        </div>
      ) : null}
    </section>
  );
}
