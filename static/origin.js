document.getElementById('originCapture').addEventListener('click', () => {
  send({ type: 'origin_capture' });
});

const originAlignButton = document.getElementById('originAlign');
originAlignButton.addEventListener('click', () => {
  const running = originAlignButton.dataset.running === 'true';
  send({type: running ? 'origin_align_cancel' : 'origin_align'});
});

const baseRenderOrigin = renderOrigin;
renderOrigin = function(origin = {}) {
  baseRenderOrigin(origin);
  const alignment = origin.alignment || {};
  const running = alignment.state === 'running';
  originAlignButton.dataset.running = String(running);
  originAlignButton.disabled = !running && (!origin.target_saved || !(origin.marker_ids || []).length);
  originAlignButton.textContent = running ? 'Detener ajuste' : (alignment.state === 'aligned' ? 'Origen listo ✓' : 'Ir al origen');
  if (running || alignment.state === 'fault' || alignment.state === 'aligned') {
    ui.originGuidance.textContent = alignment.message || ui.originGuidance.textContent;
  }
  ui.alignProgress.hidden = !(running || alignment.state === 'fault' || alignment.state === 'aligned');
  ui.alignProgress.className = `align-progress ${alignment.state || ''}`;
  ui.alignProgress.querySelectorAll('[data-phase]').forEach(step => {
    step.classList.toggle('active', running && step.dataset.phase === alignment.phase);
  });
  const score = Math.max(0, Math.min(100, alignment.errors?.score ?? origin.comparison?.score ?? 0));
  ui.alignScoreBar.style.width = `${score}%`;
  ui.alignStep.textContent = alignment.state === 'aligned' ? 'Origen listo' : (alignment.step_label || 'Midiendo pose');
  const command = alignment.command || {};
  ui.alignDetail.textContent = running && alignment.phase === 'move'
    ? `${alignment.pulse_seconds || 0} s · ruedas ${command.left_mm_s || 0}/${command.right_mm_s || 0} mm/s`
    : (alignment.phase === 'settle' ? `${alignment.settle_seconds || 1} s sin movimiento` : `${Math.round(score)}% de coincidencia`);
};
