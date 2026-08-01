import { useEffect, useMemo, useState } from "react";
import {
  createAccessRoleRequest,
  createCompanyUserRequest,
  getAccessOverviewRequest,
  updateAccessRoleRequest,
  updateCompanyUserRequest,
} from "../../api/client";

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

function RoleUsersTable({ users, roles, branches, saving, onUpdateUser }) {
  return (
    <div className="admin-access-card users-card">
      <div className="users-card-header">
        <div>
          <h2>Users</h2>
          <p>Employees and administrators connected to this company.</p>
        </div>
      </div>
      <div className="users-table-wrap">
        <table className="users-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Branch</th>
              <th>Status</th>
              <th>Last login</th>
            </tr>
          </thead>
          <tbody>
            {users?.map((user) => (
              <tr key={user.id}>
                <td>
                  <strong>{user.full_name || "Unnamed user"}</strong>
                  <span>{user.email}</span>
                </td>
                <td>
                  <select
                    value={user.role_id || ""}
                    disabled={saving}
                    onChange={(event) => onUpdateUser(user, { role_id: Number(event.target.value) })}
                  >
                    {roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
                  </select>
                </td>
                <td>
                  <select
                    value={user.branch_id || ""}
                    disabled={saving}
                    onChange={(event) => onUpdateUser(user, { branch_id: event.target.value ? Number(event.target.value) : null })}
                  >
                    <option value="">All branches</option>
                    {branches.map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
                  </select>
                </td>
                <td>
                  <select
                    value={user.membership_status}
                    disabled={saving}
                    onChange={(event) => onUpdateUser(user, { status: event.target.value })}
                  >
                    <option value="active">Active</option>
                    <option value="disabled">Disabled</option>
                  </select>
                </td>
                <td>{user.last_login_at || "Never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
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

function AddUserForm({ newUser, setNewUser, roles, branches }) {
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

export default function RolesPermissionsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState(null);
  const [selectedRoleId, setSelectedRoleId] = useState(null);
  const [newRole, setNewRole] = useState({ name: "", code: "", description: "", permission_codes: [] });
  const [newUser, setNewUser] = useState({ full_name: "", email: "", password: "", phone: "", role_id: "", branch_id: "" });

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
      setMode(null);
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
      setNewUser({ full_name: "", email: "", password: "", phone: "", role_id: "", branch_id: "" });
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

  if (loading) return <div className="admin-access-loading">Loading access control…</div>;

  return (
    <section className="admin-access-page">
      {error ? <div className="admin-access-error">{error}</div> : null}

      <div className="access-toolbar">
        <div>
          <strong>{data?.company?.name || "Company team"}</strong>
          <span>{data?.users?.length || 0} users · {data?.roles?.length || 0} roles</span>
        </div>
        <div>
          <button type="button" className="secondary-action" onClick={() => setMode("role")}>+ Add role</button>
          <button type="button" className="primary-action" onClick={() => setMode("user")}>+ Add user</button>
        </div>
      </div>

      <RoleUsersTable
        users={data?.users}
        roles={data?.roles || []}
        branches={data?.branches || []}
        saving={saving}
        onUpdateUser={updateUser}
      />

      <div className="admin-access-grid roles-management-grid">
        <aside className="admin-access-card role-list-card">
          <h2>Roles</h2>
          <div className="role-list">
            {data?.roles?.map((role) => (
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
          onTogglePermission={togglePermission}
        />
      </div>

      {mode ? (
        <div className="admin-modal-backdrop" onMouseDown={() => setMode(null)}>
          <form className="admin-modal" onSubmit={mode === "user" ? createUser : createRole} onMouseDown={(event) => event.stopPropagation()}>
            <div className="admin-modal-header">
              <h2>{mode === "user" ? "Add company user" : "Add role"}</h2>
              <button type="button" onClick={() => setMode(null)}>×</button>
            </div>
            {mode === "user" ? (
              <AddUserForm newUser={newUser} setNewUser={setNewUser} roles={data?.roles || []} branches={data?.branches || []} />
            ) : (
              <AddRoleForm newRole={newRole} setNewRole={setNewRole} groupedPermissions={groupedPermissions} onToggle={toggleNewRolePermission} />
            )}
            <button className="primary-action" type="submit" disabled={saving}>
              {saving ? "Saving…" : mode === "user" ? "Create user" : "Create role"}
            </button>
          </form>
        </div>
      ) : null}
    </section>
  );
}
