const loginView = document.querySelector("#loginView");
const dashboardView = document.querySelector("#dashboardView");
const loginForm = document.querySelector("#loginForm");
const loginMessage = document.querySelector("#loginMessage");
const loginSetupNote = document.querySelector("#loginSetupNote");
const logoutButton = document.querySelector("#logoutButton");
const chatMessages = document.querySelector("#chatMessages");
const chatForm = document.querySelector("#chatForm");
const chatQuestion = document.querySelector("#chatQuestion");
const sendQuestion = document.querySelector("#sendQuestion");
const districtPicker = document.querySelector("#districtPicker");
const selectedDistrict = { value: "" };
const chatHistory = [];
let currentRole = "collector";
let paSiteAvailable = false;
let editingTaskId = null;
let mediaRecorder = null;
let recordedChunks = [];
let mapFeatures = [];
let districtCounts = new Map();
let districtCategoryCounts = new Map();

const INCIDENT_CATEGORY_STYLES = [
  { categories: ["flood_rain"], label: "Flood / Waterlogging", color: "#2196F3" },
  { categories: ["fire"], label: "Fire incidents", color: "#F44336" },
  { categories: ["road_accident"], label: "Road accidents", color: "#FF9800" },
  { categories: ["health"], label: "Medical emergencies / Disease outbreaks", color: "#9C27B0" },
  { categories: ["landslide"], label: "Landslides / Soil erosion", color: "#795548" },
  { categories: ["water_supply"], label: "Drought / Water scarcity", color: "#FFEB3B" },
  { categories: ["infrastructure"], label: "Infrastructure damage", color: "#607D8B" },
  { categories: ["crime_law", "protest"], label: "Law and order / Public safety", color: "#E91E63" },
  { categories: ["agriculture"], label: "Agricultural issues / Crop damage", color: "#4CAF50" },
  { categories: ["cyclone"], label: "Cyclone / Severe weather impact", color: "#00BCD4" },
  { categories: ["other"], label: "Other incidents", color: "#9E9E9E" },
];
const INCIDENT_CATEGORY_STYLE_BY_ID = new Map(
  INCIDENT_CATEGORY_STYLES.flatMap((style) =>
    style.categories.map((category) => [category, style]),
  ),
);

function applyCategoryColor(element, category) {
  const style = INCIDENT_CATEGORY_STYLE_BY_ID.get(category) ||
    INCIDENT_CATEGORY_STYLE_BY_ID.get("other");
  element.style.setProperty("--category-color", style.color);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) throw new Error(`Request failed (${response.status}).`);
  }
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(payload.error || `Request failed (${response.status}).`);
  }
  return payload;
}

function showLogin() {
  loginView.hidden = false;
  dashboardView.hidden = true;
  logoutButton.hidden = true;
}

function showDashboard() {
  loginView.hidden = true;
  dashboardView.hidden = false;
  logoutButton.hidden = false;
  document.querySelector("#currentDate").textContent = new Intl.DateTimeFormat("en-IN", {
    dateStyle: "long",
    timeZone: "Asia/Kolkata",
  }).format(new Date());
}

async function showSite(site) {
  const canOpenPaSite = currentRole === "pa";
  if (site === "pa" && !canOpenPaSite) {
    setNotice(
      document.querySelector("#sidebarAccessNote"),
      paSiteAvailable
        ? "Sign out and use the PA account to open the PA site."
        : "Configure PA_USERNAME and PA_PASSWORD in .env to enable the PA site.",
    );
    return;
  }
  const paSelected = site === "pa";
  document.querySelector("#collectorWorkspace").hidden = paSelected;
  document.querySelector("#paWorkspace").hidden = !paSelected;
  document.querySelector("#collectorSiteButton").classList.toggle("is-active", !paSelected);
  document.querySelector("#paSiteButton").classList.toggle("is-active", paSelected);
  document.querySelector("#collectorSiteButton").setAttribute("aria-current", String(!paSelected));
  document.querySelector("#paSiteButton").setAttribute("aria-current", String(paSelected));
  setNotice(document.querySelector("#sidebarAccessNote"), "", false);
  const taskLoad = loadTasks();
  if (!paSelected) await Promise.all([taskLoad, loadCollectorWorkspace()]);
  else await taskLoad;
}

function configureSiteNavigation(session) {
  currentRole = session.role || "collector";
  paSiteAvailable = Boolean(session.pa_site_available);
  const canOpenPaSite = currentRole === "pa";
  document.querySelector("#signedInRole").textContent =
    canOpenPaSite ? "Personal Assistant (PA)" : "Collector";
  document.querySelector("#paSiteButton").disabled = !canOpenPaSite;
  document.querySelector("#paSiteLock").hidden = canOpenPaSite;
  document.querySelector("#paSiteDescription").textContent = canOpenPaSite
    ? "Manage the Collector's weekly tasks"
    : paSiteAvailable ? "Sign in with the PA account" : "PA account not configured";
}

function setNotice(element, message, visible = true) {
  element.textContent = message;
  element.hidden = !visible;
}

function displayNumber(value) {
  return value === null || value === undefined
    ? "Data unavailable"
    : new Intl.NumberFormat("en-IN").format(value);
}

function formatIst(value, options = {}) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: options.dateStyle || "medium",
    ...(options.timeStyle ? { timeStyle: options.timeStyle } : {}),
    timeZone: "Asia/Kolkata",
  }).format(date);
}

function formatTaskDate(value, short = false) {
  const date = new Date(`${value}T12:00:00+05:30`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", short
    ? { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" }
    : { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Kolkata" }
  ).format(date);
}

function taskRow(task, options = {}) {
  const row = document.createElement(options.pa ? "div" : "label");
  row.className = `today-task-row${task.is_complete ? " is-complete" : ""}`;
  if (options.pa) row.className = "pa-task-row";
  const copy = document.createElement("span");
  copy.className = options.pa ? "pa-task-copy" : "task-row-text";
  const text = document.createElement("span");
  text.className = "task-row-text";
  text.textContent = task.task_text;
  copy.append(text);

  if (options.pa) {
    const status = document.createElement("span");
    status.className = `pa-task-status${task.is_complete ? " is-complete" : ""}`;
    status.textContent = task.is_complete ? "Completed by Collector" : "Pending";
    copy.append(status);
    const actions = document.createElement("span");
    actions.className = "pa-task-actions";
    const edit = document.createElement("button");
    edit.className = "task-action-button";
    edit.type = "button";
    edit.textContent = "Edit";
    edit.setAttribute("aria-label", `Edit task: ${task.task_text}`);
    edit.addEventListener("click", () => beginTaskEdit(task));
    const remove = document.createElement("button");
    remove.className = "task-action-button is-delete";
    remove.type = "button";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete task: ${task.task_text}`);
    remove.addEventListener("click", () => deleteTask(task));
    actions.append(edit, remove);
    row.append(copy, actions);
    return row;
  }

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = task.is_complete;
  checkbox.setAttribute("aria-label", `Mark task complete: ${task.task_text}`);
  checkbox.addEventListener("change", () => setTaskCompletion(task, checkbox.checked));
  row.append(checkbox, copy);
  return row;
}

function renderTodayTasks(tasks, today) {
  document.querySelector("#todayTaskDate").textContent = formatTaskDate(today);
  const todaysTasks = tasks.filter((task) => task.task_date === today);
  const completed = todaysTasks.filter((task) => task.is_complete).length;
  document.querySelector("#todayTaskCount").textContent =
    todaysTasks.length ? `${completed}/${todaysTasks.length} complete` : "0 tasks today";
  const list = document.querySelector("#todayTaskList");
  list.replaceChildren();
  if (!todaysTasks.length) {
    list.append(makeText("No tasks assigned for today.", "today-task-empty"));
    return;
  }
  for (const task of todaysTasks) list.append(taskRow(task));
}

function renderCollectorWeek(tasks, today) {
  const upcoming = tasks.filter((task) => task.task_date > today);
  document.querySelector("#weekTaskRange").textContent =
    `${formatTaskDate(today, true)} – ${formatTaskDate(document.querySelector("#taskDate").max || today, true)}`;
  const container = document.querySelector("#collectorWeekTaskList");
  container.replaceChildren();
  if (!upcoming.length) {
    container.append(makeText("No upcoming tasks assigned for this week.", "week-task-empty"));
    return;
  }
  const byDate = groupTasksByDate(upcoming);
  for (const [taskDate, dayTasks] of byDate) {
    const day = document.createElement("section");
    day.className = "week-task-day";
    const heading = document.createElement("div");
    heading.className = "week-task-day-heading";
    heading.append(
      makeText(formatTaskDate(taskDate, true), "week-task-day-name"),
      makeText(`${dayTasks.length} task${dayTasks.length === 1 ? "" : "s"}`, "week-task-day-count"),
    );
    day.append(heading);
    for (const task of dayTasks) {
      const item = makeText(task.task_text, "task-row-text");
      if (task.is_complete) item.classList.add("is-complete");
      day.append(item);
    }
    container.append(day);
  }
}

function groupTasksByDate(tasks) {
  const dates = new Map();
  for (const task of tasks) {
    if (!dates.has(task.task_date)) dates.set(task.task_date, []);
    dates.get(task.task_date).push(task);
  }
  return [...dates.entries()].sort(([left], [right]) => left.localeCompare(right));
}

function renderPaTasks(tasks, today, weekEnd) {
  const board = document.querySelector("#paTaskBoard");
  board.replaceChildren();
  document.querySelector("#paWeekTaskCount").textContent =
    `${tasks.length} task${tasks.length === 1 ? "" : "s"} assigned`;
  document.querySelector("#paTaskWindow").textContent =
    `${formatTaskDate(today, true)} – ${formatTaskDate(weekEnd, true)}`;
  const grouped = new Map(groupTasksByDate(tasks));
  const end = new Date(`${weekEnd}T00:00:00Z`);
  for (const cursor = new Date(`${today}T00:00:00Z`); cursor <= end; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    const taskDate = cursor.toISOString().slice(0, 10);
    const dayTasks = grouped.get(taskDate) || [];
    const day = document.createElement("section");
    day.className = "pa-task-day";
    const heading = document.createElement("div");
    heading.className = "pa-task-day-heading";
    heading.append(
      makeText(formatTaskDate(taskDate), "pa-task-date"),
      makeText(taskDate === today ? "Today" : `${dayTasks.length} task${dayTasks.length === 1 ? "" : "s"}`, "pa-day-count"),
    );
    const list = document.createElement("div");
    list.className = "pa-task-day-list";
    if (!dayTasks.length) {
      list.append(makeText("No tasks scheduled.", "pa-task-empty"));
    } else {
      for (const task of dayTasks) list.append(taskRow(task, { pa: true }));
    }
    day.append(heading, list);
    board.append(day);
  }
}

async function loadTasks() {
  try {
    const data = await api("/api/tasks");
    const dateInput = document.querySelector("#taskDate");
    dateInput.min = data.today;
    dateInput.max = data.week_end;
    if (!dateInput.value || dateInput.value < data.today || dateInput.value > data.week_end) {
      dateInput.value = data.today;
    }
    renderTodayTasks(data.tasks || [], data.today);
    renderCollectorWeek(data.tasks || [], data.today);
    renderPaTasks(data.tasks || [], data.today, data.week_end);
    setNotice(document.querySelector("#taskPageError"), "", false);
  } catch (error) {
    document.querySelector("#todayTaskList").replaceChildren(
      makeText(`Tasks could not be loaded: ${error.message}`, "today-task-empty"),
    );
    document.querySelector("#paTaskBoard").replaceChildren(
      makeText(`Tasks could not be loaded: ${error.message}`, "empty-state"),
    );
  }
}

async function setTaskCompletion(task, isComplete) {
  try {
    await api(`/api/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_complete: isComplete }),
    });
    await loadTasks();
  } catch (error) {
    setNotice(document.querySelector("#taskPageError"), error.message);
    await loadTasks();
  }
}

function beginTaskEdit(task) {
  editingTaskId = task.id;
  document.querySelector("#taskDate").value = task.task_date;
  document.querySelector("#taskText").value = task.task_text;
  document.querySelector("#taskEditorHeading").textContent = "Edit task";
  document.querySelector("#saveTaskButton").textContent = "Save task changes";
  document.querySelector("#cancelTaskEdit").hidden = false;
  document.querySelector("#taskEditorHeading").scrollIntoView({ behavior: "smooth", block: "center" });
}

function cancelTaskEdit() {
  editingTaskId = null;
  document.querySelector("#taskForm").reset();
  document.querySelector("#taskDate").value = document.querySelector("#taskDate").min;
  document.querySelector("#taskEditorHeading").textContent = "Add a task";
  document.querySelector("#saveTaskButton").textContent = "Add to Collector's board";
  document.querySelector("#cancelTaskEdit").hidden = true;
  document.querySelector("#taskFormMessage").textContent = "";
}

async function deleteTask(task) {
  if (!window.confirm(`Remove this task from the Collector's board?\n\n${task.task_text}`)) return;
  try {
    await api(`/api/tasks/${task.id}`, { method: "DELETE", body: "{}" });
    if (editingTaskId === task.id) cancelTaskEdit();
    await loadTasks();
  } catch (error) {
    setNotice(document.querySelector("#taskPageError"), error.message);
  }
}

async function loadCollectorWorkspace() {
  await Promise.allSettled([
    loadDashboard(),
    loadDistrictOptions(),
    loadBoundaries(),
    loadSources(),
    loadIndexStatus(),
  ]);
}

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
}

function renderDashboard(data) {
  document.querySelector("#metricReported").textContent = displayNumber(data.reported_incidents_30d);
  document.querySelector("#metricReportedCaption").textContent =
    `${displayNumber(data.reported_districts_30d)} district(s) named; reports are unverified`;

  const trend = data.emerging_issues;
  document.querySelector("#metricEmerging").textContent =
    trend ? displayNumber(trend.count) : "Data unavailable";
  document.querySelector("#metricEmergingCaption").textContent = trend
    ? `${trend.recent_total} reports in the last 7 days vs ${trend.previous_total} in the preceding 7 days`
    : "Insufficient dated incident records for a 7-day comparison";

  document.querySelector("#metricActions").textContent = "Data unavailable";

  const freshness = data.freshness;
  document.querySelector("#metricFreshness").textContent =
    freshness ? formatIst(freshness, { dateStyle: "short", timeStyle: "short" }) : "Unknown";
  document.querySelector("#metricFreshnessCaption").textContent = freshness
    ? "Most recent successful source ingestion"
    : "No successful source ingestion has been recorded";
  if (freshness) {
    const ageHours = (Date.now() - new Date(freshness).getTime()) / 3_600_000;
    if (ageHours > 48) {
      document.querySelector("#metricFreshnessCaption").textContent =
        "Last successful source ingestion is over 48 hours old";
    }
  }

  if (data.tngis_error) {
    setNotice(
      document.querySelector("#pageError"),
      `TNGIS accident metrics could not be refreshed: ${data.tngis_error}`,
    );
  }
  districtCounts = new Map(Object.entries(data.reported_by_district || {}));
  districtCategoryCounts = new Map(
    Object.entries(data.reported_categories_by_district || {}),
  );
  document.querySelector("#lastUpdated").textContent =
    `Dashboard loaded ${formatIst(new Date().toISOString(), { timeStyle: "short" })}`;
  updateMapSelection();
  updateMapCategoryColors();
}

function getDistrictRings(feature) {
  if (feature.geometry?.type === "Polygon") return feature.geometry.coordinates || [];
  if (feature.geometry?.type === "MultiPolygon") return (feature.geometry.coordinates || []).flat();
  return [];
}

function renderMap(features) {
  const map = document.querySelector("#districtMap");
  const loading = document.querySelector("#mapLoading");
  map.replaceChildren();
  if (!features.length) {
    loading.textContent = "District boundary data is not available.";
    return;
  }

  const positions = [];
  for (const feature of features) {
    for (const ring of getDistrictRings(feature)) {
      for (const point of ring) {
        if (Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1])) {
          positions.push(point);
        }
      }
    }
  }
  if (!positions.length) {
    loading.textContent = "The district boundary data could not be drawn.";
    return;
  }
  const bounds = {
    minX: Infinity,
    maxX: -Infinity,
    minY: Infinity,
    maxY: -Infinity,
  };
  for (const [longitude, latitude] of positions) {
    bounds.minX = Math.min(bounds.minX, longitude);
    bounds.maxX = Math.max(bounds.maxX, longitude);
    bounds.minY = Math.min(bounds.minY, latitude);
    bounds.maxY = Math.max(bounds.maxY, latitude);
  }
  const margin = 28;
  const scale = Math.min(
    (640 - margin * 2) / Math.max(bounds.maxX - bounds.minX, 0.001),
    (700 - margin * 2) / Math.max(bounds.maxY - bounds.minY, 0.001),
  );
  const width = (bounds.maxX - bounds.minX) * scale;
  const height = (bounds.maxY - bounds.minY) * scale;
  const offsetX = (640 - width) / 2;
  const offsetY = (700 - height) / 2;
  const project = ([longitude, latitude]) => [
    offsetX + (longitude - bounds.minX) * scale,
    offsetY + (bounds.maxY - latitude) * scale,
  ];

  for (const feature of features) {
    const district = feature.properties?.district;
    if (!district) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const commands = [];
    for (const ring of getDistrictRings(feature)) {
      if (!ring.length) continue;
      const points = ring.map(project);
      commands.push(
        `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)}`,
        ...points.slice(1).map(([x, y]) => `L${x.toFixed(2)},${y.toFixed(2)}`),
        "Z",
      );
    }
    path.setAttribute("d", commands.join(" "));
    path.setAttribute("class", "district-shape");
    path.setAttribute("data-district", district);
    path.setAttribute("tabindex", "0");
    path.setAttribute("role", "button");
    path.setAttribute("aria-label", `${district}, ${districtCounts.get(district) || 0} reported incident records`);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${district} · ${districtCounts.get(district) || 0} reported records`;
    path.append(title);
    path.addEventListener("click", () => selectDistrict(district));
    path.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectDistrict(district);
      }
    });
    map.append(path);
  }
  loading.hidden = true;
  updateMapSelection();
  updateMapCategoryColors();
}

function updateMapCategoryColors() {
  for (const shape of document.querySelectorAll(".district-shape")) {
    const district = shape.getAttribute("data-district");
    const categories = districtCategoryCounts.get(district) || {};
    const dominantCategory = Object.entries(categories)
      .sort(([categoryA, countA], [categoryB, countB]) =>
        countB - countA || categoryA.localeCompare(categoryB),
      )[0]?.[0];
    const style = INCIDENT_CATEGORY_STYLE_BY_ID.get(dominantCategory) ||
      INCIDENT_CATEGORY_STYLE_BY_ID.get("other");
    shape.style.setProperty("--district-fill", dominantCategory ? style.color : "#E8EDF3");
    const reportCount = districtCounts.get(district) || 0;
    const categoryLabel = dominantCategory
      ? INCIDENT_CATEGORY_STYLE_BY_ID.get(dominantCategory)?.label || "Other incidents"
      : "no category-coded reports";
    const description = `${district} · ${reportCount} reported records · ${categoryLabel}`;
    shape.setAttribute("aria-label", description);
    const title = shape.querySelector("title");
    if (title) title.textContent = description;
  }
}

function renderMapCategoryLegend() {
  const legend = document.querySelector("#mapCategoryLegend");
  legend.replaceChildren(...INCIDENT_CATEGORY_STYLES.map(({ label, color }) => {
    const item = document.createElement("li");
    item.className = "map-category-item";
    const swatch = document.createElement("span");
    swatch.className = "map-category-swatch";
    swatch.style.backgroundColor = color;
    swatch.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = label;
    item.append(swatch, text);
    return item;
  }));
}

function updateMapSelection() {
  document.querySelectorAll(".district-shape").forEach((shape) => {
    const selected = shape.getAttribute("data-district") === selectedDistrict.value;
    shape.classList.toggle("is-selected", selected);
    shape.setAttribute("aria-pressed", String(selected));
  });
}

async function loadBoundaries() {
  const sourceLabel = document.querySelector("#mapSource");
  try {
    const data = await api("/api/boundaries");
    mapFeatures = data.features || [];
    renderMap(mapFeatures);
    sourceLabel.textContent = data.source || "Configured boundary source";
    const vintage = data.vintages?.length
      ? ` Feature vintage: ${data.vintages.join(", ")}.`
      : " Feature vintage not specified by the source.";
    document.querySelector("#mapDisclaimer").textContent =
      `${data.source || "Community boundary source"}.${vintage} This dataset has not been independently certified by the Tamil Nadu Government; verify boundaries before official decisions.`;
  } catch (error) {
    sourceLabel.textContent = "Boundary data unavailable";
    document.querySelector("#mapLoading").textContent = error.message;
    setNotice(document.querySelector("#pageError"), `District map could not be loaded: ${error.message}`);
  }
}

async function loadDistrictOptions() {
  const data = await api("/api/districts");
  for (const item of data.districts || []) {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = `${item.name} · ${item.reported_incidents}`;
    districtPicker.append(option);
  }
}

async function selectDistrict(district) {
  selectedDistrict.value = district || "";
  districtPicker.value = selectedDistrict.value;
  updateMapSelection();
  const title = document.querySelector("#districtDetailTitle");
  const count = document.querySelector("#districtIncidentCount");
  const summary = document.querySelector("#districtDetailSummary");
  const categories = document.querySelector("#districtCategories");
  const incidentList = document.querySelector("#incidentList");
  const incidentListTitle = document.querySelector("#incidentListTitle");
  const askButton = document.querySelector("#askAboutDistrict");

  if (!district) {
    title.textContent = "Select a district";
    count.textContent = "—";
    summary.textContent = "Choose a district on the map or from the list to see reported information.";
    categories.replaceChildren(makeText("Select a district to view categories.", "empty-note"));
    incidentListTitle.textContent = "District incident reports";
    incidentList.replaceChildren(makeText("Select a district to load its ingested reports.", "empty-state"));
    askButton.disabled = true;
    updateChatDistrictContext();
    return;
  }

  title.textContent = district;
  count.textContent = "Loading…";
  summary.textContent = "The configured feeds do not provide verified active status or severity.";
  categories.replaceChildren();
  incidentListTitle.textContent = `${district} · reported incident records`;
  incidentList.replaceChildren(makeText("Loading incident reports…", "empty-state"));
  askButton.disabled = false;
  updateChatDistrictContext();

  try {
    const data = await api(`/api/incidents?district=${encodeURIComponent(district)}`);
    const incidents = data.incidents || [];
    count.textContent = new Intl.NumberFormat("en-IN").format(incidents.length);
    if (!incidents.length) {
      summary.textContent =
        "No ingested reports are available for this district. This does not mean no incidents occurred.";
      categories.replaceChildren(makeText("No reported categories in the current records.", "empty-note"));
      incidentList.replaceChildren(makeText(
        "No ingested reports are available. This does not mean no incidents occurred.",
        "empty-state",
      ));
      return;
    }
    const categoryNames = [...new Set(incidents.map((record) => titleCase(record.category)))];
    summary.textContent = `${incidents.length} unique ingested report record(s). These are unverified reports, not verified active incidents.`;
    for (const category of categoryNames) {
      const tag = makeText(category, "category-tag");
      applyCategoryColor(tag, category.replaceAll(" ", "_").toLowerCase());
      categories.append(tag);
    }
    incidentList.replaceChildren(...incidents.slice(0, 10).map(renderIncident));
  } catch (error) {
    count.textContent = "Unavailable";
    incidentList.replaceChildren(makeText(error.message, "empty-state"));
  }
}

function makeText(text, className) {
  const element = document.createElement("p");
  element.className = className;
  element.textContent = text;
  return element;
}

function renderIncident(record) {
  const article = document.createElement("article");
  article.className = "incident-row";
  const header = document.createElement("div");
  header.className = "incident-row-header";
  const title = document.createElement("h4");
  title.textContent = record.title || "Untitled report";
  const date = document.createElement("time");
  date.textContent = formatIst(record.published_at, { dateStyle: "medium", timeStyle: "short" });
  header.append(title, date);
  const category = makeText(titleCase(record.category), "incident-category");
  applyCategoryColor(category, record.category);
  article.append(header, category);
  if (record.description) {
    const description = makeText(record.description, "incident-description");
    article.append(description);
  }
  const status = makeText(
    "Verification: unverified news/web report · status and severity unavailable",
    "incident-caveat",
  );
  article.append(status);
  const sourceList = document.createElement("div");
  sourceList.className = "incident-sources";
  for (const source of record.sources || []) {
    if (!source.source_url || !source.source_url.startsWith("https://")) continue;
    const link = document.createElement("a");
    link.href = source.source_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `${source.source_name || "Source"} · ${formatIst(source.published_at, { dateStyle: "short" })}`;
    sourceList.append(link);
  }
  if (sourceList.childElementCount) article.append(sourceList);
  return article;
}

function updateChatDistrictContext() {
  const context = selectedDistrict.value || "Tamil Nadu statewide";
  document.querySelector("#chatDistrictContext").textContent = context;
  document.querySelector("#clearDistrictContext").hidden = !selectedDistrict.value;
}

function appendSourceSection(message, sources) {
  const validSources = (sources || []).filter((source) =>
    typeof source.url === "string" && source.url.startsWith("https://"),
  );
  if (!validSources.length) return;
  const sourceSection = document.createElement("div");
  sourceSection.className = "answer-sources";
  sourceSection.append(makeText("Sources retrieved", "answer-sources-heading"));
  for (const source of validSources) {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = `${source.name} · ${source.evidence_level}`;
    sourceSection.append(link);
  }
  message.append(sourceSection);
}

function appendChatMessage(role, text, sources = []) {
  document.querySelector(".assistant-welcome")?.remove();
  const message = document.createElement("article");
  message.className = `chat-message chat-message-${role}`;
  const label = makeText(role === "assistant" ? "District intelligence assistant" : "You", "chat-message-label");
  const content = document.createElement("div");
  content.className = "chat-message-content";
  renderMessageText(content, text);
  message.append(label, content);
  appendSourceSection(message, sources);
  chatMessages.append(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return message;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function addSvgText(svg, x, y, value, className = "") {
  const text = svgElement("text", { x, y, class: className });
  text.textContent = value;
  svg.append(text);
  return text;
}

function createChartShell(visual, title, description) {
  visual.setAttribute("role", "group");
  visual.setAttribute("aria-label", description);
  const heading = makeText(title, "response-chart-title");
  visual.append(heading);
  return heading;
}

function renderBarChart(visual, data) {
  const labels = (data.labels || []).slice(0, 12);
  const values = (data.values || []).slice(0, labels.length).map(Number);
  if (!labels.length || values.some((value) => !Number.isFinite(value))) return false;
  createChartShell(visual, data.series_label || "Reported incidents", "Bar chart of reported incident counts");
  const svg = svgElement("svg", {
    viewBox: "0 0 640 290",
    class: "response-chart-svg",
    role: "img",
    "aria-label": "Reported incident counts by category or district",
  });
  const maxValue = Math.max(...values, 1);
  const chartLeft = 44;
  const chartTop = 22;
  const chartHeight = 195;
  const usableWidth = 570;
  const groupWidth = usableWidth / labels.length;
  const barWidth = Math.min(42, groupWidth * 0.62);
  svg.append(svgElement("line", { x1: chartLeft, y1: chartTop + chartHeight, x2: 620, y2: chartTop + chartHeight, class: "chart-axis" }));
  labels.forEach((label, index) => {
    const barHeight = (values[index] / maxValue) * (chartHeight - 14);
    const x = chartLeft + index * groupWidth + (groupWidth - barWidth) / 2;
    const y = chartTop + chartHeight - barHeight;
    const rect = svgElement("rect", {
      x, y, width: barWidth, height: barHeight, rx: 5,
      class: "response-bar",
      tabindex: 0,
    });
    const tooltip = svgElement("title");
    tooltip.textContent = `${label}: ${values[index]}`;
    rect.append(tooltip);
    svg.append(rect);
    addSvgText(svg, x + barWidth / 2, y - 5, String(values[index]), "chart-value");
    const labelText = addSvgText(svg, x + barWidth / 2, chartTop + chartHeight + 20, label.replaceAll("_", " "), "chart-label");
    labelText.setAttribute("transform", `rotate(-25 ${x + barWidth / 2} ${chartTop + chartHeight + 20})`);
  });
  visual.append(svg);
  return true;
}

function categoryColor(category) {
  return INCIDENT_CATEGORY_STYLE_BY_ID.get(category)?.color || "#9E9E9E";
}

function renderDonutChart(visual, data) {
  const items = (data.labels || []).slice(0, 10).map((label, index) => ({
    label,
    value: Number(data.values?.[index]),
  })).filter((item) => Number.isFinite(item.value) && item.value >= 0);
  const total = items.reduce((sum, item) => sum + item.value, 0);
  if (!items.length || total <= 0) return false;
  createChartShell(visual, "Incident category distribution", "Donut chart of reported incident categories");
  const svg = svgElement("svg", {
    viewBox: "0 0 260 220",
    class: "response-donut-svg",
    role: "img",
    "aria-label": "Incident category share",
  });
  const centerX = 130;
  const centerY = 108;
  const radius = 72;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  for (const item of items) {
    const length = item.value / total * circumference;
    const segment = svgElement("circle", {
      cx: centerX, cy: centerY, r: radius, fill: "none",
      stroke: categoryColor(item.label), "stroke-width": 26,
      "stroke-dasharray": `${length} ${circumference - length}`,
      "stroke-dashoffset": -offset,
      class: "donut-segment",
      tabindex: 0,
    });
    const tooltip = svgElement("title");
    tooltip.textContent = `${item.label.replaceAll("_", " ")}: ${item.value} (${Math.round(item.value / total * 100)}%)`;
    segment.append(tooltip);
    svg.append(segment);
    offset += length;
  }
  addSvgText(svg, centerX, centerY - 2, String(total), "donut-total");
  addSvgText(svg, centerX, centerY + 17, "reports", "donut-caption");
  visual.append(svg);
  const legend = document.createElement("ul");
  legend.className = "response-chart-legend";
  for (const item of items) {
    const entry = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "chart-legend-swatch";
    swatch.style.backgroundColor = categoryColor(item.label);
    const label = document.createElement("span");
    label.textContent = `${titleCase(item.label)} · ${item.value}`;
    entry.append(swatch, label);
    legend.append(entry);
  }
  visual.append(legend);
  return true;
}

function renderLineChart(visual, data) {
  const points = (data.points || []).filter((point) =>
    point.date && Number.isFinite(Number(point.value)),
  );
  if (points.length < 2) return false;
  createChartShell(visual, "Reported incident trend", "Line chart of reported incidents over time");
  const svg = svgElement("svg", {
    viewBox: "0 0 640 280",
    class: "response-chart-svg",
    role: "img",
    "aria-label": "Daily reported incident counts",
  });
  const xStart = 50;
  const xEnd = 610;
  const yStart = 25;
  const yEnd = 220;
  const maxValue = Math.max(...points.map((point) => Number(point.value)), 1);
  const coords = points.map((point, index) => ({
    ...point,
    x: xStart + index * (xEnd - xStart) / (points.length - 1),
    y: yEnd - Number(point.value) / maxValue * (yEnd - yStart),
  }));
  for (let tick = 0; tick <= 4; tick += 1) {
    const y = yEnd - tick * (yEnd - yStart) / 4;
    svg.append(svgElement("line", { x1: xStart, y1: y, x2: xEnd, y2: y, class: "chart-gridline" }));
  }
  svg.append(svgElement("polyline", {
    points: coords.map((point) => `${point.x},${point.y}`).join(" "),
    class: "response-line",
  }));
  coords.forEach((point, index) => {
    const circle = svgElement("circle", { cx: point.x, cy: point.y, r: 4, class: "response-line-point", tabindex: 0 });
    const tooltip = svgElement("title");
    tooltip.textContent = `${formatIst(point.date, { dateStyle: "medium" })}: ${point.value} incidents`;
    circle.append(tooltip);
    svg.append(circle);
    if (index === 0 || index === coords.length - 1 || index % Math.ceil(coords.length / 5) === 0) {
      addSvgText(svg, point.x, 248, formatIst(point.date, { dateStyle: "short" }), "chart-label");
    }
  });
  visual.append(svg);
  return true;
}

function renderResponseMap(visual, data) {
  const sourceMap = document.querySelector("#districtMap");
  const mapDistricts = data.districts || [];
  if (!mapDistricts.length) return false;
  createChartShell(visual, "Reported areas by district", "District map highlighting matching reported incident areas");
  const affected = new Map(mapDistricts.map((item) => [item.district, item]));
  if (sourceMap?.querySelector(".district-shape")) {
    const map = sourceMap.cloneNode(true);
    map.removeAttribute("id");
    map.classList.add("response-district-map");
    for (const shape of map.querySelectorAll(".district-shape")) {
      const district = shape.getAttribute("data-district");
      shape.classList.remove("is-selected");
      shape.addEventListener("click", () => selectDistrict(district));
      shape.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectDistrict(district);
        }
      });
      const report = affected.get(district);
      if (!report) {
        shape.style.setProperty("--district-fill", "#E8EDF3");
        shape.style.opacity = "0.28";
        continue;
      }
      const dominantCategory = Object.entries(report.categories || {})
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0]?.[0];
      shape.style.setProperty("--district-fill", categoryColor(dominantCategory));
      shape.style.opacity = "1";
      shape.setAttribute("aria-label", `${district}: ${report.count} matching reported incident(s)`);
      const title = shape.querySelector("title");
      if (title) title.textContent = `${district} · ${report.count} matching reported incident(s)`;
    }
    visual.append(map);
  } else {
    visual.append(makeText("District boundaries are unavailable; matching districts are listed below.", "response-visual-note"));
  }
  const list = document.createElement("ul");
  list.className = "response-map-districts";
  for (const report of mapDistricts.slice(0, 12)) {
    const item = makeText(`${report.district} · ${report.count}`, "response-map-district");
    list.append(item);
  }
  visual.append(list);
  const categoryLegend = document.querySelector("#mapCategoryLegend")?.cloneNode(true);
  if (categoryLegend?.childElementCount) {
    categoryLegend.removeAttribute("id");
    categoryLegend.classList.add("response-map-category-legend");
    visual.append(categoryLegend);
  }
  const note = makeText("District-level reported records only; exact incident coordinates and active status are not available.", "response-visual-note");
  visual.append(note);
  return true;
}

function renderIncidentDetails(record) {
  const item = document.createElement("li");
  item.className = "response-timeline-item";
  const time = document.createElement("time");
  if (record.published_at) {
    time.dateTime = record.published_at;
    time.textContent = formatIst(record.published_at, { dateStyle: "medium", timeStyle: "short" });
  } else {
    time.textContent = "Date not available";
  }
  const title = makeText(record.title || "Untitled incident report", "response-record-title");
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = `${record.district} · ${titleCase(record.category)} · ${record.severity}`;
  details.append(summary);
  if (record.description) details.append(makeText(record.description, "response-record-description"));
  item.append(time, title, details);
  return item;
}

function renderTimeline(visual, data) {
  const records = data.records || [];
  if (!records.length) return false;
  createChartShell(visual, "Incident timeline", "Timeline of retrieved incident records");
  const timeline = document.createElement("ol");
  timeline.className = "response-timeline";
  for (const record of records.slice(0, 30)) timeline.append(renderIncidentDetails(record));
  visual.append(timeline);
  return true;
}

function renderResponseTable(visual, data) {
  const records = data.records || [];
  if (!records.length) return false;
  createChartShell(visual, "Incident records", "Table of retrieved incident records");
  const wrapper = document.createElement("div");
  wrapper.className = "response-table-wrap";
  const table = document.createElement("table");
  const headers = ["Incident", "District", "Category", "Date", "Status", "Severity"];
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const value of headers) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = value;
    headerRow.append(cell);
  }
  head.append(headerRow);
  const body = document.createElement("tbody");
  for (const record of records.slice(0, 30)) {
    const row = document.createElement("tr");
    const values = [
      record.title,
      record.district,
      titleCase(record.category),
      record.published_at ? formatIst(record.published_at, { dateStyle: "medium" }) : "Not dated",
      record.status,
      record.severity,
    ];
    for (let index = 0; index < values.length; index += 1) {
      const cell = document.createElement("td");
      cell.textContent = values[index] || "—";
      if (index === 4) {
        cell.className = `response-status response-status-${String(record.status).toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`;
      } else if (index === 5) {
        cell.className = `response-severity response-severity-${String(record.severity).toLowerCase()}`;
      }
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  wrapper.append(table);
  visual.append(wrapper);
  if (data.truncated) {
    visual.append(makeText("Showing the 30 most recent records.", "response-visual-note"));
  }
  return true;
}

function renderResponseVisualization(message, response) {
  if (response.response_type === "text" || !response.visualization_data) return;
  const visual = document.createElement("section");
  visual.className = `chat-response-visual chat-response-${response.response_type}`;
  visual.setAttribute("aria-label", response.title || "Response visualization");
  const data = response.visualization_data;
  let rendered = true;
  if (response.response_type === "kpi_cards") {
    const grid = document.createElement("div");
    grid.className = "response-kpi-grid";
    for (const metric of data.metrics || []) {
      const card = document.createElement("article");
      card.className = "response-kpi-card";
      card.append(makeText(metric.label, "response-kpi-label"));
      card.append(makeText(new Intl.NumberFormat("en-IN").format(metric.value), "response-kpi-value"));
      grid.append(card);
    }
    visual.append(grid);
  } else if (response.response_type === "bar_chart") {
    rendered = renderBarChart(visual, data);
  } else if (response.response_type === "donut_chart") {
    rendered = renderDonutChart(visual, data);
  } else if (response.response_type === "line_chart") {
    rendered = renderLineChart(visual, data);
  } else if (response.response_type === "district_map") {
    rendered = renderResponseMap(visual, data);
  } else if (response.response_type === "timeline") {
    rendered = renderTimeline(visual, data);
  } else if (response.response_type === "table") {
    rendered = renderResponseTable(visual, data);
  }
  if (!rendered) return;
  const note = [data.scope && `Scope: ${data.scope}`, data.record_basis]
    .filter(Boolean)
    .join(" · ");
  if (note) visual.append(makeText(note, "response-visual-note"));
  message.append(visual);
}

function renderStructuredChatMessage(message, response) {
  const content = message.querySelector(".chat-message-content");
  content.replaceChildren();
  if (response.title) {
    content.append(makeText(response.title, "chat-answer-heading"));
  }
  if (response.summary) {
    content.append(makeText(response.summary, "chat-answer-summary"));
  }
  renderMessageText(content, response.answer || "No answer was returned.");
  renderResponseVisualization(message, response);
  if (response.key_observations?.length) {
    const section = document.createElement("section");
    section.className = "response-observations";
    section.append(makeText("Key observations", "response-section-heading"));
    const list = document.createElement("ul");
    for (const observation of response.key_observations) {
      list.append(makeText(observation, "response-observation"));
    }
    section.append(list);
    message.append(section);
  }
  if (response.priority_alerts?.length) {
    const section = document.createElement("section");
    section.className = "response-alerts";
    section.append(makeText("Verified priority alerts", "response-section-heading"));
    const list = document.createElement("ul");
    for (const alert of response.priority_alerts) {
      list.append(makeText(alert, "response-alert"));
    }
    section.append(list);
    message.append(section);
  }
  if (response.retrieval_notice) {
    message.append(makeText(response.retrieval_notice, "response-retrieval-notice"));
  }
  if (response.last_updated) {
    const updated = makeText(
      `Evidence timestamp: ${formatIst(response.last_updated, { dateStyle: "medium", timeStyle: "short" })}`,
      "response-last-updated",
    );
    message.append(updated);
  }
  appendSourceSection(message, response.sources);
}

function appendInlineFormatting(container, value) {
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) container.append(document.createTextNode(value.slice(cursor, index)));
    if (match[1]) {
      const strong = document.createElement("strong");
      strong.textContent = match[1];
      container.append(strong);
    } else if (match[2]) {
      const code = document.createElement("code");
      code.textContent = match[2];
      container.append(code);
    } else {
      let safeUrl = false;
      try {
        const parsed = new URL(match[4]);
        safeUrl = parsed.protocol === "https:" && !parsed.username && !parsed.password;
      } catch {
        safeUrl = false;
      }
      if (safeUrl) {
        const link = document.createElement("a");
        link.href = match[4];
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = match[3];
        container.append(link);
      } else {
        container.append(document.createTextNode(match[0]));
      }
    }
    cursor = index + match[0].length;
  }
  if (cursor < value.length) container.append(document.createTextNode(value.slice(cursor)));
}

function renderMessageText(container, value) {
  let activeList = null;
  for (const line of String(value).split(/\r?\n/)) {
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (bullet || numbered) {
      const listType = bullet ? "ul" : "ol";
      if (!activeList || activeList.tagName.toLowerCase() !== listType) {
        activeList = document.createElement(listType);
        container.append(activeList);
      }
      const item = document.createElement("li");
      appendInlineFormatting(item, (bullet || numbered)[1]);
      activeList.append(item);
      continue;
    }
    activeList = null;
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    const paragraph = document.createElement(heading ? "h4" : "p");
    paragraph.className = heading ? "chat-answer-heading" : "chat-answer-paragraph";
    appendInlineFormatting(paragraph, heading ? heading[1] : line);
    container.append(paragraph);
  }
}

async function askQuestion(question) {
  const trimmed = question.trim();
  if (!trimmed) return;
  appendChatMessage("user", trimmed);
  chatHistory.push({ role: "user", content: trimmed });
  const loading = appendChatMessage("assistant", "Reviewing available evidence…");
  sendQuestion.disabled = true;
  try {
    const links = document.querySelector("#userLinks").value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    const response = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        question: trimmed,
        district: selectedDistrict.value || null,
        response_style: document.querySelector("#responseStyle").value,
        links,
        history: chatHistory.slice(-6, -1),
      }),
    });
    if (response.response_type) {
      renderStructuredChatMessage(loading, response);
    } else {
      const answerContent = loading.querySelector(".chat-message-content");
      answerContent.replaceChildren();
      renderMessageText(answerContent, response.answer || "No answer was returned.");
      appendSourceSection(loading, response.sources);
    }
    chatHistory.push({ role: "assistant", content: response.answer });
    document.querySelector("#userLinks").value = "";
  } catch (error) {
    loading.querySelector(".chat-message-content").textContent = error.message;
    loading.classList.add("chat-message-error");
    chatHistory.pop();
  } finally {
    sendQuestion.disabled = false;
    chatQuestion.focus();
  }
}

async function loadSources() {
  const data = await api("/api/sources");
  const list = document.querySelector("#sourceList");
  list.replaceChildren();
  for (const source of data.sources || []) {
    const row = document.createElement("div");
    row.className = "source-row";
    const dot = document.createElement("span");
    dot.className = `source-dot ${source.evidence_level === "official" ? "source-dot-official" : ""}`;
    dot.setAttribute("aria-hidden", "true");
    const details = document.createElement("div");
    details.className = "source-row-details";
    const name = document.createElement("strong");
    name.textContent = source.name;
    const meta = document.createElement("span");
    meta.textContent = `${titleCase(source.evidence_level)} · ${titleCase(source.type)}`;
    details.append(name, meta);
    if (source.url?.startsWith("https://")) {
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.className = "source-open-link";
      link.setAttribute("aria-label", `Open ${source.name}`);
      link.textContent = "↗";
      row.append(dot, details, link);
    } else {
      row.append(dot, details);
    }
    list.append(row);
  }
}

async function loadIndexStatus() {
  const data = await api("/api/index");
  const summary = document.querySelector("#indexSummary");
  const rebuild = document.querySelector("#rebuildIndexButton");
  if (data.passages) {
    summary.textContent =
      `${new Intl.NumberFormat("en-IN").format(data.passages)} passages · ${data.mode} · built ${formatIst(data.built_at)}`;
  } else {
    summary.textContent = "Not built yet. Rebuild to crawl enabled approved sources.";
  }
  rebuild.disabled = data.running;
  rebuild.textContent = data.running ? "Index build in progress…" : "Rebuild local index";
  if (data.error) setNotice(document.querySelector("#indexMessage"), data.error);
  else if (data.running) setNotice(document.querySelector("#indexMessage"), data.lines?.join(" · ") || "Crawling approved sources…");
  else if (data.report) setNotice(
    document.querySelector("#indexMessage"),
    `Last build: ${data.report.total_chunks} passages from ${data.report.total_pages} pages.`,
  );
  else setNotice(document.querySelector("#indexMessage"), "", false);
  if (data.running) window.setTimeout(loadIndexStatus, 5000);
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    renderDashboard(data);
  } catch (error) {
    setNotice(document.querySelector("#pageError"), `Dashboard data could not be loaded: ${error.message}`);
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#loginButton");
  button.disabled = true;
  setNotice(loginMessage, "", false);
  try {
    const session = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.querySelector("#username").value,
        password: document.querySelector("#password").value,
      }),
    });
    loginForm.reset();
    await enterDashboard(session);
  } catch (error) {
    setNotice(loginMessage, error.message);
  } finally {
    button.disabled = false;
  }
});

async function enterDashboard(session) {
  showDashboard();
  configureSiteNavigation(session);
  renderMapCategoryLegend();
  setNotice(document.querySelector("#pageError"), "", false);
  await showSite(currentRole === "pa" ? "pa" : "collector");
}

logoutButton.addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
  } finally {
    showLogin();
  }
});

document.querySelector("#collectorSiteButton").addEventListener("click", () => showSite("collector"));
document.querySelector("#paSiteButton").addEventListener("click", () => showSite("pa"));

document.querySelector("#taskForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const task = {
    task_date: document.querySelector("#taskDate").value,
    task_text: document.querySelector("#taskText").value,
  };
  const submitButton = document.querySelector("#saveTaskButton");
  submitButton.disabled = true;
  document.querySelector("#taskFormMessage").textContent = "";
  try {
    if (editingTaskId) {
      await api(`/api/tasks/${editingTaskId}`, {
        method: "PATCH",
        body: JSON.stringify(task),
      });
    } else {
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify(task),
      });
    }
    cancelTaskEdit();
    await loadTasks();
  } catch (error) {
    document.querySelector("#taskFormMessage").textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector("#cancelTaskEdit").addEventListener("click", cancelTaskEdit);

districtPicker.addEventListener("change", () => selectDistrict(districtPicker.value));
document.querySelector("#clearDistrictContext").addEventListener("click", () => selectDistrict(""));
document.querySelector("#askAboutDistrict").addEventListener("click", () => {
  document.querySelector("#assistant").scrollIntoView({ behavior: "smooth" });
  chatQuestion.focus();
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = chatQuestion.value;
  if (!question.trim()) return;
  chatQuestion.value = "";
  await askQuestion(question);
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    chatQuestion.value = button.textContent.trim();
    chatQuestion.focus();
  });
});

document.querySelector("#toggleLinks").addEventListener("click", (event) => {
  const panel = document.querySelector("#linksPanel");
  panel.hidden = !panel.hidden;
  event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) document.querySelector("#userLinks").focus();
});

document.querySelector("#refreshButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  setNotice(document.querySelector("#refreshMessage"), "Refreshing configured incident sources and TNGIS metrics…");
  try {
    const result = await api("/api/refresh", { method: "POST", body: "{}" });
    const added = (result.results || []).reduce((total, item) => total + (item.incidents_added || 0), 0);
    const sourceSummary = (result.results || []).map((item) => `${item.source}: ${item.records_seen} record(s)`).join(" · ");
    const summary = `Refresh complete. ${added} new incident record(s) added.${sourceSummary ? ` ${sourceSummary}.` : ""}`;
    setNotice(document.querySelector("#refreshMessage"), summary);
    if (result.errors?.length) {
      setNotice(document.querySelector("#pageError"), result.errors.join(" · "));
    } else {
      setNotice(document.querySelector("#pageError"), "", false);
    }
    await loadDashboard();
    if (selectedDistrict.value) await selectDistrict(selectedDistrict.value);
  } catch (error) {
    setNotice(document.querySelector("#refreshMessage"), `Refresh failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#rebuildIndexButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/index/rebuild", { method: "POST", body: "{}" });
    setNotice(document.querySelector("#indexMessage"), result.message);
    await loadIndexStatus();
  } catch (error) {
    setNotice(document.querySelector("#indexMessage"), error.message);
  }
});

async function transcribeFile(file) {
  const message = document.querySelector("#voiceMessage");
  if (!file) return;
  if (file.size > 25_000_000) {
    setNotice(message, "Audio must be no larger than 25 MB.");
    return;
  }
  setNotice(message, "Transcribing audio…");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  try {
    const result = await api("/api/transcribe", {
      method: "POST",
      body: JSON.stringify({
        audio_base64: btoa(binary),
        filename: file.name || "recording.webm",
        content_type: file.type || "audio/webm",
      }),
    });
    setNotice(message, `Recognised: ${result.transcript}`);
    chatQuestion.value = result.transcript;
    await askQuestion(result.transcript);
    chatQuestion.value = "";
  } catch (error) {
    setNotice(message, `Transcription failed: ${error.message}`);
  }
}

document.querySelector("#audioUpload").addEventListener("change", (event) => {
  const [file] = event.currentTarget.files || [];
  if (file) transcribeFile(file);
  event.currentTarget.value = "";
});

document.querySelector("#recordButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const message = document.querySelector("#voiceMessage");
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
    button.textContent = "Start recording";
    button.classList.remove("is-recording");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    setNotice(message, "Microphone recording is not supported in this browser. Upload an audio file instead.");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.addEventListener("dataavailable", (recordingEvent) => {
      if (recordingEvent.data.size) recordedChunks.push(recordingEvent.data);
    });
    mediaRecorder.addEventListener("stop", async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
      await transcribeFile(new File([blob], "voice-question.webm", { type: blob.type }));
    }, { once: true });
    mediaRecorder.start();
    button.textContent = "Stop and transcribe";
    button.classList.add("is-recording");
    setNotice(message, "Recording… select “Stop and transcribe” when finished.");
  } catch (error) {
    setNotice(message, `Microphone unavailable: ${error.message}`);
  }
});

async function initialise() {
  try {
    const session = await api("/api/session");
    loginSetupNote.hidden = session.login_configured;
    if (session.authenticated) await enterDashboard(session);
    else showLogin();
  } catch (error) {
    setNotice(loginMessage, `Website service is unavailable: ${error.message}`);
    showLogin();
  }
}

initialise();
