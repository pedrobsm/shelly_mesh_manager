/** Click a node -> all channels, all action slots with raw URLs, firmware (SPEC §1.4). */
import { useEffect, useState } from 'react';
import type { DeviceDetail, GraphNode } from '../api';
import { api } from '../api';

type Props = {
  node: GraphNode;
  onClose: () => void;
  onChanged: () => void;
};

export default function DeviceDrawer({ node, onClose, onChanged }: Props) {
  const [device, setDevice] = useState<DeviceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDevice(null);
    setError(null);
    if (node.type !== 'shelly') return;
    let cancelled = false;
    api
      .device(node.id)
      .then((detail) => {
        if (!cancelled) setDevice(detail);
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message);
      });
    return () => {
      cancelled = true;
    };
  }, [node]);

  const submitCredentials = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.setCredentials(node.id, username, password);
      const detail = await api.device(node.id);
      setDevice(detail);
      onChanged();
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside className="drawer">
      <div className="panel-head">
        <h2>
          {node.label}
          {device?.auth_required && <span title="password protected"> 🔒</span>}
        </h2>
        <button className="button ghost" onClick={onClose}>
          ✕
        </button>
      </div>

      {node.type !== 'shelly' && (
        <div className="section">
          <p className="muted">
            {node.type === 'unknown_shelly'
              ? 'This host answers Shelly-style URLs but is not in the inventory. Every edge into it is dangling.'
              : 'External target. Ports are grouped per webhook id or path.'}
          </p>
          <ul className="list">
            {node.inputs.map((port) => (
              <li key={port.id}>{port.label}</li>
            ))}
          </ul>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {device && (
        <>
          <dl className="kv">
            <dt>Model</dt>
            <dd>
              {device.model} · Gen {device.gen}
            </dd>
            <dt>IP</dt>
            <dd>
              <a href={`http://${device.ip}`} target="_blank" rel="noreferrer">
                {device.ip}
              </a>
            </dd>
            <dt>Firmware</dt>
            <dd>{device.fw_version ?? '—'}</dd>
            <dt>Profile</dt>
            <dd>{device.profile ?? '—'}</dd>
            <dt>State</dt>
            <dd>{device.online ? 'online' : 'offline'}</dd>
            <dt>Last seen</dt>
            <dd>{device.last_seen}</dd>
            <dt>Snapshots</dt>
            <dd>
              {device.snapshot_count}
              {device.last_snapshot_at ? ` · latest ${device.last_snapshot_at}` : ''}
            </dd>
          </dl>

          {device.auth_required && (
            <div className="section">
              <h3>Credentials</h3>
              <p className="muted">
                {device.has_credentials
                  ? 'Stored credentials are used for this device.'
                  : 'This device requires authentication to complete its inventory.'}
              </p>
              <div className="form-row">
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="username"
                />
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="password"
                />
                <button className="button primary" onClick={submitCredentials} disabled={saving}>
                  {saving ? 'Saving…' : 'Save & re-probe'}
                </button>
              </div>
            </div>
          )}

          <div className="section">
            <h3>Channels ({device.channels.length})</h3>
            <ul className="list">
              {device.channels.map((channel) => (
                <li key={channel.id}>
                  <span className="mono">{channel.kind} {channel.idx}</span> · {channel.label}
                </li>
              ))}
              {device.channels.length === 0 && <li className="muted">no channels</li>}
            </ul>
          </div>

          <div className="section">
            <h3>Action slots ({device.slots.length})</h3>
            <ul className="list slots">
              {device.slots.map((slot) => (
                <li key={slot.id} className={slot.enabled ? '' : 'muted'}>
                  <div className="slot-head">
                    <strong>{slot.label}</strong>
                    <span className={`tag ${slot.enabled ? 'tag-ok' : 'tag-disabled'}`}>
                      {slot.enabled ? 'enabled' : 'disabled'}
                    </span>
                  </div>
                  <div className="mono small">{slot.native_key}</div>
                  {slot.urls.length === 0 ? (
                    <div className="muted small">no URLs</div>
                  ) : (
                    slot.urls.map((url) => (
                      <code className="raw" key={url.id}>
                        {url.raw_url}
                      </code>
                    ))
                  )}
                </li>
              ))}
            </ul>
          </div>

          <details className="section">
            <summary>Raw device info</summary>
            <pre className="raw-block">{JSON.stringify(device.raw_info, null, 2)}</pre>
          </details>
        </>
      )}
    </aside>
  );
}
