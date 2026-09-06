import { useEffect, useMemo, useState } from "react";

import {
  createAccessRoleRequest,
  createCompanyUserRequest,
  forceUserPasswordResetRequest,
  getAccessOverviewRequest,
  unlockCompanyUserRequest,
  updateAccessRoleRequest,
  updateCompanyUserRequest,
} from "../../api/client";


// The server hands back the permissions already annotated with their group and
// in group order, plus `permission_groups` (label, description, web_only). This
// turns those two into what the screen renders: the groups in order, each
// carrying only its own permissions, and any empty group dropped. A server that
// predates the grouping (no `permission_groups`) falls back to one unnamed
// group, so the screen still works rather than showing nothing.
function buildPermissionGroups(data) {
  const groups = data?.permission_groups || [];
  const permissions = data?.permissions || [];

  if (!groups.length) {
    return [
      { key: "all", label: "", description: "", web_only: false, permissions },
    ];
  }

  return groups
    .slice()
    .sort((first, second) => (first.order ?? 0) - (second.order ?? 0))
    .map((group) => ({
      ...group,
      permissions: permissions.filter(
        (permission) => permission.group === group.key,
      ),
    }))
    .filter((group) => group.permissions.length);
}


// Both actions reach into somebody else's account, so both are described in
// full and named to the person they land on before they fire.
const ACCOUNT_ACTIONS = {
  reset: {
    title: "Send a password reset link",
    confirmLabel: "Send reset link",
    describe: (user) =>
      `A single-use link will be emailed to ${user.email}. Every session ` +
      "they have open ends straight away, any lockout is cleared, and they " +
      "choose the new password themselves.",
  },
  unlock: {
    title: "Unlock this account",
    confirmLabel: "Unlock account",
    describe: (user) =>
      `${user.email} will be able to sign in again with the password they ` +
      "already have. The password itself is not changed.",
  },
};


export default function RolesPermissionsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [branchFilter, setBranchFilter] = useState("all");

  // A label, nothing more. Filtering the team by location changes what this
  // screen shows and nothing about what anybody may do.
  const visibleUsers = useMemo(() => {
    const users = data?.users || [];

    if (branchFilter === "all") {
      return users;
    }

    if (branchFilter === "none") {
      return users.filter((user) => !user.branch_id);
    }

    return users.filter((user) => String(user.branch_id) === branchFilter);
  }, [data, branchFilter]);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [selectedRoleId, setSelectedRoleId] = useState(null);

  const [newRole, setNewRole] = useState({
    name: "",
    code: "",
    description: "",
    permission_codes: [],
  });

  const [newUser, setNewUser] = useState({
    full_name: "",
    email: "",
    password: "",
    phone: "",
    role_id: "",
    branch_id: "",
  });

  async function load() {
    setLoading(true);

    try {
      const result = await getAccessOverviewRequest();

      setData(result);
      setSelectedRoleId(
        (current) =>
          current ||
          result.roles?.find((role) => role.code !== "owner")?.id ||
          result.roles?.[0]?.id,
      );
    } catch (loadError) {
      setError(loadError.message || "Unable to load access control.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const selectedRole = useMemo(
    () => data?.roles?.find((role) => role.id === selectedRoleId) || null,
    [data, selectedRoleId],
  );

  function toggle(code) {
    setNewRole((current) => ({
      ...current,
      permission_codes: current.permission_codes.includes(code)
        ? current.permission_codes.filter((item) => item !== code)
        : [...current.permission_codes, code],
    }));
  }

  async function createRole(event) {
    event.preventDefault();
    setSaving(true);

    try {
      await createAccessRoleRequest(newRole);
      setNewRole({
        name: "",
        code: "",
        description: "",
        permission_codes: [],
      });
      setMode(null);
      await load();
    } catch (createError) {
      setError(createError.message);
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
      setNewUser({
        full_name: "",
        email: "",
        password: "",
        phone: "",
        role_id: "",
        branch_id: "",
      });
      setMode(null);
      await load();
    } catch (createError) {
      setError(createError.message);
    } finally {
      setSaving(false);
    }
  }

  async function updateUser(user, updates) {
    setSaving(true);

    try {
      await updateCompanyUserRequest(user.id, {
        role_id: updates.role_id ?? user.role_id,
        branch_id:
          updates.branch_id !== undefined ? updates.branch_id : user.branch_id,
        status: updates.status ?? user.membership_status,
      });
      await load();
    } catch (updateError) {
      setError(updateError.message);
    } finally {
      setSaving(false);
    }
  }

  async function changePermission(code) {
    if (!selectedRole || selectedRole.code === "owner") {
      return;
    }

    const current = selectedRole.permission_codes || [];
    setSaving(true);

    try {
      await updateAccessRoleRequest(selectedRole.id, {
        permission_codes: current.includes(code)
          ? current.filter((item) => item !== code)
          : [...current, code],
      });
      await load();
    } catch (permissionError) {
      setError(permissionError.message);
    } finally {
      setSaving(false);
    }
  }

  async function runAccountAction() {
    const { kind, user } = confirming;

    setSaving(true);
    setError("");
    setNotice("");

    try {
      const result =
        kind === "reset"
          ? await forceUserPasswordResetRequest(user.id)
          : await unlockCompanyUserRequest(user.id);

      setNotice(result?.message || "Done.");
    } catch (actionError) {
      // Shown as it arrives. A 503 here is the mail server being unconfigured,
      // and the server's sentence names what has to be set to fix it —
      // replacing it with a generic failure would throw that away.
      setError(actionError.message);
    } finally {
      setConfirming(null);
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="admin-access-loading">Loading access control…</div>
    );
  }

  return (
    <section className="admin-access-page">
      {error ? (
        <div className="admin-access-error">{error}</div>
      ) : null}

      {notice ? (
        <div className="admin-access-notice">{notice}</div>
      ) : null}

      <div className="access-toolbar">
        <div>
          <strong>{data?.company?.name || "Company team"}</strong>
          <span>
            {data?.users?.length || 0} users · {data?.roles?.length || 0} roles
          </span>
        </div>

        <div>
          <button
            type="button"
            className="secondary-action"
            onClick={() => setMode("role")}
          >
            + Add role
          </button>

          <button
            type="button"
            className="primary-action"
            onClick={() => setMode("user")}
          >
            + Add user
          </button>
        </div>
      </div>

      <div className="admin-access-card users-card">
        <div className="users-card-header">
          <div>
            <h2>Users</h2>
            <p>Employees and administrators connected to this company.</p>
          </div>

          {/* Only for a company that has named a location. A filter offering
              one choice is a control that decides nothing. */}
          {data.branches.length ? (
            <label htmlFor="users-branch-filter">
              <span>Branch</span>

              <select
                id="users-branch-filter"
                value={branchFilter}
                onChange={(event) => setBranchFilter(event.target.value)}
              >
                <option value="all">All branches</option>
                <option value="none">No branch</option>
                {data.branches.map((branch) => (
                  <option value={String(branch.id)} key={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
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
                <th>Account</th>
              </tr>
            </thead>

            <tbody>
              {visibleUsers.map((user) => (
                <tr key={user.id}>
                  <td>
                    <strong>{user.full_name || "Unnamed user"}</strong>
                    <span>{user.email}</span>
                  </td>

                  <td>
                    <select
                      value={user.role_id || ""}
                      disabled={saving}
                      onChange={(event) =>
                        updateUser(user, {
                          role_id: Number(event.target.value),
                        })
                      }
                    >
                      {data.roles.map((role) => (
                        <option key={role.id} value={role.id}>
                          {role.name}
                        </option>
                      ))}
                    </select>
                  </td>

                  <td>
                    <select
                      value={user.branch_id || ""}
                      disabled={saving}
                      onChange={(event) =>
                        updateUser(user, {
                          branch_id: event.target.value
                            ? Number(event.target.value)
                            : null,
                        })
                      }
                    >
                      <option value="">All branches</option>
                      {data.branches.map((branch) => (
                        <option key={branch.id} value={branch.id}>
                          {branch.name}
                        </option>
                      ))}
                    </select>
                  </td>

                  <td>
                    <select
                      value={user.membership_status}
                      disabled={saving}
                      onChange={(event) =>
                        updateUser(user, { status: event.target.value })
                      }
                    >
                      <option value="active">Active</option>
                      <option value="disabled">Disabled</option>
                    </select>

                    {/* The membership above says whether this company still
                        employs them; `user_status` says whether the account
                        exists at all across the platform. Only worth the room
                        when the two disagree. */}
                    {user.user_status && user.user_status !== "active" ? (
                      <span className="user-account-status">
                        Account {user.user_status}
                      </span>
                    ) : null}
                  </td>

                  <td>{user.last_login_at || "Never"}</td>

                  <td>
                    <div className="user-actions">
                      <button
                        type="button"
                        className="secondary-action"
                        disabled={saving}
                        onClick={() =>
                          setConfirming({ kind: "reset", user })
                        }
                      >
                        Send reset link
                      </button>

                      <button
                        type="button"
                        className="secondary-action"
                        disabled={saving}
                        onClick={() =>
                          setConfirming({ kind: "unlock", user })
                        }
                      >
                        Unlock
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="admin-access-grid roles-management-grid">
        <aside className="admin-access-card role-list-card">
          <h2>Roles</h2>

          <div className="role-list">
            {data?.roles?.map((role) => (
              <button
                key={role.id}
                type="button"
                className={
                  role.id === selectedRoleId
                    ? "role-list-item active"
                    : "role-list-item"
                }
                onClick={() => setSelectedRoleId(role.id)}
              >
                <strong>{role.name}</strong>
                <span>
                  {role.code === "owner"
                    ? "Full company access"
                    : `${role.permission_codes?.length || 0} permissions`}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="admin-access-card permission-card">
          <h2>{selectedRole?.name || "Role details"}</h2>
          <p>
            {selectedRole?.description ||
              "Choose which platform actions this role can perform."}
          </p>

          {selectedRole?.code === "owner" ? (
            <div className="owner-access-note">
              Owner has full company access. Individual owner permissions are
              intentionally hidden.
            </div>
          ) : (
            <div className="permission-groups">
              {buildPermissionGroups(data).map((group) => (
                <div key={group.key} className="permission-group">
                  {group.label ? (
                    <div className="permission-group-header">
                      <div>
                        <strong>{group.label}</strong>
                        {group.description ? (
                          <span>{group.description}</span>
                        ) : null}
                      </div>
                      {group.web_only ? (
                        <span
                          className="web-only-badge"
                          title="These screens open on the web app, not on the phone."
                        >
                          Web only
                        </span>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="permission-grid">
                    {group.permissions.map((permission) => (
                      <label key={permission.code} className="permission-row">
                        <div>
                          <strong>{permission.name}</strong>
                          <span>{permission.code}</span>
                        </div>

                        <input
                          type="checkbox"
                          checked={Boolean(
                            selectedRole?.permission_codes?.includes(
                              permission.code,
                            ),
                          )}
                          disabled={saving}
                          onChange={() => changePermission(permission.code)}
                        />
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {confirming ? (
        <div
          className="admin-modal-backdrop"
          onMouseDown={() => setConfirming(null)}
        >
          <div
            className="admin-modal"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="admin-modal-header">
              <h2>{ACCOUNT_ACTIONS[confirming.kind].title}</h2>
              <button
                type="button"
                onClick={() => setConfirming(null)}
              >
                ×
              </button>
            </div>

            <p>
              {ACCOUNT_ACTIONS[confirming.kind].describe(confirming.user)}
            </p>

            <div className="admin-modal-actions">
              <button
                type="button"
                className="secondary-action"
                onClick={() => setConfirming(null)}
              >
                Cancel
              </button>

              <button
                type="button"
                className="primary-action"
                disabled={saving}
                onClick={runAccountAction}
              >
                {saving
                  ? "Working…"
                  : ACCOUNT_ACTIONS[confirming.kind].confirmLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {mode ? (
        <div
          className="admin-modal-backdrop"
          onMouseDown={() => setMode(null)}
        >
          <form
            className="admin-modal"
            onSubmit={mode === "user" ? createUser : createRole}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="admin-modal-header">
              <h2>{mode === "user" ? "Add company user" : "Add role"}</h2>
              <button type="button" onClick={() => setMode(null)}>
                ×
              </button>
            </div>

            {mode === "user" ? (
              <>
                <label>
                  Full name
                  <input
                    value={newUser.full_name}
                    onChange={(event) =>
                      setNewUser({ ...newUser, full_name: event.target.value })
                    }
                    required
                  />
                </label>

                <label>
                  Email
                  <input
                    type="email"
                    value={newUser.email}
                    onChange={(event) =>
                      setNewUser({ ...newUser, email: event.target.value })
                    }
                    required
                  />
                </label>

                <label>
                  Temporary password
                  <input
                    type="password"
                    minLength="8"
                    value={newUser.password}
                    onChange={(event) =>
                      setNewUser({ ...newUser, password: event.target.value })
                    }
                    required
                  />
                </label>

                <label>
                  Phone
                  <input
                    value={newUser.phone}
                    onChange={(event) =>
                      setNewUser({ ...newUser, phone: event.target.value })
                    }
                  />
                </label>

                <label>
                  Role
                  <select
                    value={newUser.role_id}
                    onChange={(event) =>
                      setNewUser({ ...newUser, role_id: event.target.value })
                    }
                    required
                  >
                    <option value="">Select role</option>
                    {data.roles.map((role) => (
                      <option key={role.id} value={role.id}>
                        {role.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  Branch
                  <select
                    value={newUser.branch_id}
                    onChange={(event) =>
                      setNewUser({ ...newUser, branch_id: event.target.value })
                    }
                  >
                    <option value="">All branches</option>
                    {data.branches.map((branch) => (
                      <option key={branch.id} value={branch.id}>
                        {branch.name}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            ) : (
              <>
                <label>
                  Role name
                  <input
                    value={newRole.name}
                    onChange={(event) =>
                      setNewRole({ ...newRole, name: event.target.value })
                    }
                    required
                  />
                </label>

                <label>
                  Role code
                  <input
                    value={newRole.code}
                    onChange={(event) =>
                      setNewRole({
                        ...newRole,
                        code: event.target.value
                          .toLowerCase()
                          .replace(/\s+/g, "_"),
                      })
                    }
                    required
                  />
                </label>

                <label>
                  Description
                  <textarea
                    value={newRole.description}
                    onChange={(event) =>
                      setNewRole({ ...newRole, description: event.target.value })
                    }
                  />
                </label>

                {buildPermissionGroups(data).map((group) => (
                  <div key={group.key} className="permission-mini-group">
                    {group.label ? (
                      <div className="permission-mini-heading">
                        <span>{group.label}</span>
                        {group.web_only ? (
                          <span className="web-only-badge">Web only</span>
                        ) : null}
                      </div>
                    ) : null}

                    <div className="permission-mini-list">
                      {group.permissions.map((permission) => (
                        <label key={permission.code}>
                          <input
                            type="checkbox"
                            checked={newRole.permission_codes.includes(
                              permission.code,
                            )}
                            onChange={() => toggle(permission.code)}
                          />
                          <span>{permission.name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </>
            )}

            <button
              className="primary-action"
              type="submit"
              disabled={saving}
            >
              {saving
                ? "Saving…"
                : mode === "user"
                  ? "Create user"
                  : "Create role"}
            </button>
          </form>
        </div>
      ) : null}
    </section>
  );
}
