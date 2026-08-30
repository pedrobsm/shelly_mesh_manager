/** Scan button, search, view toggles, re-layout and the health badges (SPEC §3.7). */
import type { EdgeRouting, ViewOptions } from '../graph/GraphView';

type Props = {
  demo: boolean;
  deviceCount: number;
  counts: { dangling: number; unparsed: number };
  options: ViewOptions;
  onOptions: (next: Partial<ViewOptions>) => void;
  onScan: () => void;
  onRelayout: () => void;
  scanning: boolean;
  status: string | null;
};

export default function Toolbar({
  demo,
  deviceCount,
  counts,
  options,
  onOptions,
  onScan,
  onRelayout,
  scanning,
  status,
}: Props) {
  return (
    <header className="toolbar">
      <div className="brand">
        <span className="brand-name">Shelly Mesh Manager</span>
        {demo && <span className="pill pill-demo">demo</span>}
      </div>

      <button className="button primary" onClick={onScan} disabled={scanning}>
        {scanning ? 'Scanning…' : 'Scan'}
      </button>
      <button className="button" onClick={onRelayout}>
        Re-layout
      </button>

      <input
        className="search"
        type="search"
        placeholder="Search name, IP, model, gen…"
        value={options.search}
        onChange={(event) => onOptions({ search: event.target.value })}
      />

      <label className="toggle">
        Edges
        <select
          className="select"
          value={options.routing}
          onChange={(event) => onOptions({ routing: event.target.value as EdgeRouting })}
        >
          <option value="direct">Direct</option>
          <option value="orthogonal">Orthogonal</option>
        </select>
      </label>

      <label className="toggle">
        <input
          type="checkbox"
          checked={options.showExternal}
          onChange={(event) => onOptions({ showExternal: event.target.checked })}
        />
        External nodes
      </label>
      <label className="toggle">
        <input
          type="checkbox"
          checked={options.showInactive}
          onChange={(event) => onOptions({ showInactive: event.target.checked })}
        />
        Show inactive actions
      </label>

      <div className="badges">
        <span className="pill">{deviceCount} devices</span>
        <span className={`pill ${counts.dangling ? 'pill-danger' : ''}`}>
          {counts.dangling} dangling
        </span>
        <span className={`pill ${counts.unparsed ? 'pill-warn' : ''}`}>
          {counts.unparsed} unparsed
        </span>
      </div>

      {status && <span className="status">{status}</span>}
    </header>
  );
}
