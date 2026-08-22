const $ = (id) => document.getElementById(id);
const ui = { connection: $('connection'), battery: $('battery'), charge: $('charge'), volts: $('volts'), current: $('current'), mode: $('mode'), speed: $('speed'), left: $('leftMotor'), right: $('rightMotor'), instruction: $('instruction'), joystick: $('joystick'), stick: $('stick'), arm: $('arm'), emergency: $('emergency') };
let socket, armed = false, batteryOK = false, pointer = null, position = { x: 0, y: 0 }, reconnectTimer;

function connect() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws/control`);
  socket.onopen = () => setConnection(true, 'En línea');
  socket.onclose = () => { setConnection(false, 'Desconectado'); setArmed(false); reconnectTimer = setTimeout(connect, 1200); };
  socket.onerror = () => socket.close();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.type === 'status') render(message.data);
    if (message.type === 'armed') setArmed(message.ok);
    if (message.type === 'busy') setConnection(false, 'Control ocupado');
  };
}
function send(message) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); }
function setConnection(ok, text) { ui.connection.classList.toggle('online', ok); ui.connection.classList.toggle('offline', !ok); ui.connection.querySelector('span').textContent = text; }
function setArmed(value) {
  armed = value; ui.joystick.classList.toggle('disabled', !armed); ui.arm.classList.toggle('active', armed);
  ui.arm.textContent = armed ? 'DESARMAR' : (batteryOK ? 'ARMAR CONTROL' : 'BATERÍA INSUFICIENTE');
  ui.arm.disabled = !armed && !batteryOK;
  ui.instruction.textContent = armed ? 'Mantén y arrastra para conducir' : (batteryOK ? 'Arma el control para habilitar el joystick' : 'Se requiere al menos 20% de batería');
  if (!armed) resetStick();
}
function render(data) {
  const b = data.battery || {}; ui.battery.textContent = b.percent == null ? '—%' : `${b.percent}%`; ui.charge.textContent = b.charging || 'Esperando datos';
  ui.volts.textContent = b.volts == null ? '—' : `${b.volts} V`; ui.current.textContent = b.amps == null ? '— A' : `${b.amps > 0 ? '+' : ''}${b.amps} A`;
  ui.mode.textContent = data.status === 'simulated' ? 'SIM' : (data.armed ? 'ARMADO' : 'SEGURO'); ui.speed.textContent = `Máx. ${data.max_speed || '—'} mm/s`;
  ui.left.textContent = `L ${data.motors?.left || 0}`; ui.right.textContent = `R ${data.motors?.right || 0}`;
  batteryOK = Boolean(data.battery_ok); ui.arm.disabled = !armed && !batteryOK;
  if (!armed) { ui.arm.textContent = batteryOK ? 'ARMAR CONTROL' : 'BATERÍA INSUFICIENTE'; ui.instruction.textContent = batteryOK ? 'Arma el control para habilitar el joystick' : `Se requiere al menos ${data.minimum_battery || 20}% de batería`; }
  if (!data.armed && armed) setArmed(false);
}
function moveStick(event) {
  if (!armed || event.pointerId !== pointer) return;
  const rect = ui.joystick.getBoundingClientRect(), radius = rect.width * .34;
  let x = event.clientX - (rect.left + rect.width / 2), y = event.clientY - (rect.top + rect.height / 2);
  const distance = Math.hypot(x, y); if (distance > radius) { x *= radius / distance; y *= radius / distance; }
  position = { x: x / radius, y: -y / radius }; ui.stick.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
  send({ type: 'drive', ...position });
}
function resetStick() { pointer = null; position = { x: 0, y: 0 }; ui.stick.style.transform = 'translate(-50%,-50%)'; send({ type: 'stop' }); }
ui.joystick.addEventListener('pointerdown', (event) => { if (!armed) return; pointer = event.pointerId; ui.joystick.setPointerCapture(pointer); moveStick(event); });
ui.joystick.addEventListener('pointermove', moveStick); ui.joystick.addEventListener('pointerup', resetStick); ui.joystick.addEventListener('pointercancel', resetStick);
ui.arm.addEventListener('click', () => { if (armed) { send({ type:'disarm' }); setArmed(false); } else send({ type:'arm' }); });
ui.emergency.addEventListener('click', () => { send({ type:'emergency' }); setArmed(false); if (navigator.vibrate) navigator.vibrate(120); });
setInterval(() => { if (armed) send({ type:'drive', ...position }); }, 100);
document.addEventListener('visibilitychange', () => { if (document.hidden) { send({ type:'disarm' }); setArmed(false); } });
window.addEventListener('pagehide', () => send({ type:'disarm' }));
connect();
