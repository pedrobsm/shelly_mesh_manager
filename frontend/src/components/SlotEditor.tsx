/** The slot editor drawer (SPEC §5.4) — the one place Phase 2 writes to a device. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Device, SlotDetail, SlotUrl, UrlSpec, UrlTestResult } from '../api';
import { ApiError, api } from '../api';

type Props = {
  slotId: string;
  devices: Record<string, Device>;
  demo: boolean;
  onClose: () => void;
  onSaved: () => void;
};

/** A URL row as the editor holds it: where it came from, and how to send it back. */
type Row = {
  key: string;
  spec: UrlSpec;
  url: string;
  summary: string;
  status: string;
  rebuildable: boolean;
};

function rowFromStored(url: SlotUrl, index: number): Row {
  const { parsed } = url;
  let spec: UrlSpec;
  if (parsed.target_device_id && parsed.channel_id && parsed.status !== 'unparsed') {
    spec = {
      kind: 'built',
      target_device_id: parsed.target_device_id,
      channel_id: parsed.channel_id,
      command: parsed.command,
      params: parsed.params,
    };
  } else if (parsed.webhook_id) {
    spec = { kind: 'ha_webhook', webhook_id: parsed.webhook_id };
  } else {
    // Keeps an unparsed or dangling URL verbatim; the server allows that only
    // for one already stored on this slot.
    spec = { kind: 'raw_external', url: url.raw_url };
  }
  return {
    key: `${url.id}-${index}`,
    spec,
    url: url.raw_url,
    summary: parsed.summary,
    status: parsed.status,
    rebuildable: parsed.status === 'unparsed' || parsed.target_type === 'unknown_shelly',
  };
}

type Draft = {
  targetType: 'shelly' | 'ha_webhook' | 'raw_external';
  deviceId: string;
  channelId: string;
  command: string;
  params: Record<string, string>;
  webhookId: string;
  rawUrl: string;
};

const EMPTY_DRAFT: Draft = {
  targetType: 'shelly',
  deviceId: '',
  channelId: '',
  command: '',
  params: {},
  webhookId: '',
  rawUrl: '',
};

export default function SlotEditor({ slotId, devices, demo, onClose, onSaved }: Props) {
  const [slot, setSlot] = useState<SlotDetail | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mismatch, setMismatch] = useState<Record<string, unknown> | null>(null);
  const [stale, setStale] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tests, setTests] = useState<Record<string, UrlTestResult>>({});
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    const detail = await api.slot(slotId);
    setSlot(detail);
    setEnabled(detail.enabled);
    setRows(detail.urls.map(rowFromStored));
    setStale(false);
    setDirty(false);
    setError(null);
    setMismatch(null);
  }, [slotId]);

  useEffect(() => {
    load().catch((exc: Error) => setError(exc.message));
  }, [load]);

  const deviceList = useMemo(
    () => Object.values(devices).sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id)),
    [devices],
  );
  const draftDevice = draft ? devices[draft.deviceId] : undefined;
  const draftChannel = draftDevice?.channels.find((channel) => channel.id === draft?.channelId);
  const commandParams = draftChannel?.commands[draft?.command ?? ''] ?? [];

  // Keep the read-only preview in step with the wizard (§4: the app builds it).
  useEffect(() => {
    if (!draft || draft.targetType !== 'shelly' || !draft.channelId || !draft.command) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    const params: Record<string, number> = {};
    for (const [name, value] of Object.entries(draft.params)) {
      if (value !== '') params[name] = Number(value);
    }
    api
      .buildUrl({
        kind: 'built',
        target_device_id: draft.deviceId,
        channel_id: draft.channelId,
        command: draft.command,
        params,
      })
      .then((built) => !cancelled && setPreview(built.url))
      .catch((exc: Error) => !cancelled && setPreview(`⚠ ${exc.message}`));
    return () => {
      cancelled = true;
    };
  }, [draft]);

  const addDraft = () => {
    if (!draft) return;
    const params: Record<string, number> = {};
    for (const [name, value] of Object.entries(draft.params)) {
      if (value !== '') params[name] = Number(value);
    }
    let spec: UrlSpec;
    let summary: string;
    if (draft.targetType === 'shelly') {
      if (!draft.channelId || !draft.command) return;
      spec = {
        kind: 'built',
        target_device_id: draft.deviceId,
        channel_id: draft.channelId,
        command: draft.command,
        params,
      };
      summary = preview ?? '';
    } else if (draft.targetType === 'ha_webhook') {
      if (!draft.webhookId.trim()) return;
      spec = { kind: 'ha_webhook', webhook_id: draft.webhookId.trim() };
      summary = `→ Home Assistant · webhook ${draft.webhookId.trim()}`;
    } else {
      if (!draft.rawUrl.trim()) return;
      spec = { kind: 'raw_external', url: draft.rawUrl.trim() };
      summary = draft.rawUrl.trim();
    }
    setRows((current) => [
      ...current,
      {
        key: `new-${Date.now()}`,
        spec,
        url: preview ?? (spec.kind === 'raw_external' ? spec.url : ''),
        summary,
        status: 'new',
        rebuildable: false,
      },
    ]);
    setDraft(null);
    setPreview(null);
    setDirty(true);
  };

  const save = async (confirmDelete = false) => {
    if (!slot) return;
    setSaving(true);
    setError(null);
    setMismatch(null);
    try {
      await api.saveSlot(slot.id, {
        enabled,
        urls: rows.map((row) => row.spec),
        expect_revision: slot.revision,
        confirm_delete: confirmDelete,
      });
      onSaved();
      await load();
    } catch (exc) {
      if (exc instanceof ApiError) {
        setError(exc.message);
        if (exc.code === 'stale_slot') setStale(true);
        if (exc.code === 'verify_mismatch') setMismatch(exc.body);
        if (exc.code === 'confirm_delete') {
          setSaving(false);
          if (window.confirm(`${exc.message}\n\nDelete it?`)) return save(true);
          return;
        }
        if (exc.code === 'not_found') {
          onSaved();
          onClose();
          return;
        }
      } else {
        setError((exc as Error).message);
      }
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (row: Row) => {
    if (!row.url) return;
    setTests((current) => ({ ...current, [row.key]: { ...current[row.key], elapsed_ms: -1 } as UrlTestResult }));
    const result = await api.testUrl(row.url).catch(
      (exc: Error) => ({ status_code: null, elapsed_ms: 0, body_snippet: '', error: exc.message, simulated: false }),
    );
    setTests((current) => ({ ...current, [row.key]: result }));
  };

  if (!slot) {
    return (
      <aside className="drawer">
        <div className="panel-head">
          <h2>Action slot</h2>
          <button className="button ghost" onClick={onClose}>✕</button>
        </div>
        {error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}
      </aside>
    );
  }

  return (
    <aside className="drawer">
      <div className="panel-head">
        <h2>{slot.label}</h2>
        <button className="button ghost" onClick={onClose}>✕</button>
      </div>
      <div className="muted small">
        {slot.device_name ?? slot.device_id} · Gen {slot.device_gen} · <span className="mono">{slot.native_key}</span>
      </div>

      <label className="toggle section">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => {
            setEnabled(event.target.checked);
            setDirty(true);
          }}
        />
        Slot enabled
      </label>

      <div className="section">
        <h3>URLs ({rows.length})</h3>
        <ul className="list slots">
          {rows.map((row) => (
            <li key={row.key}>
              <div className="slot-head">
                <strong className="small">{row.summary || row.url}</strong>
                <span className={`tag tag-${row.status}`}>{row.status}</span>
              </div>
              {row.url && <code className="raw">{row.url}</code>}
              {row.rebuildable && (
                <div className="muted small">
                  This URL was not recognised — remove it and add one with the wizard.
                </div>
              )}
              <div className="form-row">
                <button
                  className="button"
                  onClick={() => runTest(row)}
                  disabled={!row.url}
                  title={demo ? 'Simulated in demo mode' : 'Fires this URL now, for real'}
                >
                  Test
                </button>
                <button
                  className="button"
                  onClick={() => {
                    setRows((current) => current.filter((candidate) => candidate.key !== row.key));
                    setDirty(true);
                  }}
                >
                  Remove
                </button>
                {tests[row.key] && (
                  <span className="muted small">
                    {tests[row.key].elapsed_ms === -1
                      ? 'testing…'
                      : tests[row.key].error
                        ? `failed: ${tests[row.key].error}`
                        : `HTTP ${tests[row.key].status_code} · ${tests[row.key].elapsed_ms} ms${
                            tests[row.key].simulated ? ' · simulated' : ''
                          }`}
                  </span>
                )}
              </div>
            </li>
          ))}
          {rows.length === 0 && <li className="muted">no URLs — this slot does nothing</li>}
        </ul>
      </div>

      {draft ? (
        <div className="section wizard">
          <h3>Add URL</h3>
          <label className="field">
            Target
            <select
              value={draft.targetType}
              onChange={(event) =>
                setDraft({ ...draft, targetType: event.target.value as Draft['targetType'] })
              }
            >
              <option value="shelly">Shelly device</option>
              <option value="ha_webhook">Home Assistant webhook</option>
              <option value="raw_external">Other external URL</option>
            </select>
          </label>

          {draft.targetType === 'shelly' && (
            <>
              <label className="field">
                Device
                <select
                  value={draft.deviceId}
                  onChange={(event) =>
                    setDraft({ ...draft, deviceId: event.target.value, channelId: '', command: '', params: {} })
                  }
                >
                  <option value="">choose…</option>
                  {deviceList.map((device) => (
                    <option key={device.id} value={device.id}>
                      {device.name ?? device.id} · {device.model} · Gen {device.gen}
                      {device.online ? '' : ' · offline'}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Channel
                <select
                  value={draft.channelId}
                  disabled={!draftDevice}
                  onChange={(event) =>
                    setDraft({ ...draft, channelId: event.target.value, command: '', params: {} })
                  }
                >
                  <option value="">choose…</option>
                  {draftDevice?.channels.map((channel) => (
                    <option key={channel.id} value={channel.id}>
                      {channel.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Command
                <select
                  value={draft.command}
                  disabled={!draftChannel}
                  onChange={(event) => setDraft({ ...draft, command: event.target.value, params: {} })}
                >
                  <option value="">choose…</option>
                  {Object.keys(draftChannel?.commands ?? {}).map((command) => (
                    <option key={command} value={command}>
                      {command.replace('_', ' ')}
                    </option>
                  ))}
                </select>
              </label>
              {commandParams.map((name) => (
                <label className="field" key={name}>
                  {name.replace('_', ' ')}
                  <input
                    type="number"
                    value={draft.params[name] ?? ''}
                    onChange={(event) =>
                      setDraft({ ...draft, params: { ...draft.params, [name]: event.target.value } })
                    }
                  />
                </label>
              ))}
              {preview && (
                <code className="raw" data-testid="url-preview">
                  {preview}
                </code>
              )}
            </>
          )}

          {draft.targetType === 'ha_webhook' && (
            <label className="field">
              Webhook id
              <input
                value={draft.webhookId}
                onChange={(event) => setDraft({ ...draft, webhookId: event.target.value })}
                placeholder="lights_all_off"
              />
            </label>
          )}

          {draft.targetType === 'raw_external' && (
            <label className="field">
              URL
              <input
                value={draft.rawUrl}
                onChange={(event) => setDraft({ ...draft, rawUrl: event.target.value })}
                placeholder="http://host/path"
              />
            </label>
          )}

          <div className="form-row">
            <button className="button primary" onClick={addDraft}>
              Add
            </button>
            <button className="button" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button className="button" onClick={() => setDraft({ ...EMPTY_DRAFT })}>
          Add URL
        </button>
      )}

      {error && <p className="error">{error}</p>}
      {stale && (
        <div className="form-row">
          <button className="button" onClick={() => void load()}>
            Reload the slot
          </button>
        </div>
      )}
      {mismatch && (
        <pre className="raw-block">{JSON.stringify(mismatch, null, 2)}</pre>
      )}

      <div className="form-row section">
        <button className="button primary" onClick={() => void save()} disabled={saving || stale}>
          {saving ? 'Saving…' : 'Save to device'}
        </button>
        {dirty && <span className="muted small">unsaved changes</span>}
        {demo && <span className="muted small">demo mode — nothing is sent to a device</span>}
      </div>
    </aside>
  );
}
