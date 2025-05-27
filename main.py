import argparse
import asyncio
import itertools
import json
import math
import random
import traceback
from json import JSONEncoder

import aiomqtt
import requests
import valkey
import websockets

# Setup launch parameters
parser = argparse.ArgumentParser()
parser.add_argument("-l", "--lifetime", type=int, default=24 * 60 * 60,
                    help="How long each killmail is used in display in seconds, defaults to 24 hours")
parser.add_argument("-f", "--fresh", action="store_true",
                    help="Removes all stored killmails on startup, defaults to False")
parser.add_argument("-c", "--corporation", type=int, help="Corporation ID from which to read killmails")
parser.add_argument("-a", "--alliance", type=int,
                    help="Alliance ID from which to read killmails (is ignored if corporation id is provided)")
parser.add_argument("--mqtthost", default="localhost", help="mqtt hostname, defaults to localhost")
parser.add_argument("--mqttport", default=1883, help="mqtt port, defaults to 1883")
launch_arguments = parser.parse_args()

# Configuration
fresh = launch_arguments.fresh
corp_id = launch_arguments.corporation
alliance_id = launch_arguments.alliance
tracking_duration_seconds = launch_arguments.lifetime
reconnect_interval_seconds = 60

# Websocket
websocket_base = "wss://zkillboard.com/websocket/"
websocket_substring = JSONEncoder().encode({"action": "sub",
                                            "channel": f'{"alliance" if corp_id is None else "corporation"}:{str(corp_id if corp_id is not None else alliance_id)}'})

# ZKill API
kill_api_base = "https://zkillboard.com/api/killID"

# Valkey DB
valkey_host = "localhost"
valkey_port = 6379

# MQTT
mqtt_host = launch_arguments.mqtthost
mqtt_port = launch_arguments.mqttport
mqtt_client_id = f'python-mqtt-{random.randint(0, 1000)}'
mqtt_topic = "eve_counter/custom/eve_counter"

down_color = "#FF0000"
up_color = "#00FF00"
arrows = [[
    {"dp": [4, 4, "#ff4a4a"]},
    {"df": [1, 1, 2, 2, down_color]},
    {"df": [2, 2, 2, 2, down_color]},
    {"dr": [3, 3, 3, 3, down_color]},
    {"dl": [5, 1, 5, 5, down_color]},
    {"dl": [1, 5, 5, 5, down_color]}],
    [{"dp": [4, 2, "#78ff78"]},
     {"df": [1, 4, 2, 2, up_color]},
     {"df": [2, 3, 2, 2, up_color]},
     {"dr": [3, 1, 3, 3, up_color]},
     {"dl": [1, 1, 5, 1, up_color]},
     {"dl": [5, 1, 5, 5, up_color]}]
]


async def start_up():
    if corp_id is None and alliance_id is None:
        print("Error: either --corporation or --alliance are required arguments but not provided \nshutting down")
        return

    # seed DB
    try:
        valkey_db = valkey.asyncio.client.Valkey(host=valkey_host, port=valkey_port, db=0)

        if fresh:
            print("-- Flushing Database")
            await valkey_db.flushall()

        await valkey_db.set('last_total_value', 0)
        await valkey_db.aclose()

        # Sets up the display with zero values.
        await send_display_update_payload([0], 0, 0)
    except Exception as e:
        print(f'Exception thrown while consuming websocket message: <{type(e).__name__}>, "{e}"')
        print(traceback.format_exc())

    await asyncio.gather(
        setup_websocket(),
        check_for_expired_keys()
    ),


# Send any payload to the mqtt broker
async def mqtt_publish(payload):
    async with aiomqtt.Client(mqtt_host) as client:
        await client.publish(mqtt_topic, JSONEncoder().encode(payload))


# Permanent loop, it will attempt to reconnect if for any reason the websocket listening loop exits.
async def setup_websocket():
    while True:
        await listen_to_websocket()
        print('-- Connection with websocket dropped, attempting to reconnect in 60 seconds')
        await asyncio.sleep(reconnect_interval_seconds)


# Sets up the websocket and sends the subscribe message to zKill
async def listen_to_websocket():
    try:
        async with websockets.connect(websocket_base) as websocket:
            keepalive_task = asyncio.create_task(keepalive(websocket))
            print(f'-- Sending websocket subscription payload \'{websocket_substring}\' and waiting for response')
            await websocket.send(websocket_substring)
            print("-- Successfully connected to websocket, waiting for killmails to consume")
            await consume_handler(websocket)
    except Exception as e:
        print(f'Exception thrown: <{type(e).__name__}>, "{e}"')
        print(traceback.format_exc())
    finally:
        if keepalive_task is not None:
            keepalive_task.cancel()


# Listens for new messages and consumes them
async def consume_handler(websocket):
    try:
        async for message in websocket:
            await consume(json.loads(message))
    except Exception as e:
        print(f'Exception thrown while handling websocket message: <{type(e).__name__}>, "{e}"')
        print(traceback.format_exc())


# Consumes each message and updates the display
async def consume(message):
    response = requests.get(f'{kill_api_base}/{message['killID']}/')
    kill_value = int(response.json()[0]['zkb']['totalValue'])

    if message["alliance_id"] == corp_id:
        kill_value *= -1

    print(f'-- Consuming killmail ID: {message['killID']} with value: {str(kill_value)}')

    try:
        valkey_db = valkey.asyncio.client.Valkey(host=valkey_host, port=valkey_port, db=0)
        await valkey_db.set(f'killmail:{message['killID']}', kill_value, tracking_duration_seconds)
        await valkey_db.aclose()
    except Exception as e:
        print(f'Exception thrown while consuming websocket message: <{type(e).__name__}>, "{e}"')
        print(traceback.format_exc())

    await update_display_if_needed()


# Formats the data that's to be displayed into an AWTRIX readable payload
async def send_display_update_payload(values, total_value, last_total_value):
    formatted_number_string = format_large_number(abs(total_value))

    payload = {
        "draw": [
            *arrows[int(total_value > last_total_value)],
            *create_balance_bar_payload(values),
            {"dt": [8, 1, f'{"-" if total_value < 0 else "  "}{formatted_number_string}',
                    "#ffffff"]}
        ]}

    await mqtt_publish(payload)


# Creates a kill/loss balance bar to be part of the display payload
def create_balance_bar_payload(values):
    lost = sum(list(filter(lambda x: x < 0, values)))
    killed = sum(list(filter(lambda x: x > 0, values)))

    if lost != 0 or killed != 0:
        percentage_killed = killed / max(lost + killed, 1)
        return [
            {"dl": [2, 7, 29, 7, "#FF0000"]},
            {"dl": [2, 7, max(math.ceil(29 * percentage_killed), 2), 7, "#00FF00"]},
        ]
    else:
        return []


# Updates the display if the previous values differ
async def update_display_if_needed():
    try:
        valkey_db = valkey.asyncio.client.Valkey(host=valkey_host, port=valkey_port, db=0)

        keys = await valkey_db.keys("killmail*")
        values = await valkey_db.mget(keys)
        values = [int(value.decode()) for value in values]
        total_value = sum(values)
        last_total_value = int(await valkey_db.get('last_total_value'))

        if total_value != last_total_value:
            await send_display_update_payload(values, total_value, last_total_value)
            await valkey_db.set('last_total_value', total_value)
        await valkey_db.aclose()
    except Exception as e:
        print(f'Exception thrown while checking if display must be updated: <{type(e).__name__}>, "{e}"')
        print(traceback.format_exc())


# Database entries will automatically expire after 24 hours resulting in them no longer being returned
# in the `valkey_db.keys()` function. At a set interval we check the database keys for its total amount of entries
# if out last check differs from what we have stored, we update the display as this probably means
# old entries are expired.
async def check_for_expired_keys():
    while True:
        await update_display_if_needed()
        await asyncio.sleep(10)


# ChatGPT fueled number to string formatter. Don't ask me how it works, unless proven otherwise, I think it just does.
def format_large_number(number):
    units = [(1_000_000_000_000, 't'), (1_000_000_000, 'b'), (1_000_000, 'm'), (1_000, 'k')]
    for factor, suffix in units:
        if number >= factor * 10:
            return f"{round(number / factor):04}{suffix}"
    return f"{number:04}k"  # Default to K on zero


# Simple Ping-Pong to keep the websocket connection active
async def keepalive(websocket, ping_interval=30):
    for ping in itertools.count():
        await asyncio.sleep(ping_interval)
        try:
            await websocket.send(json.dumps({"ping": ping}))
        except Exception as e:
            print(f'Exception thrown while pinging websocket: <{type(e).__name__}>, "{e}"')
            print(traceback.format_exc())
            break


if __name__ == '__main__':
    asyncio.run(start_up())
