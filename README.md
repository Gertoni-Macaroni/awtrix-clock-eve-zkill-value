# awtrix-clock-eve-zkill-value
A Webhook listener that updates a matrix display running AWTRIX with live Zkill statistics for specified corps/alliances

required python packages:
`aiomqtt` `requests` `websockets` `valkey`

Requirements:  
- Raspberry-Pi or any other permanent on device to run `main.py` and is running [valkey](https://valkey.io/) and any mqtt broker, personally used [mosquitto](https://mosquitto.org/).
- AWTRIX Flashed matrix display, the program was built for the [Ulanzi TC001](https://www.ulanzi.com/en-gb/products/ulanzi-pixel-smart-clock-2882?_pos=1&_psq=ulanzi+tc-&_ss=e&_v=1.0)

Startup parameters 

| Parameter              | Default   | Description                                                                       |
|------------------------|-----------|-----------------------------------------------------------------------------------|
| `-l` `--lifetime`      | 86400     | How long each killmail is used in display in seconds, defaults to 24 hours        |   
| `-c` `--corporation`   | None      | Corporation ID from which to read killmails                                       |
| `-a` `--alliance`      | None      | Alliance ID from which to read killmails (is ignored if corporation id is provided) |
| `-f` `--fresh`         | False     | Removes all stored killmails on startup, defaults to False                        |
| `--mqtthost` | localhost | mqtt hostname, defaults to localhost                                              |
| `--mqttport`           | 1883      | mqtt port, defaults to 1883                                                                                  |

Either `--corporation` or `--alliance` are required parameters