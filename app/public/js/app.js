const chartEl = document.querySelector("#chart");
const tooltipEl = document.querySelector("#tooltip");
const metaEl = document.querySelector("#dataset-meta");
const profilesEl = document.querySelector("#profiles");
const clearButton = document.querySelector("#clear-selection");

const MAX_SELECTED = 6;
const COMPONENT_KEYS = ["landing", "review", "attach"];
const selected = [];

let dataset = null;

function toggleSelection(login) {
  const index = selected.indexOf(login);
  if (index >= 0) {
    selected.splice(index, 1);
  } else {
    selected.unshift(login);
    if (selected.length > MAX_SELECTED) {
      selected.pop();
    }
  }
  renderProfiles();
  syncSeriesState();
}

function syncSeriesState() {
  d3.select(chartEl)
    .selectAll(".series")
    .classed("is-selected", (d) => selected.includes(d.login));
}

function formatScore(value) {
  return value == null ? "—" : Number(value).toFixed(2);
}

function componentBar(key, value, weight) {
  const share = weight > 0 ? Math.max(0, Math.min(1, value / weight)) : 0;
  return `
    <div class="component">
      <div class="component-meta">
        <span class="component-label">${key}</span>
        <span class="component-value">${formatScore(value)} / ${formatScore(weight)}</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill" style="width:${(share * 100).toFixed(1)}%"></div>
      </div>
    </div>`;
}

function renderProfiles() {
  if (selected.length === 0) {
    profilesEl.innerHTML = '<p class="empty">No engineer selected.</p>';
    clearButton.hidden = true;
    return;
  }

  clearButton.hidden = false;
  const weights = dataset.weights ?? {};
  const intervalCaption = dataset.interval ?? "90% central credible interval";

  profilesEl.innerHTML = selected
    .map((login) => {
      const engineer = dataset.engineers.find((e) => e.login === login);
      const components = engineer.components ?? {};
      const bars = COMPONENT_KEYS.map((key) =>
        componentBar(key, components[key] ?? 0, weights[key] ?? 0),
      ).join("");
      return `
        <article class="profile">
          <h3 class="profile-name">${engineer.login}</h3>
          <p class="headline">
            <span class="headline-value">${formatScore(engineer.theta_mean)}</span>
            <span class="headline-interval">${formatScore(engineer.theta_ci_5)}–${formatScore(engineer.theta_ci_95)}</span>
            <span class="headline-caption">${intervalCaption}</span>
          </p>
          <p class="metric">
            <span class="metric-value">${engineer.total_commits.toLocaleString()}</span>
            <span class="metric-label">commits</span>
          </p>
          <div class="components">${bars}</div>
        </article>`;
    })
    .join("");
}

function placeTooltip(event) {
  const bounds = chartEl.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  const pad = 10;
  const flipX = x + tooltipEl.offsetWidth + pad * 2 > bounds.width;
  tooltipEl.style.left = `${flipX ? x - tooltipEl.offsetWidth - pad : x + pad}px`;
  tooltipEl.style.top = `${Math.max(0, y - tooltipEl.offsetHeight - 6)}px`;
}

function showTooltip(event, label) {
  tooltipEl.hidden = false;
  tooltipEl.textContent = label;
  placeTooltip(event);
}

function hideTooltip() {
  tooltipEl.hidden = true;
}

function setHover(login, isHovered) {
  d3.select(chartEl)
    .selectAll(".series")
    .filter((d) => d.login === login)
    .classed("is-hovered", isHovered);
}

function drawChart() {
  chartEl.querySelectorAll("svg").forEach((svg) => svg.remove());
  hideTooltip();

  const width = chartEl.clientWidth;
  const height = chartEl.clientHeight;
  if (width < 10 || height < 10) return;

  const margin = { top: 18, right: 12, bottom: 26, left: 36 };
  const dates = dataset.dates.map((d) => new Date(`${d}T00:00:00`));
  const [yMin, yMax] = dataset.score_range ?? [1, 10];

  const svg = d3
    .select(chartEl)
    .insert("svg", ".tooltip")
    .attr("width", width)
    .attr("height", height)
    .attr("viewBox", `0 0 ${width} ${height}`);

  const x = d3.scaleTime().domain(d3.extent(dates)).range([margin.left, width - margin.right]);

  const y = d3
    .scaleLinear()
    .domain([yMin, yMax])
    .range([height - margin.bottom, margin.top]);

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x).ticks(width / 110).tickSizeOuter(0));

  svg
    .append("g")
    .attr("class", "axis")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).ticks(5).tickSize(-(width - margin.left - margin.right)))
    .call((g) => g.select(".domain").remove());

  svg
    .append("text")
    .attr("class", "axis-title")
    .attr("x", margin.left)
    .attr("y", 12)
    .text("posterior mean of θ");

  const line = d3
    .line()
    .defined((value) => value != null)
    .x((_, i) => x(dates[i]))
    .y((value) => y(value));

  const paths = [...dataset.engineers].reverse().map((engineer) => ({
    login: engineer.login,
    d: line(engineer.series),
  }));

  const seriesGroup = svg.append("g");

  seriesGroup
    .selectAll(".series")
    .data(paths, (d) => d.login)
    .join("path")
    .attr("class", "series")
    .attr("d", (d) => d.d);

  seriesGroup
    .selectAll(".series-hit")
    .data(paths, (d) => d.login)
    .join("path")
    .attr("class", "series-hit")
    .attr("d", (d) => d.d)
    .on("pointerenter", (event, d) => {
      setHover(d.login, true);
      showTooltip(event, d.login);
    })
    .on("pointermove", (event, d) => showTooltip(event, d.login))
    .on("pointerleave", (_, d) => {
      setHover(d.login, false);
      hideTooltip();
    })
    .on("click", (_, d) => toggleSelection(d.login));

  if (dataset.mu) {
    svg
      .append("path")
      .attr("class", "series-mu")
      .attr("d", line(dataset.mu))
      .on("pointerenter", (event) => showTooltip(event, "population mean μ"))
      .on("pointermove", (event) => showTooltip(event, "population mean μ"))
      .on("pointerleave", hideTooltip)
      .on("click", (event) => event.stopPropagation());
  }

  syncSeriesState();
}

async function main() {
  try {
    const response = await fetch("/data/engineers.json");
    if (!response.ok) {
      throw new Error(`Failed to load dataset (${response.status})`);
    }
    dataset = await response.json();

    const start = dataset.dates[0];
    const end = dataset.dates[dataset.dates.length - 1];
    metaEl.textContent = `${dataset.repository} · ${dataset.engineers.length} engineers · ${dataset.window_days} days (${start} – ${end})`;

    renderProfiles();
    drawChart();
    new ResizeObserver(() => drawChart()).observe(chartEl);
  } catch (error) {
    metaEl.classList.add("error");
    metaEl.textContent = error.message;
  }
}

clearButton.addEventListener("click", () => {
  selected.length = 0;
  renderProfiles();
  syncSeriesState();
});

main();
