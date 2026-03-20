import uooxwu


client = uooxwu.Client(url="http://127.0.0.1:5000", api_key="YOUR_API_KEY")


@client.event()
def connect():
    print("connected")


@client.event()
def disconnect():
    print("disconnected")


@client.event()
def proxy_warning_update(data):
    print("warning update:", data.arrival_times)


@client.event()
def proxy_report_update(data):
    print("report update:", data.time)


if __name__ == "__main__":
    town_map = client.get_town_map()
    print(town_map["6500600"].name)
    client.connect(wait=True)
