"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  capture: null,
  reference: null,
  result: null,
  history: [],
  metric: false,
  view: "flight",
  busy: false,
  progress: 1,
  animation: 0,
};
const fmt = (value, digits = 1) =>
  Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
const distance = (m) => m * (state.metric ? 1 : 1.0936133);
const speed = (m) => m * (state.metric ? 1 : 2.2369363);
const unit = () => (state.metric ? "m" : "yd");
const speedUnit = () => (state.metric ? "m/s" : "mph");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const text = (id, value) => {
  $(id).textContent = value;
};
function message(value, error = false) {
  $("message").className = value
    ? error
      ? "error-message"
      : "busy-message"
    : "";
  text("message", value);
}
async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed");
  return body;
}
function download(name, object) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(object, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function setBusy(busy) {
  state.busy = busy;
  for (const node of document.querySelectorAll(
    ".fixture,#run,#import,#wind,#ground",
  ))
    node.disabled = busy;
  text("run", busy ? "Processing capture…" : "Run capture ↗");
}
function addCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.appendChild(cell);
  return cell;
}
function showInput() {
  const packet = state.capture;
  text("capture-title", packet.label || packet.shot_id || "Imported capture");
  const obs = Array.isArray(packet.observations) ? packet.observations : [];
  text(
    "capture-detail",
    `${obs.length} optical timestamps · two calibrated views · post-CV observations`,
  );
  text(
    "source",
    packet.source === "synthetic"
      ? "SIMULATED SENSOR DATA"
      : "IMPORTED SENSOR DATA",
  );
  text(
    "provenance",
    packet.source === "synthetic"
      ? "Reproducible inputs. Calculated launch metrics. Estimated flight."
      : "Source and timing are supplied by the file; physical provenance is not independently verified.",
  );
  text("raw", JSON.stringify(packet, null, 2));
  $("observations").replaceChildren();
  obs.slice(0, 16).forEach((o, i) => {
    const row = document.createElement("tr");
    addCell(row, String(i + 1));
    addCell(row, String(o.timestamp_us));
    for (let cam = 0; cam < 2; cam++) {
      const c = o.views?.[cam]?.center_camera_m;
      addCell(
        row,
        Array.isArray(c) ? c.map((v) => fmt(v, 5)).join(", ") : "Missing",
      );
    }
    addCell(row, (o.views || []).map((v) => v.matched_dots ?? "—").join(" / "));
    $("observations").appendChild(row);
  });
}
function resetResult() {
  cancelAnimationFrame(state.animation);
  state.result = null;
  state.progress = 1;
  render();
  for (const li of $("stages").children) li.className = "";
  message("");
  text("quality-status", "AWAITING CAPTURE");
  text(
    "quality-detail",
    "Measurements are released only after the input passes the quality checks.",
  );
  text("elapsed", "Presentation replay runs at a readable pace.");
  text("reference", "");
  text("assumptions", "Run a capture to inspect the exact model inputs.");
}
async function selectFixture(id) {
  if (state.busy) return;
  try {
    const data = await api(`/api/fixtures/${encodeURIComponent(id)}`);
    state.capture = data.capture;
    state.reference = data.reference;
    document
      .querySelectorAll(".fixture")
      .forEach((b) => b.classList.toggle("active", b.dataset.id === id));
    resetResult();
    showInput();
    $("run").disabled = false;
  } catch (error) {
    message(error.message, true);
  }
}
function getConditions() {
  const surfaces = {
    standard: { landing_speed_fraction: 0.22, roll_deceleration_m_s2: 2.5 },
    soft: { landing_speed_fraction: 0.12, roll_deceleration_m_s2: 3.5 },
    firm: { landing_speed_fraction: 0.3, roll_deceleration_m_s2: 2 },
  };
  return {
    wind_x_m_s: Number($("wind").value),
    ...surfaces[$("ground").value],
  };
}
async function run() {
  if (state.busy || !state.capture) return;
  resetResult();
  setBusy(true);
  message("Processing the supplied observations locally…");
  const request = api("/api/replay", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      capture: state.capture,
      conditions: getConditions(),
    }),
  });
  // Attach rejection handling immediately while the readable presentation transition runs.
  const settled = request.then(
    (value) => ({ value }),
    (error) => ({ error }),
  );
  try {
    await sleep(250);
    const response = await settled;
    if (response.error) throw response.error;
    const result = response.value;
    for (let i = 0; i < result.stages.length; i++) {
      $("stages").children[i].className = "done";
      await sleep(180);
    }
    if (result.status !== "accepted") {
      $("stages").children[Math.min(result.stages.length, 3)].className =
        "failed";
    }
    state.result = result;
    state.history.unshift({ result, conditions: getConditions() });
    state.history = state.history.slice(0, 50);
    render();
    renderHistory();
    text(
      "elapsed",
      `Calculation time: ${fmt(result.processing_ms, 0)} ms · animation slowed for presentation`,
    );
    if (result.status === "accepted") {
      message("");
      text("quality-status", "CAPTURE ACCEPTED");
      text(
        "quality-detail",
        "Both cameras agree. Launch and spin calculated from the observed positions and rotations.",
      );
      animate();
    } else {
      message(result.reasons.join(" "), true);
      text("quality-status", "CAPTURE REJECTED");
      text(
        "quality-detail",
        "No launch, spin or distance values released for this capture.",
      );
    }
  } catch (error) {
    message(error.message, true);
    text("quality-status", "INPUT ERROR");
    text(
      "quality-detail",
      "Check the sensor JSON format before running this capture again.",
    );
  } finally {
    setBusy(false);
  }
}
function render() {
  const r = state.result,
    valid = r?.status === "accepted",
    f = valid ? r.flight : null;
  text("units", state.metric ? "Metres · m/s" : "Yards · mph");
  text("speed-unit", speedUnit());
  document
    .querySelectorAll(".distance-unit")
    .forEach((n) => (n.textContent = unit()));
  text("speed", valid ? fmt(speed(r.launch.speed_m_s)) : "—");
  text("launch", valid ? fmt(r.launch.launch_angle_deg) : "—");
  text("spin", valid ? fmt(r.spin.rpm, 0) : "—");
  text("carry", f ? fmt(distance(f.carry_m), 0) : "—");
  text("total", f ? fmt(distance(f.total_m), 0) : "—");
  text(
    "direction",
    valid
      ? `Direction ${fmt(Math.abs(r.launch.launch_direction_deg))}° ${r.launch.launch_direction_deg >= 0 ? "left" : "right"}`
      : "Direction —",
  );
  const axis = valid ? r.spin.axis_world : null;
  text(
    "axis",
    axis
      ? `Axis [${axis.map((v) => fmt(v, 2)).join(", ")}]`
      : "3D orientation / time",
  );
  text(
    "range",
    f
      ? `Sensitivity ${f.carry_sensitivity_m.map((v) => fmt(distance(v), 0)).join("–")} ${unit()}`
      : "Model sensitivity shown after run",
  );
  text(
    "roll",
    f
      ? `+${fmt(distance(f.roll_m))} ${unit()} assumed rollout`
      : "Includes assumed rollout",
  );
  text("apex", f ? `${fmt(distance(f.apex_m))} ${unit()}` : "—");
  text("flight-time", f ? `${fmt(f.flight_time_s, 2)} s` : "—");
  text(
    "offline",
    f
      ? `${fmt(distance(Math.abs(f.offline_m)))} ${unit()} ${f.offline_m >= 0 ? "left" : "right"}`
      : "—",
  );
  text(
    "flight-title",
    valid ? "The shot, brought to life." : "A view of what comes next.",
  );
  $("canvas-empty").hidden = !!f;
  const q = r?.quality;
  text(
    "stereo",
    q?.max_stereo_error_mm != null
      ? `${fmt(q.max_stereo_error_mm, 3)} mm`
      : "—",
  );
  text("residual", valid ? `${fmt(q.position_fit_rms_mm, 3)} mm` : "—");
  text("dots", valid ? String(q.matched_dots_min) : "—");
  text(
    "timing",
    valid
      ? `${q.pulse_spacing_us.map((v) => fmt(v, 0)).join(" / ")} µs · ±${fmt(q.timing_uncertainty_us, 0)} µs`
      : "—",
  );
  $("animate").disabled = !valid;
  $("export").disabled = !r;
  if (f)
    text(
      "assumptions",
      JSON.stringify(
        {
          model: f.model,
          validation: f.validation,
          ...f.conditions,
          sensitivity: f.sensitivity_note,
        },
        null,
        2,
      ),
    );
  if (valid && state.reference) {
    const truth = state.reference;
    text(
      "reference",
      `Independent synthetic truth: ${fmt(truth.speed_m_s, 2)} m/s · ${fmt(truth.launch_angle_deg, 2)}° · ${fmt(truth.spin_rpm, 0)} RPM. Recovered errors: speed ${fmt(Math.abs(r.launch.speed_m_s - truth.speed_m_s), 3)} m/s, launch ${fmt(Math.abs(r.launch.launch_angle_deg - truth.launch_angle_deg), 3)}°, spin ${fmt(Math.abs(r.spin.rpm - truth.spin_rpm), 1)} RPM. Truth values are not passed into the solver.`,
    );
  }
  draw();
}
function renderHistory() {
  const body = $("history");
  body.replaceChildren();
  if (!state.history.length) {
    const row = document.createElement("tr");
    const c = addCell(
      row,
      "Your runs will appear here. Session history stays in this tab.",
    );
    c.colSpan = 6;
    c.className = "empty";
    body.appendChild(row);
    return;
  }
  state.history.forEach(({ result: r }) => {
    const row = document.createElement("tr");
    addCell(row, r.label);
    const c = addCell(row, "");
    const badge = document.createElement("span");
    badge.className = `status-tag ${r.status}`;
    badge.textContent = r.status.toUpperCase();
    c.appendChild(badge);
    const good = r.status === "accepted";
    addCell(
      row,
      good ? `${fmt(speed(r.launch.speed_m_s))} ${speedUnit()}` : "—",
    );
    addCell(row, good ? `${fmt(r.spin.rpm, 0)} rpm` : "—");
    addCell(row, good ? `${fmt(distance(r.flight.carry_m))} ${unit()}` : "—");
    addCell(row, good ? `${fmt(distance(r.flight.total_m))} ${unit()}` : "—");
    body.appendChild(row);
  });
}
function animate() {
  cancelAnimationFrame(state.animation);
  if (!state.result?.flight) return;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    state.progress = 1;
    draw();
    return;
  }
  const start = performance.now();
  function step(now) {
    state.progress = Math.min((now - start) / 4200, 1);
    draw();
    if (state.progress < 1) state.animation = requestAnimationFrame(step);
  }
  state.animation = requestAnimationFrame(step);
}
function draw() {
  const canvas = $("flight"),
    bounds = canvas.getBoundingClientRect(),
    dpr = window.devicePixelRatio || 1,
    w = bounds.width,
    h = bounds.height;
  if (!w || !h) return;
  if (
    canvas.width !== Math.round(w * dpr) ||
    canvas.height !== Math.round(h * dpr)
  ) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const f = state.result?.flight,
    maxX = f ? Math.max(80, f.total_m * 1.15) : 260;
  const maxY = f ? Math.max(32, Math.abs(f.offline_m) * 1.7) : 40;
  const maxZ = f ? Math.max(25, f.apex_m * 1.2) : 40;
  const project = ([x, y, z]) =>
    state.view === "overhead"
      ? [w * 0.09 + (x / maxX) * w * 0.82, h * 0.5 - (y / maxY) * h * 0.34]
      : [
          w * 0.09 + (x / maxX) * w * 0.79 - (y / maxY) * w * 0.055,
          h * 0.83 -
            (x / maxX) * h * 0.22 -
            (y / maxY) * h * 0.13 -
            (z / maxZ) * h * 0.45,
        ];
  const line = (a, b, color, width = 1, dash = []) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(...project(a));
    ctx.lineTo(...project(b));
    ctx.stroke();
    ctx.setLineDash([]);
  };
  const polygon = (points) => {
    ctx.beginPath();
    points.forEach((p, i) => {
      const a = project(p);
      i ? ctx.lineTo(...a) : ctx.moveTo(...a);
    });
    ctx.closePath();
  };
  for (let i = 0; i < 10; i++) {
    polygon([
      [(maxX * i) / 10, -maxY, 0],
      [(maxX * (i + 1)) / 10, -maxY, 0],
      [(maxX * (i + 1)) / 10, maxY, 0],
      [(maxX * i) / 10, maxY, 0],
    ]);
    ctx.fillStyle = i % 2 ? "#204c3e" : "#234f40";
    ctx.fill();
  }
  line([0, 0, 0], [maxX, 0, 0], "#63816a", 1, [4, 5]);
  const step = state.metric ? 50 : 45.72;
  ctx.font = "9px Segoe UI";
  ctx.textAlign = "center";
  for (let x = step; x < maxX; x += step) {
    line([x, -maxY, 0], [x, maxY, 0], "#42634e", 0.6);
    const [px, py] = project([x, -maxY, 0]);
    ctx.fillStyle = "#8fa587";
    ctx.fillText(`${fmt(distance(x), 0)} ${unit()}`, px, py + 17);
  }
  if (!f) return;
  const trajectory = f.trajectory;
  ctx.setLineDash([3, 5]);
  ctx.strokeStyle = "#5a7960";
  ctx.lineWidth = 1;
  ctx.beginPath();
  trajectory.forEach((sample, i) => {
    const p = project([sample.position_m[0], sample.position_m[1], 0]);
    i ? ctx.lineTo(...p) : ctx.moveTo(...p);
  });
  ctx.stroke();
  ctx.setLineDash([]);
  const end = Math.max(1, Math.floor(state.progress * (trajectory.length - 1)));
  ctx.strokeStyle = "#efa36e";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  trajectory.slice(0, end + 1).forEach((sample, i) => {
    const p = project(sample.position_m);
    i ? ctx.lineTo(...p) : ctx.moveTo(...p);
  });
  ctx.stroke();
  const position = project(trajectory[end].position_m);
  ctx.shadowColor = "#f5d2ab";
  ctx.shadowBlur = 14;
  ctx.fillStyle = "#fff5dd";
  ctx.beginPath();
  ctx.arc(...position, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  const tee = project([0, 0, 0]);
  ctx.fillStyle = "#c4d2b8";
  ctx.beginPath();
  ctx.arc(...tee, 3, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = "9px Segoe UI";
  ctx.fillText("LAUNCH", tee[0], tee[1] + 20);
  if (state.progress === 1) {
    const landing = project(f.landing_world_m);
    ctx.strokeStyle = "#efa36e";
    ctx.beginPath();
    ctx.ellipse(landing[0], landing[1], 8, 4, 0, 0, Math.PI * 2);
    ctx.stroke();
    line(f.landing_world_m, f.rest_world_m, "#a7ba92", 2, [3, 4]);
    const rest = project(f.rest_world_m);
    ctx.fillStyle = "#e7b88c";
    ctx.textAlign = "right";
    ctx.font = "11px Segoe UI";
    ctx.fillText(
      `${fmt(distance(f.carry_m), 0)} ${unit()} carry`,
      landing[0] - 7,
      landing[1] - 16,
    );
    ctx.fillStyle = "#bed0ac";
    ctx.beginPath();
    ctx.arc(...rest, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
}
$("run").addEventListener("click", run);
$("units").addEventListener("click", () => {
  state.metric = !state.metric;
  render();
  renderHistory();
});
$("animate").addEventListener("click", animate);
for (const [id, view] of [
  ["perspective", "flight"],
  ["overhead", "overhead"],
])
  $(id).addEventListener("click", () => {
    state.view = view;
    for (const key of ["perspective", "overhead"]) {
      $(key).classList.toggle("selected", key === id);
      $(key).setAttribute("aria-pressed", String(key === id));
    }
    draw();
  });
$("wind").addEventListener("input", () => {
  const n = Number($("wind").value);
  text(
    "wind-value",
    `${Math.abs(n)} m/s · ${n === 0 ? "calm" : n < 0 ? "headwind" : "tailwind"}`,
  );
});
$("export").addEventListener("click", () => {
  if (state.result)
    download("fairway-fusion-result.json", {
      capture: state.capture,
      result: state.result,
      synthetic_reference: state.reference,
    });
});
$("download-input").addEventListener("click", () => {
  if (state.capture)
    download("fairway-fusion-sensor-input.json", state.capture);
});
$("clear").addEventListener("click", () => {
  state.history = [];
  renderHistory();
});
$("import").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", async () => {
  const file = $("file").files[0];
  if (!file) return;
  try {
    if (file.size > 1000000)
      throw new Error("Sensor JSON must be smaller than 1 MB.");
    const data = JSON.parse(await file.text());
    const capture = data.capture || data;
    if (
      !capture ||
      capture.schema_version !== 1 ||
      !Array.isArray(capture.observations)
    )
      throw new Error(
        "Expected schema_version 1 with an observations array. Download a sample input for the format.",
      );
    state.capture = capture;
    state.reference = null;
    document
      .querySelectorAll(".fixture")
      .forEach((b) => b.classList.remove("active"));
    resetResult();
    showInput();
    $("run").disabled = false;
  } catch (error) {
    message(error.message, true);
  } finally {
    $("file").value = "";
  }
});
new ResizeObserver(draw).observe($("flight"));
async function init() {
  try {
    const fixtures = await api("/api/fixtures");
    for (const fixture of fixtures) {
      const button = document.createElement("button");
      button.className = "fixture";
      button.dataset.id = fixture.id;
      const [name, note] = fixture.label.split(" · ");
      const strong = document.createElement("strong");
      strong.textContent = name;
      const small = document.createElement("small");
      small.textContent = note;
      button.append(strong, small);
      button.addEventListener("click", () => selectFixture(fixture.id));
      $("fixtures").appendChild(button);
    }
    await selectFixture(fixtures[0].id);
  } catch (error) {
    message(`Unable to load the local demo: ${error.message}`, true);
  }
}
init();
