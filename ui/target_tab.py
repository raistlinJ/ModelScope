"""
Execution Target tab — canonical home for all SSH / execution-target widgets.

This is the ONLY file that defines the target_ssh_* keys.  Removing them from
config_tab.py and caf_tab.py eliminates all duplicate-key crashes.
"""
from __future__ import annotations

import streamlit as st


def _test_local_connection() -> None:
    """Attempt a probe command locally."""
    from core.environment import LocalEnvironment
    try:
        env = LocalEnvironment()
        res = env.execute("echo ok")
        if res["exit_code"] == 0 and "ok" in res["stdout"]:
            st.success("Local execution working successfully.")
        else:
            st.warning(f"Unexpected response: {res!r}")
    except Exception as exc:
        st.error(f"Local execution failed: {exc}")


def _test_pct_connection() -> None:
    """Attempt a probe command in the specified PCT container."""
    from core.environment import LocalEnvironment, PCTEnvironment
    vmid = (st.session_state.get("target_pct_vmid") or "").strip()
    if not vmid:
        st.error("VMID is required.")
        return
    try:
        env = PCTEnvironment(vmid, LocalEnvironment())
        res = env.execute("echo ok")
        if res["exit_code"] == 0 and "ok" in res["stdout"]:
            st.success(f"Connected to PCT container {vmid}")
        else:
            st.warning(f"Connected but unexpected response: {res!r}")
    except Exception as exc:
        st.error(f"PCT connection failed: {exc}")


def _test_ssh_connection() -> None:
    """Attempt a probe connection and show success or error inline."""
    import paramiko

    host     = (st.session_state.get("target_ssh_host") or "").strip()
    port     = int(st.session_state.get("target_ssh_port") or 22)
    user     = (st.session_state.get("target_ssh_user") or "root").strip()
    password = st.session_state.get("target_ssh_password") or ""
    key_path = (st.session_state.get("target_ssh_key_path") or "").strip()

    if not host:
        st.error("Host is required.")
        return
    try:
        client = paramiko.SSHClient()
        # SECURITY: AutoAddPolicy trusts unknown host keys (no MITM protection).
        # Intended only for the trusted lab network this tool targets.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname": host,
            "port":     port,
            "username": user,
            "timeout":  10,
        }
        if key_path:
            kwargs["key_filename"] = key_path
        if password:
            kwargs["password"] = password
        client.connect(**kwargs)
        _, stdout, _ = client.exec_command("echo ok")
        result = stdout.read().decode().strip()
        client.close()
        if result == "ok":
            st.success(f"Connected to {user}@{host}:{port}")
        else:
            st.warning(f"Connected but unexpected echo: {result!r}")
    except Exception as exc:
        st.error(f"Connection failed: {exc}")


def render() -> None:
    st.header("Execution Target")
    st.caption(
        "Choose where evaluation commands and CyberAgentFlow will execute. "
        "**Local** runs everything on this machine; **Remote (SSH)** connects to a Kali Linux VM."
    )

    # Canonical selectbox — keeps value space as "local" / "remote (SSH)" to
    # match all consumers (execute_tab.py:176,281 / batch_tab.py:129 / comparison_tab.py:20).
    st.radio(
        "Execution Mode",
        options=["local", "remote (SSH)", "pct (Proxmox LXC)"],
        format_func=lambda v: {"local": "Local", "remote (SSH)": "Remote (SSH)", "pct (Proxmox LXC)": "PCT (Proxmox LXC)"}.get(v, v),
        key="target_env_type",
        help="Where evaluation commands and CAF will execute.",
        horizontal=True,
    )

    target_env = st.session_state.get("target_env_type", "local")

    if target_env == "pct (Proxmox LXC)":
        st.divider()
        st.subheader("LXC Container ID (VMID)")
        st.text_input(
            "VMID",
            key="target_pct_vmid",
            placeholder="100",
            help="The numeric ID of the Proxmox container.",
        )
        col_test, _ = st.columns([2, 5])
        with col_test:
            if st.button("Test Connection", key="btn_test_pct_target", use_container_width=True, help="Verify execution in the PCT container"):
                _test_pct_connection()
    elif target_env == "remote (SSH)":
        st.divider()
        st.subheader("SSH Credentials")
        st.caption("Credentials for the remote Kali Linux machine running CyberAgentFlow.")

        c_host, c_port = st.columns([3, 1])
        with c_host:
            st.text_input(
                "Host",
                key="target_ssh_host",
                placeholder="192.168.1.100",
                help="Hostname or IP address of the remote Kali Linux machine",
            )
        with c_port:
            st.number_input(
                "Port",
                key="target_ssh_port",
                min_value=1,
                max_value=65535,
                value=22,
                step=1,
                help="SSH port on the remote machine (usually 22)",
            )

        c_user, c_pass = st.columns(2)
        with c_user:
            st.text_input(
                "Username",
                key="target_ssh_user",
                placeholder="root",
                help="SSH username for authentication on the remote machine",
            )
        with c_pass:
            st.text_input(
                "Password",
                key="target_ssh_password",
                type="password",
                placeholder="(leave blank if using key)",
                help="SSH password (leave blank if using key-based authentication)",
            )

        st.text_input(
            "Key Path",
            key="target_ssh_key_path",
            placeholder="/home/user/.ssh/id_rsa",
            help="Path to SSH private key on THIS machine. Leave blank to use password.",
        )
        st.text_input(
            "Remote CAF Directory",
            key="target_ssh_caf_dir",
            placeholder="~/cyber-agent-flow",
            help="Absolute path on the remote machine where CyberAgentFlow is installed.",
        )

        col_test, _ = st.columns([2, 5])
        with col_test:
            if st.button(
                "Test Connection",
                key="btn_test_ssh_target",
                use_container_width=True,
                help="Verify SSH credentials and connectivity to the remote host",
            ):
                _test_ssh_connection()

        # Status summary
        host    = (st.session_state.get("target_ssh_host") or "").strip()
        user    = st.session_state.get("target_ssh_user", "root")
        caf_dir = st.session_state.get("target_ssh_caf_dir") or "~/cyber-agent-flow"
        if host:
            st.info(f"Target: `{user}@{host}` | CAF dir: `{caf_dir}`")

        st.divider()
        col_save, col_note = st.columns([2, 5])
        with col_save:
            if st.button("💾 Save Settings", key="btn_target_save", use_container_width=True,
                         help="Save current target configuration to ~/.modelscope/settings.json"):
                from core.settings_store import save_settings
                save_settings(st.session_state)
                st.toast("Settings saved!", icon="💾")
        with col_note:
            st.caption(
                "Host, port, username, and CAF directory are saved. "
                "**Password and key path are never written to disk** (security)."
            )
    else:
        st.info(
            "Running locally on this machine. "
            "Switch to **Remote (SSH)** above to target a Kali Linux VM."
        )
        col_test, _ = st.columns([2, 5])
        with col_test:
            if st.button("Test Local Execution", key="btn_test_local_target", use_container_width=True, help="Verify local execution"):
                _test_local_connection()
