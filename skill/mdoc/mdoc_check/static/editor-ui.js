(() => {
  let session = null, comparison = null, pending = null, fixing = false, sessionSequence = 0, activeView = "editor", pdfContext = null, pdfTimer = null, completedPdfJob = null;
  const originalOpenFile = openFile, originalShowFinding = showFinding, originalLoadReport = loadReport, originalSetChecking = setChecking;
  const originalRenderPreview = renderPreview;
  let previewOrigin = null;
  const editorAvailable = () => Boolean(window.MdocEditor?.create);
  const dirty = () => Boolean(session?.dirty);
  const editorValue = () => session?.editor?.getValue() ?? session?.content ?? "";

  function guard(action) {
    if (fixing) { notify('warning', '当前正在自动修复【' + currentReport + ' / ' + session.path + '】，请等待完成后再切换。'); return false; }
    if (!dirty()) { action(); return true; }
    pending = action;
    let bar = document.getElementById("edit-confirm");
    if (!bar) {
      bar = document.createElement("div"); bar.id = "edit-confirm"; bar.className = "edit-confirm";
      bar.innerHTML = '<b>当前文件有未保存修改</b><span></span><button data-action="return">返回继续编辑</button><button data-action="discard">放弃修改并切换</button>';
      detail.prepend(bar);
      bar.querySelector('[data-action="return"]').onclick = () => { pending = null; bar.remove(); };
      bar.querySelector('[data-action="discard"]').onclick = () => { const next = pending; pending = null; destroyEditor(); bar.remove(); next?.(); };
    }
    return false;
  }

  function destroyEditor() {
    clearAutoFix();
    session?.editor?.destroy(); session = null;
  }
  function destroyComparison() {
    comparison?.editor?.destroy(); comparison = null;
  }

  function status(text, kind = "") {
    const node = document.getElementById("edit-status");
    if (node) { node.textContent = text; node.className = "edit-status " + kind; }
  }
  function updateEditorButtons() {
    const saveDisabled = checking || fixing || !dirty() || session?.conflict;
    for (const id of ['save-edit', 'save-check']) { const button = document.getElementById(id); if (button) button.disabled = saveDisabled; }
    const reload = document.getElementById('reload-edit'); if (reload) reload.disabled = checking || fixing;
    const autoFix = document.getElementById('auto-fix'); if (autoFix) autoFix.disabled = checking || fixing || !session?.autoFixEnabled;
  }

  function setFixing(active) {
    fixing = active;
    for (const id of ['save-edit', 'save-check', 'auto-fix', 'reload-edit', 'editor-tab', 'preview-tab', 'source-tab', 'pdf-tab']) { const node = document.getElementById(id); if (node) node.disabled = active; }
    const button = document.getElementById('auto-fix'); if (button) button.textContent = active ? '正在自动修复...' : '自动修复当前文件';
    updateEditorButtons();
  }

  function clearAutoFix() {
    session?.editor?.clearHighlights();
    document.getElementById('auto-fix-summary')?.remove();
    if (session) { session.autoFix = null; session.trailingNewline = null; }
  }

  function showAutoFixSummary(result) {
    document.getElementById('auto-fix-summary')?.remove();
    const bar = document.createElement('div'); bar.id = 'auto-fix-summary'; bar.className = 'auto-fix-summary';
    const rules = Object.entries(result.rules).map(([rule, count]) => '<button data-rule="' + esc(rule) + '">' + esc(rule) + ' · ' + esc(result.rule_help[rule]?.title || '自动格式修复') + ' · ' + count + ' 项</button>').join('');
    bar.innerHTML = '<div class="auto-fix-head"><span><b>自动修复结果：</b>已修改 ' + result.applied + ' 项；无法自动修复 ' + result.unfixable + ' 项；跳过 ' + result.skipped_rules.length + ' 条规则。<span data-auto-fix-manual class="warning hidden">自动修复后又有手动修改。</span></span><button data-auto-fix-toggle>展开详情</button></div><div class="auto-fix-details hidden"><div class="auto-fix-rules">' + rules + (result.warnings.length ? '<span class="warning">' + esc(result.warnings.join(' ')) + '</span>' : '') + '</div><div data-auto-fix-help class="auto-fix-help"></div></div>';
    document.querySelector('#editor-box .edit-toolbar')?.after(bar);
    const details = bar.querySelector('.auto-fix-details'), toggle = bar.querySelector('[data-auto-fix-toggle]');
    toggle.onclick = () => { const collapsed = details.classList.toggle('hidden'); toggle.textContent = collapsed ? '展开详情' : '收起详情'; };
    const positions = {};
    const showHelp = rule => { const help = result.rule_help[rule] || {}; bar.querySelector('[data-auto-fix-help]').innerHTML = '<b>' + esc(rule) + ' · ' + esc(help.title || '自动格式修复') + '</b><span><b>修改原因：</b>' + esc(help.description || '当前内容不符合该格式规则。') + '</span><span><b>修改方式：</b>' + esc(help.suggestion || '已按规则要求调整格式。') + '</span>' + (help.url ? '<a href="' + esc(help.url) + '" target="_blank" rel="noopener noreferrer">查看规则原文</a>' : ''); };
    bar.querySelectorAll('button[data-rule]').forEach(button => button.onclick = () => {
      showHelp(button.dataset.rule);
      const ranges = result.ranges.filter(range => range.rules.includes(button.dataset.rule)); if (!ranges.length) return;
      const index = positions[button.dataset.rule] = ((positions[button.dataset.rule] ?? -1) + 1) % ranges.length; session.editor.setOffset(ranges[index].from, ranges[index].to);
    });
    const firstRule = Object.keys(result.rules)[0]; if (firstRule) showHelp(firstRule);
  }

  function mappedSelection(selection, reverse = false) {
    if (!session?.autoFix || session.autoFix.manual || !selection || !Number.isInteger(selection.from)) return selection;
    const ranges = session.autoFix.ranges;
    const map = offset => {
      let shift = 0;
      for (const range of ranges) {
        const from = reverse ? range.before_from : range.from, to = reverse ? range.before_to : range.to, target = reverse ? range.from : range.before_from;
        if (offset < from) break;
        if (offset <= to) return target + Math.min(offset - from, Math.max(0, (reverse ? range.to : range.before_to) - target));
        shift += (reverse ? range.to - range.from : range.before_to - range.before_from) - (to - from);
      }
      return offset + shift;
    };
    return {from: map(selection.from), to: map(selection.to)};
  }

  function select(editor, position) {
    if (!position) return;
    if (position.rule || position.finding_id) { editor.setFinding(position.line, position.column, position.end_line, position.end_column); editor.setCursor(position.line, position.column); return; }
    if (Number.isInteger(position.from)) {
      if (!Number.isInteger(position.to) || position.from === position.to) editor.setCursorOffset(position.from);
      else editor.setOffset(position.from, position.to);
    } else editor.setCursor(position.line, position.column);
  }

  async function loadEditor(path, finding = null, activate = true) {
    if (session?.path === path) {
      if (activate) showView("editor");
      if (finding?.rule || finding?.finding_id) session.finding = finding;
      select(session.editor, finding);
      return;
    }
    if (dirty()) return guard(() => loadEditor(path, finding));
    destroyEditor();
    const host = document.getElementById("editor-box");
    if (!host) return;
    if (!editorAvailable()) { host.innerHTML = '<div class="panel error">Markdown 编辑器组件加载失败，请修复 mdoc toolchain 或使用外部编辑器。</div>'; return; }
    try {
      const [source, autoFixConfig] = await Promise.all([api('/api/editor/source?report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(path)), api('/api/editor/auto-fix-config?report=' + encodeURIComponent(currentReport))]);
      host.innerHTML = '<div class="edit-toolbar"><button id="save-edit" disabled>保存</button><button id="save-check" disabled></button><button id="auto-fix">自动修复当前文件</button><button id="reload-edit">重新加载</button><span id="edit-cursor" class="meta">1:1</span><span id="edit-status" class="edit-status">已保存</span></div><div id="edit-surface"></div>';
      const editor = MdocEditor.create(document.getElementById("edit-surface"), {content: source.content, onCursor: (line, column) => { const cursor = document.getElementById('edit-cursor'); if (cursor) cursor.textContent = line + ':' + column; }, onChange: value => {
        if (!session) return; session.dirty = value !== session.content;
        if (session.autoFix && value !== session.autoFix.content) { session.autoFix.manual = true; document.getElementById('auto-fix-summary')?.classList.add('warning'); document.querySelector('[data-auto-fix-manual]')?.classList.remove('hidden'); }
        if (!session.dirty && session.autoFix) queueMicrotask(() => { if (session && !dirty() && session.autoFix) clearAutoFix(); });
        updateEditorButtons();
        status(session.dirty ? "未保存" : "已保存", session.dirty ? "warning" : "");
      }});
      session = {id: ++sessionSequence, report: currentReport, path, source, content: source.content, editor, dirty: false, conflict: false, autoFix: null, trailingNewline: null, autoFixEnabled: autoFixConfig.enabled, finding: finding?.rule || finding?.finding_id ? finding : null};
      const check = document.getElementById("save-check"); check.textContent = source.summary ? "保存并重新检查原范围" : "保存并检查当前文件";
      document.getElementById("save-edit").onclick = () => save(false);
      check.onclick = () => save(true);
      const autoFix = document.getElementById('auto-fix'); autoFix.onclick = runAutoFix; autoFix.disabled = !autoFixConfig.enabled; autoFix.title = autoFixConfig.error || (!autoFixConfig.enabled ? '工作区已关闭自动修复，可在 .mdoc/check.yaml 中设置 auto_fix.enabled: true' : '');
      document.getElementById("reload-edit").onclick = () => guard(() => { destroyEditor(); loadEditor(path, finding); });
      select(editor, finding);
      if (activate) showView("editor");
    } catch (error) {
      host.innerHTML = '<div class="panel error">无法编辑此文件：' + esc(error.message) + '</div>';
    }
  }

  async function runAutoFix() {
    if (!session || fixing || checking) return;
    const active = session, content = editorValue(), report = currentReport, id = active.id;
    showView('editor'); setFixing(true); status('正在自动修复...', 'warning');
    try {
      const result = await api('/api/editor/auto-fix', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report, path: active.path, content, state: active.source.state, session_id: id})});
      if (!session || session.id !== id || session.report !== report || session.path !== active.path || editorValue() !== content) { notify('warning', '编辑内容或会话已变化，本次自动修复结果未应用。'); return; }
      if (!result.changed) { status(active.dirty ? '未保存' : '已保存', active.dirty ? 'warning' : ''); notify('warning', '当前文件没有可自动修复的问题。'); return; }
      active.autoFix = {content: result.content, ranges: result.ranges, manual: false}; active.trailingNewline = result.trailing_newline;
      active.editor.clearFindingHighlight(); active.editor.setValue(result.content, result.ranges); showAutoFixSummary(result);
      if (result.ranges.length) { const position = active.editor.setOffset(result.ranges[0].from, result.ranges[0].to), cursor = document.getElementById('edit-cursor'); if (cursor) cursor.textContent = position.line + ':' + position.column; }
      status('未保存 · 已自动修复', 'warning'); notify('success', '当前文件已完成自动修复，请确认后保存。');
    } catch (error) { status(active.dirty ? '未保存' : '已保存', active.dirty ? 'warning' : ''); notify('error', error.message); }
    finally { setFixing(false); updateEditorButtons(); }
  }

  async function save(check) {
    if (!session?.dirty || session.conflict || checking) return;
    const active = session, content = editorValue(); setChecking(true); status("正在保存...", "warning");
    try {
      const result = await api('/api/editor/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report: currentReport, path: active.path, content, state: active.source.state, trailing_newline: active.autoFix ? content.endsWith('\n') : undefined})});
      active.content = content; active.source.state = result.state; active.dirty = false;
      clearAutoFix();
      destroyComparison();
      document.getElementById("save-edit").disabled = true; document.getElementById("save-check").disabled = true;
      status(check ? "已保存，正在检查..." : "已保存，检查结果已过期", check ? "warning" : "warning");
      notify('success', '文件【' + active.path + '】已保存到工作区，尚未提交。');
      currentSourceChanged = true;
      const ignore = document.getElementById('ignore-finding'); if (ignore) ignore.disabled = true;
      const file = document.querySelector('.file[data-path="' + CSS.escape(active.path) + '"] .meta'); if (file && !file.textContent.includes('检查结果已过期')) file.textContent += ' · 检查结果已过期';
      if (check) await recheckEdited(active);
    } catch (error) {
      if (error.message.includes("外部程序修改")) { active.conflict = true; status("外部修改冲突，请重新加载", "error"); }
      notify('error', '文件【' + active.path + '】保存失败：' + error.message);
    } finally { setChecking(false); }
  }

  async function recheckEdited(active) {
    try {
      if (active.source.summary) {
        await api('/api/recheck', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report: currentReport})});
      } else {
        const result = await api('/api/check-selected', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report: currentReport, files: [active.path]})});
        if (result.failed_files) throw new Error('当前文件检查失败，请重试。');
      }
      await originalLoadReport(currentReport, null, true); currentSourceChanged = false; status("已保存并完成检查");
      notify('success', '文件【' + active.path + '】已保存并完成检查。');
    } catch (error) {
      currentSourceChanged = true; status("已保存，但检查失败", "error");
      notify('error', '文件【' + active.path + '】保存成功，但检查失败：' + error.message);
    }
  }

  function showView(view) {
    activeView = view;
    document.getElementById("editor-box")?.classList.toggle("hidden", view !== "editor");
    document.getElementById("source-lines")?.classList.toggle("hidden", view !== "source");
    document.getElementById("preview-box")?.classList.toggle("hidden", view !== "preview");
    document.getElementById("pdf-box")?.classList.toggle("hidden", view !== "pdf");
    for (const [id, name] of [["editor-tab", "editor"], ["source-tab", "source"], ["preview-tab", "preview"], ["pdf-tab", "pdf"]]) document.getElementById(id)?.classList.toggle("active", name === view);
  }

  async function showSource(path, finding = null) {
    if (comparison?.path === path) {
      showView("source");
      select(comparison.editor, finding);
      return;
    }
    destroyComparison();
    const host = document.getElementById("source-lines");
    if (!host) return;
    if (!editorAvailable()) { host.innerHTML = '<div class="panel error">Markdown 编辑器组件加载失败，无法显示完整源码对照。</div>'; showView("source"); return; }
    try {
      const source = await api('/api/editor/source?report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(path));
      host.className = 'editor-box hidden';
      host.innerHTML = '<div class="edit-toolbar"><b>源码对照</b><span class="meta">磁盘已保存版本 · 只读</span></div><div id="source-surface"></div>';
      const editor = MdocEditor.create(document.getElementById('source-surface'), {content: source.content, readOnly: true});
      comparison = {path, editor};
      if (session?.path === path && session.finding) editor.setFinding(session.finding.line, session.finding.column, session.finding.end_line, session.finding.end_column);
      select(editor, finding);
      showView("source");
    } catch (error) { host.innerHTML = '<div class="panel error">无法加载完整源码：' + esc(error.message) + '</div>'; showView("source"); }
  }

  async function previewEditor() {
    if (!session) return;
    const box = document.getElementById("preview-box"), content = editorValue();
    if (box?.querySelector('iframe') && session.previewContent === content) { showView("preview"); return; }
    const rendered = await api('/api/editor/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report: currentReport, path: session.path, content: editorValue()})});
    const doc = new DOMParser().parseFromString(rendered.html, 'text/html');
    doc.querySelectorAll('img[src]').forEach(img => { const src = img.getAttribute('src'); if (src && !/^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i.test(src)) img.setAttribute('src', '/api/preview-resource?token=' + encodeURIComponent(TOKEN) + '&report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(session.path) + '&resource=' + encodeURIComponent(src)); });
    box.innerHTML = ''; const frame = document.createElement('iframe'); frame.className = 'preview'; frame.setAttribute('sandbox', 'allow-same-origin');
    frame.onload = () => frame.contentDocument.addEventListener('click', event => { const anchor = event.target.closest('a[href]'); if (!anchor) return; event.preventDefault(); followEditorLink(anchor.getAttribute('href'), frame); });
    frame.srcdoc = '<style>' + previewStyle + '</style>' + doc.body.innerHTML; box.appendChild(frame); session.previewContent = content; showView("preview");
  }

  renderPreview = async function(path, fragment = '', origin = undefined) {
    if (origin !== undefined) previewOrigin = origin;
    else if (path === currentFinding?.path) previewOrigin = null;
    else if (!previewOrigin && currentFinding?.path) { const source = currentFinding.path; previewOrigin = {path: source, back: () => renderPreview(source)}; }
    await originalRenderPreview(path, fragment);
    if (!previewOrigin || path === previewOrigin.path) return;
    const box = document.getElementById('preview-box'), bar = document.createElement('div');
    bar.className = 'preview-nav'; bar.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f6f8fa;border-bottom:1px solid #d8dee4';
    bar.innerHTML = '<button data-preview-back>返回源文档</button><span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#687078">' + esc(previewOrigin.path) + '</span>';
    const back = previewOrigin.back; bar.querySelector('[data-preview-back]').onclick = () => { previewOrigin = null; back(); }; box.prepend(bar);
  };

  async function followEditorLink(target, frame) {
    try {
      const link = await api('/api/preview-link?report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(session.path) + '&target=' + encodeURIComponent(target));
      if (link.kind === 'anchor') {
        const node = frame.contentDocument.getElementById(link.fragment); node ? node.scrollIntoView() : notify('warning', '预览页内未找到对应位置。');
      } else if (link.kind === 'external') window.open(link.url, '_blank', 'noopener');
      else if (link.kind === 'markdown') { session.previewContent = null; renderPreview(link.path, link.fragment, {path: session.path, back: previewEditor}); }
    } catch (error) { notify('error', '预览链接打开失败：' + error.message); }
  }

  async function installEditorUI(path, finding, existingBox = null) {
    const tabs = detail.querySelector('.tabs'), panel = detail.querySelector('.panel');
    if (!tabs || !panel) return;
    tabs.classList.add('view-tabs');
    const button = document.createElement('button'); button.id = 'editor-tab'; button.textContent = '文件编辑'; tabs.prepend(button);
    const sourceTab = document.getElementById('source-tab'); if (sourceTab) sourceTab.textContent = '源文件';
    const pdfTab = document.createElement('button'); pdfTab.id = 'pdf-tab'; pdfTab.textContent = 'PDF 预览'; document.getElementById('preview-tab')?.after(pdfTab);
    let more = document.getElementById('view-more');
    if (!more) { more = document.createElement('select'); more.id = 'view-more'; more.innerHTML = '<option value="">更多</option><option value="open">打开并定位</option><option value="ignore">忽略/取消忽略</option><option value="default">Windows 默认程序</option><option value="select">重新选择编辑器...</option>'; tabs.appendChild(more); more.onchange = async () => { const value = more.value; more.value = ''; if (value === 'open') document.getElementById('open-source')?.click(); else if (value === 'ignore') document.getElementById('ignore-finding')?.click(); else if (value === 'default') await openSource('windows-default'); else if (value === 'select') await selectEditor(); }; }
    const resize = () => tabs.classList.toggle('compact', tabs.clientWidth < 720); resize(); new ResizeObserver(resize).observe(tabs);
    const box = existingBox || document.createElement('div'); box.id = 'editor-box'; box.className = 'editor-box'; panel.appendChild(box);
    const pdfBox = document.createElement('div'); pdfBox.id = 'pdf-box'; pdfBox.className = 'pdf-box hidden'; panel.appendChild(pdfBox);
    button.onclick = () => loadEditor(path, comparison?.path === path ? mappedSelection(comparison.editor.getSelection(), true) : finding);
    document.getElementById('source-tab').onclick = () => showSource(path, session?.path === path ? mappedSelection(session.editor.getSelection()) : finding);
    document.getElementById('preview-tab').onclick = () => session?.path === path ? previewEditor() : showPreview();
    pdfTab.onclick = () => showPdfPreview(path);
    await loadEditor(path, finding, activeView === 'editor');
    if (activeView === 'source') await showSource(path, finding);
    else if (activeView === 'preview') await previewEditor();
    else if (activeView === 'pdf') await showPdfPreview(path);
  }

  openFile = async function(path, box) {
    if (fixing) return guard(() => openFile(path, box));
    if (session?.path !== path && dirty()) return guard(() => openFile(path, box));
    await originalOpenFile(path, box);
  };
  showFinding = async function(finding, button) {
    if (fixing) return guard(() => showFinding(finding, button));
    if (session?.path !== finding.path && dirty()) return guard(() => showFinding(finding, button));
    if (!/\.(?:md|markdown)$/i.test(finding.path)) {
      destroyEditor(); destroyComparison(); currentFinding = finding;
      document.querySelectorAll('.finding').forEach(node => node.classList.remove('active')); button?.classList.add('active');
      try {
        const resource = await api('/api/resource-info?report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(finding.path));
        const preview = resource.image ? '<img class="resource-preview" src="/api/report-resource?token=' + encodeURIComponent(TOKEN) + '&report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(finding.path) + '" alt="' + esc(resource.name) + '">' : '';
        detail.innerHTML = '<div class="tabs"><button id="open-resource">Windows 默认程序打开</button><button id="ignore-finding" ' + (currentSourceChanged || finding.mandatory ? 'disabled' : '') + '>' + (finding.ignore_id ? '取消忽略' : '忽略此问题') + '</button></div><div class="panel"><div class="help ' + (finding.ignore_id ? 'ignored' : '') + '"><div class="help-head"><h2>' + esc(finding.help.title) + '</h2><span class="meta help-rule">' + esc(finding.rule) + '</span></div><div class="help-grid"><p class="help-item"><b>问题说明</b>' + esc(finding.help.description) + '</p><p class="help-item"><b>修改建议</b>' + esc(finding.help.suggestion) + '</p></div><div class="meta help-original">原始信息：' + esc(finding.help.original) + '</div></div><div class="resource-info"><b>' + esc(resource.path) + '</b><div class="meta">' + esc(resource.format || resource.extension || '未知格式') + (resource.image ? ' · ' + resource.width + ' × ' + resource.height + ' px' : '') + ' · ' + (resource.size / 1024).toFixed(1) + ' KiB</div></div>' + preview + '</div>';
        document.getElementById('open-resource').onclick = () => api('/api/resource/open-default', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({report: currentReport, path: finding.path})}).catch(error => notify('error', '资源打开失败：' + error.message));
        document.getElementById('ignore-finding').onclick = toggleIgnore;
      } catch (error) { notify('error', '资源【' + finding.path + '】加载失败：' + error.message); }
      return;
    }
    const existing = session?.path === finding.path ? document.getElementById('editor-box') : null, existingSource = comparison?.path === finding.path ? document.getElementById('source-lines') : null, sourceActive = existingSource && !existingSource.classList.contains('hidden');
    if (comparison && !existingSource) destroyComparison();
    existing?.remove(); existingSource?.remove(); await originalShowFinding(finding, button);
    if (existingSource) { document.getElementById('source-lines')?.remove(); detail.querySelector('.panel')?.appendChild(existingSource); select(comparison.editor, finding); }
    if (sourceActive) activeView = 'source';
    await installEditorUI(finding.path, finding, existing);
  };
  loadReport = async function(id, button, keepSelection = false) {
    if (fixing) return guard(() => loadReport(id, button, keepSelection));
    if (currentReport && id !== currentReport && dirty()) return guard(() => loadReport(id, button, keepSelection));
    destroyEditor(); destroyComparison(); return originalLoadReport(id, button, keepSelection);
  };

  const pdfRequest = (path, value = {}) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  const pdfUrl = (kind, key) => '/api/pdf/file?token=' + encodeURIComponent(TOKEN) + '&kind=' + encodeURIComponent(kind) + '&key=' + encodeURIComponent(key) + '&t=' + Date.now();
  const formatTime = value => value ? new Date(value * 1000).toLocaleString() : '未知';
  const formatSize = value => value ? (value / 1024 / 1024).toFixed(2) + ' MiB' : '0 MiB';
  function pdfOrigin(path = null) { return {report: currentReport, path, finding_id: currentFinding?.finding_id || null, view: activeView, offset: currentOffset, query: search.value, query_scope: queryScope.value, severity: severity.value, state: state.value}; }
  function resultActions(kind, key) { return '<div class="pdf-actions"><button data-pdf="browser">在浏览器中打开</button><button data-pdf="default">Windows 默认程序</button><button data-pdf="save">另存为</button><button data-pdf="folder">打开所在文件夹</button></div>'; }
  function bindResultActions(host, kind, key) {
    host.querySelector('[data-pdf=browser]')?.addEventListener('click', () => window.open(pdfUrl(kind, key), '_blank', 'noopener'));
    host.querySelector('[data-pdf=default]')?.addEventListener('click', () => pdfRequest('/api/pdf/open-default', {kind, key}).catch(error => notify('error', error.message)));
    host.querySelector('[data-pdf=save]')?.addEventListener('click', async () => { try { const result = await pdfRequest('/api/pdf/save-as', {kind, key}); if (result.status === 'saved') notify('success', 'PDF 已保存到：' + result.path); } catch (error) { notify('error', error.message); } });
    host.querySelector('[data-pdf=folder]')?.addEventListener('click', () => pdfRequest('/api/pdf/open-folder', {kind, key}).catch(error => notify('error', error.message)));
  }
  function renderPdfResult(box, context) {
    const result = context.result, environment = context.environment || {};
    if (!context.enabled) { box.innerHTML = '<p class="warning">' + esc(context.reason || 'PDF 预览不可用。') + '</p>'; return; }
    if (environment.status !== 'passed') { box.innerHTML = '<p class="error">PDF 环境不可用：' + esc(environment.reason || environment.status) + '</p><button id="pdf-doctor">重新检查 PDF 环境</button>'; box.querySelector('#pdf-doctor').onclick = async () => { await pdfRequest('/api/pdf/doctor'); showPdfPreview(context.path); }; return; }
    const occurrences = context.occurrences || [], selected = pdfContext?.occurrence && occurrences.some(item => item.id === pdfContext.occurrence) ? pdfContext.occurrence : occurrences[0]?.id || '';
    box.innerHTML = '<div class="pdf-toolbar"><select id="pdf-scope"><option value="page">当前页面</option><option value="section"' + (!context.section_available ? ' disabled' : '') + '>当前章节</option></select>' + (occurrences.length > 1 ? '<select id="pdf-occurrence">' + occurrences.map(item => '<option value="' + esc(item.id) + '"' + (item.id === selected ? ' selected' : '') + '>' + esc(item.breadcrumb) + '</option>').join('') + '</select>' : '') + '<button id="build-file-pdf"' + (!context.page_available || dirty() ? ' disabled' : '') + '>' + (result?.available ? '重新生成' : '生成 PDF') + '</button><span class="meta">' + (dirty() ? '请先保存当前编辑内容' : esc(context.reason || 'PDF 使用磁盘上已保存的 Markdown')) + '</span></div>';
    if (context.job?.status === 'failed' && context.job.path === context.path) box.insertAdjacentHTML('beforeend', '<details open><summary class="error">上次 PDF 构建失败：' + esc(context.job.error) + '</summary>' + (context.job.log ? '<pre class="pdf-log">' + esc(context.job.log) + '</pre><button data-copy-job-log>复制日志</button>' : '') + '</details>');
    if (result?.available) { box.insertAdjacentHTML('beforeend', '<div class="pdf-meta"><b>' + (result.scope === 'section' ? '章节' : '页面') + ' PDF</b><span>' + esc(result.title) + '</span><span>' + esc(result.book + ' / ' + result.locale) + '</span><span>' + formatTime(result.generated_at) + '</span><span>' + formatSize(result.size) + '</span><span class="' + (result.stale ? 'warning' : '') + '">' + (result.stale ? '已过时' : '当前') + '</span></div>' + resultActions('file', context.key) + '<iframe class="pdf-frame" src="' + pdfUrl('file', context.key) + '"></iframe>' + ((result.findings?.length || result.log) ? '<details><summary>上次构建记录</summary><pre class="pdf-log">' + esc((result.findings || []).map(item => item.message || item).join('\n') + (result.log ? '\n' + result.log : '')) + '</pre><button data-copy-log>复制日志</button></details>' : '')); bindResultActions(box, 'file', context.key); box.querySelector('[data-copy-log]')?.addEventListener('click', () => navigator.clipboard.writeText(box.querySelector('.pdf-log').textContent)); }
    const scope = box.querySelector('#pdf-scope'); if (pdfContext?.scope && (pdfContext.scope !== 'section' || context.section_available)) scope.value = pdfContext.scope; else scope.value = 'page';
    box.querySelector('[data-copy-job-log]')?.addEventListener('click', () => navigator.clipboard.writeText(context.job.log)); box.querySelector('#build-file-pdf')?.addEventListener('click', () => startPdfBuild({scope: scope.value, path: context.path, occurrence: box.querySelector('#pdf-occurrence')?.value || selected, origin: pdfOrigin(context.path)}));
  }
  async function showPdfPreview(path) {
    if (dirty()) notify('warning', '当前文件有未保存修改，PDF 只能使用已保存内容生成。');
    showView('pdf'); const box = document.getElementById('pdf-box'); if (!box) return; box.innerHTML = '<p>正在加载 PDF 预览...</p>';
    try { const context = await api('/api/pdf/context?report=' + encodeURIComponent(currentReport) + '&path=' + encodeURIComponent(path)); pdfContext = {...context, scope: pdfContext?.path === path ? pdfContext.scope : 'page', occurrence: pdfContext?.path === path ? pdfContext.occurrence : context.occurrences?.[0]?.id}; renderPdfResult(box, context); } catch (error) { box.innerHTML = '<p class="error">' + esc(error.message) + '</p>'; }
  }
  async function startPdfBuild(request) {
    try { pdfContext = {...pdfContext, path: request.path, scope: request.scope, occurrence: request.occurrence}; await pdfRequest('/api/pdf/build', {report: currentReport, ...request}); notify('warning', '已开始生成' + (request.scope === 'book' ? '整册' : request.scope === 'section' ? '当前章节' : '当前页面') + ' PDF。'); pollPdf(); } catch (error) { notify('error', 'PDF 构建启动失败：' + error.message); }
  }
  function pollPdf() { clearTimeout(pdfTimer); pdfTimer = setTimeout(async () => { try { const job = await api('/api/pdf/status'); if (!['completed', 'failed', 'idle'].includes(job.status)) { notify('warning', job.message || '正在生成 PDF...'); return pollPdf(); } if (job.id && job.id !== completedPdfJob) { completedPdfJob = job.id; if (job.status === 'failed') notify('error', 'PDF 构建失败：' + job.error); else finishPdf(job); } } catch (error) { notify('error', 'PDF 构建状态获取失败：' + error.message); } }, 1000); }
  function finishPdf(job) {
    const same = currentReport === job.report && (!job.path || currentFinding?.path === job.path) && !dirty();
    if (same && job.scope !== 'book') { notify('success', 'PDF 构建完成。'); showPdfPreview(job.path); return; }
    noticeText.innerHTML = '<span>PDF 构建完成：' + esc(job.result?.title || job.scope) + '</span><span class="notice-actions"><button data-open-result>打开 PDF</button>' + (job.origin?.path ? '<button data-return-origin>返回发起位置</button>' : '') + '</span>'; notice.className = 'notice success';
    notice.querySelector('[data-open-result]').onclick = () => window.open(pdfUrl(job.scope === 'book' ? 'book' : 'file', job.scope === 'book' ? job.result.book + '/' + job.result.locale : job.result.key || pdfContext?.key), '_blank', 'noopener');
    notice.querySelector('[data-return-origin]')?.addEventListener('click', () => returnToOrigin(job.origin));
  }
  async function returnToOrigin(origin) {
    search.value = origin.query || ''; queryScope.value = origin.query_scope || 'all'; severity.value = origin.severity || ''; state.value = origin.state || 'active'; currentOffset = origin.offset || 0;
    focusPaths.clear(); focusPaths.add(origin.path); await loadReport(origin.report, null, true); const file = document.querySelector('.file[data-path="' + CSS.escape(origin.path) + '"]'); if (file) await openFile(origin.path, file.nextElementSibling);
    if (origin.finding_id) { const data = await api('/api/file?report=' + encodeURIComponent(origin.report) + '&path=' + encodeURIComponent(origin.path)), index = data.findings.findIndex(item => item.finding_id === origin.finding_id); if (index >= 0) document.querySelector('.finding[data-index="' + index + '"]')?.click(); }
  }
  document.getElementById('build-book-pdf').onclick = async () => { try { const context = await api('/api/pdf/book-context?report=' + encodeURIComponent(currentReport)); if (!context.enabled) throw new Error(context.reason); await startPdfBuild({scope: 'book', path: null, occurrence: null, origin: pdfOrigin()}); } catch (error) { notify('error', '无法生成整册 PDF：' + error.message); } };
  document.getElementById('book-pdf-actions').onchange = async event => { const action = event.target.value; event.target.value = ''; if (!action) return; try { const context = await api('/api/pdf/book-context?report=' + encodeURIComponent(currentReport)); if (!context.result?.available) throw new Error('当前报告没有可用的整册 PDF 结果。'); if (action === 'browser') window.open(pdfUrl('book', context.key), '_blank', 'noopener'); else await pdfRequest('/api/pdf/' + (action === 'default' ? 'open-default' : action === 'save' ? 'save-as' : 'open-folder'), {kind: 'book', key: context.key}); } catch (error) { notify('error', '整册 PDF 操作失败：' + error.message); } };
  pollPdf();

  setChecking = function(active) {
    originalSetChecking(active);
    updateEditorButtons();
  };
  const previewStyle = "*{box-sizing:border-box}body{margin:0 auto;padding:24px 32px 60px;max-width:1100px;color:#24292f;background:#fff;font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif}h1,h2,h3,h4,h5,h6{margin:1.35em 0 .55em;padding:0;font-weight:650;line-height:1.3}h1{font-size:2em;border-bottom:1px solid #d8dee4;padding-bottom:.3em}h2{font-size:1.5em;border-bottom:1px solid #d8dee4;padding-bottom:.25em}blockquote{margin:1em 0;padding:.6em 1em;color:#57606a;background:#f6f8fa;border-left:4px solid #8c959f}ul,ol{padding-left:2em}img{max-width:100%;height:auto}p>img:only-child,div[align=center]>img,div[align='center']>img{display:block;margin:1em auto}table{display:block;width:max-content;max-width:100%;overflow:auto;border-collapse:collapse}td,th{border:1px solid #d0d7de;padding:6px 13px}code{padding:.15em .35em;background:#eff1f3}pre{overflow:auto;background:#f6f8fa;padding:16px}";
  window.addEventListener('beforeunload', event => { if (dirty() || fixing) { event.preventDefault(); event.returnValue = ''; } });
})();
