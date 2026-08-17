const $ = (selector) => document.querySelector(selector);
const promptInput = $('#prompt');
const sendButton = $('#send');
const runButton = $('#run');
let currentQasm = '';

const LESSON_SHOTS = 1024;
const lessonSteps = [
  {
    gateCount: 0,
    kicker: '实验 1 / 3 · 建立基线',
    title: '先认识一个确定的 0',
    description: '每次实验开始时，量子位都会被准备成 |0⟩。这次我们不做任何操作，直接测量它。',
    caption: '|0⟩ → 测量。没有 H 门。',
    question: '不做任何操作就测量，1,024 次结果会怎样？',
    options: [
      ['zero', '几乎全部是 0'],
      ['balanced', '0 和 1 大约各一半'],
      ['one', '几乎全部是 1'],
    ],
    expected: 'zero',
    observation: '起点不是随机的：结果全部（或几乎全部）是 0。',
    why: '这一步是对照组。它告诉我们后面的变化来自 H 门，而不是模拟器自己在随机开奖。',
  },
  {
    gateCount: 1,
    kicker: '实验 2 / 3 · 制造两种可能',
    title: '让 H 作用一次',
    description: 'H 门把确定的 |0⟩ 变成两种共同参与后续操作的可能性，然后我们立刻测量。',
    caption: '|0⟩ → H → 测量。',
    question: '加入一个 H 后，1,024 次结果会怎样？',
    options: [
      ['zero', '几乎全部是 0'],
      ['balanced', '0 和 1 大约各一半'],
      ['one', '几乎全部是 1'],
    ],
    expected: 'balanced',
    observation: '0 和 1 大约各占一半，但每一轮的具体数量会有波动。',
    why: '只看这一步，它很像一枚公平硬币。不过“看起来随机”还不足以解释量子位；关键证据在下一个实验。',
  },
  {
    gateCount: 2,
    kicker: '实验 3 / 3 · 看见干涉',
    title: '测量前，再作用一次 H',
    description: '这次不在两个 H 之间测量。两种可能性会继续演化，并在第二个 H 处重新汇合。',
    caption: '|0⟩ → H → H → 测量。两个 H 之间没有测量。',
    question: '连续两个 H 后，1,024 次结果会怎样？',
    options: [
      ['zero', '重新变成几乎全部是 0'],
      ['balanced', '仍然是 0 和 1 各一半'],
      ['one', '变成几乎全部是 1'],
    ],
    expected: 'zero',
    observation: '结果重新回到确定的 0：H 不是“随机抛硬币”按钮。',
    why: '第一次 H 产生的两条可能路径还保留着正负方向。第二个 H 让它们重新汇合：通往 0 的部分相加，通往 1 的部分相消。这就是量子干涉。',
    paths: [
      '通往 0：+ 1/2 与 + 1/2 相加，得到 1',
      '通往 1：+ 1/2 与 − 1/2 相消，得到 0',
    ],
  },
];

let lessonIndex = 0;
let selectedPrediction = '';

function showError(message) {
  const box = $('#error');
  box.textContent = message;
  box.hidden = false;
}

function clearError() {
  $('#error').hidden = true;
}

async function request(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({error: '服务返回了无法读取的结果'}));
  if (!response.ok) throw new Error(body.error || '请求失败，请稍后再试');
  return body;
}

function lessonQasm(gateCount) {
  const gates = Array.from({length: gateCount}, () => 'h q[0];').join('\n');
  return `OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
creg c[1];
${gates ? `${gates}\n` : ''}measure q -> c;`;
}

function renderLessonCircuit(gateCount) {
  const circuit = $('#lesson-circuit');
  circuit.replaceChildren();

  const state = document.createElement('span');
  state.className = 'circuit-state';
  state.textContent = '|0⟩';
  circuit.append(state);

  for (let index = 0; index < gateCount; index += 1) {
    const wire = document.createElement('span');
    wire.className = 'wire-segment';
    const gate = document.createElement('span');
    gate.className = 'gate-node';
    gate.textContent = 'H';
    circuit.append(wire, gate);
  }

  const finalWire = document.createElement('span');
  finalWire.className = 'wire-segment';
  const measure = document.createElement('span');
  measure.className = 'measure-node';
  measure.textContent = '测量';
  circuit.append(finalWire, measure);
}

function updateLessonProgress(activeIndex, complete = false) {
  document.querySelectorAll('#lesson-progress li').forEach((item, index) => {
    item.classList.toggle('active', !complete && index === activeIndex);
    item.classList.toggle('done', complete || index < activeIndex);
    if (!complete && index === activeIndex) item.setAttribute('aria-current', 'step');
    else item.removeAttribute('aria-current');
  });
}

function renderLessonStep() {
  const step = lessonSteps[lessonIndex];
  selectedPrediction = '';
  $('#lesson-kicker').textContent = step.kicker;
  $('#lesson-title').textContent = step.title;
  $('#lesson-description').textContent = step.description;
  $('#lesson-circuit-caption').textContent = step.caption;
  $('#lesson-question').textContent = step.question;
  renderLessonCircuit(step.gateCount);
  updateLessonProgress(lessonIndex);

  const options = $('#prediction-options');
  options.replaceChildren();
  step.options.forEach(([value, label]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.prediction = value;
    button.textContent = label;
    button.addEventListener('click', () => {
      selectedPrediction = value;
      options.querySelectorAll('button').forEach((option) => {
        const selected = option === button;
        option.classList.toggle('selected', selected);
        option.setAttribute('aria-pressed', String(selected));
      });
      const run = $('#lesson-run');
      run.disabled = false;
      run.textContent = '运行实验，检查预测';
    });
    options.append(button);
  });

  const run = $('#lesson-run');
  run.disabled = true;
  run.textContent = '选择一个预测';
  $('#lesson-working').hidden = true;
  $('#lesson-feedback').hidden = true;
  $('#lesson-error').hidden = true;
}

function renderLessonChart(result) {
  const chart = $('#lesson-chart');
  const shots = result.shots || LESSON_SHOTS;
  chart.replaceChildren();
  ['0', '1'].forEach((state) => {
    const count = result.counts[state] || 0;
    const row = document.createElement('div');
    row.className = 'bar-row';
    const label = document.createElement('span');
    label.className = 'bar-label';
    label.textContent = state;
    const track = document.createElement('div');
    track.className = 'bar-track';
    const fill = document.createElement('div');
    fill.className = 'bar-fill';
    fill.style.width = `${count / shots * 100}%`;
    track.append(fill);
    const value = document.createElement('span');
    value.className = 'bar-value';
    value.textContent = `${(count / shots * 100).toFixed(1)}%`;
    row.append(label, track, value);
    chart.append(row);
  });
}

function renderPathExplanation(paths = []) {
  const box = $('#path-explanation');
  box.replaceChildren();
  box.hidden = paths.length === 0;
  paths.forEach((path) => {
    const line = document.createElement('div');
    line.textContent = path;
    box.append(line);
  });
}

async function runLessonStep() {
  if (!selectedPrediction) return;
  const step = lessonSteps[lessonIndex];
  const run = $('#lesson-run');
  run.disabled = true;
  run.textContent = '正在运行…';
  $('#lesson-error').hidden = true;
  $('#lesson-working').hidden = false;
  $('#lesson-feedback').hidden = true;

  try {
    const result = await request('/api/run', {
      qasm: lessonQasm(step.gateCount),
      target: 'originq',
      shots: LESSON_SHOTS,
    });
    renderLessonChart(result);
    const correct = selectedPrediction === step.expected;
    $('#prediction-verdict').textContent = correct
      ? '✓ 你的预测与实验一致'
      : '预测与结果不同——这正是做实验的价值';
    $('#lesson-observation').textContent = step.observation;
    $('#lesson-why').textContent = step.why;
    renderPathExplanation(step.paths);
    $('#lesson-continue').textContent = lessonIndex === lessonSteps.length - 1
      ? '回答一个问题，确认理解'
      : '进入下一个实验';
    $('#lesson-feedback').hidden = false;
    $('#lesson-feedback').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (error) {
    const box = $('#lesson-error');
    box.textContent = `实验没有运行成功：${error.message}`;
    box.hidden = false;
  } finally {
    $('#lesson-working').hidden = true;
    run.disabled = false;
    run.textContent = '重新运行这个实验';
  }
}

function continueLesson() {
  if (lessonIndex < lessonSteps.length - 1) {
    lessonIndex += 1;
    renderLessonStep();
    $('#lesson-workspace').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    return;
  }

  $('#lesson-workspace').hidden = true;
  $('#lesson-feedback').hidden = true;
  $('#lesson-quiz').hidden = false;
  updateLessonProgress(3);
  $('#lesson-quiz').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function answerQuiz(button) {
  const feedback = $('#quiz-feedback');
  const correct = button.dataset.quizAnswer === 'interference';
  feedback.hidden = false;
  if (!correct) {
    button.classList.add('incorrect');
    feedback.className = 'quiz-feedback try-again';
    feedback.textContent = '再想想第三个实验：如果只是两次随机变化，结果不会稳定地回到 0。你可以再选一次。';
    return;
  }

  document.querySelectorAll('[data-quiz-answer]').forEach((option) => {
    option.disabled = true;
    option.classList.toggle('correct', option === button);
  });
  feedback.className = 'quiz-feedback';
  feedback.textContent = '正确。干涉发生在测量之前：可能路径带着方向重新汇合，因此有的结果被加强，有的被抵消。';
  $('#lesson-complete').hidden = false;
  updateLessonProgress(4, true);
  $('#lesson-complete').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function friendlyOnly(text) {
  return text.replace(/```(?:qasm|openqasm)?[\s\S]*?```/gi, '').trim();
}

async function sendPrompt() {
  const prompt = promptInput.value.trim();
  if (!prompt) {
    showError('先用一句自己的话说说你想看到什么，不需要使用任何专业词。');
    promptInput.focus();
    return;
  }
  clearError();
  sendButton.disabled = true;
  $('#working').hidden = false;
  $('#answer-panel').hidden = true;
  $('#result-panel').hidden = true;
  try {
    const data = await request('/api/chat', {prompt});
    currentQasm = data.qasm || '';
    $('#answer-text').textContent = friendlyOnly(data.response) || data.response;
    $('#answer-panel').hidden = false;
    $('#code-details').hidden = !currentQasm;
    $('#run-controls').hidden = !currentQasm;
    if (currentQasm) $('#qasm-code').textContent = currentQasm;
    $('#answer-panel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (error) {
    showError(error.message);
  } finally {
    sendButton.disabled = false;
    $('#working').hidden = true;
  }
}

function explainResult(counts, shots) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const leaders = entries.slice(0, 2);
  const leaderShare = leaders.reduce((sum, [, count]) => sum + count, 0) / shots;
  const allTogether = leaders.length === 2 && leaders.every(([state]) => /^0+$|^1+$/.test(state));
  if (allTogether && leaderShare > 0.85) {
    return `把它想成重复开奖 ${shots.toLocaleString()} 次：绝大多数时候，所有位会一起成为 0 或一起成为 1。重点不是某一次开出了什么，而是它们表现出了“共同变化”的稳定关系。`;
  }
  const names = leaders.map(([state]) => state).join(' 和 ');
  return `这个实验重复了 ${shots.toLocaleString()} 次，结果主要集中在 ${names}。条形越长，说明这种组合越常出现；比较整体分布，比盯着任何一次开奖更有意义。`;
}

function renderChart(result) {
  const entries = Object.entries(result.counts).sort((a, b) => b[1] - a[1]);
  const visible = entries.slice(0, 12);
  const maximum = visible[0]?.[1] || 1;
  const chart = $('#chart');
  chart.replaceChildren();
  visible.forEach(([state, count]) => {
    const row = document.createElement('div');
    row.className = 'bar-row';
    const label = document.createElement('span');
    label.className = 'bar-label';
    label.textContent = state;
    const track = document.createElement('div');
    track.className = 'bar-track';
    const fill = document.createElement('div');
    fill.className = 'bar-fill';
    fill.style.width = `${Math.max(0.2, count / maximum * 100)}%`;
    track.append(fill);
    const value = document.createElement('span');
    value.className = 'bar-value';
    value.textContent = `${count.toLocaleString()} · ${(count / result.shots * 100).toFixed(1)}%`;
    row.append(label, track, value);
    chart.append(row);
  });
  $('#backend-badge').textContent = result.backend;
  $('#result-explanation').textContent = explainResult(result.counts, result.shots);
}

async function runExperiment() {
  if (!currentQasm) return;
  clearError();
  runButton.disabled = true;
  runButton.textContent = '正在运行…';
  try {
    const result = await request('/api/run', {
      qasm: currentQasm,
      target: $('#target').value,
      shots: Number($('#shots').value),
    });
    renderChart(result);
    $('#result-panel').hidden = false;
    $('#result-panel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } catch (error) {
    showError(error.message);
  } finally {
    runButton.disabled = false;
    runButton.textContent = '运行这个实验';
  }
}

document.querySelectorAll('[data-prompt]').forEach((button) => {
  button.addEventListener('click', () => {
    promptInput.value = button.dataset.prompt;
    promptInput.focus();
  });
});
$('#lesson-run').addEventListener('click', runLessonStep);
$('#lesson-continue').addEventListener('click', continueLesson);
document.querySelectorAll('[data-quiz-answer]').forEach((button) => {
  button.addEventListener('click', () => answerQuiz(button));
});
sendButton.addEventListener('click', sendPrompt);
runButton.addEventListener('click', runExperiment);
promptInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) sendPrompt();
});

renderLessonStep();

fetch('/api/config')
  .then((response) => response.json())
  .then((config) => {
    const status = $('#model-status');
    if (config.model_configured) {
      status.classList.add('ready');
      status.lastChild.textContent = ' 模型服务已连接';
    } else {
      status.classList.add('warn');
      status.lastChild.textContent = ' 等待配置模型服务';
    }
  })
  .catch(() => {
    $('#model-status').lastChild.textContent = ' 无法连接本地服务';
  });
