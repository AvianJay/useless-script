# 🚌 twbus — Taiwan Bus / YouBike

Look up real-time Taiwan bus arrivals, route overviews, and YouBike station status, with support for favoriting stops. The bus database updates automatically every hour.

| Command | Description |
| --- | --- |
| `/bus getroute` | Look up all stops and arrival info for a specified route |
| `/bus getstop` | Look up real-time arrival info for a specific stop on a route |
| `/bus youbike` | Look up the number of available bikes and docks at a specified YouBike station |
| `/bus favorites` | View and quickly access your favorited stops and YouBike stations |

## Interactive Buttons

The embed for a query result includes action buttons below it:

| Button | Description |
| --- | --- |
| `🔄` | Refresh arrival info (10-second cooldown) |
| `❤️` | Add / remove favorite (favorite limit configurable per server) |
| `🗺️` | Open the stop's location in Google Maps (if coordinates are available) |

## Arrival Info Fields

| Field | Description |
| --- | --- |
| Estimated Arrival | Minutes and seconds until arrival; shows "Arriving" if the bus has already reached the stop |
| Scheduled Arrival | The estimated time provided by the API (HH:MM format) |
| Vehicle Status | Lists the plate numbers of approaching buses and whether they are full |
| Stop Sequence | This stop's order along the route |
| Coordinates | Click to open Google Maps |

## YouBike Info Fields

| Field | Description |
| --- | --- |
| Available Bikes | Number of bikes currently available to borrow |
| Available Docks | Number of empty docks currently available |
| Total Docks | The station's total capacity |
| Status | Whether the station is operating normally |
| Last Updated | The data update time reported by the YouBike system |

> **Rate limit:** each user can query only once every 10 seconds; the refresh button is subject to the same limit.
> **Favorites limit:** by default, each user can have up to 2 favorite stops and 2 favorite YouBike stations; the limit can be adjusted via configuration.
