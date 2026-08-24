const routeUi = {
  nogal: document.getElementById('routeNogal'),
  sopi: document.getElementById('routeSopi'),
  hint: document.getElementById('routeHint'),
};

let selectedRoute = 'nogal';
let routeCounts = {};

function selectTrainingRoute(route, force = false) {
  if (recording && !force) return;
  selectedRoute = route;
  const isSopi = route === 'sopi';
  routeUi.nogal.classList.toggle('selected', !isSopi);
  routeUi.sopi.classList.toggle('selected', isSopi);
  routeUi.hint.textContent = isSopi
    ? `Ruta nueva · ${routeCounts.sopi || 0}/10 grabaciones`
    : 'Ruta existente y entrenada';
  ui.record.textContent = `Grabar ruta ${isSopi ? 'Sopi' : 'Nogal'}`;
  if (!force) send({type: 'route_select', route_id: route});
}

routeUi.nogal.addEventListener('click', () => selectTrainingRoute('nogal'));
routeUi.sopi.addEventListener('click', () => selectTrainingRoute('sopi'));

ui.record.addEventListener('click', event => {
  event.stopImmediatePropagation();
  send(recording
    ? {type: 'record_stop'}
    : {type: 'record_start', route_id: selectedRoute});
}, true);

const baseSetRecording = setRecording;
setRecording = function(value, dataset = {}) {
  routeCounts = dataset.route_counts || routeCounts;
  if (value && dataset.route_id) selectTrainingRoute(dataset.route_id, true);
  baseSetRecording(value, dataset);
  routeUi.nogal.disabled = value;
  routeUi.sopi.disabled = value;
  ui.record.textContent = value
    ? 'Detener y guardar'
    : `Grabar ruta ${selectedRoute === 'sopi' ? 'Sopi' : 'Nogal'}`;
  ui.recordLamp.textContent = value
    ? `● Grabando ${dataset.route_name || ''}`.trim()
    : 'En vivo';
};
