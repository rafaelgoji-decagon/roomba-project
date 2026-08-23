# Roomba Deck

Portal local para controlar una Roomba desde el navegador de un celular mediante una Raspberry Pi.

Incluye video MJPEG de la webcam, D-pad táctil y grabación de datasets supervisados.

## Seguridad

- Arranca desarmado y con velocidad máxima OI de 500 mm/s.
- Suelta el joystick para detenerse.
- Un watchdog local detiene los motores si faltan comandos durante 750 ms.
- Al cerrar/ocultar la página o perder el WebSocket, se desarma.
- Solo un navegador puede tener la sesión de control.
- La Roomba se opera en modo OI Safe, que conserva las protecciones integradas.
- El umbral de batería está desactivado por defecto para pruebas breves. Puede
  restaurarse, por ejemplo, con `ROOMBA_MIN_BATTERY=20 .venv/bin/python run.py`.

Haz las primeras pruebas con la Roomba levantada, ruedas libres y batería suficiente.

## Probar sin hardware

```bash
ROOMBA_MOCK=1 .venv/bin/python run.py
```

Abre `http://localhost:8000`. Desde otro equipo de la red usa la IP de esta computadora.

## Raspberry Pi

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Requiere `ffmpeg` para la webcam (`sudo apt install ffmpeg`). La captura local
usa por defecto JPEG a color de 1280×720; el portal recibe solamente una vista
previa 320×180 en escala de grises a 4 FPS para proteger el ancho de banda.
Cada vez que se
pulsa **Comenzar a recolectar**, se crea `datasets/run-.../` con:

- `frames/*.jpg`: imágenes sincronizadas a 5 Hz.
- `labels.jsonl`: por cuadro, comando solicitado, movimiento ejecutado, estado de
  seguridad y snapshot completo de sensores OI.
- `events.jsonl`: todos los comandos del iPhone con secuencia y todos los cambios
  efectivamente enviados a las ruedas, watchdogs y eventos de sesión.
- `metadata.json`: configuración y datos de la sesión.

El snapshot de sensores conserva tanto campos interpretados (bumps, cliffs,
wall, wheel drops, distancia/ángulo, batería, encoders, light bumpers y
corrientes cuando existen) como `raw_hex`, para poder reanalizar datos en el
futuro. El controlador intenta el paquete OI extendido y cae automáticamente al
grupo estándar en modelos que no lo soportan.

Para cambiar cámara o frecuencia: `ROOMBA_CAMERA=/dev/video0`,
`ROOMBA_DATA_HZ=5`, `ROOMBA_PREVIEW_FPS=4`. Si el navegador se desconecta, la
sesión se detiene y queda guardada.

Normalmente el adaptador será `/dev/ttyUSB0`. Si hay más de uno:

```bash
ROOMBA_PORT=/dev/ttyUSB0 .venv/bin/python run.py
```

El usuario del servicio debe tener permiso para el puerto serial (habitualmente pertenecer al grupo `dialout`).
