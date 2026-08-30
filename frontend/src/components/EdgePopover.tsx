/** Click an edge -> raw URL, source event, parse result (SPEC §1.4). */
import type { GraphEdge, GraphNode } from '../api';

type Props = {
  edge: GraphEdge;
  nodes: Record<string, GraphNode>;
  onClose: () => void;
};

const STATUS_TEXT: Record<GraphEdge['status'], string> = {
  ok: 'resolved',
  disabled: 'slot disabled',
  dangling: 'target not found',
  unparsed: 'URL not recognised',
};

function portLabel(node: GraphNode | undefined, portId: string): string {
  if (!node) return portId;
  const port = [...node.inputs, ...node.outputs].find((candidate) => candidate.id === portId);
  return port?.label ?? portId;
}

export default function EdgePopover({ edge, nodes, onClose }: Props) {
  const source = nodes[edge.source];
  const target = nodes[edge.target];
  return (
    <aside className="popover">
      <div className="panel-head">
        <h2>Action URL</h2>
        <button className="button ghost" onClick={onClose}>
          ✕
        </button>
      </div>
      <dl className="kv">
        <dt>Source</dt>
        <dd>
          {source?.label ?? edge.source} · {portLabel(source, edge.sourcePort)}
        </dd>
        <dt>Target</dt>
        <dd>
          {target?.label ?? edge.target} · {portLabel(target, edge.targetPort)}
        </dd>
        <dt>Command</dt>
        <dd>{edge.command}</dd>
        {edge.params && Object.keys(edge.params).length > 0 && (
          <>
            <dt>Parameters</dt>
            <dd>
              {Object.entries(edge.params)
                .map(([key, value]) => `${key}=${value}`)
                .join(', ')}
            </dd>
          </>
        )}
        <dt>Status</dt>
        <dd>
          <span className={`tag tag-${edge.status}`}>{edge.status}</span> {STATUS_TEXT[edge.status]}
        </dd>
        <dt>Raw URL</dt>
        <dd>
          <code className="raw">{edge.rawUrl}</code>
        </dd>
      </dl>
    </aside>
  );
}
