# PAIS: Purchase specialized equipment and instruments

The DCR graph runs on dcrgraphs.net, which is the process engine. `app.py` is a Flask service in front of it:

| Who | Call | What happens |
|---|---|---|
| CEP (Siddhi) | `POST /from_siddhi` | Starts a case and executes its entry activity |
| People | `GET /cases/<id>` | Shows enabled and pending activities, and whether the case is finished |
| People | `POST /cases/<id>/activities/<label or ID>` | Executes an activity; the role is checked against the graph |
| PAIS → CEP | `POST $SIDDHI_URL` | Sent after *Register in Inventory* (`StockUpdated`) and *Put Into Service* (`EquipmentInService`) |

Every executed activity is appended to `event_log.csv` (case, activity, timestamp, role, data) for conformance checking in processdoctor.

CEP event types that start a case:

| `eventType` | Entry activity |
|---|---|
| `LowStock` | Request Consumables |
| `CasualtySurge` | Declare Emergency Procurement |
| `CapacityShortage` | Identify Equipment Needed |

## Setup (once)

```bash
sudo apt install python3.12-venv      # only if "python3 -m venv" complains about ensurepip
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source venv/bin/activate
export DCR_USERNAME=you@example.com DCR_GRAPH_ID=2017016
read -s -p "dcrgraphs.net password: " DCR_PASSWORD; export DCR_PASSWORD; echo
python3 app.py
```

Optional: `export SIDDHI_URL=http://localhost:8006/stock` to send PAIS → CEP events to Siddhi. Without it they are only printed.

## Try it without Siddhi

In a second terminal (replace `CASE` with the `caseId` from the first response):

```bash
curl -X POST localhost:5000/from_siddhi -H "Content-Type: application/json" -d '{"eventType": "LowStock", "item": "Burn dressings", "value": 12}'
curl localhost:5000/cases/CASE
curl -X POST "localhost:5000/cases/CASE/activities/Approve%20Consumables%20Order" -H "Content-Type: application/json" -d '{"role": "Finance"}'
```

Spaces in a label become `%20` in the URL; you can also use the activity's ID instead of its label.

Response codes when executing: `200` done, `403` wrong role, `404` activity unknown or excluded, `409` not enabled yet, `502` dcrgraphs.net returned an error.

## Known limitations

- Data values (`amount`, `price`) are recorded in the event log but not yet sent to dcrgraphs.net, so the guards see no value.
- `isAccepting` from the API ignores pending activities inside nests, so "finished" is computed from the pending flags instead.
- The list of cases (`GET /cases`) is kept in memory and resets when the service restarts.
