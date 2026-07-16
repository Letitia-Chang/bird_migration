import { useState, useEffect, useRef } from "react";
import * as d3 from "d3";
import { feature } from "topojson-client";

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// API LAYER
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

const API = {
  /**
   * GET /bird-observations?species=<name>&year=<year>
   * Returns: [{ common_name, scientific_name, year, month, h3_cell, lat, lon, observation_count }]
   * Note: backend currently has "limit 1" — remove it when ready for full density data
   */
  observations: (species, year, month) =>
    fetch(`${BASE_URL}/bird-observations?species=${encodeURIComponent(species)}&year=${year}&month=${encodeURIComponent(month)}`)
      .then(r => r.json()),
  
  nightlight: (year) =>
    fetch(`${BASE_URL}/nightlight?year=${year}`)
      .then(r => r.json()),

  nodes: (species, year, month) =>
    fetch(`${BASE_URL}/nodes?species=${encodeURIComponent(species)}&year=${year}&month=${encodeURIComponent(month)}`)
      .then(r => r.json()),

  edges: (species, year, month, maxDistanceMiles = 200, minDistanceMiles = 0, minCount = 2, topKPerSource = 2, topKPerTarget = 2) =>
    fetch(
      `${BASE_URL}/edges?species=${encodeURIComponent(species)}&year=${year}&month=${encodeURIComponent(month)}&max_distance_miles=${maxDistanceMiles}&min_distance_miles=${minDistanceMiles}&min_count=${minCount}&top_k_per_source=${topKPerSource}&top_k_per_target=${topKPerTarget}`
    ).then(r => r.json()),

  robustness: (species, year, month, removalPct = 0.3, trials = 20) =>
    fetch(
      `${BASE_URL}/experiment/robustness?species=${encodeURIComponent(species)}&year=${year}&month=${encodeURIComponent(month)}&removal_pct=${removalPct}&trials=${trials}`
    ).then(r => r.json()),

  fragilityCurve: (species, year, month, steps = 10, trials = 20) =>
    fetch(
      `${BASE_URL}/experiment/fragility-curve?species=${encodeURIComponent(species)}&year=${year}&month=${encodeURIComponent(month)}&steps=${steps}&trials=${trials}`
    ).then(r => r.json()),
  // Add more endpoints here as your backend grows:
  // network:    (species, year)             => fetch(`${BASE_URL}/network?species=${species}&year=${year}`).then(r => r.json()),
  // stresstest: (species, year, threshold)  => fetch(`${BASE_URL}/stresstest?species=${species}&year=${year}&threshold=${threshold}`).then(r => r.json()),
};

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// STATIC DATA — replace piece by piece as API endpoints come online
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const BIRDS = [
  { id: "ruby_throated",   name: "Ruby-throated Hummingbird", color: "#ef4444" },
  { id: "swainson",       name: "Swainson's Thrush",         color: "#f59e0b" },
  { id: "magnolia",    name: "Magnolia Warbler",              color: "#a78bfa" },
  { id: "sparrow", name: "Song Sparrow",    color: "#34d399" },
];

const YEARS = [2024, 2025, 2026];

const CITY_BIN_DEGREES = 0.60;   // bigger = more merging
const LIGHT_QUANTILE = 0.0;     // keep top 25% brightest nodes
const TOP_CITY_EDGES = 600;       // keep only strongest city-to-city edges
const MIN_CITY_ACTIVITY = 1;     // drop tiny merged clusters

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const SEASONS = [
  { key: "Q1", label: "Jan–Mar", months: [1, 2, 3], color: "#f472b6" },
  { key: "Q2", label: "Apr–Jun", months: [4, 5, 6], color: "#34d399" },
  { key: "Q3", label: "Jul–Sep", months: [7, 8, 9], color: "#f59e0b" },
  { key: "Q4", label: "Oct–Dec", months: [10, 11, 12], color: "#a78bfa" },
];

function seasonFromMonthNum(monthNum) {
  if (monthNum >= 1 && monthNum <= 3) return "Q1";
  if (monthNum >= 4 && monthNum <= 6) return "Q2";
  if (monthNum >= 7 && monthNum <= 9) return "Q3";
  return "Q4";
}

function seasonColor(seasonKey) {
  const season = SEASONS.find(s => s.key === seasonKey);
  return season ? season.color : "#cbd5e1";
}

const EXCLUDE_STATE_IDS = new Set(["02", "15"]);

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// MAP SETUP
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const VIEWBOX_W = 960;
const VIEWBOX_H = 580;

const projection = d3
  .geoAlbers()
  .scale(1280)
  .translate([VIEWBOX_W / 2, VIEWBOX_H / 2]);

const pathGenerator = d3.geoPath().projection(projection);

function useUSMap() {
  const [geo, setGeo] = useState(null);
  useEffect(() => {
    fetch("https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json")
      .then(r => r.json())
      .then(us => {
        const allStates = feature(us, us.objects.states);
        setGeo({
          states: {
            ...allStates,
            features: allStates.features.filter(f => !EXCLUDE_STATE_IDS.has(f.id)),
          },
          nation: feature(us, us.objects.nation),
        });
      });
  }, []);
  return geo;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// SUB-COMPONENTS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

// ── Map Legend ────────────────────────────────────────────────────────────────
function MapLegend({ showALAN }) {
  return (
    <g transform={`translate(16, ${VIEWBOX_H - 118})`}>
      <rect
        width={190}
        height={106}
        rx={6}
        fill="#0d1321"
        fillOpacity={0.92}
        stroke="rgba(100,160,220,0.2)"
        strokeWidth={0.8}
      />
      <text
        x={10}
        y={18}
        fontSize={9}
        fill="#4a6888"
        fontFamily="monospace"
        letterSpacing="0.07em"
      >
        LEGEND
      </text>

      <text x={10} y={34} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Bird density
      </text>

      <defs>
        <linearGradient id="densityGrad" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="#dbeafe" />
          <stop offset="35%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#1e3a8a" />
        </linearGradient>
      </defs>

      <rect x={10} y={40} width={120} height={10} rx={2} fill="url(#densityGrad)" />
      <text x={10} y={62} fontSize={9} fill="#5a7090" fontFamily="monospace">Low</text>
      <text x={102} y={62} fontSize={9} fill="#5a7090" fontFamily="monospace" textAnchor="end">High</text>

      {showALAN && (
        <>
          <text x={10} y={78} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
            ALAN intensity
          </text>
          <circle cx={18} cy={92} r={7} fill="#ffe066" opacity={0.25} />
          <circle cx={18} cy={92} r={4} fill="#ffe066" opacity={0.55} />
          <text x={30} y={96} fontSize={9} fill="#5a7090" fontFamily="monospace">
            Glow = light level
          </text>
        </>
      )}
    </g>
  );
}

// ── Network Legend ────────────────────────────────────────────────────────────
function NetworkLegend({ maxRawActivity }) {
  return (
    <g transform={`translate(16, ${VIEWBOX_H - 172})`}>
      <rect
        width={220}
        height={160}
        rx={6}
        fill="#0d1321"
        fillOpacity={0.92}
        stroke="rgba(100,160,220,0.2)"
        strokeWidth={0.8}
      />
      <text
        x={10}
        y={18}
        fontSize={9}
        fill="#4a6888"
        fontFamily="monospace"
        letterSpacing="0.07em"
      >
        LEGEND
      </text>

      <text x={10} y={34} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Bird density
      </text>

      <defs>
        <linearGradient id="networkDensityGrad" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="#dbeafe" />
          <stop offset="35%" stopColor="#38bdf8" />
          <stop offset="100%" stopColor="#1e3a8a" />
        </linearGradient>
      </defs>

      <rect x={10} y={40} width={120} height={10} rx={2} fill="url(#networkDensityGrad)" />
      <text x={10} y={62} fontSize={9} fill="#5a7090" fontFamily="monospace">Low</text>
      <text x={102} y={62} fontSize={9} fill="#5a7090" fontFamily="monospace" textAnchor="end">High</text>

      <text x={10} y={78} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        ALAN intensity
      </text>
      <circle cx={18} cy={92} r={7} fill="#ffe066" opacity={0.22} />
      <circle cx={18} cy={92} r={4} fill="#ffe066" opacity={0.5} />
      <text x={30} y={96} fontSize={9} fill="#5a7090" fontFamily="monospace">
        Glow = light level
      </text>

      <text x={10} y={114} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Seasonal flow
      </text>

      <line x1={10} y1={126} x2={40} y2={126} stroke="#f472b6" strokeWidth={2} />
      <text x={44} y={130} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Jan–Mar
      </text>

      <line x1={90} y1={126} x2={120} y2={126} stroke="#34d399" strokeWidth={2} />
      <text x={124} y={130} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Apr–Jun
      </text>

      <line x1={10} y1={142} x2={40} y2={142} stroke="#f59e0b" strokeWidth={2} />
      <text x={44} y={146} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Jul–Sep
      </text>

      <line x1={90} y1={142} x2={120} y2={142} stroke="#a78bfa" strokeWidth={2} />
      <text x={124} y={146} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Oct–Dec
      </text>
    </g>
  );
}

// ── Stress Legend ─────────────────────────────────────────────────────────────
function StressLegend() {
  return (
    <g transform={`translate(16, ${VIEWBOX_H - 206})`}>
      <rect
        width={230}
        height={192}
        rx={6}
        fill="#0d1321"
        fillOpacity={0.92}
        stroke="rgba(100,160,220,0.2)"
        strokeWidth={0.8}
      />
      <text x={10} y={18} fontSize={9} fill="#4a6888" fontFamily="monospace">
        LEGEND
      </text>

      {/* Active node */}
      <text x={10} y={34} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Active stopover node
      </text>
      <circle cx={18} cy={48} r={6} fill="#7eb8f0" opacity={0.85} />
      <text x={30} y={52} fontSize={9} fill="#5a7090" fontFamily="monospace">
        Current node in the network
      </text>

      {/* Removed node */}
      <text x={10} y={70} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Removed node
      </text>
      <circle cx={18} cy={84} r={7.5} fill="#f87171" opacity={0.82} stroke="#fecaca" strokeWidth={1.2} />
      <line x1={14} y1={80} x2={22} y2={88} stroke="#fee2e2" strokeWidth={1.2} />
      <line x1={22} y1={80} x2={14} y2={88} stroke="#fee2e2" strokeWidth={1.2} />
      <text x={30} y={88} fontSize={9} fill="#5a7090" fontFamily="monospace">
        Manually removed stopover
      </text>
      
      {/* Seasonal flow (2x2 layout SAME as Network View) */}
      <text x={10} y={108} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Seasonal flow
      </text>

      <line x1={10} y1={122} x2={34} y2={122} stroke="#f472b6" strokeWidth={2} />
      <text x={40} y={126} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Jan–Mar
      </text>

      <line x1={110} y1={122} x2={134} y2={122} stroke="#34d399" strokeWidth={2} />
      <text x={140} y={126} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Apr–Jun
      </text>

      <line x1={10} y1={138} x2={34} y2={138} stroke="#f59e0b" strokeWidth={2} />
      <text x={40} y={142} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Jul–Sep
      </text>

      <line x1={110} y1={138} x2={134} y2={138} stroke="#a78bfa" strokeWidth={2} />
      <text x={140} y={142} fontSize={8} fill="#5a7090" fontFamily="monospace">
        Oct–Dec
      </text>

      {/* Removed edges */}
      <text x={10} y={160} fontSize={10} fill="#8aa4c0" fontFamily="monospace">
        Removed edges
      </text>
      <line
        x1={10}
        y1={174}
        x2={34}
        y2={174}
        stroke="#f87171"
        strokeWidth={2.2}
        strokeDasharray="5 4"
      />
    </g>
  );
}

// ── Map View ──────────────────────────────────────────────────────────────────
function MapView({ geo, density, nightlight, showALAN, alanOpacity, selectedBird }) {
  const [tooltip, setTooltip] = useState(null);
  const [zoomTransform, setZoomTransform] = useState(d3.zoomIdentity);

  const svgRef = useRef(null);
  const zoomRef = useRef(null);

  const densityScale = d3.scaleLinear()
    .domain([0, 0.35, 1])
    .range(["#dbeafe", "#38bdf8", "#1e3a8a"]);

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);

    const zoomBehavior = d3.zoom()
      .scaleExtent([1, 30])
      .translateExtent([
        [-200, -200],
        [VIEWBOX_W + 200, VIEWBOX_H + 200],
      ])
      .on("zoom", (event) => {
        setZoomTransform(event.transform);
      });

    zoomRef.current = zoomBehavior;
    svg.call(zoomBehavior);

    return () => {
      svg.on(".zoom", null);
    };
  }, []);

  function resetZoom() {
    if (!svgRef.current || !zoomRef.current) return;

    d3.select(svgRef.current)
      .transition()
      .duration(300)
      .call(zoomRef.current.transform, d3.zoomIdentity);
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <button
        onClick={resetZoom}
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          zIndex: 2,
          background: "#111827",
          border: "1px solid rgba(100,160,220,0.25)",
          color: "#8aa4c0",
          padding: "6px 10px",
          borderRadius: 6,
          fontSize: 11,
          fontFamily: "monospace",
          cursor: "pointer",
        }}
      >
        Reset zoom
      </button>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
        width="100%"
        style={{ display: "block", borderRadius: 8, cursor: "grab" }}
      >
        <rect width={VIEWBOX_W} height={VIEWBOX_H} fill="#0d1321" rx={8} />

        <defs>
          {nightlight.map((pt, i) => {
            const innerColor = d3.interpolateYlOrBr(pt.intensity);
            return (
              <radialGradient key={i} id={`alan-${i}`} cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor={innerColor} stopOpacity={0.75 * alanOpacity} />
                <stop offset="40%" stopColor={innerColor} stopOpacity={0.35 * alanOpacity} />
                <stop offset="100%" stopColor={innerColor} stopOpacity={0} />
              </radialGradient>
            );
          })}
        </defs>

        {!geo ? (
          <text
            x={VIEWBOX_W / 2}
            y={VIEWBOX_H / 2}
            textAnchor="middle"
            fill="#3a5070"
            fontSize={14}
            fontFamily="monospace"
          >
            Loading map…
          </text>
        ) : (
          <>
            <g transform={zoomTransform.toString()}>
              {/* Nation fill */}
              <path
                d={pathGenerator(geo.nation)}
                fill="#111827"
                stroke="rgba(100,160,220,0.15)"
                strokeWidth={1}
              />

              {/* Bird density circles */}
              {density.map((cell, i) => {
                const pt = projection([cell.lng, cell.lat]);
                if (!pt) return null;
                return (
                  <circle
                    key={i}
                    cx={pt[0]}
                    cy={pt[1]}
                    r={3 + cell.density * 3}
                    fill={densityScale(cell.density)}
                    opacity={0.55}
                    style={{ cursor: "pointer" }}
                    onMouseEnter={() => setTooltip({ pt, cell })}
                    onMouseLeave={() => setTooltip(null)}
                  />
                );
              })}

              {/* ALAN glow */}
              {showALAN &&
                nightlight.map((nlPt, i) => {
                  const pt = projection([nlPt.lng, nlPt.lat]);
                  if (!pt) return null;

                  const r = nlPt.radius * (0.12 + nlPt.intensity * 0.18);

                  return <circle key={i} cx={pt[0]} cy={pt[1]} r={r} fill={`url(#alan-${i})`} />;
                })}

              {/* State borders */}
              {geo.states.features.map((f) => (
                <path
                  key={f.id}
                  d={pathGenerator(f)}
                  fill="none"
                  stroke="rgba(100,160,220,0.18)"
                  strokeWidth={0.6}
                />
              ))}
            </g>

            <MapLegend showALAN={showALAN} />

            {/* Hover tooltip */}
            {tooltip && (
              <g transform={`translate(${tooltip.pt[0] + 10},${tooltip.pt[1] - 56})`}>
                <rect
                  width={200}
                  height={56}
                  rx={4}
                  fill="#1a2540"
                  stroke="rgba(100,160,220,0.3)"
                  strokeWidth={0.8}
                />
                <text x={8} y={14} fontSize={10} fill="#7eb8f0" fontFamily="monospace">
                  {tooltip.cell.name ?? "Observation"}
                </text>
                <text x={8} y={28} fontSize={10} fill="#c8d4e8" fontFamily="monospace">
                  Observations:{" "}
                  {tooltip.cell.count?.toLocaleString() ??
                    (tooltip.cell.density * 100).toFixed(0) + "%"}
                </text>
                <text x={8} y={42} fontSize={10} fill="#c8d4e8" fontFamily="monospace">
                  Month: {tooltip.cell.month ?? "—"} · {tooltip.cell.lat?.toFixed(2)},{" "}
                  {tooltip.cell.lng?.toFixed(2)}
                </text>
              </g>
            )}
          </>
        )}
      </svg>
    </div>
  );
}

// ── Network View ──────────────────────────────────────────────────────────────
function NetworkView({ geo, network, activeSeasons }) {
  const visibleEdges = activeSeasons
    ? network.edges.filter(e => activeSeasons.has(e.season))
    : network.edges;
  network = { ...network, edges: visibleEdges };
  const nodeMap = Object.fromEntries(network.nodes.map(n => [n.id, n]));
  const maxRawActivity = Math.max(...network.nodes.map(n => n.rawActivity || 0), 1);
  const densityColorScale = d3.scaleLinear()
    .domain([0, 0.35, 1])
    .range(["#dbeafe", "#38bdf8", "#1e3a8a"]);
  const glowScale = d3.scaleLinear([0, 1], [0, 16]).clamp(true);
  const nodeRadiusScale = d3.scaleLinear([0, 1], [4, 7]).clamp(true);

  const [tooltip, setTooltip] = useState(null);
  const [zoomTransform, setZoomTransform] = useState(d3.zoomIdentity);

  const svgRef = useRef(null);
  const zoomRef = useRef(null);

  const edgeSet = new Set(
    network.edges.map(e => `${e.source}__${e.target}`)
  );

  function nodeFill(n) {
    return densityColorScale(n.activity);
  }

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);

    const zoomBehavior = d3.zoom()
      .scaleExtent([1, 30])
      .translateExtent([
        [-200, -200],
        [VIEWBOX_W + 200, VIEWBOX_H + 200],
      ])
      .on("zoom", (event) => {
        setZoomTransform(event.transform);
      });

    zoomRef.current = zoomBehavior;
    svg.call(zoomBehavior);

    return () => {
      svg.on(".zoom", null);
    };
  }, []);

  function edgeColor(flow) {
    if (flow < 0.33) return "#f5d0fe";   // low
    if (flow < 0.66) return "#e879f9";   // medium
    return "#c026d3";                    // high
  }

  function edgePath(sp, tp, bend = 0) {
    if (bend === 0) {
      return `M ${sp[0]} ${sp[1]} L ${tp[0]} ${tp[1]}`;
    }

    const mx = (sp[0] + tp[0]) / 2;
    const my = (sp[1] + tp[1]) / 2;

    const dx = tp[0] - sp[0];
    const dy = tp[1] - sp[1];
    const len = Math.sqrt(dx * dx + dy * dy) || 1;

    const nx = -dy / len;
    const ny = dx / len;

    const cx = mx + nx * bend;
    const cy = my + ny * bend;

    return `M ${sp[0]} ${sp[1]} Q ${cx} ${cy} ${tp[0]} ${tp[1]}`;
  }

  function hasReverseEdge(edgeSet, source, target) {
    return edgeSet.has(`${target}__${source}`);
  }

  function resetZoom() {
    if (!svgRef.current || !zoomRef.current) return;

    d3.select(svgRef.current)
      .transition()
      .duration(300)
      .call(zoomRef.current.transform, d3.zoomIdentity);
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <button
        onClick={resetZoom}
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          zIndex: 2,
          background: "#111827",
          border: "1px solid rgba(100,160,220,0.25)",
          color: "#8aa4c0",
          padding: "6px 10px",
          borderRadius: 6,
          fontSize: 11,
          fontFamily: "monospace",
          cursor: "pointer",
        }}
      >
        Reset zoom
      </button>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
        width="100%"
        style={{ display: "block", borderRadius: 8, cursor: "grab" }}
      >
        <rect width={VIEWBOX_W} height={VIEWBOX_H} fill="#0d1321" rx={8} />

        <defs>
          <marker
            id="edgeArrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="4"
            markerHeight="4"
            orient="auto"
            markerUnits="userSpaceOnUse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
          </marker>
        </defs>

        <g transform={zoomTransform.toString()}>
          {geo && (
            <>
              <path
                d={pathGenerator(geo.nation)}
                fill="#111827"
                stroke="rgba(100,160,220,0.15)"
                strokeWidth={1}
              />
              {geo.states.features.map((f) => (
                <path
                  key={f.id}
                  d={pathGenerator(f)}
                  fill="none"
                  stroke="rgba(100,160,220,0.18)"
                  strokeWidth={0.6}
                />
              ))}
            </>
          )}

          {network.nodes.map((n) => {
            const pt = projection([n.lng, n.lat]);
            if (!pt) return null;

            const glowR = glowScale(n.lightIntensity);
            const r = nodeRadiusScale(n.activity);

            return (
              <g
                key={n.id}
                style={{ cursor: "pointer" }}
                onMouseEnter={() => setTooltip({ pt, n })}
                onMouseLeave={() => setTooltip(null)}
              >
                {/* soft ALAN glow */}
                {glowR > 0 && (
                  <circle
                    cx={pt[0]}
                    cy={pt[1]}
                    r={glowR}
                    fill="#ffe066"
                    opacity={0.06 + n.lightIntensity * 0.12}
                  />
                )}

                {/* soft bird density node */}
                <circle
                  cx={pt[0]}
                  cy={pt[1]}
                  r={r}
                  fill={nodeFill(n)}
                  opacity={0.82}
                />
              </g>
            );
          })}

          {network.edges.map((e, i) => {
            const s = nodeMap[e.source];
            const t = nodeMap[e.target];
            if (!s || !t) return null;

            const sp = projection([s.lng, s.lat]);
            const tp = projection([t.lng, t.lat]);
            if (!sp || !tp) return null;

            const reverseExists = hasReverseEdge(edgeSet, e.source, e.target);

            let bend = 0;
            if (reverseExists) {
              bend = 10;
            }

            const d = edgePath(sp, tp, bend);

            return (
              <path
                key={i}
                d={d}
                fill="none"
                stroke={seasonColor(e.season)}
                strokeWidth={2}
                strokeLinecap="round"
                opacity={0.35 + e.flow * 0.55}
                markerEnd="url(#edgeArrow)"
              />
            );
          })}
        </g>

        <NetworkLegend maxRawActivity={maxRawActivity} />

        {tooltip && (
          <g transform={`translate(${tooltip.pt[0] + 14},${tooltip.pt[1] - 54})`}>
            <rect
              width={178}
              height={52}
              rx={4}
              fill="#1a2540"
              stroke="rgba(100,160,220,0.3)"
              strokeWidth={0.8}
            />
            <text x={8} y={16} fontSize={10} fill="#7eb8f0" fontFamily="monospace">
              Node {tooltip.n.id}
            </text>
            <text x={8} y={30} fontSize={10} fill="#c8d4e8" fontFamily="monospace">
              Activity: {(tooltip.n.activity * 100).toFixed(0)}%
            </text>
            <text x={8} y={44} fontSize={10} fill="#c8d4e8" fontFamily="monospace">
              Density: {(tooltip.n.activity * 100).toFixed(0)}% · Light: {(tooltip.n.lightIntensity * 100).toFixed(0)}%
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

// ── Stress Test View ──────────────────────────────────────────────────────────

function MetricCard({ label, value, color = "#7eb8f0", sublabel = null }) {
  return (
    <div
      style={{
        background: "#111827",
        borderRadius: 8,
        border: "1px solid rgba(100,160,220,0.12)",
        padding: "10px 14px",
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: "#4a6888",
          fontFamily: "monospace",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 4,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 700,
          color,
          fontFamily: "monospace",
        }}
      >
        {value}
      </div>
      {sublabel && (
        <div style={{ fontSize: 10, color: "#5a7090", marginTop: 4 }}>{sublabel}</div>
      )}
    </div>
  );
}

function StressTestView({ geo, network, activeSeasons }) {
  const [manualRemovedIds, setManualRemovedIds] = useState(new Set());
  const [zoomTransform, setZoomTransform] = useState(d3.zoomIdentity);
  const svgRef = useRef(null);
  const zoomRef = useRef(null);

  // Season filtering only affects what's drawn — LCC/fragmentation metrics
  // always reflect the full-year network regardless of which seasons are
  // toggled on for display.
  const visibleEdges = activeSeasons
    ? network.edges.filter(e => activeSeasons.has(e.season))
    : network.edges;

  const nodeMap = Object.fromEntries(network.nodes.map(n => [n.id, n]));
  const baselineMetrics = computeGraphMetrics(network.nodes, network.edges, new Set());
  const removedIds = new Set(manualRemovedIds);
  const currentMetrics = computeGraphMetrics(network.nodes, network.edges, removedIds);

  const edgeSet = new Set(visibleEdges.map(e => `${e.source}__${e.target}`));
  const densityColorScale = d3.scaleLinear()
    .domain([0, 0.35, 1])
    .range(["#dbeafe", "#38bdf8", "#1e3a8a"]);
  const glowScale = d3.scaleLinear([0, 1], [0, 16]).clamp(true);
  const nodeRadiusScale = d3.scaleLinear([0, 1], [4, 7]).clamp(true);

  useEffect(() => {
    setManualRemovedIds(new Set());
  }, [network.nodes]);

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);

    const zoomBehavior = d3.zoom()
      .scaleExtent([1, 30])
      .translateExtent([
        [-200, -200],
        [VIEWBOX_W + 200, VIEWBOX_H + 200],
      ])
      .on("zoom", (event) => {
        setZoomTransform(event.transform);
      });

    zoomRef.current = zoomBehavior;
    svg.call(zoomBehavior);

    return () => {
      svg.on(".zoom", null);
    };
  }, []);

  function toggleRemoved(id) {
    setManualRemovedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearManualRemovals() {
    setManualRemovedIds(new Set());
  }

  function resetZoom() {
    if (!svgRef.current || !zoomRef.current) return;

    d3.select(svgRef.current)
      .transition()
      .duration(300)
      .call(zoomRef.current.transform, d3.zoomIdentity);
  }

  function edgeColor(flow) {
    if (flow < 0.33) return "#f5d0fe";
    if (flow < 0.66) return "#e879f9";
    return "#c026d3";
  }

  function edgePath(sp, tp, bend = 0) {
    if (bend === 0) {
      return `M ${sp[0]} ${sp[1]} L ${tp[0]} ${tp[1]}`;
    }

    const mx = (sp[0] + tp[0]) / 2;
    const my = (sp[1] + tp[1]) / 2;

    const dx = tp[0] - sp[0];
    const dy = tp[1] - sp[1];
    const len = Math.sqrt(dx * dx + dy * dy) || 1;

    const nx = -dy / len;
    const ny = dx / len;

    const cx = mx + nx * bend;
    const cy = my + ny * bend;

    return `M ${sp[0]} ${sp[1]} Q ${cx} ${cy} ${tp[0]} ${tp[1]}`;
  }

  function hasReverseEdge(source, target) {
    return edgeSet.has(`${target}__${source}`);
  }

  function nodeFill(n) {
    return densityColorScale(n.activity);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, height: "100%" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
        <MetricCard
          label="LCC"
          value={`${currentMetrics.lccSize}`}
          color="#34d399"
          sublabel={`Baseline: ${baselineMetrics.lccSize}`}
        />
        <MetricCard
          label="Components"
          value={currentMetrics.numberOfComponents}
          color="#fbbf24"
          sublabel={`Baseline: ${baselineMetrics.numberOfComponents}`}
        />
        <MetricCard
          label="Fragmentation"
          value={`${(currentMetrics.fragmentationRate * 100).toFixed(0)}%`}
          color="#f87171"
          sublabel="1 - LCC_after / LCC_before"
        />
        <MetricCard
          label="Removed nodes"
          value={`${currentMetrics.removedCount}`}
          color="#a78bfa"
          sublabel={`${currentMetrics.removedCount}/${network.nodes.length} removed`}
        />
      </div>

      <div style={{ position: "relative", width: "100%", height: "100%" }}>
        <button
          onClick={clearManualRemovals}
          style={{
            position: "absolute",
            top: 12,
            left: 12,
            zIndex: 3,
            background: "rgba(17,24,39,0.92)",
            border: "1px solid rgba(100,160,220,0.25)",
            color: "#8aa4c0",
            padding: "6px 10px",
            borderRadius: 6,
            fontSize: 11,
            fontFamily: "monospace",
            cursor: "pointer",
          }}
        >
          Clear manual removals
        </button>

        <button
          onClick={resetZoom}
          style={{
            position: "absolute",
            top: 12,
            right: 12,
            zIndex: 3,
            background: "rgba(17,24,39,0.92)",
            border: "1px solid rgba(100,160,220,0.25)",
            color: "#8aa4c0",
            padding: "6px 10px",
            borderRadius: 6,
            fontSize: 11,
            fontFamily: "monospace",
            cursor: "pointer",
          }}
        >
          Reset zoom
        </button>

        <svg
          ref={svgRef}
          viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
          width="100%"
          style={{ display: "block", borderRadius: 8, cursor: "grab" }}
        >
          <rect width={VIEWBOX_W} height={VIEWBOX_H} fill="#0d1321" rx={8} />

          <defs>
            <marker
              id="stressEdgeArrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="4"
              markerHeight="4"
              orient="auto"
              markerUnits="userSpaceOnUse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
            </marker>
          </defs>

          <g transform={zoomTransform.toString()}>
            {geo && (
              <>
                <path
                  d={pathGenerator(geo.nation)}
                  fill="#111827"
                  stroke="rgba(100,160,220,0.15)"
                  strokeWidth={1}
                />
                {geo.states.features.map((f) => (
                  <path
                    key={f.id}
                    d={pathGenerator(f)}
                    fill="none"
                    stroke="rgba(100,160,220,0.18)"
                    strokeWidth={0.6}
                  />
                ))}
              </>
            )}

            {network.nodes.map((n) => {
              const pt = projection([n.lng, n.lat]);
              if (!pt) return null;

              const removed = removedIds.has(n.id);
              const glowR = glowScale(n.lightIntensity);
              const r = nodeRadiusScale(n.activity);

              return (
                <g
                  key={n.id}
                  style={{ cursor: "pointer" }}
                  onClick={() => toggleRemoved(n.id)}
                >
                  {!removed && glowR > 0 && (
                    <circle
                      cx={pt[0]}
                      cy={pt[1]}
                      r={glowR}
                      fill="#ffe066"
                      opacity={0.06 + n.lightIntensity * 0.12}
                    />
                  )}

                  {!removed ? (
                    <circle
                      cx={pt[0]}
                      cy={pt[1]}
                      r={r}
                      fill={nodeFill(n)}
                      opacity={0.82}
                    />
                  ) : (
                    <>
                      <circle
                        cx={pt[0]}
                        cy={pt[1]}
                        r={r + 1.4}
                        fill="#f87171"
                        opacity={0.82}
                        stroke="#fecaca"
                        strokeWidth={1.2}
                      />
                      <line
                        x1={pt[0] - 3.2}
                        y1={pt[1] - 3.2}
                        x2={pt[0] + 3.2}
                        y2={pt[1] + 3.2}
                        stroke="#fee2e2"
                        strokeWidth={1.2}
                      />
                      <line
                        x1={pt[0] + 3.2}
                        y1={pt[1] - 3.2}
                        x2={pt[0] - 3.2}
                        y2={pt[1] + 3.2}
                        stroke="#fee2e2"
                        strokeWidth={1.2}
                      />
                    </>
                  )}
                </g>
              );
            })}

            {visibleEdges.map((e, i) => {
              const s = nodeMap[e.source];
              const t = nodeMap[e.target];
              if (!s || !t) return null;

              const sp = projection([s.lng, s.lat]);
              const tp = projection([t.lng, t.lat]);
              if (!sp || !tp) return null;

              const reverseExists = hasReverseEdge(e.source, e.target);
              let bend = 0;

              if (reverseExists) {
                const dx = tp[0] - sp[0];
                const dy = tp[1] - sp[1];
                bend = dx >= 0 ? 10 : 10;
                if (Math.abs(dx) < Math.abs(dy)) {
                  bend = dy >= 0 ? 10 : 10;
                }
              }

              const d = edgePath(sp, tp, bend);
              const lost = removedIds.has(e.source) || removedIds.has(e.target);

              return (
                <path
                  key={i}
                  d={d}
                  fill="none"
                  stroke={seasonColor(e.season)}
                  strokeWidth={2}
                  strokeLinecap="round"
                  opacity={lost ? 0.9 : 0.95}
                  strokeDasharray={lost ? "5 4" : "none"}
                  markerEnd="url(#stressEdgeArrow)"
                />
              );
            })}
          </g>

          <StressLegend />
        </svg>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 12 }}>
  <div
    style={{
      background: "#111827",
      borderRadius: 8,
      border: "1px solid rgba(100,160,220,0.12)",
      padding: 16,
      textAlign: "left",
    }}
  >
    <div
      style={{
        fontSize: 10,
        color: "#4a6888",
        fontFamily: "monospace",
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        marginBottom: 12,
      }}
    >
      Understanding the metrics
    </div>

    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", rowGap: 10, columnGap: 12, fontSize: 12, color: "#8aa4c0", lineHeight: 1.6 }}>
      
      <div style={{ color: "#dbeafe", fontWeight: 500 }}>LCC</div>
      <div>
        Largest connected group of stopover sites.  
        <span style={{ color: "#9fb3c8" }}> Lower → backbone is shrinking</span>
      </div>

      <div style={{ color: "#dbeafe", fontWeight: 500 }}>Components</div>
      <div>
        Number of disconnected groups.  
        <span style={{ color: "#9fb3c8" }}> Higher → network is splitting</span>
      </div>

      <div style={{ color: "#dbeafe", fontWeight: 500 }}>Fragmentation</div>
      <div>
        Connectivity loss relative to original network  
        (1 − LCC_after / LCC_before).  
        <span style={{ color: "#9fb3c8" }}> Higher → more severe damage</span>
      </div>

      <div style={{ color: "#dbeafe", fontWeight: 500 }}>Removed nodes</div>
      <div>
        Number of stopover sites removed manually.
      </div>

    </div>
  </div>
</div>
    </div>
  );
}


// ── Info Card ─────────────────────────────────────────────────────────────────

function ExperimentCurveChart({ result }) {
  if (!result || !Array.isArray(result.points) || result.points.length === 0) {
    return (
      <div style={{
        background: "#111827",
        border: "1px solid rgba(100,160,220,0.12)",
        borderRadius: 8,
        padding: 16,
        color: "#5a7090",
        fontSize: 12,
      }}>
        No fragility curve data available.
      </div>
    );
  }

  const width = 760;
  const height = 260;
  const margin = { top: 18, right: 18, bottom: 34, left: 42 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const x = d3.scaleLinear().domain([0, 1]).range([0, innerW]);
  const y = d3.scaleLinear().domain([0, 1]).range([innerH, 0]);

  const lineLight = d3.line()
    .x(d => x(d.pct_removed))
    .y(d => y(d.light_based?.lcc_ratio ?? 0))
    .curve(d3.curveMonotoneX);

  const lineRandom = d3.line()
    .x(d => x(d.pct_removed))
    .y(d => y(d.random_avg?.lcc_ratio ?? 0))
    .curve(d3.curveMonotoneX);

  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div style={{
      background: "#111827",
      border: "1px solid rgba(100,160,220,0.12)",
      borderRadius: 8,
      padding: 14,
    }}>
      <div style={{
        fontSize: 10, color: "#4a6888", fontFamily: "monospace",
        textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10,
      }}>
        Fragility Curve
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ display: "block" }}>
        <rect x="0" y="0" width={width} height={height} rx="8" fill="#111827" />
        <g transform={`translate(${margin.left},${margin.top})`}>
          {ticks.map(t => (
            <g key={`y-${t}`} transform={`translate(0,${y(t)})`}>
              <line x1="0" x2={innerW} stroke="rgba(100,160,220,0.10)" />
              <text x={-8} y={4} textAnchor="end" fontSize={10} fill="#5a7090" fontFamily="monospace">
                {Math.round(t * 100)}%
              </text>
            </g>
          ))}

          {ticks.map(t => (
            <g key={`x-${t}`} transform={`translate(${x(t)},0)`}>
              <line y1="0" y2={innerH} stroke="rgba(100,160,220,0.08)" />
              <text x={0} y={innerH + 16} textAnchor="middle" fontSize={10} fill="#5a7090" fontFamily="monospace">
                {Math.round(t * 100)}%
              </text>
            </g>
          ))}

          <path d={lineRandom(result.points)} fill="none" stroke="#7eb8f0" strokeWidth="2.5" />
          <path d={lineLight(result.points)} fill="none" stroke="#f87171" strokeWidth="2.5" />

          {result.points.map((d, i) => (
            <circle key={`r-${i}`} cx={x(d.pct_removed)} cy={y(d.random_avg?.lcc_ratio ?? 0)} r="3" fill="#7eb8f0" />
          ))}
          {result.points.map((d, i) => (
            <circle key={`l-${i}`} cx={x(d.pct_removed)} cy={y(d.light_based?.lcc_ratio ?? 0)} r="3" fill="#f87171" />
          ))}

          <text x={innerW / 2} y={innerH + 30} textAnchor="middle" fontSize={10} fill="#8aa4c0" fontFamily="monospace">
            % nodes removed
          </text>
          <text
            transform={`translate(${-30},${innerH / 2}) rotate(-90)`}
            textAnchor="middle"
            fontSize={10}
            fill="#8aa4c0"
            fontFamily="monospace"
          >
            LCC ratio
          </text>
        </g>
      </svg>

      <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 12, color: "#8aa4c0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 999, background: "#f87171", display: "inline-block" }} />
          High-light removal
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 999, background: "#7eb8f0", display: "inline-block" }} />
          Random removal average
        </div>
      </div>
    </div>
  );
}

function ExperimentsView({ robustnessResult, fragilityCurveResult }) {
  if (!robustnessResult && !fragilityCurveResult) {
    return (
      <div style={{
        background: "#111827",
        border: "1px solid rgba(100,160,220,0.12)",
        borderRadius: 8,
        padding: 18,
        color: "#5a7090",
        fontSize: 12,
      }}>
        No experiment data available.
      </div>
    );
  }

  const high = robustnessResult?.high_light;
  const randomAvg = robustnessResult?.random_avg;
  const baseline = robustnessResult?.baseline;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, height: "100%" }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
        <MetricCard
          label="Removal %"
          value={`${Math.round((robustnessResult?.removal_pct ?? 0) * 100)}%`}
          color="#a78bfa"
          sublabel={`${robustnessResult?.removal_count ?? 0} nodes removed`}
        />
        <MetricCard
          label="High-light LCC"
          value={`${Math.round((high?.lcc_ratio ?? 0) * 100)}%`}
          color="#f87171"
          sublabel={`Baseline LCC: ${baseline?.lcc ?? 0}`}
        />
        <MetricCard
          label="Random LCC"
          value={`${Math.round((randomAvg?.lcc_ratio ?? 0) * 100)}%`}
          color="#7eb8f0"
          sublabel={`Averaged across ${robustnessResult?.trials ?? 0} trials`}
        />
        <MetricCard
          label="Fragility gap"
          value={`${Math.round(((randomAvg?.lcc_ratio ?? 0) - (high?.lcc_ratio ?? 0)) * 100)} pts`}
          color="#fbbf24"
          sublabel="Positive gap means light-based removal hurts more"
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 12 }}>
        <div style={{
          background: "#111827",
          border: "1px solid rgba(100,160,220,0.12)",
          borderRadius: 8,
          padding: 14,
        }}>
          <div style={{
            fontSize: 10, color: "#4a6888", fontFamily: "monospace",
            textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10,
          }}>
            Robustness Comparison
          </div>

          <div style={{
            display: "grid",
            gridTemplateColumns: "140px repeat(3, 1fr)",
            gap: 8,
            fontSize: 12,
            color: "#8aa4c0",
            alignItems: "center",
          }}>
            <div style={{ color: "#5a7090", fontFamily: "monospace" }}>Strategy</div>
            <div style={{ color: "#5a7090", fontFamily: "monospace" }}>LCC</div>
            <div style={{ color: "#5a7090", fontFamily: "monospace" }}>Components</div>
            <div style={{ color: "#5a7090", fontFamily: "monospace" }}>Fragmentation</div>

            <div style={{ color: "#f87171", fontWeight: 600 }}>High-light</div>
            <div>{Math.round((high?.lcc_ratio ?? 0) * 100)}%</div>
            <div>{(high?.components ?? 0).toFixed ? high.components.toFixed(1) : high?.components ?? 0}</div>
            <div>{Math.round((high?.fragmentation ?? 0) * 100)}%</div>

            <div style={{ color: "#7eb8f0", fontWeight: 600 }}>Random avg</div>
            <div>{Math.round((randomAvg?.lcc_ratio ?? 0) * 100)}%</div>
            <div>{(randomAvg?.components ?? 0).toFixed ? randomAvg.components.toFixed(1) : randomAvg?.components ?? 0}</div>
            <div>{Math.round((randomAvg?.fragmentation ?? 0) * 100)}%</div>
          </div>
        </div>

        <div style={{
          background: "#111827",
          border: "1px solid rgba(100,160,220,0.12)",
          borderRadius: 8,
          padding: 14,
          textAlign: "left",
        }}>
          <div style={{
            fontSize: 10, color: "#4a6888", fontFamily: "monospace",
            textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10,
          }}>
            Interpretation
          </div>

          <div style={{ fontSize: 12, color: "#8aa4c0", lineHeight: 1.7 }}>
            <div>
              • If <span style={{ color: "#f87171" }}>high-light removal</span> produces a lower LCC than random removal at the same removal level,
              illuminated stopover sites are disproportionately important.
            </div>
            <div>
              • Higher <span style={{ color: "#dbeafe" }}>components</span> and <span style={{ color: "#dbeafe" }}>fragmentation</span>
              mean the migration network is breaking into smaller disconnected pieces.
            </div>
            <div>
              • In the curve below, a steeper red decline means the network is more fragile when bright nodes are removed first.
            </div>
          </div>
        </div>
      </div>

      <ExperimentCurveChart result={fragilityCurveResult} />
    </div>
  );
}

function InfoCard({ species, month, activeView }) {
  const interpretations = {
    map: "Warmer circles show higher eBird observation density. The amber glow shows artificial light at night (ALAN), so you can compare migration activity with illuminated areas.",
    network: "Nodes represent stopover sites and edges represent inferred migration links. Larger nodes indicate higher activity, while wider edges indicate stronger migration flow.",
    stress: "Remove nodes manually from the full-year migration network to test how connectivity changes. Watch how LCC, components, and fragmentation respond as important stopover sites are removed.",
    experiments: "This view compares random removal against high-light removal on the full-year migration network. A sharper decline under high-light removal suggests stronger structural dependence on illuminated sites.",
  };

  return (
    <div style={{
      background: "#111827",
      border: "1px solid rgba(100,160,220,0.12)",
      borderRadius: 8,
      padding: "12px 16px",
      fontSize: 12,
      lineHeight: 1.6,
      flexShrink: 0,
      textAlign: "left",
    }}>
      <div style={{
        display: "flex", gap: 12, marginBottom: 6,
        fontFamily: "monospace", fontSize: 10, color: "#4a6888",
        textTransform: "uppercase", letterSpacing: "0.05em",
      }}>
        <span>Species: <span style={{ color: "#7eb8f0" }}>{species}</span></span>
        {(activeView === "map" || activeView === "experiments") && (
          <>
            {activeView === "map" && (
              <>
                <span>·</span>
                <span>Month: <span style={{ color: "#7eb8f0" }}>{month}</span></span>
              </>
            )}

            {activeView !== "map" && (
              <>
                <span>·</span>
                <span>Scope: <span style={{ color: "#7eb8f0" }}>Full year</span></span>
              </>
            )}
          </>
        )}

        {(activeView === "network" || activeView === "stress") && (
          <>
            <span>·</span>
            <span>Scope: <span style={{ color: "#7eb8f0" }}>Full year</span></span>
          </>
        )}
      </div>
      <p style={{ color: "#8aa4c0", margin: 0 }}>{interpretations[activeView]}</p>
    </div>
  );
}

// helper to get next month/year for route animation
function getNextMonthYear(monthName, year) {
  const idx = MONTHS.indexOf(monthName);
  if (idx === -1) return { nextMonth: monthName, nextYear: year };

  if (idx === MONTHS.length - 1) {
    return { nextMonth: MONTHS[0], nextYear: year + 1 };
  }

  return { nextMonth: MONTHS[idx + 1], nextYear: year };
}

function getClusterKey(lat, lng, binSize = CITY_BIN_DEGREES) {
  const latBucket = Math.round(lat / binSize) * binSize;
  const lngBucket = Math.round(lng / binSize) * binSize;
  return `${latBucket.toFixed(2)}_${lngBucket.toFixed(2)}`;
}

function getQuantile(values, q) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return d3.quantileSorted(sorted, q) ?? 0;
}

function buildCityLevelNetwork(sourceNodeRows, targetNodeRows, edgeRows) {
  const safeSourceRows = Array.isArray(sourceNodeRows) ? sourceNodeRows : [];
  const safeTargetRows = Array.isArray(targetNodeRows) ? targetNodeRows : [];
  const safeEdgeRows = Array.isArray(edgeRows) ? edgeRows : [];

  const rawNodes = [...safeSourceRows, ...safeTargetRows].map(r => ({
    id: r.node_id,
    lat: Number(r.lat),
    lng: Number(r.lon),
    grouped_count: Number(r.grouped_count) || 0,
    nightlight_mean: Number(r.nightlight_mean) || 0,
  }));

  if (rawNodes.length === 0) {
    return { nodes: [], edges: [] };
  }

  // 1. cluster ALL raw nodes first
  const clusterMap = new Map();
  const nodeToCluster = new Map();

  rawNodes.forEach(n => {
    const clusterId = getClusterKey(n.lat, n.lng);

    if (!clusterMap.has(clusterId)) {
      clusterMap.set(clusterId, {
        id: clusterId,
        weightedLatSum: 0,
        weightedLngSum: 0,
        totalActivity: 0,
        maxLight: 0,
        memberCount: 0,
      });
    }

    const cluster = clusterMap.get(clusterId);
    const weight = Math.max(n.grouped_count, 1);

    cluster.weightedLatSum += n.lat * weight;
    cluster.weightedLngSum += n.lng * weight;
    cluster.totalActivity += n.grouped_count;
    cluster.maxLight = Math.max(cluster.maxLight, n.nightlight_mean);
    cluster.memberCount += 1;

    nodeToCluster.set(n.id, clusterId);
  });

  let cityNodesRaw = Array.from(clusterMap.values()).map(c => ({
    id: c.id,
    lat: c.weightedLatSum / Math.max(c.totalActivity, 1),
    lng: c.weightedLngSum / Math.max(c.totalActivity, 1),
    totalActivity: c.totalActivity,
    maxLight: c.maxLight,
    memberCount: c.memberCount,
  }));

  if (cityNodesRaw.length === 0) {
    return { nodes: [], edges: [] };
  }

  // 2. aggregate ALL raw edges into city-to-city edges
  const cityEdgeMap = new Map();

  safeEdgeRows.forEach(r => {
    const sourceCluster = nodeToCluster.get(r.source_node_id);
    const targetCluster = nodeToCluster.get(r.target_node_id);

    if (!sourceCluster || !targetCluster) return;
    if (sourceCluster === targetCluster) return; // still remove within-city loops

    const key = `${sourceCluster}__${targetCluster}`;
    const weight = Number(r.weight) || 0;

    if (!cityEdgeMap.has(key)) {
      cityEdgeMap.set(key, {
        source: sourceCluster,
        target: targetCluster,
        totalWeight: 0,
        edgeCount: 0,
      });
    }

    const edge = cityEdgeMap.get(key);
    edge.totalWeight += weight;
    edge.edgeCount += 1;
  });

  let cityEdgesRaw = Array.from(cityEdgeMap.values());

  // 3. now apply filtering at the CITY level, not raw-node level
  const lightThreshold = getQuantile(
    cityNodesRaw.map(n => n.maxLight).filter(v => Number.isFinite(v)),
    LIGHT_QUANTILE
  );

  const allowedCityIds = new Set(
    cityNodesRaw
      .filter(
        n =>
          n.maxLight >= lightThreshold &&
          n.totalActivity >= MIN_CITY_ACTIVITY
      )
      .map(n => n.id)
  );

  cityNodesRaw = cityNodesRaw.filter(n => allowedCityIds.has(n.id));

  cityEdgesRaw = cityEdgesRaw.filter(
    e => allowedCityIds.has(e.source) && allowedCityIds.has(e.target)
  );

  // 4. remove cities with no remaining edges
  const connectedCityIds = new Set();
  cityEdgesRaw.forEach(e => {
    connectedCityIds.add(e.source);
    connectedCityIds.add(e.target);
  });

  cityNodesRaw = cityNodesRaw.filter(n => connectedCityIds.has(n.id));

  // 5. keep strongest city-to-city edges
  cityEdgesRaw = cityEdgesRaw
    .sort((a, b) => b.totalWeight - a.totalWeight)
    .slice(0, TOP_CITY_EDGES);

  if (cityNodesRaw.length === 0 || cityEdgesRaw.length === 0) {
    return { nodes: [], edges: [] };
  }

  const finalConnectedIds = new Set();
  cityEdgesRaw.forEach(e => {
    finalConnectedIds.add(e.source);
    finalConnectedIds.add(e.target);
  });

  cityNodesRaw = cityNodesRaw.filter(n => finalConnectedIds.has(n.id));

  const maxActivity = Math.max(...cityNodesRaw.map(n => n.totalActivity), 1);
  const maxLight = Math.max(...cityNodesRaw.map(n => n.maxLight), 1);
  const maxEdgeWeight = Math.max(...cityEdgesRaw.map(e => e.totalWeight), 1);

  const nodes = cityNodesRaw.map(n => ({
    id: n.id,
    lat: n.lat,
    lng: n.lng,
    activity: n.totalActivity / maxActivity,
    rawActivity: n.totalActivity,
    lightIntensity: n.maxLight / maxLight,
    rawLight: n.maxLight,
    fragility: 0.0,
  }));

  const edges = cityEdgesRaw.map(e => ({
    source: e.source,
    target: e.target,
    flow: e.totalWeight / maxEdgeWeight,
  }));

  return { nodes, edges };
}


function buildAdjacency(nodeIds, edges) {
  const adj = new Map();
  nodeIds.forEach(id => adj.set(id, new Set()));
  edges.forEach(e => {
    if (!adj.has(e.source) || !adj.has(e.target)) return;
    adj.get(e.source).add(e.target);
    adj.get(e.target).add(e.source); // weak connectivity
  });
  return adj;
}

function buildYearSeasonNetwork(monthNodeResults, monthEdgeResults) {
  const allNodeRows = monthNodeResults.flatMap(rows => Array.isArray(rows) ? rows : []);
  const allEdgeRows = monthEdgeResults.flatMap(rows => Array.isArray(rows) ? rows : []);

  if (allNodeRows.length === 0) {
    return { nodes: [], edges: [] };
  }

  // --- 1. cluster all monthly nodes into city buckets ---
  const clusterMap = new Map();
  const nodeToCluster = new Map();

  allNodeRows.forEach(r => {
    const lat = Number(r.lat);
    const lng = Number(r.lon);
    const groupedCount = Number(r.grouped_count) || 0;
    const nightlightMean = Number(r.nightlight_mean) || 0;

    const clusterId = getClusterKey(lat, lng);

    if (!clusterMap.has(clusterId)) {
      clusterMap.set(clusterId, {
        id: clusterId,
        weightedLatSum: 0,
        weightedLngSum: 0,
        totalActivity: 0,
        maxLight: 0,
        memberCount: 0,
      });
    }

    const cluster = clusterMap.get(clusterId);
    const weight = Math.max(groupedCount, 1);

    cluster.weightedLatSum += lat * weight;
    cluster.weightedLngSum += lng * weight;
    cluster.totalActivity += groupedCount;
    cluster.maxLight = Math.max(cluster.maxLight, nightlightMean);
    cluster.memberCount += 1;

    nodeToCluster.set(r.node_id, clusterId);
  });

  let cityNodesRaw = Array.from(clusterMap.values()).map(c => ({
    id: c.id,
    lat: c.weightedLatSum / Math.max(c.totalActivity, 1),
    lng: c.weightedLngSum / Math.max(c.totalActivity, 1),
    totalActivity: c.totalActivity,
    maxLight: c.maxLight,
    memberCount: c.memberCount,
  }));

  if (cityNodesRaw.length === 0) {
    return { nodes: [], edges: [] };
  }

  // --- 2. optional city-level filtering ---
  const lightThreshold = getQuantile(
    cityNodesRaw.map(n => n.maxLight).filter(v => Number.isFinite(v)),
    LIGHT_QUANTILE
  );

  const allowedCityIds = new Set(
    cityNodesRaw
      .filter(n => n.maxLight >= lightThreshold && n.totalActivity >= MIN_CITY_ACTIVITY)
      .map(n => n.id)
  );

  cityNodesRaw = cityNodesRaw.filter(n => allowedCityIds.has(n.id));

  // --- 3. aggregate edges by season + city pair ---
  const cityEdgeMap = new Map();

  allEdgeRows.forEach(e => {
    const sourceCluster = nodeToCluster.get(e.source_node_id);
    const targetCluster = nodeToCluster.get(e.target_node_id);

    if (!sourceCluster || !targetCluster) return;
    if (!allowedCityIds.has(sourceCluster) || !allowedCityIds.has(targetCluster)) return;
    if (sourceCluster === targetCluster) return;

    const season = seasonFromMonthNum(Number(e.source_month));
    const key = `${season}__${sourceCluster}__${targetCluster}`;
    const weight = Number(e.weight) || 0;

    if (!cityEdgeMap.has(key)) {
      cityEdgeMap.set(key, {
        source: sourceCluster,
        target: targetCluster,
        season,
        totalWeight: 0,
        edgeCount: 0,
      });
    }

    const edge = cityEdgeMap.get(key);
    edge.totalWeight += weight;
    edge.edgeCount += 1;
  });

  // keep more edges PER SEASON, not globally
  let cityEdgesRaw = [];
  SEASONS.forEach(({ key }) => {
    const seasonEdges = Array.from(cityEdgeMap.values())
      .filter(e => e.season === key)
      .sort((a, b) => b.totalWeight - a.totalWeight)
      .slice(0, TOP_CITY_EDGES);

    cityEdgesRaw.push(...seasonEdges);
  });

  if (cityEdgesRaw.length === 0) {
    return { nodes: [], edges: [] };
  }

  // --- 4. keep only nodes that still participate in edges ---
  const connectedIds = new Set();
  cityEdgesRaw.forEach(e => {
    connectedIds.add(e.source);
    connectedIds.add(e.target);
  });

  cityNodesRaw = cityNodesRaw.filter(n => connectedIds.has(n.id));

  const maxActivity = Math.max(...cityNodesRaw.map(n => n.totalActivity), 1);
  const maxLight = Math.max(...cityNodesRaw.map(n => n.maxLight), 1);
  const maxEdgeWeight = Math.max(...cityEdgesRaw.map(e => e.totalWeight), 1);

  const nodes = cityNodesRaw.map(n => ({
    id: n.id,
    lat: n.lat,
    lng: n.lng,
    activity: n.totalActivity / maxActivity,
    rawActivity: n.totalActivity,
    lightIntensity: n.maxLight / maxLight,
    rawLight: n.maxLight,
    fragility: 0.0,
  }));

  const edges = cityEdgesRaw.map(e => ({
    source: e.source,
    target: e.target,
    flow: e.totalWeight / maxEdgeWeight,
    season: e.season,
  }));

  return { nodes, edges };
}

function getWeaklyConnectedComponents(nodeIds, edges) {
  const adj = buildAdjacency(nodeIds, edges);
  const visited = new Set();
  const components = [];

  nodeIds.forEach(startId => {
    if (visited.has(startId)) return;
    const stack = [startId];
    const component = [];

    visited.add(startId);

    while (stack.length) {
      const current = stack.pop();
      component.push(current);

      for (const next of adj.get(current) || []) {
        if (!visited.has(next)) {
          visited.add(next);
          stack.push(next);
        }
      }
    }

    components.push(component);
  });

  components.sort((a, b) => b.length - a.length);
  return components;
}

function computeGraphMetrics(nodes, edges, removedIds = new Set()) {
  const activeNodes = nodes.filter(n => !removedIds.has(n.id));
  const activeNodeIds = new Set(activeNodes.map(n => n.id));

  const activeEdges = edges.filter(
    e => activeNodeIds.has(e.source) && activeNodeIds.has(e.target)
  );
  const lostEdges = edges.filter(
    e => removedIds.has(e.source) || removedIds.has(e.target)
  );

  if (nodes.length === 0) {
    return {
      activeNodes,
      activeEdges,
      lostEdges,
      components: [],
      componentSizes: [],
      lccSize: 0,
      lccRatio: 0,
      numberOfComponents: 0,
      fragmentationRate: 0,
      removedCount: 0,
      removedPct: 0,
      edgeSurvivalRate: 0,
    };
  }

  if (activeNodes.length === 0) {
    return {
      activeNodes,
      activeEdges,
      lostEdges,
      components: [],
      componentSizes: [],
      lccSize: 0,
      lccRatio: 0,
      numberOfComponents: 0,
      fragmentationRate: 1,
      removedCount: removedIds.size,
      removedPct: removedIds.size / nodes.length,
      edgeSurvivalRate: 0,
    };
  }

  const components = getWeaklyConnectedComponents(
    activeNodes.map(n => n.id),
    activeEdges
  );
  const componentSizes = components.map(c => c.length);
  const lccSize = componentSizes[0] || 0;
  const lccRatio = lccSize / nodes.length;
  const fragmentationRate = 1 - lccSize / nodes.length;
  const edgeSurvivalRate = edges.length ? activeEdges.length / edges.length : 0;

  return {
    activeNodes,
    activeEdges,
    lostEdges,
    components,
    componentSizes,
    lccSize,
    lccRatio,
    numberOfComponents: components.length,
    fragmentationRate,
    removedCount: removedIds.size,
    removedPct: removedIds.size / nodes.length,
    edgeSurvivalRate,
  };
}

function getTopLightRemovedIds(nodes, k) {
  return new Set(
    [...nodes]
      .sort((a, b) => (b.rawLight || 0) - (a.rawLight || 0))
      .slice(0, k)
      .map(n => n.id)
  );
}

function getRandomRemovedIds(nodes, k) {
  const shuffled = [...nodes];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }

  return new Set(shuffled.slice(0, k).map(n => n.id));
}

function computeFrontendRobustness(network, removalPct = 0.3, trials = 20) {
  const totalNodes = network.nodes.length;

  if (!totalNodes) {
    return {
      removal_pct: removalPct,
      removal_count: 0,
      trials,
      baseline: {
        lcc: 0,
        lcc_ratio: 0,
        components: 0,
        fragmentation: 1,
      },
      high_light: {
        lcc: 0,
        lcc_ratio: 0,
        components: 0,
        fragmentation: 1,
      },
      random_avg: {
        lcc: 0,
        lcc_ratio: 0,
        components: 0,
        fragmentation: 1,
      },
    };
  }

  const removalCount = Math.max(0, Math.min(totalNodes, Math.round(totalNodes * removalPct)));

  const baseline = computeGraphMetrics(network.nodes, network.edges, new Set());

  const highRemoved = getTopLightRemovedIds(network.nodes, removalCount);
  const highMetrics = computeGraphMetrics(network.nodes, network.edges, highRemoved);

  const randomTrials = [];
  for (let i = 0; i < trials; i++) {
    const randomRemoved = getRandomRemovedIds(network.nodes, removalCount);
    randomTrials.push(computeGraphMetrics(network.nodes, network.edges, randomRemoved));
  }

  const avg = key =>
    randomTrials.reduce((sum, r) => sum + r[key], 0) / Math.max(randomTrials.length, 1);

  return {
    removal_pct: removalPct,
    removal_count: removalCount,
    trials,
    baseline: {
      lcc: baseline.lccSize,
      lcc_ratio: baseline.lccRatio,
      components: baseline.numberOfComponents,
      fragmentation: baseline.fragmentationRate,
    },
    high_light: {
      lcc: highMetrics.lccSize,
      lcc_ratio: highMetrics.lccRatio,
      components: highMetrics.numberOfComponents,
      fragmentation: highMetrics.fragmentationRate,
    },
    random_avg: {
      lcc: avg("lccSize"),
      lcc_ratio: avg("lccRatio"),
      components: avg("numberOfComponents"),
      fragmentation: avg("fragmentationRate"),
    },
  };
}

function computeFrontendFragilityCurve(network, steps = 10, trials = 20) {
  const totalNodes = network.nodes.length;

  if (!totalNodes) {
    return { points: [] };
  }

  const points = [];

  for (let step = 0; step <= steps; step++) {
    const pctRemoved = step / steps;
    const removalCount = Math.max(0, Math.min(totalNodes, Math.round(totalNodes * pctRemoved)));

    const highRemoved = getTopLightRemovedIds(network.nodes, removalCount);
    const highMetrics = computeGraphMetrics(network.nodes, network.edges, highRemoved);

    const randomTrials = [];
    for (let i = 0; i < trials; i++) {
      const randomRemoved = getRandomRemovedIds(network.nodes, removalCount);
      randomTrials.push(computeGraphMetrics(network.nodes, network.edges, randomRemoved));
    }

    const avg = key =>
      randomTrials.reduce((sum, r) => sum + r[key], 0) / Math.max(randomTrials.length, 1);

    points.push({
      pct_removed: pctRemoved,
      light_based: {
        lcc_ratio: highMetrics.lccRatio,
        components: highMetrics.numberOfComponents,
        fragmentation: highMetrics.fragmentationRate,
      },
      random_avg: {
        lcc_ratio: avg("lccRatio"),
        components: avg("numberOfComponents"),
        fragmentation: avg("fragmentationRate"),
      },
    });
  }

  return { points };
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// MAIN APP
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export default function BirdMigrationMap() {
  const [activeView,      setActiveView]      = useState("map");
  const [selectedBird,    setSelectedBird]    = useState(BIRDS[0]);
  const [selectedYear,    setSelectedYear]    = useState(YEARS[0]);
  const [selectedMonth,  setSelectedMonth]  = useState(MONTHS[0]);
  const [showALAN,        setShowALAN]        = useState(false);
  const [alanOpacity,     setAlanOpacity]     = useState(0.6);
  const [activeSeasons,   setActiveSeasons]   = useState(() => new Set([SEASONS[0].key]));

  // Data state
  const [density,    setDensity]    = useState([]);
  const [nightlight, setNightlight] = useState([]);
  const [network, setNetwork] = useState({ nodes: [], edges: [] });
  const [loading,    setLoading]    = useState(false);
  const [apiError,   setApiError]   = useState(null);

  const [removalPct, setRemovalPct] = useState(0.3);
  const [experimentTrials, setExperimentTrials] = useState(20);
  const [curveSteps, setCurveSteps] = useState(10);
  const [robustnessResult, setRobustnessResult] = useState(null);
  const [fragilityCurveResult, setFragilityCurveResult] = useState(null);

  const geo   = useUSMap();

  // ── Fetch bird observations from your backend ─────────────────────────────
  useEffect(() => {
    setLoading(true);
    setApiError(null);

    API.observations(selectedBird.name, selectedYear, selectedMonth)
      .then(rows => {
        if (!Array.isArray(rows) || rows.length === 0) {
          setDensity([]);
          setLoading(false);
          return;
        }

        const maxCount = Math.max(...rows.map(r => r.observation_count));

        const densityPoints = rows.map(r => ({
          lat:        r.lat,
          lng:        r.lon,            // backend returns "lon", not "lng"
          density:    r.observation_count / maxCount,
          count:      r.observation_count,
          month:      r.month,
          name:       r.common_name,
          scientific: r.scientific_name,
        }));

        setDensity(densityPoints);
        setLoading(false);
      })
      .catch(err => {
        console.error("API error:", err);
        setApiError("Could not load observation data.");
        setLoading(false);
      });
  }, [selectedBird, selectedYear, selectedMonth]);

  useEffect(() => {
    API.nightlight(selectedYear)
      .then(rows => {
        if (!Array.isArray(rows)) {
          setNightlight([]);
          return;
        }

        const maxLight = Math.max(...rows.map(r => Number(r.nightlight_mean) || 0), 1);

        const points = rows.map(r => ({
          lat: r.lat,
          lng: r.lon,
          intensity: (Number(r.nightlight_mean) || 0) / maxLight,
          radius: 30,
        }));

        setNightlight(points);
      })
      .catch(err => {
        console.error("Nightlight API error:", err);
        setNightlight([]);
      });
  }, [selectedYear]);

  useEffect(() => {
    if (activeView !== "network") return;

    setLoading(true);
    setApiError(null);

    const nodePromises = MONTHS.map(month =>
      API.nodes(selectedBird.name, selectedYear, month)
    );

    const edgePromises = MONTHS.slice(0, 11).map(month =>
      API.edges(selectedBird.name, selectedYear, month, 1500, 0, 1, 10, 10)
    );

    Promise.all([Promise.all(nodePromises), Promise.all(edgePromises)])
      .then(([allNodeRowsByMonth, allEdgeRowsByMonth]) => {
        const fullYearNetwork = buildYearSeasonNetwork(allNodeRowsByMonth, allEdgeRowsByMonth);

        console.log("FULL YEAR NETWORK DATA", {
          selectedYear,
          nodeCount: fullYearNetwork.nodes.length,
          edgeCount: fullYearNetwork.edges.length,
        });

        setNetwork(fullYearNetwork);
        setLoading(false);
      })
      .catch(err => {
        console.error("Full-year network API error:", err);
        setNetwork({ nodes: [], edges: [] });
        setApiError("Could not load full-year network.");
        setLoading(false);
      });
  }, [activeView, selectedBird, selectedYear]);

  useEffect(() => {
    if (activeView !== "stress") return;

    setLoading(true);
    setApiError(null);

    const nodePromises = MONTHS.map(month =>
      API.nodes(selectedBird.name, selectedYear, month)
    );

    const edgePromises = MONTHS.slice(0, 11).map(month =>
      API.edges(selectedBird.name, selectedYear, month, 1500, 0, 1, 10, 10)
    );

    Promise.all([Promise.all(nodePromises), Promise.all(edgePromises)])
      .then(([allNodeRowsByMonth, allEdgeRowsByMonth]) => {
        const fullYearNetwork = buildYearSeasonNetwork(
          allNodeRowsByMonth,
          allEdgeRowsByMonth
        );

        console.log("FULL YEAR STRESS NETWORK DATA", {
          selectedYear,
          nodeCount: fullYearNetwork.nodes.length,
          edgeCount: fullYearNetwork.edges.length,
        });

        setNetwork(fullYearNetwork);
        setLoading(false);
      })
      .catch(err => {
        console.error("Full-year stress network API error:", err);
        setNetwork({ nodes: [], edges: [] });
        setApiError("Could not load full-year stress network.");
        setLoading(false);
      });
  }, [activeView, selectedBird, selectedYear]);

  useEffect(() => {
    if (activeView !== "experiments") return;

    setLoading(true);
    setApiError(null);

    const nodePromises = MONTHS.map(month =>
      API.nodes(selectedBird.name, selectedYear, month)
    );

    const edgePromises = MONTHS.slice(0, 11).map(month =>
      API.edges(selectedBird.name, selectedYear, month, 1500, 0, 1, 10, 10)
    );

    Promise.all([Promise.all(nodePromises), Promise.all(edgePromises)])
      .then(([allNodeRowsByMonth, allEdgeRowsByMonth]) => {
        const fullYearNetwork = buildYearSeasonNetwork(
          allNodeRowsByMonth,
          allEdgeRowsByMonth
        );

        console.log("FULL YEAR EXPERIMENT NETWORK DATA", {
          selectedYear,
          nodeCount: fullYearNetwork.nodes.length,
          edgeCount: fullYearNetwork.edges.length,
        });

        setNetwork(fullYearNetwork);

        const robustness = computeFrontendRobustness(
          fullYearNetwork,
          removalPct,
          experimentTrials
        );

        const fragilityCurve = computeFrontendFragilityCurve(
          fullYearNetwork,
          curveSteps,
          experimentTrials
        );

        setRobustnessResult(robustness);
        setFragilityCurveResult(fragilityCurve);

        setLoading(false);
      })
      .catch(err => {
        console.error("Full-year experiments error:", err);
        setNetwork({ nodes: [], edges: [] });
        setRobustnessResult(null);
        setFragilityCurveResult(null);
        setApiError("Could not load full-year experiment network.");
        setLoading(false);
      });
  }, [activeView, selectedBird, selectedYear, removalPct, experimentTrials, curveSteps]);

  const s = {
    app: {
      background: "#0a0e1a", minHeight: "100vh", color: "#c8d4e8",
      fontFamily: "'DM Sans','Segoe UI',sans-serif",
      display: "flex", flexDirection: "column",
    },
    header: {
      padding: "12px 20px", borderBottom: "1px solid rgba(100,160,220,0.12)",
      display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0,
    },
    title: {
      fontFamily: "monospace", fontSize: 13, fontWeight: 700,
      letterSpacing: "0.1em", color: "#7eb8f0", textTransform: "uppercase",
    },
    body:    { display: "flex", flex: 1, overflow: "hidden" },
    sidebar: {
      width: 240, flexShrink: 0,
      borderRight: "1px solid rgba(100,160,220,0.1)",
      padding: "16px 14px", display: "flex", flexDirection: "column",
      gap: 20, overflowY: "auto",
    },
    sideSection: { display: "flex", flexDirection: "column", gap: 8 },
    sideLabel: {
      fontSize: 10, fontFamily: "monospace", color: "#4a6888",
      textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 2,
    },
    select: {
      background: "#111827", border: "1px solid rgba(100,160,220,0.2)",
      color: "#c8d4e8", padding: "6px 8px", borderRadius: 6,
      fontSize: 12, cursor: "pointer", width: "100%",
    },
    toggle: {
      display: "flex", alignItems: "center", gap: 6,
      cursor: "pointer", fontSize: 12, color: "#8aa4c0", userSelect: "none",
    },
    checkbox: { accentColor: "#7eb8f0", cursor: "pointer" },
    rangeWrap: { display: "flex", alignItems: "center", gap: 6 },
    rangeVal:  { fontSize: 11, fontFamily: "monospace", color: "#7eb8f0", minWidth: 32 },
    main: {
      flex: 1, display: "flex", flexDirection: "column",
      padding: "12px 16px", gap: 10, overflow: "hidden",
    },
    tabBar: { display: "flex", gap: 6, flexShrink: 0 },
    tab: (active) => ({
      padding: "6px 16px", borderRadius: 6, fontSize: 12, cursor: "pointer",
      fontFamily: "monospace", letterSpacing: "0.04em", border: "1px solid",
      background:  active ? "rgba(126,184,240,0.15)" : "transparent",
      borderColor: active ? "rgba(126,184,240,0.5)"  : "rgba(100,160,220,0.15)",
      color:       active ? "#7eb8f0"                 : "#4a6888",
      transition: "all 0.15s",
    }),
    mapWrap: { flex: 1, position: "relative", minHeight: 0 },
    loadingOverlay: {
      position: "absolute", inset: 0,
      background: "rgba(10,14,26,0.75)",
      display: "flex", alignItems: "center", justifyContent: "center",
      borderRadius: 8, fontFamily: "monospace", fontSize: 13, color: "#3a5070",
    },
    errorBanner: {
      background: "rgba(248,113,113,0.1)",
      border: "1px solid rgba(248,113,113,0.3)",
      borderRadius: 6, padding: "6px 12px",
      fontSize: 11, fontFamily: "monospace", color: "#f87171",
    },
  };

  return (
    <div style={s.app}>

      {/* Header */}
      <div style={s.header}>
        <div>
          <div style={s.title}>Bird Migration × Artificial Light at Night</div>
          <div style={{ fontSize: 11, color: "#3a5070", fontFamily: "monospace", marginTop: 2 }}>
            eBird density · ALAN overlay · stopover network analysis
          </div>
        </div>
        <div style={{ fontFamily: "monospace", fontSize: 11, color: "#3a5070" }}>
          {activeView === "map"
            ? `${selectedMonth} · ${selectedBird.name} · ${selectedYear}`
            : `${selectedBird.name} · ${selectedYear}`}
          {/* {selectedMonth} · {selectedBird.name} · {selectedYear} */}
        </div>
      </div>

      <div style={s.body}>

        {/* ── Sidebar ── */}
        <div style={s.sidebar}>

          <div style={s.sideSection}>
            <div style={s.sideLabel}>Species</div>
            <select style={s.select} value={selectedBird.id}
              onChange={e => setSelectedBird(BIRDS.find(b => b.id === e.target.value))}>
              {BIRDS.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>

          {activeView === "map" ? (
            <div style={s.sideSection}>
              <div style={s.sideLabel}>Month</div>
              <select
                value={selectedMonth}
                onChange={e => setSelectedMonth(e.target.value)}
                style={s.select}
              >
                {MONTHS.map(m => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div style={s.sideSection}>
              <div style={s.sideLabel}>Network scope</div>
              <div style={{ fontSize: 12, color: "#5a7090", lineHeight: 1.5 }}>
                Full-year seasonal flow is shown for the selected year.
              </div>
            </div>
          )}

          {(activeView === "network" || activeView === "stress") && (
            <div style={s.sideSection}>
              <div style={s.sideLabel}>Seasons shown</div>
              {SEASONS.map(season => (
                <label key={season.key} style={s.toggle}>
                  <input
                    type="checkbox"
                    style={s.checkbox}
                    checked={activeSeasons.has(season.key)}
                    onChange={e => {
                      setActiveSeasons(prev => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(season.key);
                        else next.delete(season.key);
                        return next;
                      });
                    }}
                  />
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span style={{
                      width: 10, height: 10, borderRadius: 999,
                      background: season.color, display: "inline-block",
                    }} />
                    {season.label}
                  </span>
                </label>
              ))}
            </div>
          )}

          <div style={s.sideSection}>
            <div style={s.sideLabel}>Year</div>
            <select style={s.select} value={selectedYear}
              onChange={e => setSelectedYear(+e.target.value)}>
              {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>

          {activeView === "map" && <>
            <div style={s.sideSection}>
              <div style={s.sideLabel}>Map Layers</div>
              <label style={s.toggle}>
                <input type="checkbox" style={s.checkbox}
                  checked={showALAN} onChange={e => setShowALAN(e.target.checked)} />
                ALAN overlay
              </label>
            </div>

            <div style={s.sideSection}>
              <div style={s.sideLabel}>ALAN Opacity</div>
              <div style={s.rangeWrap}>
                <input type="range" min={0} max={1} step={0.05}
                  value={alanOpacity} style={{ flex: 1 }}
                  onChange={e => setAlanOpacity(+e.target.value)} />
                <span style={s.rangeVal}>{Math.round(alanOpacity * 100)}%</span>
              </div>
            </div>

          </>}

          {activeView === "experiments" && (
          <div style={s.sideSection}>
            <div style={s.sideLabel}>Removal Percentage</div>
            <input
              type="range"
              min={0.1}
              max={0.9}
              step={0.05}
              value={removalPct}
              onChange={(e) => setRemovalPct(parseFloat(e.target.value))}
              style={{ width: "100%" }}
            />
            <div style={{ fontSize: 12, color: "#8aa4c0", marginTop: 6 }}>
              {Math.round(removalPct * 100)}%
            </div>
          </div>
        )}

          <div
            style={{
              marginTop: "auto",
              paddingTop: 10,
              borderTop: "1px solid rgba(100,160,220,0.10)",
              fontSize: 11,
              color: "#5a7090",
              lineHeight: 1.5,
              textAlign: "left",
            }}
          >
            <div style={{ fontFamily: "monospace", fontSize: 10, color: "#4a6888", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
              Grid Resolution
            </div>
            H3 hex cells · ~1km²
          </div>

        </div>

        {/* ── Main Panel ── */}
        <div style={s.main}>

          {/* Tab bar */}
          <div style={s.tabBar}>
            {[
              { id: "map",     label: "Map View"     },
              { id: "network", label: "Network View" },
              { id: "stress",  label: "Stress Test"  },
              { id: "experiments", label: "Experiments" },
            ].map(v => (
              <button key={v.id} style={s.tab(activeView === v.id)}
                onClick={() => setActiveView(v.id)}>
                {v.label}
              </button>
            ))}
          </div>

          {/* Error banner */}
          {apiError && <div style={s.errorBanner}>{apiError}</div>}

          {/* Visualization */}
          <div style={s.mapWrap}>
            {activeView === "map" && (
              <MapView
                geo={geo}
                density={density}
                nightlight={nightlight}
                showALAN={showALAN}
                alanOpacity={alanOpacity}
                selectedBird={selectedBird}
              />
            )}
            {activeView === "network" && (
              <NetworkView geo={geo} network={network} activeSeasons={activeSeasons} />
            )}
            {activeView === "stress" && (
              <StressTestView geo={geo} network={network} activeSeasons={activeSeasons} />
            )}
            {activeView === "experiments" && (
              <ExperimentsView
                robustnessResult={robustnessResult}
                fragilityCurveResult={fragilityCurveResult}
              />
            )}
          
            {loading && (
              <div style={s.loadingOverlay}>
                {activeView === "map"
                  ? "Loading observations…"
                  : activeView === "experiments"
                  ? "Running experiments…"
                  : "Loading full-year network…"}
              </div>
            )}
          </div>

          {/* Info card */}
          <InfoCard
            species={selectedBird.name}
            month={selectedMonth}
            activeView={activeView}
          />

        </div>
      </div>
    </div>
  );
}