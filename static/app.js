const $ = (id) => document.getElementById(id);
const ui = {
  connection: $('connection'), battery: $('battery'), charge: $('charge'), volts: $('volts'),
  current: $('current'), mode: $('mode'), speed: $('speed'), left: $('leftMotor'),
  right: $('rightMotor'), instruction: $('instruction'), joystick: $('joystick'), stick: $('stick'), arm: $('arm'),
  emergency: $('emergency'), record: $('record'), recordLamp: $('recordLamp'),
  sampleCount: $('sampleCount'), session: $('session'), camera: $('camera'),
  cameraOffline: $('cameraOffline')
};
let socket, armed = false, batteryOK = false, recording = false;
let activePointer = null, position = { x: 0, y: 0 }, reconnectTimer;
let commandSequence = 0;

function connect() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws/control`);
  socket.onopen = () => setConnection(true, 'En línea');
  socket.onclose = () => {
    setConnection(false, 'Desconectado'); setArmed(false); setRecording(false);
    reconnectTimer = setTimeout(connect, 1200);
  };
  socket.onerror = () => socket.close();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.type === 'status') render(message.data);
    if (message.type === 'armed') setArmed(message.ok);
    if (message.type === 'busy') setConnection(false, 'Control ocupado');
  };
}

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function setConnection(ok, text) {
  ui.connection.classList.toggle('online', ok);
  ui.connection.classList.toggle('offline', !ok);
  ui.connection.querySelector('span').textContent = text;
}

function stopDrive() {
  activePointer = null;
  position = { x: 0, y: 0 };
  ui.stick.style.transform = 'translate(-50%,-50%)';
  send({ type: 'stop', sequence: ++commandSequence });
}

function setArmed(value) {
  armed = value;
  ui.joystick.classList.toggle('disabled', !armed);
  ui.arm.classList.toggle('active', armed);
  ui.arm.textContent = armed ? 'DESARMAR' : (batteryOK ? 'ARMAR CONTROL' : 'BATERÍA INSUFICIENTE');
  ui.arm.disabled = !armed && !batteryOK;
  ui.instruction.textContent = armed ? 'Mantén y arrastra para conducir' : (batteryOK ? 'Arma el control para habilitar el joystick' : 'Esperando telemetría de batería');
  if (!armed) stopDrive();
}

function setRecording(value, dataset = {}) {
  recording = value;
  ui.record.classList.toggle('active', recording);
  ui.record.textContent = recording ? '■ DETENER Y GUARDAR' : '● COMENZAR A RECOLECTAR';
  ui.recordLamp.textContent = recording ? '● REC' : '● STANDBY';
  ui.recordLamp.classList.toggle('active', recording);
  ui.sampleCount.textContent = `${dataset.samples || 0} MUESTRAS`;
  ui.session.textContent = dataset.session_id || 'Sin sesión de datos';
}

function render(data) {
  const b = data.battery || {};
  ui.battery.textContent = b.percent == null ? '—%' : `${b.percent}%`;
  ui.charge.textContent = b.charging || 'Esperando datos';
  ui.volts.textContent = b.volts == null ? '—' : `${b.volts} V`;
  ui.current.textContent = b.amps == null ? '— A' : `${b.amps > 0 ? '+' : ''}${b.amps} A`;
  ui.mode.textContent = data.status === 'simulated' ? 'SIM' : (data.armed ? 'ARMADO' : 'SEGURO');
  ui.speed.textContent = `Máx. ${data.max_speed || '—'} mm/s`;
  ui.left.textContent = `L ${data.motors?.left || 0}`;
  ui.right.textContent = `R ${data.motors?.right || 0}`;
  batteryOK = Boolean(data.battery_ok);
  ui.arm.disabled = !armed && !batteryOK;
  if (!armed) {
    const threshold = Number(data.minimum_battery ?? 0);
    ui.arm.textContent = batteryOK ? 'ARMAR CONTROL' : (threshold > 0 ? 'BATERÍA INSUFICIENTE' : 'ESPERANDO BATERÍA');
    ui.instruction.textContent = batteryOK ? 'Arma el control para habilitar el joystick' : (threshold > 0 ? `Se requiere al menos ${threshold}% de batería` : 'Esperando telemetría de batería');
  }
  if (!data.armed && armed) setArmed(false);
  setRecording(Boolean(data.dataset?.recording), data.dataset || {});
  ui.cameraOffline.hidden = Boolean(data.camera?.online);
}

function moveStick(event) {
  if (!armed || event.pointerId !== activePointer) return;
  const rect = ui.joystick.getBoundingClientRect();
  const radius = rect.width * 0.34;
  let x = event.clientX - (rect.left + rect.width / 2);
  let y = event.clientY - (rect.top + rect.height / 2);
  const distance = Math.hypot(x, y);
  if (distance > radius) { x *= radius / distance; y *= radius / distance; }
  position = { x: x / radius, y: -y / radius };
  ui.stick.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
}

function beginDrive(event) {
  if (!armed || activePointer !== null) return;
  event.preventDefault();
  activePointer = event.pointerId;
  ui.joystick.setPointerCapture(activePointer);
  moveStick(event);
  send({ type: 'drive', sequence: ++commandSequence, ...position });
}

ui.joystick.addEventListener('pointerdown', beginDrive);
ui.joystick.addEventListener('pointermove', moveStick);
ui.joystick.addEventListener('pointerup', stopDrive);
ui.joystick.addEventListener('pointercancel', stopDrive);
ui.joystick.addEventListener('lostpointercapture', stopDrive);
ui.arm.addEventListener('click', () => {
  if (armed) { send({ type: 'disarm' }); setArmed(false); }
  else send({ type: 'arm' });
});
ui.emergency.addEventListener('click', () => {
  send({ type: 'emergency' }); setArmed(false);
  if (navigator.vibrate) navigator.vibrate(120);
});
ui.record.addEventListener('click', () => send({ type: recording ? 'record_stop' : 'record_start' }));
ui.camera.addEventListener('load', () => { ui.cameraOffline.hidden = true; });
ui.camera.addEventListener('error', () => { ui.cameraOffline.hidden = false; });
setInterval(() => {
  if (armed && activePointer !== null) send({ type: 'drive', sequence: ++commandSequence, ...position });
}, 80);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { send({ type: 'disarm' }); setArmed(false); }
});
window.addEventListener('pagehide', () => send({ type: 'disarm' }));
connect();
