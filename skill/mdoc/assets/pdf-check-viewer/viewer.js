const token = new URLSearchParams(location.search).get("token");
let report = null;
let selected = null;
let filter = "active";
let artifactFilter = "all";

const api = (path, options = {}) => fetch(path, {
  ...options,
  headers: {"X-GV-Token": token, ...(options.headers || {})},
});
const post = (path, payload = {}) => api(path, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify(payload),
});
const esc = (value) => String(value ?? "").replace(/[&<>"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
})[character]);

function isVisible(finding) {
  const statusMatches = filter === "all" || (filter === "active"
    ? finding.status !== "ignored-by-user"
    : finding.status === filter);
  return statusMatches && (artifactFilter === "all" || finding.artifact_id === artifactFilter);
}

function drawCounts() {
  const counts = report.counts;
  document.querySelector("#counts").textContent =
    `有效错误 ${counts.effective_errors} · 已忽略 ${counts.ignored_errors} · 警告 ${counts.warnings} · 建议 ${counts.suggestions}`;
}

function drawArtifactFilter() {
  const host = document.querySelector("#artifact-filter");
  host.innerHTML = '<option value="all">全部 PDF / 语言</option>' + report.artifacts.map((artifact) =>
    `<option value="${esc(artifact.id)}">${esc(artifact.id)} · ${esc(artifact.locale)}</option>`
  ).join("");
  host.value = artifactFilter;
}

function drawList() {
  const items = report.findings.filter(isVisible);
  document.querySelector("#findings").innerHTML = items.map((finding) => `
    <div class="finding ${esc(finding.severity)} ${selected?.id === finding.id ? "selected" : ""}" data-id="${esc(finding.id)}">
      <b>${esc(finding.message)}</b>
      <div>${esc(finding.artifact_id)} · ${esc(finding.rule_id)} · 第 ${esc(finding.pdf_pages.join(","))} 页</div>
    </div>`).join("") || "<p>没有匹配的问题。</p>";
  document.querySelectorAll(".finding").forEach((element) => {
    element.onclick = () => selectFinding(report.findings.find((finding) => finding.id === element.dataset.id));
  });
}

function drawRegions(finding, image) {
  const host = document.querySelector("#regions");
  host.innerHTML = "";
  const page = report.pages.find((entry) => entry.artifact_id === finding.artifact_id && entry.page === finding.pdf_pages[0]);
  if (!page) return;
  for (const region of finding.regions || []) {
    if (region.page !== finding.pdf_pages[0] || !region.bbox_pt) continue;
    const [x0, top, x1, bottom] = region.bbox_pt;
    const element = document.createElement("div");
    element.className = `region ${finding.severity}`;
    element.style.left = `${x0 / page.width_pt * image.clientWidth}px`;
    element.style.top = `${top / page.height_pt * image.clientHeight}px`;
    element.style.width = `${(x1 - x0) / page.width_pt * image.clientWidth}px`;
    element.style.height = `${(bottom - top) / page.height_pt * image.clientHeight}px`;
    host.appendChild(element);
  }
}

function selectFinding(finding) {
  selected = finding;
  drawList();
  document.querySelector("#message").textContent = finding.message;
  const location = finding.source_locations?.[0];
  document.querySelector("#meta").innerHTML = `
    <dt>PDF</dt><dd>${esc(finding.artifact_id)}</dd>
    <dt>规则</dt><dd>${esc(finding.rule_id)}</dd>
    <dt>严重度</dt><dd>${esc(finding.severity)} / ${esc(finding.confidence)}</dd>
    <dt>PDF 页</dt><dd>${esc(finding.pdf_pages.join(","))}</dd>
    <dt>源文件</dt><dd>${esc(location ? `${location.file}:${location.start_line}` : "未映射")}</dd>`;
  document.querySelector("#suggestion").textContent = finding.suggested_fix || "";
  for (const id of ["open-source", "windows-open", "select-editor"]) {
    document.querySelector(`#${id}`).disabled = !location;
  }
  document.querySelector("#ignore").style.display = finding.status === "ignored-by-user" ? "none" : "inline-block";
  const page = finding.pdf_pages[0];
  document.querySelector("#page-title").textContent = `${finding.artifact_id} · 第 ${page} 页`;
  const image = document.querySelector("#page");
  image.src = `/pages/${encodeURIComponent(finding.artifact_id)}/page-${String(page).padStart(6, "0")}.png?token=${encodeURIComponent(token)}&v=${report.generated_at}`;
  image.onload = () => drawRegions(finding, image);
}

async function loadReport(keepSelection = false) {
  const selectedFingerprint = keepSelection ? selected?.fingerprint : null;
  const response = await api(`/api/report?v=${Date.now()}`);
  if (!response.ok) throw new Error(await response.text());
  report = await response.json();
  selected = report.findings.find((finding) => finding.fingerprint === selectedFingerprint) || null;
  drawCounts();
  drawArtifactFilter();
  drawList();
  if (selected) selectFinding(selected);
  else {
    const first = report.findings.find(isVisible);
    if (first) selectFinding(first);
  }
}

async function runAction(button, action) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "处理中…";
  try {
    const response = await action();
    if (response && !response.ok) throw new Error(await response.text());
  } catch (error) {
    alert(error.message || error);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

document.querySelectorAll("[data-filter]").forEach((button) => {
  button.onclick = () => { filter = button.dataset.filter; drawList(); };
});
document.querySelector("#artifact-filter").onchange = (event) => {
  artifactFilter = event.target.value;
  selected = null;
  drawList();
  const first = report.findings.find(isVisible);
  if (first) selectFinding(first);
};
document.querySelector("#open-source").onclick = (event) => runAction(event.target, () =>
  post("/api/open-source", {finding_id: selected.id}));
document.querySelector("#windows-open").onclick = (event) => runAction(event.target, () =>
  post("/api/editor/windows-default", {finding_id: selected.id}));
document.querySelector("#select-editor").onclick = (event) => runAction(event.target, async () => {
  const response = await post("/api/editor/select", {argument_style: "file"});
  if (response.ok) await post("/api/open-source", {finding_id: selected.id});
  return response;
});
document.querySelector("#ignore").onclick = (event) => runAction(event.target, async () => {
  if (!confirm("确认这是机器误报，并忽略当前 PDF 和规则版本下的这一项？")) return null;
  const reason = prompt("请输入忽略原因：", "机器检查误报");
  if (!reason) return null;
  const response = await post("/api/ignore", {finding_id: selected.id, reason});
  if (response.ok) alert("已保存。请点击“重新检查”使忽略状态生效。");
  return response;
});
document.querySelector("#recheck").onclick = (event) => runAction(event.target, async () => {
  const response = await post("/api/recheck");
  if (response.ok) await loadReport(true);
  return response;
});

loadReport().catch((error) => alert(`无法加载检查报告：${error.message || error}`));
