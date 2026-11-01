(() => {
  const elements = {
    apiNotice: document.getElementById('api-notice'),
    runNotice: document.getElementById('run-notice'),
    runButton: document.getElementById('run-button'),
    runButtonText: document.querySelector('#run-button span'),
    runView: document.getElementById('run-view'),
    connection: document.getElementById('connection-state'),
    connectionLabel: document.getElementById('connection-label'),
    taskList: document.getElementById('task-list'),
    taskCount: document.getElementById('task-count'),
    taskFooter: document.getElementById('task-footer-text'),
    detail: document.getElementById('detail-panel'),
    modelsStrip: document.getElementById('models-strip'),
    modelList: document.getElementById('model-list'),
    harborDescription: document.getElementById('harbor-description'),
    harborResults: document.getElementById('harbor-results'),
    harborPassed: document.getElementById('harbor-passed'),
    harborScore: document.getElementById('harbor-score'),
    harborStatus: document.getElementById('harbor-status'),
    harborTaskDetails: document.getElementById('harbor-task-details'),
    harborTaskList: document.getElementById('harbor-task-list'),
    accuracy: document.getElementById('stat-accuracy'),
    accuracyFoot: document.getElementById('stat-accuracy-foot'),
    completed: document.getElementById('stat-completed'),
    total: document.getElementById('stat-total'),
    passed: document.getElementById('stat-passed'),
    review: document.getElementById('stat-review')
  };

  const state = {
    results: null,
    currentResults: null,
    originalResults: null,
    originalError: null,
    view: 'current',
    selectedKey: null,
    loading: true,
    fetching: false,
    running: false,
    payloadSnapshot: null,
    harborSummary: null,
    currentHarborSummary: null,
    harborSnapshot: null,
    harborError: null,
    currentHarborError: null,
    harborFetching: { current: false, original: false },
    originalHarborSummary: null,
    originalHarborError: null
  };

  const roleLabels = {
    claimant: 'Claimant',
    adjudicator: 'Adjudicator',
    image: 'Image',
    voice: 'Voice'
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function valueText(value, fallback = '—') {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return fallback;
    }
  }

  function resultsTasks() {
    return Array.isArray(state.results?.tasks) ? state.results.tasks : [];
  }

  function verdictLabel(value, fallback = 'Pending') {
    if (value === 'needs_review') return 'Requires further review';
    if (value === 'covered') return 'Covered';
    if (value === 'excluded') return 'Excluded';
    return valueText(value, fallback);
  }

  function lunaResult(task) {
    if (state.view !== 'current') return null;
    const result = decisionComparison?.tasks?.find(item => item.id === task.id);
    if (task.document_retrieval_required && (!task.policy_retrieval || JSON.stringify(result?.source_policy_retrieval) !== JSON.stringify(task.policy_retrieval))) return null;
    if (!result || result.status !== 'complete' || JSON.stringify(result.source_policy) !== JSON.stringify(task.policy) ||
        JSON.stringify(result.source_transcript) !== JSON.stringify((task.transcript || []).filter(turn => turn.phase !== 'decision').map(turn => turn.text)) ||
        result.source_image !== task.image || JSON.stringify(result.source_damage_annotations ?? null) !== JSON.stringify(task.image ? task.damage_annotations ?? null : null)) return null;
    return result;
  }

  function taskMatch(task) {
    if (state.view === 'current') {
      const luna = lunaResult(task);
      if (!luna || !task?.expected?.verdict) return { label: 'Not scored', result: 'unknown' };
      return luna.verdict === task.expected.verdict ? {label: 'Matched', result: 'pass'} : {label: 'Mismatched', result: 'fail'};
    }
    const metrics = task?.metrics || {};
    if (metrics.verdict_correct === false || metrics.clause_correct === false) {
      return { label: 'Mismatched', result: 'fail' };
    }
    if (metrics.verdict_correct === true && metrics.clause_correct === true) {
      return { label: 'Matched', result: 'pass' };
    }
    return { label: 'Not scored', result: 'unknown' };
  }

  function policyClauses(task) {
    const policy = task?.policy;
    if (Array.isArray(policy)) return policy;
    if (Array.isArray(policy?.clauses)) return policy.clauses;
    return [];
  }

  function clauseId(clause) {
    if (!clause || typeof clause !== 'object') return clause;
    return clause.id ?? clause.clause_id ?? clause.code;
  }

  function clauseText(clause) {
    if (!clause || typeof clause !== 'object') return clause;
    return clause.text ?? clause.description ?? clause.content ?? clause.body;
  }

  function clauseAnchorId(id) {
    const token = Array.from(String(id), (character) => character.codePointAt(0).toString(16)).join('-');
    return `policy-${token}`;
  }

  function isRecorded(value) {
    return value !== null && value !== undefined && value !== '';
  }

  function missingValue(label) {
    return state.view === 'original' ? 'Not recorded in original run' : `${label} not returned.`;
  }

  function taskKey(task, index) {
    return task?.id === undefined || task?.id === null ? `task-row-${index}` : String(task.id);
  }

  function statusInfo(value) {
    const status = String(value || 'pending').toLowerCase();
    if (['complete', 'completed', 'done', 'passed'].includes(status)) return { label: 'Complete', className: 'status-complete' };
    if (['running', 'processing', 'in_progress', 'in progress', 'active'].includes(status)) return { label: 'Running', className: 'status-running' };
    if (status === 'failed' || status === 'failure') return { label: 'Failed', className: 'status-error' };
    if (status === 'error') return { label: 'Error', className: 'status-error' };
    if (['pending', 'queued', 'waiting'].includes(status)) return { label: 'Pending', className: '' };
    return { label: String(value), className: '' };
  }

  function accuracyText(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'string' && value.trim().endsWith('%')) return value.trim();
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return valueText(value);
    const percentage = numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
    return `${Number.isInteger(percentage) ? percentage : percentage.toFixed(1)}%`;
  }

  function updateConnection(mode, label) {
    elements.connection.classList.toggle('is-connected', mode === 'connected');
    elements.connection.classList.toggle('is-error', mode === 'error');
    elements.connectionLabel.textContent = label;
  }

  function setApiError(message) {
    elements.apiNotice.hidden = !message;
    elements.apiNotice.textContent = message || '';
  }

  function setRunNotice(message, tone = 'info') {
    elements.runNotice.hidden = !message;
    elements.runNotice.textContent = message || '';
    elements.runNotice.classList.toggle('is-error', tone === 'error');
  }

  function updateRunStatus() {
    const status = String(state.results?.status || '').toLowerCase();
    const activeStatus = String(state.currentResults?.status || status).toLowerCase();
    const active = state.running || ['running', 'processing', 'in_progress', 'in progress', 'active'].includes(activeStatus);
    elements.runButton.disabled = active;
    elements.runButton.classList.toggle('is-running', active);
    elements.runButton.setAttribute('aria-busy', String(active));
    elements.runButtonText.textContent = active ? 'Running conversations…' : 'Run conversations';

    if (state.running) return;
    if (active) {
      setRunNotice('Conversation run in progress. Results refresh automatically.');
    } else if (['complete', 'completed', 'done'].includes(status)) {
      setRunNotice('Conversation run complete. Luna results are scored separately.');
    } else if (['failed', 'failure', 'error'].includes(status)) {
      const message = state.results?.error;
      setRunNotice(message ? `Benchmark failed: ${valueText(message)}` : 'Benchmark failed.', 'error');
    } else {
      setRunNotice('');
    }
  }

  function renderStats() {
    const tasks = resultsTasks();
    const scored = tasks.filter(task => lunaResult(task));
    const correct = scored.filter(task => lunaResult(task).verdict === task.expected?.verdict).length;
    const benchmark = state.view === 'current'
      ? {completed: scored.length, total: tasks.length, passed: correct, accuracy: scored.length ? correct / scored.length : null}
      : state.results?.benchmark || {};
    const completed = Number(benchmark.completed);
    const hasCompletedCases = Number.isFinite(completed) && completed > 0;
    const hasAccuracy = benchmark.accuracy !== undefined && benchmark.accuracy !== null && hasCompletedCases;
    elements.accuracy.textContent = hasAccuracy ? accuracyText(benchmark.accuracy) : '—';
    elements.accuracyFoot.textContent = hasAccuracy ? (state.view === 'current' ? 'Luna verdicts vs expected labels' : 'Original Astra verdict accuracy') : 'Awaiting valid Luna verdicts';
    elements.completed.textContent = valueText(benchmark.completed);
    elements.total.textContent = valueText(benchmark.total);
    elements.passed.textContent = valueText(benchmark.passed);
    elements.review.textContent = String(tasks.length);
    const primaryLabel = document.querySelector('.stat-primary .stat-top span');
    if (primaryLabel) primaryLabel.textContent = state.view === 'current' ? 'Luna verdict accuracy' : 'Original Astra verdict accuracy';
    const statCards = document.querySelectorAll('.stat-card');
    if (statCards[1]) statCards[1].querySelector('.stat-top span').textContent = state.view === 'current' ? 'Luna verdicts scored' : 'Original Astra cases scored';
    if (statCards[2]) {
      statCards[2].querySelector('.stat-top span').textContent = state.view === 'current' ? 'Luna correct verdicts' : 'Original Astra correct verdicts';
      statCards[2].querySelector('.stat-foot').textContent = state.view === 'current' ? 'Luna verdicts vs expected labels' : 'Original Astra results';
    }
    document.querySelector('.harbor-copy strong').textContent = state.view === 'current' ? 'Harbor · Luna verdict replay' : 'Harbor · original Astra replay';
  }

  function renderModels() {
    const models = state.results?.models;
    elements.modelList.replaceChildren();
    if (!models || typeof models !== 'object' || Array.isArray(models)) {
      elements.modelsStrip.hidden = true;
      return;
    }

    const entries = Object.entries(roleLabels)
      .filter(([key]) => Object.prototype.hasOwnProperty.call(models, key));
    if (!entries.length) {
      elements.modelsStrip.hidden = true;
      return;
    }

    if (state.view === 'current' && decisionComparison?.model) {
      const chip = node('div', 'model-chip');
      chip.append(node('span', '', 'Decision benchmark'), node('strong', '', `${decisionComparison.model} · Decisions API`));
      elements.modelList.append(chip);
    }
    for (const [key, label] of entries) {
      const chip = node('div', 'model-chip');
      chip.append(node('span', '', label), node('strong', '', valueText(models[key], 'Not reported')));
      elements.modelList.append(chip);
    }
    elements.modelsStrip.hidden = false;
  }

  function renderHarbor() {
    const summary = state.harborSummary;
    elements.harborStatus.className = 'harbor-status';
    elements.harborResults.hidden = true;
    elements.harborTaskDetails.hidden = true;
    elements.harborTaskList.replaceChildren();

    if (state.harborError) {
      elements.harborDescription.textContent = `Replay summary unavailable: ${state.harborError}`;
      elements.harborStatus.textContent = 'Unavailable';
      elements.harborStatus.classList.add('status-error');
      return;
    }

    if (!summary) {
      elements.harborDescription.textContent = 'Harbor replay pending.';
      elements.harborStatus.textContent = 'Pending';
      return;
    }

    const status = statusInfo(summary.status);
    const mode = state.view === 'original' ? 'Original Astra saved-output replay' : valueText(summary.mode, 'Luna Decisions API saved-verdict replay');
    elements.harborStatus.textContent = summary.error && status.label !== 'Failed' ? 'Error' : status.label;
    const harborClass = summary.error ? 'status-error' : status.className;
    if (harborClass) elements.harborStatus.classList.add(harborClass);
    elements.harborDescription.textContent = summary.error
      ? `${mode}: ${valueText(summary.error)}`
      : mode;

    let isComplete = ['complete', 'completed', 'done'].includes(String(summary.status || '').toLowerCase()) && !summary.error;
    if (isComplete && state.view === 'current') {
      const cases = resultsTasks();
      const rewards = Array.isArray(summary.tasks) ? summary.tasks : [];
      const valid = summary.endpoint === '/v1/decisions' && cases.length === rewards.length && cases.every(task => {
        const luna = lunaResult(task);
        const reward = rewards.find(item => item.id === task.id);
        return luna && reward && reward.verdict === luna.verdict && reward.expected === task.expected?.verdict;
      });
      if (!valid) {
        isComplete = false;
        elements.harborDescription.textContent = 'Saved Harbor results are pending or based on earlier evidence.';
        elements.harborStatus.textContent = 'Pending';
      }
    }
    if (isComplete && summary.passed !== null && summary.passed !== undefined) {
      elements.harborPassed.textContent = `${valueText(summary.passed)} / ${valueText(summary.total)}`;
    } else {
      elements.harborPassed.textContent = '';
    }
    if (isComplete && summary.score !== null && summary.score !== undefined) {
      const score = Number(summary.score);
      const scoreText = Number.isFinite(score) && score >= 0 && score <= 1
        ? `${Math.round(score * 100)}%`
        : valueText(summary.score);
      elements.harborScore.textContent = `Score ${scoreText}`;
    } else {
      elements.harborScore.textContent = '';
    }
    const tasks = isComplete && Array.isArray(summary.tasks) ? summary.tasks : [];
    tasks.forEach((task) => {
      const item = node('span', 'harbor-task-reward');
      item.append(
        node('strong', '', valueText(task?.id, 'Task')),
        node('span', '', valueText(task?.reward, '—'))
      );
      elements.harborTaskList.append(item);
    });
    elements.harborTaskDetails.hidden = tasks.length === 0;
    elements.harborResults.hidden = false;
  }

  function makeImageIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M2.2 3.5h11.6v9H2.2zM4 10l2.3-2.4 1.8 1.7 1.5-1.4 2.4 2.8M10.4 5.8h.01');
    svg.append(path);
    return svg;
  }

  function renderTasks() {
    const tasks = resultsTasks();
    elements.taskList.replaceChildren();
    elements.taskCount.textContent = state.results ? String(tasks.length) : '—';
    elements.taskFooter.textContent = state.results
      ? `${state.view === 'current' ? tasks.filter(task => lunaResult(task)).length : valueText(state.results?.benchmark?.completed)} of ${tasks.length} verdicts evaluated`
      : 'Waiting for benchmark data';

    if (!state.results && state.loading) {
      const loading = node('div', 'loading-state');
      loading.append(node('span', 'loader'), node('span', '', 'Loading task results…'));
      elements.taskList.append(loading);
      return;
    }

    if (!tasks.length) {
      const empty = node('div', 'empty-list');
      empty.append(
        node('strong', '', state.results ? 'No task records yet' : 'Results unavailable'),
        node('span', '', state.results
          ? 'Task details will appear here when the results endpoint returns them.'
          : 'The task list will appear after results can be loaded.')
      );
      elements.taskList.append(empty);
      return;
    }

    tasks.forEach((task, index) => {
      const key = taskKey(task, index);
      const button = node('button', 'task-item');
      button.type = 'button';
      button.dataset.taskKey = key;
      button.setAttribute('aria-pressed', String(key === state.selectedKey));

      const top = node('span', 'task-item-top');
      const badges = node('span', 'task-badges');
      const match = taskMatch(task);
      top.append(
        node('span', 'task-category', valueText(task?.category, 'Claim review')),
        badges
      );
      badges.append(
        node('span', `task-match is-${match.result}`, match.label),
        node('span', `task-status ${statusInfo(task?.status).className}`, statusInfo(task?.status).label)
      );
      button.append(top, node('span', 'task-title', valueText(task?.title, 'Untitled task')));

      const bottom = node('span', 'task-item-bottom');
      if (task?.has_image) {
        const imageFlag = node('span', 'task-image-flag');
        imageFlag.append(makeImageIcon(), node('span', '', 'Image evidence'));
        bottom.append(imageFlag);
      } else {
        bottom.append(node('span', '', 'Benchmark case'));
      }
      const metrics = task?.metrics || {};
      if (state.view === 'original' && typeof metrics.score === 'number') bottom.append(node('span', '', `Astra score ${valueText(metrics.score)}`));
      button.append(bottom);
      elements.taskList.append(button);
    });
  }

  let decisionComparison = null;

  function renderDecision(task, parent) {
    const decision = task?.decision || {};
    const card = node('section', 'decision-card');
    const heading = node('div', 'subsection-heading');
    const match = taskMatch(task);
    const resultLabel = match.result === 'pass' ? 'PASS · Matched'
      : match.result === 'fail' ? 'FAIL · Mismatched' : 'Not scored';
    heading.append(node('h3', '', 'Decision comparison'), node('span', `comparison-result is-${match.result}`, resultLabel));
    card.append(heading);

    const grid = node('div', 'comparison-grid');
    const expected = task?.expected || {};
    const expectedCard = node('div', 'comparison-card');
    expectedCard.append(node('h4', '', 'Expected'));
    appendComparisonField(expectedCard, 'Verdict', isRecorded(expected.verdict) ? verdictLabel(expected.verdict) : missingValue('Expected verdict'), 'comparison-verdict');
    appendComparisonClauses(expectedCard, 'Policy clause IDs', expected.clause_ids);
    appendComparisonField(expectedCard, 'Rationale', isRecorded(expected.rationale)
      ? expected.rationale
      : (state.view === 'original' ? missingValue('Expected rationale') : 'Not provided.'), '');

    const actualCard = node('div', 'comparison-card astra-card');
    actualCard.append(node('h4', '', 'Astra · conversation and explanation'));
    appendComparisonField(actualCard, 'Verdict', isRecorded(decision.verdict) ? verdictLabel(decision.verdict) : missingValue('Astra verdict'), 'comparison-verdict');
    appendComparisonField(actualCard, 'Reason', isRecorded(decision.reason) ? decision.reason : missingValue('Reason'), '');
    appendComparisonClauses(actualCard, 'Policy clause IDs', decision.clause_ids);
    const confidence = decision.confidence;
    let confidenceLabel = missingValue('Confidence');
    if (isRecorded(confidence)) {
      const numeric = Number(confidence);
      const percentage = Number.isFinite(numeric) && numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
      confidenceLabel = Number.isFinite(percentage)
        ? `${Number.isInteger(percentage) ? percentage : percentage.toFixed(1)}%`
        : valueText(confidence);
    }
    appendComparisonField(actualCard, 'Confidence', confidenceLabel, '');
    if (isRecorded(confidence)) actualCard.append(node('span', 'confidence-note', 'Model self-assessment of this outcome, not probability of coverage; uncalibrated.'));
    grid.append(expectedCard);
    if (state.view === 'current') {
      const result = decisionComparison?.tasks?.find(item => item.id === task.id);
      const apiCard = node('div', 'comparison-card');
      apiCard.append(node('h4', '', 'Luna Decisions API · benchmarked verdict'));
      const currentEvidence = Boolean(lunaResult(task));
      if (result?.status === 'complete' && currentEvidence) {
        appendComparisonField(apiCard, 'Verdict', verdictLabel(result.verdict), 'comparison-verdict');
        appendComparisonField(apiCard, 'Expected outcome', result.verdict === task.expected?.verdict ? 'Match' : 'Mismatch', '');
        appendComparisonField(apiCard, 'Astra comparison', result.verdict === task.decision?.verdict ? 'Agrees with Astra' : 'Differs from Astra', '');
        appendComparisonField(apiCard, 'Latency', `${result.latency_seconds} s`, '');
        appendComparisonField(apiCard, 'Confidence', accuracyText(result.confidence), '');
        apiCard.append(node('span', 'confidence-note', 'Model confidence is uncalibrated. This is a separate classification, not a replacement for Astra’s explanation.'));
      } else {
        apiCard.append(node('p', 'pending-copy', result?.error || (result && !currentEvidence ? 'Saved comparison is from an earlier claim run.' : 'Decision comparison pending.')));
      }
      grid.append(apiCard);
    }
    grid.append(actualCard);
    card.append(grid);
    parent.append(card);
  }

  function appendComparisonField(parent, label, value, valueClass) {
    const field = node('div', 'comparison-field');
    field.append(node('span', 'decision-label', label));
    field.append(node('span', `decision-value${valueClass ? ` ${valueClass}` : ''}`, valueText(value)));
    parent.append(field);
  }

  function appendComparisonClauses(parent, label, clauses) {
    const field = node('div', 'comparison-field');
    field.append(node('span', 'decision-label', label));
    if (Array.isArray(clauses)) {
      if (clauses.length) {
        const list = node('div', 'clause-list');
        clauses.forEach((clause) => list.append(node('span', 'clause-pill', valueText(clauseId(clause), valueText(clause)))));
        field.append(list);
      } else {
        field.append(node('span', 'decision-value', 'None cited'));
      }
    } else {
      field.append(node('span', 'decision-value', missingValue(label)));
    }
    parent.append(field);
  }

  function renderEvidence(task, parent) {
    const section = node('section', 'detail-section');
    const heading = node('div', 'subsection-heading');
    heading.append(node('h3', '', 'Evidence cited'), node('span', 'section-tag', 'LINKED SOURCES'));
    section.append(heading);
    const evidence = task?.decision?.evidence;
    if (!Array.isArray(evidence)) {
      section.append(node('p', 'pending-copy', missingValue('Evidence citations')));
      parent.append(section);
      return;
    }
    if (!evidence.length) {
      section.append(node('p', 'pending-copy', 'No evidence citations returned for this decision.'));
      parent.append(section);
      return;
    }

    const list = node('div', 'evidence-list');
    evidence.forEach((item) => {
      const entry = node('article', 'evidence-entry');
      const source = String(item?.source || '');
      const sourceRow = node('div', 'evidence-source');
      const sourceLabel = source === 'image' ? 'Image evidence' : /^turn-\d+$/.test(source) ? `Transcript ${source}` : valueText(source, 'Evidence source');
      if (source === 'image' || /^turn-\d+$/.test(source)) {
        const link = node('a', '', sourceLabel);
        link.href = source === 'image' ? '#claim-image' : `#${source}`;
        sourceRow.append(link);
      } else {
        sourceRow.append(node('span', '', sourceLabel));
      }
      entry.append(sourceRow, node('p', 'evidence-observation', valueText(item?.observation, 'No observation text returned.')));

      const clauseIds = Array.isArray(item?.clause_ids) ? item.clause_ids : [];
      if (clauseIds.length) {
        const clauses = node('div', 'evidence-clauses');
        clauseIds.forEach((id) => {
          const policyClause = policyClauses(task).find((clause) => String(clauseId(clause)) === String(id));
          const clauseRow = node('div', 'evidence-clause');
          const link = node('a', 'clause-pill', valueText(id));
          link.href = `#${clauseAnchorId(id)}`;
          clauseRow.append(link);
          const text = clauseText(policyClause);
          clauseRow.append(node('span', 'evidence-clause-text', isRecorded(text) ? valueText(text) : 'Clause text unavailable in the returned policy.'));
          clauses.append(clauseRow);
        });
        entry.append(clauses);
      }
      list.append(entry);
    });
    section.append(list);
    parent.append(section);
  }

  function renderReviewNotes(task, parent) {
    const decision = task?.decision || {};
    const section = node('section', 'detail-section');
    const heading = node('div', 'subsection-heading');
    heading.append(node('h3', '', 'Review notes'), node('span', 'section-tag', 'UNCERTAINTY AND NEXT STEP'));
    section.append(heading);
    const grid = node('div', 'review-notes-grid');
    const missing = decision.missing_information;
    const missingText = Array.isArray(missing)
      ? (missing.length ? missing.map((item) => `• ${valueText(item)}`).join('\n') : 'None reported.')
      : (isRecorded(missing) ? valueText(missing) : missingValue('Missing information'));
    const uncertainty = isRecorded(decision.uncertainty) ? valueText(decision.uncertainty) : missingValue('Uncertainty');
    const nextAction = isRecorded(decision.next_action) ? valueText(decision.next_action) : missingValue('Next step');
    [
      ['Missing information', missingText],
      ['Uncertainty', uncertainty],
      ['Next step', nextAction]
    ].forEach(([label, value]) => {
      const item = node('div', 'review-note');
      item.append(node('span', 'decision-label', label), node('p', '', value));
      grid.append(item);
    });
    section.append(grid);
    parent.append(section);
  }

  function mediaSource(value) {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object') return value.url || value.src || value.data || '';
    return '';
  }

  function renderTranscript(task, parent) {
    const section = node('section', 'detail-section');
    const heading = node('div', 'subsection-heading');
    heading.append(node('h3', '', 'Conversation transcript'), node('span', 'section-tag', 'CLAIMANT ↔ AGENT'));
    section.append(heading);
    section.append(node('p', 'pending-copy', 'Prerecorded AI conversation. Decisions uses the interview text and available image, not vocal tone or Astra’s final verdict. Audio is generated from the displayed text; this is not a live microphone session.'));
    const transcript = Array.isArray(task?.transcript) ? task.transcript : [];

    if (!transcript.length) {
      section.append(node('p', 'pending-copy', 'Transcript is pending; no conversation turns have been returned for this case.'));
      parent.append(section);
      return;
    }

    const entries = node('div', 'transcript');
    transcript.forEach((turn) => {
      const entry = node('div', 'transcript-entry');
      if (/^turn-\d+$/.test(String(turn?.id || ''))) entry.id = turn.id;
      const speaker = node('div', 'transcript-speaker', valueText(turn?.speaker, 'Speaker'));
      const copy = node('div', 'transcript-copy');
      if (turn?.model !== null && turn?.model !== undefined && turn.model !== '') {
        copy.append(node('span', 'transcript-model', `Text model · ${valueText(turn.model)}`));
      }
      if (turn?.voice_model !== null && turn?.voice_model !== undefined && turn.voice_model !== '') {
        const voice = turn.voice ? ` · ${valueText(turn.voice)}` : '';
        copy.append(node('span', 'transcript-model', `Voice model · ${valueText(turn.voice_model)}${voice}`));
      }
      copy.append(node('p', 'transcript-text', valueText(turn?.text, 'No transcript text returned.')));
      const audioUrl = mediaSource(turn?.audio);
      if (audioUrl) {
        const audio = node('audio', 'audio-player');
        audio.controls = true;
        audio.preload = 'none';
        audio.src = audioUrl;
        audio.setAttribute('aria-label', `Audio for ${valueText(turn?.speaker, 'transcript turn')}`);
        copy.append(audio);
      }
      entry.append(speaker, copy);
      entries.append(entry);
    });
    section.append(entries);
    parent.append(section);
  }

  function mediaCard(label, url, type, pendingText, wide = false, captionsUrl = '') {
    const card = node('div', `media-card${wide ? ' wide' : ''}`);
    const labelRow = node('div', 'media-label');
    labelRow.append(node('span', '', label), node('span', '', url ? 'Returned evidence' : 'Pending'));
    card.append(labelRow);

    if (!url) {
      card.append(node('div', 'media-empty', pendingText));
      return card;
    }

    if (type === 'image') {
      const image = node('img', 'media-image');
      image.src = url;
      image.alt = 'Synthetic claim image evidence';
      image.loading = 'lazy';
      image.addEventListener('error', () => {
        image.replaceWith(node('div', 'media-empty', 'The returned image could not be loaded.'));
      }, { once: true });
      card.append(image);
    } else {
      const video = node('video', 'media-video');
      video.controls = true;
      video.preload = 'metadata';
      video.src = url;
      video.setAttribute('aria-label', 'Synthetic claim video evidence');
      if (captionsUrl) {
        const track = node('track');
        track.kind = 'captions';
        track.srclang = 'en';
        track.label = 'Conversation';
        track.default = true;
        track.src = captionsUrl;
        video.append(track);
      }
      video.addEventListener('error', () => {
        video.replaceWith(node('div', 'media-empty', 'The returned video could not be loaded.'));
      }, { once: true });
      card.append(video);
    }
    return card;
  }

  function renderMedia(task, parent) {
    const section = node('section', 'detail-section');
    const heading = node('div', 'subsection-heading');
    heading.append(node('h3', '', 'Claim evidence'), node('span', 'section-tag', 'SYNTHETIC MEDIA'));
    section.append(heading);

    const imageUrl = mediaSource(task?.image);
    const videoUrl = mediaSource(task?.video);
    const captionsUrl = mediaSource(task?.captions);
    const grid = node('div', 'media-grid');
    if (task?.has_image) {
      const imageCard = mediaCard('Original image', imageUrl, 'image', 'Image evidence is expected but has not been returned yet.');
      imageCard.id = 'claim-image';
      grid.append(imageCard);
      const annotated = node('div', 'media-card');
      annotated.append(node('div', 'media-label', 'ASTRA · VISIBLE DAMAGE'));
      const findings = task.damage_annotations;
      if (findings && findings.source_image === task.image && imageUrl) {
        const overlay = node('div', 'damage-overlay');
        const img = node('img');
        img.src = imageUrl;
        img.alt = 'Original claim image with Astra damage location overlays';
        overlay.append(img);
        const legend = node('ol', 'damage-legend');
        findings.regions.forEach((region, index) => {
          const [x, y, width, height] = region.box;
          const box = node('div', 'damage-box');
          Object.assign(box.style, {left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%`});
          box.title = region.label;
          box.append(node('span', '', String(index + 1)));
          overlay.append(box);
          legend.append(node('li', '', region.label));
        });
        annotated.append(overlay, legend);
        if (!findings.regions.length) annotated.append(node('p', 'pending-copy', 'No damage could be reliably localized.'));
        annotated.append(node('p', 'pending-copy', findings.uncertainty));
      } else {
        annotated.append(node('div', 'media-empty', 'Astra damage localization is pending for this image.'));
      }
      annotated.append(node('p', 'pending-copy', 'Approximate AI visual observations on a synthetic image. Boxes are not measurements or proof of cause. These visual observations feed the Decisions API with the policy, interview, and original image.'));
      grid.append(annotated);
    } else {
      const textCase = node('div', 'media-card');
      textCase.append(node('div', 'media-label', 'TEXT-ONLY SCENARIO'), node('div', 'media-empty', 'This case is evaluated from the claimant conversation and policy.'));
      grid.append(textCase);
    }
    grid.append(mediaCard('Video', videoUrl, 'video', 'No video evidence was returned for this case.', false, captionsUrl));
    section.append(grid);
    parent.append(section);
  }

  function renderMetrics(task, parent) {
    const section = node('section', 'detail-section');
    const heading = node('div', 'subsection-heading');
    heading.append(node('h3', '', 'Astra explanation checks'), node('span', 'section-tag', 'SEPARATE FROM LUNA BENCHMARK'));
    section.append(heading);

    const metrics = task?.metrics || {};
    const definitions = [
      ['verdict_correct', 'Verdict match', (value) => typeof value === 'boolean' ? (value ? 'Correct' : 'Incorrect') : valueText(value), 'boolean'],
      ['clause_correct', 'Clause match', (value) => typeof value === 'boolean' ? (value ? 'Correct' : 'Incorrect') : valueText(value), 'boolean'],
      ['score', 'Score', (value) => valueText(value)],
      ['latency_seconds', 'Total latency', (value) => `${valueText(value)} s`],
      ['adjudication_seconds', 'Adjudication latency', (value) => `${valueText(value)} s`]
    ];
    const taskComplete = ['complete', 'completed', 'done'].includes(String(task?.status || '').toLowerCase());
    const available = definitions.filter(([key]) => {
      if (key === 'adjudication_seconds' && state.view === 'original') return true;
      if (metrics[key] === null || metrics[key] === undefined) return false;
      return !(key === 'latency_seconds' && Number(metrics[key]) === 0 && !taskComplete);
    });

    if (!available.length) {
      section.append(node('p', 'pending-copy', 'Case metrics are pending; no scorecard data has been returned yet.'));
      parent.append(section);
      return;
    }

    const grid = node('div', 'metrics-grid');
    available.forEach(([key, label, format, kind]) => {
      const card = node('div', 'metric-card');
      card.append(node('span', 'metric-name', label));
      const text = key === 'adjudication_seconds' && state.view === 'original' && !isRecorded(metrics[key])
        ? missingValue('Adjudication latency')
        : format(metrics[key]);
      const value = node('div', 'metric-value', text);
      if (kind === 'boolean' && metrics[key] === true) value.classList.add('is-correct');
      if (kind === 'boolean' && metrics[key] === false) value.classList.add('is-incorrect');
      card.append(value);
      grid.append(card);
    });
    section.append(grid);
    if (available.some(([key]) => key === 'latency_seconds' || key === 'adjudication_seconds')) {
      section.append(node('p', 'metrics-note', 'Total latency includes media generation. Adjudication latency measures the final model request.'));
    }
    parent.append(section);
  }

  function renderPolicy(task, parent) {
    const section = node('section', 'detail-section');
    const heading = node('div', 'subsection-heading');
    heading.append(node('h3', '', 'Policy reference'), node('span', 'section-tag', 'SOURCE MATERIAL'));
    section.append(heading);
    if (task?.policy_source?.url) {
      const source = node('div', 'policy-card');
      const link = node('a', '', valueText(task.policy_source.title, 'Policy source'));
      link.href = task.policy_source.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      source.append(link, node('p', 'policy-copy', valueText(task.policy_source.note, '')));
      section.append(source);
    }
    const card = node('div', 'policy-card');
    const policy = task?.policy;
    const clauses = Array.isArray(policy) ? policy : (Array.isArray(policy?.clauses) ? policy.clauses : null);
    if (clauses) {
      if (!clauses.length) {
        card.append(node('p', 'policy-copy', 'No policy clauses were returned for this case.'));
      }
      clauses.forEach((clause) => {
        const row = node('div', 'policy-clause');
        if (clause && typeof clause === 'object') {
          const id = clauseId(clause);
          const text = clauseText(clause);
          if (isRecorded(id)) row.id = clauseAnchorId(id);
          if (id !== null && id !== undefined) row.append(node('strong', 'policy-clause-id', valueText(id)));
          if (clause.source_section) row.append(node('p', 'policy-copy', clause.source_section));
          if (text !== null && text !== undefined) {
            row.append(node('p', 'policy-copy', valueText(text)));
          } else if (id === null || id === undefined) {
            row.append(node('pre', 'policy-object', valueText(clause)));
          }
        } else {
          row.append(node('p', 'policy-copy', valueText(clause)));
        }
        card.append(row);
      });
    } else if (policy && typeof policy === 'object') {
      card.append(node('pre', 'policy-object', valueText(policy)));
    } else {
      card.append(node('p', 'policy-copy', valueText(policy, 'Policy text is pending; no policy reference has been returned for this case.')));
    }
    section.append(card);
    parent.append(section);
  }

  function renderDetail() {
    const tasks = resultsTasks();
    const index = tasks.findIndex((task, taskIndex) => taskKey(task, taskIndex) === state.selectedKey);
    elements.detail.replaceChildren();
    if (index < 0) {
      const empty = node('div', 'detail-empty');
      const art = node('div', 'empty-art');
      art.setAttribute('aria-hidden', 'true');
      art.append(node('span'), node('span'), node('span'));
      empty.append(
        art,
        node('h2', '', tasks.length ? 'Choose a case to review' : 'No case selected'),
        node('p', '', tasks.length
          ? 'Task evidence, policy terms, and the benchmark decision will appear here when you choose a case.'
          : 'The case review will appear here when the results endpoint returns task records.')
      );
      elements.detail.append(empty);
      return;
    }

    const task = tasks[index];
    const content = node('div', 'detail-content');
    const header = node('header', 'detail-heading');
    const copy = node('div', 'detail-heading-copy');
    copy.append(
      node('div', 'detail-kicker', valueText(task?.category, 'Claim review')),
      node('h2', '', valueText(task?.title, 'Untitled task'))
    );
    const status = statusInfo(task?.status);
    header.append(copy, node('span', `detail-status ${status.className}`, status.label));
    content.append(header);

    if (task?.error) content.append(node('div', 'detail-error', valueText(task.error)));
    renderDecision(task, content);
    renderEvidence(task, content);
    renderReviewNotes(task, content);
    if (task.challenge) content.append(node('p', 'pending-copy', `Case challenge: ${task.challenge}`));
    if (task.document_retrieval_required) {
      const docs = node('section', 'detail-section');
      docs.append(node('h3', '', 'Policy document retrieval'));
      const retrieval = task.policy_retrieval;
      if (!retrieval) docs.append(node('p', 'pending-copy', 'OpenAI document retrieval and Astra review are pending. Previous scores do not evaluate this document workflow.'));
      else {
        docs.append(node('p', '', retrieval.review), node('p', 'pending-copy', retrieval.limitation));
        retrieval.passages.forEach(passage => {
          const item = node('details', 'policy-card');
          item.append(node('summary', '', `${passage.id} · relevance ${passage.similarity}`));
          const link = node('a', '', 'Original policy document');
          link.href = passage.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          item.append(link, node('p', 'pending-copy', passage.note), node('p', '', passage.text));
          docs.append(item);
        });
      }
      content.append(docs);
    }
    renderMedia(task, content);
    renderTranscript(task, content);
    renderMetrics(task, content);
    renderPolicy(task, content);
    elements.detail.append(content);
  }

  function render(options = {}) {
    renderStats();
    renderModels();
    renderHarbor();
    renderTasks();
    updateRunStatus();
    if (!options.keepDetail) renderDetail();
  }

  function activateRun(view) {
    state.view = view;
    elements.runView.value = view;
    state.results = view === 'original' ? state.originalResults : state.currentResults;
    state.loading = view === 'original' ? !state.originalResults : !state.currentResults;
    state.harborSummary = view === 'original' ? state.originalHarborSummary : state.currentHarborSummary;
    state.harborError = view === 'original' ? state.originalHarborError : state.currentHarborError;
    const tasks = resultsTasks();
    if (!tasks.some((task, index) => taskKey(task, index) === state.selectedKey)) {
      state.selectedKey = tasks.length ? taskKey(tasks[0], 0) : null;
    }
    render();
  }

  async function loadOriginalRun() {
    try {
      const response = await fetch('/data/baseline.json', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Original run request failed (${response.status}).`);
      const payload = await response.json();
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Original run file returned an invalid response.');
      }
      state.originalResults = payload;
      state.originalError = null;
      if (state.view === 'original') {
        state.results = payload;
        state.loading = false;
        const tasks = resultsTasks();
        if (!tasks.some((task, index) => taskKey(task, index) === state.selectedKey)) {
          state.selectedKey = tasks.length ? taskKey(tasks[0], 0) : null;
        }
        render();
      }
    } catch (error) {
      state.originalError = error instanceof Error ? error.message : 'Unable to load original run.';
      if (state.view === 'original') {
        state.loading = false;
        setApiError(`Could not load the original run: ${state.originalError}`);
        render();
      }
    }
  }

  async function selectRunView(view) {
    if (view === 'current') {
      activateRun('current');
      return;
    }
    activateRun('original');
    const requests = [];
    if (!state.originalResults) requests.push(loadOriginalRun());
    if (!state.originalHarborSummary && !state.originalHarborError) requests.push(fetchHarborSummary('original'));
    if (requests.length) await Promise.all(requests);
  }

  async function fetchResults() {
    if (state.fetching) return;
    state.fetching = true;
    let needsRender = state.loading;
    let keepDetail = false;
    try {
      const response = await fetch('/api/results', { cache: 'no-store' });
      if (!response.ok) throw new Error(`Results request failed (${response.status}).`);
      const payload = await response.json();
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Results endpoint returned an invalid response.');
      }

      const previousResults = state.results;
      const previousSelectedKey = state.selectedKey;
      const previousSelectedTask = resultsTasks().find((task, index) => taskKey(task, index) === previousSelectedKey);
      const snapshot = JSON.stringify(payload);
      const changed = snapshot !== state.payloadSnapshot;
      if (changed) state.payloadSnapshot = snapshot;
      state.currentResults = payload;
      if (state.view === 'current') state.results = payload;
      setApiError('');
      updateConnection('connected', state.view === 'original' ? 'Current run connected' : 'Results connected');
      if (state.view === 'current') {
        const tasks = resultsTasks();
        if (!tasks.some((task, index) => taskKey(task, index) === state.selectedKey)) {
          state.selectedKey = tasks.length ? taskKey(tasks[0], 0) : null;
        }
        const selectedTask = tasks.find((task, index) => taskKey(task, index) === state.selectedKey);
        keepDetail = Boolean(
          previousResults &&
          previousSelectedTask &&
          selectedTask &&
          previousSelectedKey === state.selectedKey &&
          JSON.stringify(previousSelectedTask) === JSON.stringify(selectedTask)
        );
        needsRender = needsRender || changed;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to load benchmark results.';
      if (state.view === 'current') setApiError(`Could not load benchmark results: ${message}`);
      updateConnection('error', state.view === 'original' ? 'Current run unavailable' : 'Results unavailable');
    } finally {
      if (state.view === 'current') state.loading = false;
      state.fetching = false;
      if (state.view === 'current' && needsRender) render({ keepDetail });
    }
  }

  async function fetchHarborSummary(view = 'current') {
    if (state.harborFetching[view]) return;
    state.harborFetching[view] = true;
    const url = view === 'original' ? '/data/baseline-harbor.json' : '/data/harbor-summary.json';
    try {
      const response = await fetch(url, { cache: 'no-store' });
      if (response.status === 404) {
        if (view === 'original') {
          state.originalHarborSummary = null;
          state.originalHarborError = null;
        } else {
          state.currentHarborSummary = null;
          state.harborSnapshot = null;
          state.currentHarborError = null;
        }
        if (state.view === view) {
          state.harborSummary = null;
          state.harborError = null;
          renderHarbor();
        }
        return;
      }
      if (!response.ok) throw new Error(`Summary request failed (${response.status}).`);
      const payload = await response.json();
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Summary endpoint returned an invalid response.');
      }
      const snapshot = JSON.stringify(payload);
      if (view === 'original') {
        state.originalHarborSummary = payload;
        state.originalHarborError = null;
      } else {
        state.currentHarborSummary = payload;
        state.harborSnapshot = snapshot;
        state.currentHarborError = null;
      }
      if (state.view === view) {
        state.harborSummary = payload;
        state.harborError = null;
        renderHarbor();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to load Harbor replay summary.';
      if (view === 'original') state.originalHarborError = message;
      else state.currentHarborError = message;
      if (state.view === view) {
        state.harborError = message;
        renderHarbor();
      }
    } finally {
      state.harborFetching[view] = false;
    }
  }

  async function runBenchmark() {
    if (state.view === 'original') activateRun('current');
    const currentStatus = String(state.results?.status || '').toLowerCase();
    if (state.running || ['running', 'processing', 'in_progress', 'in progress', 'active'].includes(currentStatus)) return;
    state.running = true;
    elements.runButton.disabled = true;
    elements.runButton.classList.add('is-running');
    elements.runButtonText.textContent = 'Starting benchmark…';
    setRunNotice('Sending benchmark request…');
    try {
      const response = await fetch('/api/run', { method: 'POST' });
      if (!response.ok) throw new Error(`Run request failed (${response.status}).`);
      setApiError('');
      setRunNotice('Benchmark request accepted. Waiting for task results…');
      await fetchResults();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to start the benchmark.';
      setApiError(`Benchmark request failed: ${message}`);
      setRunNotice('');
    } finally {
      state.running = false;
      updateRunStatus();
    }
  }

  elements.taskList.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-task-key]');
    if (!button) return;
    state.selectedKey = button.dataset.taskKey;
    renderTasks();
    renderDetail();
  });
  elements.runButton.addEventListener('click', runBenchmark);
  elements.runView.addEventListener('change', (event) => selectRunView(event.target.value));

  async function fetchDecisionComparison() {
    try {
      const response = await fetch('/data/decisions.json', {cache: 'no-store'});
      if (!response.ok) return;
      const next = await response.json();
      if (JSON.stringify(next) !== JSON.stringify(decisionComparison)) {
        decisionComparison = next;
        if (state.view === 'current') render();
      }
    } catch { /* Existing claims remain available if comparisons cannot load. */ }
  }
  fetchResults();
  fetchHarborSummary();
  fetchDecisionComparison();
  window.setInterval(() => {
    fetchDecisionComparison();
    fetchResults();
    fetchHarborSummary();
  }, 3000);
})();
