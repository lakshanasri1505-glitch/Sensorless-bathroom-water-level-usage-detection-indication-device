"""
SENSORLESS BATHROOM WATER LEVEL AND USAGE DETECTION / INDICATION DEVICE
-----------------------------------------------------------------------
No physical level sensor is used. The water level is ESTIMATED with a
mass-balance model:

    level(t+dt) = level(t) + pump_inflow*dt - sum(active usage flow)*dt

Inputs (no sensors on the tank itself):
  * Pump ON/OFF state (from relay / smart plug / motor power reading)
  * Usage events: shower, tap, flush (from switches, smart plug or the UI)
Calibration: reset level when tank is known to be full/empty.

Run:    pip install flask
        python app.py
Open:   http://localhost:5000
"""
import random
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

# ---------------- CONFIG ----------------
TANK_CAPACITY_L = 500.0     # tank size in litres
PUMP_FLOW_LPM = 25.0        # pump inflow, litres/min
LOW_PCT, FULL_PCT = 20.0, 95.0
USAGE_LPM = {"shower": 9.0, "tap": 6.0, "flush": 12.0}  # flow while active
SIMULATION = True           # True = auto-generate pump/usage activity

app = Flask(__name__)
lock = threading.Lock()


class Estimator:
    def __init__(self):
        self.level = TANK_CAPACITY_L * 0.5
        self.pump_on = False
        self.active = {}        # usage type -> start time
        self.history = []       # finished usage sessions
        self.total_used = 0.0
        self.today_used = 0.0
        self.alerts = []

    def tick(self, dt):
        inflow = PUMP_FLOW_LPM / 60 * dt if self.pump_on else 0.0
        outflow = sum(USAGE_LPM[u] for u in self.active) / 60 * dt
        outflow = min(outflow, self.level + inflow)
        self.level = max(0.0, min(TANK_CAPACITY_L, self.level + inflow - outflow))
        self.total_used += outflow
        self.today_used += outflow
        # simple alerts
        pct = self.pct()
        if pct <= LOW_PCT and not self.pump_on:
            self.alert("Low water - switch pump ON")
        if pct >= FULL_PCT and self.pump_on:
            self.alert("Tank full - switch pump OFF")

    def pct(self):
        return round(self.level / TANK_CAPACITY_L * 100, 1)

    def alert(self, msg):
        if not self.alerts or self.alerts[-1]["msg"] != msg:
            self.alerts.append({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg})
            self.alerts = self.alerts[-10:]

    def start_use(self, kind):
        if kind in USAGE_LPM and kind not in self.active:
            self.active[kind] = time.time()

    def stop_use(self, kind):
        start = self.active.pop(kind, None)
        if start:
            secs = time.time() - start
            self.history.append({
                "type": kind,
                "time": datetime.now().strftime("%H:%M:%S"),
                "seconds": round(secs, 1),
                "litres": round(USAGE_LPM[kind] / 60 * secs, 2),
            })
            self.history = self.history[-20:]

    def status(self):
        pct = self.pct()
        state = "LOW" if pct <= LOW_PCT else "FULL" if pct >= FULL_PCT else "NORMAL"
        return {
            "level_l": round(self.level, 1),
            "percent": pct,
            "state": state,
            "pump_on": self.pump_on,
            "active_usage": list(self.active.keys()),
            "total_used_l": round(self.total_used, 1),
            "today_used_l": round(self.today_used, 1),
            "history": self.history[::-1],
            "alerts": self.alerts[::-1],
            "capacity_l": TANK_CAPACITY_L,
            "simulation": SIMULATION,
        }


est = Estimator()


def engine():
    """Background loop: advances the model once per second."""
    last = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        with lock:
            est.tick(now - last)
            if SIMULATION:
                simulate()
        last = now


def simulate():
    # Automatic pump control with hysteresis
    if est.pct() <= LOW_PCT:
        est.pump_on = True
    elif est.pct() >= FULL_PCT:
        est.pump_on = False
    # Random usage events
    for kind in USAGE_LPM:
        if kind in est.active:
            if random.random() < 0.08:
                est.stop_use(kind)
        elif random.random() < 0.02:
            est.start_use(kind)


# ---------------- API ----------------
@app.route("/api/status")
def api_status():
    with lock:
        return jsonify(est.status())


@app.route("/api/usage", methods=["POST"])
def api_usage():
    d = request.get_json(force=True)
    with lock:
        (est.start_use if d.get("action") == "start" else est.stop_use)(d.get("type"))
    return jsonify(ok=True)


@app.route("/api/pump", methods=["POST"])
def api_pump():
    with lock:
        est.pump_on = bool(request.get_json(force=True).get("state"))
    return jsonify(ok=True)


@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    pct = float(request.get_json(force=True).get("percent", 100))
    with lock:
        est.level = TANK_CAPACITY_L * max(0, min(100, pct)) / 100
    return jsonify(ok=True)


# ---------------- WEB PAGE ----------------
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensorless Water Monitor</title>
<style>
 body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:16px}
 h1{font-size:1.2rem;margin:0 0 12px}
 .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
 .card{background:#1e293b;border-radius:12px;padding:14px}
 .tank{height:200px;width:120px;border:3px solid #94a3b8;border-radius:10px;
       margin:auto;position:relative;overflow:hidden;background:#0b1220}
 .water{position:absolute;bottom:0;width:100%;background:#38bdf8;transition:height .8s}
 .pct{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
      font-size:1.6rem;font-weight:700;text-shadow:0 0 4px #000}
 .badge{display:inline-block;padding:4px 12px;border-radius:99px;font-weight:700}
 .NORMAL{background:#16a34a}.LOW{background:#dc2626}.FULL{background:#2563eb}
 button{background:#334155;color:#fff;border:0;border-radius:8px;padding:10px 14px;margin:4px 4px 0 0;font-size:.95rem}
 button.on{background:#0ea5e9}
 table{width:100%;font-size:.85rem;border-collapse:collapse}
 td,th{padding:4px;text-align:left;border-bottom:1px solid #334155}
 .alert{color:#fbbf24;font-size:.85rem;margin:2px 0}
 input{width:70px;padding:8px;border-radius:8px;border:0}
</style></head><body>
<h1>💧 Sensorless Bathroom Water Level &amp; Usage Monitor</h1>
<div class="grid">
 <div class="card" style="text-align:center">
  <div class="tank"><div class="water" id="water"></div><div class="pct" id="pct">--</div></div>
  <p><span class="badge" id="state">--</span></p>
  <p><b id="litres">--</b> / <span id="cap">--</span> L (estimated)</p>
  <p>Pump: <b id="pump">--</b></p>
 </div>
 <div class="card">
  <b>Usage detection</b>
  <p>Active now: <b id="active">none</b></p>
  <p>Today: <b id="today">0</b> L &nbsp; Total: <b id="total">0</b> L</p>
  <div id="ctrls"></div>
  <p style="margin-top:12px"><b>Pump control</b><br>
   <button onclick="pump(true)">Pump ON</button><button onclick="pump(false)">Pump OFF</button></p>
  <p><b>Calibrate</b> (tank known level %)<br>
   <input id="cal" type="number" value="100" min="0" max="100">
   <button onclick="calib()">Set</button></p>
  <small id="sim"></small>
 </div>
 <div class="card"><b>Alerts</b><div id="alerts"></div></div>
 <div class="card"><b>Recent usage</b>
  <table><thead><tr><th>Time</th><th>Type</th><th>Sec</th><th>L</th></tr></thead>
  <tbody id="hist"></tbody></table></div>
</div>
<script>
const types=["shower","tap","flush"];
const post=(u,b)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)});
const pump=s=>post("/api/pump",{state:s}); 
const calib=()=>post("/api/calibrate",{percent:document.getElementById("cal").value});
const toggle=(t,a)=>post("/api/usage",{type:t,action:a});
document.getElementById("ctrls").innerHTML=types.map(t=>
 `<button onclick="toggle('${t}','start')">Start ${t}</button><button onclick="toggle('${t}','stop')">Stop ${t}</button>`).join("<br>");
async function refresh(){
 const s=await (await fetch("/api/status")).json();
 water.style.height=s.percent+"%"; pct.textContent=s.percent+"%";
 state.textContent=s.state; state.className="badge "+s.state;
 litres.textContent=s.level_l; cap.textContent=s.capacity_l;
 pump_.textContent=s.pump_on?"ON":"OFF";
 active.textContent=s.active_usage.join(", ")||"none";
 today.textContent=s.today_used_l; total.textContent=s.total_used_l;
 alerts.innerHTML=s.alerts.map(a=>`<div class="alert">${a.time} - ${a.msg}</div>`).join("")||"No alerts";
 hist.innerHTML=s.history.map(h=>`<tr><td>${h.time}</td><td>${h.type}</td><td>${h.seconds}</td><td>${h.litres}</td></tr>`).join("");
 sim.textContent=s.simulation?"Simulation mode is ON":"";
}
const pump_=document.getElementById("pump");
setInterval(refresh,1000); refresh();
</script></body></html>"""


@app.route("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    threading.Thread(target=engine, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
