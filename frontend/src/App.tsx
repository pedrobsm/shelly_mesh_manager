/** Phase 1 shell: toolbar + graph + drawer/popover, all read-only. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { Device, Graph, GraphEdge, GraphNode } from './api';
import GraphView, { type ViewOptions } from './graph/GraphView';
import DeviceDrawer from './components/DeviceDrawer';
import EdgePopover from './components/EdgePopover';
import Toolbar from './components/Toolbar';

const EMPTY_GRAPH: Graph = { nodes: [], edges: [] };
const POLL_MS = 700;

export default function App() {
  const [graph, setGraph] = useState<Graph>(EMPTY_GRAPH);
  const [devices, setDevices] = useState<Record<string, Device>>({});
  const [demo, setDemo] = useState(false);
  const [options, setOptions] = useState<ViewOptions>({
    showInactive: false,
    showExternal: true,
    search: '',
  });
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [scanning, setScanning] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [relayoutToken, setRelayoutToken] = useState(0);

  const refresh = useCallback(async () => {
    const [nextGraph, deviceList] = await Promise.all([api.graph(), api.devices()]);
    setGraph(nextGraph);
    setDevices(Object.fromEntries(deviceList.map((device) => [device.id, device])));
  }, []);

  useEffect(() => {
    api
      .health()
      .then((health) => setDemo(health.demo))
      .catch(() => undefined);
    refresh().catch((exc: Error) => setStatus(exc.message));
  }, [refresh]);

  const runScan = useCallback(async () => {
    setScanning(true);
    setStatus('Scan started…');
    try {
      const { scan_id: scanId } = await api.startScan();
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        const state = await api.scanStatus(scanId);
        if (state.status === 'running') {
          setStatus(`Scanning (${state.method})…`);
          continue;
        }
        setStatus(
          `${state.status === 'error' ? 'Scan failed' : 'Scan done'} · ${state.found} devices` +
            (state.errors.length ? ` · ${state.errors.length} errors` : ''),
        );
        break;
      }
      await refresh();
    } catch (exc) {
      setStatus((exc as Error).message);
    } finally {
      setScanning(false);
    }
  }, [refresh]);

  const counts = useMemo(
    () => ({
      dangling: graph.edges.filter((edge) => edge.status === 'dangling').length,
      unparsed: graph.edges.filter((edge) => edge.status === 'unparsed').length,
    }),
    [graph],
  );

  const nodeIndex = useMemo(
    () => Object.fromEntries(graph.nodes.map((node) => [node.id, node])),
    [graph],
  );

  const deviceCount = graph.nodes.filter((node) => node.type === 'shelly').length;

  return (
    <div className="app">
      <Toolbar
        demo={demo}
        deviceCount={deviceCount}
        counts={counts}
        options={options}
        onOptions={(next) => setOptions((current) => ({ ...current, ...next }))}
        onScan={runScan}
        onRelayout={() => setRelayoutToken((token) => token + 1)}
        scanning={scanning}
        status={status}
      />
      <main className="workspace">
        <GraphView
          graph={graph}
          devices={devices}
          options={options}
          relayoutToken={relayoutToken}
          onNodeClick={setSelectedNode}
          onEdgeClick={setSelectedEdge}
        />
        {selectedNode && (
          <DeviceDrawer
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
            onChanged={() => void refresh()}
          />
        )}
        {selectedEdge && (
          <EdgePopover
            edge={selectedEdge}
            nodes={nodeIndex}
            onClose={() => setSelectedEdge(null)}
          />
        )}
        <Legend />
      </main>
    </div>
  );
}

function Legend() {
  return (
    <div className="legend">
      <span>
        <i className="line ok" /> ok
      </span>
      <span>
        <i className="line disabled" /> disabled
      </span>
      <span>
        <i className="line dangling" /> dangling
      </span>
      <span>
        <i className="line unparsed" /> unparsed
      </span>
    </div>
  );
}
