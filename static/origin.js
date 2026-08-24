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
};
