"""Ask the Roomba for one battery reading without activating its motors."""

from roomba import Roomba, find_port


def main() -> None:
    port = find_port()
    print(f"Puerto detectado: {port}")
    last_error: Exception | None = None

    for baudrate in (115200, 57600):
        try:
            with Roomba(port, baudrate=baudrate) as bot:
                battery = bot.battery()
            print(f"Comunicación OK a {baudrate} baud")
            print(f"Estado:      {battery['charging']}")
            print(f"Voltaje:     {battery['volts']:.2f} V")
            print(f"Corriente:   {battery['amps']:+.3f} A")
            print(f"Carga:       {battery['charge_mah']} / {battery['capacity_mah']} mAh")
            print(f"Porcentaje:  {battery['percent']:.1f}%")
            print(f"Temperatura: {battery['temp_c']} °C")
            return
        except TimeoutError as error:
            last_error = error
            print(f"Sin respuesta a {baudrate} baud ({error})")

    raise SystemExit(f"No hubo respuesta de la Roomba. Último error: {last_error}")


if __name__ == "__main__":
    main()
